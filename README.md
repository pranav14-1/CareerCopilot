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