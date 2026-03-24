"""
DealRadar — AI Engine
=====================
Three agents powered by Claude API:

  Agent 1 · Intent Extractor  (claude-sonnet-4-6)
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
                "body_style":   {
                    "type": "string",
                    "enum": ["convertible","coupe","sedan","SUV","crossover","truck","hatchback","wagon",""],
                    "description": (
                        "Vehicle body style / roof type. "
                        "'convertible roof' / 'drop top' / 'ragtop' / 'cabriolet' / 'open top' → convertible. "
                        "A convertible can also be described as a coupe — if user says 'convertible coupe' or "
                        "'coupe with convertible roof', set body_style=convertible (not coupe). "
                        "Only set body_style=coupe when the user explicitly wants a fixed-roof coupe with NO convertible mention."
                    )
                },
                "no_accidents": {"type": "boolean", "description": "True if user wants accident-free vehicles only"},
                "one_owner":    {"type": "boolean", "description": "True if user wants single-owner vehicles"},
                "min_price":    {"type": "integer", "description": "Minimum listing price in USD. Set when user says 'over $X', 'at least $X', 'between $X and $Y'."},
                "year_from":    {"type": "integer", "description": "Minimum model year. Set when user says '2022 or newer', '2021+', 'recent model year', 'last N years'."},
                "zip_code":     {"type": "string",  "description": "5-digit US ZIP code if the user mentions one, e.g. 'near 30047', 'within 90210'. When zip_code is set, do NOT also set state."},
                "radius_miles": {"type": "integer", "description": "Search radius in miles around the ZIP code. If a ZIP is given but no radius mentioned, default to 100."},
                "brand_category": {
                    "type": "string",
                    "enum": ["european", "japanese", "american", "korean", "luxury", "electric", ""],
                    "description": (
                        "Set when the user refers to a REGIONAL or TYPE category rather than a specific brand. "
                        "'European cars' / 'European brands' / 'European models' → 'european'. "
                        "'Japanese cars' / 'JDM' → 'japanese'. "
                        "'American cars' / 'domestic' / 'American brands' → 'american'. "
                        "'Korean cars' / 'Korean brands' → 'korean'. "
                        "'Luxury cars' / 'premium cars' → 'luxury'. "
                        "'Electric cars' / 'EVs' / 'all-electric' → 'electric'. "
                        "Leave empty if the user named a specific brand."
                    )
                },
                "brand_was_specified": {
                    "type": "boolean",
                    "description": "True if the user explicitly named a car brand/make. False if you are recommending a brand based on feature description or brand category."
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
            model="claude-sonnet-4-6",
            max_tokens=1500,
            tools=tools,
            tool_choice={"type": "any"},
            system="""You are a car search concierge. Extract precise structured search parameters from natural language. Always call set_search_params.

PRICE PARSING:
- "under 50" / "under 50k" / "below $50,000" / "fifty thousand" / "50 grand" → max_price=50000
- "around $45k" / "approx 45k" / "roughly 45" → max_price=47000 (add ~5% buffer)
- "between 30k and 50k" / "$30-50k" / "30 to 50 thousand" → min_price=30000, max_price=50000
- "over $30k" / "at least $30,000" / "minimum 30k" → min_price=30000
- Numbers without $ or k under 500 are assumed thousands: "under 45" → max_price=45000
- "cheapest" / "most affordable" / "on a budget" → condition=used (no price filter unless stated)

YEAR PARSING:
- "2022 or newer" / "2022+" / "2022 and up" → year_from=2022
- "recent" / "last 2 years" / "last 3 years" (current year is 2026) → year_from=2024 or 2023
- "2022 model" / "a 2022" → year=2022 (exact); omit year_from

LOCATION — convert city/region mentions to 2-letter state codes:
  New York/NYC/Manhattan/Brooklyn/Queens/Long Island/NJ → NY
  Los Angeles/LA/SoCal/Hollywood/Burbank/Pasadena/Orange County/Long Beach → CA
  San Francisco/SF/Bay Area/Silicon Valley/San Jose/Oakland/Palo Alto → CA
  San Diego/Riverside/Inland Empire/Anaheim → CA | Sacramento/Fresno/Bakersfield → CA
  Chicago/Chicagoland/Naperville → IL | Houston/greater Houston/Katy/Sugar Land → TX
  Dallas/DFW/Fort Worth/Irving/Plano/Frisco/Austin/San Antonio/El Paso → TX
  Phoenix/Scottsdale/Tempe/Mesa/Chandler/Gilbert or Tucson → AZ
  Philadelphia/Philly/South Jersey → PA | Pittsburgh/Harrisburg → PA
  Miami/Fort Lauderdale/Boca Raton/West Palm/Orlando/Tampa/Jacksonville/South Florida → FL
  Atlanta/ATL/Georgia/Southeast → GA | Charlotte/Raleigh/Durham/Research Triangle/Greensboro → NC
  Denver/Boulder/Colorado Springs/Colorado → CO | Seattle/Tacoma/Bellevue/Pacific Northwest → WA
  Nashville/Memphis/Knoxville/Tennessee → TN | Las Vegas/Henderson/Reno → NV
  Boston/Cambridge/Worcester/New England → MA | Portland/Eugene/Oregon → OR
  Detroit/Motor City/Ann Arbor/Grand Rapids/Michigan → MI
  Minneapolis/Twin Cities/Saint Paul → MN | New Orleans/Baton Rouge/Louisiana → LA
  Baltimore/Annapolis/Maryland → MD | Louisville/Lexington/Kentucky → KY
  Salt Lake City/SLC/Utah → UT | Virginia Beach/Richmond/Northern Virginia/DC suburbs → VA
  Kansas City/St. Louis/Springfield/Missouri → MO | Milwaukee/Madison/Wisconsin → WI
  Columbus/Cleveland/Cincinnati/Ohio → OH | Indianapolis/Indiana → IN
  Albuquerque/Santa Fe/New Mexico → NM
  When zip_code is set: do NOT also set state. zip_code takes full priority.

MILEAGE:
  "low mileage/miles" → max_mileage=30000 | "very low" / "barely driven" → max_mileage=15000
  "under Xk miles" / "less than X miles" → max_mileage=X (×1000 if k suffix)

CONDITION:
  "certified" / "CPO" / "certified pre-owned" → cpo | "new" / "brand new" → new
  "used" / "pre-owned" / "second-hand" → used | "affordable" / "budget" → used

FEATURE → CAR matching (when no brand named, set brand_was_specified=false):
  3-ROW SUVs by audio: Bose 12sp/Harman → Hyundai Palisade; B&W/Bowers&Wilkins → Volvo XC90;
    Bang&Olufsen → Audi Q7; Revel 28sp → Lincoln Aviator; ELS Studio 16sp → Acura MDX;
    Bose 17sp → Infiniti QX60; AKG → Cadillac XT6; Meridian → Land Rover Discovery
  LUXURY 2-ROW SUVs: B&W → BMW X5; Burmester → Mercedes GLE or GLC
  FAMILY SUV under $55k: Toyota Highlander or Kia Telluride
  FULL-SIZE TRUCK: Ford F-150 (default); Ram 1500 or Chevy Silverado as alts
  MIDSIZE TRUCK: Toyota Tacoma or Ford Ranger
  SPORTY/PERFORMANCE SEDAN: BMW 3 Series or Audi A4
  MUSCLE CAR / PONY CAR: Ford Mustang or Dodge Challenger
  RELIABLE FAMILY SEDAN: Toyota Camry or Honda Accord
  RELIABLE COMPACT: Toyota Corolla or Honda Civic
  MINIVAN: Honda Odyssey or Toyota Sienna
  BEST HYBRID SUV: Toyota RAV4 Hybrid or Ford Escape Hybrid
  HYBRID SEDAN: Toyota Camry Hybrid or Honda Accord Hybrid
  EV under $45k: Tesla Model 3 or Chevrolet Equinox EV
  LUXURY EV: Tesla Model S or BMW i4
  OFF-ROAD / OVERLANDING: Jeep Wrangler or Toyota 4Runner
  SPORTS CAR under $40k: Toyota GR86 or Mazda MX-5 Miata
  BUDGET FIRST CAR under $20k: Toyota Corolla (used) or Honda Civic (used)
  red brake calipers standard → BMW M-Sport or Audi S-line

MODEL NAME NORMALIZATION (use canonical API form):
  Lexus TX/TX350/TX500/TX550h → "TX" | RX350 → "RX 350" | RX450h → "RX 450h" | RX500h → "RX 500h"
  NX350 → "NX 350" | NX450h → "NX 450h+" | ES350 → "ES 350" | GX460 → "GX 460" | GX550 → "GX 550"
  Mercedes C300 → "C 300" | GLE350 → "GLE 350" | GLE450 → "GLE 450" | E350 → "E 350" | E450 → "E 450"
  GLS450 → "GLS 450" | S500 → "S 500" | S580 → "S 580" | AMG GT → "AMG GT"
  BMW X5M → "X5 M" | M3 Competition → "M3 Competition" | M5 Comp → "M5 Competition"
  IONIQ5/Ioniq 5 → "IONIQ 5" | IONIQ6 → "IONIQ 6" | Ioniq9 → "IONIQ 9"
  Ford F150/F 150 → "F-150" | Bronco Sport ≠ Bronco (different models)
  Toyota RAV4 Hybrid → "RAV4 Hybrid" | RAV4 Prime → "RAV4 Prime"
  Genesis GV80/GV70/G80/G90 → use as-is
  Porsche Cayenne/Macan/911/Panamera → use as-is

BODY STYLE:
  "convertible"/"drop top"/"ragtop"/"cabriolet"/"open top" → convertible
  "coupe with convertible roof"/"convertible coupe" → convertible (NOT coupe)
  "hardtop coupe"/"2-door"/"fixed roof coupe" → coupe
  "sedan"/"4-door" → sedan | "SUV" → SUV | "crossover" → crossover
  "truck"/"pickup" → truck | "van"/"minivan" → wagon

FUEL TYPE:
  "EV"/"electric"/"battery"/"BEV" → electric | "hybrid"/"HEV" → hybrid
  "plug-in"/"PHEV"/"plug in hybrid" → phev | "gas"/"gasoline"/"petrol"/"ICE" → gas | "diesel" → diesel

BRAND CATEGORIES (set when user says a group, not a specific brand):
  "European cars/brands" → brand_category=european, brand_was_specified=false
    (BMW, Mercedes-Benz, Audi, Volkswagen, Volvo, Porsche, Land Rover, MINI, Alfa Romeo, Jaguar)
  "Japanese cars/JDM" → brand_category=japanese
    (Toyota, Honda, Lexus, Mazda, Subaru, Nissan, Infiniti, Acura, Mitsubishi)
  "American cars/domestic" → brand_category=american
    (Ford, Chevrolet, Cadillac, Jeep, Dodge, Ram, Lincoln, Buick, GMC)
  "Korean cars" → brand_category=korean (Hyundai, Kia, Genesis)
  "Luxury cars/premium" → brand_category=luxury
    (BMW, Mercedes-Benz, Audi, Lexus, Cadillac, Lincoln, Porsche, Volvo, Genesis, Infiniti, Acura, Land Rover)
  "Electric cars/EVs/BEVs" → brand_category=electric
  For category queries: set max_price from budget if mentioned; pick BEST representative make+model;
  populate suggested_alternatives with ALL notable brands in the category.

ACCIDENTS / HISTORY:
  "clean history"/"accident-free"/"no accidents"/"clean Carfax" → no_accidents=true
  "single owner"/"one owner"/"1 owner" → one_owner=true

VEHICLE HISTORY:
  'clean history' / 'accident-free' / 'no accidents' / 'clean Carfax' → no_accidents=true
  'single owner' / 'one owner' / '1 owner' → one_owner=true

IMPORTANT: Always pick ONE best make+model as primary. Set brand_was_specified=false when recommending.
List alternatives in suggested_alternatives (4-6 items) when brand_was_specified=false.""",
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

    title_brand = (listing.get("title_brand") or "Clean").strip()
    _branded_titles = ("salvage", "rebuilt", "lemon", "flood", "junk", "insurance loss")
    is_branded_title = title_brand.lower() in _branded_titles

    title_brand_line = f"• ⚠️  TITLE BRAND: {title_brand.upper()} — BRANDED/PROBLEM TITLE DETECTED" if is_branded_title else f"• Title: {title_brand}"
    dealer_reported_accidents = listing.get('accidents', 0)

    context = f"""LISTING DETAILS:
• {listing.get('year')} {listing.get('make')} {listing.get('model')} {listing.get('trim','')}
• Listed: ${price:,}  |  MSRP: ${listing.get('base_msrp',0) or 0:,}
• Discount: {listing.get('discount_pct',0)}% off MSRP  (${listing.get('discount_amount',0) or 0:,.0f} savings)
• Mileage: {listing.get('mileage',0):,} miles
• Drivetrain: {listing.get('drivetrain','?')}  |  Color: {listing.get('exterior_color','?')}
• Condition: {'CPO Certified' if listing.get('is_cpo') else 'Used' if listing.get('is_used') else 'Brand New'}
• Dealer-reported accidents: {dealer_reported_accidents}  |  One Owner: {'Yes' if listing.get('one_owner') else 'Unknown'}
{title_brand_line}
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

    # Build title brand warning injection
    if is_branded_title:
        title_warning_block = f"""
⚠️  CRITICAL TITLE BRAND ALERT:
This vehicle has a {title_brand.upper()} title. This is a MAJOR red flag that MUST dominate your analysis.
- A {title_brand} title means the vehicle was declared a total loss by an insurance company, severely damaged, or had a major legal/safety event.
- Even if the dealer reports "no accidents", a branded title IS in direct contradiction with a clean history claim. This is a serious CARFAX discrepancy risk.
- DealRadar's data comes from dealer-reported fields which may NOT match CARFAX or the actual title history.
- Branded title vehicles typically sell for 20-40% below market value for a reason — they carry permanent stigma, higher insurance rates, difficult resale, and potential hidden structural or safety issues.
- Your recommendation MUST be "Pass" or at minimum "Negotiate" with very aggressive price targets (30%+ below market).
- In red_flags, explicitly state: "CARFAX DISCLAIMER: Dealer reports 0 accidents but title brand is {title_brand} — always pull an independent CARFAX/AutoCheck report before purchasing."
"""
    else:
        title_warning_block = "\n⚠️  CARFAX DISCLAIMER: Dealer-reported accident counts may not match CARFAX or AutoCheck independent reports. Always verify with a paid vehicle history report before purchasing.\n"

    try:
        resp = c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            tools=tools,
            tool_choice={"type": "any"},
            system=f"""You are an expert car buying advisor — think Consumer Reports meets a sharp negotiator.
You know dealer pricing tactics, manufacturer incentives, and how to read market data.
Be honest, specific about numbers, and genuinely helpful to the buyer.
Never be vague. If the deal is bad, say so. If it's great, say so with reasons.

IMPORTANT — TITLE BRAND AWARENESS:
- Dealer-reported accident counts are self-reported and may NOT reflect actual vehicle history.
- If the listing has a SALVAGE, REBUILT, LEMON, or FLOOD title, this MUST be treated as the most important risk factor — more important than price.
- Always remind buyers to check CARFAX or AutoCheck independently, since listings may show "no accidents" while the title tells a different story.{title_warning_block}""",
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
