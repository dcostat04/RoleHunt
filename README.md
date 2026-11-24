# Job Scout

Job hunting as a PM is exhausting. Relevant roles are scattered across LinkedIn,
Hacker News, Twitter, Wellfound and HasJob, and 95% of listings are irrelevant.
Job Scout automates the morning scroll: it scrapes everything, filters with an LLM,
and sends a clean email digest twice a day.

- Scrapes **LinkedIn, Hacker News, Twitter/X, Wellfound and HasJob** concurrently
- **3-stage filter:** keyword pre-filter → location matching → Gemini Flash LLM scoring
- **Triple-layered deduplication:** URL-based, cross-source and historical
- Runs on **GitHub Actions cron (8 AM + 6 PM IST)**, so there's no infrastructure to pay for

`Python 3.12` · `asyncio + httpx` · `BeautifulSoup` · `Pydantic` · `Gemini API` · `GitHub Actions`

## How it works

```
 LinkedIn ─┐
 HN        ─┤                ┌─ 1. URL dedup (tracking params stripped, LinkedIn/X ids canonicalised)
 X         ─┼─ asyncio ──►   ├─ 3. Historical dedup (data/seen_jobs.json, committed back by CI)
 Wellfound ─┤   gather       ├─ 2. Cross-source dedup (normalised company + title fingerprint)
 HasJob    ─┘                ├─ Stage 1: keyword filter on title
                             ├─ Stage 2: location filter
                             ├─ enrich survivors (LinkedIn full descriptions, fetched lazily)
                             ├─ Stage 3: Gemini Flash scores 0-10 vs. your profile, in batches
                             └─ HTML + text email digest → mark processed jobs as seen
```

The cheap stages run first, so only a small fraction of listings ever reach the LLM.
Historical dedup runs before scoring, so a job is never paid for twice.

| Source | Method | Notes |
|---|---|---|
| LinkedIn | Public `jobs-guest` endpoints (no login) | Full description fetched only for jobs that pass stages 1 and 2 |
| Hacker News | Algolia HN API, latest *Who is hiring?* thread | Parses `Company \| Role \| Location` headers |
| Twitter/X | Official v2 recent search | Needs `X_BEARER_TOKEN` (paid API tier); skipped without it |
| Wellfound | `__NEXT_DATA__` Apollo cache on role/location pages | Bot protection often returns 403 from datacenter IPs; reported, not fatal |
| HasJob | Public Atom feed | |

A failing source never sinks the run. Its error shows up in the log and at the bottom of
the email. If Gemini is unavailable, jobs are emailed unscored (`fail_open`) rather than dropped.

## Setup

1. **Create a repo** from this folder and push it to GitHub.
2. **Edit `config.yaml`.** The `profile` block is what the LLM scores against, so make it specific
   (seniority, domains, location constraints, deal-breakers). Adjust queries, keywords and locations to match.
3. **Add repository secrets** (Settings → Secrets and variables → Actions):

   | Secret | Required | |
   |---|---|---|
   | `GEMINI_API_KEY` | yes | From Google AI Studio |
   | `SMTP_USER` | yes | e.g. your Gmail address |
   | `SMTP_PASSWORD` | yes | Gmail **App Password**, not your account password |
   | `EMAIL_TO` | yes | Comma-separated recipients |
   | `X_BEARER_TOKEN` | no | Enables the Twitter/X source |

   Optional repository *variables* `SMTP_HOST` / `SMTP_PORT` (defaults `smtp.gmail.com` / `587`).
4. **Allow the workflow to push:** Settings → Actions → General → Workflow permissions →
   *Read and write*. The workflow commits `data/seen_jobs.json` after each run.
5. Run it once manually: Actions → *Job Scout* → *Run workflow* (tick *dry run* to preview first;
   the rendered digest is uploaded as a workflow artifact).

Schedules are `30 2 * * *` and `30 12 * * *` UTC (08:00 and 18:00 IST). GitHub may start scheduled
runs a few minutes late under load.

## Running locally

```bash
python3.12 -m venv .venv && . .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

```bash
cp .env.example .env   # then fill it in
```

```bash
set -a && . ./.env && set +a && python -m job_scout --dry-run
```

`--dry-run` sends no email and leaves history untouched; open `out/digest.html` to see the result.

Other flags:

- `--sources linkedin,hn,hasjob` limits the run to those sources
- `--no-llm` skips Gemini scoring
- `--send-empty` sends the email even when nothing matched
- `-v` turns on debug logging

## Tests

```bash
pytest -q
```

The suite covers every parser against fixture responses, the filters and dedup layers,
and full pipeline runs with all HTTP calls (including Gemini) mocked via `respx`.

## Project layout

```
job_scout/
  scrapers/        one module per source, shared retry/backoff base
  filters/         keyword.py, location.py, llm.py (Gemini REST, structured JSON output)
  dedup.py         URL normalisation, fingerprints, SeenStore
  digest.py        HTML + plain-text email rendering
  emailer.py       SMTP (STARTTLS or SSL)
  pipeline.py      orchestration
config.yaml        your profile, queries, keywords, locations, thresholds
data/seen_jobs.json  historical dedup state (auto-committed)
.github/workflows/job-scout.yml
```

## Caveats

- LinkedIn's guest endpoints and Wellfound's page structure are unofficial and change
  without notice. If a source starts returning 0 jobs, check its parser first.
- Scrape politely. Concurrency is capped (`http.max_concurrency`) and requests back off on 429.
  Review each site's terms before running this at a higher frequency.
