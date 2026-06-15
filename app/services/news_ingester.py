import logging
import httpx
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import delete

import instructor
from app.models.schemas import NewsArticle
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Target feeds
RSS_FEEDS = {
    "Towards Data Science": "https://towardsdatascience.com/feed",
    "r/MachineLearning": "https://www.reddit.com/r/MachineLearning/.rss",
    "r/cscareerquestions": "https://www.reddit.com/r/cscareerquestions/.rss"
}

# Standard custom User-Agent to prevent Reddit and other platforms from blocking requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CareerCopilotNewsBot/1.0"
}


class IngestedArticle(BaseModel):
    title: str = Field(..., description="Cleaned, engaging title of the tech story")
    url: str = Field(..., description="Original URL link of the story")
    summary: str = Field(..., description="A crisp 2-3 sentence summary outlining what the article is about and its technical core")
    source: str = Field(..., description="Short source identifier: 'Hacker News', 'Towards Data Science', 'r/MachineLearning', or 'r/cscareerquestions'")


class AggregatedNewsFeed(BaseModel):
    articles: List[IngestedArticle] = Field(..., description="Top 10-12 curated, deduplicated tech news articles")


def parse_feed_xml(xml_content: str, source_name: str) -> List[Dict[str, Any]]:
    """
    Parses RSS or Atom XML content using standard xml.etree.ElementTree.
    Extracts titles, URLs, and summaries.
    """
    articles = []
    try:
        # Strip encoding declaration if present to avoid parser issues
        if xml_content.strip().startswith("<?xml"):
            idx = xml_content.find(">")
            if idx != -1:
                xml_content = xml_content[idx+1:]
        
        root = ET.fromstring(xml_content.strip())
        
        # 1. Try RSS <item> format
        items = root.findall(".//item")
        if items:
            for item in items:
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                
                title = title_el.text if title_el is not None else ""
                link = link_el.text if link_el is not None else ""
                description = desc_el.text if desc_el is not None else ""
                
                if title and link:
                    articles.append({
                        "title": title.strip(),
                        "url": link.strip(),
                        "summary": description.strip()[:300] if description else "",
                        "source": source_name
                    })
            return articles
            
        # 2. Try Atom <entry> format (used by subreddits)
        entries = root.findall(".//entry") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if entries:
            for entry in entries:
                title_el = entry.find("title") or entry.find("{http://www.w3.org/2005/Atom}title")
                link_el = entry.find("link") or entry.find("{http://www.w3.org/2005/Atom}link")
                
                title = title_el.text if title_el is not None else ""
                
                link = ""
                if link_el is not None:
                    link = link_el.get("href") or link_el.text or ""
                    
                desc_el = (
                    entry.find("summary") or entry.find("{http://www.w3.org/2005/Atom}summary") or
                    entry.find("content") or entry.find("{http://www.w3.org/2005/Atom}content")
                )
                description = desc_el.text if desc_el is not None else ""
                
                if title and link:
                    articles.append({
                        "title": title.strip(),
                        "url": link.strip(),
                        "summary": description.strip()[:300] if description else "",
                        "source": source_name
                    })
    except Exception as e:
        logger.error(f"Error parsing XML for {source_name}: {e}")
    return articles


async def fetch_hacker_news() -> List[Dict[str, Any]]:
    """
    Fetches top stories from Hacker News using the Firebase API.
    """
    articles = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            # Fetch top stories list
            top_stories_res = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            if top_stories_res.status_code != 200:
                logger.warning(f"Failed to fetch Hacker News top stories: Status {top_stories_res.status_code}")
                return []
                
            story_ids = top_stories_res.json()[:15]
            for s_id in story_ids:
                try:
                    story_res = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{s_id}.json")
                    if story_res.status_code == 200:
                        story = story_res.json()
                        if story and story.get("type") == "story" and story.get("url") and story.get("title"):
                            articles.append({
                                "title": story["title"],
                                "url": story["url"],
                                "summary": f"Hacker News story with {story.get('score', 0)} points and {story.get('descendants', 0)} comments.",
                                "source": "Hacker News"
                            })
                except Exception as ex:
                    logger.warning(f"Failed to fetch HN details for item {s_id}: {ex}")
    except Exception as e:
        logger.error(f"Error fetching Hacker News: {e}")
    return articles


async def fetch_and_ingest_news() -> None:
    """
    Background worker function that collects, deduplicates, summarizes,
    and stores top tech news articles in the database.
    """
    logger.info("Starting background news ingestion pipeline...")
    raw_articles = []
    
    # 1. Fetch Hacker News
    hn_articles = await fetch_hacker_news()
    raw_articles.extend(hn_articles)
    logger.info(f"Aggregated {len(hn_articles)} articles from Hacker News.")

    # 2. Fetch RSS/Atom Feeds
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        for source, url in RSS_FEEDS.items():
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    feed_articles = parse_feed_xml(res.text, source)
                    raw_articles.extend(feed_articles[:10])
                    logger.info(f"Aggregated {len(feed_articles[:10])} articles from {source}.")
                else:
                    logger.warning(f"Failed to fetch feed {source} (Status {res.status_code})")
            except Exception as e:
                logger.error(f"Error fetching feed {source}: {e}")

    if not raw_articles:
        logger.warning("No raw articles aggregated from any feed sources. Ingestion skipped.")
        return

    # 3. Deduplicate and summarize via Gemini
    logger.info("Initializing Instructor for news summarization...")
    try:
        instructor_client = instructor.from_provider(
            "google/gemini-2.5-flash",
            async_client=True,
        )

        prompt = (
            "You are an expert technical editor. Below is a raw list of aggregated tech news "
            "and articles from Hacker News, Reddit subreddits, and RSS blogs.\n"
            "Analyze the list, filter out non-technical or low-value articles, and deduplicate similar stories.\n"
            "Select the top 10-12 most significant technical updates for a software / AI engineer.\n"
            "For each selected story, write a polished 2-3 sentence technical summary and clean up titles.\n\n"
            "=== RAW AGGREGATED STORIES ===\n"
        )
        for idx, art in enumerate(raw_articles):
            prompt += (
                f"\nStory {idx+1}:\n"
                f"Title: {art['title']}\n"
                f"Source: {art['source']}\n"
                f"URL: {art['url']}\n"
                f"Context: {art['summary'][:150]}\n"
            )

        response, raw = await instructor_client.create_with_completion(
            response_model=AggregatedNewsFeed,
            messages=[
                {"role": "user", "content": prompt}
            ],
            config={"temperature": 0.2}
        )

        # Log Token Usage
        usage = getattr(raw, "usage_metadata", None)
        if usage:
            logger.info(
                f"[News Ingestion] Prompt tokens: {usage.prompt_token_count}, "
                f"Completion tokens: {usage.candidates_token_count}, "
                f"Total: {usage.total_token_count}"
            )

        # 4. Save to Database
        async with AsyncSessionLocal() as db:
            saved_count = 0
            for article in response.articles:
                try:
                    stmt = insert(NewsArticle).values(
                        title=article.title,
                        url=article.url,
                        summary=article.summary,
                        source=article.source,
                        created_at=datetime.utcnow()
                    )
                    stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
                    await db.execute(stmt)
                    saved_count += 1
                except Exception as ex:
                    logger.warning(f"Failed to insert news article {article.title}: {ex}")
            
            # 5. Clean up old stories (> 48 hours)
            cutoff = datetime.utcnow() - timedelta(hours=48)
            delete_stmt = delete(NewsArticle).where(NewsArticle.created_at < cutoff)
            await db.execute(delete_stmt)
            
            await db.commit()
            logger.info(f"Ingested {saved_count} new articles and pruned older stories from db.")

    except Exception as e:
        logger.error(f"Failed to deduplicate and ingest tech news: {e}", exc_info=True)
