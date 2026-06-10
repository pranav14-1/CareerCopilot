from typing import TypedDict, Optional, List, Dict, Any


class TailorState(TypedDict):
    """
    LangGraph state schema representing the shared state between
    the Writer and Critic agent nodes.
    """
    user_profile: Dict[str, Any]
    target_job: Dict[str, Any]
    current_draft: Optional[Dict[str, Any]]
    critique_feedback: Optional[str]
    score_history: List[int]
    final_resume: Optional[bytes]
    iteration_count: int
