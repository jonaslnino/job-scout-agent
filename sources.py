"""
Job boards read through their public APIs (Greenhouse, Lever, Ashby, Gem).

Any other careers page can be added as a "page": the agent reads it with WebFetch on every run
instead of through an API (slower and less exact, but it works for any site).

This is plain, deterministic Python, not part of the agent. It downloads a company's
job list, normalizes it to one format and filters it by location and title using the
rules in profile/filters.json. Only what passes the filter reaches the model.

Why: a board like Stripe lists 700+ jobs. Filtering them with the model would cost
many tokens and could be wrong; a regex is free and always gives the same answer.
"""

import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FOLDER = Path(__file__).parent
SOURCES_PATH = FOLDER / "profile" / "sources.json"
FILTERS_PATH = FOLDER / "profile" / "filters.json"

# URL patterns used by onboarding to detect a company's ATS from its careers link.
ATS_PATTERNS = {
    "greenhouse": re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([\w-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.I),
    "gem": re.compile(r"jobs\.gem\.com/([\w-]+)", re.I),
}


def detect_ats(url: str) -> tuple[str, str] | None:
    """'https://jobs.ashbyhq.com/openai' -> ('ashby', 'openai'). None if the ATS is not supported."""
    for system, pattern in ATS_PATTERNS.items():
        m = pattern.search(url)
        if m:
            return system, m.group(1)
    return None


def load_sources() -> dict:
    """{key: {"system": ..., "slug": ..., "priority": "high" | "medium"}} for ATS boards, or
    {key: {"system": "page", "url": ..., "priority": ...}} for any other careers page."""
    if not SOURCES_PATH.exists():
        return {}
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def boards(sources: dict) -> dict:
    """Sources with a public API (scanned in code)."""
    return {k: v for k, v in sources.items() if v.get("system") in READERS}


def pages(sources: dict) -> dict:
    """Careers pages without an API (read by the agent with WebFetch)."""
    return {k: v for k, v in sources.items() if v.get("system") == "page"}


def _compile(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words if w and w.strip()]
    if not words:
        return None
    # Word boundaries: "US" must not match "Business" or "Austin".
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")", re.I)


class Filters:
    """Location and title rules from profile/filters.json (written by onboarding, editable by hand)."""

    def __init__(self, data: dict | None = None):
        if data is None:
            data = json.loads(FILTERS_PATH.read_text(encoding="utf-8")) if FILTERS_PATH.exists() else {}
        self.location_yes = _compile(data.get("locations", []))
        self.remote_ok = bool(data.get("remote_ok", True))
        # "Remote" often means "remote within the US" or "remote within the EU": these words disqualify it.
        self.remote_excluded = _compile(data.get("remote_excluded_regions", []))
        self.title_yes = _compile(data.get("title_keywords", []))
        self.title_no = _compile(data.get("title_excluded", []))

    def passes(self, job: dict) -> bool:
        loc = job.get("location", "")
        remote_text = re.search(r"remote|remoto|anywhere", loc, re.I)
        remote = self.remote_ok and (remote_text or (job.get("remote") and not loc.strip()))
        if remote and self.remote_excluded and self.remote_excluded.search(loc):
            remote = False
        location_ok = bool(self.location_yes and self.location_yes.search(loc)) or bool(remote)
        title = job.get("title", "")
        title_ok = (self.title_yes is None or bool(self.title_yes.search(title))) and not (
            self.title_no and self.title_no.search(title))
        return location_ok and title_ok


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 job-scout-agent"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def unescape(text: str) -> str:
    """Some boards escape HTML two or three times (&amp;amp;). Decode until it stops changing."""
    previous = None
    while text != previous:
        previous, text = text, html.unescape(text)
    return text


def _plain(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    t = re.sub(r"<[^>]+>", " ", unescape(raw_html))
    return " ".join(unescape(t).split())


def _date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):  # Lever uses milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(value)[:10]


# ── One reader per system; all return the same shape. ────────────────────────

def _greenhouse(slug):
    for j in _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")["jobs"]:
        yield {"title": j["title"], "location": (j.get("location") or {}).get("name", ""), "remote": False,
               "date": _date(j.get("first_published") or j.get("updated_at")),
               "link": j["absolute_url"], "description": _plain(j.get("content"))}


def _ashby(slug):
    for j in _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")["jobs"]:
        places = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        yield {"title": j["title"], "location": " / ".join(filter(None, places)),
               "remote": bool(j.get("isRemote")), "date": _date(j.get("publishedAt")), "link": j["jobUrl"],
               "description": unescape(j.get("descriptionPlain") or "") or _plain(j.get("descriptionHtml"))}


def _lever(slug):
    for j in _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        cat = j.get("categories") or {}
        places = cat.get("allLocations") or [cat.get("location") or ""]
        # Lever keeps requirements and responsibilities in "lists", apart from the description:
        # without them the model only sees the intro.
        full = (j.get("description") or "") + "".join(
            f"<h3>{l.get('text', '')}</h3><ul>{l.get('content', '')}</ul>" for l in j.get("lists") or []
        ) + (j.get("additional") or "")
        yield {"title": j["text"], "location": " / ".join(places),
               "remote": (j.get("workplaceType") or "").lower() == "remote", "date": _date(j.get("createdAt")),
               "link": j["hostedUrl"], "description": _plain(full)}


def _gem(slug):
    for j in _get_json(f"https://api.gem.com/job_board/v0/{slug}/job_posts/"):
        loc = j.get("location")
        loc = loc.get("name", "") if isinstance(loc, dict) else (loc or "")
        yield {"title": j["title"], "location": loc, "remote": "remote" in str(j.get("location_type", "")).lower(),
               "date": _date(j.get("first_published_at") or j.get("updated_at")), "link": j["absolute_url"],
               "description": unescape(j.get("content_plain") or "") or _plain(j.get("content"))}


READERS = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever, "gem": _gem}


def scan(key: str, sources: dict, filters: Filters) -> tuple[int, list[dict]]:
    """Returns (total jobs on the board, jobs that pass the filter, newest first)."""
    src = sources[key]
    every = list(READERS[src["system"]](src["slug"]))
    passed = [dict(j, company=key) for j in every if filters.passes(j)]
    passed.sort(key=lambda j: j["date"], reverse=True)
    return len(every), passed


if __name__ == "__main__":
    # Quick check of your sources without spending anything: python sources.py
    srcs, flt = load_sources(), Filters()
    for k, v in pages(srcs).items():
        print(f"{k}: page, read by the agent on each run ({v['url']})")
    for k in boards(srcs):
        try:
            total, ok = scan(k, srcs, flt)
            print(f"{k}: {total} jobs, {len(ok)} pass your filters")
        except Exception as e:
            print(f"{k}: error {type(e).__name__}: {e}")
