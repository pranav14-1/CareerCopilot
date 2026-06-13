# CareerCopilot

## 📚 Project Overview
CareerCopilot is an **AI‑powered Telegram assistant** that helps BTech students and early‑career engineers discover, evaluate, and tailor technology job opportunities, with a focus on high‑quality Indian tech portals. It turns a raw PDF resume into a structured profile, performs hybrid lexical‑semantic search, and uses a large language model (Gemini‑1.5‑flash) to evaluate relevance, suggest skill‑gap roadmaps, and generate a customized resume for any selected job.

---

## 🚀 Core Capabilities
- **Resume Ingestion** – Users upload a PDF; the system parses the document, extracts a structured profile, and creates a 768‑dimensional embedding for semantic search.
- **Two‑Stage Hybrid Search** – BM25 lexical retrieval + pgvector semantic retrieval, merged with Reciprocal Rank Fusion (RRF). Explicit role/keyword and location signals receive extra weighting; Indian portals (Instahyre, Cutshort, Hirist) receive a dedicated boost.
- **AI‑Driven Evaluation** – Gemini‑1.5‑flash evaluates each candidate job, producing a `JobMatchEvaluation` (score, reasoning, skill‑gap list).
- **Skill‑Gap Roadmaps** – Automatically suggests missing skills and learning resources based on the profile‑to‑job gap analysis.
- **Resume Tailoring Agent** – A LangGraph multi‑agent graph extracts required skills, identifies gaps, and generates a customized PDF resume for any selected job.
- **Observability** – OpenTelemetry instrumentation, latency histograms, operation counters, and optional LangSmith tracing give full visibility into request flow and performance.

---

## 🏗️ Architecture Snapshot
```
CareerCopilot
│
├─ app/
│   ├─ core/            # Config, DB engine, OpenTelemetry helpers
│   ├─ models/          # SQLAlchemy schemas (User, Job, etc.)
│   ├─ services/        # job_ingester, search, parser, analytics
│   ├─ agents/          # LangGraph resume‑tailor workflow
│   ├─ bot/             # Telegram command & message handlers
│   └─ main.py          # FastAPI entry point & bot startup logic
│
├─ docker-compose.yml  # DB, Redis, FastAPI containers (production ready)
└─ README.md           # (this file)
```
* **FastAPI** serves the HTTP API and hosts background jobs.
* **Telegram Bot** (python‑telegram‑bot) interacts with users, routing commands to the service layer.
* **Job Ingester** scrapes Instahyre, Cutshort.io, and Hirist.tech using Playwright with stealth, random delays, and strict field truncation to keep the database stable.
* **Search Service** performs the hybrid retrieval and RRF fusion, applying location and portal boosts before passing candidates to the LLM reranker.
* **User Service** stores the extracted profile, embedding, and experience level in PostgreSQL.
* **Analytics** records operation latency and counts in Redis/OpenTelemetry for monitoring.

---

## 🔍 Search & Ranking Details
1. **Stage 1 – Retrieval**
   - BM25 lexical index over job title/description.
   - pgvector semantic index over embedding vectors.
   - Top‑5 candidates from each source are merged.
2. **Stage 2 – Rerank**
   - Gemini LLM evaluates each candidate, producing a score (0‑100), reasoning, and a list of missing skills.
3. **RRF Fusion**
   - Scores from lexical, semantic, and preference (role/keyword) paths are combined.
   - Multipliers: 2× for explicit role/keyword, +0.15 for location match, +0.15 portal boost for Indian sources.
4. **Result Presentation**
   - Telegram messages display job title, company, location, match score, reasoning, and identified skill gaps, with inline buttons for detail view or resume tailoring.

---

## 🤖 Agentic Resume Tailoring Workflow
The LangGraph graph orchestrates four sequential steps:
1. **Skill Extraction** – Pull required skills from the selected job posting.
2. **Gap Identification** – Compare required skills with the user's extracted profile.
3. **Prompt Construction** – Build a detailed LLM prompt that includes the gap list and the original resume.
4. **PDF Generation** – Gemini generates a tailored resume PDF, cached in Redis for fast reuse.

---

## 📈 Observability & Metrics
- **OpenTelemetry** – Traces each API call and bot interaction; optional OTLP exporter for external back‑ends.
- **Metrics** – Histograms for query latency, counters for `job_search`, `resume_tailor`, and other core operations.
- **Redis** – Stores transient state (user query, experience level) and cached evaluations to avoid re‑computations.

---

## 🛠️ Extensibility
- **Add New Sources** – Extend `SCRAPING_SOURCES` in `.env` and implement a Playwright scraper in `app/services/job_ingester.py`.
- **Custom Boosts** – Adjust RRF weighting constants in `app/services/search.py` to prioritize different portals or attributes.
- **Plug‑In New Agents** – Create additional LangGraph graphs for tasks such as interview question generation or salary negotiation assistance.

---

