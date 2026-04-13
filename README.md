# Winters Job Finder

Automated job search pipeline for Senior Technical Product Leadership roles. Collects job postings from multiple sources, scores them against a candidate profile, and delivers a daily HTML email digest with a GitHub Pages dashboard.

## Architecture

```
profile.json          Candidate profile (titles, skills, variants, resume context)
companies.json        136 target companies with verified ATS career URLs
main.py               Pipeline orchestrator (12-step flow)
scorer.py             Job scoring engine (0-100 scale + salary extraction)
scorer_llm.py         Claude Haiku LLM re-scorer with caching
digest.py             HTML email digest generator (4 sections)
emailer.py            Brevo SMTP email sender
db.py                 SQLite persistence / dedup / LLM cache
dashboard.py          Static HTML dashboard generator (dark mode)
track.py              Application tracking CLI
tracking.py           Separate tracking database (CI-safe)
weekly_summary.py     Weekly summary email generator
collectors/
  adzuna.py           Adzuna API collector (12 titles x 3 locations)
  career_pages.py     Greenhouse/Lever/Ashby API scraper (125 companies)
  remotive.py         Remotive API collector (3 categories)
  hn_hiring.py        Hacker News "Who is Hiring" thread parser
.github/workflows/
  daily.yml           Daily pipeline + GitHub Pages deploy (7am PT)
  weekly.yml          Weekly summary email (Fridays 5pm PT)
```

## Pipeline Flow

```
 1. Load profile       (PROFILE_JSON env var or profile.json)
 2. Collect jobs        (Adzuna + career_pages + Remotive + HN Hiring)
 2b. Age filter         (skip postings >30 days old)
 2c. Title+company dedup (collapse Greenhouse multi-location variants)
 3. DB dedup            (SHA-256 hash of title+company+normalized_url)
 4. Location filter     (Seattle metro + Whidbey Island + Everett or remote)
 4b. Negative filter    (skip intern, part-time, clearance, unpaid)
 5. Score               (title + skills + experience + industry + boosts - penalties)
 5b. Backfill           (re-score any DB jobs with NULL scores)
 6. LLM re-score        (Claude Haiku for jobs ≥60, with caching)
 7. Build digest        (New Today + Still Open + Open 7+ Days + Recently Closed)
 8. Send email          (Brevo SMTP with DKIM/DMARC-verified sender)
 9. Save to DB          (upsert all jobs, salary data, detect closed jobs)
 9c. Generate dashboard (static HTML to docs/index.html)
10. Print summary       (collected / filtered / scored / emailed)
```

## Scoring Algorithm

| Component | Points | Method |
|-----------|--------|--------|
| Title match | 0-30 | Exact=30, partial=20, adjacent=10 |
| Skill match | 0-40 | High 3pts (cap 24), medium 2pts (cap 10), low 1pt (cap 6). Includes 40 variant groups. |
| Experience | 0-15 | Years fit=10, team size=5 |
| Industry fit | 0-15 | Industry overlap=10, company size=5 |
| Company boost | +5 | Job is from one of 136 target companies |
| Description boost | +3 | Description ≥500 chars (higher-quality posting) |
| Dealbreaker | -15 | Coding required, CS degree, junior scope, short contract, unpaid |
| Overqualified | -10 | JD asks for significantly fewer years |

**Salary extraction**: Parses "$150K-$200K" patterns from descriptions. Displayed as green badge.

**LLM Tier 2** (jobs scoring ≥60): Claude Haiku evaluates fit, returns 1-10 score, strengths, concerns, recommendation (Apply/Maybe/Skip), salary_range, team_size, reports_to, role_type. Responses cached in `llm_cache` table.

Score badges: **green** (70+), **yellow** (50-69), **red** (<50).

## Target Companies

136 companies across 6 ATS platforms:

| ATS | Count | Examples |
|-----|-------|---------|
| Greenhouse | 90 | Stripe, GitLab, Figma, Databricks, Affirm, Wiz, Brex, Pendo |
| Ashby | 23 | Notion, Zapier, Vanta, Ramp, OpenAI, 1Password, Anthropic |
| Lever | 12 | Shopify, Plaid, Canva, Outreach, Scale AI, Mistral AI |
| Workday | 9 | Zillow, Expedia, T-Mobile, Nordstrom, Boeing, F5 |
| Rippling | 1 | Rippling |
| iCIMS | 1 | Alaska Airlines |

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
| `ANTHROPIC_API_KEY` | No | Claude API key for LLM scoring ([console.anthropic.com](https://console.anthropic.com)) |
| `USE_LLM_SCORING` | No | Set `true` to enable LLM re-scoring (auto-set if ANTHROPIC_API_KEY present) |

### Local Development

```bash
# Install dependencies
pip install requests beautifulsoup4

# Run individual modules
python scorer.py           # Demo scoring with 4 sample jobs
python db.py               # Self-test SQLite operations
python digest.py           # Generate digest_preview.html
python dashboard.py        # Generate docs/index.html
python -m collectors.remotive   # Test Remotive collector (no keys needed)
python -m collectors.adzuna     # Test Adzuna collector (needs API keys)
python -m collectors.hn_hiring  # Test HN Who's Hiring collector

# Dry run full pipeline (no email)
SKIP_EMAIL=true python main.py

# Full pipeline
ADZUNA_APP_ID=xxx ADZUNA_API_KEY=xxx BREVO_SMTP_LOGIN=xxx \
BREVO_SMTP_KEY=xxx EMAIL_TO=you@example.com python main.py

# Application tracking
python track.py search "brex"                    # Find jobs
python track.py set <job_id> applied "via careers page"  # Track
python track.py list                              # View funnel
python track.py show applied                      # List by status

# Inspect the database
sqlite3 data/jobs.db "SELECT title, company, score, salary_min, salary_max FROM jobs ORDER BY score DESC LIMIT 10;"
```

### GitHub Actions

**Daily pipeline** runs at **7am PT** via `.github/workflows/daily.yml`. Commits `jobs.db` + `docs/index.html`, deploys to GitHub Pages.

**Weekly summary** runs **Fridays 5pm PT** via `.github/workflows/weekly.yml`. Sends a trend report email.

Both support manual trigger from the Actions tab.

**Required repository secrets** (Settings > Secrets and variables > Actions):
- `ADZUNA_APP_ID`, `ADZUNA_API_KEY`
- `BREVO_SMTP_LOGIN`, `BREVO_SMTP_KEY`
- `EMAIL_TO`
- `PROFILE_JSON` (paste full contents of profile.json)

Optional: `BREVO_SENDER`, `ANTHROPIC_API_KEY`

## Email Digest Sections

1. **Top 10 Matches** — Today's new scored jobs (full detail: score bar, salary badge, skill pills, LLM insights)
2. **Still Open** — Jobs from the last 7 days still on career pages
3. **Still Open 7+ Days — Apply Soon** — Roles that haven't been filled (positive signal)
4. **Recently Closed** — Jobs that disappeared from career pages

## Dashboard

Dark mode static HTML dashboard at `docs/index.html`, deployed via GitHub Pages.

Features: 6 stat cards, score distribution chart, source breakdown, daily trend, top companies, top 20 matches with salary badges and status dots, application tracking funnel, status legend.

## Application Tracking

Statuses are stored in a separate `data/tracking.db` that CI never overwrites.

```bash
python track.py search "stripe"                        # Find a job
python track.py set <id> applied "sent resume 4/3"     # Mark as applied
python track.py set <id> interviewing "phone screen"   # Update status
python track.py list                                    # View funnel
python track.py show interviewing                       # List by status
python track.py reset <id>                              # Remove override
git add -f data/tracking.db && git commit -m "track" && git push  # Persist
```

Valid statuses: `applied`, `interviewing`, `offer`, `rejected`, `withdrawn`

## File Descriptions

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | 342 | Pipeline orchestrator (12 steps) |
| `scorer.py` | 522 | Job scoring engine + salary extraction |
| `scorer_llm.py` | 298 | Claude Haiku LLM re-scorer with caching |
| `db.py` | 446 | SQLite wrapper (dedup, save, history, LLM cache, URL normalization) |
| `dashboard.py` | 438 | Dark mode HTML dashboard generator |
| `digest.py` | 337 | HTML email builder (4 sections, salary badges) |
| `weekly_summary.py` | 206 | Weekly trend summary email |
| `track.py` | 130 | Application tracking CLI |
| `tracking.py` | 88 | Separate tracking database |
| `emailer.py` | 131 | Brevo SMTP email sender |
| `collectors/career_pages.py` | 285 | Greenhouse/Lever/Ashby API scraper |
| `collectors/hn_hiring.py` | 183 | HN "Who is Hiring" thread parser |
| `collectors/adzuna.py` | 170 | Adzuna API collector (12 titles x 3 locations) |
| `collectors/remotive.py` | 118 | Remotive API collector (3 categories) |
| `profile.json` | ~500 | Candidate profile (44 skills, 40 variant groups, resume context) |
| `companies.json` | ~1000 | 136 target companies with ATS URLs |

**Total**: ~3,700 lines of Python

## Roadmap

- [x] Career pages collector (Greenhouse/Lever/Ashby APIs)
- [x] LLM re-scoring with Claude Haiku
- [x] Application tracking (separate tracking.db)
- [x] Weekly summary digest (Friday workflow)
- [x] GitHub Pages dashboard (dark mode)
- [x] Salary extraction + display
- [x] LLM response caching
- [x] URL normalization (strip tracking params)
- [x] Negative keyword filtering
- [x] HN "Who is Hiring" collector
- [ ] Add LinkedIn job collector
- [ ] Add Workday API scraper (9 companies)
- [ ] AI-powered resume tailoring per job
- [ ] Outcome-based calibration (feedback loop from tracking data)
