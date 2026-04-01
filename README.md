# Winters Job Finder

Automated job search pipeline for Senior Technical Product Leadership roles. Collects job postings from multiple sources, scores them against a candidate profile, and delivers a daily HTML email digest.

## Architecture

```
profile.json          Candidate profile (titles, skills, experience, preferences)
companies.json        85 target companies with verified ATS career URLs
main.py               Pipeline orchestrator (10-step flow)
scorer.py             Job scoring engine (0-100 scale)
digest.py             HTML email digest generator
emailer.py            Brevo SMTP email sender
db.py                 SQLite persistence / deduplication
collectors/
  adzuna.py           Adzuna API collector (product leadership roles)
  remotive.py         Remotive API collector (remote senior roles)
  career_pages.py     Company career page scraper (stub)
.github/workflows/
  daily.yml           GitHub Actions cron job (7am PT daily)
```

## Pipeline Flow

```
1. Load profile      (PROFILE_JSON env var or profile.json)
2. Collect jobs       (Adzuna + Remotive + career_pages)
3. Deduplicate        (SHA-256 hash of title+company+url against SQLite)
4. Filter location    (Seattle metro or remote)
5. Score              (title + skills + experience + industry - penalties)
6. LLM re-score       (stub — USE_LLM_SCORING=true for jobs scoring 50+)
7. Build digest       (HTML email with ranked jobs, score bars, skill pills)
8. Send email         (Brevo SMTP with DKIM/DMARC-verified sender)
9. Save to DB         (upsert all jobs for future dedup)
10. Print summary     (collected / new / filtered / scored / emailed)
```

## Scoring Algorithm

| Component | Points | Method |
|-----------|--------|--------|
| Title match | 0-30 | Exact=30, partial=20, adjacent=10 |
| Skill match | 0-40 | High-weight 3pts (cap 24), medium 2pts (cap 10), low 1pt (cap 6) |
| Experience | 0-15 | Years fit=10, team size=5 |
| Industry fit | 0-15 | Industry overlap=10, company size=5 |
| Dealbreaker | -15 | Coding required, CS degree, junior scope, short contract, unpaid |
| Overqualified | -10 | JD asks for significantly fewer years |

Score badges: **green** (70+), **yellow** (50-69), **red** (<50).

## Target Companies

85 companies across 5 ATS platforms:

| ATS | Count | Examples |
|-----|-------|---------|
| Greenhouse | 53 | Stripe, GitLab, Figma, Databricks, Affirm, Wiz |
| Ashby | 13 | Notion, Zapier, Vanta, Ramp, OpenAI, 1Password |
| Lever | 10 | Shopify, Plaid, Canva, Outreach, Scale AI |
| Workday | 8 | Zillow, Expedia, T-Mobile, Nordstrom, F5 |
| Rippling | 1 | Rippling |

## Setup

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ADZUNA_APP_ID` | Yes | Adzuna API app ID ([developer.adzuna.com](https://developer.adzuna.com)) |
| `ADZUNA_API_KEY` | Yes | Adzuna API key |
| `BREVO_SMTP_LOGIN` | Yes | Brevo account email |
| `BREVO_SMTP_KEY` | Yes | Brevo SMTP password ([app.brevo.com](https://app.brevo.com)) |
| `BREVO_SENDER` | No | From address (default: `Winters Product Group <jobfinder@michaeladamwinters.com>`) |
| `EMAIL_TO` | Yes | Recipient email(s), comma-separated |
| `PROFILE_JSON` | No | Full profile as JSON string (overrides profile.json) |
| `SKIP_EMAIL` | No | Set `true` to skip email send |
| `USE_LLM_SCORING` | No | Set `true` to enable LLM re-scoring (stub) |

### Local Development

```bash
# Install dependencies
pip install requests beautifulsoup4

# Run individual modules
python scorer.py           # Demo scoring with 4 sample jobs
python db.py               # Self-test SQLite operations
python digest.py           # Generate digest_preview.html
python -m collectors.remotive   # Test Remotive collector (no keys needed)
python -m collectors.adzuna     # Test Adzuna collector (needs API keys)

# Dry run full pipeline (no email)
SKIP_EMAIL=true python main.py

# Full pipeline
ADZUNA_APP_ID=xxx ADZUNA_API_KEY=xxx BREVO_SMTP_LOGIN=xxx \
BREVO_SMTP_KEY=xxx EMAIL_TO=you@example.com python main.py

# Inspect the database
sqlite3 data/jobs.db "SELECT title, company, score FROM jobs ORDER BY score DESC LIMIT 10;"
```

### GitHub Actions

The pipeline runs daily at **7am PT** (2pm UTC) via `.github/workflows/daily.yml`. It can also be triggered manually from the Actions tab.

**Required repository secrets** (Settings > Secrets and variables > Actions):
- `ADZUNA_APP_ID`, `ADZUNA_API_KEY`
- `BREVO_SMTP_LOGIN`, `BREVO_SMTP_KEY`
- `EMAIL_TO`
- `PROFILE_JSON` (paste full contents of profile.json)

Optional: `BREVO_SENDER`

## File Descriptions

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | 224 | Pipeline orchestrator |
| `scorer.py` | 439 | Job scoring engine with demo |
| `digest.py` | 217 | HTML email builder with inline CSS |
| `db.py` | 205 | SQLite wrapper (init, dedup, save, history) |
| `collectors/adzuna.py` | 160 | Adzuna API job collector |
| `collectors/remotive.py` | 103 | Remotive API job collector |
| `collectors/career_pages.py` | 25 | Career page scraper (stub) |
| `emailer.py` | 131 | Brevo SMTP email sender |
| `profile.json` | 93 | Candidate profile |
| `companies.json` | ~600 | 85 target companies with ATS URLs |

## Roadmap

- [ ] Implement `career_pages.py` collector (Greenhouse/Lever/Ashby API scrapers)
- [ ] Implement `scorer_llm.py` for AI-powered re-scoring of top candidates
- [ ] Add LinkedIn job collector
- [ ] Improve scoring with full job descriptions from career pages
- [ ] Add weekly summary report with trends
- [ ] Track application status in the database
