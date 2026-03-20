# DealRadar — Demo Script
*Talking points for a 5–10 minute walkthrough*

---

## OPENING HOOK  *(~30 seconds)*

> "How many of you have spent hours on CarGurus or AutoTrader, copy-pasting prices into a spreadsheet trying to figure out if a car is actually a good deal — or just looks like one?"

> "The problem isn't that information doesn't exist. It's that interpreting it requires expertise most people don't have. You need to know what a fair discount is, what a clean history looks like, whether the mileage is reasonable for the price — and do all of that across 20+ listings at once."

> "DealRadar does all of that. It's an AI-powered car deal analyst. You just tell it what you want, in plain English, and it tells you what to buy, what to negotiate, and what to walk away from."

---

## THE DEMO  *(~4 minutes)*

### Step 1 — Natural Language Search

**[Type into the search bar:]**
> *"I want a clean title Toyota Camry under $28,000 with no accidents, used"*

**Say:**
> "Notice I didn't fill out any dropdown menus. I just said what I want, the way I'd say it to a friend who knows cars."

> "Behind the scenes, Agent 1 — running on Claude Haiku — reads that sentence and extracts structured parameters: make Toyota, model Camry, max price $28,000, condition used, no accidents true. It passes those as exact filters to our SQLite database. Zero guessing."

**[Point to the Tool Calls panel if visible]**
> "You can actually see the AI's work right here — what it understood, and how many listings matched."

---

### Step 2 — The Results

**[Results load on screen]**

**Say:**
> "These results are sorted by Deal Score — a number we calculate from three things: how much it's discounted off MSRP, vehicle history, and whether it's certified. That score is deterministic — it's not the AI guessing, it's a formula applied consistently to every single listing."

> "The AI is reserved for interpretation — not data. The data comes from auto.dev's inventory API, cached locally so we're not burning API calls on every search."

---

### Step 3 — AI Deal Analysis  *(the money moment)*

**[Click "Analyze Deal" on one listing]**

**Say:**
> "This is where Agent 2 comes in — running on Claude Sonnet, the more powerful model. It gets the listing details, the market average for this exact make/model, and five comparable listings from our database."

> "It comes back with: a recommendation — Strong Buy, Buy, Negotiate, or Pass — a headline, specific negotiation tactics, green flags, red flags, and a bottom line."

**[Read the recommendation aloud]**

> "This isn't generic advice. It's grounded in the actual numbers for this listing. If the car is $2,000 above market average, the AI says that. If there's a one-owner history and the price is still aggressive, it says that too."

---

### Step 4 — Market Pulse

**[Click Market Pulse / Intel tab]**

**Say:**
> "Agent 3 reads the live inventory stats — total listings, price range, price drops, new arrivals — and writes four structured insights: supply, pricing, momentum, and verdict."

> "It also gives a market condition score: Strong Buyer, Buyer, Neutral, Seller, or Strong Seller. Right now in this market, you can see it says..."

**[Read the verdict aloud]**

---

### Step 5 — Concierge QA  *(optional, if time allows)*

**[Type in the search bar:]**
> *"Which of these has the best resale value?"*

**Say:**
> "When the AI can't extract a specific make and model — like this question — it switches to concierge mode. Agent 4 sees the six listings currently on your screen and answers in context. It's not a generic web search. It knows exactly what you're looking at."

---

## THE AI ARCHITECTURE  *(~2 minutes, for technical audiences)*

> "Let me quickly explain what kind of AI system this actually is, because people often assume it must be RAG — Retrieval-Augmented Generation."

> "It's not. RAG is for searching unstructured text — like your company's PDF documentation. We have structured data, so we use a different pattern."

> "This is a **Multi-Agent Tool-Use Pipeline.** Four specialized agents, each with a specific job:"

| Agent | Model | Job |
|---|---|---|
| Intent Extractor | Claude Haiku | Natural language → SQL filters |
| Deal Analyst | Claude Sonnet | Listing + market data → Buy/Negotiate/Pass |
| Market Pulse | Claude Haiku | Stats → narrative market insights |
| Concierge QA | Claude Haiku | General car questions with session context |

> "Every agent uses Claude's **structured tool use** — which means the output is always typed JSON, not free text. The AI fills out a schema, not a paragraph. That's what makes it reliable enough to drive real database queries and real UI components."

> "The orchestrator in FastAPI decides which agent to call and in what order. Agent 1 always runs first. If it can't extract a make and model, the orchestrator routes to Agent 4 instead."

> "The models are intentionally tiered — Haiku for speed and volume, Sonnet only for the deep analysis that justifies the extra cost and latency."

---

## KEY DIFFERENTIATORS  *(~1 minute)*

- **No hallucinated prices.** Every number comes from the database. The AI reasons over real data, not training memory.
- **Smart caching.** First search costs 3–5 API calls. Every repeat search for the same make/model costs zero.
- **Stale detection.** Cars that disappear from the market get flagged as removed after two consecutive misses — so you're never looking at sold inventory.
- **Title brand alerts.** Salvage, rebuilt, lemon, and flood titles trigger a 0.45x score penalty and force a "Pass" recommendation from the AI.
- **Model routing.** Fast tasks use Haiku. Complex analysis uses Sonnet. Cost and latency are optimized per use case.

---

## CLOSING LINE

> "The goal wasn't to build a chatbot that talks about cars. It was to build a system where AI does the expert interpretation, the database does the filtering, and you get a clear answer: buy it, negotiate it, or walk away."

---

## QUICK ANSWERS FOR Q&A

**"How is this different from CarGurus?"**
> CarGurus shows you a price rating (good, fair, high). DealRadar tells you *why*, gives you negotiation tactics specific to that listing, and shows you the market context behind the score.

**"What if I want a car that's not in the database yet?"**
> First search triggers a live fetch from auto.dev — up to 500 listings. After that, results are cached for 24 hours. You can force a refresh anytime.

**"Could this work for other verticals?"**
> The architecture — intent extraction → structured DB query → AI analysis → market pulse — applies to anything with inventory data. Real estate, used electronics, B2B equipment. The agents are the pattern, not just the car domain.

**"How much does the AI cost to run?"**
> A typical search costs ~$0.002 in Anthropic API credits. An AI deal analysis costs ~$0.015. The caching system means most sessions cost almost nothing in API calls after the first load.

**"Is this production-ready?"**
> It's deployed on Render, backed by SQLite with WAL mode for concurrent reads, and has a health endpoint that surfaces all env var and provider status. It's demo-ready and close to production-ready — next step would be swapping SQLite for PostgreSQL for multi-user scale.
