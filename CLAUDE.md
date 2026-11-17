# Instructions for Claude: set up and run Job Scout Agent for the user

You are helping someone use this repo without touching a terminal themselves. They may not be technical.
Talk to them in their language and do the technical steps yourself.
Read README.md once for context. Never commit, push or print anything from `.env`, `profile/` or `output/`.

## How to talk to them (applies to everything below)

- **One step per message.** Finish a step, confirm it worked, then move to the next. Never mix two steps
  (for example, don't ask search questions while they are still getting their API key).
- **One question at a time** during the search conversation. Wait for the answer before the next one.
- **Clarify before moving on.** If an answer is vague or could mean two things ("senior roles", "good pay",
  "tech companies"), ask one short follow-up with two or three concrete options, then restate what you
  understood in one line. The value of this setup is a precise brief; a vague brief gives vague results.
- Offer help instead of assuming they know how: "Do you already have an Anthropic API key, or should I walk
  you through getting one?"
- Keep messages short. Do the technical work silently and report only what they need to know or decide.

## If `profile/criteria.md` does not exist: first-time setup

Start with one line on what will happen (about 15 minutes: install, an API key, a short conversation about
their search, then a first run that costs well under US$1), and begin step 1.

### 1. Python environment (no questions unless something fails)
- Find a working Python 3.10+: try `py --version`, then `python3 --version`, then `python --version`.
  On Windows, `python` may be a Microsoft Store alias that fails; prefer `py`.
- If none works, stop and tell them how to install it (python.org, tick "Add python.exe to PATH" on Windows),
  or offer to run `winget install Python.Python.3.12` on Windows with their OK.
- Create the environment with that interpreter: `<python> -m venv .venv`.
- From here on, always call the environment's Python directly, never "activate":
  Windows `.\.venv\Scripts\python.exe`, macOS/Linux `.venv/bin/python`. Below it is written `<venv-python>`.
- Install: `<venv-python> -m pip install -r requirements.txt`. Tell them in one line when it's done.

### 2. Anthropic key (the key never goes through the chat)
- Ask whether they already have an Anthropic API key. If not, offer to guide them, one action per message:
  console.anthropic.com → sign in → Billing (add credits; API usage is prepaid and separate from a Claude
  subscription, though Max, Ultra and Team plans include monthly API credits they can link) → Settings →
  API keys → Create key → copy it.
- Create `.env` with these two lines, open or point them to it, and ask them to paste the key after the `=`:
  ```
  ANTHROPIC_API_KEY=
  APIFY_API_TOKEN=
  ```
- When they say it's done, check that the value starts with `sk-ant-` without printing it (report only
  "found" or "missing").

### 3. LinkedIn (optional, its own step)
Explain in two lines: without it, the agent searches the web by role and reads public job boards; with an
Apify token it also searches LinkedIn, for about US$0.02 per run. Ask if they want it. If yes, guide them
(console.apify.com → Settings → API & Integrations → copy the token) and have them paste it in `.env` the
same way. If no, move on.

### 4. Their search (a conversation, one question at a time)
Go through the topics below in this order, one question per message, clarifying vague answers as described
above. Suggest examples from what they already told you. Store the answers in `profile/answers.json` with
exactly these keys:

| Key | Type | What to ask |
|---|---|---|
| `language` | text | Language for the reports (often obvious from the chat: confirm instead of asking) |
| `target_roles` | list | Job titles they are after. Ask what the job actually does day to day, then propose 3 to 6 titles, including synonyms companies use for the same work. **This is the heart of the search**: the agent looks for these roles across many companies. |
| `level` | text | Minimum level, and what feels too junior |
| `locations` | list | Cities or countries for on-site or hybrid work (empty for remote only) |
| `remote_ok` | bool | Are fully remote roles OK |
| `remote_excluded_regions` | list | "Remote" regions that don't work for them (default: US, USA, United States, UK, EMEA, Europe). Explain that many "remote" jobs are remote only inside one country. |
| `relocation` | list | Places they would move to |
| `min_comp` | text | Minimum compensation, with currency and period |
| `languages` | list | Languages at business level |
| `hard_blockers` | list | Never show me… |
| `no_signals` | list | Lowers my interest… |
| `magnets` | list | Show me anyway if… (conditions, industries, kinds of companies) |
| `example_jobs` | list | Optional: 1 to 3 postings they'd love. If they paste links, read them and store one line each: "title at company: what they like about it" (ask them what they like). If they have none, skip it. |
| `summary` | text | Who they are in one line. Draft it from the CV in step 5 and ask them to confirm. |
| `title_excluded` | list | Title words to skip. Propose the default (intern, internship, trainee, analyst, associate, engineer, developer, designer, recruiter, assistant, coordinator, support, scientist) and remove any that clash with their target roles. |
| `cv_path` | text | Leave empty; the CV goes in step 5 |
| `companies` | object | Filled in step 6 (can stay empty) |
| `linkedin_location` | text | Only if they added an Apify token: country or city for LinkedIn |

If they have an answer that fits nowhere, put it in the closest list in their own words. When the topics are
done, show a 5-line recap and ask if it's right before continuing.

### 5. CV
Ask them to drop or point to their CV (PDF, DOCX or text). Convert it to plain Markdown and save it as
`profile/cv.md`. Keep their wording; don't improve or summarize it. Then draft `summary` and confirm it.

### 6. Companies and job sites (optional extras)
Explain in one line that the agent already searches by role across many companies, so this list is only for
places they want checked on every run anyway. Ask one thing at a time:
- **Job sites.** Ask which job sites they use. If they don't know, suggest two or three well-known ones for
  their country and field (only ones you are confident exist and show listings without a login), and add the
  ones they pick.
- **Companies.** Ask if there are companies they'd love to work for. For each, look for a careers link on
  Greenhouse, Lever, Ashby or Gem (the careers page usually links to it; search if needed) and check it with
  `<venv-python> -c "from sources import detect_ats; print(detect_ats('<link>'))"`.
- Found: store `{"<slug lowercase>": {"system": ..., "slug": ..., "priority": "high" | "medium"}}` in `companies`.
- Not found, or a job site: store `{"<short name>": {"system": "page", "url": "<careers or listing URL>", "priority": ...}}`.
  Pages are read on every run, so they are slower and cost a few more tokens than boards with an API.
It's fine to finish with an empty list.

### 7. Build the profile and check it for free
- Run `<venv-python> onboard.py --answers profile/answers.json`.
- Show a short summary of `profile/criteria.md` (not the whole file) and ask if anything is off; fix it by
  editing `profile/answers.json` and running the command again.
- If they listed companies, run `<venv-python> sources.py` (free: no model) and report jobs per board and how
  many pass their filters. If one shows 0 passing, offer to loosen `profile/filters.json`.

### 8. First run
Ask for a clear OK before running, because it spends their credits (around US$0.30, capped by
`max_spend_usd` in `profile/settings.json`). Then run `<venv-python> agent.py` (or `--sources web` if they
have no Apify token). It takes 3 to 5 minutes. When it ends, read the newest `output/report-*.md` and give them
the top 3 with one line each.

### 9. Rating, so it learns
Explain in two lines: they rate each job High (apply now), Medium (maybe) or Pass, with a few words on why;
then it learns from that. Start the rating page in the background with `<venv-python> review.py` and give them
the link it prints (http://127.0.0.1:8765): one card per job with the agent's reasons, three buttons and a
note box; every click saves to `output/tracker.csv`. If they prefer, they can rate in the chat instead and you
write it into the tracker. When they're done, stop the page and go to "Learn from my ratings".

## If the profile exists: day-to-day

- "Run it" / "find me jobs": ask for an OK (it costs credits), run `agent.py`, summarize the new report, and
  offer the rating page (`review.py`).
- After a run, if `output/discovered.json` has companies the agent found by searching by role, and some had
  jobs shown, mention them in one line and offer to add them to their list. Never add them without an OK.
- "Rate my jobs": start `review.py` and give them the link.
- "Change my criteria": edit `profile/criteria.md` directly, bump the version in its title, and update
  `profile/filters.json` if titles or locations changed.
- "Learn from my ratings": if they rated in the chat, write their verdicts and notes into `output/tracker.csv`
  first. Run `<venv-python> learn.py`, show them the summary, and only with their OK run
  `<venv-python> learn.py --apply`. If it says there are too few verdicts, help them rate more.
- "How is it doing?": run `measure.py` and explain catch rate and noise in plain words.
- "Add a company" or "watch this site": same as step 6, then edit `profile/sources.json`.

## Never
- Print, log or commit API keys, or anything in `profile/`, `output/` or `.env`.
- Apply to jobs, contact anyone, or log in to LinkedIn with their account.
- Run `agent.py` without their OK.
