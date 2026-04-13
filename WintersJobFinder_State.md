# Winters Job Finder — Project State

> Last updated: 2026-04-02

## Current Iteration: v2.2

### What Works
- **5 collectors**: Adzuna (12 titles x 3 locations), career pages (125 queryable companies via Greenhouse/Lever/Ashby APIs), Remotive (3 categories), HN Who's Hiring (monthly thread)
- **Two-tier scoring**: Tier 1 keyword scoring (0-100 with skill variants, company boost, desc boost) + Tier 2 Claude Haiku LLM (score, strengths, concerns, recommendation, salary, team size, reports_to, role_type)
- **LLM response caching**: Cached in `llm_cache` table, skips API on re-runs
- **URL normalization**: Strips 15+ tracking params (utm_*, fbclid, gh_jid, lever-via, etc.) before dedup
- **Salary extraction**: Regex parses compensation from JDs, stored in DB, displayed as green badges
- **4-section email digest**: New Today, Still Open, Open 7+ Days, Recently Closed + salary badges + LLM insights
- **Dark mode dashboard**: Score charts, source breakdown, daily trend, top companies, top 20 matches, tracking funnel, status legend. Published to GitHub Pages.
- **Application tracking**: Separate `tracking.db` (CI-safe). CLI: search, set, show, list, reset.
- **Weekly summary**: Friday 5pm PT email with trends, top companies, source breakdown, application funnel
- **Pipeline filters**: Age (>30d), negative keywords (intern/part-time/clearance/unpaid), location (Seattle metro + Whidbey + Everett + remote), title+company dedup
- **Score backfill**: Re-scores NULL/0 jobs on each run with company boost
- **Closed detection**: title+company matching (not URL) to avoid false positives from Greenhouse multi-location variants
- **Email delivery**: Brevo SMTP with DKIM+DMARC-verified sender (michaeladamwinters.com)
- **GitHub Actions**: Daily 7am PT (main branch) + weekly Friday 5pm PT. DB + dashboard committed per run.

### What's Stubbed
- `output/` directory — Referenced in workflow artifacts but not yet generated

### Known Issues
- **Workday/Rippling/iCIMS companies (11)**: No public JSON API. Picked up by Adzuna but not by career_pages collector.
- **Node.js 20 deprecation warning**: Actions will be forced to Node.js 24 starting June 2, 2026. Non-blocking.
- **GitHub Pages deploy**: Requires enabling GitHub Actions as source in Settings → Pages.

### Key Metrics (from CI run — 2026-04-02)
- Adzuna: ~1,200 jobs collected (12 titles x 3 locations)
- Career pages: ~200 jobs (125 companies queried)
- Remotive: 0-5 jobs (depends on category inventory)
- HN Hiring: varies monthly
- After dedup + filters: ~150-300 new per run
- Top scores: 96-99 (Brex, Vanta, Contentful, PagerDuty)
- Strong matches (70+): 8+
- Email: Sent successfully
- Dashboard: Generated, deployed to GitHub Pages

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| `profile.json` | Done | 14 target titles, 44 skills + 40 variant groups, 7 roles, 20 key metrics, 2.4K resume context, board experience |
| `companies.json` | Done | 136 companies, 6 ATS types, verified URLs |
| `collectors/adzuna.py` | Done | 12 title queries x 3 locations (Seattle + Remote + Whidbey), rate limiting |
| `collectors/career_pages.py` | Done | Greenhouse, Lever, Ashby public JSON APIs. 125 companies. Title filter. |
| `collectors/remotive.py` | Done | 3 categories (product, marketing, business). Title filter, HTML stripping. |
| `collectors/hn_hiring.py` | Done | Monthly HN 'Who is Hiring' thread via Algolia API. Title filter. |
| `scorer.py` | Done | 6 scoring dimensions + salary extraction, skill variants, company/desc boosts |
| `scorer_llm.py` | Done | Claude Haiku, threshold ≥60, Apply/Maybe/Skip + metadata extraction, response caching |
| `digest.py` | Done | 4 sections, inline CSS, score colors, skill/flag pills, salary badges, LLM insights |
| `emailer.py` | Done | Brevo SMTP, verified sender domain |
| `db.py` | Done | SQLite, URL normalization, salary columns, LLM cache table, status tracking, migrations |
| `dashboard.py` | Done | Dark mode, stat cards, 4 charts, top 20, tracking funnel, status legend |
| `track.py` | Done | CLI for application tracking (search, set, show, list, reset) |
| `tracking.py` | Done | Separate tracking.db, CI-safe, status overrides |
| `weekly_summary.py` | Done | Weekly trend email: stats, companies, sources, funnel, top matches |
| `main.py` | Done | 12-step pipeline, env var config, backfill, negative filter, age filter |
| `daily.yml` | Done | Cron 7am PT on main, workflow_dispatch, DB + dashboard commit, Pages deploy |
| `weekly.yml` | Done | Cron Friday 5pm PT, sends weekly summary email |

## Configuration

### Secrets (GitHub Actions)
| Secret | Status | Notes |
|--------|--------|-------|
| `ADZUNA_APP_ID` | Configured | developer.adzuna.com |
| `ADZUNA_API_KEY` | Configured | developer.adzuna.com |
| `BREVO_SMTP_LOGIN` | Configured | Brevo account email |
| `BREVO_SMTP_KEY` | Configured | Brevo SMTP password |
| `EMAIL_TO` | Configured | Recipient address |
| `PROFILE_JSON` | Configured | Full profile as JSON string |
| `BREVO_SENDER` | Optional | Uses default: jobfinder@michaeladamwinters.com |
| `ANTHROPIC_API_KEY` | Optional | Required for LLM scoring. Auto-enables USE_LLM_SCORING. |

### DNS / Email
- Domain: `michaeladamwinters.com` (via Vercel)
- DKIM: Configured and verified
- DMARC: Configured and verified
- Sender: `Winters Product Group <jobfinder@michaeladamwinters.com>`

## Target Profile Summary
- **Role**: Senior Technical Product Leader (15+ years)
- **Companies**: Amazon (6 years), Apple, Google, Chewy
- **Target titles**: Director/VP/Head of Product, Senior/Principal/Group PM, Director Technical PM
- **Top skills**: Product strategy, roadmap, P&L, platform, PLG, data-driven, AI/ML, supply chain, compliance/GRC
- **Location**: Remote, Seattle/Bellevue/Redmond, Whidbey Island/Oak Harbor, Everett
- **Company size**: Mid-size to enterprise
- **Dealbreakers**: Hands-on coding, CS degree required, junior scope, short contract, unpaid

## Companies Breakdown
- **Total**: 136
- **Greenhouse**: 90 | **Ashby**: 23 | **Lever**: 12 | **Workday**: 9 | **Rippling**: 1 | **iCIMS**: 1

### By Category
- **Seattle/PNW** (16): Zillow, Redfin, Expedia, T-Mobile, Nordstrom, Smartsheet, Qualtrics, Outreach, Highspot, Rover, OfferUp, Remitly, F5, Twitch, Alaska Airlines, Boeing
- **Remote-first** (30+): GitLab, Zapier, Notion, Linear, Canva, Deel, PostHog, Supabase, DuckDuckGo, Grafana Labs, Webflow, Tailscale, dbt Labs, Cursor, Replit, etc.
- **E-commerce/marketplace** (10+): Shopify, Chewy, DoorDash, Instacart, Faire, OfferUp, Toast, BigCommerce, Whatnot
- **Fintech** (10+): Stripe, Coinbase, Affirm, Ramp, Plaid, Remitly, Brex, Mercury, Kraken, Marqeta, Expensify
- **Security/compliance** (10+): Okta, OneTrust, Drata, Vanta, Wiz, Snyk, 1Password, Abnormal Security, Coalition, ID.me, Persona
- **AI/ML** (8+): OpenAI, Anthropic, Databricks, Mistral AI, Scale AI, Weights & Biases, Cohere, Perplexity
- **Analytics/data** (10+): Amplitude, Datadog, PostHog, Fivetran, Metabase, Pendo, Mixpanel, Heap, Hightouch, Census, Monte Carlo, LiveRamp, Segment, Starburst

## Database Schema
- **jobs table**: id, title, company, location, url, description, source, date_posted, score, matched_skills, flags, first_seen, last_seen, status, salary_min, salary_max
- **llm_cache table**: job_id, response (JSON), created
- **tracking.db** (separate): job_id, status (applied/interviewing/offer/rejected/withdrawn), notes, updated

## Codebase Stats
- **Total Python**: ~3,700 lines across 14 files
- **Companies**: 136 with verified ATS URLs
- **Skills**: 44 keywords + 40 variant groups (~200 matchable terms)
- **Collectors**: 5 (Adzuna, career pages, Remotive, HN Hiring, + career_pages covers 3 ATS APIs)

## Next Steps (Priority Order)
1. **Add LinkedIn job collector** — Broader coverage of product leadership roles
2. **Add Workday API scraper** — Cover 9 Workday companies (Zillow, Expedia, T-Mobile, Boeing, etc.)
3. **AI-powered resume tailoring** — Generate customized resume per job using Claude + resume context
4. **Outcome-based calibration** — Analyze tracking data to improve scoring weights
5. **Obsidian integration** — Export scored jobs as markdown with YAML frontmatter
