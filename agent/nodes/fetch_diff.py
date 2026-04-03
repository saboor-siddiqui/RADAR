import logging
import traceback
from agent.state import PRReviewState
from agent.tools.github_tools import GitHubTools

logger = logging.getLogger(__name__)


def fetch_pr_diff(state: PRReviewState) -> dict:
    """
    Node 2: Fetch the full PR diff and list of changed files.
    Updates: diff_text, files_changed, additions, deletions.
    Short-circuits if an error is already in state.
    """
    try:
        if state.get("error"):
            return {}   # error passthrough — return unchanged

        repo_full_name = state["repo_full_name"]
        pr_number = state["pr_number"]

        tools = GitHubTools()

        diff_text = tools.get_pr_diff(repo_full_name, pr_number)
        files_changed, additions, deletions = tools.get_changed_files(
            repo_full_name, pr_number
        )

        if not diff_text:
            return {"error": "PR has no changes. Nothing to review."}

        return {
            "diff_text": diff_text,
            "files_changed": files_changed,
            "additions": additions,
            "deletions": deletions,
        }

    except Exception as e:
        logger.error("fetch_pr_diff failed: %s\n%s", e, traceback.format_exc())
        return {"error": str(e)}
