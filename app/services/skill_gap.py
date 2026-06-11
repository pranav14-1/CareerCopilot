import logging
import json
import instructor
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.schemas import Job
from app.core.config import settings

logger = logging.getLogger(__name__)


class SkillGapItem(BaseModel):
    skill: str = Field(..., description="Name of the missing skill or tool")
    priority: str = Field(..., description="Priority level: High, Medium, or Low")
    importance: str = Field(..., description="Why this skill/tool is critical for target roles like AI Engineer / Backend Developer / FDE")


class LearningWeek(BaseModel):
    week: str = Field(..., description="e.g., 'Week 1: Foundations', 'Week 2: Advanced Integration'")
    focus: str = Field(..., description="Core learning focus for this week")
    resources: List[str] = Field(..., description="Recommended high-quality online courses, documentation links, or tutorials (mix of free and paid)")
    action_items: List[str] = Field(..., description="Specific practical actions the candidate should perform this week")


class SuggestedProject(BaseModel):
    title: str = Field(..., description="Project Title")
    description: str = Field(..., description="Brief summary of the application or feature to build")
    key_deliverables: List[str] = Field(..., description="Key technical features or deliverables of the project")


class SkillGapRoadmap(BaseModel):
    gaps: List[SkillGapItem] = Field(..., description="List of 5-7 key identified skill gaps compared against the job market")
    weekly_plan: List[LearningWeek] = Field(..., description="4-week practical weekly learning plan")
    suggested_projects: List[SuggestedProject] = Field(..., description="1-2 suggested practical projects to bridge the gaps")


async def analyze_skill_gaps(user_profile: Dict[str, Any], db) -> Dict[str, Any]:
    """
    Compares the candidate profile against job postings in the database,
    identifies top skill gaps, and generates a personalized learning roadmap.
    """
    logger.info("Fetching jobs from database to analyze market requirements...")
    # Fetch up to 50 latest jobs to represent the current job market
    stmt = select(Job).order_by(Job.created_at.desc()).limit(50)
    res = await db.execute(stmt)
    jobs = res.scalars().all()
    
    if not jobs:
        logger.warning("No jobs found in the database. Relying on default market knowledge.")
        market_jobs_str = (
            "No specific job market data available in DB. Assume general Junior/Mid Backend/AI/Forward "
            "Deployed Engineering market requirements."
        )
    else:
        # Aggregate job requirements
        jobs_list = []
        for idx, job in enumerate(jobs):
            jobs_list.append(
                f"- Job {idx+1}: {job.title} at {job.company}\n"
                f"  Location: {job.location}\n"
                f"  Requirements: {job.requirements or 'N/A'}\n"
                f"  Description: {job.description[:400]}"
            )
        market_jobs_str = "\n\n".join(jobs_list)

    logger.info("Initializing Instructor for skill gap analysis...")
    instructor_client = instructor.from_provider(
        "google/gemini-1.5-flash",
        async_client=True,
    )
    
    system_prompt = (
        "You are an elite career development strategist and tech recruiter specializing in "
        "AI Engineering, Backend, and Forward Deployed Engineering roles.\n"
        "Your task is to analyze the candidate's profile against the provided job openings to identify "
        "5-7 critical skill gaps, and generate a concrete, weekly learning roadmap (typically 4 weeks) "
        "with specific project recommendations to help them bridge these gaps."
    )
    
    user_prompt = (
        f"=== CANDIDATE RESUME PROFILE ===\n"
        f"{json.dumps(user_profile, indent=2)}\n\n"
        f"=== CURRENT JOB MARKET REQUIREMENTS ===\n"
        f"{market_jobs_str}\n\n"
        f"Identify the top 5-7 skill gaps and output the complete evaluation and weekly roadmap."
    )
    
    try:
        response, raw = await instructor_client.create_with_completion(
            response_model=SkillGapRoadmap,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            validation_context={"temperature": 0.2}
        )
        
        # Log Token Usage
        usage = getattr(raw, "usage_metadata", None)
        if usage:
            logger.info(
                f"[Skill Gap] Prompt tokens: {usage.prompt_token_count}, "
                f"Completion tokens: {usage.candidates_token_count}, "
                f"Total: {usage.total_token_count}"
            )
            
        return response.model_dump()
    except Exception as e:
        logger.error(f"Failed to generate skill gap roadmap: {e}", exc_info=True)
        raise e
