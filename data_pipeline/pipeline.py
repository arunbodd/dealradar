"""
AI Car Deal Finder — Data Pipeline (auto.dev)
=============================================
Fetches vehicle listings from auto.dev API, normalizes the data,
computes deal scores, and outputs ranked results.

Usage:
    python pipeline.py --make BMW --model X5
    python pipeline.py --make Toyota --model Camry --year 2024 --state CA
    python pipeline.py --make BMW --model X5 --zip 30047 --distance 100

Setup:
    pip install requests python-dotenv
"""

import os
import json
import time
import logging
import argparse
import math
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
AUTO_DEV_API_KEY = os.getenv("AUTO_DEV_API_KEY", "")
AUTO_DEV_BASE    = "https://api.auto.dev"


# ─────────────────────────────────────────────────────────────
# DEAL SCORING
# Weighted formula:
#   Score = (Discount% × 0.40) + (History × 0.25) + (Availability × 0.20) + (CPO × 0.15)
# ─────────────────────────────────────────────────────────────
def compute_deal_score(listing: dict) -> dict:
    """
    Compute a 0–1 deal score for a normalized listing.
    Higher = better deal.
    """
    msrp     = listing.get("base_msrp") or 0
    price    = listing.get("listing_price") or msrp
    is_used  = listing.get("is_used", True)
    is_cpo   = listing.get("is_cpo", False)
    history  = listing.get("history") or {}
    accidents = history.get("accidentCount", 0) if history else 0
    one_owner = history.get("oneOwner", False) if history else False

    # 1. Discount score: how much below MSRP (cap at 20% = 1.0)
    if msrp > 0 and price < msrp:
        discount_pct   = (msrp - price) / msrp * 100
        discount_score = min(discount_pct / 20.0, 1.0)
    else:
        discount_pct   = 0
        discount_score = 0.0

    # 2. History score (used cars only)
    if not is_used:
        history_score = 1.0   # new car, perfect history
    elif accidents == 0 and one_owner:
        history_score = 1.0
    elif accidents == 0:
        history_score = 0.75
    elif accidents == 1:
        history_score = 0.4
    else:
        history_score = 0.1

    # 3. Availability / freshness score (online = 1.0)
    availability_score = 1.0 if listing.get("online", True) else 0.0

    # 4. CPO premium score
    cpo_score = 1.0 if is_cpo else 0.5

    # Weighted composite
    score = (
        discount_score     * 0.40 +
        history_score      * 0.25 +
        availability_score * 0.20 +
        cpo_score          * 0.15
    )

    return {
        "score":              round(score, 4),
        "discount_pct":       round(discount_pct, 2),
        "discount_score":     round(discount_score, 4),
        "history_score":      round(history_score, 4),
        "availability_score": round(availability_score, 4),
        "cpo_score":          round(cpo_score, 4),
    }


def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    """Compute straight-line distance between two GPS points in miles."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─────────────────────────────────────────────────────────────
# AUTO.DEV API CLIENT
# ─────────────────────────────────────────────────────────────
class AutoDevClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict) -> dict:
        params["apiKey"] = self.api_key
        resp = self.session.get(f"{AUTO_DEV_BASE}{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def search_listings(self, make: str, model: str,
                        year: int = None, state: str = None,
                        zip_code: str = None, distance: int = None,
                        price_max: float = None,
                        page: int = 1, limit: int = 100) -> dict:
        """
        Search active vehicle listings.
        Docs: https://docs.auto.dev/v2/products/vehicle-listings
        """
        params = {
            "vehicle.make":  make,
            "vehicle.model": model,
            "page":          page,
            "limit":         limit,
        }
        if year:
            params["vehicle.year"] = year
        if state:
            params["retailListing.state"] = state
        if zip_code:
            params["zip"] = zip_code
        if distance:
            params["distance"] = distance
        if price_max:
            params["retailListing.price"] = f"1-{int(price_max)}"

        return self._get("/listings", params)

    def get_vin_details(self, vin: str) -> dict:
        """Decode a VIN for full spec detail."""
        return self._get(f"/vin/{vin}", {})

    def get_specs(self, vin: str) -> dict:
        """Get detailed specs for a VIN."""
        return self._get(f"/specs/{vin}", {})


# ─────────────────────────────────────────────────────────────
# DATA NORMALIZER — maps auto.dev fields → our schema
# ─────────────────────────────────────────────────────────────
def normalize_listing(raw: dict) -> dict:
    v   = raw.get("vehicle", {})
    rl  = raw.get("retailListing") or {}
    loc = raw.get("location", [None, None])  # [lng, lat]

    price     = rl.get("price")
    base_msrp = v.get("baseMsrp")
    discount  = (base_msrp - price) if (base_msrp and price and base_msrp > price) else None

    return {
        # Identity
        "vin":             raw.get("vin"),
        "listing_url":     raw.get("@id"),
        "online":          raw.get("online", True),
        "created_at_src":  raw.get("createdAt"),

        # Vehicle
        "year":            v.get("year"),
        "make":            v.get("make"),
        "model":           v.get("model"),
        "trim":            v.get("trim"),
        "series":          v.get("series"),
        "body_style":      v.get("bodyStyle"),
        "type":            v.get("type"),
        "drivetrain":      v.get("drivetrain"),
        "engine":          v.get("engine"),
        "transmission":    v.get("transmission"),
        "fuel":            v.get("fuel"),
        "cylinders":       v.get("cylinders"),
        "doors":           v.get("doors"),
        "seats":           v.get("seats"),
        "exterior_color":  v.get("exteriorColor"),
        "interior_color":  v.get("interiorColor"),
        "base_msrp":       base_msrp,
        "base_invoice":    v.get("baseInvoice"),

        # Retail listing
        "listing_price":   price,
        "discount_amount": discount,
        "mileage":         rl.get("miles", 0),
        "is_used":         rl.get("used", True),
        "is_cpo":          rl.get("cpo", False),
        "photo_count":     rl.get("photoCount", 0),
        "primary_image":   rl.get("primaryImage"),
        "carfax_url":      rl.get("carfaxUrl"),
        "vdp_id":          rl.get("vdp"),

        # Dealer / location
        "dealer_name":     rl.get("dealer"),
        "dealer_city":     rl.get("city"),
        "dealer_state":    rl.get("state"),
        "dealer_zip":      rl.get("zip"),
        "dealer_lng":      loc[0] if len(loc) > 1 else None,
        "dealer_lat":      loc[1] if len(loc) > 1 else None,

        # History (used cars)
        "history":         raw.get("history"),
    }


# ─────────────────────────────────────────────────────────────
# PIPELINE RUNNER
# ─────────────────────────────────────────────────────────────
def run_pipeline(make: str, model: str,
                 year: int = None, state: str = None,
                 zip_code: str = None, distance: int = None,
                 price_max: float = None,
                 max_pages: int = 5, dry_run: bool = False,
                 output_file: str = "results.json"):
    """
    Full ETL: Fetch → Normalize → Score → Sort → Output.
    """
    if not AUTO_DEV_API_KEY:
        log.error("AUTO_DEV_API_KEY is not set. Add it to your .env file.")
        return []

    client = AutoDevClient(AUTO_DEV_API_KEY)
    label  = f"{year or ''} {make} {model}".strip()
    log.info(f"Starting pipeline: {label}"
             + (f" | state={state}" if state else "")
             + (f" | zip={zip_code} ±{distance}mi" if zip_code else "")
             + (f" | max_price=${price_max:,.0f}" if price_max else ""))

    all_results = []

    for page in range(1, max_pages + 1):
        log.info(f"  Fetching page {page}/{max_pages}...")
        try:
            data = client.search_listings(
                make, model, year=year, state=state,
                zip_code=zip_code, distance=distance,
                price_max=price_max, page=page, limit=100
            )
        except requests.HTTPError as e:
            log.error(f"  API error on page {page}: {e}")
            break

        listings = data.get("data", [])
        if not listings:
            log.info(f"  No more listings at page {page}.")
            break

        for raw in listings:
            norm   = normalize_listing(raw)
            scores = compute_deal_score(norm)
            norm.update(scores)
            all_results.append(norm)

        log.info(f"  {len(all_results)} listings collected so far.")
        time.sleep(0.25)

    if not all_results:
        log.warning("No listings found. Try broadening your search parameters.")
        return []

    # Sort by composite score descending
    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

    if dry_run:
        _print_top_deals(all_results, label)

    # Save full results to JSON
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    log.info(f"  {len(all_results)} ranked listings saved to {output_file}")

    return all_results


def _print_top_deals(results: list, label: str, top_n: int = 10):
    print(f"\n{'='*75}")
    print(f"  TOP {top_n} DEALS — {label}")
    print(f"{'='*75}")
    print(f"{'#':<3} {'Dealer':<32} {'City,ST':<18} {'Price':>8} {'MSRP':>8} {'Disc%':>6} {'Score':>6} {'Cond'}")
    print(f"{'-'*75}")

    for i, r in enumerate(results[:top_n], 1):
        price   = r.get("listing_price")
        msrp    = r.get("base_msrp")
        disc    = r.get("discount_pct", 0)
        score   = r.get("score", 0)
        dealer  = (r.get("dealer_name") or "")[:31]
        city_st = f"{r.get('dealer_city','')}, {r.get('dealer_state','')}"
        cond    = "CPO" if r.get("is_cpo") else ("New" if not r.get("is_used") else "Used")
        p_str   = f"${price:,.0f}" if price else "N/A"
        m_str   = f"${msrp:,.0f}"  if msrp  else "N/A"
        print(f"{i:<3} {dealer:<32} {city_st:<18} {p_str:>8} {m_str:>8} {disc:>5.1f}% {score:>5.3f} {cond}")

    print(f"{'='*75}")
    best = results[0]
    savings = (best.get("base_msrp") or 0) - (best.get("listing_price") or 0)
    print(f"\n  BEST DEAL: {best.get('year')} {best.get('make')} {best.get('model')} {best.get('trim') or ''}")
    print(f"  Dealer :  {best.get('dealer_name')} — {best.get('dealer_city')}, {best.get('dealer_state')}")
    print(f"  Price  :  ${best.get('listing_price'):,.0f}  (MSRP: ${best.get('base_msrp'):,.0f})")
    print(f"  Saving :  ${savings:,.0f}  ({best.get('discount_pct',0):.1f}% off MSRP)")
    print(f"  VIN    :  {best.get('vin')}")
    print(f"  Carfax :  {best.get('carfax_url') or 'N/A'}")
    print(f"  Score  :  {best.get('score'):.4f}\n")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Car Deal Finder — auto.dev pipeline")
    parser.add_argument("--make",       required=True,          help="Car make (e.g. BMW)")
    parser.add_argument("--model",      required=True,          help="Car model (e.g. X5)")
    parser.add_argument("--year",       type=int,               help="Model year (e.g. 2024)")
    parser.add_argument("--state",                              help="Filter by state code (e.g. GA)")
    parser.add_argument("--zip",                                help="Center search on ZIP code")
    parser.add_argument("--distance",   type=int,               help="Radius in miles from ZIP")
    parser.add_argument("--max-price",  type=float,             help="Maximum listing price")
    parser.add_argument("--pages",      type=int, default=3,    help="Max API pages (default: 3)")
    parser.add_argument("--output",     default="results.json", help="Output JSON file")
    parser.add_argument("--dry-run",    action="store_true",    help="Print top deals to console")
    args = parser.parse_args()

    run_pipeline(
        make=args.make,
        model=args.model,
        year=args.year,
        state=args.state,
        zip_code=args.zip,
        distance=args.distance,
        price_max=args.max_price,
        max_pages=args.pages,
        dry_run=args.dry_run,
        output_file=args.output,
    )
