import logging
import json
import instructor
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

from app.agents.state import TailorState
from app.services.compiler import inject_and_compile_typst

logger = logging.getLogger(__name__)


class ProjectDraft(BaseModel):
    title: str = Field(..., description="Title of the project")
    description: str = Field(..., description="1-2 sentences summarizing the project, achievements, and impact tailored to match the job requirements.")
    technologies: List[str] = Field(..., description="List of technologies used in the project")


class ExperienceDraft(BaseModel):
    role: str = Field(..., description="Role title")
    company: str = Field(..., description="Company name")
    duration: str = Field(..., description="Duration or dates of employment")
    bullet_points: List[str] = Field(..., description="2-3 highly-impactful bullet points showing achievements matching the job description.")


class ResumeDraft(BaseModel):
    name: str = Field(..., description="Candidate full name")
    email: str = Field(..., description="Candidate email")
    phone: str = Field(..., description="Candidate phone number")
    summary: str = Field(..., description="Technical professional summary tailored to the target job description")
    skills: List[str] = Field(..., description="Selected technical skills, programming languages, and tools matching the job")
    experience: List[ExperienceDraft] = Field(..., description="Tailored experience entries")
    projects: List[ProjectDraft] = Field(..., description="Tailored projects entries")
    education: str = Field(..., description="Candidate education details")


class CritiqueResult(BaseModel):
    score: int = Field(..., ge=0, le=100, description="ATS score from 0 to 100 based on alignment with the job description")
    feedback: str = Field(..., description="Actionable critic feedback highlighting key areas of improvement")


async def writer_agent(state: TailorState, config: Optional[Dict[str, Any]] = None) -> TailorState:
    """
    Writer Agent: Crafts or refines resume sections matching the target job using gemini-1.5-flash.
    """
    logger.info("Running Writer Agent node...")
    
    # Notify callback if present
    callback = config.get("configurable", {}).get("status_callback") if config else None
    iteration = state.get("iteration_count", 0) + 1
    if callback:
        await callback(f"✍️ <b>[Iteration {iteration}/3]</b> Writer Agent is tailoring resume sections...")

    instructor_client = instructor.from_provider(
        "google/gemini-1.5-flash",
        async_client=True,
    )

    user_profile = state["user_profile"]
    target_job = state["target_job"]
    feedback = state.get("critique_feedback")
    current_draft = state.get("current_draft")

    if not current_draft:
        # First iteration: Create initial draft from user profile
        system_prompt = (
            "You are an elite technical resume writer.\n"
            "Your goal is to tailor the candidate's resume to align with the target job description.\n"
            "Highlight relevant experiences, skills, and projects while maintaining honesty (do not invent experiences).\n"
            "Return the tailored resume structure according to the specified Pydantic schema."
        )
        user_prompt = (
            f"=== CANDIDATE RESUME PROFILE ===\n"
            f"{json.dumps(user_profile, indent=2)}\n\n"
            f"=== TARGET JOB DESCRIPTION ===\n"
            f"Title: {target_job.get('title')}\n"
            f"Company: {target_job.get('company')}\n"
            f"Description: {target_job.get('description')}\n"
            f"Requirements: {target_job.get('requirements')}\n"
        )
    else:
        # Subsequent iterations: Refine current draft based on critic feedback
        system_prompt = (
            "You are an elite technical resume writer.\n"
            "Your goal is to refine the current resume draft based on the ATS critic's feedback to better match the job description.\n"
            "Address the feedback directly and optimize wording and skill highlights. Keep it strictly honest.\n"
            "Return the tailored resume structure according to the specified Pydantic schema."
        )
        user_prompt = (
            f"=== ORIGINAL CANDIDATE PROFILE ===\n"
            f"{json.dumps(user_profile, indent=2)}\n\n"
            f"=== TARGET JOB DESCRIPTION ===\n"
            f"Title: {target_job.get('title')}\n"
            f"Company: {target_job.get('company')}\n"
            f"Description: {target_job.get('description')}\n\n"
            f"=== CURRENT RESUME DRAFT ===\n"
            f"{json.dumps(current_draft, indent=2)}\n\n"
            f"=== ATS CRITIC FEEDBACK ===\n"
            f"{feedback}\n"
        )

    try:
        response, raw = await instructor_client.create_with_completion(
            response_model=ResumeDraft,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            validation_context={"temperature": 0.2}
        )

        # Log Token Usage
        usage = getattr(raw, "usage_metadata", None)
        if usage:
            logger.info(
                f"[Writer] Prompt tokens: {usage.prompt_token_count}, "
                f"Completion tokens: {usage.candidates_token_count}, "
                f"Total: {usage.total_token_count}"
            )

        state["current_draft"] = response.model_dump()
    except Exception as e:
        logger.error(f"Writer Agent LLM call failed: {e}", exc_info=True)
        # Fallback to importing profile fields if failure occurs
        if not state.get("current_draft"):
            state["current_draft"] = {
                "name": user_profile.get("name", "Candidate"),
                "email": user_profile.get("email", ""),
                "phone": user_profile.get("phone", ""),
                "summary": "Tailoring failed. Using default profile summary.",
                "skills": user_profile.get("skills", []),
                "experience": [
                    {
                        "role": exp.get("role", ""),
                        "company": exp.get("company", ""),
                        "duration": f"{exp.get('start_date', '')} - {exp.get('end_date', '')}",
                        "bullet_points": [exp.get("description", "")]
                    }
                    for exp in user_profile.get("experience", [])
                ],
                "projects": [
                    {
                        "title": proj.get("title", ""),
                        "description": proj.get("description", ""),
                        "technologies": proj.get("technologies", [])
                    }
                    for proj in user_profile.get("projects", [])
                ],
                "education": ", ".join([
                    f"{edu.get('degree', '')} in {edu.get('major', '')} from {edu.get('institution', '')}"
                    for edu in user_profile.get("education", [])
                ])
            }

    return state


async def ats_critic_agent(state: TailorState, config: Optional[Dict[str, Any]] = None) -> TailorState:
    """
    ATS Critic Agent: Evaluates alignment quality (score 0-100) and feeds edits back using gemini-1.5-flash.
    """
    logger.info("Running ATS Critic Agent node...")
    
    # Notify callback if present
    callback = config.get("configurable", {}).get("status_callback") if config else None
    iteration = state.get("iteration_count", 0) + 1
    if callback:
        await callback(f"📊 <b>[Iteration {iteration}/3]</b> ATS Critic is evaluating draft alignment...")

    instructor_client = instructor.from_provider(
        "google/gemini-1.5-flash",
        async_client=True,
    )

    current_draft = state["current_draft"]
    target_job = state["target_job"]

    system_prompt = (
        "You are an elite Applicant Tracking System (ATS) evaluator and corporate recruiter.\n"
        "Your task is to critically analyze the candidate's tailored resume draft against the target job description.\n"
        "Assign a realistic alignment score (0-100) representing how well the candidate's summary, skills, projects, "
        "and experience align with the job requirements. Provide detailed, actionable feedback highlighting critical "
        "missing keywords, formatting issues, or weak experience descriptions."
    )
    user_prompt = (
        f"=== TARGET JOB DESCRIPTION ===\n"
        f"Title: {target_job.get('title')}\n"
        f"Company: {target_job.get('company')}\n"
        f"Description: {target_job.get('description')}\n"
        f"Requirements: {target_job.get('requirements')}\n\n"
        f"=== TAILORED RESUME DRAFT ===\n"
        f"{json.dumps(current_draft, indent=2)}\n"
    )

    try:
        response, raw = await instructor_client.create_with_completion(
            response_model=CritiqueResult,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            validation_context={"temperature": 0.1}
        )

        # Log Token Usage
        usage = getattr(raw, "usage_metadata", None)
        if usage:
            logger.info(
                f"[Critic] Prompt tokens: {usage.prompt_token_count}, "
                f"Completion tokens: {usage.candidates_token_count}, "
                f"Total: {usage.total_token_count}"
            )

        state["critique_feedback"] = response.feedback
        state["score_history"].append(response.score)
    except Exception as e:
        logger.error(f"Critic Agent LLM call failed: {e}", exc_info=True)
        # Fallback score and feedback
        state["critique_feedback"] = "Failed to run critique evaluation. Compiling draft directly."
        state["score_history"].append(85)  # Force compilation next step

    state["iteration_count"] = iteration
    return state


def route_critique(state: TailorState) -> str:
    """
    Decides whether to continue refinement or compile to PDF.
    """
    scores = state.get("score_history", [])
    latest_score = scores[-1] if scores else 0
    iterations = state.get("iteration_count", 0)

    if latest_score >= 85 or iterations >= 3:
        logger.info(f"Critique loop finished. Final score: {latest_score}, Iterations: {iterations}")
        return "compiler"
    
    logger.info(f"Score ({latest_score}) below threshold (85) at iteration {iterations}. Routing back to writer...")
    return "writer"


async def compiler_node(state: TailorState, config: Optional[Dict[str, Any]] = None) -> TailorState:
    """
    Compiler Node: Prepares structured draft data and compiles to PDF bytes via Typst.
    """
    logger.info("Running Compiler Node...")
    
    callback = config.get("configurable", {}).get("status_callback") if config else None
    if callback:
        await callback("🖨️ <b>Generating PDF:</b> Compiling tailored draft using Typst...")

    current_draft = state["current_draft"]
    try:
        pdf_bytes = await inject_and_compile_typst(current_draft)
        state["final_resume"] = pdf_bytes
    except Exception as e:
        logger.error(f"Compiler Node failed: {e}", exc_info=True)
        state["final_resume"] = None

    return state


def build_tailor_graph():
    """
    Builds and compiles the LangGraph StateGraph state machine.
    """
    builder = StateGraph(TailorState)

    builder.add_node("writer", writer_agent)
    builder.add_node("critic", ats_critic_agent)
    builder.add_node("compiler", compiler_node)

    builder.set_entry_point("writer")
    builder.add_edge("writer", "critic")

    builder.add_conditional_edges(
        "critic",
        route_critique,
        {
            "writer": "writer",
            "compiler": "compiler"
        }
    )

    builder.add_edge("compiler", END)
    return builder.compile()
