# AI Career Copilot

**A Production-Grade Agentic AI Assistant for Job Search and Career Development**

AI Career Copilot is a Telegram-based intelligent system built to streamline the end-to-end job hunting process. It combines semantic understanding, hybrid search, and multi-agent AI orchestration to deliver highly relevant job opportunities and personalized resume optimization.

## Project Overview

This project transforms a basic chatbot into a complete agentic AI system capable of:

- Ingesting and structuring resumes from PDF documents
- Performing intelligent job discovery using hybrid lexical and vector search
- Running a multi-agent critique loop to iteratively tailor resumes for specific roles
- Analyzing skill gaps against the current job market
- Delivering personalized technology briefings

The system is designed with production-grade architecture, emphasizing reliability, observability, and cost efficiency.

## Core Capabilities

### Semantic Onboarding
- Parses PDF resumes in memory using pdfplumber
- Extracts structured profile data using Gemini + Instructor with strict Pydantic validation
- Generates 768-dimensional embeddings and stores profiles in PostgreSQL with pgvector

### Two-Stage Hybrid Job Search
- Combines BM25 full-text search with semantic vector similarity for fast retrieval
- LLM-powered reranking with detailed reasoning and skill-gap analysis
- India-focused job ingestion from platforms including Instahyre, Cutshort, Hirist, and public APIs

### Multi-Agent Resume Tailoring
- Built using LangGraph, featuring two specialized agents:
  - **Writer Agent**: Crafts tailored resume sections based on target job descriptions
  - **ATS Critic Agent**: Evaluates alignment (0-100 score) and provides actionable feedback
- Iterative critique loop continues until the resume meets high standards or maximum iterations are reached
- Final output compiled into clean, professional PDFs using Typst

### Additional Intelligence
- Skill gap analysis comparing user profile against active job market requirements
- Personalized weekly learning roadmaps
- Automated tech news aggregation and summarization

### Production Features
- Full async architecture with FastAPI
- Redis caching for performance and cost optimization
- Comprehensive observability using OpenTelemetry and LangSmith
- Robust error handling and multi-tenancy isolation

## Technical Architecture

- **Backend**: FastAPI (async), SQLAlchemy + asyncpg
- **Agent Framework**: LangGraph (StateGraph with conditional routing)
- **AI Layer**: Gemini 1.5 Flash + Instructor for structured outputs
- **Vector Database**: PostgreSQL 16 + pgvector
- **Document Engine**: Typst for high-quality PDF generation
- **Scraping**: Playwright (responsible, rate-limited usage)
- **Monitoring**: OpenTelemetry, LangSmith

## Key Learnings & Highlights

This project served as a deep dive into building agentic AI systems. The multi-agent resume tailoring workflow demonstrates practical implementation of autonomous AI collaboration, state management, and iterative reasoning — core concepts in modern agentic AI development.

The system reflects production thinking through proper caching strategies, observability, async design, and cost-aware LLM usage.

---
