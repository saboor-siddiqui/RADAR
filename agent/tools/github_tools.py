import os
import logging
from typing import List, Optional
from dotenv import load_dotenv
from github import Github, GithubException

load_dotenv()

logger = logging.getLogger(__name__)


class GitHubTools:
    """Wrapper around PyGithub for PR fetching and review posting."""

    def __init__(self):
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise EnvironmentError("GITHUB_TOKEN is not set in environment.")
        self._client = Github(token)

    # ------------------------------------------------------------------
    # Method 1: Fetch PR metadata
    # ------------------------------------------------------------------
    def get_pr_metadata(self, repo_full_name: str, pr_number: int) -> Optional[dict]:
        """Return a dict of PR metadata fields."""
        try:
            repo = self._client.get_repo(repo_full_name)
            pr = repo.get_pull(pr_number)
            return {
                "title": pr.title,
                "description": pr.body or "",
                "author_login": pr.user.login,
                "base_branch": pr.base.ref,
                "head_branch": pr.head.ref,
                "state": pr.state,
                "created_at": pr.created_at.isoformat(),
                "labels": [lbl.name for lbl in pr.labels],
            }
        except GithubException as e:
            if e.status == 403:
                print("GitHub token lacks permission. Required: pull-requests:write")
            elif e.status == 404:
                print("PR not found. Check the URL and token access.")
            else:
                print(f"GitHub API error: {e.data}")
            return None
        except Exception as e:
            logger.exception("Unexpected error fetching PR metadata")
            print(f"Unexpected error: {e}")
            return None

    # ------------------------------------------------------------------
    # Method 2: Fetch full diff text
    # ------------------------------------------------------------------
    def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Build a formatted diff string from all changed files."""
        try:
            repo = self._client.get_repo(repo_full_name)
            pr = repo.get_pull(pr_number)
            diff_parts: List[str] = []

            for file in pr.get_files():
                header = (
                    f"=== FILE: {file.filename} "
                    f"(+{file.additions} / -{file.deletions}) ===\n"
                )
                patch = file.patch or ""

                # Truncate oversized file diffs
                lines = patch.splitlines()
                if len(lines) > 150:
                    patch = "\n".join(lines[:150]) + "\n[TRUNCATED - file too large]"

                diff_parts.append(header + patch)

            full_diff = "\n\n".join(diff_parts)

            # Hard cap on total diff size
            if len(full_diff) > 12000:
                # Rebuild with per-file truncation already applied; still honor cap
                full_diff = full_diff[:12000] + "\n[DIFF TRUNCATED AT 12000 CHARS]"

            return full_diff
        except GithubException as e:
            if e.status == 403:
                print("GitHub token lacks permission. Required: pull-requests:write")
            elif e.status == 404:
                print("PR not found. Check the URL and token access.")
            else:
                print(f"GitHub API error: {e.data}")
            return ""
        except Exception as e:
            logger.exception("Unexpected error fetching PR diff")
            print(f"Unexpected error: {e}")
            return ""

    # ------------------------------------------------------------------
    # Method 3: List changed files + line counts
    # ------------------------------------------------------------------
    def get_changed_files(
        self, repo_full_name: str, pr_number: int
    ) -> tuple[List[str], int, int]:
        """Return (filenames, total_additions, total_deletions)."""
        try:
            repo = self._client.get_repo(repo_full_name)
            pr = repo.get_pull(pr_number)
            filenames: List[str] = []
            total_add = 0
            total_del = 0
            for file in pr.get_files():
                filenames.append(file.filename)
                total_add += file.additions
                total_del += file.deletions
            return filenames, total_add, total_del
        except GithubException as e:
            if e.status == 403:
                print("GitHub token lacks permission. Required: pull-requests:write")
            elif e.status == 404:
                print("PR not found. Check the URL and token access.")
            else:
                print(f"GitHub API error: {e.data}")
            return [], 0, 0
        except Exception as e:
            logger.exception("Unexpected error fetching changed files")
            print(f"Unexpected error: {e}")
            return [], 0, 0

    # ------------------------------------------------------------------
    # Method 4: Post the review comment
    # ------------------------------------------------------------------
    def post_pr_review(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str = "COMMENT",
    ) -> bool:
        """Post a PR review. Always uses event='COMMENT'."""
        try:
            repo = self._client.get_repo(repo_full_name)
            pr = repo.get_pull(pr_number)
            pr.create_review(body=body, event="COMMENT")
            return True
        except GithubException as e:
            if e.status == 403:
                print("GitHub token lacks permission. Required: pull-requests:write")
            elif e.status == 404:
                print("PR not found. Check the URL and token access.")
            else:
                print(f"GitHub API error: {e.data}")
            return False
        except Exception as e:
            logger.exception("Unexpected error posting PR review")
            print(f"Unexpected error: {e}")
            return False
