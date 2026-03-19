# AI Car Deal Finder — Project Plan

## Vision
Search all US car dealers and surface the best deal for any brand/model.
Example: "Find me a BMW X5" → returns the #1 deal nationwide with discount amount,
financing options, incentives, vehicle availability, and VIN.

---

## Architecture

```
User Query (NL)
      │
      ▼
 Frontend (React/Next.js)
 - Search bar, filters, map view
      │
      ▼
 Backend API (FastAPI / Python)
 - NLU query parsing
 - Deal aggregation & ranking
 - Redis caching
      │
      ▼
 Database Layer
 - PostgreSQL (listings, dealers, incentives, scores)
 - Vector DB / Pinecone (semantic search)
      │
      ▼
 Data Ingestion Layer
 - MarketCheck API (primary — daily inventory)
 - Edmunds API (specs, ratings)
 - ETL pipeline (pipeline.py) — runs hourly/daily
```

---

## Best Deal Score Formula

```
Score = (Discount% × 0.40)
      + (Financing Quality × 0.30)
      + (Distance Factor × 0.20)
      + (Availability × 0.10)
```

- **Discount%**: (MSRP − Price) / MSRP × 100, capped at 20% = score of 1.0
- **Financing Quality**: based on lowest available APR (0% = 1.0, 9%+ = 0.0)
- **Distance Factor**: geodesic distance from user to dealer (0–1, user-supplied location)
- **Availability**: penalizes cars sitting >60 days on lot

---

## Data Sources

| Source | What it provides | Cost |
|--------|-----------------|------|
| [MarketCheck](https://www.marketcheck.com/apis/) | Live dealer inventory, daily updates, pricing, VIN, location | Paid API |
| [Edmunds](https://developer.edmunds.com/) | Vehicle specs, ratings, photos, content | Free tier available |
| [Auto.dev](https://www.auto.dev/listings) | US dealer listings, VIN decode | Paid API |

**Recommended starting point:** MarketCheck API — register at https://www.marketcheck.com/apis/

---

## Phased Execution Plan

### ✅ Phase 1 — Data Foundation (Weeks 1–2) ← YOU ARE HERE
- [x] Design database schema (`database/schema.sql`)
- [x] Build ETL pipeline (`data_pipeline/pipeline.py`)
- [ ] Get MarketCheck API key
- [ ] Stand up PostgreSQL locally (Docker recommended)
- [ ] Run pipeline in dry-run mode: `python pipeline.py --make BMW --model X5 --dry-run`
- [ ] Verify data quality, check scores

### Phase 2 — Core Backend API (Weeks 3–4)
- [ ] Build FastAPI app with `/search` endpoint
- [ ] Add NLU query parsing (extract make/model/budget/state from free text)
- [ ] Connect PostgreSQL + Redis caching
- [ ] Implement deal ranking and response pagination

### Phase 3 — AI & Personalization (Weeks 5–6)
- [ ] Integrate OpenAI or Anthropic API for natural language query understanding
- [ ] Add vector search via Pinecone for "similar deals" recommendations
- [ ] Incorporate manufacturer incentives data

### Phase 4 — Frontend (Weeks 7–8)
- [ ] Build React/Next.js search UI
- [ ] Deal cards: price, discount badge, financing options, VIN, dealer map pin
- [ ] Side-by-side comparison view
- [ ] "Best nationwide" vs "Best near me" toggle

### Phase 5 — Production Hardening (Ongoing)
- [ ] Start with top 10 metro areas, expand nationwide
- [ ] Add "data as of [timestamp]" freshness indicators
- [ ] Legal review for any scraping components
- [ ] Monitor for outdated/stale listings and flag them

---

## Immediate Next Steps

1. **Get API key** → Sign up at [MarketCheck](https://www.marketcheck.com/apis/)
2. **Install dependencies** → `pip install requests psycopg2-binary python-dotenv`
3. **Set up `.env`** → Copy `.env.example` to `.env`, fill in your API key
4. **Run dry-run test** → `python data_pipeline/pipeline.py --make BMW --model X5 --dry-run`
5. **Review `sample_output.json`** to validate the data structure

---

## Critical Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Data freshness | MarketCheck updates daily; add "as of" timestamps in UI |
| Data accuracy | Cross-verify with 2+ sources; flag listings not seen in 48h |
| Legal / TOS | Use official APIs only; get legal review before any scraping |
| Scalability | Start with 10 metro areas; add state-by-state gradually |
| Cost | MarketCheck is paid — evaluate pricing tiers against usage |
