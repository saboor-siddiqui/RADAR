# RADAR — Graph-based Review Agent for Code Evaluation

RADAR is a production-quality, LangGraph-powered agent that automatically reviews GitHub Pull Requests. It fetches the PR diff, runs two parallel AI analysis passes (general code quality + SQL/Airflow DAG-specific), presents a structured review, pauses for your approval, and — if you say yes — posts the review directly back to GitHub.

Available as both a **CLI tool** (`main.py`) and a **modern web UI** (`server.py`).

---

## Screenshots

### Landing Page
![RADAR landing page — gradient title, input card, Run Review button](docs/screenshots/01_landing.png)

### Analysis Pipeline (Running)
![Animated pipeline — green checkmarks on completed steps, spinner on active step](docs/screenshots/02_pipeline_running.png)

### Review Dashboard
![Full review — PR metadata bar, quality score ring, findings breakdown, comment cards](docs/screenshots/03_review_ready.png)

### Approval Bar + Comment Cards
![Comment cards grouped by file with severity badges, frosted-glass approval bar at bottom](docs/screenshots/04_approval_bar.png)

### Discard Result
![Post step shows gray "—" dash after discarding — spinner resolved, toast confirms nothing posted](docs/screenshots/05_discard.png)

### Post to GitHub — Success
![Post step shows green ✓ — all 5 pipeline steps complete after posting review to GitHub](docs/screenshots/06_post_success.png)

---

## Architecture

```
START
  │
  ▼
fetch_metadata      ← parses PR URL, fetches PR metadata via PyGithub
  │
  ▼
fetch_diff          ← fetches full diff + file list
  │
  ├──────────────────────┐
  ▼                      ▼
analyze_code         analyze_sql      ← parallel fan-out (both write to review_comments via operator.add)
  │                      │
  └──────────┬───────────┘
             ▼
        generate_review   ← pure Python: deduplicate, sort, score, summarize
             │
             ▼
        human_gate        ← Rich display + interrupt() pause (HITL)
             │
             ▼
        post_review       ← posts Markdown review to GitHub (if approved)
             │
             ▼
            END
```

**Key design choices:**
- `operator.add` reducer on `review_comments` allows both parallel nodes to append safely without overwriting.
- `MemorySaver` checkpointer is required for `interrupt()` to persist state across the resume call.
- `human_gate` uses `langgraph.types.interrupt` — **not** `interrupt_before=` on compile.
- `generate_review` is a pure Python node — no LLM calls.

---

## Tech Stack

| Library | Purpose |
|---|---|
| `langgraph>=0.2.0` | Graph orchestration + HITL interrupt |
| `langchain-anthropic` | Claude claude-sonnet-4-6-20250514 LLM calls |
| `langchain-core` | Messages (SystemMessage, HumanMessage) |
| `PyGithub>=2.1.0` | GitHub REST API client |
| `python-dotenv` | `.env` file loading |
| `rich` | Terminal output (panels, tables, colors) |
| `pydantic>=2.0` | Data validation |

---

## File Structure

```
radar/
├── .env.example
├── requirements.txt
├── README.md
├── main.py               ← CLI entrypoint
├── server.py             ← Web UI server (FastAPI + SSE)
├── agent/
│   ├── __init__.py
│   ├── graph.py
│   ├── state.py
│   └── nodes/
│       ├── __init__.py
│       ├── fetch_metadata.py
│       ├── fetch_diff.py
│       ├── analyze_code.py
│       ├── analyze_sql.py
│       ├── generate_review.py
│       ├── human_gate.py
│       └── post_review.py
│   └── tools/
│       ├── __init__.py
│       └── github_tools.py
├── prompts/
│   ├── code_review.py
│   └── sql_review.py
└── docs/
    └── screenshots/      ← UI screenshots
```

---

## Setup

### 1. Clone and create a virtual environment

```bash
cd RADAR
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
GITHUB_TOKEN=ghp_your_personal_access_token_here
ANTHROPIC_API_KEY=sk-ant-your_anthropic_key_here
```

**GitHub token scopes required:**
- `repo` — to read PR metadata, diffs, and post reviews on private repos
- `public_repo` — sufficient for public repos only

---

## Usage

### Option A — Web UI (recommended)

```bash
python server.py
```

Open **http://localhost:8080** in your browser. Paste a PR URL, click **▶ Run Review**, and interact with the animated review dashboard.

Features:
- Visual pipeline with animated step indicators
- Quality score ring + severity breakdown chart
- Comment cards grouped by file with severity badges
- Frosted-glass approval bar with **Post to GitHub** / **Discard** buttons
- Real-time output streamed via Server-Sent Events (SSE)

### Option B — CLI

```bash
python main.py
```

You will be prompted:

```
Enter GitHub PR URL:
```

Paste a URL like `https://github.com/owner/repo/pull/42` and press Enter.

RADAR will:
1. Fetch PR metadata and diff
2. Run code quality analysis (Claude)
3. Run SQL/DAG analysis in parallel (Claude)
4. Display the full review in your terminal using Rich
5. Ask: `Post this review to GitHub? [y/N]:`
6. Post or discard based on your input

---

## Review Output Example

```
╔══════════════════════════════════════════════════════════╗
║         RADAR Review — Add user authentication          ║
║  PR #42 by alice | Score: 7/10                          ║
╚══════════════════════════════════════════════════════════╝

           Severity Summary
 Severity    Count  Symbol
 ─────────────────────────
 Critical      1    !!
 Warning       2    !
 Suggestion    3    i
 Praise        1    OK

──────────── src/auth/login.py ────────────────────────────
  [CRITICAL] Line 34: Password is stored as plaintext in the database.
    Suggestion: Use bcrypt.hashpw() before persisting. Example:
                hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

  [WARNING] Line 78: JWT secret is read from a hardcoded fallback value.
    Suggestion: Raise a RuntimeError if JWT_SECRET env var is not set.

  [PRAISE] General: Clean separation of authentication logic from routing.

╭─────────────────── Overall Assessment ──────────────────╮
│ Score: 7/10 — The authentication flow is structurally   │
│ sound but has a critical security issue (plaintext       │
│ passwords) that must be fixed before merging.            │
╰──────────────────────────────────────────────────────────╯

Post this review to GitHub? [y/N]: y

📤 Posting review...
✅ Review posted successfully to https://github.com/alice/myapp/pull/42

✅ RADAR done.
```

---

## State Fields

| Field | Type | Description |
|---|---|---|
| `pr_url` | `str` | Input PR URL |
| `repo_full_name` | `str` | `owner/repo` |
| `pr_number` | `int` | PR number |
| `diff_text` | `str` | Full formatted diff |
| `files_changed` | `List[str]` | Changed file paths |
| `review_comments` | `List[ReviewComment]` | All comments (merged via `operator.add`) |
| `severity_summary` | `dict` | Counts per severity level |
| `overall_score` | `int` | 1–10 score |
| `review_summary` | `str` | One-paragraph assessment |
| `approved` | `bool` | Human approval decision |
| `posted` | `bool` | Whether review was posted |
| `error` | `Optional[str]` | Error message for passthrough chain |

---

## Error Handling

- Every node is wrapped in `try/except`.
- Any node that encounters an error sets `{"error": str(e)}` and returns early.
- All downstream nodes check `state.get("error")` at the top and short-circuit — the graph always reaches `END`.
- GitHub 403/404 errors produce human-readable messages.
- Invalid LLM JSON is logged at WARNING level; the node returns empty defaults without crashing.

---

## Constraints

- ✅ No OpenAI SDK or models
- ✅ No CrewAI, AutoGen, or other agent frameworks
- ✅ No hardcoded API keys
- ✅ No `event="REQUEST_CHANGES"` — always uses `"COMMENT"`
- ✅ No LLM calls in `generate_review`
- ✅ No global variables — all state flows through `PRReviewState`
- ✅ `human_gate` interrupt is mandatory and never skipped
# RADAR
