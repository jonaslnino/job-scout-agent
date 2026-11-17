"""
Onboarding: sets up your keys and turns a few questions into your search profile.

    python onboard.py                      # interactive, ~10 minutes
    python onboard.py --answers my.json    # non-interactive (same keys as the questions below)

It writes (all git-ignored, so your data never leaves your machine):
    .env                    your API keys
    profile/criteria.md     how the agent filters and scores jobs (edit it freely afterwards)
    profile/filters.json    cheap regex filters applied before the model sees anything
    profile/sources.json    the company job boards to scan
    profile/settings.json   language, model, budgets
    profile/cv.md           your CV in plain text (optional, improves the fit score a lot)

Run it again any time: it shows your current answers as defaults.
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from sources import detect_ats

FOLDER = Path(__file__).parent
PROFILE = FOLDER / "profile"
ENV_PATH = FOLDER / ".env"
ANSWERS_PATH = PROFILE / "answers.json"

DEFAULT_TITLE_EXCLUDED = ["intern", "internship", "trainee", "analyst", "associate", "engineer", "developer",
                          "designer", "recruiter", "assistant", "coordinator", "support", "scientist"]

# (key, question, help text, default, kind) — kind: text | list | yesno
QUESTIONS = [
    ("language", "In which language should the agent write its reports?", "", "English", "text"),
    ("summary", "Describe yourself in one line, the way you'd open a cover letter.",
     "e.g. 'Strategy & Operations leader, 12+ years in marketplaces, led teams of 8, P&L across 7 countries.'",
     "", "text"),
    ("target_roles", "Which job titles are you after? (comma-separated)",
     "e.g. Head of Strategy & Operations, Senior Manager Marketplace, Product Lead. These drive search and filters.",
     "", "list"),
    ("level", "What's the minimum level you'd take, and what feels too junior?",
     "e.g. 'Senior Manager or above, leading a team. Too junior: asks for 5 years or less.'", "", "text"),
    ("locations", "Where can you work on-site or hybrid? (cities or countries, comma-separated)",
     "e.g. Mexico City, Mexico. Leave empty if you only want remote.", "", "list"),
    ("remote_ok", "Are fully remote roles OK?", "", "yes", "yesno"),
    ("remote_excluded_regions", "Remote roles restricted to which regions DON'T work for you? (comma-separated)",
     "Many 'remote' roles are remote only inside the US or the EU. e.g. US, USA, United States, Canada, EMEA",
     "US, USA, United States, UK, EMEA, Europe", "list"),
    ("relocation", "Would you relocate? Where to? (comma-separated, empty if not)", "e.g. Tokyo, Toronto", "", "list"),
    ("min_comp", "What's your minimum compensation?", "e.g. 'US$100k/year base' or '€70k/year gross'", "", "text"),
    ("languages", "Which languages do you speak at business level? (comma-separated)", "", "English", "list"),
    ("hard_blockers", "Hard blockers: what should NEVER be shown to you? (comma-separated)",
     "e.g. sales with quota, crypto companies, pure engineering roles, on-site in another city", "", "list"),
    ("no_signals", "Soft 'no' signals: what lowers your interest but could be outweighed? (comma-separated)",
     "e.g. account management, FP&A as the main function, internal operations", "", "list"),
    ("magnets", "Magnets: what makes you want to see a job anyway? (comma-separated)",
     "e.g. companies you admire, relocation to Tokyo, building with AI, owning a P&L", "", "list"),
    ("title_excluded", "Title words that mean 'skip without reading'? (comma-separated)",
     "Applied by a free regex before the model sees anything.", ", ".join(DEFAULT_TITLE_EXCLUDED), "list"),
    ("cv_path", "Path to your CV as a .md or .txt file (empty to skip)",
     "Export your CV to plain text first. Strongly recommended: it is what the fit score reads.", "", "text"),
]


def ask(question: str, help_text: str, default: str, kind: str) -> str | list | bool:
    print(f"\n{question}")
    if help_text:
        print(f"  {help_text}")
    raw = input(f"  [{default}] > " if default else "  > ").strip() or default
    if kind == "list":
        return [x.strip() for x in raw.split(",") if x.strip()]
    if kind == "yesno":
        return raw.lower().startswith(("y", "s"))
    return raw


def as_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value or "")


# ── Step 1 · Keys ──

def read_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def setup_keys(answers: dict, interactive: bool) -> dict:
    env = read_env()
    if interactive:
        print("\n── Step 1 of 3 · Keys ──")
        print("Anthropic (required): the model that reads and scores every job.")
        print("  Get a key at https://console.anthropic.com → Settings → API keys.")
        print("  A typical run costs about US$0.20. You can cap it in profile/settings.json (max_spend_usd).")
        current = env.get("ANTHROPIC_API_KEY", "")
        key = input(f"  ANTHROPIC_API_KEY{' [keep current]' if current else ''} > ").strip() or current
        while not key.startswith("sk-ant-"):
            key = input("  That doesn't look like an Anthropic key (it starts with sk-ant-). Try again > ").strip()
        env["ANTHROPIC_API_KEY"] = key

        print("\nApify (optional): adds LinkedIn search, the biggest source of postings.")
        print("  Without it, the agent scans only the company job boards you list (no Apify cost; the model still uses tokens).")
        print("  Get a token at https://console.apify.com → Settings → Integrations. About US$0.02 per run.")
        current = env.get("APIFY_API_TOKEN", "")
        token = input(f"  APIFY_API_TOKEN [{'keep current' if current else 'empty to skip'}] > ").strip() or current
        if token:
            env["APIFY_API_TOKEN"] = token
            answers["linkedin_location"] = input(
                f"  Country or city for LinkedIn search [{answers.get('linkedin_location', '')}] > ").strip() \
                or answers.get("linkedin_location", "")
        else:
            env.pop("APIFY_API_TOKEN", None)
    else:
        for k in ("ANTHROPIC_API_KEY", "APIFY_API_TOKEN"):
            if answers.get(k):
                env[k] = answers.pop(k)
    if not env.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is required.")
    ENV_PATH.write_text("# Never commit this file.\n" + "".join(f"{k}={v}\n" for k, v in env.items()),
                        encoding="utf-8")
    return env


# ── Step 2 · Profile questions ──

def ask_profile(answers: dict) -> dict:
    print("\n── Step 2 of 3 · Your search ──")
    print("Short answers are fine. Everything lands in profile/criteria.md, which you can edit later.")
    for key, question, help_text, default, kind in QUESTIONS:
        answers[key] = ask(question, help_text, as_text(answers.get(key, default)), kind)
    return answers


# ── Step 3 · Companies ──

def ask_companies(answers: dict) -> dict:
    print("\n── Step 3 of 3 · Companies to watch ──")
    print("Paste careers links one by one (empty line to finish). Greenhouse, Lever, Ashby and Gem boards are read")
    print("through their API (fast and exact); any other careers page is read by the agent on every run.")
    print("  e.g. https://jobs.ashbyhq.com/openai · https://boards.greenhouse.io/stripe · https://jobs.lever.co/kavak")
    companies = dict(answers.get("companies", {}))
    if companies:
        print(f"  Current: {', '.join(companies)}")
    while True:
        url = input("  Careers link > ").strip()
        if not url:
            break
        found = detect_ats(url)
        if found:
            system, slug = found
            entry = {"system": system, "slug": slug}
        else:
            print("  No job board API found in that link (tip: many careers pages link to jobs.lever.co,")
            print("  greenhouse.io or jobs.ashbyhq.com; that link is better). It can still be read as a page.")
            slug = input("  Short name for this source (e.g. acme) > ").strip().lower().replace(" ", "-")
            if not slug:
                continue
            system, entry = "page", {"system": "page", "url": url}
        priority = "high" if input(f"  {slug}: high priority? [y/N] > ").strip().lower().startswith(("y", "s")) \
            else "medium"
        companies[slug.lower()] = {**entry, "priority": priority}
        print(f"  ✓ {slug} ({system}, {priority})")
    answers["companies"] = companies
    return answers


# ── Writing the profile ──

def bullets(items: list[str], empty: str = "None given.") -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def build_criteria(a: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    where = a.get("locations") or []
    remote = "Fully remote roles are OK" if a.get("remote_ok") else "Fully remote roles are NOT wanted"
    excluded = ", ".join(a.get("remote_excluded_regions") or []) or "none"
    relocation = ", ".join(a.get("relocation") or []) or "none"
    return f"""# Search criteria ({'v' + str(a.get('version', 1))}, {today})
Generated by onboard.py. Edit freely; bump the version in the title when you change how jobs are judged,
so you can compare runs.

The agent's job is to **bring options, not decide**. It filters (Show / Don't show) with the rule
"when in doubt, show", and for every job it shows it gives an evaluation and a recommendation that
the person uses as information. Hiding a job they would want is the costly mistake; showing one they
dismiss in 10 seconds is cheap.

## Profile in one line
{a.get('summary') or 'Not given.'}

## Roles that fit
{bullets(a.get('target_roles', []))}

The title matters less than the mandate: a "Senior Manager" can have more scope than a "Head of".

## Level
{a.get('level') or 'Not given.'}

## Location
- On-site or hybrid in: {', '.join(where) or 'nowhere (remote only)'}.
- {remote}. Remote roles restricted to these regions don't count: {excluded}.
- Would relocate to: {relocation}.
- Anything on-site or hybrid elsewhere, without relocation, is a hard blocker.

## Hard blockers (Don't show, even if everything else is great)
{bullets(a.get('hard_blockers', []))}
- Requires a language they don't speak at business level. They speak: {', '.join(a.get('languages') or []) or 'not given'}.
- Location impossible for them (see Location).

## Soft "no" signals (lower interest; a strong magnet can outweigh them)
{bullets(a.get('no_signals', []))}
- Clearly below their level (see Level).

## Magnets (can make a job worth showing despite a soft "no")
{bullets(a.get('magnets', []))}

If a job has a strong magnet and only one soft "no", **show it** and explain the tradeoff.

## Step 1 · Filter: Show or Don't show?
- **Don't show** only if: it hits a hard blocker, or it has clear soft "no" signals and no magnet.
- **Show** everything else. When in doubt, show. There is no cap.

## Step 2 · Evaluation of every job you show (information, not a decision)
### Interest (0–100): does the job give them what they want?
| Goal | Weight | What counts as met |
|---|---|---|
| Role content | 30 | The day-to-day work matches the roles that fit and their strengths. |
| Compensation | 20 | Meets their minimum: {a.get('min_comp') or 'not given'}. If not published, infer from level and company and mark it "inferred"; never assume it is met. |
| Ownership and impact | 20 | Real scope: a team, a P&L, or direct influence on leadership decisions. |
| Growth | 15 | Lets them learn or build what they want next (see Magnets). |
| Flexibility and company | 15 | Location and work mode fit; company type and stage they like. |

### Fit (0–100): do they meet what the job asks, and would they do it very well?
- If they truly lack a must-have requirement, fit stays at 50 or below. Say whether the gap is bridgeable or hard.
- Experience from the CV that transfers directly counts even without the exact title.
- Level: years and team size asked versus theirs.
- Fit is information, not a filter: a low fit with a bridgeable gap is still shown.

### Recommendation (a suggestion; they decide)
**High** (apply now), **Medium** (apply if there's time, or validate in screening), **Pass**.
- Interest leads: High with interest ≥ 70, Medium with 55–69, Pass below 55.
- A bridgeable gap lowers the recommendation one step at most.
- Honesty rule: every reason and gap cites the posting or the CV; if there's no data, say "unknown".

## LinkedIn keywords (in priority order)
{bullets(a.get('target_roles', []))}

## What their past verdicts revealed
{a.get('_learned') or LEARNED_PLACEHOLDER}
"""


LEARNED_SECTION = "## What their past verdicts revealed"
LEARNED_PLACEHOLDER = """Empty for now. This is how the agent learns: after a run, rate the jobs in output/tracker.csv
(`my_verdict`: High / Medium / Pass, plus a few words in `my_note`), then run `python learn.py`. It proposes
the patterns it sees for this section, and `python learn.py --apply` writes them here once you agree."""


GENERIC_TITLE_WORDS = {"senior", "junior", "manager", "head", "lead", "director", "chief", "principal", "staff",
                       "the", "and", "for", "with", "global", "regional"}


def title_keywords(roles: list[str]) -> list[str]:
    """'Head of Strategy & Operations' -> ['strateg', 'operations', ...]: the words that make a title relevant.
    Seniority words are left out (they appear everywhere) and added back as phrases."""
    words = []
    for role in roles:
        for w in "".join(c if c.isalnum() else " " for c in role.lower()).split():
            if len(w) >= 4 and w not in GENERIC_TITLE_WORDS and w not in words:
                words.append(w)
    return words + ["head of", "director", "general manager", "country manager"]


def write_profile(a: dict) -> None:
    PROFILE.mkdir(exist_ok=True)
    previous = json.loads(ANSWERS_PATH.read_text(encoding="utf-8")) if ANSWERS_PATH.exists() else {}
    criteria_path = PROFILE / "criteria.md"
    old_criteria = criteria_path.read_text(encoding="utf-8") if criteria_path.exists() else ""
    # Re-running onboarding never erases what the agent learned, and never lowers the version.
    m = re.search(r"\(v(\d+)", old_criteria.splitlines()[0]) if old_criteria else None
    a["version"] = max(previous.get("version", 0), int(m.group(1)) if m else 0) + 1
    i = old_criteria.find(LEARNED_SECTION)
    learned = old_criteria[i + len(LEARNED_SECTION):].strip() if i >= 0 else ""
    a["_learned"] = learned if learned and not learned.startswith("Empty for now") else ""
    criteria_path.write_text(build_criteria(a), encoding="utf-8")
    a.pop("_learned", None)
    places = (a.get("locations") or []) + (a.get("relocation") or [])
    filters = {"locations": places, "remote_ok": bool(a.get("remote_ok")),
               "remote_excluded_regions": a.get("remote_excluded_regions") or [],
               "title_keywords": title_keywords(a.get("target_roles", [])),
               "title_excluded": a.get("title_excluded") or []}
    (PROFILE / "filters.json").write_text(json.dumps(filters, ensure_ascii=False, indent=1), encoding="utf-8")
    (PROFILE / "sources.json").write_text(json.dumps(a.get("companies", {}), ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    settings = json.loads((PROFILE / "settings.json").read_text(encoding="utf-8")) \
        if (PROFILE / "settings.json").exists() else {"model": "claude-sonnet-5-5", "max_spend_usd": 1.5,
                                                     "linkedin_cap": 20}
    settings.update({"language": a.get("language") or "English",
                     "linkedin_location": a.get("linkedin_location", settings.get("linkedin_location", ""))})
    (PROFILE / "settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    cv = Path(a.get("cv_path") or "").expanduser()
    if a.get("cv_path") and cv.is_file():
        shutil.copyfile(cv, PROFILE / "cv.md")
    elif a.get("cv_path"):
        print(f"  ! CV not found at {cv}. Add it later as profile/cv.md.")
    ANSWERS_PATH.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up Job Scout Agent")
    parser.add_argument("--answers", help="JSON file with the answers (non-interactive)")
    args = parser.parse_args()

    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8")) if ANSWERS_PATH.exists() else {}
    if args.answers:
        answers.update(json.loads(Path(args.answers).read_text(encoding="utf-8")))
        setup_keys(answers, interactive=False)
    else:
        print("Job Scout Agent · setup. Your answers stay on this machine (profile/ and .env are git-ignored).")
        setup_keys(answers, interactive=True)
        ask_profile(answers)
        ask_companies(answers)
    write_profile(answers)

    print("\n✓ Profile ready in profile/. Read profile/criteria.md once: it is the agent's whole brief.")
    print("  Check your boards for free:  python sources.py")
    print("  First run:                   python agent.py")
    if not answers.get("companies") and not read_env().get("APIFY_API_TOKEN"):
        print("  ! You have no boards and no Apify token: the agent has nothing to search. Run onboarding again.")


if __name__ == "__main__":
    main()
