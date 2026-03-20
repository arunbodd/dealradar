# DealRadar · AI Car Deal Intelligence

> Describe the car you want in plain English. Get ranked listings, AI deal analysis, and negotiation tactics — in seconds.

```
"CPO Porsche Cayenne under $75k, no accidents"
→ ranked listings · deal score per car · market pulse · full AI breakdown
```

---

## How It Works

1. You type a natural language query into the chat
2. **Agent 1** (Claude Haiku) extracts structured search params from your words
3. Listings are fetched from auto.dev and cached in SQLite — zero repeated API calls
4. **Agent 2** (Claude Sonnet) analyzes any listing against live market comparables and returns a buy/negotiate/pass recommendation
5. **Agent 3** (Claude Haiku) reads inventory stats and writes a market pulse: supply, pricing trends, and a buyer/seller verdict
6. **Agent 4** (Claude Haiku) answers any follow-up car question, grounded in your current search session

---

## Features

- **Natural language search** — Any phrasing; the AI extracts make, model, year, budget, drivetrain, color, mileage, accident history, and more
- **Deal scoring** — 0–100 score per listing based on discount off MSRP, mileage, accident history, and CPO status
- **Title brand detection** — Salvage, rebuilt, lemon, and flood titles trigger a 0.45× score penalty and a forced "Pass" recommendation
- **AI deal analysis** — Recommendation, headline, price vs. market assessment, specific negotiation tactics, green flags, red flags, bottom line
- **Market Pulse** — Supply, pricing, and momentum insights with a buyer/seller market verdict
- **Automotive concierge** — Ask any car question grounded in your current listing session
- **Smart caching** — First search costs 3–5 API calls. Repeat searches cost zero. Stale listings auto-flagged after 2 missed syncs
- **Price drop tracking** — Detects price reductions between refreshes
- **Persistent search history** — Previous searches survive page refreshes via localStorage

---

## Architecture

```
car-deal-finder/
├── api/
│   ├── main.py            # FastAPI backend — routes, SQLite queries, delta sync
│   └── ai_engine.py       # Four AI agents (Anthropic Claude)
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
| 1 — Intent Extractor | claude-haiku-4-5 | Natural language → structured SQL filters |
| 2 — Deal Analyst | claude-sonnet-4-6 | Listing + market data → buy/negotiate/pass |
| 3 — Market Pulse | claude-haiku-4-5 | Inventory stats → supply/pricing/momentum narrative |
| 4 — Concierge QA | claude-haiku-4-5 | Car questions answered with current listing context |

All agents use `tool_choice: {"type": "any"}` — output is always typed JSON, never free text.

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
AUTO_DEV_API_KEY=your_auto_dev_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
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
| `GET` | `/api/usage` | API call budget tracking |
| `GET` | `/api/health` | Env var and provider status |

### Examples

```bash
# Natural language search
curl -X POST "http://localhost:8000/api/chat?query=white+AWD+BMW+X5+under+50k+no+accidents"

# AI deal analysis for a VIN
curl "http://localhost:8000/api/analyze/1GNSCCKR4JR123456"

# Market pulse
curl "http://localhost:8000/api/market-intel?make=BMW&model=X5"
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

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python 3.10+, SQLite |
| AI | Anthropic Claude API — Haiku (speed) + Sonnet (analysis) |
| Frontend | Vanilla JS, single HTML file, no build step |
| Data | Auto.dev API with delta-sync caching |
| Deployment | Render.com |

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Built with [Claude](https://claude.ai) · [Anthropic](https://anthropic.com)*
