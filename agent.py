"""
Job Scout Agent: finds job postings that match your criteria, evaluates each one against
your CV, and hands you a short list to decide on. It brings options; you decide.

The core idea: an agent is a LOOP.
    1. The model THINKS (reads its instructions and decides what to do).
    2. It asks to use a TOOL (e.g. scan_board) with some parameters.
    3. Our code runs the tool and returns the result (the model OBSERVES).
    4. Back to step 1 with the new information, until it decides it is done.

The Claude Agent SDK runs that loop for us. Our job is to:
    - give it instructions (system prompt, built from your criteria and CV),
    - give it tools (Python functions),
    - put hard limits in code (budgets, allowed tools), not in the model's good will,
    - and log every step so you can see what it did and what it cost.

Where it looks, in this order:
    1. By ROLE: web searches for your target titles on public job boards (Greenhouse, Lever, Ashby, Gem)
       and on the job sites you chose. When it finds a posting on a board, it reads that company's whole
       board through its API. Companies it discovers this way are saved to output/discovered.json.
    2. LinkedIn, if you set up Apify.
    3. Your own list of companies and careers pages, as a complement.

Usage (after onboarding):
    python agent.py                     # everything enabled
    python agent.py --sources web       # role search + your list, no LinkedIn
    python agent.py --include-seen      # show jobs you already saw (for experiments)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

import output
import sources as boards
from seen import Seen
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

sys.stdout.reconfigure(encoding="utf-8")

# ───────────────────────────────────────────────────────────────────────────
# 1. CONFIGURATION
#    Keys live in .env (git-ignored). They are loaded but never printed or logged.
# ───────────────────────────────────────────────────────────────────────────

FOLDER = Path(__file__).parent
PROFILE = FOLDER / "profile"
CRITERIA_PATH = PROFILE / "criteria.md"
CV_PATH = PROFILE / "cv.md"
SETTINGS_PATH = PROFILE / "settings.json"

if not CRITERIA_PATH.exists() or not SETTINGS_PATH.exists():
    sys.exit("No profile yet. Run `python onboard.py` first.")

load_dotenv(FOLDER / ".env")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN")
if not ANTHROPIC_API_KEY:
    sys.exit("ANTHROPIC_API_KEY is missing in .env. Run `python onboard.py`.")

# If you run this from a terminal that is logged in to Claude, drop those credentials so the
# agent can only use the API key (your credits), never your personal session.
for var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(var, None)

SETTINGS = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
MODEL = SETTINGS.get("model", "claude-sonnet-5-5")
LANGUAGE = SETTINGS.get("language", "English")
MAX_TURNS = 60
MAX_SPEND_USD = float(SETTINGS.get("max_spend_usd", 1.5))  # the SDK stops the run if it goes over

LINKEDIN_ACTOR = "curious_coder/linkedin-jobs-scraper"
LINKEDIN_LOCATION = SETTINGS.get("linkedin_location", "")
LINKEDIN_CAP = int(SETTINGS.get("linkedin_cap", 20))   # max LinkedIn jobs per run (each one costs money)
LINKEDIN_WINDOW = "pastWeek"
BOARDS_CAP = int(SETTINGS.get("board_jobs_cap", 30))        # max board jobs sent to the model per run
WEBSEARCH_CAP = int(SETTINGS.get("role_searches", 10))       # web searches by role: the main way to find jobs
WEBFETCH_EXTRA = 8         # pages read with WebFetch beyond your own list of careers pages
ROLE_FIRST = min(WEBSEARCH_CAP, int(SETTINGS.get("role_searches_first", 6)))  # searches before their own list
MAX_PER_COMPANY = 3        # jobs per company sent to the model: keeps the run about roles, not a few companies
DISCOVERED_PATH = output.OUTPUT / "discovered.json"

STAMP = datetime.now().strftime("%Y%m%d-%H%M")
LOG_PATH = FOLDER / "logs" / f"run-{STAMP}.md"
REPORT_PATH = output.OUTPUT / f"report-{STAMP}.md"
LOG_PATH.parent.mkdir(exist_ok=True)
output.OUTPUT.mkdir(exist_ok=True)

# The CLI under the SDK keeps its config in ~/.claude.json. Give the agent its own folder so it
# never touches (or sees) your personal Claude setup, and parallel runs don't corrupt it.
AGENT_CONFIG = FOLDER / ".claude-agent"
AGENT_CONFIG.mkdir(exist_ok=True)
AGENT_ENV = {"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY, "CLAUDE_CONFIG_DIR": str(AGENT_CONFIG)}

ALL_SOURCES = boards.load_sources()
SOURCES = boards.boards(ALL_SOURCES)      # job boards with an API, scanned in code
PAGES = boards.pages(ALL_SOURCES)         # any other careers page, read with WebFetch
FILTERS = boards.Filters()
SEEN = Seen()
SKIP_SEEN = True

state = {"linkedin_jobs": 0, "apify_runs": 0, "apify_usd": 0.0, "linkedin_raw": [],
         "WebSearch": 0, "WebFetch": 0, "web_blocked": 0, "board_jobs": 0, "boards_scanned": [],
         "board_raw": {}, "already_seen": 0, "delivered": [], "discovered": {}, "role_first": 0, "read": set()}


def role_searches_pending() -> int:
    """How many role searches are still due before the agent may use the person's own list or finish.
    Enforced in code because the model, left alone, starts with the familiar names on the list."""
    return max(0, state["role_first"] - state["WebSearch"])


def log(tag: str, text: str = "") -> None:
    line = f"{tag} {text}".rstrip()
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n\n")


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + " …"


def text_result(payload, error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], **({"is_error": True} if error else {})}


# ───────────────────────────────────────────────────────────────────────────
# 2. TOOLS
#    A tool = Python function + name + description + parameters. The model never sees
#    the code, only the name and description, so the description is the interface.
# ───────────────────────────────────────────────────────────────────────────

def _run_apify(run_input: dict, n: int):
    from apify_client import ApifyClient
    client = ApifyClient(APIFY_API_TOKEN)
    run = client.actor(LINKEDIN_ACTOR).call(run_input=run_input, max_items=n,
                                            max_total_charge_usd=Decimal("0.10"), logger=None)
    if run is None:
        return [], 0.0, "NO_RESPONSE"
    items = client.dataset(run.default_dataset_id).list_items(limit=n).items
    return items[:n], float(run.usage_total_usd or 0), str(run.status)


@tool(
    "search_linkedin",
    "Searches LinkedIn job postings through an Apify scraper. Pass one concrete job title in English "
    "(e.g. 'Head of Pricing'). The whole run has a LinkedIn budget (each answer says how much is left): "
    "split max_results across several searches. Every job costs real money.",
    {"keywords": str, "max_results": int, "remote": bool},
)
async def search_linkedin(args: dict) -> dict:
    left = LINKEDIN_CAP - state["linkedin_jobs"]
    if left <= 0:
        return text_result(f"LinkedIn budget used up ({LINKEDIN_CAP} jobs). Work with what you have.", True)
    n = max(1, min(int(args.get("max_results", 5)), left))
    keywords = args["keywords"].strip() + (" remote" if args.get("remote") else "")
    run_input = {"keywords": keywords, "location": LINKEDIN_LOCATION, "datePosted": LINKEDIN_WINDOW,
                 "limitPerSource": n, "scrapeCompany": False}
    try:
        items, cost, status = await asyncio.to_thread(_run_apify, run_input, n)
    except Exception as e:  # the error goes back to the model as an observation, never with keys
        return text_result(f"Apify error: {type(e).__name__}: {e}", True)

    state["apify_runs"] += 1
    state["apify_usd"] += cost
    first_id = state["linkedin_jobs"] + 1
    state["linkedin_jobs"] += len(items)
    state["linkedin_raw"].extend(items)
    log("   💲 APIFY", f"{status} · {len(items)} jobs · US${cost:.4f} · LinkedIn {state['linkedin_jobs']}/{LINKEDIN_CAP}")

    compact, skipped = [], 0
    for i, it in enumerate(items, start=first_id):
        if SKIP_SEEN and SEEN.seen(it.get("companyName"), it.get("title"), it.get("link")):
            skipped += 1
            continue
        state["delivered"].append((it.get("companyName"), it.get("title"), it.get("link"), "LinkedIn"))
        compact.append({"id": f"LI-{i}", "title": it.get("title"), "company": it.get("companyName"),
                        "location": it.get("location"), "posted": it.get("postedAt"),
                        "salary": it.get("salary") or "not published", "link": it.get("link"),
                        "description": clip(boards.unescape(it.get("descriptionText", "")), 1500)})
    state["already_seen"] += skipped
    return text_result({"search": keywords, "jobs": compact, "skipped_already_seen": skipped,
                        "budget_left": LINKEDIN_CAP - state["linkedin_jobs"]})


@tool(
    "scan_board",
    "Reads ONE company's job board through its public API (fresh data) and returns only the jobs that pass "
    "the person's location and title filters. Pass either a job link you found with WebSearch on Greenhouse, "
    "Lever, Ashby or Gem (any posting of that company works), or the name of a board on their own list: "
    f"{', '.join(f'{k} ({v.get('priority', 'medium')})' for k, v in SOURCES.items()) or 'none'}. "
    f"Total board budget: {BOARDS_CAP} jobs; at most {MAX_PER_COMPANY} per company.",
    {"board": str, "max_results": int},
)
async def scan_board(args: dict) -> dict:
    ref = args["board"].strip()
    run_sources = dict(SOURCES)
    link = ref if "/" in ref else ""
    if not link and role_searches_pending():
        return text_result(f"Search by role first: do {role_searches_pending()} more WebSearch queries for the target "
                           f"roles, and scan the companies you find, before using their own list.", True)
    if link:  # a link found while searching by role
        found = boards.source_from_link(ref)
        if not found:
            return text_result("That link is not on Greenhouse, Lever, Ashby or Gem. Read it with WebFetch "
                               "instead, or pass a board from the list.", True)
        key, entry = found
        if key not in SOURCES:
            run_sources[key] = entry
            state["discovered"].setdefault(key, entry)
    else:
        key = ref.lower()
    if key not in run_sources:
        return text_result(f"Unknown board: {key}. Pass a job link from Greenhouse, Lever, Ashby or Gem, "
                           f"or one of: {', '.join(SOURCES) or 'none'}", True)
    if key in state["boards_scanned"]:
        return text_result(f"{key} was already scanned in this run.", True)
    left = BOARDS_CAP - state["board_jobs"]
    if left <= 0:
        return text_result(f"Board budget used up ({BOARDS_CAP}). Work with what you have.", True)
    try:
        total, passed = await asyncio.to_thread(boards.scan, key, run_sources, FILTERS, link)
    except Exception as e:
        return text_result(f"Error reading {key}: {type(e).__name__}: {e}", True)

    # Already-seen jobs are removed BEFORE choosing, so they don't eat the budget.
    new = [j for j in passed if not (SKIP_SEEN and SEEN.seen(j["company"], j["title"], j["link"]))]
    state["already_seen"] += len(passed) - len(new)
    n = max(1, min(int(args.get("max_results", MAX_PER_COMPANY)), left, MAX_PER_COMPANY))
    chosen = new[:n]
    if key in state["discovered"]:
        state["discovered"][key]["passing"] = len(passed)
    state["boards_scanned"].append(key)
    compact = []
    for j in chosen:
        state["board_jobs"] += 1
        jid = f"WEB-{state['board_jobs']}"
        state["board_raw"][jid] = j
        state["delivered"].append((j["company"], j["title"], j["link"], f"board {key}"))
        compact.append({"id": jid, "title": j["title"], "company": key, "location": j["location"],
                        **({"found_by_your_search": True} if j.get("found_by_search") else {}),
                        "remote": j["remote"], "posted": j["date"], "link": j["link"],
                        "description": clip(j["description"], 600)})
    log("   🗂️  BOARD", f"{key}: {total} jobs → {len(passed)} pass filters → {len(passed) - len(new)} already seen "
                       f"→ {len(chosen)} to the agent · boards {state['board_jobs']}/{BOARDS_CAP}")
    return text_result({"board": key, "total_on_board": total, "pass_filters": len(passed),
                        "skipped_already_seen": len(passed) - len(new), "skipped_by_budget": len(new) - len(chosen),
                        "jobs": compact, "board_budget_left": BOARDS_CAP - state["board_jobs"]})


@tool(
    "read_job",
    "Returns the FULL description of a job already found (id WEB-n or LI-n), at no network cost. "
    "Use it on every finalist before evaluating, so you never say 'unknown' about something the posting says.",
    {"id": str},
)
async def read_job(args: dict) -> dict:
    jid = args["id"].strip().upper()
    state["read"].add(jid)
    if jid in state["board_raw"]:
        j = state["board_raw"][jid]
        text = f"{j['title']} — {j['company']} — {j['location']} — posted {j['date']}\n{j['link']}\n\n{j['description']}"
    elif jid.startswith("LI-") and jid[3:].isdigit() and 0 < int(jid[3:]) <= len(state["linkedin_raw"]):
        it = state["linkedin_raw"][int(jid[3:]) - 1]
        text = (f"{it.get('title')} — {it.get('companyName')} — {it.get('location')} — posted {it.get('postedAt')}\n"
                f"{it.get('link')}\n\n{boards.unescape(it.get('descriptionText', ''))}")
    else:
        return text_result(f"I could not find job {jid}.", True)
    return text_result(clip(text, 6000))


RECOMMENDATIONS = {"High", "Medium", "Pass"}
SHOWN_TEXTS = ("location", "work_mode", "summary", "why", "fit_notes", "gaps", "level", "tradeoff")


def criteria_version() -> str:
    """Reads the version from criteria.md's first line: '# Search criteria (v1, 2026-11-17)' -> 'v1'."""
    m = re.search(r"\((v[\d.]+)", CRITERIA_PATH.read_text(encoding="utf-8").splitlines()[0])
    return m.group(1) if m else "?"


def validate(text: str) -> tuple[list | None, str]:
    """Checks the JSON the model sends. If something is wrong, the message goes back so it can fix it."""
    try:
        jobs = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"jobs_json is not valid JSON: {e}"
    if not isinstance(jobs, list):
        return None, "jobs_json must be a list"
    for i, j in enumerate(jobs):
        missing = [k for k in ("id", "title", "company", "link", "show") if k not in j]
        if missing:
            return None, f"job {i}: missing {missing}"
        if j["show"]:
            if j.get("recommendation") not in RECOMMENDATIONS:
                return None, f"job {j['id']}: recommendation must be High, Medium or Pass"
            for axis in ("fit", "interest"):
                if not isinstance(j.get(axis), int) or not 0 <= j[axis] <= 100:
                    return None, f"job {j['id']}: {axis} must be an integer from 0 to 100"
            missing = [k for k in SHOWN_TEXTS if not j.get(k)]
            if missing:
                return None, f"job {j['id']}: missing texts {missing}"
    return jobs, ""


@tool(
    "save_results",
    "Delivers the final result. Every job with show=true goes to the tracker (output/tracker.csv) and the "
    "run report. Takes jobs_json: a JSON list with EVERY job you reviewed, shown or not. Call it once, at the "
    "end. If the JSON has an error, I tell you which and you call it again.",
    {"jobs_json": str},
)
async def save_results(args: dict) -> dict:
    pending = role_searches_pending()
    if pending:
        return text_result(f"Nothing saved. Do {pending} more role searches first (WebSearch for the target roles, "
                           f"then scan_board on the job board links you find), then call save_results again.", True)
    jobs, error = validate(args.get("jobs_json", ""))
    if error:
        return text_result(f"Nothing saved. {error}", True)
    # Honesty rule, enforced: a job is only evaluated after reading its full posting.
    unread = [str(j["id"]).upper() for j in jobs if j["show"] and str(j["id"]).upper()[:3] in ("WEB", "LI-")
              and str(j["id"]).upper() not in state["read"]]
    if unread:
        return text_result(f"Nothing saved. Read the full posting with read_job before evaluating: {', '.join(unread)}. "
                           f"Then call save_results again.", True)
    today = datetime.now().strftime("%Y-%m-%d")
    shown = [j for j in jobs if j["show"] and not (SKIP_SEEN and SEEN.seen(j["company"], j["title"], j["link"]))]
    hidden = [j for j in jobs if not j["show"]]
    for j in shown:
        jid = str(j["id"]).upper()
        j["source"] = "LinkedIn" if jid.startswith("LI-") else "Page" if jid.startswith("PAGE-") else "Board"
    written = output.append_tracker(shown, today, criteria_version())
    output.write_report(shown, hidden, REPORT_PATH, f"Job Scout run · {today}")
    run_record = {"stamp": STAMP, "date": today, "criteria": criteria_version(), "model": MODEL, "jobs": jobs}
    (FOLDER / "logs" / f"results-{STAMP}.json").write_text(json.dumps(run_record, ensure_ascii=False, indent=1),
                                                           encoding="utf-8")
    # Jobs read from careers pages never went through a tool of ours: register them here.
    state["delivered"] += [(j["company"], j["title"], j["link"], "page") for j in jobs
                           if str(j["id"]).upper().startswith("PAGE-")]
    # Jobs only count as seen once saved: if the run crashes before, nothing is lost.
    new_seen = sum(SEEN.add(c, t, l, s, today) for c, t, l, s in state["delivered"])
    SEEN.save()
    state["shown"] = written
    save_discovered(today, {str(j["company"]).lower() for j in shown})
    log("   📥 SAVED", f"{written} jobs added to {output.TRACKER_PATH.name} · report: {REPORT_PATH.name} "
                     f"· {new_seen} new in seen.json")
    return text_result(f"Saved {written} jobs to the tracker and wrote {REPORT_PATH.name}.")


def save_discovered(today: str, shown_companies: set) -> None:
    """Companies found by searching by role, not on the person's list. Claude Code offers to add the good ones."""
    if not state["discovered"]:
        return
    known = json.loads(DISCOVERED_PATH.read_text(encoding="utf-8")) if DISCOVERED_PATH.exists() else {}
    for key, entry in state["discovered"].items():
        base = {k: v for k, v in entry.items() if k != "passing"}
        row = known.setdefault(key, {**base, "first_seen": today, "runs": 0, "jobs_shown": 0})
        row["runs"] += 1
        row["last_seen"] = today
        row["passing_filters"] = entry.get("passing", 0)
        row["jobs_shown"] += int(key in shown_companies)
    DISCOVERED_PATH.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")


def build_server(use_linkedin: bool, use_boards: bool):
    """Register only this run's tools. A registered-but-forbidden tool still tempts the model to try it."""
    own = ([search_linkedin] if use_linkedin else []) + ([scan_board] if use_boards else [])
    return create_sdk_mcp_server(name="jobs", tools=own + [read_job, save_results])


# Hook: a guard that runs BEFORE every web tool call. The cap is set by code, not by the model.
WEBFETCH_CAP = WEBFETCH_EXTRA + len(PAGES)
WEB_CAPS = {"WebSearch": WEBSEARCH_CAP, "WebFetch": WEBFETCH_CAP}


def deny(reason: str) -> dict:
    state["web_blocked"] += 1
    log("   🚧 HOOK", reason)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


PAGE_URLS = {v["url"].rstrip("/").lower() for v in PAGES.values()}


async def cap_web(entry: dict, tool_use_id, context) -> dict:
    name = entry["tool_name"]
    if name == "WebFetch":
        url = str((entry.get("tool_input") or {}).get("url", ""))
        if boards.detect_ats(url):
            # A job board with an API: scan_board reads the whole company for free and registers it.
            return deny("That link is on a job board with an API. Call scan_board with this link instead: it reads "
                        "this posting and the company's other jobs, and lets read_job give you the full text.")
        if url.rstrip("/").lower() in PAGE_URLS and role_searches_pending():
            return deny(f"Search by role first: {role_searches_pending()} more WebSearch queries before reading "
                        f"their own list of pages.")
    if state[name] >= WEB_CAPS[name]:
        state["web_blocked"] += 1
        log("   🚧 HOOK", f"{name} blocked: {WEB_CAPS[name]} already used")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": f"{name} cap reached. Work with what you have."}}
    state[name] += 1
    return {}


# ───────────────────────────────────────────────────────────────────────────
# 3. INSTRUCTIONS (system prompt): who the agent works for, how it decides, how it delivers.
# ───────────────────────────────────────────────────────────────────────────

def sources_block(use_linkedin: bool, use_boards: bool) -> str:
    """Describe only the enabled tools. What is not mentioned, the model does not try."""
    parts = []
    if use_boards:
        sites = " OR ".join(f"site:{d}" for d in boards.ATS_DOMAINS)
        parts.append(
            f"- WebSearch, by ROLE: your MAIN source. The point is to find roles across many companies, including\n"
            f"  ones the person has never heard of. Max {WEBSEARCH_CAP} searches; do at least {ROLE_FIRST} before\n"
            f"  touching their own list (the tools refuse until then), and use the rest if results are thin. Build each one from a target role\n"
            f"  (or a close synonym) plus a location or 'remote', restricted to public job boards, e.g.\n"
            f"  \"<role>\" \"<city, country or remote>\" ({sites}). Vary roles and places across searches.\n"
            f"- scan_board: whenever a search result is a posting on Greenhouse, Lever, Ashby or Gem, pass its link\n"
            f"  (never WebFetch it: that is blocked). It returns that posting plus the company's other matching jobs.\n"
            f"  Pass EVERY relevant posting link from your search results, one company each: discovering companies\n"
            f"  is the point, so aim for at least 4 different companies found this way. Budget: {BOARDS_CAP} jobs, at most\n"
            f"  {MAX_PER_COMPANY} per company. Spread the budget across many companies rather than a few.")
    if use_linkedin:
        parts.append(f"- search_linkedin: LinkedIn jobs via Apify, last week only. Use the role searches from the "
                     f"criteria. Budget: {LINKEDIN_CAP} jobs; split it in searches of 4 to 8, highest priority first.")
    if use_boards and SOURCES:
        parts.append(f"- Their own list of companies (a complement, after the role searches): pass the name to "
                     f"scan_board. High priority first.")
    if use_boards and PAGES:
        listed = "\n".join(f"  - {k} ({v.get('priority', 'medium')}): {v['url']}" for k, v in PAGES.items())
        parts.append(f"- Careers pages and job sites they chose, to read with WebFetch on EVERY run (no API). Apply\n"
                     f"  the location and title rules of the criteria yourself; give their jobs ids PAGE-n and use the\n"
                     f"  posting's own link. For PAGE-n jobs, read the full posting with WebFetch instead of read_job:\n"
                     f"{listed}")
    if use_boards:
        parts.append(f"- WebFetch: reads a page when there is no API. Max {WEBFETCH_CAP} reads. If a page fails, move on.\n"
                     f"- Besides the job sites they chose, only the company's own pages or its ATS count. Don't use\n"
                     f"  aggregators found by search that repost jobs (Indeed, Glassdoor, Talent.com, Jooble and\n"
                     f"  similar): look for the original posting instead.")
    parts.append("- read_job: full description of a job you already found. Use it on EVERY finalist before evaluating.")
    return "\n".join(parts)


def build_prompt(use_linkedin: bool, use_boards: bool) -> str:
    criteria = CRITERIA_PATH.read_text(encoding="utf-8")
    cv = CV_PATH.read_text(encoding="utf-8") if CV_PATH.exists() else "(no CV provided: evaluate fit from the criteria)"
    return f"""You find and evaluate job postings for one person. Today is {datetime.now():%Y-%m-%d}.
Write every text field in {LANGUAGE}.

# Their criteria
{criteria}

# Their CV
{cv}

# Sources enabled in this run
{sources_block(use_linkedin, use_boards)}

# Process
1. Before each tool call, write ONE line: what you will do and why.
2. Search with the enabled sources, roles first: several companies with one good job beat one company
   with many.
3. Merge everything and remove duplicates (same company + same role, even if the title changes slightly).
4. Apply "Step 1 · Filter" from the criteria. Your job is to BRING options; the person decides. When in doubt, show.
5. Read every job you will show with read_job and evaluate it with "Step 2 · Evaluation". Be honest, don't
   inflate. If something is not in the posting, say "unknown".
6. Call save_results ONCE with jobs_json: a JSON list with EVERY job you reviewed (shown and hidden):
   {{"id": "LI-3", "title": "...", "company": "...", "link": "...", "location": "...",
     "work_mode": "remote | hybrid | on-site | unknown", "salary": "... or not published",
     "show": true, "recommendation": "High | Medium | Pass", "fit": 62, "interest": 78,
     "summary": "3-5 lines: what the role does, who it reports to, team, key requirements. Facts only.",
     "why": "why it could interest them", "fit_notes": "how well they fit and why",
     "gaps": "each one marked bridgeable or hard; 'none' if none",
     "level": "IC or leads a team; above, at or below their level",
     "tradeoff": "the main tradeoff in one short sentence", "reason_hidden": null}}
   If show is false, id, title, company, link, location, show and reason_hidden are enough.
7. When done, answer in 3 lines: jobs seen per source, how many you showed, and your top pick.
"""


# ───────────────────────────────────────────────────────────────────────────
# 4. THE LOOP: ClaudeSDKClient opens the conversation; receive_response() yields every message.
# ───────────────────────────────────────────────────────────────────────────

async def run(use_linkedin: bool, use_boards: bool) -> None:
    native = ["WebSearch", "WebFetch"] if use_boards else []
    allowed = (["mcp__jobs__search_linkedin"] if use_linkedin else []) + (
        ["mcp__jobs__scan_board"] if use_boards else []) + native + ["mcp__jobs__read_job", "mcp__jobs__save_results"]
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=build_prompt(use_linkedin, use_boards),
        mcp_servers={"jobs": build_server(use_linkedin, use_boards)},
        tools=native,                  # native SDK tools: only web, no Bash/Write/Read
        allowed_tools=allowed,         # these run without asking
        hooks={"PreToolUse": [HookMatcher(matcher="WebSearch|WebFetch", hooks=[cap_web])]} if use_boards else None,
        permission_mode="dontAsk",     # anything else is denied
        setting_sources=[],            # don't load CLAUDE.md or settings: a "pure" agent
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_SPEND_USD,
        cwd=str(FOLDER),
        env=AGENT_ENV,
    )
    state["role_first"] = ROLE_FIRST if use_boards else 0
    log(f"# Run {STAMP} · linkedin={use_linkedin} · boards={use_boards} · model={MODEL}")
    SEEN.add_rows(output.read_tracker())
    turn = 0
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Find jobs following your process and deliver them with save_results.")
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                if msg.data.get("apiKeySource") != "ANTHROPIC_API_KEY":
                    log("⛔ STOP", "The agent is not using ANTHROPIC_API_KEY. Stopping so it never spends your session.")
                    await client.interrupt()
                    return
            elif isinstance(msg, AssistantMessage):
                turn += 1
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        log(f"🧠 THINKS [{turn}]", block.text.strip())
                    elif isinstance(block, ToolUseBlock):
                        entry = dict(block.input)
                        if "jobs_json" in entry:
                            entry["jobs_json"] = clip(entry["jobs_json"], 200)
                        log(f"🔧 ACTS [{turn}]", f"{block.name} {json.dumps(entry, ensure_ascii=False)}")
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        log("📥 OBSERVES" + (" (ERROR)" if block.is_error else ""), clip(block.content, 500))
            elif isinstance(msg, ResultMessage):
                summary = {"stamp": STAMP, "model": MODEL, "turns": msg.num_turns,
                           "seconds": round(msg.duration_ms / 1000), "anthropic_usd": msg.total_cost_usd,
                           "apify_usd": round(state["apify_usd"], 4), "linkedin_jobs": state["linkedin_jobs"],
                           "board_jobs": state["board_jobs"], "boards_scanned": state["boards_scanned"],
                           "discovered": sorted(state["discovered"]),
                           "skipped_already_seen": state["already_seen"], "shown": state.get("shown", 0),
                           "web_blocked_by_hook": state["web_blocked"], "ended": msg.subtype}
                (FOLDER / "logs" / f"run-{STAMP}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
                log("✅ DONE", json.dumps(summary, ensure_ascii=False))
                if msg.result:
                    log("📝 FINAL ANSWER", msg.result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Scout Agent")
    parser.add_argument("--sources", choices=["all", "web", "boards", "linkedin"], default="all",
                        help="web (= boards): role search + your list, without LinkedIn")
    parser.add_argument("--include-seen", action="store_true", help="don't skip jobs you already saw")
    a = parser.parse_args()
    SKIP_SEEN = not a.include_seen
    use_linkedin = a.sources in ("all", "linkedin") and bool(APIFY_API_TOKEN)
    use_boards = a.sources in ("all", "web", "boards")
    if a.sources == "linkedin" and not APIFY_API_TOKEN:
        sys.exit("LinkedIn needs APIFY_API_TOKEN in .env. Run `python onboard.py` to add it.")

    lock = FOLDER / "logs" / "running.lock"
    if lock.exists() and datetime.now().timestamp() - lock.stat().st_mtime < 2 * 3600:
        sys.exit(f"A run is already in progress. If not, delete {lock}.")
    lock.write_text(STAMP, encoding="utf-8")
    try:
        asyncio.run(run(use_linkedin, use_boards))
    finally:
        lock.unlink(missing_ok=True)
