# ==========================================
# Multi-Stage Build Dockerfile for FastAPI App
# ==========================================

# --- Stage 1: Builder ---
FROM python:3.11-slim AS builder

WORKDIR /build

# Upgrade pip and install virtualenv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=1000 --upgrade pip && \
    pip install --default-timeout=1000 -r requirements.txt

# --- Stage 2: Runner ---
FROM python:3.11-slim AS runner

WORKDIR /app

# Install system dependencies, including fonts for Typst resume compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root system user and group
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -s /bin/bash appuser

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Playwright browser binaries and their OS dependencies
# This must be run as root to allow installing apt packages
RUN playwright install --with-deps chromium

# Copy project files and set ownership
COPY --chown=appuser:appuser . /app

# Expose FastAPI port
EXPOSE 8000

# Prefer IPv4 over IPv6 in DNS resolution to prevent api.telegram.org timeouts
RUN sed -i 's/#precedence ::ffff:0:0\/96  100/precedence ::ffff:0:0\/96  100/' /etc/gai.conf || true

# Switch to non-root user
USER appuser

# Default execution command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

