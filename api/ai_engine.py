"""
DealRadar — AI Engine
=====================
Three agents powered by Claude API:

  Agent 1 · Intent Extractor  (claude-haiku-4-5)
    Natural language → structured search params
    "I want a clean white AWD BMW X5 under $50k in Florida" →
    {make:"BMW", model:"X5", color:"White", drivetrain:"AWD",
     max_price:50000, state:"FL", no_accidents:True}

  Agent 2 · Deal Analyst  (claude-sonnet-4-6)
    Listing + market data → buy/negotiate/pass recommendation
    Returns: headline, price assessment, negotiation tips,
             green flags, red flags, bottom line

  Agent 3 · Market Pulse  (claude-haiku-4-5)
    Inventory stats → 3-4 bullet narrative insights
    "Prices down 4% this month · Best deals in TX and FL · AWD rare in Southeast"

All agents operate ONLY on the local SQLite cache — zero extra auto.dev API calls.
Requires: ANTHROPIC_API_KEY in data_pipeline/.env
"""

import os, json, logging
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load .env file when running locally — silently ignored if file doesn't exist (e.g. on Railway)
load_dotenv(Path(__file__).parent.parent / "data_pipeline" / ".env", override=False)
log = logging.getLogger(__name__)

_client = None

def _get_client():
    global _client
    if _client is None:
        try:
            from anthropic import Anthropic
            # Strip whitespace — Railway/cloud platforms can inject invisible chars
            key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
            if not key:
                raise ValueError(
                    "ANTHROPIC_API_KEY is not set. "
                    "On Railway: add it under your service → Variables. "
                    "Locally: add it to data_pipeline/.env"
                )
            _client = Anthropic(api_key=key)
        except ImportError:
            raise RuntimeError("anthropic package not installed — run: pip install anthropic")
    return _client


# ═══════════════════════════════════════════════════════════════
# AGENT 1 — Intent Extractor
# ═══════════════════════════════════════════════════════════════

def extract_search_intent(query: str) -> dict:
    """
    Parse a natural language car search query into structured filter params.
    Uses tool_use so output is always a clean JSON object.

    Returns dict with keys: make, model, year, state, max_price, condition,
    drivetrain, max_mileage, color, no_accidents, one_owner, summary,
    suggested_alternatives (list of {make, model} when no brand was specified)
    """
    c = _get_client()

    tools = [{
        "name": "set_search_params",
        "description": "Set structured search parameters extracted from the user's natural language car search query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "make":         {"type": "string",  "description": "Car manufacturer, e.g. BMW, Toyota, Ford. Capitalize properly. If user didn't name a brand, pick the BEST single match for their description."},
                "model":        {"type": "string",  "description": "Car model, e.g. X5, Camry, F-150. Use standard naming. If user didn't name a model, pick the BEST single match."},
                "year":         {"type": "integer", "description": "Model year if specified"},
                "state":        {"type": "string",  "description": "2-letter US state code if a state or region is mentioned (e.g. FL for Florida, GA for Georgia/Atlanta, TX for Texas)"},
                "max_price":    {"type": "integer", "description": "Maximum listing price in USD"},
                "condition":    {"type": "string",  "enum": ["new","used","cpo",""], "description": "Vehicle condition"},
                "drivetrain":   {"type": "string",  "enum": ["AWD","RWD","FWD",""],  "description": "Drivetrain preference"},
                "max_mileage":  {"type": "integer", "description": "Maximum odometer miles. 'Low miles' → 30000, 'Very low' → 15000"},
                "color":        {"type": "string",  "description": "Exterior color preference"},
                "no_accidents": {"type": "boolean", "description": "True if user wants accident-free vehicles only"},
                "one_owner":    {"type": "boolean", "description": "True if user wants single-owner vehicles"},
                "brand_was_specified": {
                    "type": "boolean",
                    "description": "True if the user explicitly named a car brand/make. False if you are recommending a brand based on feature description."
                },
                "suggested_alternatives": {
                    "type": "array",
                    "description": "When brand_was_specified is false, list 4-6 other strong matches for the user's description, in order of fit. Omit if user named a specific brand.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "make":   {"type": "string", "description": "Car manufacturer"},
                            "model":  {"type": "string", "description": "Car model"},
                            "reason": {"type": "string", "description": "One short phrase why this matches (e.g. 'B&W 19 speakers, panoramic roof')"}
                        },
                        "required": ["make", "model", "reason"]
                    }
                },
                "summary": {"type": "string", "description": "One sentence confirming what you understood. If you chose a brand for the user, say which one you picked and why."},
            },
            "required": ["make", "model", "brand_was_specified", "summary"]
        }
    }]

    try:
        resp = c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            tools=tools,
            tool_choice={"type": "any"},
            system="""You are a car search expert and concierge. Extract structured search parameters from natural language.

FEATURE MATCHING RULES — when user describes features without naming a brand:
- 3-row SUV + Bose 12 speakers → Hyundai Palisade (best overall value) or Kia Telluride
- 3-row SUV + Bowers & Wilkins → Volvo XC90
- 3-row SUV + Bang & Olufsen → Audi Q7
- 3-row SUV + Revel audio / 28 speakers → Lincoln Aviator
- 3-row SUV + ELS Studio / 16 speakers → Acura MDX
- 3-row SUV + Bose 17 speakers → Infiniti QX60
- 3-row SUV + AKG audio → Cadillac XT6
- 3-row SUV + Meridian → Land Rover Discovery
- luxury SUV (no 3rd row) + B&W → BMW X5
- luxury SUV (no 3rd row) + Burmester → Mercedes GLE
- family SUV under $55k → Toyota Highlander or Kia Telluride
- sporty sedan → BMW 3 Series or Audi A4
- red brake calipers standard → BMW M-Sport, Audi S-line, Volvo R-Design
- 'clean history' / 'accident-free' / 'no accidents' → no_accidents=true
- 'low miles' → max_mileage=30000; 'very low miles' → 15000
- Atlanta / GA → state=GA; Southeast → state=GA; Texas → state=TX
- 'affordable' / 'budget' → used/cpo condition
- 'certified' / 'CPO' → condition=cpo

IMPORTANT: Always pick ONE best make+model as the primary. Then list alternatives in suggested_alternatives.
Set brand_was_specified=false when recommending based on features.""",
            messages=[{"role": "user", "content": f"Parse this car search query: {query}"}]
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
    except Exception as e:
        log.error(f"Intent extraction failed: {e}")
    return {}


# ═══════════════════════════════════════════════════════════════
# AGENT 2 — Deal Analyst
# ═══════════════════════════════════════════════════════════════

def analyze_deal(listing: dict, market_stats: dict, similar_listings: list) -> dict:
    """
    Deep analysis of a specific listing vs the current market.

    Returns structured dict:
      recommendation  : "Strong Buy" | "Buy" | "Negotiate" | "Wait" | "Pass"
      headline        : one punchy sentence
      price_assessment: 2-3 sentences on pricing vs market
      negotiation_tips: list of specific tactics
      green_flags     : list of positives
      red_flags       : list of concerns
      bottom_line     : final actionable advice
    """
    c = _get_client()

    avg = market_stats.get("avg_price", 0) or 0
    price = listing.get("listing_price", 0) or 0
    diff  = price - avg
    vs_market = f"${abs(int(diff)):,} {'above' if diff > 0 else 'below'} the ${avg:,} market average" if avg else "market avg unknown"

    context = f"""LISTING DETAILS:
• {listing.get('year')} {listing.get('make')} {listing.get('model')} {listing.get('trim','')}
• Listed: ${price:,}  |  MSRP: ${listing.get('base_msrp',0) or 0:,}
• Discount: {listing.get('discount_pct',0)}% off MSRP  (${listing.get('discount_amount',0) or 0:,.0f} savings)
• Mileage: {listing.get('mileage',0):,} miles
• Drivetrain: {listing.get('drivetrain','?')}  |  Color: {listing.get('exterior_color','?')}
• Condition: {'CPO Certified' if listing.get('is_cpo') else 'Used' if listing.get('is_used') else 'Brand New'}
• Accidents: {listing.get('accidents',0)} reported  |  One Owner: {'Yes' if listing.get('one_owner') else 'Unknown'}
• Dealer: {listing.get('dealer_name','?')} — {listing.get('dealer_city','?')}, {listing.get('dealer_state','?')}

MARKET CONTEXT ({market_stats.get('total',0)} active listings):
• This listing is {vs_market}
• Market range: ${market_stats.get('min_price',0) or 0:,} – ${market_stats.get('max_price',0) or 0:,}
• Average discount off MSRP in market: {market_stats.get('avg_discount',0):.1f}%
• This listing's discount: {listing.get('discount_pct',0):.1f}%

COMPARABLE LISTINGS (similar mileage, same model):
""" + "\n".join([
        f"• ${s.get('listing_price',0):,} | {s.get('mileage',0):,} mi | {s.get('exterior_color','?')} | {s.get('dealer_city','?')}, {s.get('dealer_state','?')}"
        for s in similar_listings[:5]
    ])

    tools = [{
        "name": "deal_analysis",
        "description": "Return a structured deal analysis",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommendation": {
                    "type": "string",
                    "enum": ["Strong Buy", "Buy", "Negotiate", "Wait", "Pass"],
                    "description": "Overall recommendation"
                },
                "headline": {
                    "type": "string",
                    "description": "One punchy sentence summarizing the deal quality"
                },
                "price_assessment": {
                    "type": "string",
                    "description": "2-3 sentences comparing price to market and explaining whether it's fair"
                },
                "negotiation_tips": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-3 specific, actionable negotiation tactics for this exact deal"
                },
                "green_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Positive aspects of this listing"
                },
                "red_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Concerns or risks a buyer should know"
                },
                "bottom_line": {
                    "type": "string",
                    "description": "Final 1-2 sentence actionable advice"
                }
            },
            "required": ["recommendation","headline","price_assessment","negotiation_tips","green_flags","red_flags","bottom_line"]
        }
    }]

    try:
        resp = c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            tools=tools,
            tool_choice={"type": "any"},
            system="""You are an expert car buying advisor — think Consumer Reports meets a sharp negotiator.
You know dealer pricing tactics, manufacturer incentives, and how to read market data.
Be honest, specific about numbers, and genuinely helpful to the buyer.
Never be vague. If the deal is bad, say so. If it's great, say so with reasons.""",
            messages=[{"role": "user", "content": f"Analyze this car deal and tell me whether I should buy it:\n\n{context}"}]
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
    except Exception as e:
        log.error(f"Deal analysis failed: {e}")
        return {
            "recommendation": "Unknown",
            "headline": "Analysis unavailable",
            "price_assessment": str(e),
            "negotiation_tips": [],
            "green_flags": [],
            "red_flags": [],
            "bottom_line": "Check your ANTHROPIC_API_KEY configuration."
        }


# ═══════════════════════════════════════════════════════════════
# AGENT 3 — Market Pulse
# ═══════════════════════════════════════════════════════════════

def generate_market_pulse(stats: dict, make: str, model: str, year: Optional[int] = None) -> dict:
    """
    Generate structured market intelligence cards from inventory stats.
    Returns a dict with labeled insight fields. Fast — uses Haiku.
    """
    c = _get_client()
    label = f"{year} {make} {model}" if year else f"{make} {model}"

    top_states = stats.get("top_states", [])
    top_states_str = ", ".join([f"{s} ({n})" for s, n in top_states[:4]]) if top_states else "nationwide"

    tools = [{
        "name": "market_pulse",
        "description": "Structured market intelligence for a car buyer",
        "input_schema": {
            "type": "object",
            "properties": {
                "supply": {
                    "type": "string",
                    "description": "1-2 sentence insight on inventory availability. Include total count and top states. E.g. '320 listings active — heavy concentration in TX (45) and CA (38). Inventory is strong, giving buyers negotiating leverage.'"
                },
                "pricing": {
                    "type": "string",
                    "description": "1-2 sentence insight on current pricing. Include avg price, MSRP discount range, and what's realistic to pay. E.g. 'Average ask is $31,316 with discounts up to 7.7% off MSRP. Target $29-31k for a solid deal.'"
                },
                "momentum": {
                    "type": "string",
                    "description": "1-2 sentence insight on market momentum — new listings and price drops. E.g. '10 new listings added this week with 8 price drops detected — sellers are actively adjusting. Market favors buyers.'"
                },
                "verdict": {
                    "type": "string",
                    "description": "1-2 sentence bottom-line buying advice. Should answer: is now a good time to buy? E.g. 'Good time to buy. Strong inventory and active price drops signal a buyer's market — push for at least 5% off asking.'"
                },
                "market_score": {
                    "type": "string",
                    "enum": ["Strong Buyer", "Buyer", "Neutral", "Seller", "Strong Seller"],
                    "description": "Overall market condition from buyer's perspective"
                }
            },
            "required": ["supply", "pricing", "momentum", "verdict", "market_score"]
        }
    }]

    try:
        resp = c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            tools=tools,
            tool_choice={"type": "any"},
            system="You are a concise, numbers-focused automotive market analyst. Be specific, buyer-focused, and direct. No fluff.",
            messages=[{"role": "user", "content": f"""Analyze this market data for a buyer shopping {label}:

- {stats.get('total', 0)} active listings nationwide
- Price range: ${stats.get('min_price',0) or 0:,} – ${stats.get('max_price',0) or 0:,}
- Average price: ${stats.get('avg_price',0) or 0:,}
- Average discount off MSRP: {stats.get('avg_discount',0):.1f}%
- Best discount available: {stats.get('best_discount',0):.1f}%
- Most inventory in: {top_states_str}
- New listings in last 48h: {stats.get('new_count', 0)}
- Price drops detected: {stats.get('price_drop_count', 0)}

Give structured market intelligence this buyer needs right now."""}]
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        return {"supply": "", "pricing": "", "momentum": "", "verdict": "", "market_score": "Neutral"}
    except Exception as e:
        log.error(f"Market pulse failed: {e}")
        return f"⚠️ Market analysis unavailable: {e}"


# ═══════════════════════════════════════════════════════════════
# AGENT 4 — Concierge QA
# ═══════════════════════════════════════════════════════════════

def answer_car_question(query: str, listings_context: str = None) -> str:
    """
    Answer a general car-related question conversationally.
    Optionally grounded in the user's current listing session.

    Examples:
      "What is the difference between AWD and 4WD?"
      "Is the GV80 more reliable than the X5?"
      "Which of the cars currently listed has the best resale value?"
      "What should I look for when buying a CPO car?"
    """
    c = _get_client()

    system = """You are an expert automotive concierge for DealRadar, a car deal intelligence platform.

Answer car-related questions with expertise and precision. Your knowledge covers:
- Vehicle comparisons, reliability, ownership costs, and value retention
- Buying strategies: timing, new vs CPO vs used, negotiation tactics, financing
- Technical specifics: drivetrains, trim levels, engine types, safety features
- Market dynamics: depreciation curves, best value segments, seasonal pricing

Guidelines:
- Keep answers focused and practical, 2-5 sentences unless complexity demands more
- Use plain, direct language. No em dashes. No excessive hedging.
- When the user asks about their currently visible listings, reference those vehicles specifically
- If asked something unrelated to cars or automotive buying, politely redirect to car topics"""

    user_content = query
    if listings_context:
        user_content = f"Current listing session context:\n{listings_context}\n\nUser question: {query}"

    try:
        resp = c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": user_content}]
        )
        return resp.content[0].text
    except Exception as e:
        log.error(f"Concierge QA failed: {e}")
        return "I wasn't able to answer that right now. Try searching for a specific make and model to get listings."
