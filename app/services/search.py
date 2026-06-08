import logging
import re
import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional
import instructor
from sqlalchemy import select, func
from pydantic import BaseModel, Field

from app.models.schemas import Job, User
from app.core.database import redis_client
from app.services.parser import generate_embedding

logger = logging.getLogger(__name__)


class JobMatchEvaluation(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Match score from 0 to 100 based on candidate fit")
    reasoning: str = Field(..., description="Detailed explanation of why this score was assigned")
    skill_gaps: List[str] = Field(default_factory=list, description="Skills or technologies mentioned in the job description that the candidate lacks")


async def generate_query_embedding(query: str) -> List[float]:
    """
    Generates a 768-dimension text embedding vector using the Gemini API.
    """
    return await generate_embedding(query)


def build_tsquery_from_skills(skills: List[str]) -> str:
    """
    Formats user skills or query words into a PostgreSQL full-text search tsquery string.
    Filters out non-alphanumeric characters and joins with OR (|) operator.
    """
    terms = []
    for skill in skills:
        cleaned = re.sub(r'[^a-zA-Z0-9\-\.\#\+]', '', skill)
        if cleaned:
            terms.append(cleaned)
    if not terms:
        return ""
    return " | ".join(terms)


async def stage1_retrieval(
    user: User, 
    db: Any, 
    limit: int = 20, 
    user_query: Optional[str] = None
) -> List[Job]:
    """
    Executes a hybrid retrieval combining BM25 Full-Text Search and pgvector similarity.
    Integrates the candidate's resume and their explicit search query if provided.
    Returns the Top N candidate jobs using Reciprocal Rank Fusion (RRF).
    """
    logger.info(f"Executing Stage 1 retrieval for user {user.telegram_id} (Query: {user_query})...")

    # Extract user profile information
    profile = user.extracted_profile or {}
    skills = profile.get("skills", [])
    
    # Combined FTS vectors
    fts_vector = func.to_tsvector(
        "english",
        func.coalesce(Job.title, "") + " " +
        func.coalesce(Job.company, "") + " " +
        func.coalesce(Job.description, "") + " " +
        func.coalesce(Job.requirements, "")
    )

    # 1. Lexical Search (FTS) on Resume Skills
    lexical_skills_jobs = []
    skills_query = build_tsquery_from_skills(skills)
    if skills_query:
        ts_query_skills = func.to_tsquery("english", skills_query)
        stmt = (
            select(Job)
            .where(fts_vector.op("@@")(ts_query_skills))
            .order_by(func.ts_rank_cd(fts_vector, ts_query_skills).desc())
            .limit(30)
        )
        try:
            res = await db.execute(stmt)
            lexical_skills_jobs = res.scalars().all()
        except Exception as e:
            logger.error(f"FTS skills query failed: {e}")

    # 2. Lexical Search (FTS) on Stated Preference Query
    lexical_pref_jobs = []
    if user_query:
        # Split search query words
        query_words = user_query.split()
        pref_query = build_tsquery_from_skills(query_words)
        if pref_query:
            ts_query_pref = func.to_tsquery("english", pref_query)
            stmt = (
                select(Job)
                .where(fts_vector.op("@@")(ts_query_pref))
                .order_by(func.ts_rank_cd(fts_vector, ts_query_pref).desc())
                .limit(30)
            )
            try:
                res = await db.execute(stmt)
                lexical_pref_jobs = res.scalars().all()
            except Exception as e:
                logger.error(f"FTS preference query failed: {e}")

    # 3. Semantic Search (pgvector) on Resume Embedding
    vector_resume_jobs = []
    if user.resume_embedding is not None:
        stmt = (
            select(Job)
            .order_by(Job.embedding.cosine_distance(user.resume_embedding))
            .limit(30)
        )
        try:
            res = await db.execute(stmt)
            vector_resume_jobs = res.scalars().all()
        except Exception as e:
            logger.error(f"Vector resume query failed: {e}")

    # 4. Semantic Search (pgvector) on Stated Preference Query Embedding
    vector_pref_jobs = []
    if user_query:
        try:
            pref_embedding = await generate_query_embedding(user_query)
            stmt = (
                select(Job)
                .order_by(Job.embedding.cosine_distance(pref_embedding))
                .limit(30)
            )
            res = await db.execute(stmt)
            vector_pref_jobs = res.scalars().all()
        except Exception as e:
            logger.error(f"Vector preference query failed: {e}")

    # Merge routes using RRF
    rrf_scores = {}
    job_map = {}

    # Process all lists
    for r_list in [lexical_skills_jobs, lexical_pref_jobs, vector_resume_jobs, vector_pref_jobs]:
        for rank, job in enumerate(r_list):
            job_map[job.id] = job
            rrf_scores[job.id] = rtf_score = rrf_scores.get(job.id, 0.0) + (1.0 / (60.0 + rank + 1))

    # Fallback to recent if everything returned empty
    if not rrf_scores:
        logger.info("FTS and Vector retrieval pathways returned no results. Fallback to latest jobs.")
        stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    # Sort candidates by combined RRF scores
    sorted_job_ids = sorted(rrf_scores.keys(), key=lambda j_id: rrf_scores[j_id], reverse=True)
    return [job_map[j_id] for j_id in sorted_job_ids[:limit]]


async def stage2_rerank(
    user: User, 
    candidates: List[Job], 
    user_query: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Reranks candidate jobs using LLM reasoning and filters duplicated evaluations using Redis.
    Incorporates both the candidate's profile and current query interests.
    """
    if not candidates:
        return []

    logger.info(f"Executing Stage 2 LLM Re-ranking for user {user.telegram_id} on {len(candidates)} candidates...")
    
    user_profile = user.extracted_profile or {}
    
    # Initialize the Instructor client for Gemini
    instructor_client = instructor.from_provider(
        "google/gemini-2.5-flash",
        async_client=True,
    )
    
    semaphore = asyncio.Semaphore(3)  # Max 3 concurrent Gemini calls

    # Clean the query for key matching (hash query)
    cleaned_query = (user_query or "").strip().lower()
    query_hash = hashlib.md5(cleaned_query.encode("utf-8")).hexdigest() if cleaned_query else "none"

    async def evaluate_single_job(job: Job) -> Dict[str, Any]:
        # Cache key is user + job + query interests
        cache_key = f"job_eval:{user.telegram_id}:{job.id}:{query_hash}"
        
        # Check cache
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                logger.info(f"Redis Cache HIT for job: {job.title} ({job.id})")
                eval_dict = json.loads(cached_data)
                return {
                    "job": job,
                    "evaluation": JobMatchEvaluation.model_validate(eval_dict)
                }
        except Exception as e:
            logger.warning(f"Failed to query Redis cache: {e}")

        # Uncached: Call Gemini
        async with semaphore:
            logger.info(f"Calling Gemini Re-ranker for job: {job.title} ({job.id})")
            
            prompt = (
                "You are an elite technical recruiter.\n"
                "Evaluate how well the candidate's resume profile matches the job description, "
                "keeping in mind the candidate's stated job search interest.\n\n"
                "=== CANDIDATE RESUME ===\n"
                f"{json.dumps(user_profile, indent=2)}\n\n"
                f"=== CANDIDATE SEARCH INTEREST ===\n"
                f"{user_query or 'None stated'}\n\n"
                "=== JOB OPENING ===\n"
                f"Title: {job.title}\n"
                f"Company: {job.company}\n"
                f"Location: {job.location}\n"
                f"Description: {job.description[:1500]}\n"
                f"Requirements: {job.requirements}\n\n"
                "Assign a matching score (0-100), write a concise reasoning explaining the fit "
                "or lack thereof (incorporating how well it matches both the resume and their search interest), "
                "and specify any skill gaps."
            )
            
            try:
                evaluation: JobMatchEvaluation = await instructor_client.create(
                    response_model=JobMatchEvaluation,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    validation_context={"temperature": 0.1}
                )
                
                # Cache results in Redis for 7 days
                try:
                    await redis_client.set(
                        cache_key,
                        evaluation.model_dump_json(),
                        ex=604800
                    )
                except Exception as cache_err:
                    logger.warning(f"Failed to set Redis cache: {cache_err}")
                
                return {
                    "job": job,
                    "evaluation": evaluation
                }
            except Exception as e:
                logger.error(f"Gemini evaluation failed for job {job.id}: {e}")
                fallback = JobMatchEvaluation(
                    score=50,
                    reasoning="Failed to perform deep AI matching due to an external service error. Showing default match.",
                    skill_gaps=[]
                )
                return {
                    "job": job,
                    "evaluation": fallback
                }

    # Run evaluations concurrently
    tasks = [evaluate_single_job(job) for job in candidates]
    results = await asyncio.gather(*tasks)
    
    # Sort results by score descending
    results.sort(key=lambda x: x["evaluation"].score, reverse=True)
    return results
