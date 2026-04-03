from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from agent.state import PRReviewState
from agent.nodes.fetch_metadata import fetch_pr_metadata
from agent.nodes.fetch_diff import fetch_pr_diff
from agent.nodes.analyze_code import analyze_code_quality
from agent.nodes.analyze_sql import check_sql_standards
from agent.nodes.generate_review import generate_review
from agent.nodes.human_gate import human_review_gate
from agent.nodes.post_review import post_pr_review_to_github


def build_graph():
    """
    Build and compile the RADAR LangGraph workflow.

    Topology:
      START → fetch_metadata → fetch_diff
                                  ↓            ↓
                            analyze_code   analyze_sql   (parallel fan-out)
                                  ↓            ↓
                              generate_review              (fan-in via operator.add)
                                  ↓
                              human_gate       (interrupt — HITL pause)
                                  ↓
                              post_review
                                  ↓
                               END
    """
    workflow = StateGraph(PRReviewState)

    # Register nodes
    workflow.add_node("fetch_metadata", fetch_pr_metadata)
    workflow.add_node("fetch_diff",     fetch_pr_diff)
    workflow.add_node("analyze_code",   analyze_code_quality)
    workflow.add_node("analyze_sql",    check_sql_standards)
    workflow.add_node("generate_review", generate_review)
    workflow.add_node("human_gate",     human_review_gate)
    workflow.add_node("post_review",    post_pr_review_to_github)

    # Edges — sequential chain
    workflow.add_edge(START,            "fetch_metadata")
    workflow.add_edge("fetch_metadata", "fetch_diff")

    # Parallel fan-out from fetch_diff
    workflow.add_edge("fetch_diff",     "analyze_code")
    workflow.add_edge("fetch_diff",     "analyze_sql")

    # Parallel fan-in — operator.add reducer merges review_comments
    workflow.add_edge("analyze_code",   "generate_review")
    workflow.add_edge("analyze_sql",    "generate_review")

    # Rest of the pipeline
    workflow.add_edge("generate_review", "human_gate")
    workflow.add_edge("human_gate",      "post_review")
    workflow.add_edge("post_review",     END)

    # MemorySaver is required for interrupt() to persist state across the resume
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
