import asyncio
import logging
import uuid
import httpx
from sqlalchemy.future import select

from app.core.database import AsyncSessionLocal
from app.models.schemas import Job
from app.services.parser import generate_embedding

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


async def fetch_remotive_jobs(limit: int = 30) -> list:
    """Fetch job postings from the Remotive API."""
    url = "https://remotive.com/api/remote-jobs?limit=50"
    logger.info(f"Fetching jobs from Remotive: {url}")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=HEADERS)
            if response.status_code == 200:
                data = response.json()
                jobs = data.get("jobs", [])
                logger.info(f"Fetched {len(jobs)} jobs from Remotive.")
                return jobs[:limit]
            else:
                logger.error(f"Remotive API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching from Remotive: {e}")
    return []


async def fetch_arbeitnow_jobs(limit: int = 30) -> list:
    """Fetch job postings from the Arbeitnow API."""
    url = "https://www.arbeitnow.com/api/job-board-api"
    logger.info(f"Fetching jobs from Arbeitnow: {url}")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=HEADERS)
            if response.status_code == 200:
                data = response.json()
                jobs = data.get("data", [])
                logger.info(f"Fetched {len(jobs)} jobs from Arbeitnow.")
                return jobs[:limit]
            else:
                logger.error(f"Arbeitnow API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching from Arbeitnow: {e}")
    return []


def clean_html(raw_html: str) -> str:
    """Remove simple HTML tags from description strings if present."""
    if not raw_html:
        return ""
    import re
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()


async def fetch_and_ingest_jobs() -> None:
    """
    Main job ingestion pipeline. Fetches jobs from APIs, normalizes,
    generates embeddings and saves new postings to the database.
    """
    logger.info("Starting background job ingestion...")
    
    # 1. Fetch raw listings
    remotive_raw = await fetch_remotive_jobs(limit=25)
    arbeitnow_raw = await fetch_arbeitnow_jobs(limit=25)

    normalized_jobs = []

    # 2. Normalize Remotive
    for r_job in remotive_raw:
        title = r_job.get("title", "").strip()
        company = r_job.get("company_name", "").strip()
        location = r_job.get("candidate_required_location", "Remote").strip()
        desc = clean_html(r_job.get("description", ""))
        tags = r_job.get("tags", [])
        reqs = ", ".join(tags) if tags else "Python, Web Development"
        
        if title and company:
            normalized_jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "description": desc,
                "requirements": reqs
            })

    # 3. Normalize Arbeitnow
    for a_job in arbeitnow_raw:
        title = a_job.get("title", "").strip()
        company = a_job.get("company_name", "").strip()
        
        # Check remote status or location
        is_remote = a_job.get("remote", False)
        loc = a_job.get("location", "Germany").strip()
        if is_remote:
            loc = f"{loc} (Remote)"
            
        desc = clean_html(a_job.get("description", ""))
        tags = a_job.get("tags", [])
        reqs = ", ".join(tags) if tags else "Software Engineering"
        
        if title and company:
            normalized_jobs.append({
                "title": title,
                "company": company,
                "location": loc,
                "description": desc,
                "requirements": reqs
            })

    logger.info(f"Total normalized candidates for ingestion: {len(normalized_jobs)}")

    # 4. Check for duplicates and write to DB
    async with AsyncSessionLocal() as session:
        new_jobs_added = 0
        
        for idx, job_data in enumerate(normalized_jobs):
            # Check if this job (title + company) already exists
            stmt = select(Job).where(
                Job.title == job_data["title"],
                Job.company == job_data["company"]
            )
            res = await session.execute(stmt)
            existing_job = res.scalars().first()
            
            if existing_job:
                continue
                
            logger.info(f"Ingesting new job: {job_data['title']} at {job_data['company']}")
            
            # Combine fields to build a representation for embedding
            combined_text = (
                f"Title: {job_data['title']}\n"
                f"Company: {job_data['company']}\n"
                f"Location: {job_data['location']}\n"
                f"Description: {job_data['description'][:1000]}\n" # limit description size for embedding
                f"Requirements: {job_data['requirements']}"
            )
            
            try:
                # Generate embedding
                embedding_vector = await generate_embedding(combined_text)
            except Exception as e:
                logger.error(f"Failed to generate embedding for job {job_data['title']}: {e}")
                # Use fallback zero vector if API fails to avoid breaking ingestion
                embedding_vector = [0.0] * 768

            job = Job(
                id=uuid.uuid4(),
                title=job_data["title"],
                company=job_data["company"],
                location=job_data["location"],
                description=job_data["description"],
                requirements=job_data["requirements"],
                embedding=embedding_vector
            )
            session.add(job)
            new_jobs_added += 1
            
            # Keep rate limits in mind (max 1 request per second)
            await asyncio.sleep(1.2)
            
            # Commit periodically
            if new_jobs_added % 5 == 0:
                await session.commit()

        await session.commit()
        logger.info(f"Job ingestion cycle complete. Added {new_jobs_added} new jobs.")
