import logging
import traceback
from agent.state import PRReviewState
from agent.tools.github_tools import GitHubTools

logger = logging.getLogger(__name__)


def post_pr_review_to_github(state: PRReviewState) -> dict:
    """
    Node 6: If the review was approved, format and post it as a GitHub PR review.
    Updates: posted (bool), optionally error.
    """
    try:
        if not state.get("approved"):
            print("Review discarded. Nothing posted to GitHub.")
            return {"posted": False}

        repo_full_name = state.get("repo_full_name", "")
        pr_number      = state.get("pr_number", 0)
        pr_url         = state.get("pr_url", "")
        overall_score  = state.get("overall_score", 0)
        review_summary = state.get("review_summary", "")
        severity_summary: dict = state.get("severity_summary", {})

        # ── Build Markdown body ────────────────────────────────────────
        severity_table_rows = "\n".join(
            f"| {sev.capitalize():<10} | {count:<5} |"
            for sev, count in severity_summary.items()
        )

        body = f"""## RADAR Review

**Score:** {overall_score}/10

| Severity   | Count |
|------------|-------|
{severity_table_rows}

---

{review_summary}

---
*Posted by RADAR — Graph-based Review Agent for Code Evaluation*"""

        tools = GitHubTools()
        success = tools.post_pr_review(repo_full_name, pr_number, body)

        if success:
            print(f"✅ Review posted successfully to {pr_url}")
            return {"posted": True}
        else:
            return {"posted": False, "error": "GitHub API returned a failure response."}

    except Exception as e:
        logger.error("post_pr_review_to_github failed: %s\n%s", e, traceback.format_exc())
        return {"posted": False, "error": str(e)}
