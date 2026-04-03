import logging
import traceback
from typing import List
from agent.state import PRReviewState, ReviewComment

logger = logging.getLogger(__name__)

# Severity ordering for sort
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "suggestion": 2, "praise": 3}


def generate_review(state: PRReviewState) -> dict:
    """
    Node 4 (pure Python — NO LLM calls): Deduplicate and sort all comments
    from both parallel analysis nodes, compute severity summary, overall score,
    and build the final review_summary string.
    Updates: review_comments, severity_summary, overall_score, review_summary.
    """
    try:
        if state.get("error"):
            return {}   # error passthrough

        raw_comments: List[ReviewComment] = state.get("review_comments", [])

        # --- Deduplicate on (file, line, category) ---
        seen: set = set()
        deduped: List[ReviewComment] = []
        for comment in raw_comments:
            key = (comment.get("file"), comment.get("line"), comment.get("category"))
            if key not in seen:
                seen.add(key)
                deduped.append(comment)

        # --- Sort by severity priority ---
        sorted_comments = sorted(
            deduped,
            key=lambda c: _SEVERITY_ORDER.get(c.get("severity", "suggestion"), 99),
        )

        # --- Severity summary ---
        severity_summary = {"critical": 0, "warning": 0, "suggestion": 0, "praise": 0}
        for comment in sorted_comments:
            sev = comment.get("severity", "suggestion")
            if sev in severity_summary:
                severity_summary[sev] += 1

        # --- Overall score ---
        code_analysis: dict = state.get("code_analysis", {})
        sql_analysis: dict = state.get("sql_analysis", {})

        code_score = code_analysis.get("overall_score", 5)
        sql_summary_text = sql_analysis.get("summary", "")

        if sql_summary_text and sql_summary_text != "No SQL or DAG files changed.":
            sql_score = sql_analysis.get("overall_score", code_score)
            overall_score = round((code_score + sql_score) / 2)
        else:
            overall_score = round(code_score)

        # --- Review summary ---
        code_summary = code_analysis.get("summary", "")
        combined_parts = [p for p in [code_summary, sql_summary_text] if p and p != "No SQL or DAG files changed."]
        combined = " ".join(combined_parts).strip() or "No analysis available."
        review_summary = f"Score: {overall_score}/10 — {combined}"

        return {
            "review_comments": sorted_comments,
            "severity_summary": severity_summary,
            "overall_score": overall_score,
            "review_summary": review_summary,
        }

    except Exception as e:
        logger.error("generate_review failed: %s\n%s", e, traceback.format_exc())
        return {"error": str(e)}
