import re
import logging
import traceback
from agent.state import PRReviewState
from agent.tools.github_tools import GitHubTools

logger = logging.getLogger(__name__)

# Regex to parse GitHub PR URLs
_PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


def fetch_pr_metadata(state: PRReviewState) -> dict:
    """
    Node 1: Parse the PR URL and fetch metadata from GitHub.
    Updates: repo_full_name, pr_number, pr_title, pr_description,
             pr_author, base_branch, head_branch.
    """
    try:
        pr_url = state["pr_url"]
        match = _PR_URL_RE.match(pr_url)
        if not match:
            return {"error": "Invalid GitHub PR URL format"}

        owner = match.group("owner")
        repo = match.group("repo")
        pr_number = int(match.group("number"))
        repo_full_name = f"{owner}/{repo}"

        tools = GitHubTools()
        metadata = tools.get_pr_metadata(repo_full_name, pr_number)

        if metadata is None:
            return {"error": f"Failed to fetch PR metadata for {repo_full_name}#{pr_number}"}

        return {
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "pr_title": metadata["title"],
            "pr_description": metadata["description"],
            "pr_author": metadata["author_login"],
            "base_branch": metadata["base_branch"],
            "head_branch": metadata["head_branch"],
        }

    except Exception as e:
        logger.error("fetch_pr_metadata failed: %s\n%s", e, traceback.format_exc())
        return {"error": str(e)}
