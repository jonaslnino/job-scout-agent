# Instructions for Claude: set up and run Job Scout Agent for the user

You are helping someone use this repo without touching a terminal themselves. They may not be technical.
Talk to them in their language, keep each message short, and do the technical steps yourself.
Read README.md once for context. Never commit, push or print anything from `.env`, `profile/` or `output/`.

## If `profile/criteria.md` does not exist: first-time setup

Tell them in one line what will happen (about 10 minutes, a few questions, then a first run of about US$0.20),
then go step by step.

### 1. Python environment
- Find a working Python 3.10+: try `py --version`, then `python3 --version`, then `python --version`.
  On Windows, `python` may be a Microsoft Store alias that fails; prefer `py`.
- If none works, stop and tell them how to install it (python.org, tick "Add python.exe to PATH" on Windows),
  or offer to run `winget install Python.Python.3.12` on Windows with their OK.
- Create the environment with that interpreter: `<python> -m venv .venv`.
- From here on, always call the environment's Python directly, never "activate":
  Windows `.\.venv\Scripts\python.exe`, macOS/Linux `.venv/bin/python`. Below it is written `<venv-python>`.
- Install: `<venv-python> -m pip install -r requirements.txt`.

### 2. Keys (the key never goes through the chat)
- Create `.env` with these two lines and ask them to open it and paste their key after the `=`:
  ```
  ANTHROPIC_API_KEY=
  APIFY_API_TOKEN=
  ```
- Explain: Anthropic is required (console.anthropic.com → Settings → API keys). API usage is billed in the
  Claude Console with prepaid credits; Max, Ultra and Team plans include monthly API credits they can link.
  Apify is optional: it adds LinkedIn for about US$0.02 per run; leave the line empty to skip it.
- When they say it's done, check that `.env` has a value starting with `sk-ant-` without printing it
  (for example, read the file and only report "found" or "missing").

### 3. Their search (conversation, not a form)
Ask these in small groups of 2 or 3, in plain words, and suggest examples from what they already told you.
Store the answers in `profile/answers.json` with exactly these keys:

| Key | Type | What to ask |
|---|---|---|
| `language` | text | Language for the reports |
| `summary` | text | Who they are in one line (years, focus, team size) |
| `target_roles` | list | Job titles they are after |
| `level` | text | Minimum level, and what feels too junior |
| `locations` | list | Cities or countries for on-site or hybrid work (empty for remote only) |
| `remote_ok` | bool | Are fully remote roles OK |
| `remote_excluded_regions` | list | "Remote" regions that don't work for them (default: US, USA, United States, UK, EMEA, Europe) |
| `relocation` | list | Places they would move to |
| `min_comp` | text | Minimum compensation, with currency and period |
| `languages` | list | Languages at business level |
| `hard_blockers` | list | Never show me… |
| `no_signals` | list | Lowers my interest… |
| `magnets` | list | Show me anyway if… (companies, conditions) |
| `title_excluded` | list | Title words to skip (default: intern, internship, trainee, analyst, associate, engineer, developer, designer, recruiter, assistant, coordinator, support, scientist) |
| `cv_path` | text | Leave empty; handle the CV in step 4 |
| `companies` | object | Filled in step 5 |
| `linkedin_location` | text | Only if they added an Apify token: country or city for LinkedIn |

If they have an answer that fits nowhere, put it in the closest list in their own words.

### 4. CV
Ask them to drop or point to their CV (PDF, DOCX or text). Convert it to plain Markdown and save it as
`profile/cv.md`. Keep their wording; don't improve or summarize it.

### 5. Companies to watch
Ask for 3 to 10 companies they'd like to work for. For each one, find the careers link that lives on
Greenhouse, Lever, Ashby or Gem (the company's careers page usually links to it; search if needed). Check it with
`<venv-python> -c "from sources import detect_ats; print(detect_ats('<link>'))"` and store each one in
`companies` as `{"<slug lowercase>": {"system": ..., "slug": ..., "priority": "high" | "medium"}}`, asking
which ones are high priority. If a company uses another system, tell them it isn't supported yet and skip it.

### 6. Build the profile and check it for free
- Run `<venv-python> onboard.py --answers profile/answers.json`.
- Show them a short summary of `profile/criteria.md` (not the whole file) and ask if anything is off; fix it
  by editing `profile/answers.json` and running the command again.
- Run `<venv-python> sources.py` (free: no model). Report jobs per board and how many pass their filters.
  If a board shows 0 passing, offer to loosen `profile/filters.json` before spending anything.

### 7. First run
Ask for a clear OK before running, because it spends their credits (about US$0.20, capped by
`max_spend_usd` in `profile/settings.json`). Then run `<venv-python> agent.py` (or `--sources boards` if they
have no Apify token). It takes 3 to 5 minutes. When it ends, read the newest `output/report-*.md` and give them
the top 3 with one line each, plus where the full report and `output/tracker.csv` are.

Close by explaining, in two lines, how the agent gets better: mark `my_verdict` (yes / maybe / no) in the
tracker, run `measure.py`, and adjust `profile/criteria.md`.

## If the profile exists: day-to-day

- "Run it" / "find me jobs": ask for an OK (it costs credits), run `agent.py`, summarize the new report.
- "Change my criteria": edit `profile/criteria.md` directly, bump the version in its title, and update
  `profile/filters.json` if titles or locations changed.
- "How is it doing?": run `measure.py` and explain catch rate and noise in plain words.
- "Add a company": same as step 5, then edit `profile/sources.json`.

## Never
- Print, log or commit API keys, or anything in `profile/`, `output/` or `.env`.
- Apply to jobs, contact anyone, or log in to LinkedIn with their account.
- Run `agent.py` without their OK.
