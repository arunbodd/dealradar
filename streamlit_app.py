"""
DealRadar — Streamlit Edition
==============================
Standalone deployment for Streamlit Cloud sharing.
Users provide their own API keys via the sidebar.

Deploy:
  streamlit run streamlit_app.py

Streamlit Cloud:
  1. Push this repo to GitHub
  2. Go to share.streamlit.io → New app → select repo → streamlit_app.py
  3. In App Settings > Secrets add:
       ANTHROPIC_API_KEY = "sk-ant-..."
       AUTO_DEV_API_KEY  = "your-key"
     (users can also paste keys in the sidebar)

Requirements (add to requirements.txt):
  streamlit>=1.32.0
  anthropic>=0.25.0
  requests>=2.31.0
"""

import os, json, time, sqlite3, hashlib, math
from pathlib import Path
from typing import Optional

import streamlit as st
import requests

# ─── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="DealRadar — AI Car Deal Finder",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────
st.markdown("""
<style>
  /* Dark theme tweaks */
  .stApp { background: #080c14; color: #f1f5f9; }
  .stSidebar { background: #0d1321; border-right: 1px solid rgba(255,255,255,0.07); }

  /* Card styles */
  .car-card {
    background: #0f1724; border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px; padding: 16px; margin-bottom: 12px;
    transition: all .2s;
  }
  .car-card:hover { border-color: rgba(59,130,246,0.3); }
  .card-title { font-size: 1.05rem; font-weight: 700; color: #f1f5f9; margin-bottom: 2px; }
  .card-price { font-size: 1.3rem; font-weight: 800; color: #f1f5f9; }
  .card-savings { font-size: 0.78rem; color: #10b981; font-weight: 600; }
  .card-msrp { font-size: 0.78rem; color: #64748b; text-decoration: line-through; }
  .badge {
    display: inline-block; font-size: 0.65rem; font-weight: 700;
    padding: 2px 8px; border-radius: 20px; margin-right: 4px; text-transform: uppercase;
  }
  .badge-new  { background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3); }
  .badge-used { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid rgba(100,116,139,0.3); }
  .badge-cpo  { background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }
  .badge-hot  { background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(239,68,68,0.3); }
  .badge-deal { background: rgba(245,158,11,0.15); color: #fbbf24; border: 1px solid rgba(245,158,11,0.3); }
  .meta-tag {
    display: inline-block; font-size: 0.75rem; color: #94a3b8;
    background: rgba(255,255,255,0.04); border-radius: 6px; padding: 2px 8px; margin: 2px;
  }
  .dealer-line { font-size: 0.78rem; color: #64748b; margin-top: 8px; }
  .dealer-name-link { color: #94a3b8; text-decoration: none; font-weight: 600; }
  .dealer-name-link:hover { color: #f59e0b; }
  .tool-call-box {
    background: rgba(16,185,129,0.05); border: 1px solid rgba(16,185,129,0.2);
    border-radius: 8px; padding: 10px 14px; margin: 6px 0;
    font-family: monospace; font-size: 0.75rem; color: #86efac;
  }
  .tool-header { color: #10b981; font-weight: 700; margin-bottom: 6px; font-size: 0.8rem; font-family: sans-serif; }
  .rec-badge {
    display: inline-block; font-size: 0.9rem; font-weight: 800;
    padding: 5px 16px; border-radius: 20px; margin-bottom: 10px;
  }
  .rec-strong-buy { background: rgba(16,185,129,0.2); color: #34d399; border: 2px solid #10b981; }
  .rec-buy        { background: rgba(59,130,246,0.2); color: #60a5fa; border: 2px solid #3b82f6; }
  .rec-negotiate  { background: rgba(245,158,11,0.2); color: #fbbf24; border: 2px solid #f59e0b; }
  .rec-wait       { background: rgba(139,92,246,0.2); color: #a78bfa; border: 2px solid #8b5cf6; }
  .rec-pass       { background: rgba(239,68,68,0.2);  color: #f87171; border: 2px solid #ef4444; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# SESSION STATE
# ════════════════════════════════════════════════════════════════
def init_state():
    defaults = {
        "messages": [],
        "listings": [],
        "last_intent": {},
        "anthropic_key": "",
        "autodev_key": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ════════════════════════════════════════════════════════════════
# SIDEBAR — API KEYS + FILTERS
# ════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🚗 DealRadar")
    st.markdown("*AI-powered car deal finder*")
    st.divider()

    st.markdown("### 🔑 API Keys")
    # Load from Streamlit secrets if available
    default_anthro = st.secrets.get("ANTHROPIC_API_KEY", "") if hasattr(st, "secrets") else ""
    default_autodev = st.secrets.get("AUTO_DEV_API_KEY", "") if hasattr(st, "secrets") else ""

    anthro_key = st.text_input(
        "Anthropic API Key",
        value=st.session_state.anthropic_key or default_anthro,
        type="password",
        placeholder="sk-ant-...",
        help="Get yours at console.anthropic.com",
    )
    autodev_key = st.text_input(
        "auto.dev API Key",
        value=st.session_state.autodev_key or default_autodev,
        type="password",
        placeholder="Your auto.dev key",
        help="Get yours at auto.dev",
    )
    if anthro_key:  st.session_state.anthropic_key = anthro_key
    if autodev_key: st.session_state.autodev_key   = autodev_key

    keys_ok = bool(st.session_state.anthropic_key and st.session_state.autodev_key)
    if keys_ok:
        st.success("✅ Keys configured — ready to search!")
    else:
        st.warning("⚠️ Add both API keys to start searching")

    st.divider()

    # Post-search filters (only relevant once listings exist)
    if st.session_state.listings:
        st.markdown("### 🎛️ Filters")
        condition_filter = st.multiselect(
            "Condition", ["New", "Used", "CPO"], default=[]
        )
        max_price_filter = st.number_input("Max Price ($)", value=0, step=1000)
        no_accidents_filter = st.checkbox("No accidents only")
        one_owner_filter = st.checkbox("One owner only")
        max_mileage_filter = st.slider("Max Mileage", 0, 200000, 200000, step=5000)
        st.divider()

    st.markdown("### ℹ️ About")
    st.markdown("""
This app uses:
- **Anthropic Claude** to understand your search
- **auto.dev API** for live US dealer inventory
- Results are analyzed by AI for deal quality

[View source on GitHub](https://github.com) · Built with ❤️
""")


# ════════════════════════════════════════════════════════════════
# AUTO.DEV FETCHER
# ════════════════════════════════════════════════════════════════
AUTO_DEV_BASE = "https://auto.dev/api"

def fetch_listings(make: str, model: str, year: Optional[int] = None,
                   state: Optional[str] = None, api_key: str = "") -> list:
    """Fetch listings from auto.dev API."""
    params = {
        "make": make, "model": model,
        "apikey": api_key,
        "sortBy": "price", "sortOrder": "asc",
        "numRecords": 50,
    }
    if year:  params["year"]  = year
    if state: params["state"] = state

    all_listings = []
    for page in range(1, 4):  # max 3 pages
        params["page"] = page
        try:
            r = requests.get(f"{AUTO_DEV_BASE}/listings", params=params, timeout=12)
            r.raise_for_status()
            data = r.json()
            records = data.get("records", [])
            if not records:
                break
            all_listings.extend(records)
            if len(records) < params["numRecords"]:
                break
        except Exception as e:
            st.warning(f"auto.dev API error (page {page}): {e}")
            break

    return all_listings


def normalize_listing(raw: dict) -> dict:
    """Convert raw auto.dev record to our standard format."""
    listing_price = raw.get("price") or raw.get("listingPrice") or 0
    base_msrp = raw.get("msrp") or raw.get("baseMsrp") or 0
    discount_amount = (base_msrp - listing_price) if base_msrp and listing_price and base_msrp > listing_price else 0
    discount_pct = (discount_amount / base_msrp * 100) if base_msrp and discount_amount > 0 else 0

    dealer = raw.get("dealer") or {}
    dealer_name = dealer.get("name") or raw.get("dealerName", "")
    dealer_city = dealer.get("city") or raw.get("dealerCity", "")
    dealer_state = dealer.get("state") or raw.get("dealerState", "")

    listing_url = (
        raw.get("clickoffUrl")
        or (("https://auto.dev" + raw["vdpUrl"]) if raw.get("vdpUrl") else None)
    )

    return {
        "vin": raw.get("vin", ""),
        "year": raw.get("year"),
        "make": raw.get("make", ""),
        "model": raw.get("model", ""),
        "trim": raw.get("trim", ""),
        "listing_price": listing_price,
        "base_msrp": base_msrp,
        "discount_amount": discount_amount,
        "discount_pct": round(discount_pct, 1),
        "mileage": raw.get("mileage") or 0,
        "condition": raw.get("condition", ""),
        "is_new": raw.get("isNew", False),
        "is_used": raw.get("isUsed", True),
        "is_cpo": raw.get("isCertified", False),
        "drivetrain": raw.get("drivetrain", ""),
        "exterior_color": raw.get("exteriorColor", ""),
        "interior_color": raw.get("interiorColor", ""),
        "accidents": raw.get("accidentCount"),
        "one_owner": raw.get("oneOwner", False),
        "primary_image": raw.get("primaryPhotoUrl") or (raw.get("photos") or [{}])[0].get("url", ""),
        "dealer_name": dealer_name,
        "dealer_city": dealer_city,
        "dealer_state": dealer_state,
        "carfax_url": raw.get("carfaxUrl") or raw.get("carfaxReportUrl"),
        "listing_url": listing_url,
    }


# ════════════════════════════════════════════════════════════════
# AI ENGINE (inline — no FastAPI dependency)
# ════════════════════════════════════════════════════════════════

def get_anthropic_client(api_key: str):
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


def extract_intent(query: str, api_key: str) -> tuple[dict, list]:
    """
    Agent 1: NL → structured params using claude-haiku-4-5.
    Returns (intent_dict, tool_call_details_list).
    """
    client = get_anthropic_client(api_key)
    tools = [{
        "name": "set_search_params",
        "description": "Set structured search parameters from a natural language car search query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "make":         {"type": "string"},
                "model":        {"type": "string"},
                "year":         {"type": "integer"},
                "state":        {"type": "string", "description": "2-letter US state code"},
                "max_price":    {"type": "integer"},
                "condition":    {"type": "string", "enum": ["new","used","cpo",""]},
                "drivetrain":   {"type": "string", "enum": ["AWD","RWD","FWD",""]},
                "max_mileage":  {"type": "integer"},
                "color":        {"type": "string"},
                "no_accidents": {"type": "boolean"},
                "one_owner":    {"type": "boolean"},
                "brand_was_specified": {"type": "boolean"},
                "suggested_alternatives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "make":   {"type": "string"},
                            "model":  {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["make", "model", "reason"]
                    }
                },
                "summary": {"type": "string"},
            },
            "required": ["make", "model", "brand_was_specified", "summary"]
        }
    }]

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        tools=tools,
        tool_choice={"type": "any"},
        system="""You are a car search expert. Extract structured search parameters from natural language.
FEATURE MATCHING: 3-row SUV + Bose 12 → Hyundai Palisade; 3-row SUV + B&W → Volvo XC90;
luxury SUV no 3rd row + B&W → BMW X5; luxury no 3rd row + Burmester → Mercedes GLE;
family SUV under $55k → Toyota Highlander; sporty sedan → BMW 3 Series.
Always pick ONE best make+model as primary. List alternatives in suggested_alternatives.
brand_was_specified=false when recommending based on features.""",
        messages=[{"role": "user", "content": f"Parse this car search: {query}"}]
    )

    intent = {}
    for block in resp.content:
        if block.type == "tool_use":
            intent = block.input
            break

    # Build tool call record for display
    tool_calls = [{
        "label": "Intent Extraction",
        "model": "claude-haiku-4-5",
        "icon": "🧠",
        "input": {"query": query},
        "output": {k: v for k, v in intent.items()
                   if k not in ("suggested_alternatives",) and v not in (None, "", [], False)},
    }]

    return intent, tool_calls


def analyze_deal_streamlit(listing: dict, all_listings: list, api_key: str) -> dict:
    """Agent 2: Deep analysis for a specific listing."""
    client = get_anthropic_client(api_key)
    prices = [l["listing_price"] for l in all_listings if l.get("listing_price")]
    avg_price = round(sum(prices)/len(prices)) if prices else 0
    listing_price = listing.get("listing_price", 0) or 0
    diff = listing_price - avg_price
    vs_market = f"${abs(int(diff)):,} {'above' if diff > 0 else 'below'} the ${avg_price:,} market average" if avg_price else "market avg unknown"

    context = f"""LISTING: {listing.get('year')} {listing.get('make')} {listing.get('model')} {listing.get('trim','')}
Price: ${listing_price:,} | Mileage: {listing.get('mileage',0):,} mi
Drivetrain: {listing.get('drivetrain','?')} | Color: {listing.get('exterior_color','?')}
Condition: {'CPO' if listing.get('is_cpo') else 'Used' if listing.get('is_used') else 'New'}
Accidents: {listing.get('accidents',0)} | One Owner: {'Yes' if listing.get('one_owner') else 'Unknown'}
Dealer: {listing.get('dealer_name','?')} — {listing.get('dealer_city','?')}, {listing.get('dealer_state','?')}
MARKET ({len(all_listings)} listings): This car is {vs_market}
Range: ${min(prices) if prices else 0:,} – ${max(prices) if prices else 0:,}"""

    tools = [{"name":"deal_analysis","description":"Structured deal analysis","input_schema":{"type":"object","properties":{
        "recommendation":{"type":"string","enum":["Strong Buy","Buy","Negotiate","Wait","Pass"]},
        "headline":{"type":"string"},
        "price_assessment":{"type":"string"},
        "negotiation_tips":{"type":"array","items":{"type":"string"}},
        "green_flags":{"type":"array","items":{"type":"string"}},
        "red_flags":{"type":"array","items":{"type":"string"}},
        "bottom_line":{"type":"string"},
    },"required":["recommendation","headline","price_assessment","negotiation_tips","green_flags","red_flags","bottom_line"]}}]

    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=900, tools=tools, tool_choice={"type":"any"},
        system="Expert car buying advisor. Be honest, specific, and helpful.",
        messages=[{"role":"user","content":f"Analyze this deal:\n\n{context}"}]
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    return {}


# ════════════════════════════════════════════════════════════════
# LISTING CARD RENDERER
# ════════════════════════════════════════════════════════════════

def apply_filters(listings: list) -> list:
    """Apply sidebar filters to listings."""
    filtered = listings
    if "condition_filter" in dir() and condition_filter:
        cond_map = {"New": lambda l: not l.get("is_used") and not l.get("is_cpo"),
                    "Used": lambda l: l.get("is_used") and not l.get("is_cpo"),
                    "CPO":  lambda l: l.get("is_cpo")}
        filtered = [l for l in filtered if any(cond_map[c](l) for c in condition_filter if c in cond_map)]
    if "max_price_filter" in dir() and max_price_filter > 0:
        filtered = [l for l in filtered if (l.get("listing_price") or 0) <= max_price_filter]
    if "no_accidents_filter" in dir() and no_accidents_filter:
        filtered = [l for l in filtered if l.get("accidents") == 0]
    if "one_owner_filter" in dir() and one_owner_filter:
        filtered = [l for l in filtered if l.get("one_owner")]
    if "max_mileage_filter" in dir() and max_mileage_filter < 200000:
        filtered = [l for l in filtered if (l.get("mileage") or 0) <= max_mileage_filter]
    return filtered


def render_listing_card(listing: dict, idx: int):
    """Render a single car listing as an HTML card."""
    price  = listing.get("listing_price", 0) or 0
    msrp   = listing.get("base_msrp", 0) or 0
    disc_a = listing.get("discount_amount", 0) or 0
    disc_p = listing.get("discount_pct", 0) or 0

    badges = []
    if listing.get("is_cpo"):   badges.append('<span class="badge badge-cpo">CPO</span>')
    elif listing.get("is_used"): badges.append('<span class="badge badge-used">Used</span>')
    else:                        badges.append('<span class="badge badge-new">New</span>')
    if disc_p >= 15: badges.append('<span class="badge badge-hot">🔥 Hot Deal</span>')
    elif disc_p >= 8: badges.append('<span class="badge badge-deal">Good Deal</span>')

    metas = []
    if listing.get("mileage"):       metas.append(f'🛣 {listing["mileage"]:,} mi')
    if listing.get("drivetrain"):    metas.append(f'⚙ {listing["drivetrain"]}')
    if listing.get("exterior_color"): metas.append(f'🎨 {listing["exterior_color"]}')
    if listing.get("accidents") == 0: metas.append('✓ No accidents')
    if listing.get("one_owner"):     metas.append('👤 1 owner')

    is_direct = listing.get("listing_url") and "auto.dev" not in listing.get("listing_url","")
    dn = listing.get("dealer_name","Unknown Dealer")
    dealer_url = listing.get("listing_url") if is_direct else f"https://www.google.com/search?q={listing.get('vin','')}+{dn}+dealership"
    dealer_link = f'<a class="dealer-name-link" href="{dealer_url}" target="_blank">{dn}</a>'

    action_links = []
    if listing.get("listing_url"):
        label = "🔗 Dealer" if is_direct else "🖼 Photos"
        action_links.append(f'<a href="{listing["listing_url"]}" target="_blank" style="font-size:.75rem;color:#f59e0b;text-decoration:none">{label}</a>')
    if listing.get("vin"):
        action_links.append(f'<a href="https://www.google.com/search?q={listing["vin"]}" target="_blank" style="font-size:.75rem;color:#818cf8;text-decoration:none">🔍 VIN Search</a>')
    if listing.get("carfax_url"):
        action_links.append(f'<a href="{listing["carfax_url"]}" target="_blank" style="font-size:.75rem;color:#34d399;text-decoration:none">📋 Carfax</a>')

    img_html = f'<img src="{listing["primary_image"]}" style="width:100%;height:160px;object-fit:cover;border-radius:8px;margin-bottom:10px" onerror="this.style.display=\'none\'">' if listing.get("primary_image") else ""

    html = f"""
<div class="car-card">
  {img_html}
  <div>{"".join(badges)}</div>
  <div class="card-title">{listing.get('year','')} {listing.get('make','')} {listing.get('model','')} {listing.get('trim','')}</div>
  <div style="display:flex;align-items:baseline;gap:10px;margin:6px 0">
    <div class="card-price">${price:,}</div>
    {"<div class='card-msrp'>MSRP $"+f"{msrp:,}"+"</div>" if msrp else ""}
  </div>
  {"<div class='card-savings'>↓ $"+f"{round(disc_a):,}"+" off MSRP ("+f"{disc_p:.1f}"+"% discount)</div>" if disc_a > 0 else ""}
  <div style="margin:8px 0">{"".join(f'<span class="meta-tag">{m}</span>' for m in metas)}</div>
  <div class="dealer-line">📍 {dealer_link} · {listing.get('dealer_city','')}, {listing.get('dealer_state','')}</div>
  <div style="display:flex;gap:12px;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06)">
    {"  ".join(action_links)}
  </div>
</div>"""
    return html


# ════════════════════════════════════════════════════════════════
# MAIN LAYOUT — Chat + Listings (2 columns)
# ════════════════════════════════════════════════════════════════

col_listings, col_chat = st.columns([2.2, 1], gap="medium")

# ── CHAT COLUMN ──────────────────────────────────────────────
with col_chat:
    st.markdown("### ✨ AI Car Finder")

    # Render chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🚗" if msg["role"] == "assistant" else "👤"):
            if msg.get("is_html"):
                st.markdown(msg["content"], unsafe_allow_html=True)
            else:
                st.markdown(msg["content"])

            # Show tool calls if present
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    with st.expander(f"{tc['icon']} {tc['label']} ({tc['model']})", expanded=False):
                        st.markdown("**Input:**")
                        st.json(tc["input"])
                        st.markdown("**Output:**")
                        st.json(tc["output"])

    # Chat input
    user_query = st.chat_input(
        "What car are you looking for? (e.g. Used BMW X5 AWD under $50k Texas)",
        disabled=not keys_ok,
    )

    if not keys_ok:
        st.info("⬆️ Add your API keys in the sidebar to start searching")

    if user_query and keys_ok:
        # Add user message
        st.session_state.messages.append({"role": "user", "content": user_query})

        with st.chat_message("user", avatar="👤"):
            st.markdown(user_query)

        with st.chat_message("assistant", avatar="🚗"):
            with st.spinner("🧠 Extracting search intent…"):
                try:
                    intent, tool_calls = extract_intent(
                        user_query, st.session_state.anthropic_key
                    )
                except Exception as e:
                    st.error(f"Intent extraction failed: {e}")
                    st.stop()

            # Show tool call
            with st.expander("🧠 Intent Extraction (claude-haiku-4-5)", expanded=True):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Input**")
                    st.json({"query": user_query})
                with col_b:
                    st.markdown("**Output**")
                    st.json(tool_calls[0]["output"])

            make  = intent.get("make", "")
            model = intent.get("model", "")

            if not make or not model:
                st.warning("Could not determine make/model. Try being more specific.")
                st.stop()

            with st.spinner(f"🗄️ Fetching {make} {model} listings from auto.dev…"):
                raw_listings = fetch_listings(
                    make, model,
                    year=intent.get("year"),
                    state=intent.get("state"),
                    api_key=st.session_state.autodev_key,
                )
                listings = [normalize_listing(r) for r in raw_listings]

            # DB query tool call display
            db_call = {
                "label": "auto.dev Fetch",
                "model": "auto.dev API",
                "icon": "🗄️",
                "input": {k: v for k, v in intent.items() if v and k != "suggested_alternatives"},
                "output": {"listings_fetched": len(listings), "make": make, "model": model},
            }
            with st.expander("🗄️ API Fetch (auto.dev)", expanded=True):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Input**")
                    st.json(db_call["input"])
                with col_b:
                    st.markdown("**Output**")
                    st.json(db_call["output"])

            all_tool_calls = tool_calls + [db_call]

            # Apply intent filters
            filtered = listings
            if intent.get("max_price"):
                filtered = [l for l in filtered if (l.get("listing_price") or 0) <= intent["max_price"]]
            if intent.get("condition"):
                c = intent["condition"].lower()
                if c == "new":  filtered = [l for l in filtered if not l.get("is_used") and not l.get("is_cpo")]
                elif c == "used": filtered = [l for l in filtered if l.get("is_used")]
                elif c == "cpo":  filtered = [l for l in filtered if l.get("is_cpo")]
            if intent.get("max_mileage"):
                filtered = [l for l in filtered if (l.get("mileage") or 0) <= intent["max_mileage"]]
            if intent.get("no_accidents"):
                filtered = [l for l in filtered if l.get("accidents") == 0]
            if intent.get("drivetrain"):
                dt = intent["drivetrain"].upper()
                filtered = [l for l in filtered if l.get("drivetrain","").upper() == dt]

            # Sort by discount
            filtered.sort(key=lambda l: l.get("discount_pct",0), reverse=True)
            st.session_state.listings = filtered

            total = len(filtered)
            prices = [l["listing_price"] for l in filtered if l.get("listing_price")]
            avg_p = round(sum(prices)/len(prices)) if prices else 0

            summary = intent.get("summary", "")
            ai_msg_parts = [f"**{make} {model}** — found **{total}** listing{'s' if total!=1 else ''}"]
            if avg_p: ai_msg_parts.append(f"avg **${avg_p:,}**")
            response_text = " · ".join(ai_msg_parts)
            if summary: response_text += f"\n\n*{summary}*"

            alts = intent.get("suggested_alternatives", [])
            if alts:
                response_text += "\n\n**Also try:** " + " · ".join([f"{a['make']} {a['model']}" for a in alts[:4]])

            st.markdown(response_text)
            st.session_state.messages.append({
                "role": "assistant",
                "content": response_text,
                "tool_calls": all_tool_calls,
            })

        st.rerun()


# ── LISTINGS COLUMN ─────────────────────────────────────────────
with col_listings:
    listings = st.session_state.listings

    if not listings:
        st.markdown("""
<div style="text-align:center;padding:60px 20px;background:#0f1724;border-radius:14px;border:1px solid rgba(255,255,255,0.07)">
  <div style="font-size:3rem;margin-bottom:16px">🤖</div>
  <div style="font-size:1.2rem;font-weight:700;color:#f1f5f9;margin-bottom:8px">Ask the AI to find your car</div>
  <div style="color:#64748b">Type what you're looking for in the chat panel →</div>
</div>""", unsafe_allow_html=True)
    else:
        intent = st.session_state.last_intent or {}
        total = len(listings)
        prices = [l["listing_price"] for l in listings if l.get("listing_price")]
        avg_p  = round(sum(prices)/len(prices)) if prices else 0
        best_d = max((l.get("discount_pct",0) for l in listings), default=0)

        # Stats row
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Results", f"{total:,}")
        c2.metric("Avg Price", f"${avg_p:,}" if avg_p else "—")
        c3.metric("Best Discount", f"{best_d:.1f}%" if best_d else "—")
        c4.metric("Price Range", f"${min(prices):,}–${max(prices):,}" if len(prices) >= 2 else "—")

        st.divider()

        # AI analysis for top listing
        top = listings[0]
        with st.expander(f"✨ AI Deal Analysis — {top.get('year')} {top.get('make')} {top.get('model')} (Best Deal)", expanded=False):
            if st.button("🔍 Analyze this deal with Claude Sonnet", key="analyze_top"):
                with st.spinner("Claude Sonnet analyzing deal vs market…"):
                    try:
                        analysis = analyze_deal_streamlit(top, listings, st.session_state.anthropic_key)
                        rec = analysis.get("recommendation","")
                        rec_class = "rec-" + rec.lower().replace(" ","-")
                        st.markdown(f'<span class="rec-badge {rec_class}">{rec}</span>', unsafe_allow_html=True)
                        st.markdown(f"**{analysis.get('headline','')}**")
                        st.markdown(analysis.get("price_assessment",""))

                        col_g, col_r = st.columns(2)
                        with col_g:
                            st.markdown("**✅ Green Flags**")
                            for f in analysis.get("green_flags",[]):
                                st.markdown(f"• {f}")
                        with col_r:
                            st.markdown("**🚩 Red Flags**")
                            for f in analysis.get("red_flags",[]):
                                st.markdown(f"• {f}")

                        st.markdown("**💬 Negotiation Tips**")
                        for tip in analysis.get("negotiation_tips",[]):
                            st.markdown(f"• {tip}")
                        st.info(f"💡 **Bottom Line:** {analysis.get('bottom_line','')}")
                    except Exception as e:
                        st.error(f"Analysis failed: {e}")

        # Render cards in a 2-column grid
        cards_per_row = 2
        for i in range(0, min(len(listings), 20), cards_per_row):
            row_cols = st.columns(cards_per_row)
            for j, col in enumerate(row_cols):
                idx = i + j
                if idx < len(listings):
                    with col:
                        st.markdown(render_listing_card(listings[idx], idx), unsafe_allow_html=True)
