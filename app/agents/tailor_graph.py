import logging
from langgraph.graph import StateGraph, END
from app.agents.state import TailorState

logger = logging.getLogger(__name__)


async def writer_node(state: TailorState) -> TailorState:
    """
    Writer Agent Node: Crafts or refines resume sections matching the target job.
    """
    logger.info("Running Writer Agent node...")
    # Placeholder: Call Gemini to rewrite resume sections based on feedback or initial JD
    state["current_tailored_resume"] = "Tailored resume components mapped to job description."
    state["revision_count"] = state.get("revision_count", 0) + 1
    return state


async def critic_node(state: TailorState) -> TailorState:
    """
    ATS Critic Agent Node: Evaluates alignment quality (score 0-100) and feeds edits.
    """
    logger.info("Running ATS Critic Agent node...")
    # Placeholder: Call Gemini to score the resume and generate critique
    state["alignment_score"] = 90  # Mock passing score (>= 85)
    state["critic_feedback"] = "Excellent alignment of skills."
    return state


def route_critique(state: TailorState) -> str:
    """
    Conditional routing function checking if the alignment score is >= 85
    or if we have exceeded maximum allowed revision steps.
    """
    score = state.get("alignment_score", 0)
    revisions = state.get("revision_count", 0)
    max_rev = state.get("max_revisions", 3)

    if score >= 85 or revisions >= max_rev:
        logger.info(f"Critique loop finished. Final score: {score}, Revisions: {revisions}")
        return "compile_pdf"
    logger.info(f"Score ({score}) below threshold (85). Routing back to writer...")
    return "writer"


async def compile_pdf_node(state: TailorState) -> TailorState:
    """
    Compiler Node: Converts final resume draft to a native Typst binary.
    """
    logger.info("Running Compiler Node...")
    # Placeholder: Call typst compiler service
    state["compiled_pdf_bytes"] = b"%PDF-1.4 mock pdf from tailor graph"
    return state


def build_tailor_graph():
    """
    Build and compile the LangGraph State Machine for the resume tailor flow.
    """
    builder = StateGraph(TailorState)

    # Register nodes
    builder.add_node("writer", writer_node)
    builder.add_node("critic", critic_node)
    builder.add_node("compile_pdf", compile_pdf_node)

    # Set workflow flow
    builder.set_entry_point("writer")
    builder.add_edge("writer", "critic")
    
    # Conditional routing from critic
    builder.add_conditional_edges(
        "critic",
        route_critique,
        {
            "writer": "writer",
            "compile_pdf": "compile_pdf"
        }
    )
    
    builder.add_edge("compile_pdf", END)

    # Compile the runnable graph
    graph = builder.compile()
    logger.info("LangGraph resume tailor state machine compiled successfully.")
    return graph
