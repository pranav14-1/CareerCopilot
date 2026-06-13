import logging
import time
from typing import Dict, Any
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter

logger = logging.getLogger(__name__)

# Try to initialize Metrics Provider if not already set up
try:
    # Set up a console metric reader for development observability
    reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    logger.info("OpenTelemetry MeterProvider initialized successfully.")
except Exception as e:
    logger.warning(f"OpenTelemetry MeterProvider initialization skipped or already configured: {e}")

# Get meter reference
meter = metrics.get_meter("careercopilot.meter")

# 1. Custom Counters
token_counter = meter.create_counter(
    name="careercopilot_llm_tokens_total",
    description="Tracks total prompt and completion tokens processed by the LLMs",
    unit="1"
)

cost_counter = meter.create_counter(
    name="careercopilot_llm_cost_usd",
    description="Tracks estimated cost of LLM queries in USD",
    unit="USD"
)

operation_counter = meter.create_counter(
    name="careercopilot_operations_total",
    description="Tracks count of core operations executed",
    unit="1"
)

# 2. Custom Histograms
latency_histogram = meter.create_histogram(
    name="careercopilot_operation_latency_ms",
    description="Tracks execution duration of core workflows in milliseconds",
    unit="ms"
)


def record_llm_metrics(user_id: int, operation: str, prompt_tokens: int, completion_tokens: int, duration_ms: float) -> None:
    """
    Helper to record LLM token usage, cost, and latency under OpenTelemetry metrics and traces.
    """
    # Gemini 1.5 Flash Pricing
    cost = (prompt_tokens * 0.000000075) + (completion_tokens * 0.00000030)
    
    # 1. Record Metrics
    attributes = {"user_id": str(user_id), "operation": operation}
    token_counter.add(prompt_tokens, {**attributes, "type": "prompt"})
    token_counter.add(completion_tokens, {**attributes, "type": "completion"})
    cost_counter.add(cost, attributes)
    latency_histogram.record(duration_ms, attributes)
    
    # 2. Record Traces
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        current_span.set_attribute("llm.prompt_tokens", prompt_tokens)
        current_span.set_attribute("llm.completion_tokens", completion_tokens)
        current_span.set_attribute("llm.cost_usd", cost)
        current_span.set_attribute("llm.duration_ms", duration_ms)
        current_span.set_attribute("user.id", user_id)
        current_span.set_attribute("operation.name", operation)

    logger.info(
        f"[Telemetry] User {user_id} | Op: {operation} | "
        f"Prompt tokens: {prompt_tokens} | Completion tokens: {completion_tokens} | "
        f"Cost: ${cost:.6f} | Latency: {duration_ms:.2f}ms"
    )


def record_operation_metrics(user_id: int, operation: str, duration_ms: float) -> None:
    """
    Helper to record non-LLM operation latency and executions under OpenTelemetry.
    """
    attributes = {"user_id": str(user_id), "operation": operation}
    operation_counter.add(1, attributes)
    latency_histogram.record(duration_ms, attributes)

    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        current_span.set_attribute("operation.name", operation)
        current_span.set_attribute("operation.duration_ms", duration_ms)
        current_span.set_attribute("user.id", user_id)

    logger.info(f"[Telemetry] User {user_id} | Op: {operation} | Latency: {duration_ms:.2f}ms")
