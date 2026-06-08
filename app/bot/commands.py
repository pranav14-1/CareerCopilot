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
        "/remind - Add natural language reminders\n"
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

    # Set user state in Redis to expect preference query
    from app.core.database import redis_client
    try:
        await redis_client.set(f"user_state:{tg_id}", "awaiting_role_pref", ex=600)
    except Exception as e:
        logger.error(f"Failed to set Redis user state: {e}")

    await update.message.reply_html(
        "🔍 <b>Conversational Job Search</b>\n\n"
        "What kind of role are you looking for? (e.g., <i>AI Engineer, Backend Developer, SDE-2, Remote, Bangalore, etc.</i>)\n\n"
        "Please type your response below and send it!"
    )


async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /tailor command. Initiates resume tailoring process.
    """
    await update.message.reply_text("✨ *Resume Tailoring Loop:*\n\n[Placeholder] Specify a target job context to run the multi-agent critique.", parse_mode="Markdown")


async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /learn command. Evaluates skill gaps and outputs roadmap.
    """
    await update.message.reply_text("🛠️ *Skill Gap Roadmap:*\n\n[Placeholder] Calculating vector deltas between your skills and the market...", parse_mode="Markdown")


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /news command. Shows tech briefings.
    """
    await update.message.reply_text("📰 *Tailored Tech Briefing:*\n\n[Placeholder] Loading latest articles and Hacker News feeds...", parse_mode="Markdown")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /remind command. Schedules tasks.
    """
    await update.message.reply_text("⏰ *AI Reminder Setup:*\n\n[Placeholder] Enter details like: '/remind next Tuesday at 3 PM to follow up'", parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /stats command. Shows observability diagnostics.
    """
    await update.message.reply_text("📊 *Observability & Token Stats:*\n\n[Placeholder] Request latency and financial metrics...", parse_mode="Markdown")
