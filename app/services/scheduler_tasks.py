import logging
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.future import select

from app.core.database import AsyncSessionLocal
from app.models.schemas import Job
from app.services.job_ingester import fetch_and_ingest_jobs

logger = logging.getLogger(__name__)

# Global scheduler reference
scheduler = AsyncIOScheduler()


async def aggregate_tech_briefings() -> None:
    """
    Cron task that aggregates RSS feeds, Hacker News articles, and Reddit posts.
    Performs LLM deduplication and stores summaries in the database.
    """
    logger.info("Executing background cron task: aggregate_tech_briefings")
    # Placeholder: Feeds ingestion, parsing, LLM summarization, database sync.


async def dispatch_reminder(reminder_id: str) -> None:
    """
    Dispatches a Telegram message reminder to the target user.
    Called dynamically by APScheduler trigger events.
    """
    logger.info(f"Executing scheduled dispatch for reminder: {reminder_id}")
    # Placeholder: Fetch reminder details, call context.bot.send_message


async def startup_ingestion_check() -> None:
    """Runs a one-off job ingestion on startup if the database is empty."""
    logger.info("Checking if database needs initial job seeding...")
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Job).limit(1))
            existing_job = result.scalars().first()
            
            if not existing_job:
                logger.info("Database is empty. Triggering immediate background job ingestion...")
                # Run concurrently in the background
                asyncio.create_task(fetch_and_ingest_jobs())
            else:
                logger.info("Database already contains job listings. Initial seeding skipped.")
    except Exception as e:
        logger.error(f"Error during startup ingestion check: {e}")


def initialize_scheduler() -> None:
    """
    Setup APScheduler instance and schedule background recurring jobs.
    """
    logger.info("Initializing APScheduler configuration...")
    
    try:
        # Schedule the job ingester to run every 12 hours
        scheduler.add_job(
            fetch_and_ingest_jobs,
            "interval",
            hours=12,
            id="job_ingestion_interval",
            replace_existing=True
        )
        
        # Start the scheduler
        scheduler.start()
        logger.info("APScheduler started successfully.")
        
        # Trigger startup database check/ingestion in background
        asyncio.create_task(startup_ingestion_check())
        
    except Exception as e:
        logger.error(f"Failed to start APScheduler: {e}", exc_info=True)
