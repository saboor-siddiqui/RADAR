import json
import logging
import traceback
from typing import List
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from agent.state import PRReviewState, ReviewComment
from prompts.sql_review import SQL_REVIEW_SYSTEM

logger = logging.getLogger(__name__)

# File-matching criteria for SQL / DAG analysis
_SQL_EXTENSIONS = (".sql",)
_DAG_KEYWORD = "dag"
_SQL_KEYWORDS = ("SELECT", "INSERT", "CREATE TABLE")


def _is_sql_or_dag_file(filename: str, diff_text: str) -> bool:
    """Return True if the file should be included in SQL analysis."""
    lower = filename.lower()
    if lower.endswith(_SQL_EXTENSIONS):
        return True
    if lower.endswith(".py") and _DAG_KEYWORD in lower:
        return True
    if lower.endswith(".py") and any(kw in diff_text for kw in _SQL_KEYWORDS):
        return True
    return False


def _extract_file_section(diff_text: str, filename: str) -> str:
    """Pull out the diff section for a single file."""
    sections = diff_text.split("=== FILE:")
    for section in sections[1:]:  # skip empty first split
        if section.strip().startswith(filename):
            return "=== FILE:" + section
    return ""


def _map_to_review_comment(raw: dict) -> ReviewComment:
    return ReviewComment(
        file=raw.get("file", "unknown"),
        line=raw.get("line"),
        severity=raw.get("severity", "suggestion"),
        category=raw.get("category", "sql"),
        body=raw.get("body", ""),
        suggestion=raw.get("suggestion"),
    )


def check_sql_standards(state: PRReviewState) -> dict:
    """
    Node 3b: SQL/DAG-specific analysis via Claude.
    Updates: sql_analysis, review_comments (appended via operator.add).
    Short-circuits if an error is already in state.
    """
    try:
        if state.get("error"):
            return {"sql_analysis": {}, "review_comments": []}

        files_changed: List[str] = state.get("files_changed", [])
        diff_text: str = state.get("diff_text", "")

        # Filter to SQL/DAG-relevant files only
        matching_files = [
            f for f in files_changed if _is_sql_or_dag_file(f, diff_text)
        ]

        if not matching_files:
            return {
                "sql_analysis": {
                    "overall_score": 10,
                    "summary": "No SQL or DAG files changed.",
                },
                "review_comments": [],
            }

        # Build filtered diff containing only the matching files
        filtered_diff_parts = [
            _extract_file_section(diff_text, fname) for fname in matching_files
        ]
        filtered_diff = "\n\n".join(p for p in filtered_diff_parts if p)

        llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)

        files_list = ", ".join(matching_files)
        user_content = (
            f"PR Title: {state.get('pr_title', '')}\n"
            f"Author: {state.get('pr_author', '')}\n"
            f"Base branch: {state.get('base_branch', '')}\n\n"
            f"Description:\n{state.get('pr_description', '')}\n\n"
            f"SQL/DAG files changed: {files_list}\n\n"
            f"Diff:\n{filtered_diff}"
        )

        messages = [
            SystemMessage(content=SQL_REVIEW_SYSTEM),
            HumanMessage(content=user_content),
        ]

        response = llm.invoke(messages)
        raw_text = response.content.strip()

        # Strip markdown fences if Claude wrapped the JSON despite instructions
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            raw_text = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning(
                "check_sql_standards: LLM returned invalid JSON. Raw response:\n%s",
                raw_text,
            )
            return {
                "sql_analysis": {"overall_score": 5, "summary": ""},
                "review_comments": [],
            }

        comments: List[ReviewComment] = [
            _map_to_review_comment(c) for c in data.get("comments", [])
        ]

        sql_analysis = {
            "overall_score": int(data.get("overall_score", 5)),
            "summary": data.get("summary", ""),
        }

        return {
            "sql_analysis": sql_analysis,
            "review_comments": comments,
        }

    except Exception as e:
        logger.error("check_sql_standards failed: %s\n%s", e, traceback.format_exc())
        return {
            "sql_analysis": {"overall_score": 5, "summary": ""},
            "review_comments": [],
            "error": str(e),
        }
