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
    try:
        from app.services.news_ingester import fetch_and_ingest_news
        await fetch_and_ingest_news()
    except Exception as e:
        logger.error(f"Error executing aggregate_tech_briefings: {e}")


async def dispatch_reminder(reminder_id: str) -> None:
    """
    Dispatches a Telegram message reminder to the target user.
    Called dynamically by APScheduler trigger events.
    """
    logger.info(f"Executing scheduled dispatch for reminder: {reminder_id}")
    # Placeholder: Fetch reminder details, call context.bot.send_message


async def startup_ingestion_check() -> None:
    """Runs a one-off job and news ingestion on startup if empty."""
    logger.info("Checking if database needs initial job/news seeding...")
    try:
        from app.models.schemas import NewsArticle
        async with AsyncSessionLocal() as session:
            from sqlalchemy import or_
            result = await session.execute(select(Job).limit(1))
            existing_job = result.scalars().first()
            
            # Check if we have any India-focused or partner-imported jobs
            indian_result = await session.execute(
                select(Job).where(
                    or_(
                        Job.location.ilike("%india%"),
                        Job.location.ilike("%bangalore%"),
                        Job.location.ilike("%bengaluru%"),
                        Job.location.ilike("%hyderabad%"),
                        Job.location.ilike("%delhi%"),
                        Job.location.ilike("%mumbai%"),
                        Job.location.ilike("%noida%"),
                        Job.location.ilike("%pune%"),
                        Job.location.ilike("%chennai%"),
                        Job.location.ilike("%gurgaon%"),
                        Job.url.ilike("%instahyre%"),
                        Job.url.ilike("%cutshort%"),
                        Job.url.ilike("%hirist%")
                    )
                ).limit(1)
            )
            has_indian_job = indian_result.scalars().first()
            
            if not existing_job or not has_indian_job:
                logger.info("Database has no jobs or lacks India-market jobs. Triggering immediate background job ingestion...")
                asyncio.create_task(fetch_and_ingest_jobs())
            else:
                logger.info("Database already contains job listings including India-focused roles. Initial seeding skipped.")

            # Check news articles
            news_res = await session.execute(select(NewsArticle).limit(1))
            existing_news = news_res.scalars().first()
            if not existing_news:
                logger.info("Database has no news. Triggering immediate background news ingestion...")
                asyncio.create_task(aggregate_tech_briefings())
            else:
                logger.info("Database already contains news articles. News seeding skipped.")
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

        # Schedule the news briefing ingester to run every 12 hours
        scheduler.add_job(
            aggregate_tech_briefings,
            "interval",
            hours=12,
            id="news_ingestion_interval",
            replace_existing=True
        )
        
        # Start the scheduler
        scheduler.start()
        logger.info("APScheduler started successfully.")
        
        # Trigger startup database check/ingestion in background
        asyncio.create_task(startup_ingestion_check())
        
    except Exception as e:
        logger.error(f"Failed to start APScheduler: {e}", exc_info=True)
