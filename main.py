"""
RADAR — Graph-based Review Agent for Code Evaluation
CLI entrypoint.
"""
import uuid
import logging
from dotenv import load_dotenv

load_dotenv()  # Load .env before importing graph (Claude + GitHub keys needed)

from langgraph.types import Command          # noqa: E402
from agent.graph import build_graph          # noqa: E402
from rich.console import Console             # noqa: E402

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)

console = Console()


def main() -> None:
    graph = build_graph()

    pr_url = input("Enter GitHub PR URL: ").strip()
    if not pr_url:
        console.print("[red]No URL provided. Exiting.[/red]")
        return

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "pr_url": pr_url,
        "approved": False,
        "posted": False,
        "review_comments": [],
        "files_changed": [],
        "error": None,
    }

    console.print("\n[bold blue]🔍 RADAR starting...[/bold blue]\n")

    # ── Phase 1: Run until interrupt in human_gate ─────────────────────
    fatal_nodes = {"fetch_metadata", "fetch_diff"}
    for event in graph.stream(initial_state, config=config, stream_mode="updates"):
        node_name = list(event.keys())[0] if event else "unknown"
        if "__interrupt__" in node_name:
            continue
        node_output = event.get(node_name, {})
        if isinstance(node_output, dict) and node_output.get("error") and node_name in fatal_nodes:
            console.print(
                f"\n[bold red]❌ Error in {node_name}:[/bold red] {node_output['error']}"
            )
            return
        console.print(f"[dim]✔ Completed: {node_name}[/dim]")

    # ── Phase 2: Human approval prompt ────────────────────────────────
    user_input = input("\nPost this review to GitHub? [y/N]: ").strip().lower()
    approved = user_input == "y"

    console.print(
        f"\n[bold]{'📤 Posting review...' if approved else '🗑️  Discarding review...'}[/bold]"
    )

    # ── Phase 3: Resume the graph with the human's decision ───────────
    for event in graph.stream(
        Command(resume=approved),
        config=config,
        stream_mode="updates",
    ):
        pass

    console.print("\n[bold green]✅ RADAR done.[/bold green]")


if __name__ == "__main__":
    main()
