from typing import TypedDict, Optional, List, Dict, Any


class TailorState(TypedDict):
    """
    LangGraph state schema representing the shared state between
    the Writer and Critic agent nodes.
    """
    # Core inputs
    user_id: int
    raw_resume: str
    job_description: str

    # Agent processing variables
    current_tailored_resume: str
    critic_feedback: Optional[str]
    alignment_score: int
    revision_count: int
    max_revisions: int

    # Final outputs
    compilation_variables: Optional[Dict[str, Any]]
    compiled_pdf_bytes: Optional[bytes]
    history: List[Dict[str, Any]]
