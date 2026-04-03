import json
import logging
import traceback
from typing import List
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from agent.state import PRReviewState, ReviewComment
from prompts.code_review import CODE_REVIEW_SYSTEM

logger = logging.getLogger(__name__)


def _map_to_review_comment(raw: dict) -> ReviewComment:
    """Map a raw LLM-returned dict to a ReviewComment TypedDict."""
    return ReviewComment(
        file=raw.get("file", "unknown"),
        line=raw.get("line"),
        severity=raw.get("severity", "suggestion"),
        category=raw.get("category", "style"),
        body=raw.get("body", ""),
        suggestion=raw.get("suggestion"),
    )


def analyze_code_quality(state: PRReviewState) -> dict:
    """
    Node 3a: General code-quality analysis via Claude.
    Updates: code_analysis, review_comments (appended via operator.add).
    Short-circuits if an error is already in state.
    """
    try:
        if state.get("error"):
            return {"code_analysis": {}, "review_comments": []}

        llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)

        files_list = ", ".join(state.get("files_changed", []))
        user_content = (
            f"PR Title: {state.get('pr_title', '')}\n"
            f"Author: {state.get('pr_author', '')}\n"
            f"Base branch: {state.get('base_branch', '')}\n\n"
            f"Description:\n{state.get('pr_description', '')}\n\n"
            f"Files changed: {files_list}\n\n"
            f"Diff:\n{state.get('diff_text', '')}"
        )

        messages = [
            SystemMessage(content=CODE_REVIEW_SYSTEM),
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
                "analyze_code_quality: LLM returned invalid JSON. Raw response:\n%s",
                raw_text,
            )
            return {"code_analysis": {"overall_score": 5, "summary": ""}, "review_comments": []}

        comments: List[ReviewComment] = [
            _map_to_review_comment(c) for c in data.get("comments", [])
        ]

        code_analysis = {
            "overall_score": int(data.get("overall_score", 5)),
            "summary": data.get("summary", ""),
        }

        return {
            "code_analysis": code_analysis,
            "review_comments": comments,
        }

    except Exception as e:
        logger.error("analyze_code_quality failed: %s\n%s", e, traceback.format_exc())
        return {"code_analysis": {"overall_score": 5, "summary": ""}, "review_comments": [], "error": str(e)}
