"""
RADAR — Web Server (Modern UI)
Run with: python server.py
"""
import asyncio
import json
import queue
import threading
import uuid
import re
import sys
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="RADAR Web UI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_runs: dict[str, dict] = {}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)

class QueueStream:
    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, text: str):
        if text and text.strip():
            self._q.put(("log", _strip_ansi(text).strip()))

    def flush(self): pass
    def isatty(self): return False


def _run_radar(run_id: str, pr_url: str):
    run = _runs[run_id]
    q: queue.Queue = run["queue"]
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = QueueStream(q)

    try:
        from agent.graph import build_graph
        from langgraph.types import Command

        graph = build_graph()
        config = {"configurable": {"thread_id": run_id}}

        initial_state = {
            "pr_url": pr_url, "approved": False, "posted": False,
            "review_comments": [], "files_changed": [], "error": None,
        }

        fatal_nodes = {"fetch_metadata", "fetch_diff"}
        interrupted = False

        for event in graph.stream(initial_state, config=config, stream_mode="updates"):
            node_name = list(event.keys())[0] if event else "unknown"
            if "__interrupt__" in node_name:
                interrupted = True
                break
            node_output = event.get(node_name, {})
            if isinstance(node_output, dict) and node_output.get("error") and node_name in fatal_nodes:
                q.put(("error", node_output["error"]))
                q.put(("done", ""))
                return
            q.put(("step", node_name))

        if not interrupted:
            q.put(("done", ""))
            return

        # Emit structured review data from graph state
        snapshot = graph.get_state(config)
        sv = snapshot.values
        q.put(("review_data", json.dumps({
            "pr_title":       sv.get("pr_title", ""),
            "pr_number":      sv.get("pr_number", ""),
            "pr_author":      sv.get("pr_author", ""),
            "pr_url":         sv.get("pr_url", pr_url),
            "base_branch":    sv.get("base_branch", ""),
            "head_branch":    sv.get("head_branch", ""),
            "additions":      sv.get("additions", 0),
            "deletions":      sv.get("deletions", 0),
            "overall_score":  sv.get("overall_score", 0),
            "severity_summary": sv.get("severity_summary", {}),
            "review_comments":  sv.get("review_comments", []),
            "review_summary":   sv.get("review_summary", ""),
            "files_changed":    sv.get("files_changed", []),
        })))

        run["graph"] = graph
        run["config"] = config

        approved_event: threading.Event = run["approved_event"]
        approved_event.wait(timeout=300)
        approved = run.get("approved_value", False)

        post_result: dict = {"posted": False, "error": None}
        for event in graph.stream(Command(resume=approved), config=config, stream_mode="updates"):
            node_name = list(event.keys())[0] if event else "unknown"
            node_out = event.get(node_name, {})
            if node_name == "post_review" and isinstance(node_out, dict):
                post_result = node_out

        q.put(("posted", json.dumps({
            "approved": approved,
            "success": bool(post_result.get("posted", False)) if approved else False,
            "error": str(post_result.get("error") or ""),
        })))

    except Exception as e:
        q.put(("error", str(e)))
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        q.put(("done", ""))


@app.post("/api/run")
async def start_run(request: Request):
    body = await request.json()
    pr_url = body.get("pr_url", "").strip()
    if not pr_url:
        return {"error": "No PR URL provided"}
    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "queue": queue.Queue(),
        "approved_event": threading.Event(),
        "approved_value": False,
        "graph": None, "config": None,
    }
    threading.Thread(target=_run_radar, args=(run_id, pr_url), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str):
    if run_id not in _runs:
        return HTMLResponse("Run not found", status_code=404)
    q: queue.Queue = _runs[run_id]["queue"]

    async def event_generator():
        loop = asyncio.get_event_loop()
        while True:
            try:
                kind, data = await loop.run_in_executor(None, lambda: q.get(timeout=0.2))
                yield f"data: {json.dumps({'kind': kind, 'data': data})}\n\n"
                if kind == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'kind': 'ping', 'data': ''})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/approve/{run_id}")
async def approve_run(run_id: str, request: Request):
    if run_id not in _runs:
        return {"error": "Run not found"}
    body = await request.json()
    approved = bool(body.get("approved", False))
    _runs[run_id]["approved_value"] = approved
    _runs[run_id]["approved_event"].set()
    return {"ok": True}


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RADAR — AI PR Review Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #07070f;
  --bg2: #0d0d1a;
  --surface: rgba(255,255,255,0.04);
  --surface2: rgba(255,255,255,0.07);
  --border: rgba(255,255,255,0.08);
  --border2: rgba(255,255,255,0.13);
  --purple: #8b5cf6;
  --purple2: #7c3aed;
  --cyan: #06b6d4;
  --text: #f1f5f9;
  --text2: #94a3b8;
  --text3: #475569;
  --critical: #ef4444;
  --warning: #f59e0b;
  --suggestion: #3b82f6;
  --praise: #10b981;
  --r-sm: 8px;
  --r-md: 14px;
  --r-lg: 20px;
}

html { scroll-behavior: smooth; }

body {
  font-family: 'Inter', sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}

/* Animated mesh background */
body::before {
  content: '';
  position: fixed; inset: 0; z-index: 0;
  background:
    radial-gradient(ellipse 80% 50% at 20% -20%, rgba(139,92,246,0.12) 0%, transparent 60%),
    radial-gradient(ellipse 60% 40% at 80% 110%, rgba(6,182,212,0.08) 0%, transparent 60%);
  pointer-events: none;
}

.page { position: relative; z-index: 1; max-width: 900px; margin: 0 auto; padding: 48px 20px 120px; }

/* ── Header ────────────────────────────────────────────────────── */
.header { text-align: center; margin-bottom: 48px; }
.logo-mark {
  display: inline-flex; align-items: center; gap: 10px;
  background: var(--surface); border: 1px solid var(--border2);
  border-radius: 999px; padding: 6px 14px 6px 8px;
  font-size: 12px; font-weight: 600; letter-spacing: 0.05em;
  color: var(--text2); margin-bottom: 20px;
}
.logo-dot {
  width: 22px; height: 22px; border-radius: 50%;
  background: linear-gradient(135deg, #8b5cf6, #06b6d4);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px;
}
h1 {
  font-size: clamp(42px, 8vw, 72px);
  font-weight: 900;
  letter-spacing: -0.03em;
  background: linear-gradient(135deg, #fff 30%, rgba(139,92,246,0.9) 65%, #06b6d4 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  line-height: 1;
  margin-bottom: 12px;
}
.tagline { font-size: 16px; color: var(--text2); font-weight: 400; }

/* ── Input card ────────────────────────────────────────────────── */
.input-card {
  background: var(--surface);
  border: 1px solid var(--border2);
  border-radius: var(--r-lg);
  padding: 20px;
  margin-bottom: 28px;
  backdrop-filter: blur(12px);
  transition: border-color 0.2s;
}
.input-card:focus-within { border-color: rgba(139,92,246,0.6); }
.input-label { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text3); text-transform: uppercase; margin-bottom: 10px; }
.input-row { display: flex; gap: 10px; }

input[type=text] {
  flex: 1; background: rgba(255,255,255,0.05);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 12px 16px; color: var(--text);
  font-family: 'Inter', sans-serif; font-size: 14px;
  outline: none; transition: border-color 0.2s, background 0.2s;
}
input[type=text]:focus { border-color: rgba(139,92,246,0.5); background: rgba(255,255,255,0.07); }
input[type=text]::placeholder { color: var(--text3); }

.btn {
  padding: 12px 22px; border-radius: var(--r-md); border: none;
  font-family: 'Inter', sans-serif; font-weight: 600; font-size: 14px;
  cursor: pointer; transition: all 0.15s; white-space: nowrap;
  display: flex; align-items: center; gap: 7px;
}
.btn-run {
  background: linear-gradient(135deg, #8b5cf6, #7c3aed);
  color: #fff;
  box-shadow: 0 0 0 0 rgba(139,92,246,0);
}
.btn-run:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 24px rgba(139,92,246,0.4);
}
.btn-run:disabled { opacity: 0.45; cursor: not-allowed; transform: none !important; }

/* ── Pipeline steps ────────────────────────────────────────────── */
.pipeline {
  display: none; margin-bottom: 28px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 20px 24px; backdrop-filter: blur(12px);
}
.pipeline.visible { display: block; }
.pipeline-label { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text3); text-transform: uppercase; margin-bottom: 16px; }

.steps { display: flex; align-items: center; gap: 0; flex-wrap: wrap; row-gap: 12px; }
.step {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 500; color: var(--text3);
  transition: color 0.3s;
}
.step.active { color: var(--purple); }
.step.done { color: var(--praise); }
.step-icon {
  width: 28px; height: 28px; border-radius: 50%;
  border: 2px solid var(--border2);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; transition: all 0.3s; flex-shrink: 0;
  background: var(--surface);
}
.step.active .step-icon {
  border-color: var(--purple); background: rgba(139,92,246,0.15);
  animation: pulse-ring 1.5s ease-in-out infinite;
}
@keyframes pulse-ring {
  0%,100% { box-shadow: 0 0 0 0 rgba(139,92,246,0.4); }
  50% { box-shadow: 0 0 0 6px rgba(139,92,246,0); }
}
.step.done .step-icon { border-color: var(--praise); background: rgba(16,185,129,0.15); color: var(--praise); }
.step-connector {
  width: 28px; height: 2px; flex-shrink: 0;
  background: var(--border); border-radius: 2px; margin: 0 2px;
  transition: background 0.4s;
}
.step-connector.lit { background: var(--praise); }

/* Parallel branch styling */
.parallel-group {
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start;
}

/* ── Review panel ──────────────────────────────────────────────── */
#reviewPanel { display: none; animation: fadeUp 0.5s ease forwards; }
#reviewPanel.visible { display: block; }
@keyframes fadeUp { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }

/* PR meta bar */
.pr-meta {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 14px 18px;
  margin-bottom: 20px; font-size: 13px;
}
.pr-meta-title { font-weight: 700; font-size: 15px; flex: 1; min-width: 200px; }
.pr-chip {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 999px; padding: 4px 10px; font-size: 12px; color: var(--text2);
}
.pr-chip-icon { opacity: 0.7; }

/* Score + summary top row */
.score-row {
  display: grid; grid-template-columns: 180px 1fr; gap: 16px;
  margin-bottom: 20px;
}
@media (max-width: 580px) { .score-row { grid-template-columns: 1fr; } }

.score-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 24px 20px; text-align: center;
  position: relative; overflow: hidden;
}
.score-card::before {
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse at 50% 120%, rgba(139,92,246,0.15), transparent 70%);
}
.score-label { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text3); font-weight: 600; margin-bottom: 8px; }
.score-number {
  font-size: 56px; font-weight: 900; line-height: 1; letter-spacing: -0.04em;
  background: linear-gradient(135deg, #8b5cf6, #06b6d4);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.score-denom { font-size: 18px; color: var(--text3); font-weight: 500; margin-top: 2px; }

/* Circular ring SVG */
.score-ring { position: relative; width: 80px; height: 80px; margin: 0 auto 10px; }
.score-ring svg { transform: rotate(-90deg); }
.ring-bg { fill: none; stroke: var(--border2); stroke-width: 6; }
.ring-fill { fill: none; stroke-width: 6; stroke-linecap: round; stroke-dasharray: 220; stroke-dashoffset: 220; transition: stroke-dashoffset 1.2s cubic-bezier(0.4,0,0.2,1); }

.severity-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: 20px; display: flex;
  flex-direction: column; gap: 10px;
}
.sev-title { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text3); font-weight: 600; margin-bottom: 2px; }
.sev-list { display: flex; flex-direction: column; gap: 8px; }
.sev-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px;
}
.sev-name {
  font-size: 13px; font-weight: 500;
  display: flex; align-items: center; gap: 8px;
}
.sev-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.sev-bar-wrap { flex: 1; height: 4px; background: var(--surface2); border-radius: 4px; overflow: hidden; }
.sev-bar { height: 100%; border-radius: 4px; transition: width 1s ease; }
.sev-count {
  font-size: 13px; font-weight: 700; min-width: 22px; text-align: right;
}

/* Review summary */
.summary-box {
  background: linear-gradient(135deg, rgba(139,92,246,0.08), rgba(6,182,212,0.05));
  border: 1px solid rgba(139,92,246,0.25);
  border-radius: var(--r-lg); padding: 20px;
  font-size: 14px; line-height: 1.7; color: var(--text2);
  margin-bottom: 24px;
}
.summary-box strong { color: var(--text); }

/* Section title */
.section-title {
  font-size: 13px; font-weight: 600; color: var(--text2);
  letter-spacing: 0.04em; margin-bottom: 12px;
  display: flex; align-items: center; gap: 8px;
}
.section-title::after {
  content: ''; flex: 1; height: 1px; background: var(--border);
}

/* File group */
.file-group { margin-bottom: 20px; }
.file-header {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--r-md);
  font-size: 13px; font-weight: 600; cursor: pointer;
  transition: background 0.2s; user-select: none;
  margin-bottom: 1px;
}
.file-header:hover { background: var(--surface2); }
.file-icon { color: var(--text3); font-size: 14px; }
.file-name { font-family: 'Inter', sans-serif; flex: 1; }
.file-count {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 999px; padding: 2px 8px; font-size: 11px; color: var(--text2);
}
.file-chevron { color: var(--text3); font-size: 11px; transition: transform 0.2s; }
.file-header.collapsed .file-chevron { transform: rotate(-90deg); }

.comments-list { display: flex; flex-direction: column; gap: 8px; padding-top: 8px; }
.comment-card {
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid transparent;
  border-radius: var(--r-md); padding: 14px 16px;
  transition: border-color 0.2s, background 0.2s;
  animation: slideIn 0.3s ease forwards;
  opacity: 0;
}
@keyframes slideIn { from { opacity:0; transform:translateX(-8px); } to { opacity:1; transform:translateX(0); } }
.comment-card:hover { background: var(--surface2); }
.comment-card.critical { border-left-color: var(--critical); }
.comment-card.warning  { border-left-color: var(--warning); }
.comment-card.suggestion { border-left-color: var(--suggestion); }
.comment-card.praise   { border-left-color: var(--praise); }

.comment-top { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 8px; }
.sev-badge {
  display: inline-flex; align-items: center; gap: 4px;
  border-radius: 999px; padding: 3px 8px; font-size: 11px; font-weight: 700;
  letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap; flex-shrink: 0;
}
.sev-badge.critical   { background: rgba(239,68,68,0.15); color: #fca5a5; }
.sev-badge.warning    { background: rgba(245,158,11,0.15); color: #fde68a; }
.sev-badge.suggestion { background: rgba(59,130,246,0.15); color: #93c5fd; }
.sev-badge.praise     { background: rgba(16,185,129,0.15); color: #6ee7b7; }

.line-chip {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 6px; padding: 2px 7px; font-size: 11px; color: var(--text3);
  font-family: 'Inter', monospace; flex-shrink: 0;
}
.comment-body { font-size: 13px; line-height: 1.6; color: var(--text2); flex: 1;}
.comment-suggestion {
  margin-top: 10px; padding: 10px 12px;
  background: rgba(255,255,255,0.03); border-radius: var(--r-sm);
  border-left: 2px solid rgba(255,255,255,0.1);
  font-size: 12px; color: var(--text2); line-height: 1.6;
}
.suggestion-label { font-size: 11px; font-weight: 600; color: var(--text3); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 4px; }

/* ── Approval bar (fixed bottom) ────────────────────────────────── */
#approvalBar {
  display: none; position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
  backdrop-filter: blur(20px);
  background: rgba(7,7,15,0.85);
  border-top: 1px solid var(--border2);
  padding: 16px 24px;
  animation: slideUp 0.4s cubic-bezier(0.4,0,0.2,1);
}
@keyframes slideUp { from { transform: translateY(100%); opacity:0; } to { transform: translateY(0); opacity:1; } }
#approvalBar.visible { display: block; }
.approval-inner {
  max-width: 900px; margin: 0 auto;
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  flex-wrap: wrap;
}
.approval-text h3 { font-size: 15px; font-weight: 700; margin-bottom: 2px; }
.approval-text p { font-size: 13px; color: var(--text2); }
.approval-buttons { display: flex; gap: 10px; }
.btn-post {
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  box-shadow: 0 0 0 0 rgba(16,185,129,0);
}
.btn-post:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 24px rgba(16,185,129,0.4);
}
.btn-ghost {
  background: var(--surface2); color: var(--text2);
  border: 1px solid var(--border2);
}
.btn-ghost:hover { background: rgba(255,255,255,0.1); color: var(--text); }

/* Spinner */
.spin {
  display: inline-block; width: 14px; height: 14px;
  border: 2px solid rgba(255,255,255,0.2);
  border-top-color: #fff; border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Toast notification */
.toast {
  position: fixed; top: 20px; right: 20px; z-index: 200;
  padding: 12px 18px; border-radius: var(--r-md);
  font-size: 14px; font-weight: 500; backdrop-filter: blur(12px);
  border: 1px solid transparent; animation: toastIn 0.3s ease;
  max-width: 340px;
}
@keyframes toastIn { from {opacity:0;transform:translateY(-10px)} to {opacity:1;transform:translateY(0)} }
.toast.success { background: rgba(16,185,129,0.15); border-color: rgba(16,185,129,0.3); color: #6ee7b7; }
.toast.error   { background: rgba(239,68,68,0.15); border-color: rgba(239,68,68,0.3); color: #fca5a5; }
.toast.info    { background: rgba(139,92,246,0.15); border-color: rgba(139,92,246,0.3); color: #c4b5fd; }

/* Empty state */
.empty-state { text-align: center; padding: 60px 20px; color: var(--text3); }
.empty-icon { font-size: 48px; margin-bottom: 12px; opacity: 0.5; }
</style>
</head>
<body>
<div class="page">

  <!-- Header -->
  <div class="header">
    <div class="logo-mark">
      <span class="logo-dot">⬡</span>
      Powered by Claude Sonnet 4.6 + LangGraph
    </div>
    <h1>RADAR</h1>
    <p class="tagline">Graph-based Review Agent for Code Evaluation</p>
  </div>

  <!-- Input -->
  <div class="input-card">
    <div class="input-label">GitHub Pull Request URL</div>
    <div class="input-row">
      <input type="text" id="prUrl" placeholder="https://github.com/owner/repo/pull/42" />
      <button class="btn btn-run" id="runBtn" onclick="startRun()">
        <span id="runIcon">▶</span>
        <span id="runLabel">Run Review</span>
      </button>
    </div>
  </div>

  <!-- Pipeline steps -->
  <div class="pipeline" id="pipeline">
    <div class="pipeline-label">Analysis Pipeline</div>
    <div class="steps" id="stepsContainer">
      <div class="step" id="step-fetch_metadata" data-label="Fetch Metadata">
        <div class="step-icon" id="icon-fetch_metadata">1</div>
        <span>Metadata</span>
      </div>
      <div class="step-connector" id="conn-1"></div>
      <div class="step" id="step-fetch_diff" data-label="Fetch Diff">
        <div class="step-icon" id="icon-fetch_diff">2</div>
        <span>Diff</span>
      </div>
      <div class="step-connector" id="conn-2"></div>
      <div class="parallel-group">
        <div class="step" id="step-analyze_code">
          <div class="step-icon" id="icon-analyze_code">3a</div>
          <span>Code Analysis</span>
        </div>
        <div class="step" id="step-analyze_sql">
          <div class="step-icon" id="icon-analyze_sql">3b</div>
          <span>SQL/DAG Review</span>
        </div>
      </div>
      <div class="step-connector" id="conn-3"></div>
      <div class="step" id="step-generate_review">
        <div class="step-icon" id="icon-generate_review">4</div>
        <span>Generate</span>
      </div>
      <div class="step-connector" id="conn-4"></div>
      <div class="step" id="step-post_review">
        <div class="step-icon" id="icon-post_review">5</div>
        <span>Post</span>
      </div>
    </div>
  </div>

  <!-- Review panel (filled by review_data event) -->
  <div id="reviewPanel"></div>

</div>

<!-- Approval bar -->
<div id="approvalBar">
  <div class="approval-inner">
    <div class="approval-text">
      <h3>🔍 Review Ready</h3>
      <p>RADAR completed the analysis. Post this review to GitHub?</p>
    </div>
    <div class="approval-buttons">
      <button class="btn btn-post" id="postBtn" onclick="submitApproval(true)">✅ Post to GitHub</button>
      <button class="btn btn-ghost" onclick="submitApproval(false)">🗑 Discard</button>
    </div>
  </div>
</div>

<script>
let currentRunId = null;
let es = null;

const SEV_COLORS = { critical:'#ef4444', warning:'#f59e0b', suggestion:'#3b82f6', praise:'#10b981' };
const SEV_ICONS  = { critical:'⚠', warning:'◆', suggestion:'●', praise:'✦' };

const STEP_ORDER = ['fetch_metadata','fetch_diff','analyze_code','analyze_sql','generate_review','post_review'];
const CONN_MAP = {
  'fetch_metadata':'conn-1',
  'fetch_diff':'conn-2',
  'analyze_sql':'conn-3',
  'analyze_code':'conn-3',
  'generate_review':'conn-4',
};

function setStepActive(name) {
  const el = document.getElementById('step-' + name);
  if (el) { el.classList.add('active'); el.querySelector('.step-icon').innerHTML = '<div class="spin"></div>'; }
}
function setStepDone(name) {
  const el = document.getElementById('step-' + name);
  if (el) {
    el.classList.remove('active');
    el.classList.add('done');
    el.querySelector('.step-icon').innerHTML = '✓';
    const conn = CONN_MAP[name];
    if (conn) document.getElementById(conn)?.classList.add('lit');
  }
  // Activate next step (don't auto-activate post_review — it's manually triggered)
  const idx = STEP_ORDER.indexOf(name);
  if (idx !== -1 && idx + 1 < STEP_ORDER.length) {
    const next = STEP_ORDER[idx + 1];
    if (next === 'post_review') return;  // wait for user approval
    if (!document.getElementById('step-' + next)?.classList.contains('done')) {
      setStepActive(next);
    }
  }
}
function markStepSkipped(name) {
  const el = document.getElementById('step-' + name);
  if (!el) return;
  el.classList.remove('active');
  const icon = el.querySelector('.step-icon');
  icon.innerHTML = '—'; icon.style.color = 'var(--text3)';
  icon.style.animation = 'none';
}
function markStepError(name) {
  const el = document.getElementById('step-' + name);
  if (!el) return;
  el.classList.remove('active');
  const icon = el.querySelector('.step-icon');
  icon.innerHTML = '✕';
  icon.style.borderColor = 'var(--critical)';
  icon.style.color = 'var(--critical)';
  icon.style.background = 'rgba(239,68,68,0.15)';
  icon.style.animation = 'none';
}

function showToast(msg, type='info', duration=4000) {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity='0'; t.style.transition='opacity 0.3s'; setTimeout(()=>t.remove(),300); }, duration);
}

function renderReview(d) {
  const panel = document.getElementById('reviewPanel');

  // Count max severity for bar width
  const maxCount = Math.max(...Object.values(d.severity_summary || {}), 1);

  // Build severity rows
  const sevHTML = ['critical','warning','suggestion','praise'].map(s => {
    const count = (d.severity_summary||{})[s] || 0;
    const pct = Math.round((count/maxCount)*100);
    return `<div class="sev-row">
      <div class="sev-name">
        <div class="sev-dot" style="background:${SEV_COLORS[s]}"></div>
        <span style="color:${count>0?'#e2e8f0':'#475569'}">${s.charAt(0).toUpperCase()+s.slice(1)}</span>
      </div>
      <div class="sev-bar-wrap"><div class="sev-bar" style="width:${pct}%;background:${SEV_COLORS[s]}"></div></div>
      <div class="sev-count" style="color:${count>0?SEV_COLORS[s]:'#475569'}">${count}</div>
    </div>`;
  }).join('');

  // Group comments by file
  const byFile = {};
  (d.review_comments||[]).forEach(c => {
    const f = c.file||'General';
    if(!byFile[f]) byFile[f] = [];
    byFile[f].push(c);
  });

  const filesHTML = Object.entries(byFile).map(([file, comments], fi) => {
    const commentCards = comments.map((c, ci) => {
      const sev = c.severity||'suggestion';
      const suggestion = c.suggestion ? `<div class="comment-suggestion">
        <div class="suggestion-label">Suggested Fix</div>
        ${escHtml(c.suggestion)}
      </div>` : '';
      return `<div class="comment-card ${sev}" style="animation-delay:${(fi*0.05+ci*0.08)}s">
        <div class="comment-top">
          <span class="sev-badge ${sev}">${SEV_ICONS[sev]||'●'} ${sev}</span>
          ${c.line ? `<span class="line-chip">Line ${c.line}</span>` : ''}
          <div class="comment-body">${escHtml(c.body||'')}</div>
        </div>
        ${suggestion}
      </div>`;
    }).join('');

    const fname = file.split('/').pop();
    return `<div class="file-group">
      <div class="file-header" onclick="toggleFile(this)">
        <span class="file-icon">📄</span>
        <span class="file-name">${escHtml(file)}</span>
        <span class="file-count">${comments.length} comment${comments.length!==1?'s':''}</span>
        <span class="file-chevron">▾</span>
      </div>
      <div class="comments-list">${commentCards}</div>
    </div>`;
  }).join('');

  // Compute ring offset: circumference=220, score/10
  const offset = 220 - (d.overall_score / 10) * 220;
  const ringColor = d.overall_score >= 8 ? '#10b981' : d.overall_score >= 6 ? '#f59e0b' : '#ef4444';

  panel.innerHTML = `
    <div class="pr-meta">
      <span class="pr-meta-title">${escHtml(d.pr_title||'PR Review')}</span>
      <span class="pr-chip"><span class="pr-chip-icon">#</span>${d.pr_number}</span>
      <span class="pr-chip"><span class="pr-chip-icon">👤</span>${escHtml(d.pr_author||'')}</span>
      ${d.base_branch ? `<span class="pr-chip"><span class="pr-chip-icon">⬅</span>${escHtml(d.base_branch)}</span>` : ''}
      ${d.additions||d.deletions ? `<span class="pr-chip" style="color:#10b981">+${d.additions||0}</span><span class="pr-chip" style="color:#ef4444">-${d.deletions||0}</span>` : ''}
    </div>

    <div class="score-row">
      <div class="score-card">
        <div class="score-label">Quality Score</div>
        <div class="score-ring">
          <svg width="80" height="80" viewBox="0 0 80 80">
            <circle class="ring-bg" cx="40" cy="40" r="35"/>
            <circle class="ring-fill" cx="40" cy="40" r="35"
              style="stroke:${ringColor};stroke-dashoffset:${offset}"
              id="scoreRing"/>
          </svg>
        </div>
        <div class="score-number" id="scoreNum">0</div>
        <div class="score-denom">/ 10</div>
      </div>
      <div class="severity-card">
        <div class="sev-title">Findings Breakdown</div>
        <div class="sev-list">${sevHTML}</div>
      </div>
    </div>

    <div class="summary-box">${escHtml(d.review_summary||'No summary available.')}</div>

    ${Object.keys(byFile).length > 0 ? `
      <div class="section-title">Review Comments (${(d.review_comments||[]).length})</div>
      ${filesHTML}
    ` : `<div class="empty-state"><div class="empty-icon">✨</div><p>No comments — this diff looks clean!</p></div>`}
  `;

  panel.classList.add('visible');

  // Animate score count-up
  let n = 0, target = d.overall_score || 0;
  const interval = setInterval(() => {
    n = Math.min(n + 1, target);
    document.getElementById('scoreNum').textContent = n;
    if (n >= target) clearInterval(interval);
  }, 80);
}

function toggleFile(header) {
  header.classList.toggle('collapsed');
  const list = header.nextElementSibling;
  list.style.display = header.classList.contains('collapsed') ? 'none' : 'flex';
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function startRun() {
  const url = document.getElementById('prUrl').value.trim();
  if (!url) { showToast('Please enter a GitHub PR URL', 'error'); return; }
  if (!url.includes('github.com') || !url.includes('/pull/')) {
    showToast('That doesn\'t look like a GitHub PR URL', 'error'); return;
  }

  // Reset
  document.getElementById('reviewPanel').innerHTML = '';
  document.getElementById('reviewPanel').classList.remove('visible');
  document.getElementById('approvalBar').classList.remove('visible');
  document.getElementById('pipeline').classList.add('visible');
  document.getElementById('runBtn').disabled = true;
  document.getElementById('runIcon').innerHTML = '<div class="spin"></div>';
  document.getElementById('runLabel').textContent = 'Running…';

  STEP_ORDER.forEach(s => {
    const el = document.getElementById('step-'+s);
    if(el){ el.classList.remove('active','done'); el.querySelector('.step-icon').textContent = s==='analyze_code'?'3a':s==='analyze_sql'?'3b':String(STEP_ORDER.indexOf(s)+1); }
  });
  document.querySelectorAll('.step-connector').forEach(c=>c.classList.remove('lit'));

  setStepActive('fetch_metadata');

  const resp = await fetch('/api/run', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({pr_url: url})
  });
  const data = await resp.json();
  if (data.error) { showToast(data.error, 'error'); resetBtn(); return; }

  currentRunId = data.run_id;
  if (es) es.close();
  es = new EventSource('/api/stream/' + currentRunId);

  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'ping') return;

    if (msg.kind === 'step') {
      setStepDone(msg.data);
    }

    if (msg.kind === 'review_data') {
      try {
        const d = JSON.parse(msg.data);
        renderReview(d);
        setStepDone('generate_review');
        // Highlight post_review step as waiting (not spinning)
        const postEl = document.getElementById('step-post_review');
        if (postEl) { postEl.style.color = 'var(--purple)'; }
        document.getElementById('approvalBar').classList.add('visible');
        showToast('Review ready — approve to post to GitHub', 'info');
      } catch(err) { console.error(err); }
    }

    if (msg.kind === 'error') {
      showToast('Error: ' + msg.data, 'error', 8000);
      markStepError('post_review');
      resetBtn();
    }

    if (msg.kind === 'posted') {
      document.getElementById('approvalBar').classList.remove('visible');
      try {
        const p = JSON.parse(msg.data);
        if (!p.approved) {
          markStepSkipped('post_review');
          showToast('Review discarded — nothing posted to GitHub.', 'info', 4000);
        } else if (p.success) {
          setStepDone('post_review');
          showToast('✅ Review posted to GitHub!', 'success', 6000);
        } else {
          markStepError('post_review');
          const errMsg = p.error || 'Check that your GITHUB_TOKEN has repo write access.';
          showToast('❌ Post failed: ' + errMsg, 'error', 9000);
        }
      } catch(e) { markStepError('post_review'); }
    }

    if (msg.kind === 'done') {
      es.close();
      resetBtn();
    }
  };

  es.onerror = () => { es.close(); resetBtn(); };
}

function resetBtn() {
  document.getElementById('runBtn').disabled = false;
  document.getElementById('runIcon').textContent = '▶';
  document.getElementById('runLabel').textContent = 'Run Review';
}

async function submitApproval(approved) {
  if (!currentRunId) return;
  document.getElementById('postBtn').disabled = true;
  document.getElementById('approvalBar').querySelector('.btn-ghost').disabled = true;
  await fetch('/api/approve/' + currentRunId, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({approved})
  });
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('prUrl').addEventListener('keydown', e => { if(e.key==='Enter') startRun(); });
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


if __name__ == "__main__":
    print("🚀 RADAR Web UI → http://localhost:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
