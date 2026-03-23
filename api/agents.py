"""
DealRadar — LangGraph Two-Stage AI Pipeline
============================================
Stage 1 · Query Understanding  (claude-haiku-4-5, free-form)
  Raw user query → normalized, enriched description.
  Handles: city→state, price shorthands, feature→car-type mapping,
           brand spelling, year ranges, colloquial terms.
  This is a pure LLM pass — no tool schema, just plain text output.

Stage 2 · Structured Extraction  (claude-haiku-4-5, tool_use)
  Enriched description → structured JSON search params.
  Works on clean, unambiguous input so extraction is reliable.

LangSmith tracing is auto-enabled when LANGSMITH_API_KEY env var is set.
Set LANGCHAIN_PROJECT=dealradar in env for project grouping.
"""

import os, time, logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

log = logging.getLogger(__name__)

# ── LangSmith — optional, degrades gracefully ────────────────
try:
    from langsmith import traceable
    _LANGSMITH_AVAILABLE = True
except ImportError:
    _LANGSMITH_AVAILABLE = False
    def traceable(_func=None, *, name=None, **kwargs):  # no-op decorator
        def decorator(func): return func
        return decorator(_func) if _func else decorator

_LANGSMITH_ENABLED = _LANGSMITH_AVAILABLE and bool(
    os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY"))

if _LANGSMITH_ENABLED:
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT",
                          os.getenv("LANGCHAIN_PROJECT", "dealradar"))
    log.info("LangSmith tracing active (project=%s)",
             os.getenv("LANGCHAIN_PROJECT", "dealradar"))


# ── State ─────────────────────────────────────────────────────
class PipelineState(TypedDict):
    raw_query:      str
    context_make:   Optional[str]
    context_model:  Optional[str]
    enriched_query: str           # output of Stage 1
    intent:         dict          # output of Stage 2
    timing:         dict          # {"understand_ms": N, "extract_ms": N}


# ── Stage 1: Query Understanding ─────────────────────────────
_UNDERSTAND_SYSTEM = """\
You are a car search query normalizer. Convert the raw user query into a \
clear, precise description ready for structured extraction.

Output ONLY the normalized description (2–3 sentences max). \
No preamble, no explanation, no quotes around the output.

Apply these normalizations:

PRICES
  "50k" → "$50,000"   "under 45" → "under $45,000"   "45 grand" → "$45,000"
  "around 45k" → "under $47,000"   "between 30–50k" → "between $30,000 and $50,000"
  "over 30k" / "at least 30" → "over $30,000"
  Bare numbers under 500 assume thousands: "under 45" → "under $45,000"

YEARS  (current year = 2026)
  "recent" / "last 2 years" → "2024 or newer"
  "last 3 years" → "2023 or newer"
  "2022+" / "2022 and up" → "2022 or newer"
  "a 2022" / "2022 model" → "year 2022" (exact, not a range)

LOCATIONS — convert city/region to state abbreviation:
  Atlanta / Southeast → Georgia (GA)
  NYC / Manhattan / Brooklyn / Long Island / NJ → New York (NY)
  LA / SoCal / Hollywood / Orange County / Long Beach → California (CA)
  Bay Area / Silicon Valley / San Jose / Oakland / Palo Alto → California (CA)
  San Diego / Riverside / Inland Empire → California (CA)
  Dallas / DFW / Fort Worth / Plano / Frisco / Austin / Houston / San Antonio → Texas (TX)
  Miami / Fort Lauderdale / Boca Raton / Orlando / Tampa / South Florida → Florida (FL)
  Chicago / Chicagoland / Naperville → Illinois (IL)
  Seattle / Tacoma / Bellevue / Pacific Northwest → Washington (WA)
  Denver / Boulder / Colorado Springs → Colorado (CO)
  Phoenix / Scottsdale / Tempe / Mesa → Arizona (AZ)
  Boston / Cambridge / Worcester / New England → Massachusetts (MA)
  Charlotte / Raleigh / Durham / Research Triangle → North Carolina (NC)
  Nashville / Memphis / Tennessee → Tennessee (TN)
  Detroit / Ann Arbor / Michigan → Michigan (MI)
  Minneapolis / Twin Cities → Minnesota (MN)
  Las Vegas / Henderson → Nevada (NV)
  Portland / Eugene / Oregon → Oregon (OR)
  Salt Lake City / SLC / Utah → Utah (UT)
  Baltimore / Maryland → Maryland (MD)
  Richmond / Northern Virginia / DC suburbs → Virginia (VA)
  Columbus / Cleveland / Cincinnati → Ohio (OH)
  If a 5-digit ZIP code is present (e.g. "near 30047"), keep it as-is — do NOT convert to state.

FEATURES — decode plain-language features to search-friendly terms:
  "3-row SUV with B&W audio" → "3-row luxury SUV with Bowers & Wilkins audio"
  "drop top" / "ragtop" / "cabriolet" → "convertible"
  "clean Carfax" / "accident-free" → "no accident history"
  "single owner" / "1 owner" → "one owner"
  "CPO" → "certified pre-owned"
  "low miles" → "under 30,000 miles"
  "very low miles" / "barely driven" → "under 15,000 miles"
  Keep HUD, lane-keeping, panoramic, adaptive cruise, heated seats etc. as-is.

BRANDS — normalize spelling:
  "bmw x5m" → "BMW X5 M"   "merc c300" → "Mercedes C 300"
  "lexus rx350" → "Lexus RX 350"   "f150" → "Ford F-150"
  "ioniq5" → "Hyundai IONIQ 5"

NON-SEARCH QUESTIONS — if the query is a general question (not a car search,
  e.g. "what's better, X or Y?"), preserve it unchanged.

Examples:
  Input:  "clean white awd bmw x5 under 50k atlanta single owner"
  Output: Clean white AWD BMW X5, under $50,000, in Georgia (GA), one owner, no accident history.

  Input:  "3 row suv bose sound panoramic under 55 in DFW 2022 or newer"
  Output: 3-row SUV with Bose audio and panoramic sunroof, under $55,000, in Texas (TX), 2022 or newer.

  Input:  "recent genesis gv80 low miles single owner"
  Output: Genesis GV80, 2024 or newer, under 30,000 miles, one owner.\
"""


@traceable(name="stage1_understand")
def _understand_node(state: PipelineState) -> dict:
    """Stage 1: free-form LLM pass — normalize the raw query."""
    from ai_engine import _get_client
    c = _get_client()
    t0 = time.time()
    try:
        resp = c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=250,
            system=_UNDERSTAND_SYSTEM,
            messages=[{"role": "user", "content": state["raw_query"]}],
        )
        enriched = resp.content[0].text.strip() if resp.content else state["raw_query"]
    except Exception as e:
        log.warning("Stage 1 failed, falling back to raw query: %s", e)
        enriched = state["raw_query"]

    dur_ms = round((time.time() - t0) * 1000)
    log.info("Stage1 | raw=%r → enriched=%r (%dms)", state["raw_query"], enriched, dur_ms)
    return {"enriched_query": enriched, "timing": {"understand_ms": dur_ms}}


# ── Stage 2: Structured Extraction ───────────────────────────
@traceable(name="stage2_extract")
def _extract_node(state: PipelineState) -> dict:
    """Stage 2: tool_use extraction on the normalized enriched query."""
    from ai_engine import extract_search_intent
    t0 = time.time()
    try:
        # Feed the enriched query so the extractor works on clean input
        intent = extract_search_intent(state["enriched_query"])
    except Exception as e:
        log.error("Stage 2 failed: %s", e)
        intent = {}

    dur_ms = round((time.time() - t0) * 1000)
    timing = {**state.get("timing", {}), "extract_ms": dur_ms}
    log.info("Stage2 | make=%r model=%r (%dms)",
             intent.get("make"), intent.get("model"), dur_ms)
    return {"intent": intent, "timing": timing}


# ── Graph assembly ────────────────────────────────────────────
def _build_pipeline():
    g = StateGraph(PipelineState)
    g.add_node("understand", _understand_node)
    g.add_node("extract", _extract_node)
    g.set_entry_point("understand")
    g.add_edge("understand", "extract")
    g.add_edge("extract", END)
    return g.compile()


_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = _build_pipeline()
    return _pipeline


# ── Public API ────────────────────────────────────────────────
@traceable(name="dealradar_pipeline")
def run_pipeline(query: str,
                 context_make: Optional[str] = None,
                 context_model: Optional[str] = None) -> PipelineState:
    """
    Run the two-stage understand → extract pipeline.

    Returns a PipelineState dict:
      enriched_query  — normalized query from Stage 1
      intent          — structured params from Stage 2
      timing          — {"understand_ms": N, "extract_ms": N}
    """
    pipe = _get_pipeline()
    initial: PipelineState = {
        "raw_query":      query,
        "context_make":   context_make,
        "context_model":  context_model,
        "enriched_query": "",
        "intent":         {},
        "timing":         {},
    }
    return pipe.invoke(initial)
