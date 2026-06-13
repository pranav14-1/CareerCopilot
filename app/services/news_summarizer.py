import logging
import json
import instructor
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.schemas import NewsArticle
from app.core.config import settings

logger = logging.getLogger(__name__)


class PersonalizedStory(BaseModel):
    title: str = Field(..., description="Title of the news story")
    url: str = Field(..., description="Original URL of the story")
    summary: str = Field(..., description="The summary of the story")
    source: str = Field(..., description="Source of the story")
    relevance: str = Field(..., description="Explain concisely in 1 sentence why this article is relevant to the candidate's skills, goals, or experience level.")


class PersonalizedBriefing(BaseModel):
    stories: List[PersonalizedStory] = Field(..., description="List of 4-6 personalized tech news stories matched to the candidate")


async def get_personalized_briefing(user_profile: dict, db, user_id: int) -> dict:
    """
    Fetches the latest news articles from the database, compares them against the
    user's parsed resume/profile, and returns a tailored briefing of 4-6 stories
    with custom relevance explanations.
    """
    logger.info("Fetching latest tech news from database...")
    stmt = select(NewsArticle).order_by(NewsArticle.created_at.desc()).limit(15)
    res = await db.execute(stmt)
    articles = res.scalars().all()
    
    if not articles:
        logger.warning("No tech news articles found in db. Ingesting immediately...")
        # Trigger an immediate ingestion to avoid returning empty results
        from app.services.news_ingester import fetch_and_ingest_news
        try:
            await fetch_and_ingest_news()
            res = await db.execute(stmt)
            articles = res.scalars().all()
        except Exception as ex:
            logger.error(f"Immediate ingestion trigger failed: {ex}")
        
    if not articles:
        logger.warning("No articles found in DB even after ingestion. Returning empty feed.")
        return {"stories": []}

    logger.info("Initializing Instructor for news personalization...")
    instructor_client = instructor.from_provider(
        "google/gemini-2.5-flash",
        async_client=True,
    )
    
    system_prompt = (
        "You are an elite AI Career Advisor. Your task is to select the top 4-6 most relevant "
        "and interesting tech news stories from the provided database entries for the given candidate profile.\n"
        "For each selected story, you must generate a highly specific, one-sentence relevance explanation "
        "linking the topic of the story to the candidate's skills, experience, or career goals (AI, backend, etc.)."
    )
    
    news_text = ""
    for idx, art in enumerate(articles):
        news_text += (
            f"\nStory {idx+1}:\n"
            f"Title: {art.title}\n"
            f"Source: {art.source}\n"
            f"URL: {art.url}\n"
            f"Summary: {art.summary}\n"
        )
        
    user_prompt = (
        f"=== CANDIDATE PROFILE ===\n"
        f"{json.dumps(user_profile, indent=2)}\n\n"
        f"=== AVAILABLE TECH NEWS STORIES ===\n"
        f"{news_text}\n\n"
        f"Select the top 4-6 most relevant stories and write the custom relevance explanations."
    )
    
    try:
        import time
        from app.services.analytics import track_event

        start_time = time.time()
        response, raw = await instructor_client.create_with_completion(
            response_model=PersonalizedBriefing,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            validation_context={"temperature": 0.2}
        )
        duration_ms = (time.time() - start_time) * 1000
        
        # Log Token Usage
        usage = getattr(raw, "usage_metadata", None)
        if usage:
            logger.info(
                f"[News Personalization] Prompt tokens: {usage.prompt_token_count}, "
                f"Completion tokens: {usage.candidates_token_count}, "
                f"Total: {usage.total_token_count}"
            )
            await track_event(
                user_id=user_id,
                event_type="news_briefing_llm_call",
                latency_ms=duration_ms,
                prompt_tokens=usage.prompt_token_count,
                completion_tokens=usage.candidates_token_count
            )
            
        return response.model_dump()
    except Exception as e:
        logger.error(f"Failed to personalize tech briefing: {e}", exc_info=True)
        # Fallback: just return the raw first 5 articles with placeholder relevance
        fallback_stories = []
        for art in articles[:5]:
            fallback_stories.append({
                "title": art.title,
                "url": art.url,
                "summary": art.summary,
                "source": art.source,
                "relevance": "Generic relevance placeholder: relevant to broad tech/engineering trends."
            })
        return {"stories": fallback_stories}
