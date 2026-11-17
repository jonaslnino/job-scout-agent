# Job Scout Agent

An AI agent that scans job boards for you, reads every posting against your criteria and your CV, and hands you a short list with a fit score, an interest score and the main tradeoff of each job. **It brings options; you decide.**

I built it for my own job search in October 2026 with the [Claude Agent SDK](https://docs.claude.com/en/docs/agent-sdk/overview), measured it against my own verdicts on real postings, and then made it generic so anyone can clone it, answer a few questions and run it.

A typical run reviews 30 to 40 postings and costs about **US$0.20**.

## Why

Job boards are noisy. The roles worth your time are spread across company career pages and LinkedIn, and titles are unreliable: a "Senior Manager" can have more scope than a "Head of". Reading everything yourself takes hours a week. Keyword alerts miss the good jobs with odd titles and flood you with the wrong ones.

The agent reads the full posting, judges it the way you would, and tells you why. The design rule that shaped everything: **hiding a job you would want is the expensive mistake; showing you one you dismiss in ten seconds is cheap.** So it filters with "when in doubt, show", and leaves the decision to you.

## Easiest: let Claude set it up

No terminal and no coding. You need [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) and an Anthropic API key.

1. **Get the code.** On this page, click the green **Code** button → **Download ZIP**, and unzip it wherever you like. (If you use git: `git clone https://github.com/jonaslnino/job-scout-agent.git`.) You don't need to create a new project: this folder is the project.
2. **Open the folder in Claude Code.** In the Claude desktop app, open Claude Code and choose the folder you just unzipped (from the ZIP it is called `job-scout-agent-main`). From a terminal, go into the folder and type `claude`.
3. **Say: "Set this up for me."** Claude follows [CLAUDE.md](CLAUDE.md): it installs what's needed and asks about your search in a short conversation.
4. **Paste your API key when Claude asks.** It creates a `.env` file for you to paste the key into, so the key never goes through the chat.
5. **Review and run.** Claude turns your CV into plain text, finds the job boards of the companies you name, checks everything for free, and asks for your OK before the first paid run (about US$0.20).

## Quick start (terminal)

macOS / Linux:

```bash
git clone https://github.com/jonaslnino/job-scout-agent.git
cd job-scout-agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python onboard.py    # ~10 minutes: keys, your criteria, companies to watch
.venv/bin/python sources.py    # free check: how many jobs each board has and how many pass your filters
.venv/bin/python agent.py      # first run
```

Windows (PowerShell): use the `py` launcher, since `python` is often a Microsoft Store shortcut, and call the environment's Python with `.\`:

```powershell
git clone https://github.com/jonaslnino/job-scout-agent.git
cd job-scout-agent
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe onboard.py
.\.venv\Scripts\python.exe sources.py
.\.venv\Scripts\python.exe agent.py
```

Results land in `output/tracker.csv` (opens in Excel or Google Sheets) and a readable report per run, `output/report-*.md`.

### What onboarding asks

1. **Keys.** Anthropic is required: it is the model that reads and scores every job. Apify is optional: it adds LinkedIn search for about US$0.02 per run. Without it, the agent scans only the company job boards you list: reading those boards costs nothing, and you pay only the model's tokens.
2. **Your search.** Target roles, minimum level, where you can work, minimum compensation, languages, hard blockers, soft "no" signals, and "magnets" (things that make you want to see a job anyway). Optionally, your CV as plain text.
3. **Companies and sites to watch.** Paste careers links. Greenhouse, Lever, Ashby and Gem boards are detected and read through their API; any other careers page or job site is added as a page the agent reads on every run.

It writes everything to `profile/` and `.env`, both git-ignored. Your criteria become `profile/criteria.md`: a plain document you can read and edit, and the agent's entire brief.

## How it works

```mermaid
flowchart LR
    A[Company boards<br/>Greenhouse · Lever · Ashby · Gem] -->|public APIs| F[Regex filters<br/>location + title]
    L[LinkedIn<br/>via Apify, optional] --> M
    F -->|only what passes| M[Claude agent loop]
    S[(seen.json<br/>+ tracker)] -->|skip what you saw| M
    C[criteria.md<br/>+ cv.md] --> M
    M -->|reads full postings,<br/>scores, explains| O[tracker.csv<br/>+ report.md]
    O -->|you rate: High · Medium · Pass| E[learn.py]
    E -->|proposes, you approve| C
```

An agent is a loop: the model thinks, asks for a tool, observes the result, and repeats until it is done. The SDK runs that loop. This repo provides the instructions, the tools and the guardrails.

**Tools the agent can use**

| Tool | What it does |
|---|---|
| `scan_board` | Reads one company's board through its public API, already filtered |
| `search_linkedin` | LinkedIn search through Apify, with a per-run budget |
| `read_job` | Full text of a posting it already found, at no network cost |
| `WebSearch` / `WebFetch` | Discovery outside your list of boards, capped by a hook |
| `save_results` | Validates the output and writes the tracker and the report |

**Design decisions**

- **Deterministic code does the cheap work; the model does the judgment.** A board like Stripe lists 700+ jobs. Filtering them by location and title with a regex is free and always gives the same answer, so only the survivors reach the model.
- **Limits live in code, not in the prompt.** Spend caps, per-source budgets and a pre-tool hook stop the run, whatever the model "wants". The agent can only use the tools registered for that run, and it refuses to run on anything but your API key.
- **It brings options; you decide.** Every shown job gets two scores (fit: can you do it very well; interest: does it give you what you want), a recommendation, its gaps marked bridgeable or hard, and the main tradeoff. The recommendation is information, not a filter.
- **Honesty rule.** Every reason cites the posting or the CV. If the posting doesn't say, the answer is "unknown", never a guess.
- **No hidden memory, on purpose.** What it learns lives in `criteria.md`, a document you can read, edit and version. `seen.json` keeps it from showing you the same job twice.

## How it learns

The agent recommends; you decide; it learns from your decisions.

1. **It recommends.** Every job it shows comes with High, Medium or Pass, and the reasons.
2. **You rate.** In `output/tracker.csv`, set `my_verdict` to High (apply now), Medium (maybe) or Pass, and add a few words in `my_note` when you can ("too junior", "love the company", "on-site only").
3. **It finds the patterns.** `python learn.py` compares its recommendations with your verdicts: what you keep choosing, what you always pass on, where it over- or under-rated. It writes a proposal; nothing changes yet.
4. **You approve.** `python learn.py --apply` adds those patterns to your criteria and bumps their version. The next run judges jobs with what it learned.

Nothing is learned behind your back: every lesson is a line in `profile/criteria.md` that you can read, edit or delete. With Claude Code, just say "learn from my ratings".

## Measuring it

A job agent is only useful if it catches the jobs you'd want. With your verdicts in the tracker, run `python measure.py`. It reports, per criteria version:

- **Catch rate:** of the jobs you rated High or Medium, how many it recommended High or Medium.
- **Noise:** of the jobs you passed on, how many it recommended High.

This is how I tuned it. Each version of my criteria was measured on postings I had already judged, and the rule was to test a new version on postings **not** used to write it; otherwise it just memorizes:

| Version | What changed | Caught the jobs I wanted | Shown but I passed |
|---|---|---|---|
| v3 | Scoring on two axes (fit, interest) | 2 of 4 | 1 of 8 |
| v4 | Verdict as an action | 2 of 4 | 2 of 8 |
| v5 | "Bring options, don't decide": filter with "when in doubt, show" | 3 of 4 | 3 of 6 |

v3 and v4 were measured on 12 postings I had judged; v5 on the 10 I judged from a fresh sample of 20, drawn from the same sources as a normal run. The one miss required a language I don't speak, which my own rules said to hide: the agent was right by the rules, so the rule was the thing to revisit.

## Cost and limits

- About US$0.18 of Anthropic and US$0.02 of Apify per run. Every posting the model reads costs tokens, whatever the source. Anthropic API usage is paid with prepaid credits in the Claude Console, separate from a Claude subscription; Max, Ultra and Team plans include monthly API credits you can link to it. `max_spend_usd` in `profile/settings.json` caps the model spend per run (default US$1.50).
- LinkedIn search looks at the last week and is capped at 20 postings per run (`linkedin_cap`).
- Careers pages without an API are read with WebFetch on every run: it works for any site, but it is slower and spends a few more tokens than a job board API.
- The agent never applies, never contacts anyone and never logs in to LinkedIn with your account.

## Files

| File | What it is |
|---|---|
| `CLAUDE.md` | Instructions for Claude Code to set it up and run it for you |
| `onboard.py` | Setup: keys, questions, profile |
| `agent.py` | The agent: tools, guardrails, prompt, loop |
| `sources.py` | Job board readers, careers pages and regex filters (`python sources.py` for a free check) |
| `seen.py` | Memory of jobs already seen |
| `output.py` | Tracker CSV and Markdown report |
| `learn.py` | Turns your ratings into proposed updates to your criteria |
| `measure.py` | Catch rate and noise from your verdicts |
| `examples/` | A sample answers file for non-interactive setup |

## License

MIT. Built by [Jonas López Niño](https://linkedin.com/in/jonasnino).
