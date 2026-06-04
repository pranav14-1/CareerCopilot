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
    
    # 1. Enforce PDF check
    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Only PDF resumes are accepted. Please upload a valid .pdf file.")
        return

    # 2. Enforce Size Limit (5MB)
    MAX_SIZE = 5 * 1024 * 1024
    if document.file_size > MAX_SIZE:
        await update.message.reply_text("❌ The file is too large. Maximum allowed size is 5MB.")
        return

    await update.message.reply_text("📥 Ingesting resume. Parsing and profile indexing started...")
    
    # Placeholder: In subsequent phases we will download to BytesIO and parse with pdfplumber
    # file = await context.bot.get_file(document.file_id)
    # pdf_bytes = io.BytesIO()
    # await file.download_to_memory(out=pdf_bytes)


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
