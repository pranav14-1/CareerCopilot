import logging
import re
import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional
import instructor
from sqlalchemy import select, func, or_, and_
from pydantic import BaseModel, Field

from app.models.schemas import Job, User
from app.core.database import redis_client
from app.services.parser import generate_embedding

logger = logging.getLogger(__name__)


class JobMatchEvaluation(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Match score from 0 to 100 based on candidate fit")
    reasoning: str = Field(..., description="Detailed explanation of why this score was assigned")
    skill_gaps: List[str] = Field(default_factory=list, description="Skills or technologies mentioned in the job description that the candidate lacks")


class JobMatchEvaluationWithId(JobMatchEvaluation):
    job_id: str = Field(..., description="The exact UUID string of the job opening evaluated")


class BatchJobMatchEvaluations(BaseModel):
    evaluations: List[JobMatchEvaluationWithId] = Field(..., description="List of job match evaluations corresponding to each job ID passed in the request")


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


def passes_pre_filter(user_profile: Dict[str, Any], user_query: Optional[str], job: Job) -> bool:
    """
    Cheap pre-filter check based on basic keyword overlaps.
    """
    job_text = f"{job.title} {job.description} {job.requirements or ''}".lower()

    # 1. Check user query keywords (if any)
    if user_query:
        q_words = [w.lower() for w in user_query.split() if len(w) > 3 and w.lower() not in ("with", "tech", "jobs", "role", "role:", "developer", "engineer")]
        if q_words:
            for w in q_words:
                if w in job_text:
                    return True
            return False

    # 2. Check resume skills keywords
    skills = user_profile.get("skills", [])
    for skill in skills:
        cleaned_skill = skill.strip().lower()
        if len(cleaned_skill) > 2 and cleaned_skill in job_text:
            return True
            
    return False


async def stage1_retrieval(
    user: User, 
    db: Any, 
    limit: int = 10, 
    user_query: Optional[str] = None
) -> List[Job]:
    """
    Executes a hybrid retrieval combining BM25 Full-Text Search and pgvector similarity.
    Integrates the candidate's resume and their explicit search query if provided.
    Returns the Top N candidate jobs using Reciprocal Rank Fusion (RRF) with location and experience boosts.
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

    # Define strict location filter for Indian Job Market (including remote eligible for India)
    location_filter = or_(
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
        Job.location.ilike("%ncr%"),
        Job.location.ilike("%kolkata%"),
        Job.location.is_(None),
        and_(
            or_(Job.location.ilike("%remote%"), Job.location.ilike("%worldwide%")),
            Job.location.not_ilike("%us%"),
            Job.location.not_ilike("%usa%"),
            Job.location.not_ilike("%europe%"),
            Job.location.not_ilike("%germany%"),
            Job.location.not_ilike("%uk%"),
            Job.location.not_ilike("%canada%"),
            Job.location.not_ilike("%america%"),
        )
    )

    rrf_scores = {}
    job_map = {}

    # Run two-pass retrieval:
    # Pass 1: Try strict location filtering (India + Remote)
    # Pass 2: Fallback to searching without location constraints if no Indian/remote matches are found
    locations_to_check = ["india", "remote", "bangalore", "hyderabad", "delhi", "mumbai", "noida", "pune", "chennai", "gurgaon", "bengaluru", "kolkata"]
    has_explicit_location = False
    if user_query:
        q_clean = user_query.lower()
        for loc in locations_to_check:
            if loc in q_clean:
                has_explicit_location = True
                break

    for pass_num in [1, 2]:
        use_location = (pass_num == 1)
        if not use_location and has_explicit_location:
            break
        filter_cond = location_filter if use_location else None
        
        # 1. Lexical Search (FTS) on Resume Skills
        lexical_skills_jobs = []
        skills_query = build_tsquery_from_skills(skills)
        if skills_query:
            ts_query_skills = func.to_tsquery("english", skills_query)
            stmt = select(Job).where(fts_vector.op("@@")(ts_query_skills))
            if filter_cond is not None:
                stmt = stmt.where(filter_cond)
            stmt = stmt.order_by(func.ts_rank_cd(fts_vector, ts_query_skills).desc()).limit(30)
            try:
                res = await db.execute(stmt)
                lexical_skills_jobs = res.scalars().all()
            except Exception as e:
                logger.error(f"FTS skills query failed: {e}")

        # 2. Lexical Search (FTS) on Stated Preference Query
        lexical_pref_jobs = []
        if user_query:
            query_words = user_query.split()
            pref_query = build_tsquery_from_skills(query_words)
            if pref_query:
                ts_query_pref = func.to_tsquery("english", pref_query)
                stmt = select(Job).where(fts_vector.op("@@")(ts_query_pref))
                if filter_cond is not None:
                    stmt = stmt.where(filter_cond)
                stmt = stmt.order_by(func.ts_rank_cd(fts_vector, ts_query_pref).desc()).limit(30)
                try:
                    res = await db.execute(stmt)
                    lexical_pref_jobs = res.scalars().all()
                except Exception as e:
                    logger.error(f"FTS preference query failed: {e}")

        # 3. Semantic Search (pgvector) on Resume Embedding
        vector_resume_jobs = []
        if user.resume_embedding is not None:
            stmt = select(Job)
            if filter_cond is not None:
                stmt = stmt.where(filter_cond)
            stmt = stmt.order_by(Job.embedding.cosine_distance(user.resume_embedding)).limit(30)
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
                stmt = select(Job)
                if filter_cond is not None:
                    stmt = stmt.where(filter_cond)
                stmt = stmt.order_by(Job.embedding.cosine_distance(pref_embedding)).limit(30)
                res = await db.execute(stmt)
                vector_pref_jobs = res.scalars().all()
            except Exception as e:
                logger.error(f"Vector preference query failed: {e}")

        # Merge routes using RRF with double weight for user preference queries (role & keywords)
        rrf_scores.clear()
        job_map.clear()
        for idx, r_list in enumerate([lexical_skills_jobs, lexical_pref_jobs, vector_resume_jobs, vector_pref_jobs]):
            weight_multiplier = 2.0 if idx in [1, 3] else 1.0
            for rank, job in enumerate(r_list):
                job_map[job.id] = job
                rrf_scores[job.id] = rrf_scores.get(job.id, 0.0) + weight_multiplier * (1.0 / (60.0 + rank + 1))

        # If we found matches or we are already in the fallback pass, break
        if rrf_scores or not use_location:
            if not use_location and rrf_scores:
                logger.info("No Indian opportunities matched. Fallback pass retrieved relevant global/other options.")
            break

    # Apply boosts for location and experience level
    user_exp = (user.extracted_profile or {}).get("experience_level", "Fresher").strip()
    
    # Extract location keywords from query
    locations_to_check = ["india", "remote", "bangalore", "hyderabad", "delhi", "mumbai", "noida", "pune", "chennai", "gurgaon"]
    target_locations = []
    has_explicit_location = False
    if user_query:
        q_clean = user_query.lower()
        for loc in locations_to_check:
            if loc in q_clean:
                target_locations.append(loc)
        has_explicit_location = bool(target_locations)
    
    # Default to India + Remote if none specified
    if not target_locations:
        target_locations = ["india", "remote"]

    for job_id, job in job_map.items():
        boost = 0.0
        job_loc = (job.location or "").lower()
        job_text = f"{job.title} {job.description} {job.requirements or ''}".lower()

        # 1. Location boosting (higher boost if user explicitly requested this location)
        for target in target_locations:
            if target in job_loc:
                boost += 0.05  # RRF score boost for location match
                if has_explicit_location:
                    boost += 0.15  # Extra boost for matching explicitly specified user location

        # 3. Source boosting for Indian users (Instahyre, Cutshort, Hirist)
        job_url = (job.url or "").lower()
        if "instahyre" in job_url or "cutshort" in job_url or "hirist" in job_url:
            boost += 0.15  # Additional boost for preferred Indian portals (increased from 0.08)


        # 2. Experience level boosting
        uexp_lower = user_exp.lower()
        if "fresher" in uexp_lower or "0-1" in uexp_lower:
            junior_terms = ["junior", "entry level", "fresher", "intern", "0-2 years", "0-1 years", "graduate", "associate"]
            for term in junior_terms:
                if term in job_text:
                    boost += 0.05
                    break
        elif "1-3" in uexp_lower:
            mid_terms = ["1-3 years", "2-3 years", "mid level", "associate", "1-2 years", "mid-level"]
            for term in mid_terms:
                if term in job_text:
                    boost += 0.05
                    break
        elif "3-5" in uexp_lower:
            mid_senior_terms = ["3-5 years", "3-4 years", "mid-senior", "senior", "lead"]
            for term in mid_senior_terms:
                if term in job_text:
                    boost += 0.05
                    break
        elif "5+" in uexp_lower:
            senior_terms = ["senior", "lead", "staff", "principal", "5+ years", "5-8 years", "architect"]
            for term in senior_terms:
                if term in job_text:
                    boost += 0.05
                    break

        rrf_scores[job_id] += boost

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
    user_query: Optional[str] = None,
    refine: bool = False
) -> List[Dict[str, Any]]:
    """
    Reranks candidate jobs using LLM reasoning (via single structured batch call).
    Always provides fresh evaluation results.
    """
    if not candidates:
        return []

    logger.info(f"Executing Stage 2 LLM Re-ranking for user {user.telegram_id} on {len(candidates)} candidates...")
    
    user_profile = user.extracted_profile or {}
    user_id = user.telegram_id
    exp_clean = user_profile.get("experience_level", "Fresher").strip().lower()

    results = []
    
    # Cheap pre-filtering before LLM call
    jobs_to_llm = []
    for job in candidates:
        if passes_pre_filter(user_profile, user_query, job):
            jobs_to_llm.append(job)
        else:
            # Low score for failed pre-filter
            fallback_eval = JobMatchEvaluation(
                score=30,
                reasoning="Did not pass initial skill keyword pre-filtering.",
                skill_gaps=[]
            )
            results.append({
                "job": job,
                "evaluation": fallback_eval
            })

    # Call Gemini in batch for jobs that passed the pre-filter
    if jobs_to_llm:
        logger.info(f"Calling Gemini Batch Re-ranker for {len(jobs_to_llm)} jobs...")
        instructor_client = instructor.from_provider(
            "google/gemini-2.5-flash",
            async_client=True,
        )
        
        jobs_list_str = ""
        for idx, job in enumerate(jobs_to_llm):
            jobs_list_str += f"\n--- JOB {idx + 1} ---\n"
            jobs_list_str += f"ID: {job.id}\n"
            jobs_list_str += f"Title: {job.title}\n"
            jobs_list_str += f"Company: {job.company}\n"
            jobs_list_str += f"Location: {job.location}\n"
            jobs_list_str += f"Description: {job.description[:1000]}\n"
            jobs_list_str += f"Requirements: {job.requirements or 'N/A'}\n"

        prompt = (
            "You are an elite technical recruiter.\n"
            "Evaluate how well the candidate's resume profile matches each of the following job openings, "
            "keeping in mind the candidate's stated job search interest and experience level.\n\n"
            "=== CANDIDATE RESUME ===\n"
            f"{json.dumps(user_profile, indent=2)}\n\n"
            f"=== CANDIDATE SEARCH INTEREST ===\n"
            f"{user_query or 'None stated'}\n\n"
            f"=== CANDIDATE EXPERIENCE LEVEL ===\n"
            f"{exp_clean}\n\n"
            "=== JOB OPENINGS TO EVALUATE ===\n"
            f"{jobs_list_str}\n\n"
            "For each job opening, assign a matching score (0-100), write a concise reasoning explaining the fit "
            "or lack thereof (incorporating how well it matches both the resume, experience level, and their search interest), "
            "and specify any skill gaps."
        )

        try:
            import time
            from app.services.analytics import track_event

            start_time = time.time()
            batch_response, raw = await instructor_client.create_with_completion(
                response_model=BatchJobMatchEvaluations,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                config={"temperature": 0.1}
            )
            duration_ms = (time.time() - start_time) * 1000

            # Record LLM usage
            prompt_tokens = 0
            completion_tokens = 0
            usage = getattr(raw, "usage_metadata", None)
            if usage:
                prompt_tokens = usage.prompt_token_count
                completion_tokens = usage.candidates_token_count

            await track_event(
                user_id=user_id,
                event_type="rerank_llm_call",
                latency_ms=duration_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens
            )
            
            eval_map = {str(e.job_id): e for e in batch_response.evaluations}
            
            for job in jobs_to_llm:
                eval_obj = eval_map.get(str(job.id))
                if not eval_obj:
                    eval_obj = JobMatchEvaluation(
                        score=50,
                        reasoning="Batch AI evaluation did not return specific results for this job.",
                        skill_gaps=[]
                    )
                else:
                    eval_obj = JobMatchEvaluation(
                        score=eval_obj.score,
                        reasoning=eval_obj.reasoning,
                        skill_gaps=eval_obj.skill_gaps
                    )
                    
                results.append({
                    "job": job,
                    "evaluation": eval_obj
                })

        except Exception as batch_err:
            logger.error(f"Gemini batch evaluation failed: {batch_err}")
            for job in jobs_to_llm:
                fallback = JobMatchEvaluation(
                    score=50,
                    reasoning="Failed to perform batch AI matching due to an external service error.",
                    skill_gaps=[]
                )
                results.append({
                    "job": job,
                    "evaluation": fallback
                })

    # Sort results by score descending
    results.sort(key=lambda x: x["evaluation"].score, reverse=True)
    return results
