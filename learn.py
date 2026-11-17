"""
How the agent learns from you.

The model has no memory between runs. What it "learns" lives in profile/criteria.md, under
"What their past verdicts revealed". This script turns your verdicts into an update of that section:

1. After a run, rate the jobs: `python review.py` opens a page with one button per verdict,
   High (apply now), Medium (maybe) or Pass, and a box for a few words on why. (Or edit
   `my_verdict` and `my_note` in output/tracker.csv by hand.)
2. Run: python learn.py
   Claude compares its recommendations with your verdicts, finds the patterns (what you keep
   choosing, what you always pass on, where it over- or under-rated), and writes a proposal to
   output/learning-proposal.md. Nothing changes yet.
3. Read the proposal. If you agree: python learn.py --apply
   It updates the section in criteria.md and bumps the criteria version, so measure.py can
   compare the next runs against the old version.

You stay in control: the agent proposes, you approve. Costs a few cents per run.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import output
from seen import link_key

FOLDER = Path(__file__).parent
CRITERIA_PATH = FOLDER / "profile" / "criteria.md"
SETTINGS_PATH = FOLDER / "profile" / "settings.json"
LEARNED_PATH = output.OUTPUT / "learned.json"
PROPOSAL_PATH = output.OUTPUT / "learning-proposal.md"
SECTION = "## What their past verdicts revealed"
VERDICTS = {"high": "High", "medium": "Medium", "pass": "Pass",
            "yes": "High", "maybe": "Medium", "no": "Pass", "alta": "High", "media": "Medium", "descartado": "Pass"}


def criteria_version(text: str) -> int:
    m = re.search(r"\(v(\d+)", text.splitlines()[0])
    return int(m.group(1)) if m else 1


def current_section(text: str) -> str:
    i = text.find(SECTION)
    return text[i + len(SECTION):].strip() if i >= 0 else ""


def new_verdicts() -> list[dict]:
    learned = set(json.loads(LEARNED_PATH.read_text(encoding="utf-8"))) if LEARNED_PATH.exists() else set()
    rows = []
    for r in output.read_tracker():
        verdict = VERDICTS.get((r.get("my_verdict") or "").strip().lower())
        if verdict and link_key(r.get("link")) not in learned:
            rows.append({"job": f"{r.get('title')} · {r.get('company')}", "link": r.get("link"),
                         "agent_recommendation": r.get("recommendation"), "fit": r.get("fit"),
                         "interest": r.get("interest"), "agent_why": r.get("why"), "agent_gaps": r.get("gaps"),
                         "level": r.get("level"), "location": r.get("location"), "work_mode": r.get("work_mode"),
                         "my_verdict": verdict, "my_note": r.get("my_note", "")})
    return rows


def build_prompt(criteria: str, rows: list[dict]) -> str:
    return f"""You help a job-search agent learn one person's preferences from their verdicts.

These are their current criteria (the agent's whole brief):
<criteria>
{criteria}
</criteria>

These are jobs the agent showed them, with its recommendation and their verdict
(High = apply now, Medium = maybe, Pass = not interested) and, sometimes, a note on why:
<verdicts>
{json.dumps(rows, ensure_ascii=False, indent=1)}
</verdicts>

Find what the verdicts reveal that the criteria don't already say:
- patterns in what they choose and what they pass on (type of work, level, company, location, industry),
- where the agent's recommendation disagreed with their verdict, and why that likely happened,
- preferences they state in their notes.
Rules: only patterns backed by at least two verdicts, or by one explicit note. Cite the jobs as evidence.
Write in the same language as the criteria. Don't repeat what the criteria already say. Don't invent reasons.

Then rewrite the section "What their past verdicts revealed" so it keeps what is still true, adds the new
patterns as short bullets (each with its evidence in parentheses), and stays under 25 bullets.

Answer with JSON only, no other text:
{{"summary": "3 to 5 lines for the person: what you learned and what will change in the next runs",
  "disagreements": "how many verdicts differed from the agent's recommendation, and the main reason",
  "section": "the full new content of the section, in Markdown, without its heading"}}"""


async def ask_claude(prompt: str, model: str, api_key: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
    config = FOLDER / ".claude-agent"
    config.mkdir(exist_ok=True)
    options = ClaudeAgentOptions(model=model, tools=[], allowed_tools=[], permission_mode="dontAsk",
                                 setting_sources=[], max_turns=1, max_budget_usd=0.5, cwd=str(FOLDER),
                                 env={"ANTHROPIC_API_KEY": api_key, "CLAUDE_CONFIG_DIR": str(config)})
    text = ""
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    return text


def parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Claude did not return JSON")
    data = json.loads(m.group(0))
    if not data.get("section"):
        raise ValueError("The proposal has no section")
    return data


def propose(min_verdicts: int) -> None:
    rows = new_verdicts()
    if len(rows) < min_verdicts:
        print(f"{len(rows)} new verdicts. Rate at least {min_verdicts} jobs in output/tracker.csv "
              f"(my_verdict: High / Medium / Pass) and run this again.")
        return
    load_dotenv(FOLDER / ".env")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is missing in .env.")
    for var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)
    model = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("model", "claude-sonnet-5-5")
    criteria = CRITERIA_PATH.read_text(encoding="utf-8")
    print(f"Learning from {len(rows)} new verdicts...")
    data = parse(asyncio.run(ask_claude(build_prompt(criteria, rows), model, key)))
    data["links"] = [link_key(r["link"]) for r in rows]
    PROPOSAL_PATH.write_text(
        f"# Learning proposal · {datetime.now():%Y-%m-%d}\n\n{data['summary']}\n\n"
        f"**Disagreements:** {data.get('disagreements', '')}\n\n"
        f"## Proposed \"{SECTION[3:]}\"\n\n{data['section']}\n\n"
        f"<!-- learn.py data: {json.dumps(data, ensure_ascii=False)} -->\n", encoding="utf-8")
    print(f"\n{data['summary']}\n\nFull proposal: {PROPOSAL_PATH}\nIf you agree: python learn.py --apply")


def apply() -> None:
    if not PROPOSAL_PATH.exists():
        sys.exit("No proposal yet. Run `python learn.py` first.")
    m = re.search(r"<!-- learn.py data: (.*) -->", PROPOSAL_PATH.read_text(encoding="utf-8"), re.S)
    data = json.loads(m.group(1))
    text = CRITERIA_PATH.read_text(encoding="utf-8")
    version = criteria_version(text) + 1
    lines = text.splitlines()
    lines[0] = re.sub(r"\(v\d+[^)]*\)", f"(v{version}, {datetime.now():%Y-%m-%d})", lines[0])
    text = "\n".join(lines) + "\n"
    i = text.find(SECTION)
    text = (text[:i] if i >= 0 else text + "\n") + f"{SECTION}\n{data['section'].strip()}\n"
    CRITERIA_PATH.write_text(text, encoding="utf-8")
    learned = set(json.loads(LEARNED_PATH.read_text(encoding="utf-8"))) if LEARNED_PATH.exists() else set()
    LEARNED_PATH.write_text(json.dumps(sorted(learned | set(data["links"])), indent=1), encoding="utf-8")
    PROPOSAL_PATH.unlink()
    print(f"criteria.md updated to v{version}. The next run uses it; compare versions with `python measure.py`.")


if __name__ == "__main__":
    if not CRITERIA_PATH.exists():
        sys.exit("No profile yet. Run `python onboard.py` first.")
    p = argparse.ArgumentParser(description="Learn from your verdicts")
    p.add_argument("--apply", action="store_true", help="apply the last proposal to profile/criteria.md")
    p.add_argument("--min", type=int, default=5, help="minimum new verdicts to learn from (default 5)")
    a = p.parse_args()
    apply() if a.apply else propose(a.min)
