"""
Memory of jobs already seen (output/seen.json), so the agent never shows you the same job twice.

Plain, deterministic Python. A job is recognized by its link (without tracking parameters)
or by company + title, normalized. Everything already in output/tracker.csv also counts as seen.
"""

import json
import re
import unicodedata
from pathlib import Path

FOLDER = Path(__file__).parent
SEEN_PATH = FOLDER / "output" / "seen.json"


def _normalize(text: str | None) -> str:
    """'Sr. Manager, Strategy (Méx)' -> 'sr manager strategy mex'."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def link_key(link: str | None) -> str:
    """The same link with or without ?utm=... or #... is the same job."""
    return re.sub(r"[?#].*$", "", (link or "").strip()).rstrip("/").lower()


def role_key(company: str | None, title: str | None) -> str:
    return f"{_normalize(company)}|{_normalize(title)}"


class Seen:
    def __init__(self):
        self.records = json.loads(SEEN_PATH.read_text(encoding="utf-8")) if SEEN_PATH.exists() else []
        self.links = {r["link_key"] for r in self.records if r.get("link_key")}
        self.roles = {r["role_key"] for r in self.records}

    def seen(self, company, title, link) -> bool:
        lk = link_key(link)
        return bool(lk and lk in self.links) or role_key(company, title) in self.roles

    def add_rows(self, rows: list[dict]) -> None:
        """Rows already in the tracker count as seen (in memory only)."""
        for r in rows:
            if r.get("link"):
                self.links.add(link_key(r["link"]))
            self.roles.add(role_key(r.get("company"), r.get("title")))

    def add(self, company, title, link, source: str, date: str) -> bool:
        """Returns True if it was new."""
        if self.seen(company, title, link):
            return False
        r = {"company": company, "title": title, "link": link, "source": source, "seen": date,
             "link_key": link_key(link), "role_key": role_key(company, title)}
        self.records.append(r)
        if r["link_key"]:
            self.links.add(r["link_key"])
        self.roles.add(r["role_key"])
        return True

    def save(self) -> None:
        SEEN_PATH.parent.mkdir(exist_ok=True)
        SEEN_PATH.write_text(json.dumps(self.records, ensure_ascii=False, indent=1), encoding="utf-8")
