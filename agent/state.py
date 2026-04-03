from typing import TypedDict, Optional, List
from typing_extensions import Annotated
import operator


class ReviewComment(TypedDict):
    file: str
    line: Optional[int]
    severity: str        # "critical" | "warning" | "suggestion" | "praise"
    category: str        # "bug" | "security" | "style" | "performance" | "sql" | "logic"
    body: str
    suggestion: Optional[str]


class PRReviewState(TypedDict):
    # Input
    pr_url: str
    repo_full_name: str
    pr_number: int

    # Fetched data
    pr_title: str
    pr_description: str
    pr_author: str
    base_branch: str
    head_branch: str
    diff_text: str
    files_changed: List[str]
    additions: int
    deletions: int

    # Analysis results (written by parallel nodes)
    code_analysis: dict
    sql_analysis: dict

    # Generated review
    review_comments: Annotated[List[ReviewComment], operator.add]
    severity_summary: dict
    overall_score: int
    review_summary: str

    # Control flow
    approved: bool
    posted: bool
    error: Optional[str]
