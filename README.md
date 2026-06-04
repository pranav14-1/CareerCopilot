# 💼 AI Career Copilot

> Telegram-based agentic job assistant designed to help engineering students build, optimize, and secure backend, AI engineering, and forward-deployed engineering opportunities.

---

## 🛠️ Tech Stack & Ecosystem

- **Backend core**: Python 3.11+, FastAPI (Fully Async Engine), SQLAlchemy, asyncpg
- **AI & Agentic Orchestration**: LangGraph (writer-critic loops), Instructor, Google GenAI (`gemini-2.5-flash`)
- **Database & Cache**: PostgreSQL 16 + `pgvector` extension, Redis (caching and rate evaluations)
- **Document Rendering**: Typst (native python-bound compiler)
- **Background Jobs**: `python-telegram-bot` (v21 async handlers), `APScheduler`
- **Observability**: OpenTelemetry SDK, LangSmith traces

---

## 📁 Repository Directory Layout

```text
app/
├── main.py                     # Entry point initializing FastAPI and OpenTelemetry
├── core/
│   ├── config.py               # Centralized configuration via pydantic-settings
│   └── database.py             # Asynchronous SQLAlchemy engine & Redis connections
├── models/
│   └── schemas.py              # Declarative SQLAlchemy database mapping models
├── bot/
│   ├── handler.py              # Core handlers for the Telegram Bot API loop
│   └── commands.py             # Implementation for commands: /start, /jobs, /learn, etc.
├── services/
│   ├── parser.py               # Resume text extraction and parsing service
│   ├── search.py               # Dual-Stage BM25 + pgvector hybrid engine implementation
│   ├── compiler.py             # Logic for Typst template injection and compilation
│   └── scheduler_tasks.py      # Background worker handling briefings and reminders
└── agents/
    ├── state.py                # Definition of LangGraph state models (TailorState)
    ├── tailor_graph.py         # Multi-agent Writer and Critic loop configuration
    └── evaluators.py           # Offline evaluation pipeline scripts
```

---

## 🚀 Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/)
- Python 3.11+ (for local development)

### 1. Environment Configuration

Copy the example environment configuration to your active `.env` file:

```bash
cp .env.example .env
```

Open `.env` and fill in your details:
- **`TELEGRAM_BOT_TOKEN`**: Register a bot with [@BotFather](https://t.me/BotFather) on Telegram and retrieve the token.
- **`GEMINI_API_KEY`**: Obtain an API key from [Google AI Studio](https://aistudio.google.com/).
- **`LANGCHAIN_API_KEY`**: Optional, for LangSmith trace views.

### 2. Local Startup via Docker Compose

Run all services including PostgreSQL (with pgvector), Redis, and the FastAPI application using Docker:

```bash
docker compose up --build
```

The database health checks will wait for pgvector and redis to be ready before initiating the web container.
Once running, the FastAPI web API is accessible at:
- **Service API**: `http://localhost:8000`
- **Swagger Docs**: `http://localhost:8000/docs`
- **Health Check**: `http://localhost:8000/health`

### 3. Manual Local Development

If you prefer to run services outside Docker for faster hot-reloading:

1. **Start DB & Redis** (Docker is easiest here):
   ```bash
   docker compose up -d db redis
   ```

2. **Setup virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Start FastAPI**:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

---

## 🔍 Observability and Monitoring

- **Traces**: Distributed trace propagation handles request tracking over OpenTelemetry. By default, spans are written to the OTLP collector (`http://localhost:4317`) or printed to stdout in the developer console.
- **Agent Steps**: If `LANGCHAIN_TRACING_V2` is set to `true`, agent graphs are pushed straight to your LangSmith dashboard.
