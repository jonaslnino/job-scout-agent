"""
Where results land: output/tracker.csv (one row per job shown, across all runs) and a
readable Markdown report per run. The CSV opens in Excel or Google Sheets.

You own three columns in the tracker: `my_verdict` (High / Medium / Pass), `my_note` (why, in a few
words) and `status`. The agent never overwrites them. `learn.py` learns from your verdicts and notes;
`measure.py` uses the verdicts to check whether the agent is getting better.
"""

import csv
from pathlib import Path

FOLDER = Path(__file__).parent
OUTPUT = FOLDER / "output"
TRACKER_PATH = OUTPUT / "tracker.csv"

COLUMNS = ["date_found", "company", "title", "link", "location", "work_mode", "salary",
           "recommendation", "fit", "interest", "summary", "why", "fit_notes", "gaps", "level",
           "tradeoff", "source", "criteria_version", "my_verdict", "my_note", "status"]


def read_tracker() -> list[dict]:
    if not TRACKER_PATH.exists():
        return []
    with TRACKER_PATH.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def append_tracker(jobs: list[dict], date: str, criteria_version: str) -> int:
    """Appends the jobs to show; returns how many rows were written."""
    OUTPUT.mkdir(exist_ok=True)
    new_file = not TRACKER_PATH.exists()
    with TRACKER_PATH.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for j in jobs:
            w.writerow({**j, "date_found": date, "criteria_version": criteria_version,
                        "my_verdict": "", "my_note": "", "status": "to review"})
    return len(jobs)


def write_report(jobs: list[dict], hidden: list[dict], path: Path, header: str) -> None:
    """A Markdown report: shown jobs by interest, then what was hidden and why."""
    lines = [f"# {header}", ""]
    shown = sorted(jobs, key=lambda j: j.get("interest", 0), reverse=True)
    lines.append(f"**{len(shown)} jobs to review** · {len(hidden)} hidden by your hard rules\n")
    for j in shown:
        lines += [
            f"## {j['title']} · {j['company']}",
            f"{j.get('recommendation')} · Fit {j.get('fit')} · Interest {j.get('interest')} · "
            f"{j.get('location')} ({j.get('work_mode')}) · {j.get('salary')}",
            f"[Open the posting]({j['link']})", "",
            j.get("summary", ""), "",
            f"- **Why it could interest you:** {j.get('why')}",
            f"- **Fit:** {j.get('fit_notes')}",
            f"- **Gaps:** {j.get('gaps')}",
            f"- **Level:** {j.get('level')}",
            f"- **Main tradeoff:** {j.get('tradeoff')}", "",
        ]
    if hidden:
        lines.append("## Hidden")
        lines += [f"- {h['title']} · {h['company']}: {h.get('reason_hidden')}" for h in hidden]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
