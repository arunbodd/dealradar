# DealRadar · Best Car Deals

> **AI-powered car deal intelligence.** DealRadar surfaces the best new and pre-owned vehicle deals in real time, combining live market data with four specialized AI agents to score, rank, and explain every listing.

---

## Overview

DealRadar is a full-stack web application that lets you describe the car you want in plain English and instantly see ranked, scored listings with AI-generated deal analysis, market pulse reports, and an automotive concierge that answers any car question.

```
"CPO Porsche Cayenne under $75k, no accidents"
→ 12 ranked listings, deal score per car, market pulse, full AI breakdown
```

---

## Features

- **Natural language search** — Describe what you want in any phrasing; the AI extracts make, model, year range, budget, and preferences.
- **Deal scoring** — Each listing receives a 0–100 deal score based on price vs. market value, mileage, accident history, CPO status, and regional demand.
- **AI deal analysis** — Detailed breakdown of why each car is or isn't a good deal, negotiation leverage, and what to watch out for.
- **Market Pulse** — Supply, pricing trend, and momentum analysis for any make/model, rendered as visual insight cards with a buyer/seller market verdict.
- **Automotive concierge** — Chat to ask general car questions (differences between trims, what to inspect at a test drive, etc.) grounded in your current search session.
- **Persistent search history** — Previous searches survive page refreshes via localStorage, with time-stamped session history in the sidebar.
- **Luxury typography** — Bodoni Moda, Cinzel, and Cormorant Garamond throughout for a premium feel.

---

## Architecture

```
car-deal-finder/
├── api/
│   ├── main.py          # FastAPI backend — REST endpoints, SQLite queries
│   └── ai_engine.py     # Four AI agents (Anthropic Claude)
├── frontend/
│   └── index.html       # Single-file SPA (~2400 lines, vanilla JS)
├── data_pipeline/
│   └── pipeline.py      # Auto.dev scraper → SQLite ingestion
│   └── .env             # ← NOT committed (API keys live here)
├── database/            # Schema and migration files
├── docs/                # Architecture notes
├── streamlit_app.py     # Streamlit demo wrapper
├── requirements.txt
├── Procfile             # For Railway / Heroku deployment
└── start.sh             # Local dev launcher
```

### AI Agents (ai_engine.py)

| Agent | Model | Role |
|---|---|---|
| Agent 1 — Intent Extractor | claude-haiku-4-5 | Parses natural language queries into structured search params |
| Agent 2 — Deal Analyst | claude-sonnet-4-6 | Scores listings 0–100 and writes deal summaries |
| Agent 3 — Market Pulse | claude-haiku-4-5 | Generates supply/pricing/momentum market report |
| Agent 4 — Concierge QA | claude-haiku-4-5 | Answers general automotive questions with optional listing context |

All agents use `tool_choice: {"type": "any"}` for reliable structured JSON output.

---

## Setup

### Prerequisites

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)
- An [Auto.dev API key](https://auto.dev/) (for live listing data)

### 1. Clone

```bash
git clone https://github.com/arunbodd/dealradar.git
cd dealradar
```

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure secrets

Create `data_pipeline/.env`:

```env
AUTO_DEV_API_KEY=your_auto_dev_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

> **Never commit this file.** It is excluded by `.gitignore`.

### 4. Run the backend

```bash
bash start.sh
# or manually:
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Open the app

Open `frontend/index.html` directly in your browser, or serve it:

```bash
cd frontend && python -m http.server 3000
# then visit http://localhost:3000
```

The frontend expects the API at `http://localhost:8000` by default. Update the `API` constant at the top of `index.html` if you deploy elsewhere.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Main chat endpoint — search or QA |
| `GET` | `/api/market-intel` | Market pulse for a make/model |
| `GET` | `/api/listings` | Fetch ranked listings from DB |
| `GET` | `/api/stats` | Aggregate stats for a search key |
| `POST` | `/api/analyze` | AI deal analysis for a single listing |

### Chat endpoint

```
POST /api/chat?query=CPO+BMW+X5+under+65k&context_make=BMW&context_model=X5
```

If the query contains a recognized make/model, it performs a search and returns scored listings. If not, it routes to the Concierge QA agent, optionally grounded in the current listing context.

---

## Deployment

### Railway / Heroku

The `Procfile` is set up for Railway:

```
web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

Set `ANTHROPIC_API_KEY` and `AUTO_DEV_API_KEY` as environment variables in your deployment dashboard — never in code.

### Streamlit demo

A lightweight Streamlit wrapper (`streamlit_app.py`) is included for sharing a demo where others supply their own API keys:

```bash
streamlit run streamlit_app.py
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key from console.anthropic.com |
| `AUTO_DEV_API_KEY` | Yes | Auto.dev marketplace API key |

---

## Tech Stack

- **Backend**: FastAPI, SQLite, Python 3.10+
- **AI**: Anthropic Claude (Haiku + Sonnet) via tool_use
- **Frontend**: Vanilla JS, single HTML file, no build step
- **Data**: Auto.dev API via `data_pipeline/pipeline.py`
- **Fonts**: Bodoni Moda, Cinzel, Cormorant Garamond, Inter (Google Fonts)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built with Claude · Anthropic*
