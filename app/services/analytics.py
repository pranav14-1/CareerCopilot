import logging
from typing import Dict, Any, List
from app.core.database import redis_client
from app.core.observability import record_llm_metrics, record_operation_metrics

logger = logging.getLogger(__name__)


async def track_event(
    user_id: int,
    event_type: str,
    latency_ms: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0
) -> None:
    """
    Registers an application analytics event.
    Updates Redis counter keys, rolling average latency queues, and triggers OpenTelemetry hooks.
    """
    cost = (prompt_tokens * 0.000000075) + (completion_tokens * 0.00000030)
    
    # 1. Record via OpenTelemetry
    try:
        if prompt_tokens > 0 or completion_tokens > 0:
            record_llm_metrics(user_id, event_type, prompt_tokens, completion_tokens, latency_ms)
        else:
            record_operation_metrics(user_id, event_type, latency_ms)
    except Exception as otel_err:
        logger.warning(f"Failed to record OTel telemetry attributes: {otel_err}")

    # 2. Update Redis Statistics
    try:
        # Counters
        await redis_client.incrby(f"stats:system:events:{event_type}", 1)
        await redis_client.incrby(f"stats:user:{user_id}:events:{event_type}", 1)

        if prompt_tokens > 0 or completion_tokens > 0:
            await redis_client.incrby("stats:system:tokens:prompt", prompt_tokens)
            await redis_client.incrby("stats:system:tokens:completion", completion_tokens)
            await redis_client.incrbyfloat("stats:system:cost", cost)

            await redis_client.incrby(f"stats:user:{user_id}:tokens:prompt", prompt_tokens)
            await redis_client.incrby(f"stats:user:{user_id}:tokens:completion", completion_tokens)
            await redis_client.incrbyfloat(f"stats:user:{user_id}:cost", cost)

        # Rolling latencies (System keeps last 100, User keeps last 50)
        await redis_client.lpush("stats:system:latencies", latency_ms)
        await redis_client.ltrim("stats:system:latencies", 0, 99)

        await redis_client.lpush(f"stats:user:{user_id}:latencies", latency_ms)
        await redis_client.ltrim(f"stats:user:{user_id}:latencies", 0, 49)

    except Exception as redis_err:
        logger.error(f"Failed to update Redis analytics counters: {redis_err}", exc_info=True)


async def get_average_latency(redis_key: str) -> float:
    """Computes average from a rolling list of latencies stored in Redis."""
    try:
        latencies = await redis_client.lrange(redis_key, 0, -1)
        if not latencies:
            return 0.0
        return sum(float(x) for x in latencies) / len(latencies)
    except Exception as e:
        logger.warning(f"Error computing average latency for {redis_key}: {e}")
        return 0.0


async def get_system_stats() -> Dict[str, Any]:
    """
    Compiles system-wide aggregated metrics.
    """
    try:
        jobs_searched = int(await redis_client.get("stats:system:events:job_search") or 0)
        resumes_tailored = int(await redis_client.get("stats:system:events:resume_tailor") or 0)
        gaps_analyzed = int(await redis_client.get("stats:system:events:skill_gap") or 0)
        news_generated = int(await redis_client.get("stats:system:events:news_briefing") or 0)

        prompt_tokens = int(await redis_client.get("stats:system:tokens:prompt") or 0)
        completion_tokens = int(await redis_client.get("stats:system:tokens:completion") or 0)
        total_cost = float(await redis_client.get("stats:system:cost") or 0.0)

        avg_latency = await get_average_latency("stats:system:latencies")

        return {
            "jobs_searched": jobs_searched,
            "resumes_tailored": resumes_tailored,
            "skill_gaps_analyzed": gaps_analyzed,
            "news_briefings_generated": news_generated,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": total_cost,
            "avg_latency_ms": avg_latency
        }
    except Exception as e:
        logger.error(f"Failed to gather system analytics stats: {e}", exc_info=True)
        return {}


async def get_user_stats(user_id: int) -> Dict[str, Any]:
    """
    Compiles user-specific aggregated metrics.
    """
    try:
        jobs_searched = int(await redis_client.get(f"stats:user:{user_id}:events:job_search") or 0)
        resumes_tailored = int(await redis_client.get(f"stats:user:{user_id}:events:resume_tailor") or 0)
        gaps_analyzed = int(await redis_client.get(f"stats:user:{user_id}:events:skill_gap") or 0)
        news_generated = int(await redis_client.get(f"stats:user:{user_id}:events:news_briefing") or 0)

        prompt_tokens = int(await redis_client.get(f"stats:user:{user_id}:tokens:prompt") or 0)
        completion_tokens = int(await redis_client.get(f"stats:user:{user_id}:tokens:completion") or 0)
        total_cost = float(await redis_client.get(f"stats:user:{user_id}:cost") or 0.0)

        avg_latency = await get_average_latency(f"stats:user:{user_id}:latencies")

        return {
            "jobs_searched": jobs_searched,
            "resumes_tailored": resumes_tailored,
            "skill_gaps_analyzed": gaps_analyzed,
            "news_briefings_generated": news_generated,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": total_cost,
            "avg_latency_ms": avg_latency
        }
    except Exception as e:
        logger.error(f"Failed to gather user {user_id} analytics stats: {e}", exc_info=True)
        return {}
