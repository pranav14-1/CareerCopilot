import io
import json
import uuid
import logging
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

        keyboard = [
            [
                InlineKeyboardButton("Fresher", callback_data="set_exp_Fresher"),
                InlineKeyboardButton("0-1 year", callback_data="set_exp_0-1 year"),
            ],
            [
                InlineKeyboardButton("1-3 years", callback_data="set_exp_1-3 years"),
                InlineKeyboardButton("3-5 years", callback_data="set_exp_3-5 years"),
            ],
            [
                InlineKeyboardButton("5+ years", callback_data="set_exp_5+ years")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await status_message.edit_text(
            f"✅ <b>Resume processed successfully!</b>\n\n"
            f"Welcome, <b>{parsed_name}</b>. Your profile has been semantically indexed.\n\n"
            f"💼 <b>One last step:</b> Please select your experience level below to complete your profile:",
            reply_markup=reply_markup,
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
    Handles inline keyboard button clicks for job details view, tailoring, experience onboarding, and deep refinement.
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    logger.info(f"Received callback query '{data}' from user {user_id}")

    from app.core.database import redis_client
    import hashlib

    if data.startswith("set_exp_"):
        selected_exp = data[len("set_exp_"):]
        async with AsyncSessionLocal() as db:
            from app.services.user_service import get_user_profile
            user = await get_user_profile(db, user_id)
            if user:
                profile = user.extracted_profile or {}
                profile["experience_level"] = selected_exp
                user.extracted_profile = profile
                
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(user, "extracted_profile")
                await db.commit()
                
        await query.edit_message_text(
            f"✅ <b>Experience level set to: {selected_exp}</b>\n\n"
            "Your profile is now complete! Use /jobs to find matching roles.",
            parse_mode="HTML"
        )

    elif data == "jobs_refine":
        # Let's perform a deep AI rerank (bypassing the cooldown)
        try:
            user_query = await redis_client.get(f"user_last_query:{user_id}")
            if user_query:
                user_query = user_query.decode("utf-8") if isinstance(user_query, bytes) else user_query
            else:
                user_query = ""
        except Exception as e:
            logger.error(f"Failed to fetch last query from Redis: {e}")
            user_query = ""

        status_msg = await query.message.reply_html("🧠 <b>Running deep AI re-ranking (bypassing cooldown)...</b>")

        try:
            async with AsyncSessionLocal() as db:
                from app.services.user_service import get_user_profile
                user = await get_user_profile(db, user_id)

                if not user or not user.extracted_profile or user.resume_embedding is None:
                    await status_msg.edit_text(
                        "❌ <b>No resume found.</b> Please upload your PDF resume first.",
                        parse_mode="HTML"
                    )
                    return

                from app.services.search import stage1_retrieval, stage2_rerank
                import time
                start_time = time.time()
                # Fetch Top 5 candidates for low latency
                candidates = await stage1_retrieval(user, db, limit=5, user_query=user_query)

                if not candidates:
                    await status_msg.edit_text(
                        "🤷 <b>No matching jobs found in our database.</b>",
                        parse_mode="HTML"
                    )
                    return

                # Run rerank with refine=True to bypass cooldown
                reranked = await stage2_rerank(user, candidates, user_query=user_query, refine=True)

                # Display the top reranked jobs
                top_results = reranked[:5]
                await status_msg.delete()

                if not top_results:
                    await query.message.reply_html(
                        "🤷 <b>No matching jobs found.</b>\n"
                        "Try refining your search keyword or profile details."
                    )
                else:
                    await query.message.reply_html(
                        f"💼 <b>Deep AI Job Recommendations:</b>"
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
                            f"💡 <b>Reasoning:</b> {escape(evaluation.reasoning)}\n"
                            f"⚠️ <b>Key Skill Gaps:</b> {gaps_str}\n"
                        )

                        keyboard = [
                            [
                                InlineKeyboardButton("🔍 View Details", callback_data=f"job_view_{job.id}"),
                                InlineKeyboardButton("✨ Tailor Resume", callback_data=f"job_tailor_{job.id}")
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        await query.message.reply_html(card_text, reply_markup=reply_markup)

                from app.services.analytics import track_event
                latency = (time.time() - start_time) * 1000
                await track_event(user_id, "job_search", latency)

        except Exception as e:
            logger.error(f"Error during refine command flow: {e}", exc_info=True)
            await status_msg.edit_text(
                f"❌ <b>Refine failed:</b> <code>{escape(str(e))}</code>",
                parse_mode="HTML"
            )

    elif data.startswith("job_view_"):
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

            # 3. Retrieve or calculate match evaluation using new smart cache key
            from app.services.search import JobMatchEvaluation, stage2_rerank
            
            exp_clean = (user.extracted_profile or {}).get("experience_level", "Fresher").strip().lower()
            try:
                user_query = await redis_client.get(f"user_last_query:{user_id}")
                if user_query:
                    user_query = user_query.decode("utf-8") if isinstance(user_query, bytes) else user_query
                else:
                    user_query = ""
            except Exception:
                user_query = ""
            
            cleaned_query = (user_query or "").strip().lower()
            query_hash = hashlib.md5(cleaned_query.encode("utf-8")).hexdigest() if cleaned_query else "none"
            cache_key = f"job_eval:{user_id}:{job.id}:{exp_clean}:{query_hash}"

            evaluation = None
            try:
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    evaluation = JobMatchEvaluation.model_validate(json.loads(cached_data))
            except Exception as e:
                logger.warning(f"Failed to query Redis cache in callback: {e}")

            if not evaluation:
                try:
                    reranked = await stage2_rerank(user, [job], user_query=user_query)
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
            
            await query.message.reply_html(detail_text)

    elif data.startswith("job_tailor_"):
        job_id_str = data[len("job_tailor_"):]
        try:
            job_uuid = uuid.UUID(job_id_str)
        except ValueError:
            await query.message.reply_html("❌ <b>Invalid Job ID.</b>")
            return

        from app.bot.commands import run_tailoring_flow
        await run_tailoring_flow(user_id, job_uuid, context)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Processes plain text messages. If user is in the 'awaiting_role_pref' state,
    it executes the preference-aware hybrid job search. Otherwise, sends help info.
    """
    tg_id = update.effective_user.id
    from app.core.database import redis_client
    
    try:
        user_state = await redis_client.get(f"user_state:{tg_id}")
        if user_state:
            user_state = user_state.decode("utf-8") if isinstance(user_state, bytes) else user_state
    except Exception as e:
        logger.error(f"Failed to fetch user state from Redis: {e}")
        user_state = None

    if user_state == "awaiting_role_pref":
        try:
            await redis_client.delete(f"user_state:{tg_id}")
        except Exception as e:
            logger.error(f"Failed to clear user state: {e}")

        user_query = update.message.text
        logger.info(f"User {tg_id} search preference: '{user_query}'")

        # Save query to Redis so they can trigger "Refine Results"
        try:
            await redis_client.set(f"user_last_query:{tg_id}", user_query, ex=3600)
        except Exception as e:
            logger.error(f"Failed to save last query to Redis: {e}")

        import time
        start_time = time.time()
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

                # Step 1: Hybrid Lexical + Vector Retrieval (Top 5 candidates for low latency)
                candidates = await stage1_retrieval(user, db, limit=5, user_query=user_query)

                if not candidates:
                    await status_msg.edit_text(
                        "🤷 <b>No matching jobs found in our database.</b> Please try different search keywords.",
                        parse_mode="HTML"
                    )
                    return



                # Step 2: LLM Reranking (passing the user query to the reranker)
                await status_msg.edit_text("🧠 <b>Running AI match analysis and re-ranking...</b>", parse_mode="HTML")
                reranked = await stage2_rerank(user, candidates, user_query=user_query, refine=False)

                # Display the top reranked jobs
                top_results = reranked[:5]
                await status_msg.delete()

                if not top_results:
                    await update.message.reply_html(
                        "🤷 <b>No matching jobs found.</b>\n"
                        "Try refining your search keyword or profile details."
                    )
                else:
                    await update.message.reply_html(
                        f"💼 <b>Top Job Recommendations:</b>"
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



                from app.services.analytics import track_event
                latency = (time.time() - start_time) * 1000
                await track_event(tg_id, "job_search", latency)

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
