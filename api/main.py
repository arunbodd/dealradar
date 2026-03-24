"""
AI Car Deal Finder — FastAPI Backend
======================================

STORAGE ARCHITECTURE
─────────────────────
  SQLite (local DB at ~/.car-deal-finder/inventory.db)
  ├── inventory       — one row per VIN, persisted across refreshes
  │     status: active | removed | pending_verification
  │     consecutive_misses: # times it wasn't seen in latest API sync
  │     → after 2 misses → marked 'removed' (sold / taken down)
  ├── search_meta     — tracks when each make/model/year combo was last synced
  └── api_usage       — counts every real API call made

  vs PostgreSQL (schema.sql):
    Same design but hosted, multi-user, production-scale.
    Use PostgreSQL when: multiple users, team, or >100k listings.
    SQLite is perfectly fine for personal/small-team use.

HOW STALE INVENTORY IS DETECTED
─────────────────────────────────
  On each refresh for a combo (e.g. BMW X5 2024):
    1. Fetch fresh VINs from auto.dev API
    2. Compare against DB VINs with status='active'
    3. VINs in DB but missing from API → consecutive_misses += 1
       • miss 1: status stays 'active' (API glitch, just relisted, etc.)
       • miss 2: status → 'removed'  (sold / contract / taken down)
    4. VINs back in API → reset consecutive_misses=0, status → 'active'
    5. New VINs → insert fresh

API CALL BUDGET
────────────────
  • First search for a combo: ~5 calls (500 listings max)
  • Repeat within CACHE_TTL_HOURS: 0 calls (served from DB)
  • Force refresh: ~5 calls, but only delta-updates rows (no full replace)

Start:
    uvicorn main:app --reload --port 8000
"""

import os, sys, json, math, sqlite3, time, logging
from pathlib import Path
from typing import Optional, List

# Ensure the api/ directory is on sys.path so `import ai_engine` works
# regardless of whether uvicorn is invoked from the project root or api/
sys.path.insert(0, str(Path(__file__).parent))

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

load_dotenv(Path(__file__).parent.parent / "data_pipeline" / ".env", override=False)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

AUTO_DEV_API_KEY = (os.getenv("AUTO_DEV_API_KEY") or "").strip()
AUTO_DEV_BASE    = "https://api.auto.dev"
DATA_PROVIDER    = "autodev" if AUTO_DEV_API_KEY else "none"

# DB_PATH: use env var on cloud, fall back to ~/.car-deal-finder/inventory.db locally
_db_env = os.getenv("DB_PATH")
DB_PATH = Path(_db_env) if _db_env else Path.home() / ".car-deal-finder" / "inventory.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── Startup env check (visible in Render/Railway logs) ───────
log.info(f"ANTHROPIC_API_KEY: {'SET [ok]' if (os.getenv('ANTHROPIC_API_KEY') or '').strip() else 'MISSING [!]'}")
log.info(f"AUTO_DEV_API_KEY:  {'SET [ok]' if AUTO_DEV_API_KEY else 'MISSING [!]'}")
log.info(f"DATA_PROVIDER:     {DATA_PROVIDER}")
log.info(f"DB_PATH: {DB_PATH}")
CACHE_TTL_HOURS   = 24      # hours before auto-refresh
MAX_FETCH_PAGES   = 5       # 5 pages × 100 listings = 500 per combo
MISS_THRESHOLD    = 2       # consecutive misses before marking 'removed'


# ═══════════════════════════════════════════════════════════════
# SMART MODEL RESOLUTION  (three layers, no hardcoded tables)
#
#  1. Regex   — universal spacing fix: "ES350" → "ES 350"
#  2. DB mine — query our own inventory for known models for this
#               make; fuzzy-match the user's input against them.
#               Self-building: improves as more searches are done.
#  3. API retry — if fetch returns 0 results, strip powertrain
#               suffixes progressively ("TX 350h" → "TX 350" → "TX")
#               until the API responds.  Winning name is stored in
#               DB so future queries skip the retry entirely.
# ═══════════════════════════════════════════════════════════════

import re as _re

def _normalize_spacing(model: str) -> str:
    """Insert space between letters and digits where missing.
    'ES350' → 'ES 350',  'GLE450' → 'GLE 450',  'TX350h' → 'TX 350h'
    Also collapses multiple spaces and strips edges.
    """
    s = model.strip()
    s = _re.sub(r'([A-Za-z])(\d)', r'\1 \2', s)   # letter→digit boundary
    s = _re.sub(r'(\d)([A-Za-z]{2,})', r'\1 \2', s)  # digit→multi-letter
    s = _re.sub(r' {2,}', ' ', s)
    return s


def _model_variants(model: str) -> list[str]:
    """
    Return an ordered list of progressively simpler model strings to try.
    e.g. 'TX 350h' → ['TX 350h', 'TX 350', 'TX']
         'RX 350'  → ['RX 350', 'RX']
    """
    spaced = _normalize_spacing(model)
    seen, variants = set(), []
    for v in [model, spaced]:
        if v and v not in seen:
            seen.add(v); variants.append(v)

    # Strip trailing hybrid/powertrain tokens one at a time:
    # 'TX 350h' → 'TX 350' → 'TX'
    # 'GLE 450' → 'GLE'   (stops here, does NOT strip 'E' from 'GLE')
    current = spaced
    while True:
        # Remove trailing: digits + optional lowercase hybrid suffix (h/e/+)
        # Uppercase letters are kept — they're part of the model name (GLE, AMG, etc.)
        shorter = _re.sub(r'\s*\d+[he+]*\s*$', '', current, flags=_re.IGNORECASE).strip()
        if not shorter or shorter == current:
            break
        if shorter not in seen:
            seen.add(shorter); variants.append(shorter)
        current = shorter

    return variants


def _db_fuzzy_match(make: str, user_model: str) -> Optional[str]:
    """
    Query our own inventory DB for canonical model names for this make,
    then return the best match for user_model (or None).

    Match priority:
      1. Exact (case-insensitive)
      2. Space-collapsed exact  ('RX350' matches 'RX 350')
      3. User input is a prefix of a DB model ('TX' matches 'TX 350')
      4. DB model is a prefix of user input   ('TX' from 'TX350h')
    """
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT model FROM inventory WHERE LOWER(make)=? AND status='active'",
            [make.lower()]
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    db_models = [r["model"] for r in rows]
    u = user_model.lower().replace(" ", "").replace("-", "")

    # Priority 1: exact case-insensitive
    for m in db_models:
        if m.lower() == user_model.lower():
            return m

    # Priority 2: space-stripped exact
    for m in db_models:
        if m.lower().replace(" ", "").replace("-", "") == u:
            return m

    # Priority 3: user input is a prefix of a DB model (short → long)
    prefix_hits = [m for m in db_models if m.lower().replace(" ", "").startswith(u)]
    if prefix_hits:
        return sorted(prefix_hits, key=len)[0]   # shortest (most general)

    # Priority 4: DB model is a prefix of user input (catches 'TX' → 'TX350h')
    suffix_hits = [m for m in db_models if u.startswith(m.lower().replace(" ", ""))]
    if suffix_hits:
        return sorted(suffix_hits, key=len, reverse=True)[0]  # longest (most specific)

    return None


def canonicalize_model(make: str, model: str) -> str:
    """
    Layer 1 + 2: regex normalize then DB fuzzy-match.
    Returns the best known canonical name, or the regex-normalized
    string if the DB has no data for this make yet.
    Called before every fetch/key so stale keys are never created.
    """
    if not make or not model:
        return model
    spaced = _normalize_spacing(model)
    db_hit = _db_fuzzy_match(make, spaced)
    if db_hit:
        if db_hit != model:
            log.info(f"Model resolved via DB: {make} '{model}' → '{db_hit}'")
        return db_hit
    if spaced != model:
        log.info(f"Model spacing fixed: '{model}' → '{spaced}'")
    return spaced


# ═══════════════════════════════════════════════════════════════
# GEO HELPERS — Haversine distance + ZIP geocoding
# ═══════════════════════════════════════════════════════════════

_zip_cache: dict = {}  # in-memory zip→(lat,lng) cache (resets on server restart)

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance between two GPS coordinates in miles."""
    import math
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_zip(zip_code: str) -> Optional[tuple]:
    """
    Return (lat, lng) for a US ZIP code via OpenStreetMap Nominatim.
    Results are cached in-memory for the lifetime of the server process.
    """
    if zip_code in _zip_cache:
        return _zip_cache[zip_code]
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"postalcode": zip_code, "country": "US", "format": "json", "limit": 1},
            headers={"User-Agent": "DealRadar/1.0"},
            timeout=5,
        )
        data = resp.json()
        if data:
            coords = (float(data[0]["lat"]), float(data[0]["lon"]))
            _zip_cache[zip_code] = coords
            return coords
    except Exception as e:
        log.warning(f"Geocode failed for ZIP {zip_code}: {e}")
    return None


app = FastAPI(title="AI Car Deal Finder", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    """Diagnostic endpoint — shows env var status."""
    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    autodev_key   = (os.getenv("AUTO_DEV_API_KEY")  or "").strip()

    all_var_names = sorted(os.environ.keys())
    similar = [k for k in all_var_names
               if any(x in k.upper() for x in ("ANTHROPIC", "AUTO_DEV", "AUTODEV"))]

    return {
        "status":            "ok" if (anthropic_key and autodev_key) else "degraded",
        "ANTHROPIC_API_KEY": "set" if anthropic_key else "MISSING",
        "AUTO_DEV_API_KEY":  "set" if autodev_key   else "MISSING",
        "DATA_PROVIDER":     DATA_PROVIDER,
        "DB_PATH":           str(DB_PATH),
        "db_exists":         DB_PATH.exists(),
        "all_env_var_names": all_var_names,
        "similar_keys_found":similar,
    }


# ═══════════════════════════════════════════════════════════════
# DATABASE — schema & helpers
# ═══════════════════════════════════════════════════════════════

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # faster concurrent reads
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (
            vin                 TEXT PRIMARY KEY,
            search_key          TEXT NOT NULL,        -- 'bmw|x5|2024|us'
            status              TEXT DEFAULT 'active', -- active | removed
            consecutive_misses  INTEGER DEFAULT 0,

            -- Vehicle identity
            year                INTEGER,
            make                TEXT,
            model               TEXT,
            trim                TEXT,
            series              TEXT,
            body_style          TEXT,
            drivetrain          TEXT,
            engine              TEXT,
            transmission        TEXT,
            fuel                TEXT,
            exterior_color      TEXT,
            interior_color      TEXT,
            cylinders           INTEGER,

            -- Pricing (updated on each refresh)
            listing_price       REAL,
            base_msrp           REAL,
            base_invoice        REAL,
            discount_pct        REAL,
            discount_amount     REAL,
            prev_listing_price  REAL,        -- price on last refresh (detect price drops)

            -- Listing metadata
            listing_url         TEXT,
            mileage             INTEGER DEFAULT 0,
            is_used             INTEGER DEFAULT 1,
            is_cpo              INTEGER DEFAULT 0,
            is_online           INTEGER DEFAULT 1,
            primary_image       TEXT,
            carfax_url          TEXT,

            -- Dealer
            dealer_name         TEXT,
            dealer_city         TEXT,
            dealer_state        TEXT,
            dealer_zip          TEXT,
            dealer_lat          REAL,
            dealer_lng          REAL,

            -- Vehicle history
            accidents           INTEGER DEFAULT 0,
            one_owner           INTEGER,
            usage_type          TEXT,

            -- Deal score
            score               REAL,
            discount_score      REAL,
            history_score       REAL,
            cpo_score           REAL,

            -- Lifecycle timestamps
            first_seen_at       REAL NOT NULL,   -- unix timestamp
            last_seen_at        REAL NOT NULL,   -- last time API returned this VIN
            last_verified_at    REAL,            -- last time we ran a refresh for this combo
            removed_at          REAL,            -- when it was marked removed
            src_created_at      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_inv_search_key ON inventory(search_key);

        CREATE INDEX IF NOT EXISTS idx_inv_status     ON inventory(status);
        CREATE INDEX IF NOT EXISTS idx_inv_score      ON inventory(score DESC);
        CREATE INDEX IF NOT EXISTS idx_inv_price      ON inventory(listing_price);
        CREATE INDEX IF NOT EXISTS idx_inv_state      ON inventory(dealer_state);

        CREATE TABLE IF NOT EXISTS search_meta (
            search_key      TEXT PRIMARY KEY,
            last_synced_at  REAL,
            api_calls_used  INTEGER DEFAULT 0,
            total_fetched   INTEGER DEFAULT 0,
            active_count    INTEGER DEFAULT 0,
            removed_count   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS api_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            search_key  TEXT,
            calls       INTEGER DEFAULT 1,
            ts          REAL DEFAULT (strftime('%s','now'))
        );
    """)
    conn.commit()
    # Add columns if they don't exist (migration-safe)
    for col in [
        ("ai_analysis",   "TEXT"),
        ("ai_analyzed_at","REAL"),
        ("title_brand",   "TEXT"),   # salvage / rebuilt / lemon / clean / unknown
    ]:
        try:
            conn.execute(f"ALTER TABLE inventory ADD COLUMN {col[0]} {col[1]}")
            conn.commit()
        except Exception:
            pass  # column already exists
    return conn


def search_key(make: str, model: str, year: Optional[int], state: Optional[str],
               zip_code: Optional[str] = None, radius_miles: Optional[int] = None) -> str:
    if zip_code:
        return f"{make.lower()}|{model.lower()}|{year or 'any'}|zip{zip_code}r{radius_miles or 100}"
    return f"{make.lower()}|{model.lower()}|{year or 'any'}|{state or 'us'}"


def is_stale(key: str) -> bool:
    """Returns True if the combo hasn't been synced within CACHE_TTL_HOURS."""
    conn = get_db()
    row = conn.execute(
        "SELECT last_synced_at FROM search_meta WHERE search_key=?", (key,)
    ).fetchone()
    conn.close()
    if not row or not row["last_synced_at"]:
        return True
    age_hours = (time.time() - row["last_synced_at"]) / 3600
    return age_hours > CACHE_TTL_HOURS


def get_total_api_calls() -> int:
    conn = get_db()
    row = conn.execute("SELECT COALESCE(SUM(calls),0) as total FROM api_usage").fetchone()
    conn.close()
    return int(row["total"])


# ═══════════════════════════════════════════════════════════════
# NORMALIZE — auto.dev raw → flat dict
# ═══════════════════════════════════════════════════════════════

def normalize(raw: dict) -> dict:
    v    = raw.get("vehicle", {}) or {}
    rl   = raw.get("retailListing") or {}
    loc  = raw.get("location") or [None, None]
    hist = raw.get("history") or {}

    price     = rl.get("price")
    base_msrp = v.get("baseMsrp")
    discount  = round((base_msrp - price) / base_msrp * 100, 1) if (base_msrp and price and base_msrp > price) else 0

    discount_score = min(discount / 20.0, 1.0)
    accidents  = hist.get("accidentCount", 0) if hist else 0
    one_owner  = hist.get("oneOwner", False) if hist else False

    # Title brand: check history for salvage/rebuilt/lemon flags
    # auto.dev returns titleBrand or branded title info in history
    _title_raw = (hist.get("titleBrand") or hist.get("titleStatus") or "").lower() if hist else ""
    _acc_list  = hist.get("accidents") or []  # list of accident dicts
    _salvage_keywords = ("salvage", "rebuilt", "lemon", "flood", "junk", "insurance loss")
    if any(kw in _title_raw for kw in _salvage_keywords):
        title_brand = _title_raw.capitalize()
    elif any(kw in str(a).lower() for a in _acc_list for kw in ("salvage", "total loss", "rebuilt")):
        title_brand = "Salvage"
    elif _title_raw in ("clean", "clear"):
        title_brand = "Clean"
    elif accidents and accidents > 0:
        title_brand = "Unknown"   # has accidents but no title info
    else:
        title_brand = "Clean"

    # Determine new/used reliably:
    # auto.dev often omits `used:false` for dealer-new stock, so use mileage as ground truth.
    # <= 500 miles = effectively new regardless of what the API says.
    mileage    = rl.get("miles") or 0
    api_used   = rl.get("used")          # True / False / None
    is_cpo_raw = rl.get("cpo", False)
    if is_cpo_raw:
        is_used = True                   # CPO is always a used car
    elif api_used is False:
        is_used = False                  # API explicitly says new
    elif mileage <= 500:
        is_used = False                  # Very low miles → treat as new
    else:
        is_used = True                   # Everything else: used

    history_score = 1.0 if (not is_used or one_owner) else 0.5
    cpo_score  = 1.0 if rl.get("cpo") else 0.5
    # Salvage/rebuilt title is a hard penalty — no matter how cheap, it's risky
    _is_branded = title_brand.lower() in ("salvage", "rebuilt", "lemon", "flood")
    title_penalty = 0.45 if _is_branded else 1.0   # slash score nearly in half
    score      = round(discount_score * 0.45 + history_score * 0.20 + 1.0 * 0.20 + cpo_score * 0.15, 4)
    score      = round(score * title_penalty, 4)

    return {
        "vin":            raw.get("vin"),
        "listing_url":    (
            raw.get("clickoffUrl")                                          # actual dealer website (best)
            or (("https://auto.dev" + raw["vdpUrl"]) if raw.get("vdpUrl") else None)  # auto.dev listing page w/ photos
        ),
        "year":           v.get("year"),
        "make":           v.get("make"),
        "model":          v.get("model"),
        "trim":           v.get("trim"),
        "series":         v.get("series"),
        "body_style":     v.get("bodyStyle"),
        "drivetrain":     v.get("drivetrain"),
        "engine":         v.get("engine"),
        "transmission":   v.get("transmission"),
        "fuel":           v.get("fuel"),
        "exterior_color": v.get("exteriorColor"),
        "interior_color": v.get("interiorColor"),
        "cylinders":      v.get("cylinders"),
        "base_msrp":      base_msrp,
        "base_invoice":   v.get("baseInvoice"),
        "listing_price":  price,
        "discount_pct":   discount,
        "discount_amount":round(base_msrp - price, 0) if (base_msrp and price and base_msrp > price) else None,
        "mileage":        mileage,
        "is_used":        is_used,
        "is_cpo":         is_cpo_raw,
        "is_online":      raw.get("online", True),
        "primary_image":  rl.get("primaryImage"),
        "carfax_url":     rl.get("carfaxUrl"),
        "dealer_name":    rl.get("dealer"),
        "dealer_city":    rl.get("city"),
        "dealer_state":   rl.get("state"),
        "dealer_zip":     rl.get("zip"),
        "dealer_lat":     loc[1] if len(loc) > 1 else None,
        "dealer_lng":     loc[0] if len(loc) > 1 else None,
        "accidents":      accidents,
        "one_owner":      one_owner,
        "usage_type":     hist.get("usageType") if hist else None,
        "title_brand":    title_brand,
        "src_created_at": raw.get("createdAt"),
        "score":          score,
        "discount_score": round(discount_score, 4),
        "history_score":  round(history_score, 4),
        "cpo_score":      round(cpo_score, 4),
    }


# ═══════════════════════════════════════════════════════════════
# FETCH — auto.dev
# ═══════════════════════════════════════════════════════════════

def fetch_from_api(make: str, model: str, year: Optional[int] = None,
                   state: Optional[str] = None, zip_code: Optional[str] = None,
                   radius_miles: Optional[int] = None) -> tuple[List[dict], int]:
    """Fetch listings from auto.dev API. Returns (listings, pages_used).
    Supports geo search via zip_code + radius_miles (auto.dev handles radius natively).
    """
    if not AUTO_DEV_API_KEY:
        raise RuntimeError("AUTO_DEV_API_KEY is not set.")

    session = requests.Session()
    results, pages = [], 0

    for page in range(1, MAX_FETCH_PAGES + 1):
        params = {
            "apiKey":        AUTO_DEV_API_KEY,
            "vehicle.make":  make,
            "vehicle.model": model,
            "page":          page,
            "limit":         100,
        }
        if year:  params["vehicle.year"] = year
        if zip_code:
            params["zip"]      = zip_code
            params["distance"] = radius_miles or 100
        elif state:
            params["retailListing.state"] = state

        log.info(f"  [auto.dev] pg{page} — {make} {model} {year or ''} {state or ''}")
        try:
            resp = session.get(f"{AUTO_DEV_BASE}/listings", params=params, timeout=15)
            if resp.status_code != 200:
                log.warning(f"  [auto.dev] {resp.status_code} on page {page}")
                break
            raw_data = resp.json().get("data", [])
            batch = raw_data if isinstance(raw_data, list) else []
            pages += 1
            if not batch:
                break
            results.extend(normalize(r) for r in batch)
            if len(batch) < 100:
                log.info(f"  [auto.dev] Partial page ({len(batch)}) — done")
                break
        except Exception as e:
            log.error(f"  [auto.dev] Fetch error: {e}")
            break
        time.sleep(0.2)

    return results, pages


def fetch_with_retry(make: str, model: str, year: Optional[int] = None,
                     state: Optional[str] = None, zip_code: Optional[str] = None,
                     radius_miles: Optional[int] = None) -> tuple[List[dict], int, str]:
    """
    Layer 3: fetch with progressive model simplification on zero results.
    Tries each variant from _model_variants() in order, stops at first hit.
    Returns (results, pages_used, resolved_model_name).

    Example: 'TX350h' → tries 'TX 350h', 'TX 350', 'TX'
             First non-empty response wins; that name is returned so the
             caller can update the search key / DB with the canonical form.
    """
    variants = _model_variants(model)
    total_pages = 0
    for attempt in variants:
        results, pages = fetch_from_api(make, attempt, year, state, zip_code, radius_miles)
        total_pages += pages
        if results:
            if attempt != model:
                log.info(f"Model retry succeeded: '{model}' → '{attempt}' ({len(results)} results)")
            return results, total_pages, attempt
        if pages == 0:
            break  # API error, no point retrying
    return [], total_pages, model


# ═══════════════════════════════════════════════════════════════
# DELTA SYNC — the core of inventory freshness
# ═══════════════════════════════════════════════════════════════

def delta_sync(key: str, fresh: List[dict], pages_used: int):
    """
    Merge fresh API results into the inventory table.

    For each VIN in the fresh batch   → upsert (insert new / update existing)
    For each active VIN NOT in fresh  → increment consecutive_misses
      if misses >= MISS_THRESHOLD     → mark status='removed'

    This means:
      • A car gone from the API for 1 refresh stays active (API glitch buffer)
      • Gone for 2 consecutive refreshes → marked removed (sold / in contract)
      • If it reappears after being removed → reactivated
    """
    conn  = get_db()
    now   = time.time()

    fresh_vins = {r["vin"] for r in fresh if r.get("vin")}

    # ── 1. Upsert every VIN returned by the API ──────────────────
    for r in fresh:
        vin = r.get("vin")
        if not vin:
            continue

        existing = conn.execute(
            "SELECT listing_price, status FROM inventory WHERE vin=?", (vin,)
        ).fetchone()

        if existing:
            # Track price change
            prev_price = existing["listing_price"]
            conn.execute("""
                UPDATE inventory SET
                    status='active', consecutive_misses=0,
                    listing_price=?, prev_listing_price=?,
                    base_msrp=?, discount_pct=?, discount_amount=?,
                    is_online=?, score=?, discount_score=?, history_score=?, cpo_score=?,
                    primary_image=?, dealer_name=?, dealer_city=?, dealer_state=?,
                    last_seen_at=?, last_verified_at=?, removed_at=NULL
                WHERE vin=?
            """, (
                r["listing_price"], prev_price,
                r["base_msrp"], r["discount_pct"], r["discount_amount"],
                1 if r["is_online"] else 0,
                r["score"], r["discount_score"], r["history_score"], r["cpo_score"],
                r["primary_image"], r["dealer_name"], r["dealer_city"], r["dealer_state"],
                now, now, vin
            ))
        else:
            # Brand new listing
            conn.execute("""
                INSERT INTO inventory (
                    vin, search_key, status, consecutive_misses,
                    year, make, model, trim, series, body_style, drivetrain,
                    engine, transmission, fuel, exterior_color, interior_color, cylinders,
                    listing_price, base_msrp, base_invoice, discount_pct, discount_amount,
                    prev_listing_price, listing_url, mileage, is_used, is_cpo, is_online,
                    primary_image, carfax_url,
                    dealer_name, dealer_city, dealer_state, dealer_zip, dealer_lat, dealer_lng,
                    accidents, one_owner, usage_type, title_brand,
                    score, discount_score, history_score, cpo_score,
                    first_seen_at, last_seen_at, last_verified_at, src_created_at
                ) VALUES (
                    ?,?,?,0,
                    ?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,
                    ?,?,?,?,?,
                    NULL,?,?,?,?,?,
                    ?,?,
                    ?,?,?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?
                )
            """, (
                vin, key, "active",
                r["year"], r["make"], r["model"], r["trim"], r["series"], r["body_style"], r["drivetrain"],
                r["engine"], r["transmission"], r["fuel"], r["exterior_color"], r["interior_color"], r["cylinders"],
                r["listing_price"], r["base_msrp"], r["base_invoice"], r["discount_pct"], r["discount_amount"],
                r["listing_url"], r["mileage"], 1 if r["is_used"] else 0, 1 if r["is_cpo"] else 0, 1 if r["is_online"] else 0,
                r["primary_image"], r["carfax_url"],
                r["dealer_name"], r["dealer_city"], r["dealer_state"], r["dealer_zip"], r["dealer_lat"], r["dealer_lng"],
                r["accidents"], 1 if r.get("one_owner") else 0, r["usage_type"], r.get("title_brand", "Clean"),
                r["score"], r["discount_score"], r["history_score"], r["cpo_score"],
                now, now, now, r["src_created_at"]
            ))

    # ── 2. Handle active VINs that disappeared from the API ───────
    active_in_db = conn.execute(
        "SELECT vin, consecutive_misses FROM inventory WHERE search_key=? AND status='active'",
        (key,)
    ).fetchall()

    removed_this_sync = 0
    for row in active_in_db:
        if row["vin"] not in fresh_vins:
            new_misses = row["consecutive_misses"] + 1
            if new_misses >= MISS_THRESHOLD:
                conn.execute(
                    "UPDATE inventory SET status='removed', consecutive_misses=?, removed_at=?, last_verified_at=? WHERE vin=?",
                    (new_misses, now, now, row["vin"])
                )
                removed_this_sync += 1
                log.info(f"  ⚠ VIN {row['vin']} marked REMOVED (missed {new_misses}x)")
            else:
                conn.execute(
                    "UPDATE inventory SET consecutive_misses=?, last_verified_at=? WHERE vin=?",
                    (new_misses, now, row["vin"])
                )

    # ── 3. Update search_meta ────────────────────────────────────
    active_count  = conn.execute(
        "SELECT COUNT(*) as n FROM inventory WHERE search_key=? AND status='active'", (key,)
    ).fetchone()["n"]
    removed_count = conn.execute(
        "SELECT COUNT(*) as n FROM inventory WHERE search_key=? AND status='removed'", (key,)
    ).fetchone()["n"]

    conn.execute("""
        INSERT INTO search_meta (search_key, last_synced_at, api_calls_used, total_fetched, active_count, removed_count)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(search_key) DO UPDATE SET
            last_synced_at=excluded.last_synced_at,
            api_calls_used=api_calls_used + excluded.api_calls_used,
            total_fetched=excluded.total_fetched,
            active_count=excluded.active_count,
            removed_count=excluded.removed_count
    """, (key, now, pages_used, len(fresh), active_count, removed_count))

    conn.execute("INSERT INTO api_usage (search_key, calls) VALUES (?,?)", (key, pages_used))
    conn.commit()
    conn.close()

    log.info(f"  Sync complete: {len(fresh)} fresh | {active_count} active | {removed_this_sync} newly removed")
    return active_count, removed_this_sync


# ═══════════════════════════════════════════════════════════════
# BRAND CATEGORIES — for regional/type car queries
# ═══════════════════════════════════════════════════════════════

BRAND_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    "european": [
        ("BMW", "3 Series"), ("BMW", "5 Series"), ("BMW", "X5"), ("BMW", "X3"),
        ("Mercedes-Benz", "C 300"), ("Mercedes-Benz", "E 350"), ("Mercedes-Benz", "GLE 350"), ("Mercedes-Benz", "GLC 300"),
        ("Audi", "A4"), ("Audi", "A6"), ("Audi", "Q5"), ("Audi", "Q7"),
        ("Volkswagen", "Tiguan"), ("Volkswagen", "Atlas"), ("Volkswagen", "Jetta"),
        ("Volvo", "XC60"), ("Volvo", "XC90"), ("Volvo", "S60"),
        ("Porsche", "Cayenne"), ("Porsche", "Macan"),
        ("Land Rover", "Range Rover Sport"), ("Land Rover", "Defender"),
        ("MINI", "Cooper"), ("Alfa Romeo", "Stelvio"), ("Alfa Romeo", "Giulia"),
        ("Jaguar", "F-PACE"), ("Jaguar", "XE"),
    ],
    "japanese": [
        ("Toyota", "Camry"), ("Toyota", "RAV4"), ("Toyota", "Highlander"),
        ("Honda", "Accord"), ("Honda", "CR-V"), ("Honda", "Pilot"),
        ("Lexus", "RX 350"), ("Lexus", "ES 350"), ("Lexus", "NX 350"),
        ("Mazda", "CX-5"), ("Mazda", "CX-50"), ("Subaru", "Outback"), ("Subaru", "Forester"),
        ("Nissan", "Altima"), ("Nissan", "Rogue"), ("Infiniti", "QX60"),
        ("Acura", "MDX"), ("Acura", "TLX"), ("Mitsubishi", "Outlander"),
    ],
    "american": [
        ("Ford", "F-150"), ("Ford", "Explorer"), ("Ford", "Mustang"), ("Ford", "Edge"),
        ("Chevrolet", "Silverado 1500"), ("Chevrolet", "Equinox"), ("Chevrolet", "Traverse"),
        ("Cadillac", "XT5"), ("Cadillac", "Escalade"), ("Cadillac", "CT5"),
        ("Jeep", "Grand Cherokee"), ("Jeep", "Wrangler"),
        ("Ram", "1500"), ("Dodge", "Durango"),
        ("Lincoln", "Nautilus"), ("GMC", "Sierra 1500"), ("Buick", "Enclave"),
    ],
    "korean": [
        ("Hyundai", "Tucson"), ("Hyundai", "Santa Fe"), ("Hyundai", "Palisade"), ("Hyundai", "Sonata"),
        ("Kia", "Sportage"), ("Kia", "Telluride"), ("Kia", "Sorento"), ("Kia", "K5"),
        ("Genesis", "G80"), ("Genesis", "GV80"), ("Genesis", "GV70"),
    ],
    "luxury": [
        ("BMW", "7 Series"), ("Mercedes-Benz", "S-Class"), ("Audi", "A8"),
        ("Cadillac", "CT5"), ("Lexus", "LS 500"), ("Porsche", "Panamera"),
        ("Genesis", "G90"), ("Lincoln", "Navigator"), ("Infiniti", "QX80"),
    ],
    "electric": [
        ("Tesla", "Model 3"), ("Tesla", "Model Y"), ("Tesla", "Model S"), ("Tesla", "Model X"),
        ("Rivian", "R1T"), ("Lucid", "Air"), ("BMW", "i4"),
        ("Hyundai", "IONIQ 5"), ("Hyundai", "IONIQ 6"), ("Kia", "EV6"),
        ("Audi", "e-tron"), ("Mercedes-Benz", "EQS"), ("Chevrolet", "Bolt EV"),
        ("Ford", "Mustang Mach-E"), ("Volkswagen", "ID.4"),
    ],
}

def query_inventory_by_makes(makes: list, max_price: Optional[int] = None,
                              condition: Optional[str] = None, state: Optional[str] = None,
                              sort_by: str = "score", *,
                              min_price: Optional[int] = None,
                              year_from: Optional[int] = None,
                              max_mileage: Optional[int] = None,
                              no_accidents: Optional[bool] = None,
                              one_owner: Optional[bool] = None,
                              drivetrain: Optional[str] = None,
                              zip_code: Optional[str] = None,
                              radius_miles: Optional[int] = None) -> list:
    """Query the local DB for active listings from any of the given makes (category search)."""
    if not makes:
        return []
    placeholders = ",".join("?" * len(makes))
    wheres = [f"UPPER(make) IN ({placeholders})", "status='active'", "listing_price IS NOT NULL", "listing_price > 0"]
    params: list = [m.upper() for m in makes]

    if max_price:
        wheres.append("listing_price <= ?"); params.append(max_price)
    if min_price:
        wheres.append("listing_price >= ?"); params.append(min_price)
    if year_from:
        wheres.append("year >= ?"); params.append(year_from)
    if condition == "new":
        wheres.append("is_used=0")
    elif condition == "used":
        wheres.append("is_used=1 AND is_cpo=0")
    elif condition == "cpo":
        wheres.append("is_cpo=1")
    if state:
        wheres.append("UPPER(dealer_state)=?"); params.append(state.upper())
    if drivetrain:
        wheres.append("UPPER(drivetrain)=?"); params.append(drivetrain.upper())
    if max_mileage:
        wheres.append("mileage <= ?"); params.append(max_mileage)
    if no_accidents:
        wheres.append("accidents=0")
    if one_owner:
        wheres.append("one_owner=1")

    order = {"score": "score DESC", "price": "listing_price ASC",
              "discount": "discount_pct DESC", "newest": "first_seen_at DESC"}.get(sort_by, "score DESC")

    sql = f"SELECT * FROM inventory WHERE {' AND '.join(wheres)} ORDER BY {order}"
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = [dict(r) for r in rows]

    if zip_code and radius_miles:
        coords = geocode_zip(zip_code)
        if coords:
            user_lat, user_lng = coords
            in_range = []
            for r in results:
                dlat, dlng = r.get("dealer_lat"), r.get("dealer_lng")
                if dlat and dlng:
                    dist = haversine_miles(user_lat, user_lng, dlat, dlng)
                    r["distance_miles"] = round(dist, 1)
                    if dist <= radius_miles:
                        in_range.append(r)
                else:
                    r["distance_miles"] = None
                    in_range.append(r)
            results = sorted(in_range, key=lambda x: (x["distance_miles"] is None, x.get("distance_miles", 9999)))
    return results


# ═══════════════════════════════════════════════════════════════
# QUERY — read from inventory DB
# ═══════════════════════════════════════════════════════════════

def query_inventory(key: str, max_price: Optional[int] = None,
                    condition: Optional[str] = None, state: Optional[str] = None,
                    sort_by: str = "score", *,
                    min_price: Optional[int] = None,
                    drivetrain: Optional[str] = None, max_mileage: Optional[int] = None,
                    color: Optional[str] = None, no_accidents: Optional[bool] = None,
                    one_owner: Optional[bool] = None,
                    year_from: Optional[int] = None,
                    body_style: Optional[str] = None,
                    zip_code: Optional[str] = None,
                    radius_miles: Optional[int] = None) -> List[dict]:
    """
    Read active listings from inventory DB.
    All filtering/sorting happens in SQLite — zero API calls.
    """
    wheres = ["search_key=?", "status='active'", "listing_price IS NOT NULL", "listing_price > 0"]
    params = [key]

    if max_price:
        wheres.append("listing_price <= ?")
        params.append(max_price)
    if min_price:
        wheres.append("listing_price >= ?")
        params.append(min_price)
    if condition == "new":
        wheres.append("is_used=0")
    elif condition == "used":
        wheres.append("is_used=1 AND is_cpo=0")
    elif condition == "cpo":
        wheres.append("is_cpo=1")
    if state:
        wheres.append("UPPER(dealer_state)=?")
        params.append(state.upper())
    if drivetrain:
        wheres.append("UPPER(drivetrain)=?")
        params.append(drivetrain.upper())
    if max_mileage:
        wheres.append("mileage <= ?")
        params.append(max_mileage)
    if color:
        wheres.append("LOWER(exterior_color) LIKE ?")
        params.append(f"%{color.lower()}%")
    if no_accidents:
        wheres.append("accidents=0")
    if one_owner:
        wheres.append("one_owner=1")
    if year_from:
        wheres.append("year >= ?")
        params.append(year_from)
    if body_style:
        wheres.append("LOWER(body_style) LIKE ?")
        params.append(f"%{body_style.lower()}%")

    order = {
        "score":    "score DESC",
        "price":    "listing_price ASC",
        "discount": "discount_pct DESC",
        "newest":   "first_seen_at DESC",
        "distance": "score DESC",   # distance sort applied in Python post-filter via Haversine
    }.get(sort_by, "score DESC")

    sql = f"SELECT * FROM inventory WHERE {' AND '.join(wheres)} ORDER BY {order}"
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = [dict(r) for r in rows]

    # ── Geo post-filter: if zip+radius provided, calculate distances and filter ──
    # auto.dev already geo-filtered at ingest time, but this adds distance_miles
    # to each row and re-filters any cached nationwide results.
    if zip_code and radius_miles:
        coords = geocode_zip(zip_code)
        if coords:
            user_lat, user_lng = coords
            in_range = []
            for r in results:
                dlat = r.get("dealer_lat")
                dlng = r.get("dealer_lng")
                if dlat and dlng:
                    dist = haversine_miles(user_lat, user_lng, dlat, dlng)
                    r["distance_miles"] = round(dist, 1)
                    if dist <= radius_miles:
                        in_range.append(r)
                else:
                    r["distance_miles"] = None
                    in_range.append(r)
            results = sorted(in_range, key=lambda x: (x["distance_miles"] is None, x.get("distance_miles", 9999)))

    return results


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/api/search")
async def search(
    make:         str           = Query(...),
    model:        str           = Query(...),
    year:         Optional[int] = Query(None),
    state:        Optional[str] = Query(None),
    max_price:    Optional[int] = Query(None),
    condition:    Optional[str] = Query(None, description="new | used | cpo"),
    drivetrain:   Optional[str] = Query(None, description="AWD | RWD | FWD"),
    max_mileage:  Optional[int] = Query(None, description="e.g. 30000"),
    color:        Optional[str] = Query(None, description="exterior color"),
    no_accidents: Optional[bool]= Query(None, description="true = 0 accidents only"),
    one_owner:    Optional[bool]= Query(None, description="true = single owner only"),
    year_from:    Optional[int] = Query(None, description="minimum model year, e.g. 2020"),
    min_price:    Optional[int] = Query(None, description="minimum listing price in USD"),
    body_style:   Optional[str] = Query(None, description="convertible | coupe | sedan | SUV | crossover | truck | hatchback | wagon"),
    zip_code:     Optional[str] = Query(None, description="5-digit US zip code for geo radius search"),
    radius_miles: Optional[int] = Query(None, description="Search radius in miles around zip (default 100)"),
    sort_by:      str           = Query("score", description="score | price | discount | newest | distance"),
    page:         int           = Query(1, ge=1),
    per_page:     int           = Query(20, ge=1, le=100),
):
    # Normalize model name (e.g. "TX350h" → "TX", "RX350" → "RX 350")
    model = canonicalize_model(make, model)

    # Zip-based geo search takes priority over state filter
    eff_state  = None if zip_code else state
    eff_radius = radius_miles or (100 if zip_code else None)

    key            = search_key(make, model, year, eff_state, zip_code, eff_radius)
    api_calls_used = 0
    synced_now     = False

    if is_stale(key):
        if DATA_PROVIDER == "none":
            raise HTTPException(500, "No data provider configured. Set AUTO_DEV_API_KEY.")
        log.info(f"Cache MISS / stale — fetching from {DATA_PROVIDER}: {key}")
        fresh, pages, resolved_model = fetch_with_retry(make, model, year, eff_state, zip_code, eff_radius)
        if not fresh:
            return {"results": [], "total": 0, "api_calls_used": pages,
                    "cached": False, "data_age_hours": 0,
                    "stats": {"min_price": None, "max_price": None, "avg_price": None,
                              "avg_discount": None, "best_discount": None, "top_states": []}}
        # If retry found a canonical name different from what was passed in, re-key
        if resolved_model != model:
            model = resolved_model
            key   = search_key(make, model, year, eff_state, zip_code, eff_radius)
        delta_sync(key, fresh, pages)
        api_calls_used = pages
        synced_now     = True
    else:
        log.info(f"Cache HIT — serving from DB: {key}")

    # Sort by distance automatically when geo mode is active
    eff_sort = "distance" if zip_code and sort_by == "score" else sort_by

    # Query DB (zero API calls)
    listings = query_inventory(key, max_price, condition, eff_state, eff_sort,
                               min_price=min_price or None,
                               drivetrain=drivetrain, max_mileage=max_mileage,
                               color=color, no_accidents=no_accidents, one_owner=one_owner,
                               year_from=year_from, body_style=body_style or None,
                               zip_code=zip_code, radius_miles=eff_radius)

    total = len(listings)
    start = (page - 1) * per_page
    paged = listings[start: start + per_page]

    prices    = [l["listing_price"] for l in listings if l.get("listing_price")]
    discounts = [l["discount_pct"]  for l in listings if l.get("discount_pct")]
    states_ct = {}
    for l in listings:
        s = l.get("dealer_state", "?")
        states_ct[s] = states_ct.get(s, 0) + 1

    # Data age
    conn = get_db()
    meta = conn.execute("SELECT last_synced_at FROM search_meta WHERE search_key=?", (key,)).fetchone()
    conn.close()
    age_hours = round((time.time() - meta["last_synced_at"]) / 3600, 1) if meta else 0

    return {
        "results":        paged,
        "total":          total,
        "page":           page,
        "per_page":       per_page,
        "pages":          math.ceil(total / per_page) if total else 0,
        "cached":         not synced_now,
        "api_calls_used": api_calls_used,
        "data_age_hours": age_hours,
        "stats": {
            "min_price":    min(prices)    if prices    else None,
            "max_price":    max(prices)    if prices    else None,
            "avg_price":    round(sum(prices) / len(prices)) if prices else None,
            "avg_discount": round(sum(discounts) / len(discounts), 1) if discounts else None,
            "best_discount":max(discounts) if discounts else None,
            "top_states":   sorted(states_ct.items(), key=lambda x: -x[1])[:5],
        },
    }


@app.get("/api/inventory/status")
async def inventory_status(
    make:  str           = Query(...),
    model: str           = Query(...),
    year:  Optional[int] = Query(None),
    state: Optional[str] = Query(None),
):
    """
    Full picture of what's in the DB for this combo:
    active listings, removed listings, price drops, and sync history.
    """
    key  = search_key(make, model, year, state)
    conn = get_db()

    meta = conn.execute(
        "SELECT * FROM search_meta WHERE search_key=?", (key,)
    ).fetchone()

    active_count = conn.execute(
        "SELECT COUNT(*) as n FROM inventory WHERE search_key=? AND status='active'", (key,)
    ).fetchone()["n"]

    removed_count = conn.execute(
        "SELECT COUNT(*) as n FROM inventory WHERE search_key=? AND status='removed'", (key,)
    ).fetchone()["n"]

    # Recently removed (last 7 days)
    cutoff = time.time() - 7 * 86400
    recently_removed = conn.execute("""
        SELECT vin, year, make, model, trim, listing_price, base_msrp,
               dealer_name, dealer_city, dealer_state, removed_at,
               first_seen_at, last_seen_at
        FROM inventory
        WHERE search_key=? AND status='removed' AND removed_at >= ?
        ORDER BY removed_at DESC LIMIT 20
    """, (key, cutoff)).fetchall()

    # Recent price drops (price went down since last refresh)
    price_drops = conn.execute("""
        SELECT vin, year, make, model, trim, listing_price, prev_listing_price,
               dealer_name, dealer_city, dealer_state
        FROM inventory
        WHERE search_key=? AND status='active'
          AND prev_listing_price IS NOT NULL
          AND listing_price < prev_listing_price
        ORDER BY (prev_listing_price - listing_price) DESC LIMIT 10
    """, (key,)).fetchall()

    # New listings (appeared in last 48h)
    recent_cutoff = time.time() - 48 * 3600
    new_listings = conn.execute("""
        SELECT vin, year, make, model, trim, listing_price, score,
               dealer_name, dealer_city, dealer_state, first_seen_at
        FROM inventory
        WHERE search_key=? AND status='active' AND first_seen_at >= ?
        ORDER BY first_seen_at DESC LIMIT 10
    """, (key, recent_cutoff)).fetchall()

    conn.close()

    age_hours = None
    if meta and meta["last_synced_at"]:
        age_hours = round((time.time() - meta["last_synced_at"]) / 3600, 1)

    return {
        "search_key":       key,
        "last_synced_at":   meta["last_synced_at"] if meta else None,
        "data_age_hours":   age_hours,
        "next_refresh_in":  max(0, round(CACHE_TTL_HOURS - (age_hours or 0), 1)),
        "active_count":     active_count,
        "removed_count":    removed_count,
        "total_ever_seen":  active_count + removed_count,
        "recently_removed": [dict(r) for r in recently_removed],
        "price_drops":      [dict(r) for r in price_drops],
        "new_listings":     [dict(r) for r in new_listings],
    }


@app.post("/api/refresh")
async def force_refresh(
    make:  str           = Query(...),
    model: str           = Query(...),
    year:  Optional[int] = Query(None),
    state: Optional[str] = Query(None),
):
    """
    Force a sync regardless of cache TTL.
    Use sparingly — costs ~5 API calls per call.
    """
    if DATA_PROVIDER == "none":
        raise HTTPException(500, "No data provider configured. Set AUTO_DEV_API_KEY.")

    key = search_key(make, model, year, state)
    log.info(f"Force refresh: {key}")
    fresh, pages = fetch_from_api(make, model, year, state)
    if not fresh:
        return {"ok": False, "message": "No listings returned from API", "api_calls": pages}

    active_count, removed_count = delta_sync(key, fresh, pages)
    return {
        "ok":            True,
        "api_calls":     pages,
        "fresh_fetched": len(fresh),
        "active_now":    active_count,
        "newly_removed": removed_count,
    }


PRE_TRACKING_CALLS = 36   # pipeline + curl tests before DB tracking started (auto.dev shows 46 total)

@app.get("/api/usage")
async def api_usage():
    total     = get_total_api_calls()
    grand_total = total + PRE_TRACKING_CALLS
    remaining = max(0, 1000 - grand_total)
    conn = get_db()
    searches = conn.execute(
        "SELECT search_key, last_synced_at, api_calls_used, active_count, removed_count FROM search_meta ORDER BY last_synced_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return {
        "total_used":   grand_total,
        "remaining":    remaining,
        "quota":        1000,
        "ttl_hours":    CACHE_TTL_HOURS,
        "miss_threshold": MISS_THRESHOLD,
        "cached_combos": [dict(r) for r in searches],
    }


@app.get("/api/filters")
async def available_filters(
    make:  str           = Query(...),
    model: str           = Query(...),
    year:  Optional[int] = Query(None),
    state: Optional[str] = Query(None),
):
    """Return distinct filter values available for this search combo."""
    key  = search_key(make, model, year, state)
    conn = get_db()
    base = "FROM inventory WHERE search_key=? AND status='active' AND listing_price IS NOT NULL AND listing_price > 0"

    drivetrains = [r[0] for r in conn.execute(
        f"SELECT DISTINCT drivetrain {base} AND drivetrain IS NOT NULL ORDER BY drivetrain", (key,)
    ).fetchall()]

    colors = [r[0] for r in conn.execute(
        f"SELECT DISTINCT exterior_color {base} AND exterior_color IS NOT NULL ORDER BY exterior_color", (key,)
    ).fetchall()]

    mileage_stats = conn.execute(
        f"SELECT MIN(mileage), MAX(mileage), AVG(mileage) {base}", (key,)
    ).fetchone()

    year_stats = conn.execute(
        f"SELECT MIN(year), MAX(year) {base} AND year IS NOT NULL", (key,)
    ).fetchone()

    accident_counts = conn.execute(
        f"SELECT accidents, COUNT(*) as n {base} GROUP BY accidents ORDER BY accidents", (key,)
    ).fetchall()

    owner_counts = conn.execute(
        f"SELECT one_owner, COUNT(*) as n {base} GROUP BY one_owner", (key,)
    ).fetchall()

    conn.close()
    return {
        "drivetrains": drivetrains,
        "colors": colors,
        "mileage_min": mileage_stats[0] if mileage_stats else 0,
        "mileage_max": mileage_stats[1] if mileage_stats else 0,
        "mileage_avg": round(mileage_stats[2]) if mileage_stats and mileage_stats[2] else 0,
        "accident_breakdown": {str(r[0]): r[1] for r in accident_counts},
        "owner_breakdown": {str(r[0]): r[1] for r in owner_counts},
        "year_min": year_stats[0] if year_stats else None,
        "year_max": year_stats[1] if year_stats else None,
    }


@app.get("/api/makes")
async def get_makes():
    return {"makes": [
        "Acura","Audi","BMW","Buick","Cadillac","Chevrolet","Chrysler","Dodge",
        "Ford","Genesis","GMC","Honda","Hyundai","Infiniti","Jeep","Kia",
        "Land Rover","Lexus","Lincoln","Mazda","Mercedes-Benz","MINI",
        "Mitsubishi","Nissan","Porsche","RAM","Rivian","Subaru","Tesla",
        "Toyota","Volkswagen","Volvo"
    ]}


# ═══════════════════════════════════════════════════════════════
# AI ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def ai_chat(
    query:         str           = Query(..., description="Natural language search query or car question"),
    context_make:  Optional[str] = Query(None, description="Make of currently visible listings (for QA context)"),
    context_model: Optional[str] = Query(None, description="Model of currently visible listings (for QA context)"),
):
    """
    Agent 1 (search): Natural language → search params → results.
    Agent 4 (QA): General car questions answered in context of current listings.

    Routes automatically: if no make/model can be extracted, answers as a concierge QA.
    """
    try:
        from ai_engine import answer_car_question
    except Exception as e:
        log.error(f"AI engine import failed: {e}")
        raise HTTPException(503, f"AI engine unavailable: {e}")

    # Two-stage pipeline: Stage 1 normalises the raw query (free-form LLM),
    # Stage 2 extracts structured params via tool_use on the clean text.
    enriched_query = query   # fallback if pipeline unavailable
    pipeline_timing: dict = {}
    try:
        from agents import run_pipeline
        pipeline_state = run_pipeline(query, context_make, context_model)
        intent         = pipeline_state["intent"]
        enriched_query = pipeline_state["enriched_query"] or query
        pipeline_timing = pipeline_state.get("timing", {})
    except Exception as e:
        log.warning(f"LangGraph pipeline failed, falling back to direct extraction: {e}")
        try:
            from ai_engine import extract_search_intent
            intent = extract_search_intent(query)
        except Exception as e2:
            log.error(f"Intent extraction failed: {e2}")
            return {"error": str(e2), "intent": {}, "results": [], "total": 0}

    if not intent.get("make") or not intent.get("model"):
        # QA mode — answer as an automotive concierge
        listings_context = None
        if context_make and context_model:
            try:
                key = search_key(context_make, context_model, None, None)
                conn = get_db()
                top = conn.execute("""
                    SELECT year, make, model, trim, listing_price, mileage, dealer_state, score,
                           discount_pct, is_used, is_cpo, accidents
                    FROM inventory WHERE search_key=? AND status='active' AND listing_price > 0
                    ORDER BY score DESC LIMIT 6
                """, (key,)).fetchall()
                conn.close()
                if top:
                    rows = [
                        f"{r['year']} {r['make']} {r['model']}{' '+r['trim'] if r['trim'] else ''} "
                        f"${r['listing_price']:,.0f} ({r['mileage']:,} mi, {r['dealer_state']}, "
                        f"{'CPO' if r['is_cpo'] else 'New' if not r['is_used'] else 'Used'}"
                        f"{', '+str(r['accidents'])+' accident(s)' if r['accidents'] else ', clean'})"
                        for r in top
                    ]
                    listings_context = f"User is currently viewing {context_make} {context_model} listings. Top results by deal score:\n" + "\n".join(rows)
            except Exception as e:
                log.warning(f"Could not build listings context: {e}")

        try:
            answer = answer_car_question(query, listings_context)
        except Exception as e:
            log.error(f"Concierge QA failed: {e}")
            answer = "I wasn't able to answer that right now. Please try again."

        return {
            "is_qa":   True,
            "answer":  answer,
            "intent":  intent,
            "results": [],
            "total":   0,
        }

    make          = intent.get("make", "")
    model         = intent.get("model", "")
    # Normalize model name before any lookups (e.g. "TX350h" → "TX")
    model         = canonicalize_model(make, model)
    year          = intent.get("year")
    state         = intent.get("state") or None
    max_price     = intent.get("max_price")
    min_price     = intent.get("min_price")
    year_from     = intent.get("year_from")
    condition     = intent.get("condition") or None
    drivetrain    = intent.get("drivetrain") or None
    max_mileage   = intent.get("max_mileage")
    color         = intent.get("color") or None
    no_acc        = intent.get("no_accidents", False)
    one_own       = intent.get("one_owner", False)
    body_style    = intent.get("body_style") or None
    zip_code      = intent.get("zip_code") or None
    radius_miles  = intent.get("radius_miles") or None
    brand_category = intent.get("brand_category") or None

    # Zip-based geo search takes priority over state filter
    eff_state  = None if zip_code else state
    eff_radius = radius_miles or (100 if zip_code else None)

    # ── BRAND CATEGORY path (e.g. "European cars under $50k") ──────────────
    if brand_category and brand_category in BRAND_CATEGORIES and not intent.get("brand_was_specified"):
        category_makes_models = BRAND_CATEGORIES[brand_category]
        category_makes = list({m for m, _ in category_makes_models})

        listings = query_inventory_by_makes(
            category_makes, max_price=max_price, condition=condition, state=eff_state,
            sort_by="score", max_mileage=max_mileage, no_accidents=no_acc,
            one_owner=one_own, drivetrain=drivetrain,
            zip_code=zip_code, radius_miles=eff_radius,
            min_price=min_price, year_from=year_from,
        )

        prices    = [l["listing_price"] for l in listings if l.get("listing_price")]
        discounts = [l["discount_pct"] for l in listings if l.get("discount_pct")]

        # Build suggested_alternatives from the full category list
        # (de-duplicated by make) so the user can drill into any specific brand
        seen_makes: set = set()
        alts = []
        for m, mdl in category_makes_models:
            if m not in seen_makes:
                seen_makes.add(m)
                alts.append({"make": m, "model": mdl, "reason": f"Popular {brand_category.title()} model"})
        intent["suggested_alternatives"] = alts

        intent_display = {k: v for k, v in intent.items()
                          if k not in ("suggested_alternatives",) and v not in (None, "", [], False)}

        tool_calls = [
            {
                "name": "understand_query",
                "label": "Stage 1 — Query Understanding",
                "model": "claude-haiku-4-5",
                "icon": "🔍",
                "input": {"query": query},
                "output": {"enriched": enriched_query},
                "duration_ms": pipeline_timing.get("understand_ms"),
            },
            {
                "name": "extract_search_intent",
                "label": "Stage 2 — Intent Extraction",
                "model": "claude-haiku-4-5",
                "icon": "🎯",
                "input": {"query": enriched_query},
                "output": intent_display,
                "duration_ms": pipeline_timing.get("extract_ms"),
            },
            {
                "name": "query_inventory_by_makes",
                "label": f"Database Search — {brand_category.title()} brands",
                "model": "SQLite",
                "icon": "",
                "input": {"makes": category_makes, **({"max_price": max_price} if max_price else {})},
                "output": {
                    "results_found": len(listings),
                    "returned": min(20, len(listings)),
                    "data_source": "local cache (no API calls — use chips below to fetch live data per brand)",
                },
                "duration_ms": None,
            },
        ]

        return {
            "intent":   intent,
            "results":  listings[:20],
            "total":    len(listings),
            "api_calls": 0,
            "brand_was_specified": False,
            "brand_category": brand_category,
            "suggested_alternatives": alts,
            "tool_calls": tool_calls,
            "stats": {
                "avg_price":    round(sum(prices)/len(prices)) if prices else None,
                "best_discount":max(discounts) if discounts else None,
            }
        }

    # ── SINGLE BRAND path (normal search) ────────────────────────────────────
    key = search_key(make, model, year, eff_state, zip_code, eff_radius)
    api_calls = 0
    if is_stale(key):
        if DATA_PROVIDER == "none":
            return {"error": "No data provider configured. Set AUTO_DEV_API_KEY.", "intent": intent,
                    "results": [], "total": 0}
        fresh, pages, resolved_model = fetch_with_retry(make, model, year, eff_state, zip_code, eff_radius)
        if fresh:
            # Re-key if retry found a canonical model name (e.g. 'TX350h' → 'TX')
            if resolved_model != model:
                model = resolved_model
                key   = search_key(make, model, year, eff_state, zip_code, eff_radius)
            delta_sync(key, fresh, pages)
        api_calls = pages

    db_params = {
        "make": make, "model": model,
        **({"year": year} if year else {}),
        **({"state": eff_state} if eff_state else {}),
        **({"zip_code": zip_code} if zip_code else {}),
        **({"radius_miles": eff_radius} if eff_radius else {}),
        **({"max_price": max_price} if max_price else {}),
        **({"condition": condition} if condition else {}),
        **({"drivetrain": drivetrain} if drivetrain else {}),
        **({"max_mileage": max_mileage} if max_mileage else {}),
        **({"color": color} if color else {}),
        **({"no_accidents": True} if no_acc else {}),
        **({"one_owner": True} if one_own else {}),
        **({"body_style": body_style} if body_style else {}),
        **({"min_price": min_price} if min_price else {}),
        **({"year_from": year_from} if year_from else {}),
    }

    listings = query_inventory(key, max_price, condition, eff_state, "score",
                               drivetrain=drivetrain, max_mileage=max_mileage,
                               color=color, no_accidents=no_acc, one_owner=one_own,
                               body_style=body_style,
                               zip_code=zip_code, radius_miles=eff_radius,
                               min_price=min_price, year_from=year_from)

    # Safety fallback: if a condition filter wiped all results but unfiltered data exists,
    # retry without condition so the user always sees something (they can re-filter via chips)
    if not listings and condition:
        fallback = query_inventory(key, max_price, None, eff_state, "score",
                                   drivetrain=drivetrain, max_mileage=max_mileage,
                                   color=color, no_accidents=no_acc, one_owner=one_own,
                                   body_style=body_style,
                                   zip_code=zip_code, radius_miles=eff_radius,
                                   min_price=min_price, year_from=year_from)
        if fallback:
            log.info(f"Condition filter '{condition}' yielded 0 results — returning unfiltered ({len(fallback)} listings)")
            listings = fallback
            condition = None   # clear so intent reflects reality

    prices    = [l["listing_price"] for l in listings if l.get("listing_price")]
    discounts = [l["discount_pct"] for l in listings if l.get("discount_pct")]

    # Build readable intent output for tool call display (exclude raw arrays)
    intent_display = {k: v for k, v in intent.items()
                      if k not in ("suggested_alternatives",) and v not in (None, "", [], False)}

    tool_calls = [
        {
            "name": "understand_query",
            "label": "Stage 1 — Query Understanding",
            "model": "claude-haiku-4-5",
            "icon": "🔍",
            "input": {"query": query},
            "output": {"enriched": enriched_query},
            "duration_ms": pipeline_timing.get("understand_ms"),
        },
        {
            "name": "extract_search_intent",
            "label": "Stage 2 — Intent Extraction",
            "model": "claude-haiku-4-5",
            "icon": "🎯",
            "input": {"query": enriched_query},
            "output": intent_display,
            "duration_ms": pipeline_timing.get("extract_ms"),
        },
        {
            "name": "query_inventory",
            "label": "Database Search",
            "model": "SQLite",
            "icon": "",
            "input": db_params,
            "output": {
                "results_found": len(listings),
                "returned": min(20, len(listings)),
                "data_source": f"fresh from {DATA_PROVIDER}" if api_calls else "cached (0 API calls)",
            },
            "duration_ms": None,
        },
    ]

    return {
        "intent":   intent,
        "results":  listings[:20],
        "total":    len(listings),
        "api_calls": api_calls,
        "brand_was_specified": intent.get("brand_was_specified", True),
        "brand_category": brand_category,
        "suggested_alternatives": intent.get("suggested_alternatives", []),
        "tool_calls": tool_calls,
        "stats": {
            "avg_price":    round(sum(prices)/len(prices)) if prices else None,
            "best_discount":max(discounts) if discounts else None,
        }
    }


@app.get("/api/analyze/{vin}")
async def analyze_listing(vin: str):
    """
    Agent 2: Deep AI deal analysis for a specific VIN.
    Returns: recommendation, headline, price assessment, negotiation tips,
             green flags, red flags, bottom line.
    Results are cached in DB for 24h to save API calls.
    """
    try:
        from ai_engine import analyze_deal
    except Exception as e:
        raise HTTPException(503, f"AI engine unavailable: {e}")

    conn = get_db()
    listing = conn.execute("SELECT * FROM inventory WHERE vin=?", (vin,)).fetchone()
    if not listing:
        conn.close()
        raise HTTPException(404, f"VIN {vin} not found in inventory")
    listing = dict(listing)

    # Serve cached analysis if < 24h old
    if listing.get("ai_analyzed_at") and listing.get("ai_analysis"):
        age = time.time() - listing["ai_analyzed_at"]
        if age < 86400:
            conn.close()
            return {"vin": vin, "analysis": json.loads(listing["ai_analysis"]), "cached": True}

    # Market stats for this search combo
    key = listing["search_key"]
    stats_row = conn.execute("""
        SELECT MIN(listing_price) as min_p, MAX(listing_price) as max_p,
               AVG(listing_price) as avg_p, AVG(discount_pct) as avg_d, COUNT(*) as total
        FROM inventory WHERE search_key=? AND status='active' AND listing_price > 0
    """, (key,)).fetchone()

    market_stats = {
        "min_price":    stats_row["min_p"],
        "max_price":    stats_row["max_p"],
        "avg_price":    round(stats_row["avg_p"]) if stats_row["avg_p"] else None,
        "avg_discount": round(stats_row["avg_d"], 1) if stats_row["avg_d"] else 0,
        "total":        stats_row["total"],
    }

    # 5 most similar listings by price proximity and mileage range
    mileage = listing.get("mileage", 0) or 0
    similar = [dict(r) for r in conn.execute("""
        SELECT listing_price, mileage, exterior_color, dealer_city, dealer_state
        FROM inventory
        WHERE search_key=? AND status='active' AND vin!=? AND listing_price > 0
          AND mileage BETWEEN ? AND ?
        ORDER BY ABS(listing_price - ?) LIMIT 5
    """, (key, vin, max(0, mileage-20000), mileage+20000,
          listing.get("listing_price", 0) or 0)).fetchall()]
    conn.close()

    log.info(f"Running AI deal analysis for VIN {vin}")
    try:
        analysis = analyze_deal(listing, market_stats, similar)
    except Exception as e:
        log.error(f"analyze_deal failed for VIN {vin}: {e}")
        raise HTTPException(500, f"Deal analysis failed: {e}")

    # Cache analysis result
    conn2 = get_db()
    conn2.execute(
        "UPDATE inventory SET ai_analysis=?, ai_analyzed_at=? WHERE vin=?",
        (json.dumps(analysis), time.time(), vin)
    )
    conn2.commit()
    conn2.close()

    return {"vin": vin, "analysis": analysis, "cached": False}


@app.get("/api/market-intel")
async def market_intelligence(
    make:  str           = Query(...),
    model: str           = Query(...),
    year:  Optional[int] = Query(None),
    state: Optional[str] = Query(None),
):
    """
    Agent 3: AI-generated Market Pulse — narrative insights from your inventory data.
    Zero extra API calls: reads only from local SQLite cache.
    """
    try:
        from ai_engine import generate_market_pulse
    except Exception as e:
        raise HTTPException(503, f"AI engine unavailable: {e}")

    # Query directly by make/model columns rather than search_key.
    # This avoids mismatches between zip-based vs state-based search_keys
    # and handles partial model names (user types "NX 350", DB stores "NX"):
    #   LOWER(model) LIKE 'nx 350%'  → matches "NX 350"
    #   LOWER('nx 350') LIKE 'nx%'   → matches DB model "NX"  ← cross-direction
    conn = get_db()
    year_clause = "AND year = ?" if year else ""
    extra = [year] if year else []

    def _mp(sql, *p):
        return conn.execute(sql, list(p) + extra).fetchone()

    stats_row = _mp(f"""
        SELECT MIN(listing_price) as min_p, MAX(listing_price) as max_p,
               AVG(listing_price) as avg_p, AVG(discount_pct) as avg_d,
               MAX(discount_pct) as best_d, COUNT(*) as total
        FROM inventory
        WHERE LOWER(make) = LOWER(?)
          AND (LOWER(model) LIKE LOWER(?) || '%' OR LOWER(?) LIKE LOWER(model) || '%')
          {year_clause}
          AND status='active' AND listing_price > 0
    """, make, model, model)

    top_states = [(r[0], r[1]) for r in conn.execute(f"""
        SELECT dealer_state, COUNT(*) as n FROM inventory
        WHERE LOWER(make) = LOWER(?)
          AND (LOWER(model) LIKE LOWER(?) || '%' OR LOWER(?) LIKE LOWER(model) || '%')
          {year_clause}
          AND status='active'
        GROUP BY dealer_state ORDER BY n DESC LIMIT 5
    """, [make, model, model] + extra).fetchall()]

    price_drops = conn.execute(f"""
        SELECT COUNT(*) as n FROM inventory
        WHERE LOWER(make) = LOWER(?)
          AND (LOWER(model) LIKE LOWER(?) || '%' OR LOWER(?) LIKE LOWER(model) || '%')
          {year_clause}
          AND status='active'
          AND prev_listing_price IS NOT NULL AND listing_price < prev_listing_price
    """, [make, model, model] + extra).fetchone()["n"]

    new_count = conn.execute(f"""
        SELECT COUNT(*) as n FROM inventory
        WHERE LOWER(make) = LOWER(?)
          AND (LOWER(model) LIKE LOWER(?) || '%' OR LOWER(?) LIKE LOWER(model) || '%')
          {year_clause}
          AND status='active' AND first_seen_at >= ?
    """, [make, model, model] + extra + [time.time() - 48*3600]).fetchone()["n"]

    conn.close()

    stats = {
        "min_price":       stats_row["min_p"],
        "max_price":       stats_row["max_p"],
        "avg_price":       round(stats_row["avg_p"]) if stats_row["avg_p"] else None,
        "avg_discount":    round(stats_row["avg_d"], 1) if stats_row["avg_d"] else 0,
        "best_discount":   round(stats_row["best_d"], 1) if stats_row["best_d"] else 0,
        "total":           stats_row["total"],
        "top_states":      top_states,
        "new_count":       new_count,
        "price_drop_count":price_drops,
    }

    try:
        pulse = generate_market_pulse(stats, make, model, year)
    except Exception as e:
        log.error(f"generate_market_pulse failed: {e}")
        return {"pulse": f"Market analysis unavailable: {e}", "stats": stats}

    # pulse is now a structured dict (supply, pricing, momentum, verdict, market_score)
    if isinstance(pulse, dict):
        return {"insights": pulse, "stats": stats}
    # Fallback: legacy plain-text string
    return {"pulse": pulse, "stats": stats}


# ── Serve frontend ───────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/", response_class=FileResponse)
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"message": "AI Car Deal Finder API — visit /docs"})
