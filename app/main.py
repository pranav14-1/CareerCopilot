import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.core.config import settings
from app.core.database import verify_database_connection, verify_redis_connection, redis_client, init_db

# Setup basic logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# --- OpenTelemetry Instrumentation Setup ---
def setup_telemetry(app: FastAPI) -> None:
    """Initialize OpenTelemetry tracer and instrument FastAPI."""
    try:
        # Create resource attributes
        resource = Resource.create(attributes={
            "service.name": settings.OTEL_SERVICE_NAME
        })

        provider = TracerProvider(resource=resource)
        
        # Configure OTLP Exporter if configured
        if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                otlp_processor = BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
                )
                provider.add_span_processor(otlp_processor)
                logger.info(f"OpenTelemetry registered with OTLP endpoint: {settings.OTEL_EXPORTER_OTLP_ENDPOINT}")
            except Exception as e:
                logger.warning(f"Could not load OTLP gRPC Exporter ({e}), falling back to HTTP or Console exporter.")
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHttpSpanExporter
                    otlp_processor = BatchSpanProcessor(
                        OTLPHttpSpanExporter(endpoint=f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces")
                    )
                    provider.add_span_processor(otlp_processor)
                    logger.info("OpenTelemetry registered with OTLP HTTP Exporter.")
                except Exception as e_http:
                    logger.warning(f"Could not load OTLP HTTP Exporter ({e_http}), falling back to console.")
                    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            # Fallback to console trace logging in debug/development
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("OpenTelemetry registered with Console Span Exporter.")

        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI application instrumented with OpenTelemetry.")

    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry instrumentation: {e}", exc_info=True)


# --- Lifespan Handler ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan events for FastAPI.
    Handles startup connection checks and shutdown cleanups.
    """
    logger.info("Starting up CareerCopilot API Service...")
    
    # Verify connections
    db_ok = await verify_database_connection()
    redis_ok = await verify_redis_connection()
    
    if not db_ok:
        logger.error("CRITICAL: Database connection verification failed during startup.")
    else:
        try:
            await init_db()
        except Exception as e:
            logger.error(f"Failed to auto-create database tables on startup: {e}")
            
    if not redis_ok:
        logger.error("CRITICAL: Redis connection verification failed during startup.")

    # Start bot if token is configured
    bot_app = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_TOKEN not in ("your-telegram-bot-token-here", "placeholder_token"):
        try:
            from app.bot.handler import create_bot_app
            logger.info("Initializing Telegram Bot...")
            bot_app = create_bot_app()
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling()
            logger.info("Telegram Bot started and polling successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize/start Telegram Bot: {e}")

    yield

    logger.info("Shutting down CareerCopilot API Service...")
    
    # Stop bot if running
    if bot_app:
        try:
            logger.info("Stopping Telegram Bot...")
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
            logger.info("Telegram Bot stopped successfully.")
        except Exception as e:
            logger.error(f"Failed to stop Telegram Bot gracefully: {e}")

    # Close Redis connections
    await redis_client.close()
    logger.info("Closed connections.")


# --- FastAPI Instance ---
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API service for the Telegram-based AI Career Copilot.",
    version="0.1.0",
    debug=settings.DEBUG,
    lifespan=lifespan
)

# Apply Telemetry
setup_telemetry(app)


# --- Middleware ---
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Timing and tracing logging middleware."""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.info(f"{request.method} {request.url.path} completed in {process_time:.4f}s with status {response.status_code}")
    return response


# --- Endpoints ---
@app.get("/health", tags=["System"])
async def health_check():
    """Service health endpoint indicating status of backend, DB and Redis."""
    db_status = "unverified"
    redis_status = "unverified"
    
    try:
        db_status = "connected" if await verify_database_connection() else "disconnected"
    except Exception:
        db_status = "error"
        
    try:
        redis_status = "connected" if await verify_redis_connection() else "disconnected"
    except Exception:
        redis_status = "error"

    overall_status = "ok" if db_status == "connected" and redis_status == "connected" else "degraded"
    
    return {
        "status": overall_status,
        "services": {
            "database": db_status,
            "redis": redis_status
        }
    }
