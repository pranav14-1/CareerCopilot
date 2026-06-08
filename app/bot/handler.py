import io
import json
import uuid
import logging
from html import escape
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.schemas import Job
from app.services.parser import parse_resume_pdf, extract_structured_profile, generate_embedding
from app.services.user_service import upsert_user_profile
from app.bot.commands import (
    start_command,
    profile_command,
    jobs_command,
    tailor_command,
    learn_command,
    news_command,
    remind_command,
    stats_command,
)

logger = logging.getLogger(__name__)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle document uploads, checking size and extension, then parsing.
    """
    document = update.message.document
    tg_id = update.effective_user.id
    username = update.effective_user.username or "Anonymous"

    # 1. Enforce PDF check
    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_html("❌ <b>Only PDF resumes are accepted.</b> Please upload a valid <code>.pdf</code> file.")
        return

    # 2. Enforce Size Limit (5MB)
    MAX_SIZE = 5 * 1024 * 1024
    if document.file_size > MAX_SIZE:
        await update.message.reply_html("❌ <b>The file is too large.</b> Maximum allowed size is 5MB.")
        return

    status_message = await update.message.reply_html("📥 <b>Ingesting resume...</b> Extacting text content...")

    try:
        # 3. Download to memory buffer
        file_obj = await context.bot.get_file(document.file_id)
        pdf_buffer = io.BytesIO()
        await file_obj.download_to_memory(out=pdf_buffer)
        pdf_buffer.seek(0)

        # 4. Parse text using pdfplumber
        logger.info(f"Parsing PDF for user {tg_id} ({username})")
        raw_text = await parse_resume_pdf(pdf_buffer)
        
        if not raw_text.strip():
            raise ValueError("The PDF contains no extractable text content.")

        # 5. Extract structured profile using Gemini + Instructor
        await status_message.edit_text("🧠 <b>Analyzing resume with AI...</b> Extracting structured profile...", parse_mode="HTML")
        structured_profile = await extract_structured_profile(raw_text)

        # 6. Generate 768-dim text embedding vector
        await status_message.edit_text("🧬 <b>Generating vector embeddings...</b> Indexing skills...", parse_mode="HTML")
        embedding = await generate_embedding(raw_text)

        # 7. Upsert user in database
        await status_message.edit_text("💾 <b>Saving to secure database...</b>", parse_mode="HTML")
        
        # Convert Pydantic schema to dict
        profile_dict = structured_profile.model_dump()
        
        # Determine name/email with fallbacks
        parsed_name = structured_profile.name or update.effective_user.full_name or "Unknown Candidate"
        parsed_email = structured_profile.email or ""

        async with AsyncSessionLocal() as db:
            await upsert_user_profile(
                db=db,
                telegram_id=tg_id,
                full_name=parsed_name,
                email=parsed_email,
                extracted_profile=profile_dict,
                resume_text=raw_text,
                resume_embedding=embedding
            )

        logger.info(f"Successfully onboarding user {tg_id}")
        await status_message.edit_text(
            f"✅ <b>Resume processed successfully!</b>\n\n"
            f"Welcome, <b>{parsed_name}</b>. Your profile has been semantically indexed.\n\n"
            f"• Use /profile to view your parsed details\n"
            f"• Use /jobs to find hyper-relevant job openings",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error onboarding user {tg_id}: {e}", exc_info=True)
        await status_message.edit_text(
            "❌ <b>Onboarding failed.</b>\n\n"
            f"An error occurred during resume parsing: <code>{str(e)}</code>.\n"
            "Please ensure your PDF is not scan-only or password protected, then try again.",
            parse_mode="HTML"
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles inline keyboard button clicks for job details view and tailoring.
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    logger.info(f"Received callback query '{data}' from user {user_id}")

    if data.startswith("job_view_"):
        job_id_str = data[len("job_view_"):]
        try:
            job_uuid = uuid.UUID(job_id_str)
        except ValueError:
            await query.message.reply_html("❌ <b>Invalid Job ID.</b>")
            return

        async with AsyncSessionLocal() as db:
            # 1. Fetch job
            stmt = select(Job).where(Job.id == job_uuid)
            res = await db.execute(stmt)
            job = res.scalars().first()
            if not job:
                await query.message.reply_html("❌ <b>Job not found in system.</b>")
                return

            # 2. Fetch user to check profile
            from app.services.user_service import get_user_profile
            user = await get_user_profile(db, user_id)
            if not user or not user.extracted_profile:
                await query.message.reply_html("❌ <b>User profile not found.</b> Please upload a resume first.")
                return

            # 3. Retrieve or calculate match evaluation
            from app.services.search import JobMatchEvaluation, stage2_rerank
            from app.core.database import redis_client
            
            cache_key = f"job_eval:{user_id}:{job.id}"
            evaluation = None
            try:
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    evaluation = JobMatchEvaluation.model_validate(json.loads(cached_data))
            except Exception as e:
                logger.warning(f"Failed to query Redis cache in callback: {e}")

            if not evaluation:
                # If cache missed, run a quick one-off re-rank (should be rare)
                try:
                    reranked = await stage2_rerank(user, [job])
                    if reranked:
                        evaluation = reranked[0]["evaluation"]
                except Exception as e:
                    logger.error(f"Failed to evaluate job on the fly: {e}")

            if not evaluation:
                evaluation = JobMatchEvaluation(
                    score=50,
                    reasoning="Deep evaluation unavailable. Default score shown.",
                    skill_gaps=[]
                )

            # 4. Format detailed message
            gaps_str = ", ".join([f"<code>{escape(g)}</code>" for g in evaluation.skill_gaps]) if evaluation.skill_gaps else "<i>None identified!</i>"
            
            detail_text = (
                f"🎯 <b>{escape(job.title)}</b> at <b>{escape(job.company)}</b>\n"
                f"📍 <b>Location:</b> {escape(job.location or 'Remote')}\n\n"
                f"📝 <b>Job Description:</b>\n"
                f"{escape(job.description)}\n\n"
                f"🛠️ <b>Requirements:</b>\n"
                f"{escape(job.requirements or 'N/A')}\n\n"
                f"🧠 <b>AI Match Analysis:</b>\n"
                f"📊 <b>Score:</b> {evaluation.score}%\n"
                f"💡 <b>Reasoning:</b> {escape(evaluation.reasoning)}\n"
                f"⚠️ <b>Key Gaps:</b> {gaps_str}"
            )
            
            # Send as new message to keep conversation history neat
            await query.message.reply_html(detail_text)

    elif data.startswith("job_tailor_"):
        job_id_str = data[len("job_tailor_"):]
        try:
            job_uuid = uuid.UUID(job_id_str)
        except ValueError:
            await query.message.reply_html("❌ <b>Invalid Job ID.</b>")
            return

        async with AsyncSessionLocal() as db:
            stmt = select(Job).where(Job.id == job_uuid)
            res = await db.execute(stmt)
            job = res.scalars().first()
            if not job:
                await query.message.reply_html("❌ <b>Job not found in system.</b>")
                return

            tailor_msg = (
                f"✨ <b>Resume Tailoring Request received!</b>\n\n"
                f"Targeting: <b>{escape(job.title)}</b> at <b>{escape(job.company)}</b>\n\n"
                f"I will now pass your resume and this job opening to our Multi-Agent Critique System to "
                f"rewrite and align your details. In Phase 3, this will compile a professional, tailored PDF resume."
            )
            await query.message.reply_html(tailor_msg)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Processes plain text messages. If user is in the 'awaiting_role_pref' state,
    it executes the preference-aware hybrid job search. Otherwise, sends help info.
    """
    tg_id = update.effective_user.id
    from app.core.database import redis_client
    
    # 1. Check user state in Redis
    try:
        user_state = await redis_client.get(f"user_state:{tg_id}")
    except Exception as e:
        logger.error(f"Failed to fetch user state from Redis: {e}")
        user_state = None

    if user_state == "awaiting_role_pref":
        # Clear the state so subsequent messages are not treated as preferences
        try:
            await redis_client.delete(f"user_state:{tg_id}")
        except Exception as e:
            logger.error(f"Failed to clear user state: {e}")

        user_query = update.message.text
        logger.info(f"User {tg_id} search preference: '{user_query}'")

        status_msg = await update.message.reply_html("🔍 <b>Searching for matches using your resume & preferences...</b>")

        try:
            async with AsyncSessionLocal() as db:
                from app.services.user_service import get_user_profile
                user = await get_user_profile(db, tg_id)

                if not user or not user.extracted_profile or user.resume_embedding is None:
                    await status_msg.edit_text(
                        "❌ <b>No resume found.</b> Please upload your PDF resume first.",
                        parse_mode="HTML"
                    )
                    return

                from app.services.search import stage1_retrieval, stage2_rerank
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                # Step 1: Hybrid Lexical + Vector Retrieval (Top 20 candidates)
                candidates = await stage1_retrieval(user, db, limit=20, user_query=user_query)

                if not candidates:
                    await status_msg.edit_text(
                        "🤷 <b>No matching jobs found in our database.</b> Please try different search keywords.",
                        parse_mode="HTML"
                    )
                    return

                # Step 2: LLM Reranking (passing the user query to the reranker)
                await status_msg.edit_text("🧠 <b>Running AI match analysis and re-ranking...</b>", parse_mode="HTML")
                reranked = await stage2_rerank(user, candidates, user_query=user_query)

                top_results = reranked[:5]
                await status_msg.delete()

                await update.message.reply_html(
                    f"💼 <b>Top Job Recommendations for:</b> <code>{escape(user_query)}</code>"
                )

                for res in top_results:
                    job = res["job"]
                    evaluation = res["evaluation"]

                    gaps = evaluation.skill_gaps
                    gaps_str = ", ".join([f"<code>{escape(g)}</code>" for g in gaps]) if gaps else "<i>None identified!</i>"

                    card_text = (
                        f"🎯 <b>{escape(job.title)}</b> at <b>{escape(job.company)}</b>\n"
                        f"📍 <b>Location:</b> {escape(job.location or 'Remote')}\n"
                        f"📊 <b>Match Score:</b> <b>{evaluation.score}%</b>\n"
                        f"⚠️ <b>Key Skill Gaps:</b> {gaps_str}\n"
                    )

                    keyboard = [
                        [
                            InlineKeyboardButton("🔍 View Details", callback_data=f"job_view_{job.id}"),
                            InlineKeyboardButton("✨ Tailor Resume", callback_data=f"job_tailor_{job.id}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await update.message.reply_html(card_text, reply_markup=reply_markup)

        except Exception as e:
            logger.error(f"Error during search command flow: {e}", exc_info=True)
            await status_msg.edit_text(
                f"❌ <b>Search failed:</b> <code>{escape(str(e))}</code>",
                parse_mode="HTML"
            )

    else:
        # Default help guidelines
        help_text = (
            "👋 <b>How can I help you?</b>\n\n"
            "• Send me your resume as a <b>PDF document</b> (max 5MB) to index your profile.\n"
            "• Use the /jobs command to trigger our <b>Conversational Job Matching</b> system.\n"
            "• Use the /profile command to view your parsed resume details."
        )
        await update.message.reply_html(help_text)


def create_bot_app() -> Application:
    """
    Initialize and return the Telegram Bot Application with registered handlers.
    """
    if settings.TELEGRAM_BOT_TOKEN == "placeholder_token":
        logger.warning("TELEGRAM_BOT_TOKEN is set to placeholder. Bot initialization skipped or will run degraded.")

    bot_app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Register slash command handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("profile", profile_command))
    bot_app.add_handler(CommandHandler("jobs", jobs_command))
    bot_app.add_handler(CommandHandler("tailor", tailor_command))
    bot_app.add_handler(CommandHandler("learn", learn_command))
    bot_app.add_handler(CommandHandler("news", news_command))
    bot_app.add_handler(CommandHandler("remind", remind_command))
    bot_app.add_handler(CommandHandler("stats", stats_command))

    # Register document upload handler
    bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Register text message handler for conversational preferences
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Register callback query handler for buttons
    bot_app.add_handler(CallbackQueryHandler(handle_callback_query))

    logger.info("Telegram Bot handlers successfully registered.")
    return bot_app
