# DealRadar · AI Car Deal Intelligence

> Describe the car you want in plain English. Get ranked listings, AI deal analysis, and negotiation tactics — in seconds.

```
"Hybrid SUV under $40k in Atlanta, no accidents"
→ ranked listings · deal score per car · market pulse · full AI breakdown
```

---

## How It Works

1. Type a natural-language query into the chat (make, model, budget, location, fuel type, body style — anything)
2. **Stage 1** (Claude Sonnet) normalises the raw query into a clean, unambiguous description
3. **Stage 2** (Claude Sonnet) extracts structured search params via tool_use — make, model, year, budget, drivetrain, body style, fuel type, location, and more
4. Listings are fetched from auto.dev and cached in SQLite — zero repeated API calls on repeat searches
5. **Agent 2** (Claude Sonnet) analyses any individual listing against live market comparables and returns a buy / negotiate / pass recommendation
6. **Agent 3** (Claude Haiku) reads inventory stats and writes a Market Pulse: supply, pricing trends, momentum, and a buyer/seller verdict
7. **Agent 4** (Claude Haiku) answers any follow-up car question grounded in your current search session

---

## Features

- **Conversational search** — No dropdowns needed. Describe what you want: fuel type (hybrid, EV, PHEV), body style (convertible, SUV, truck), location, budget, mileage, history — all parsed from natural language
- **Two-stage LangGraph intent pipeline** — Query normalisation (Stage 1) + structured extraction (Stage 2), traced end-to-end via LangSmith
- **Deal scoring** — 0–100 score per listing based on discount off MSRP, mileage, accident history, and CPO status
- **Title brand detection** — Salvage, rebuilt, lemon, and flood titles trigger a 0.45× score penalty and a forced "Pass" recommendation
- **AI deal analysis** — Recommendation, headline, price vs. market, negotiation tactics, green flags, red flags, bottom line
- **Market Pulse** — Horizontal collapsible banner with supply, pricing, and momentum insight cards plus a buyer/seller verdict; refreshes on demand
- **Body style intent** — "convertible roof", "drop top", "ragtop", "cabriolet" → `body_style=convertible`; coupe, sedan, SUV, crossover, truck, hatchback, wagon all extracted
- **Fuel type via chat** — Mention "hybrid", "EV", "plug-in hybrid", "diesel" in your query; no sidebar dropdown needed
- **Geo radius search** — Zip code + mile radius from query text or sidebar; "Near Me" auto-detects location via browser
- **Smart caching** — First search costs 3–5 API calls. Repeat searches cost zero. Stale listings auto-flagged after 2 missed syncs
- **Delta sync** — Only changed listings are written on refresh; price drops tracked via `prev_listing_price`
- **Brand category search** — "European cars under $50k", "Japanese sedans" → multi-make search with clickable brand chips
- **Smart model resolution** — Three-layer fallback: regex normalisation → DB fuzzy match → API retry with progressive simplification (e.g., "GLE450" → "GLE")
- **AI step traceback** — Collapsible tool-call cards in the chat sidebar show Stage 1 → Stage 2 → Database Search inputs and outputs
- **Comparison mode** — Add up to 4 listings side-by-side
- **Days on lot** — Green (60+ days), gold (30–60 days) pill on each card
- **Condition chip sync** — Condition filter always reflects what the AI extracted; clicking All/New/Used uses the exact same geographic scope as the original chat search
- **Condition fallback** — If the extracted condition (e.g., "new") yields zero results, automatically retries without it so results always appear
- **Clear All filters** — One-click reset of all sidebar filters
- **Persistent search history** — Previous searches survive page refreshes via localStorage

---

## Architecture

```
car-deal-finder/
├── api/
│   ├── main.py            # FastAPI backend — routes, SQLite queries, delta sync, smart model resolution
│   ├── ai_engine.py       # Four AI agents (Anthropic Claude)
│   └── agents.py          # LangGraph two-stage pipeline (understand → extract) + LangSmith tracing
├── frontend/
│   └── index.html         # Single-file SPA — vanilla JS, no build step
├── data_pipeline/
│   ├── pipeline.py        # Auto.dev scraper → SQLite ingestion (standalone)
│   └── .env               # API keys — NOT committed
├── database/
│   └── schema.sql         # Reference schema (SQLite auto-creates tables on startup)
├── .env.example           # Copy to data_pipeline/.env and fill in keys
├── render.yaml            # Render.com deployment config
├── requirements.txt
└── start.sh               # Local dev launcher
```

### AI Agents

| Agent | Model | Job |
|---|---|---|
| Stage 1 — Query Normaliser | claude-sonnet-4-6 | Raw query → clean, unambiguous description |
| Stage 2 — Intent Extractor | claude-sonnet-4-6 | Clean description → structured SQL filters via tool_use |
| Deal Analyst | claude-sonnet-4-6 | Listing + market data → buy/negotiate/pass recommendation |
| Market Pulse | claude-haiku-4-5 | Inventory stats → supply/pricing/momentum narrative |
| Concierge QA | claude-haiku-4-5 | Car questions answered with current listing context |

All agents use `tool_choice: {"type": "any"}` — output is always typed JSON, never free text.

The two-stage pipeline runs inside a **LangGraph** state machine and is traced end-to-end via **LangSmith** (set `LANGSMITH_API_KEY` to enable).

---

## Setup

### Prerequisites

- Python 3.10+
- [Anthropic API key](https://console.anthropic.com/)
- [Auto.dev API key](https://auto.dev/)

### 1. Clone

```bash
git clone https://github.com/arunbodd/dealradar.git
cd dealradar
```

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example data_pipeline/.env
# Edit data_pipeline/.env and fill in your keys
```

```env
ANTHROPIC_API_KEY=your_anthropic_key_here
AUTO_DEV_API_KEY=your_auto_dev_key_here

# Optional — LangSmith tracing
LANGSMITH_API_KEY=your_langsmith_key_here
LANGCHAIN_PROJECT=dealradar
LANGSMITH_WORKSPACE_ID=default    # required for org-scoped LangSmith API keys
```

> **Never commit this file.** It is excluded by `.gitignore`.

### 4. Run

```bash
bash start.sh
# or directly:
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://localhost:8000` — the frontend is served by FastAPI at the root.

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Natural language search or concierge QA |
| `GET` | `/api/analyze/{vin}` | AI deal analysis for a specific VIN |
| `GET` | `/api/market-intel` | Market pulse for a make/model |
| `GET` | `/api/search` | Direct listing search with filter params |
| `GET` | `/api/filters` | Available filter values for a search combo |
| `GET` | `/api/inventory/status` | Sync metadata, price drops, new listings |
| `POST` | `/api/refresh` | Force re-sync from auto.dev |
| `GET` | `/api/health` | Env var and provider status |

### Key `/api/chat` behaviour

- If make/model can be extracted → search mode: fetch + cache + return ranked listings
- If no make/model found → concierge QA mode: answers the question grounded in current listing context
- If extracted condition returns 0 results → automatically retries without condition filter
- Brand category queries (e.g., "European cars") → multi-make search with clickable brand chips

### Examples

```bash
# Natural language search
curl -X POST "http://localhost:8000/api/chat?query=hybrid+SUV+under+40k+in+Atlanta+no+accidents"

# AI deal analysis for a VIN
curl "http://localhost:8000/api/analyze/1GNSCCKR4JR123456"

# Market pulse
curl "http://localhost:8000/api/market-intel?make=Toyota&model=RAV4"

# Direct search with filters
curl "http://localhost:8000/api/search?make=Toyota&model=Camry&condition=used&max_price=30000&zip_code=30301&radius_miles=50"
```

---

## Deployment (Render)

The repo includes `render.yaml` for one-click deploy on [Render.com](https://render.com).

1. Connect your GitHub repo to Render
2. Set environment variables in the Render dashboard:
   - `ANTHROPIC_API_KEY`
   - `AUTO_DEV_API_KEY`
   - `DB_PATH` → `/tmp/inventory.db`
3. Start command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API — [console.anthropic.com](https://console.anthropic.com) |
| `AUTO_DEV_API_KEY` | Yes | Live inventory data — [auto.dev](https://auto.dev) |
| `DB_PATH` | No | SQLite path (default: `~/.car-deal-finder/inventory.db`; use `/tmp/inventory.db` on cloud) |
| `LANGSMITH_API_KEY` | No | LangSmith tracing — enables pipeline observability |
| `LANGCHAIN_PROJECT` | No | LangSmith project name (default: `dealradar`) |
| `LANGSMITH_WORKSPACE_ID` | No | Required for org-scoped LangSmith keys (typically `default`) |
| `CACHE_TTL_HOURS` | No | How long before a cached search is considered stale (default: `6`) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python 3.10+, SQLite |
| AI | Anthropic Claude API — Sonnet (search + analysis) + Haiku (market pulse + QA) |
| Pipeline | LangGraph state machine + LangSmith tracing |
| Frontend | Vanilla JS, single HTML file, no build step |
| Data | Auto.dev API with delta-sync caching |
| Deployment | Render.com |

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Built by [@arunbodd](https://github.com/arunbodd)*
