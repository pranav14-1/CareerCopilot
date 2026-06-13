import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from html import escape

from app.core.database import AsyncSessionLocal
from app.services.user_service import get_user_profile

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /start command. Welcomes user and prompts for resume upload.
    """
    welcome_text = (
        "👋 <b>Welcome to AI Career Copilot!</b>\n\n"
        "I am your agentic job assistant, designed to help BTech students break "
        "into AI, Backend, and Forward Deployed Engineering roles.\n\n"
        "⚡ <b>Getting Started:</b>\n"
        "1. Send me your resume as a <b>PDF document</b> (max 5MB).\n"
        "2. I'll parse it and build your semantic profile.\n"
        "3. Use /jobs to find hyper-relevant matches!\n\n"
        "<b>Available commands:</b>\n"
        "/start - Welcome and onboarding info\n"
        "/profile - Inspect your parsed profile\n"
        "/jobs - Two-stage hybrid job matches\n"
        "/tailor - Adapt your resume for a job\n"
        "/learn - Generate missing skill roadmaps\n"
        "/news - View personalized tech briefings\n"
        "/stats - View system trace and cost statistics"
    )
    await update.message.reply_html(welcome_text)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /profile command. Displays the user's parsed profile.
    """
    tg_id = update.effective_user.id
    logger.info(f"Received /profile command from user {tg_id}")

    async with AsyncSessionLocal() as db:
        user = await get_user_profile(db, tg_id)

    if not user or not user.extracted_profile:
        await update.message.reply_html(
            "📋 <b>Your Profile:</b>\n\n"
            "No profile found. Please upload your resume as a <b>PDF document</b> first!"
        )
        return

    profile = user.extracted_profile
    name = escape(profile.get("name", "N/A"))
    email = escape(profile.get("email", "N/A"))
    phone = escape(profile.get("phone", "N/A"))
    skills = [escape(s) for s in profile.get("skills", [])]
    education = profile.get("education", [])
    experience = profile.get("experience", [])
    projects = profile.get("projects", [])

    # Format the profile into HTML
    html_lines = [
        f"👤 <b>Profile: {name}</b>",
        f"📧 <b>Email:</b> {email}",
        f"📞 <b>Phone:</b> {phone}",
        "",
        "🛠️ <b>Key Skills:</b>",
        f"{', '.join(skills) if skills else 'None listed'}",
        ""
    ]

    if education:
        html_lines.append("🎓 <b>Education:</b>")
        for edu in education[:3]:  # limit to top 3 to avoid character overflow
            inst = escape(edu.get("institution", "N/A"))
            deg = escape(edu.get("degree", "N/A"))
            major = escape(edu.get("major", ""))
            yr = edu.get("end_year") or "N/A"
            major_str = f" in {major}" if major else ""
            html_lines.append(f"• <b>{inst}</b> - {deg}{major_str} (Class of {yr})")
        html_lines.append("")

    if experience:
        html_lines.append("💼 <b>Experience:</b>")
        for exp in experience[:3]:  # limit to top 3
            comp = escape(exp.get("company", "N/A"))
            role = escape(exp.get("role", "N/A"))
            desc = escape(exp.get("description", ""))
            start = escape(exp.get("start_date", "N/A"))
            end = escape(exp.get("end_date", "Present"))
            
            # Keep description concise
            if len(desc) > 100:
                desc = desc[:97] + "..."
            
            html_lines.append(f"• <b>{role}</b> at <b>{comp}</b> ({start} - {end})")
            if desc:
                html_lines.append(f"  <i>{desc}</i>")
        html_lines.append("")

    if projects:
        html_lines.append("🚀 <b>Projects:</b>")
        for proj in projects[:3]:  # limit to top 3
            title = escape(proj.get("title", "N/A"))
            desc = escape(proj.get("description", ""))
            techs = [escape(t) for t in proj.get("technologies", [])]
            
            if len(desc) > 100:
                desc = desc[:97] + "..."
                
            techs_str = f" [Tech: {', '.join(techs)}]" if techs else ""
            html_lines.append(f"• <b>{title}</b>{techs_str}")
            if desc:
                html_lines.append(f"  <i>{desc}</i>")
        html_lines.append("")

    # Construct and send response, keeping total length safe
    full_text = "\n".join(html_lines)
    if len(full_text) > 4000:
        full_text = full_text[:3997] + "..."
        
    await update.message.reply_html(full_text)

async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /jobs command. Initiates the conversational role preference prompt.
    Checks and prompts for experience level if missing.
    """
    tg_id = update.effective_user.id
    logger.info(f"Received /jobs command from user {tg_id}")

    async with AsyncSessionLocal() as db:
        user = await get_user_profile(db, tg_id)

        if not user or not user.extracted_profile or user.resume_embedding is None:
            await update.message.reply_html(
                "❌ <b>No resume found.</b>\n\n"
                "Please upload your resume as a <b>PDF document</b> first so I can find matching jobs for you!"
            )
            return

        profile = user.extracted_profile or {}
        if not profile.get("experience_level"):
            keyboard = [
                [
                    InlineKeyboardButton("Fresher", callback_data="set_exp_Fresher"),
                    InlineKeyboardButton("0-1 year", callback_data="set_exp_0-1 year"),
                ],
                [
                    InlineKeyboardButton("1-3 years", callback_data="set_exp_1-3 years"),
                    InlineKeyboardButton("3-5 years", callback_data="set_exp_3-5 years"),
                ],
                [
                    InlineKeyboardButton("5+ years", callback_data="set_exp_5+ years")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_html(
                "📋 <b>Experience Level Required</b>\n\n"
                "To personalize your job matches, please select your experience level first:",
                reply_markup=reply_markup
            )
            return

    # Set user state in Redis to expect preference query
    from app.core.database import redis_client
    try:
        await redis_client.set(f"user_state:{tg_id}", "awaiting_role_pref", ex=600)
    except Exception as e:
        logger.error(f"Failed to set Redis user state: {e}")

    await update.message.reply_html(
        "🔍 <b>Conversational Job Search</b>\n\n"
        "What kind of role and location are you targeting? (e.g., <i>AI Engineer in Bangalore</i>, <i>Backend Developer in India</i>, or <i>SDE Remote</i>)\n\n"
        "<i>Note: If you don't specify a location, we will automatically default to India + Remote.</i>"
    )


import io
import uuid
from sqlalchemy import select
from app.core.database import redis_client
from app.models.schemas import Job
from app.agents.tailor_graph import build_tailor_graph


async def run_tailoring_flow(
    user_id: int,
    job_uuid: uuid.UUID,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Executes the resume tailoring pipeline: checks Redis cache, invokes LangGraph,
    saves the output to cache, and sends the final PDF resume.
    """
    # 1. Start progress updates
    status_msg = await context.bot.send_message(
        chat_id=user_id,
        text="🚀 <b>Initializing Multi-Agent Resume Tailoring Loop...</b>",
        parse_mode="HTML"
    )

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to edit status message: {e}")

    # 2. Fetch job details
    async with AsyncSessionLocal() as db:
        stmt = select(Job).where(Job.id == job_uuid)
        res = await db.execute(stmt)
        job = res.scalars().first()
        if not job:
            await update_status("❌ <b>Job not found in system.</b>")
            return

        # Fetch user profile
        user = await get_user_profile(db, user_id)
        if not user or not user.extracted_profile:
            await update_status("❌ <b>User profile not found.</b> Please upload your PDF resume first.")
            return

        user_profile = user.extracted_profile

    # 3. Check Redis cache for tailored resume PDF
    cache_key = f"tailored_resume:{user_id}:{job.id}"
    try:
        cached_pdf = await redis_client.get(cache_key)
        if cached_pdf:
            logger.info(f"Cache HIT for tailored resume of user {user_id} and job {job.id}")
            pdf_stream = io.BytesIO(cached_pdf)
            pdf_stream.name = f"Resume_{job.company.replace(' ', '_')}.pdf"
            
            await update_status(
                f"✨ <b>Tailored Resume Found (Cached):</b>\n"
                f"Target: <b>{escape(job.title)}</b> at <b>{escape(job.company)}</b>\n\n"
                f"Sending your customized PDF resume now..."
            )
            await context.bot.send_document(
                chat_id=user_id,
                document=pdf_stream,
                filename=pdf_stream.name,
                caption=f"Tailored Resume: {job.title} - {job.company}"
            )
            return
    except Exception as e:
        logger.warning(f"Failed to check Redis cache for tailored resume: {e}")

    # 4. Cache Miss: Start LangGraph process
    job_dict = {
        "id": str(job.id),
        "title": job.title,
        "company": job.company,
        "description": job.description,
        "requirements": job.requirements or ""
    }

    initial_state = {
        "user_profile": user_profile,
        "target_job": job_dict,
        "current_draft": None,
        "critique_feedback": None,
        "score_history": [],
        "final_resume": None,
        "iteration_count": 0
    }

    import time
    start_time = time.time()
    try:
        graph = build_tailor_graph()
        # Pass update_status and user_id as configurable status callbacks/contexts
        config = {"configurable": {"status_callback": update_status, "user_id": user_id}}
        
        final_state = await graph.ainvoke(initial_state, config=config)
        
        pdf_bytes = final_state.get("final_resume")
        scores = final_state.get("score_history", [])
        final_score = scores[-1] if scores else 85
        
        if not pdf_bytes:
            await update_status("❌ <b>Compilation Failed.</b> Typst was unable to generate the PDF resume.")
            return

        # Cache final PDF in Redis for 7 days (604800 seconds)
        try:
            await redis_client.set(cache_key, pdf_bytes, ex=604800)
            logger.info(f"Cached tailored resume for user {user_id} and job {job.id} (7 days TTL)")
        except Exception as e:
            logger.warning(f"Failed to cache tailored resume: {e}")

        # Send success message & PDF document
        await update_status(
            f"✅ <b>Resume Tailored successfully!</b>\n"
            f"📈 <b>Final ATS Score:</b> {final_score}%\n"
            f"🔄 <b>Iterations Run:</b> {final_state.get('iteration_count', 1)}\n\n"
            f"Sending document..."
        )

        pdf_stream = io.BytesIO(pdf_bytes)
        pdf_stream.name = f"Resume_{job.company.replace(' ', '_')}.pdf"
        
        await context.bot.send_document(
            chat_id=user_id,
            document=pdf_stream,
            filename=pdf_stream.name,
            caption=f"🎯 Tailored Resume: {job.title} - {job.company} (ATS score: {final_score}%)"
        )

        from app.services.analytics import track_event
        duration_ms = (time.time() - start_time) * 1000
        await track_event(user_id, "resume_tailor", duration_ms)

    except Exception as e:
        logger.error(f"Tailoring workflow failed for user {user_id} and job {job.id}: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Tailoring failed:</b> <code>{escape(str(e))}</code>",
            parse_mode="HTML"
        )


async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /tailor command. Initiates resume tailoring process.
    Supports: /tailor <job_id>
    If no job_id is provided, shows a list of recent jobs that can be tailored.
    """
    tg_id = update.effective_user.id
    logger.info(f"Received /tailor command from user {tg_id}")

    args = context.args
    if args:
        # User specified a Job UUID
        job_id_str = args[0]
        try:
            job_uuid = uuid.UUID(job_id_str)
        except ValueError:
            await update.message.reply_html("❌ <b>Invalid Job ID format.</b> Please provide a valid UUID.")
            return

        await run_tailoring_flow(tg_id, job_uuid, context)
        return

    # No arguments: list latest jobs in system to tailor
    async with AsyncSessionLocal() as db:
        stmt = select(Job).order_by(Job.created_at.desc()).limit(3)
        res = await db.execute(stmt)
        jobs = res.scalars().all()

        if not jobs:
            await update.message.reply_html(
                "❌ <b>No jobs found in the system database.</b>\n\n"
                "Please run /jobs first to ingest and search for available openings."
            )
            return

        keyboard = []
        for job in jobs:
            keyboard.append([
                InlineKeyboardButton(
                    f"✨ Tailor for {job.title[:20]} ({job.company[:15]})",
                    callback_data=f"job_tailor_{job.id}"
                )
            ])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_html(
            "✨ <b>Resume Tailoring System</b>\n\n"
            "Please select one of the latest job openings below to tailor your resume for, "
            "or use /jobs to search for other roles and click the tailoring button directly on their cards:",
            reply_markup=reply_markup
        )


async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /learn command. Evaluates skill gaps and outputs roadmap.
    """
    tg_id = update.effective_user.id
    logger.info(f"Received /learn command from user {tg_id}")

    # 1. Fetch user profile
    async with AsyncSessionLocal() as db:
        user = await get_user_profile(db, tg_id)
        if not user or not user.extracted_profile:
            await update.message.reply_html(
                "❌ <b>No resume profile found.</b>\n\n"
                "Please upload your resume as a <b>PDF document</b> first so I can analyze your skills!"
            )
            return

        user_profile = user.extracted_profile

    # 2. Check Redis cache for cached skill gap report
    cache_key = f"skill_gap:{tg_id}"
    try:
        import json
        cached_data = await redis_client.get(cache_key)
        if cached_data:
            logger.info(f"Cache HIT for skill gap report of user {tg_id}")
            result = json.loads(cached_data)
            
            # Format and send long message
            html_content = format_skill_gap_html(result)
            await send_long_message(context.bot, tg_id, html_content)
            return
    except Exception as e:
        logger.warning(f"Failed to check Redis cache for skill gap: {e}")

    # 3. Cache Miss: Show loading message and call service
    import time
    start_time = time.time()
    status_msg = await update.message.reply_html(
        "🛠️ <b>Analyzing your skills against the current job market...</b>\n"
        "<i>This will only take a moment.</i>"
    )

    try:
        from app.services.skill_gap import analyze_skill_gaps
        
        async with AsyncSessionLocal() as db:
            result = await analyze_skill_gaps(user_profile, db, tg_id)
            
        # Cache results in Redis for 48 hours (172800 seconds)
        try:
            await redis_client.set(cache_key, json.dumps(result), ex=172800)
            logger.info(f"Cached skill gap report for user {tg_id} (48 hours TTL)")
        except Exception as e:
            logger.warning(f"Failed to cache skill gap report: {e}")

        # Delete status message and send formatted report
        try:
            await context.bot.delete_message(chat_id=tg_id, message_id=status_msg.message_id)
        except Exception as e:
            logger.warning(f"Failed to delete status message: {e}")

        html_content = format_skill_gap_html(result)
        await send_long_message(context.bot, tg_id, html_content)

        from app.services.analytics import track_event
        duration_ms = (time.time() - start_time) * 1000
        await track_event(tg_id, "skill_gap", duration_ms)

    except Exception as e:
        logger.error(f"Skill gap analysis failed for user {tg_id}: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Skill Gap analysis failed:</b> <code>{escape(str(e))}</code>",
            parse_mode="HTML"
        )


def format_skill_gap_html(result: dict) -> str:
    """Formats the skill gap JSON result into engaging HTML for Telegram."""
    gaps = result.get("gaps", [])
    weekly_plan = result.get("weekly_plan", [])
    projects = result.get("suggested_projects", [])
    
    html = "🛠️ <b>Personalized Skill Gap & Learning Roadmap</b>\n\n"
    
    html += "🎯 <b>Top Identified Gaps:</b>\n"
    for item in gaps:
        priority = item.get("priority", "Medium").lower()
        if "high" in priority:
            emoji = "🔴"
        elif "medium" in priority:
            emoji = "🟡"
        else:
            emoji = "🟢"
        
        html += f"{emoji} <b>{escape(item.get('skill', ''))}</b> (Priority: {item.get('priority', 'Medium')})\n"
        html += f"   <i>Why: {escape(item.get('importance', ''))}</i>\n\n"
        
    html += "📅 <b>Weekly Learning Plan:</b>\n"
    for week in weekly_plan:
        html += f"<b>{escape(week.get('week', ''))}</b>: {escape(week.get('focus', ''))}\n"
        html += "• <i>Action Items:</i>\n"
        for act in week.get("action_items", []):
            html += f"  - {escape(act)}\n"
        html += "• <i>Resources:</i>\n"
        for res in week.get("resources", []):
            html += f"  - {escape(res)}\n"
        html += "\n"
        
    html += "🚀 <b>Suggested Projects to Build:</b>\n"
    for proj in projects:
        html += f"💡 <b>{escape(proj.get('title', ''))}</b>\n"
        html += f"   {escape(proj.get('description', ''))}\n"
        html += "   <i>Key Deliverables:</i>\n"
        for deliv in proj.get("key_deliverables", []):
            html += f"   - {escape(deliv)}\n"
        html += "\n"
        
    html += "💪 <i>Stay consistent and build every day. You've got this!</i>"
    return html


async def send_long_message(bot, chat_id: int, text: str, parse_mode: str = "HTML") -> None:
    """Helper to split and send messages exceeding Telegram's 4096 char limit."""
    if len(text) <= 4096:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return
        
    parts = []
    current_part = ""
    for line in text.split("\n"):
        if len(current_part) + len(line) + 1 > 4096:
            parts.append(current_part)
            current_part = line
        else:
            if current_part:
                current_part += "\n" + line
            else:
                current_part = line
    if current_part:
        parts.append(current_part)
        
    for part in parts:
        await bot.send_message(chat_id=chat_id, text=part, parse_mode=parse_mode)


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /news command. Shows personalized tech briefings.
    """
    tg_id = update.effective_user.id
    logger.info(f"Received /news command from user {tg_id}")

    # 1. Fetch user profile
    async with AsyncSessionLocal() as db:
        user = await get_user_profile(db, tg_id)
        if not user or not user.extracted_profile:
            await update.message.reply_html(
                "❌ <b>No resume profile found.</b>\n\n"
                "Please upload your resume as a <b>PDF document</b> first so I can tailor the news briefings to your skills!"
            )
            return

        user_profile = user.extracted_profile

    # 2. Check Redis cache
    cache_key = f"news_briefing:{tg_id}"
    try:
        import json
        cached_data = await redis_client.get(cache_key)
        if cached_data:
            logger.info(f"Cache HIT for news briefing of user {tg_id}")
            result = json.loads(cached_data)
            html_content = format_news_html(result)
            await send_long_message(context.bot, tg_id, html_content)
            return
    except Exception as e:
        logger.warning(f"Failed to check Redis cache for news briefing: {e}")

    # 3. Cache Miss: Show loading message
    import time
    start_time = time.time()
    status_msg = await update.message.reply_html(
        "📰 <b>Gathering latest tech updates and tailoring to your profile...</b>\n"
        "<i>Analyzing Hacker News, subreddits, and top blogs.</i>"
    )

    try:
        from app.services.news_summarizer import get_personalized_briefing
        
        async with AsyncSessionLocal() as db:
            result = await get_personalized_briefing(user_profile, db, tg_id)
            
        # Cache results in Redis for 12 hours (43200 seconds)
        try:
            await redis_client.set(cache_key, json.dumps(result), ex=43200)
            logger.info(f"Cached news briefing for user {tg_id} (12 hours TTL)")
        except Exception as e:
            logger.warning(f"Failed to cache news briefing: {e}")

        # Delete loading message and send tech briefing
        try:
            await context.bot.delete_message(chat_id=tg_id, message_id=status_msg.message_id)
        except Exception as e:
            logger.warning(f"Failed to delete status message: {e}")

        html_content = format_news_html(result)
        await send_long_message(context.bot, tg_id, html_content)

        from app.services.analytics import track_event
        duration_ms = (time.time() - start_time) * 1000
        await track_event(tg_id, "news_briefing", duration_ms)

    except Exception as e:
        logger.error(f"News briefing generation failed for user {tg_id}: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>News briefing failed:</b> <code>{escape(str(e))}</code>",
            parse_mode="HTML"
        )


def format_news_html(result: dict) -> str:
    """Formats the news briefing JSON result into engaging HTML for Telegram."""
    stories = result.get("stories", [])
    if not stories:
        return (
            "📰 <b>Tailored Tech Briefing</b>\n\n"
            "No news articles are currently available. Please try again later or verify that background news ingestion is running."
        )
        
    html = "📰 <b>Tailored Tech Briefing</b>\n\n"
    html += "Here is your personalized tech briefing based on your profile and interests:\n\n"
    
    for idx, story in enumerate(stories):
        title = story.get("title", "News Update")
        url = story.get("url", "#")
        summary = story.get("summary", "")
        source = story.get("source", "Web")
        relevance = story.get("relevance", "")
        
        html += f"<b>{idx+1}. <a href=\"{escape(url)}\">{escape(title)}</a></b> ({escape(source)})\n"
        html += f"📝 {escape(summary)}\n"
        html += f"🧠 <i>Why relevant: {escape(relevance)}</i>\n\n"
        
    html += "✨ <i>Stay ahead in tech! Run /news anytime for updates.</i>"
    return html


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /remind command. Schedules tasks.
    """
    await update.message.reply_text("⏰ *AI Reminder Setup:*\n\n[Placeholder] Enter details like: '/remind next Tuesday at 3 PM to follow up'", parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /stats command. Shows observability diagnostics.
    """
    tg_id = update.effective_user.id
    logger.info(f"Received /stats command from user {tg_id}")

    from app.services.analytics import get_system_stats, get_user_stats
    from sqlalchemy import text
    import time

    # 1. Health checks
    db_healthy = False
    db_latency_ms = 0.0
    try:
        start_db = time.time()
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        db_latency_ms = (time.time() - start_db) * 1000
        db_healthy = True
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")

    redis_healthy = False
    redis_latency_ms = 0.0
    try:
        start_redis = time.time()
        await redis_client.ping()
        redis_latency_ms = (time.time() - start_redis) * 1000
        redis_healthy = True
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")

    # 2. Retrieve Stats
    user_stats = await get_user_stats(tg_id)
    system_stats = await get_system_stats()

    # 3. Format beautiful HTML message
    html = "📊 <b>CareerCopilot Metrics & Diagnostics</b>\n\n"
    
    html += "👤 <b>Your Analytics:</b>\n"
    html += f"  • Jobs Searched: <code>{user_stats.get('jobs_searched', 0)}</code>\n"
    html += f"  • Resumes Tailored: <code>{user_stats.get('resumes_tailored', 0)}</code>\n"
    html += f"  • Skill Gaps Run: <code>{user_stats.get('skill_gaps_analyzed', 0)}</code>\n"
    html += f"  • News Briefings: <code>{user_stats.get('news_briefings_generated', 0)}</code>\n"
    html += f"  • Total LLM Tokens: <code>{user_stats.get('total_tokens', 0)}</code>\n"
    html += f"  • Estimated LLM Cost: <code>${user_stats.get('estimated_cost_usd', 0.0):.5f}</code>\n"
    html += f"  • Avg Latency: <code>{user_stats.get('avg_latency_ms', 0.0):.1f}ms</code>\n\n"

    html += "⚙️ <b>System-Wide Analytics:</b>\n"
    html += f"  • Jobs Searched: <code>{system_stats.get('jobs_searched', 0)}</code>\n"
    html += f"  • Resumes Tailored: <code>{system_stats.get('resumes_tailored', 0)}</code>\n"
    html += f"  • Skill Gaps Run: <code>{system_stats.get('skill_gaps_analyzed', 0)}</code>\n"
    html += f"  • News Briefings: <code>{system_stats.get('news_briefings_generated', 0)}</code>\n"
    html += f"  • Total LLM Tokens: <code>{system_stats.get('total_tokens', 0)}</code>\n"
    html += f"  • Estimated LLM Cost: <code>${system_stats.get('estimated_cost_usd', 0.0):.4f}</code>\n"
    html += f"  • Avg Latency: <code>{system_stats.get('avg_latency_ms', 0.0):.1f}ms</code>\n\n"

    html += "🏥 <b>Deployment Health:</b>\n"
    db_status = f"🟢 Healthy ({db_latency_ms:.1f}ms)" if db_healthy else "🔴 Unhealthy"
    redis_status = f"🟢 Healthy ({redis_latency_ms:.1f}ms)" if redis_healthy else "🔴 Unhealthy"
    html += f"  • PostgreSQL: {db_status}\n"
    html += f"  • Redis Cache: {redis_status}\n"

    await update.message.reply_html(html)
