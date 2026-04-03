import logging
import traceback
from collections import defaultdict
from typing import List

from langgraph.types import interrupt
from langgraph.errors import GraphInterrupt
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from agent.state import PRReviewState, ReviewComment

logger = logging.getLogger(__name__)
console = Console()

# Severity → (rich color, symbol)
_SEVERITY_STYLE = {
    "critical":   ("bold red",     "!! "),
    "warning":    ("bold yellow",  "!  "),
    "suggestion": ("bold blue",    "i  "),
    "praise":     ("bold green",   "OK "),
}


def _print_review(state: PRReviewState) -> None:
    """Render the full review to the terminal using Rich."""

    pr_title     = state.get("pr_title", "Unknown PR")
    pr_number    = state.get("pr_number", "?")
    pr_author    = state.get("pr_author", "unknown")
    overall_score = state.get("overall_score", 0)
    severity_summary: dict = state.get("severity_summary", {})
    comments: List[ReviewComment] = state.get("review_comments", [])
    review_summary = state.get("review_summary", "")

    # ── Header panel ──────────────────────────────────────────────────
    console.print(
        Panel(
            f"[bold]PR #{pr_number}[/bold] by [cyan]{pr_author}[/cyan]  |  "
            f"Score: [bold yellow]{overall_score}/10[/bold yellow]",
            title=f"[bold magenta]RADAR Review — {pr_title}[/bold magenta]",
            box=box.DOUBLE_EDGE,
            expand=True,
        )
    )

    # ── Severity summary table ─────────────────────────────────────────
    table = Table(title="Severity Summary", box=box.SIMPLE_HEAD, expand=False)
    table.add_column("Severity",  style="bold", no_wrap=True)
    table.add_column("Count",     justify="right")
    table.add_column("Symbol",    justify="center")

    for sev, (color, symbol) in _SEVERITY_STYLE.items():
        count = severity_summary.get(sev, 0)
        table.add_row(
            f"[{color}]{sev.capitalize()}[/{color}]",
            str(count),
            f"[{color}]{symbol}[/{color}]",
        )
    console.print(table)

    # ── Per-file comments ──────────────────────────────────────────────
    if comments:
        by_file: dict = defaultdict(list)
        for c in comments:
            by_file[c.get("file", "unknown")].append(c)

        for filename, file_comments in by_file.items():
            console.rule(f"[bold dim]{filename}[/bold dim]")
            for c in file_comments:
                sev = c.get("severity", "suggestion")
                color, symbol = _SEVERITY_STYLE.get(sev, ("white", "   "))
                line_ref = f"Line {c['line']}" if c.get("line") else "General"
                console.print(
                    f"  [{color}][{sev.upper()}][/{color}] {line_ref}: {c.get('body', '')}"
                )
                if c.get("suggestion"):
                    console.print(
                        f"    [dim]Suggestion:[/dim] {c['suggestion']}"
                    )
    else:
        console.print("[green]No comments — the diff looks clean![/green]")

    # ── Overall assessment panel ───────────────────────────────────────
    console.print(
        Panel(
            review_summary,
            title="[bold]Overall Assessment[/bold]",
            box=box.ROUNDED,
            expand=True,
        )
    )


def human_review_gate(state: PRReviewState) -> dict:
    """
    Node 5: Display the full review with Rich, then pause via interrupt().
    The caller (main.py) resumes the graph with the user's y/N decision.
    Updates: approved (bool).
    """
    try:
        _print_review(state)

        # Pause the graph — main.py will resume with Command(resume=approved)
        decision = interrupt({
            "action": "approve_review",
            "pr_url": state.get("pr_url", ""),
            "overall_score": state.get("overall_score", 0),
            "severity_summary": state.get("severity_summary", {}),
        })

        return {"approved": bool(decision)}

    except GraphInterrupt:
        # GraphInterrupt is the LangGraph pause signal — must be re-raised
        raise
    except Exception as e:
        logger.error("human_review_gate failed: %s\n%s", e, traceback.format_exc())
        return {"approved": False, "error": str(e)}
