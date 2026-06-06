import io
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.core.database import AsyncSessionLocal
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
        await status_message.edit_text("🧠 <b>Analyzing resume with AI...</b> Extracting structured profile...")
        structured_profile = await extract_structured_profile(raw_text)

        # 6. Generate 768-dim text embedding vector
        await status_message.edit_text("🧬 <b>Generating vector embeddings...</b> Indexing skills...")
        embedding = await generate_embedding(raw_text)

        # 7. Upsert user in database
        await status_message.edit_text("💾 <b>Saving to secure database...</b>")
        
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
            f"• Use /jobs to find hyper-relevant job openings"
        )

    except Exception as e:
        logger.error(f"Error onboarding user {tg_id}: {e}", exc_info=True)
        await status_message.edit_text(
            "❌ <b>Onboarding failed.</b>\n\n"
            f"An error occurred during resume parsing: <code>{str(e)}</code>.\n"
            "Please ensure your PDF is not scan-only or password protected, then try again."
        )


def create_bot_app() -> Application:
    """
    Initialize and return the Telegram Bot Application with registered handlers.
    """
    if settings.TELEGRAM_BOT_TOKEN == "placeholder_token":
        logger.warning("TELEGRAM_BOT_TOKEN is set to placeholder. Bot initialization skipped or will run degraded.")

    bot_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

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

    logger.info("Telegram Bot handlers successfully registered.")
    return bot_app
