#!/usr/bin/env python3
"""
generate_natal_report.py — Natal chart report generator in Marcia Fervienza's voice.

Pipeline:
  1. Read chart JSON (AstroAPI.cloud format)
  2. For each section: run 2-3 Pinecone retrieval queries with planet filters
  3. Synthesize each section with Claude Sonnet 4.6
  4. Generate final 'Fio Condutor' synthesis section
  5. Save complete report to output/{name}_natal_report.txt

Usage:
    python3 generate_natal_report.py <chart.json>
    python3 generate_natal_report.py <chart.json> --only abertura,triade,lua
    python3 generate_natal_report.py <chart.json> --limit 3
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from pinecone import Pinecone
from openai import OpenAI
from anthropic import Anthropic

# ============================================================
# CONFIG
# ============================================================
# Secrets are read from environment variables at startup.
# Required: PINECONE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
# Optional: PINECONE_INDEX (defaults to "consultas-db"), ASTROAPI_KEY (reserved for future chart-fetching)
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX = os.environ.get("PINECONE_INDEX", "consultas-db")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ASTROAPI_KEY = os.environ.get("ASTROAPI_KEY", "")

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "claude-sonnet-4-6"
SECTION_MAX_TOKENS = 1500
FIO_CONDUTOR_MAX_TOKENS = 2000
MIN_SCORE = 0.35
DEFAULT_TOP_K = 6
PER_SECTION_CHUNK_CAP = 8  # max retrieved chunks fed to Claude per section

OUTPUT_DIR = Path("/Users/marciaqfervienza/Documents/Consultas DB/report_generator/output")

# ============================================================
# CLIENTS (initialized lazily to avoid startup cost on import)
# ============================================================
_pc = None
_index = None
_oai = None
_anth = None


def init_clients():
    global _pc, _index, _oai, _anth
    if _pc is None:
        _pc = Pinecone(api_key=PINECONE_API_KEY)
        _index = _pc.Index(PINECONE_INDEX)
    if _oai is None:
        _oai = OpenAI(api_key=OPENAI_API_KEY)
    if _anth is None:
        _anth = Anthropic(api_key=ANTHROPIC_API_KEY)


# ============================================================
# PORTUGUESE LABELS
# ============================================================
PLANET_LABEL_PT = {
    "sun": "Sol", "moon": "Lua", "mercury": "Mercúrio", "venus": "Vênus",
    "mars": "Marte", "jupiter": "Júpiter", "saturn": "Saturno", "uranus": "Urano",
    "neptune": "Netuno", "pluto": "Plutão", "chiron": "Quíron", "lilith": "Lilith",
    "north_node": "Nodo Norte", "south_node": "Nodo Sul",
    "ceres": "Ceres", "vesta": "Vesta", "juno": "Juno", "pallas": "Palas",
}

# ============================================================
# IN-SIGN ASPECT DETECTION
# ============================================================
# An "in-sign" aspect is one where the two planets' signs naturally form that
# aspect by zodiac geometry — independent of orb. Conjunctions are in-sign
# when both planets are in the same sign; oppositions when their signs are 6
# steps apart; etc. We use this to filter the chart's full aspect list down
# to the geometrically clean ones shown in the PDF's aspects table.
IN_SIGN_ASPECTS = {
    "conjunction": 0,
    "opposition": 6,
    "trine": 4,
    "square": 3,
    "sextile": 2,
}

SIGN_ORDER = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]


def is_in_sign_aspect(planet_a_sign: str, planet_b_sign: str, aspect_type: str) -> bool:
    """Return True if the two signs naturally correspond to the given aspect type."""
    if aspect_type not in IN_SIGN_ASPECTS:
        return False
    sa = (planet_a_sign or "").strip().lower()
    sb = (planet_b_sign or "").strip().lower()
    if sa not in SIGN_ORDER or sb not in SIGN_ORDER:
        return False
    ia = SIGN_ORDER.index(sa)
    ib = SIGN_ORDER.index(sb)
    raw = abs(ia - ib)
    # Wheel distance: take the shorter of the two directions
    distance = min(raw, 12 - raw)
    return distance == IN_SIGN_ASPECTS[aspect_type]


def get_in_sign_aspects(aspects: list, points: dict = None) -> list:
    """Filter aspects to only major in-sign aspects.

    Each input aspect dict should have planet_a, planet_b, type. The signs can
    either be embedded as planet_a_sign / planet_b_sign on the aspect, or looked
    up via `points` ({planet_key: {sign: 'aries', ...}, ...}).
    """
    out = []
    points = points or {}
    for a in aspects or []:
        sa = a.get("planet_a_sign")
        sb = a.get("planet_b_sign")
        if not sa:
            sa = (points.get(a.get("planet_a"), {}) or {}).get("sign")
        if not sb:
            sb = (points.get(a.get("planet_b"), {}) or {}).get("sign")
        if not sa or not sb:
            continue
        if is_in_sign_aspect(sa, sb, a.get("type", "")):
            out.append(a)
    return out


SIGN_OPPOSITE_PT = {
    "Áries": "Libra", "Libra": "Áries",
    "Touro": "Escorpião", "Escorpião": "Touro",
    "Gêmeos": "Sagitário", "Sagitário": "Gêmeos",
    "Câncer": "Capricórnio", "Capricórnio": "Câncer",
    "Leão": "Aquário", "Aquário": "Leão",
    "Virgem": "Peixes", "Peixes": "Virgem",
}

# Depth tiers (instruction text appended per section)
DEPTH_TIER_1 = "Esta é a seção mais importante e profunda do relatório. Escreva com máxima profundidade psicológica. Extensão: 450-550 palavras."
DEPTH_TIER_2 = "Extensão: 380-450 palavras, com profundidade psicológica real."
DEPTH_TIER_3 = "Extensão: 280-350 palavras."

# For sextile-query enrichment: which chart planet keys each section is "about"
SECTION_PLANET_KEYS = {
    "abertura": ["sun", "moon"],
    "triade": ["sun", "moon"],
    "mercurio": ["mercury"],
    "lua": ["moon"],
    "casa_4": [],  # dynamic — derived from planets in house 4
    "sol_saturno": ["sun", "saturn"],
    "venus_marte": ["venus", "mars"],
    "jupiter": ["jupiter"],
    "saturno": ["saturn"],
    "quiron": ["chiron"],
    "urano": ["uranus"],
    "netuno": ["neptune"],
    "plutao": ["pluto"],
    "lilith": ["lilith"],
    "nodos": ["north_node", "south_node"],
    "asteroides": ["ceres", "vesta", "juno", "pallas"],
}


def planets_in_house(chart, house_num):
    """Return list of chart planet keys whose house == house_num."""
    return [k for k, v in chart.get("points", {}).items() if v.get("house") == house_num]


def ic_for_chart(chart):
    """Compute IC sign from MC (always opposite). Returns dict with sign_pt and degrees, or None."""
    mc = chart.get("midheaven", {})
    mc_sign_pt = mc.get("sign_pt")
    if not mc_sign_pt:
        return None
    ic_sign_pt = SIGN_OPPOSITE_PT.get(mc_sign_pt)
    return {"sign_pt": ic_sign_pt, "degrees": mc.get("degrees", 0.0)}


# ============================================================
# ASPECT PRIORITIZATION & FILTERING
# ============================================================
# Aspect priority tiers — used by filter_and_prioritize_aspects() to rank
# which aspects of a chart deserve focus. The "combined tier" of an aspect
# is max(tier_a, tier_b) — i.e., the lower-priority planet drives the
# overall tier of the pair.
ASPECT_TIERS = {
    "tier1": ["Sol", "Lua"],                                                                # always include, full depth
    "tier2": ["Mercúrio", "Vênus", "Marte", "Júpiter", "Saturno"],                          # include with good depth
    "tier3": ["Plutão", "Quíron", "Urano", "Netuno", "Lilith", "Nodo Norte", "Nodo Sul"],   # include if orb tight or thematically central
    "tier4": ["Ceres", "Vesta", "Juno", "Palas"],                                           # include only if orb under 2° and not already covered
}

EXCLUDED_FROM_ASPECTS = ["Ascendente", "Meio-do-Céu", "IC", "Descendente"]                  # angles never used as aspecting bodies

IMPOSSIBLE_ASPECTS = [
    ("Nodo Norte", "Nodo Sul"),
    ("Nodo Sul", "Nodo Norte"),
]  # Always opposing by definition, never interpret


def _tier_for_planet(planet_pt: str) -> int:
    for tier_name, planet_list in ASPECT_TIERS.items():
        if planet_pt in planet_list:
            return int(tier_name.replace("tier", ""))
    return 5  # unknown → lowest priority


def _aspect_dedup_key(aspect: dict):
    """Stable key: ({planet_a_pt, planet_b_pt}, type). Mirrors collapse to the same key."""
    pa = aspect.get("_pa_pt") or PLANET_LABEL_PT.get(aspect.get("planet_a", ""), aspect.get("planet_a", ""))
    pb = aspect.get("_pb_pt") or PLANET_LABEL_PT.get(aspect.get("planet_b", ""), aspect.get("planet_b", ""))
    return (frozenset({pa, pb}), aspect.get("type", ""))


def filter_and_prioritize_aspects(aspects: list, chart_data=None) -> list:
    """
    Apply tier-based prioritization, exclusions and dedup to a list of aspect dicts.

    - Removes aspects involving angles (Ascendente, MC, IC, DS).
    - Removes IMPOSSIBLE_ASPECTS pairs (Nodo Norte/Sul, etc.).
    - Removes mirror duplicates (Lua-Plutão vs Plutão-Lua).
    - Tier 4 aspects only kept when orb < 2°.
    - Sorts by combined tier ascending, then by orb ascending.

    Returns a new list of aspect dicts annotated with `_combined_tier`,
    `_pa_pt`, `_pb_pt` for downstream consumers.
    """
    seen = set()
    out = []
    for a in aspects or []:
        pa = PLANET_LABEL_PT.get(a.get("planet_a", ""), a.get("planet_a", ""))
        pb = PLANET_LABEL_PT.get(a.get("planet_b", ""), a.get("planet_b", ""))

        # 1. Exclude angles
        if pa in EXCLUDED_FROM_ASPECTS or pb in EXCLUDED_FROM_ASPECTS:
            continue
        # 2. Exclude impossible
        if (pa, pb) in IMPOSSIBLE_ASPECTS or (pb, pa) in IMPOSSIBLE_ASPECTS:
            continue
        # 3. Dedupe mirrors
        key = (frozenset({pa, pb}), a.get("type", ""))
        if key in seen:
            continue
        seen.add(key)

        # 4. Determine combined tier (worst of the two — higher number wins)
        ta, tb = _tier_for_planet(pa), _tier_for_planet(pb)
        combined = max(ta, tb)
        orb = a.get("orb", 99.0)
        # 5. Tier 4 only if orb < 2°
        if combined == 4 and orb >= 2.0:
            continue

        out.append({**a, "_combined_tier": combined, "_pa_pt": pa, "_pb_pt": pb})

    out.sort(key=lambda x: (x["_combined_tier"], x.get("orb", 99.0)))
    return out


# ============================================================
# CROSS-SECTION ASPECT TRACKING
# ============================================================
# Set of dedup-keys for aspects that have already been *described* in a
# previously-generated section. Each section's chart context only includes
# new aspects not yet in this set. Reset at the start of main().
described_aspect_themes: set = set()

# Per-section audit — records the filtered+deduplicated aspect list that
# was passed to Claude for each section. Used for verification/debugging.
_section_aspect_audit: dict = {}


def _planets_for_section(section_name: str, chart: dict) -> list:
    """Return chart planet keys (lowercase English) relevant to a section."""
    keys = list(SECTION_PLANET_KEYS.get(section_name, []))
    if section_name == "casa_4":
        keys = keys + planets_in_house(chart, 4)
    # Dedupe preserving order
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k); out.append(k)
    return out


def aspects_for_section_filtered(section_name: str, chart: dict, exclude_described: bool = True) -> list:
    """Return the section's prioritized, deduped aspect list, optionally
    excluding aspects already described in prior sections.

    Idempotency: if this section's filtered list is already in
    `_section_aspect_audit` from a prior call in the same run, return that
    cached result instead of recomputing. This is essential for the parallel
    execution path — the pre-compute phase in `generate_report()` populates
    audit entries for every section sequentially (so cross-section dedup is
    deterministic), and then threads calling this function during parallel
    Claude generation must see those pre-computed lists, NOT recompute against
    a fully-populated `described_aspect_themes` set (which would return empty).

    SPECIAL CASE — the Lua section always receives the full list of aspects
    involving Lua, regardless of whether other sections have already mentioned
    them. Lua aspects are Tier 1 and deserve dedicated treatment in the
    Lua section. All other dedup rules (angles, IMPOSSIBLE_ASPECTS, mirrors,
    Tier-4 orb threshold) still apply.
    """
    # Return cached result if already computed in this run
    if section_name in _section_aspect_audit:
        return _section_aspect_audit[section_name]

    keys = _planets_for_section(section_name, chart)
    raw = []
    seen_raw = set()
    for k in keys:
        for a in aspects_for_planet(chart, k):
            sig = (a.get("planet_a"), a.get("planet_b"), a.get("type"))
            if sig in seen_raw:
                continue
            seen_raw.add(sig)
            raw.append(a)
    filtered = filter_and_prioritize_aspects(raw, chart)

    # Lua section bypasses the "already-described" filter: all Lua aspects are
    # Tier 1 and must appear in the Lua section even if upstream sections
    # (abertura, triade) have already mentioned them.
    if section_name == "lua":
        exclude_described = False

    if exclude_described:
        filtered = [a for a in filtered if _aspect_dedup_key(a) not in described_aspect_themes]
    _section_aspect_audit[section_name] = filtered
    return filtered


def fmt_filtered_aspects(filtered_aspects: list) -> str:
    """Pretty-print the prioritized aspect list with tier annotation."""
    if not filtered_aspects:
        return "(nenhum aspecto novo a destacar nesta seção — outros já foram descritos antes)"
    parts = []
    for a in filtered_aspects:
        pa, pb = a["_pa_pt"], a["_pb_pt"]
        tier = a["_combined_tier"]
        parts.append(f"{pa}-{pb} ({a['type_pt']}, orbe {a['orb']:.1f}°, T{tier})")
    return "; ".join(parts)


def sextile_queries_for_section(section_name, chart):
    """Build extra retrieval queries for sextile aspects involving section's planets."""
    keys = set(SECTION_PLANET_KEYS.get(section_name, []))
    if section_name == "casa_4":
        keys.update(planets_in_house(chart, 4))
    if not keys:
        return []
    queries = []
    for a in chart.get("aspects", []):
        if a.get("type") != "sextile":
            continue
        if a["planet_a"] in keys or a["planet_b"] in keys:
            pa = PLANET_LABEL_PT.get(a["planet_a"], a["planet_a"])
            pb = PLANET_LABEL_PT.get(a["planet_b"], a["planet_b"])
            queries.append(
                f"{pa} sextil {pb} aspectos de sextil representam potencial a ser desenvolvido conscientemente não dons automáticos"
            )
    return queries


# ============================================================
# RETRIEVAL
# ============================================================
def retrieve_chunks(query_text, planets_filter=None, top_k=DEFAULT_TOP_K):
    """
    Embed query with OpenAI, query Pinecone twice (once for natal consultation
    chunks, once for class chunks), apply optional planets filter, dedupe by
    youtube_id, return top_k chunks scored >= MIN_SCORE.
    """
    init_clients()

    # Embed
    emb_resp = _oai.embeddings.create(model=EMBED_MODEL, input=query_text)
    qvec = emb_resp.data[0].embedding

    matches = []

    # Query 1 — refined natal consultations
    consult_filter = {"reading_type": {"$eq": "natal"}}
    if planets_filter:
        consult_filter["planets"] = {"$in": planets_filter}
    try:
        r1 = _index.query(
            vector=qvec, top_k=top_k * 2, filter=consult_filter, include_metadata=True
        )
        matches.extend(r1.matches)
    except Exception as e:
        print(f"  WARN consultation query failed: {e}", flush=True)

    # Query 2 — class chunks (lectures + foundations)
    class_filter = {"content_type": {"$in": ["class_lecture", "class_foundations"]}}
    if planets_filter:
        class_filter["planets"] = {"$in": planets_filter}
    try:
        r2 = _index.query(
            vector=qvec, top_k=top_k * 2, filter=class_filter, include_metadata=True
        )
        matches.extend(r2.matches)
    except Exception as e:
        print(f"  WARN class query failed: {e}", flush=True)

    # Filter by score, sort, dedupe by youtube_id
    matches = [m for m in matches if m.score >= MIN_SCORE]
    matches.sort(key=lambda m: m.score, reverse=True)

    seen_yids = set()
    deduped = []
    for m in matches:
        yid = (m.metadata or {}).get("youtube_id")
        if yid and yid in seen_yids:
            continue
        if yid:
            seen_yids.add(yid)
        deduped.append(m)
        if len(deduped) >= top_k:
            break
    return deduped


def format_chunks_for_prompt(chunks):
    if not chunks:
        return "(nenhuma passagem relevante encontrada)"
    parts = []
    for i, m in enumerate(chunks, 1):
        meta = m.metadata or {}
        text = (meta.get("text") or "").strip()
        ctype = meta.get("content_type") or meta.get("reading_type") or "?"
        topic = meta.get("topic") or ""
        header = f"--- Passagem {i} (relevância={m.score:.2f}, tipo={ctype}"
        if topic:
            header += f", tópico={topic}"
        header += ") ---"
        parts.append(f"{header}\n{text}")
    return "\n\n".join(parts)


# ============================================================
# CHART HELPERS
# ============================================================
def aspects_for_planet(chart, planet_key):
    """Return aspects involving the given planet (a or b)."""
    out = []
    for a in chart.get("aspects", []):
        if a.get("planet_a") == planet_key or a.get("planet_b") == planet_key:
            out.append(a)
    return out


def fmt_aspects(aspects):
    if not aspects:
        return "(sem aspectos maiores listados)"
    parts = []
    for a in aspects:
        pa = PLANET_LABEL_PT.get(a["planet_a"], a["planet_a"])
        pb = PLANET_LABEL_PT.get(a["planet_b"], a["planet_b"])
        parts.append(f"{pa}-{pb} ({a['type_pt']}, orbe {a['orb']:.1f}°)")
    return "; ".join(parts)


def fmt_position(p):
    s = f"{p['sign_pt']} ({p['degrees']:.1f}°)"
    if p.get("retrograde"):
        s += " ℞"
    return s


def section_chart_context(section_name, chart):
    """
    Per-section concise chart-data context shown to Claude.

    Aspects come from `aspects_for_section_filtered()`, which:
      - applies tier-based prioritization,
      - excludes angles and IMPOSSIBLE_ASPECTS,
      - dedupes mirrors,
      - drops Tier-4 pairs with orb ≥ 2°,
      - excludes aspects already described in previous sections
        (via the module-level `described_aspect_themes` set).
    """
    p = chart["points"]
    asc = chart["ascendant"]
    mc = chart.get("midheaven", {})

    # Compute filtered aspects for this section once (also records into _section_aspect_audit)
    filtered_aspects = aspects_for_section_filtered(section_name, chart)
    aspects_line = fmt_filtered_aspects(filtered_aspects)

    if section_name == "abertura":
        return (
            f"Sol: {fmt_position(p['sun'])} na casa {p['sun']['house']}\n"
            f"Lua: {fmt_position(p['moon'])} na casa {p['moon']['house']}\n"
            f"Ascendente: {fmt_position(asc)}\n"
            f"Meio-do-Céu: {fmt_position(mc)}"
        )

    if section_name == "triade":
        return (
            f"Sol: {fmt_position(p['sun'])} na casa {p['sun']['house']}\n"
            f"Lua: {fmt_position(p['moon'])} na casa {p['moon']['house']}\n"
            f"Ascendente: {fmt_position(asc)}\n"
            f"Meio-do-Céu: {fmt_position(mc)}\n\n"
            f"Aspectos relevantes da tríade (filtrados, priorizados, sem duplicatas): {aspects_line}"
        )

    if section_name == "mercurio":
        return (
            f"Mercúrio: {fmt_position(p['mercury'])} na casa {p['mercury']['house']}\n"
            f"Aspectos relevantes de Mercúrio (filtrados): {aspects_line}\n\n"
            f"NOTA DE ESTILO PARA ESTA SEÇÃO: Avoid doubling the same verb in sequence — "
            f"'precisa conhecer a fundo, precisa poder sustentar' should be restructured. "
            f"Evite a construção 'como onde' — use sempre 'como o lugar onde' ou reescreva a frase."
        )

    if section_name == "lua":
        return (
            f"Lua: {fmt_position(p['moon'])} na casa {p['moon']['house']}\n"
            f"Aspectos relevantes da Lua (filtrados): {aspects_line}"
        )

    if section_name == "casa_4":
        ic = ic_for_chart(chart)
        in_h4 = planets_in_house(chart, 4)
        lines = []
        if ic:
            lines.append(f"IC (Imum Coeli): {ic['sign_pt']} ({ic['degrees']:.1f}°)")
        if in_h4:
            lines.append("Planetas na Casa 4:")
            for k in in_h4:
                planet_name = PLANET_LABEL_PT.get(k, k)
                lines.append(f"  - {planet_name}: {fmt_position(p[k])}")
        else:
            lines.append("Casa 4: sem planetas listados (interpretar a partir do IC)")
        lines.append("")
        lines.append(f"Aspectos relevantes envolvendo a Casa 4 (filtrados): {aspects_line}")
        return "\n".join(lines)

    if section_name == "sol_saturno":
        return (
            f"Sol: {fmt_position(p['sun'])} na casa {p['sun']['house']}\n"
            f"Saturno: {fmt_position(p['saturn'])} na casa {p['saturn']['house']}\n"
            f"Aspectos relevantes Sol/Saturno (filtrados): {aspects_line}"
        )

    if section_name == "venus_marte":
        return (
            f"Vênus: {fmt_position(p['venus'])} na casa {p['venus']['house']}\n"
            f"Marte: {fmt_position(p['mars'])} na casa {p['mars']['house']}\n"
            f"Aspectos relevantes Vênus/Marte (filtrados): {aspects_line}"
        )

    if section_name == "jupiter":
        return (
            f"Júpiter: {fmt_position(p['jupiter'])} na casa {p['jupiter']['house']}\n"
            f"Aspectos relevantes de Júpiter (filtrados): {aspects_line}"
        )

    if section_name == "saturno":
        return (
            f"Saturno: {fmt_position(p['saturn'])} na casa {p['saturn']['house']}\n"
            f"Aspectos relevantes de Saturno (filtrados): {aspects_line}"
        )

    if section_name == "quiron":
        return (
            f"Quíron: {fmt_position(p['chiron'])} na casa {p['chiron']['house']}\n"
            f"Aspectos relevantes de Quíron (filtrados): {aspects_line}"
        )

    if section_name == "urano":
        return (
            f"Urano: {fmt_position(p['uranus'])} na casa {p['uranus']['house']}\n"
            f"Aspectos relevantes de Urano (filtrados): {aspects_line}"
        )

    if section_name == "netuno":
        return (
            f"Netuno: {fmt_position(p['neptune'])} na casa {p['neptune']['house']}\n"
            f"Aspectos relevantes de Netuno (filtrados): {aspects_line}"
        )

    if section_name == "plutao":
        return (
            f"Plutão: {fmt_position(p['pluto'])} na casa {p['pluto']['house']}\n"
            f"Aspectos relevantes de Plutão (filtrados): {aspects_line}"
        )

    if section_name == "lilith":
        return (
            f"Lilith: {fmt_position(p['lilith'])} na casa {p['lilith']['house']}\n"
            f"Aspectos relevantes de Lilith (filtrados): {aspects_line}"
        )

    if section_name == "nodos":
        return (
            f"Nodo Sul: {fmt_position(p['south_node'])} na casa {p['south_node']['house']}\n"
            f"Nodo Norte: {fmt_position(p['north_node'])} na casa {p['north_node']['house']}\n"
            f"Aspectos relevantes dos Nodos (filtrados): {aspects_line}"
        )

    if section_name == "asteroides":
        return (
            f"Ceres: {fmt_position(p['ceres'])} na casa {p['ceres']['house']}\n"
            f"Vesta: {fmt_position(p['vesta'])} na casa {p['vesta']['house']}\n"
            f"Juno: {fmt_position(p['juno'])} na casa {p['juno']['house']}\n"
            f"Palas: {fmt_position(p['pallas'])} na casa {p['pallas']['house']}\n"
            f"Aspectos relevantes dos asteróides (filtrados, orbe < 2°): {aspects_line}"
        )

    return ""


# ============================================================
# SECTION DEFINITIONS — built dynamically from chart
# ============================================================
def build_sections(chart):
    p = chart["points"]
    asc = chart["ascendant"]

    sun = p["sun"]
    moon = p["moon"]
    mercury = p["mercury"]
    venus = p["venus"]
    mars = p["mars"]
    jupiter = p["jupiter"]
    saturn = p["saturn"]
    uranus = p["uranus"]
    neptune = p["neptune"]
    pluto = p["pluto"]
    chiron = p["chiron"]
    lilith = p["lilith"]
    north_node = p["north_node"]
    south_node = p["south_node"]
    ceres = p["ceres"]
    vesta = p["vesta"]
    juno = p["juno"]
    pallas = p["pallas"]

    moon_aspects_text = fmt_aspects(aspects_for_planet(chart, "moon"))

    # Casa 4 — dynamic planet list and queries
    h4_keys = planets_in_house(chart, 4)
    h4_planets_pt = [PLANET_LABEL_PT.get(k, k) for k in h4_keys]
    ic = ic_for_chart(chart)
    ic_sign = ic["sign_pt"] if ic else None
    casa4_queries = [
        "Casa 4 raízes família origem fundação emocional casa interior",
        "IC família de origem padrão familiar atmosfera doméstica",
    ]
    if ic_sign:
        casa4_queries.append(f"IC em {ic_sign} casa quatro fundação emocional")
    for k in h4_keys:
        pl_pt = PLANET_LABEL_PT.get(k, k)
        sign_pt = chart["points"][k]["sign_pt"]
        casa4_queries.append(f"{pl_pt} em {sign_pt} casa 4 raízes família")

    # Canonical section order. Edit this tuple to reorder sections without
    # touching the dict literals below.
    SECTION_ORDER = (
        "abertura", "triade", "lua", "casa_4", "sol_saturno", "mercurio",
        "venus_marte", "jupiter", "saturno", "quiron", "urano", "netuno",
        "plutao", "lilith", "nodos", "asteroides",
    )

    _sections_unordered = [
        {
            "name": "abertura",
            "title": "Abertura",
            "queries": [
                f"Sol em {sun['sign_pt']} Lua em {moon['sign_pt']} Ascendente em {asc['sign_pt']} síntese identidade",
                f"quem é essa pessoa {sun['sign_pt']} {moon['sign_pt']} {asc['sign_pt']}",
            ],
            "planets_filter": ["Sol", "Lua"],
            "psychological_frame": (
                "Escreva uma abertura calorosa que funcione como uma porta de entrada, não como uma análise imediata. "
                "O primeiro parágrafo deve criar uma sensação de reconhecimento — como se alguém que te conhece "
                "profundamente estivesse dizendo 'eu te vejo'. Apenas no segundo parágrafo comece a nomear as tensões "
                "centrais do mapa. Termine com uma frase que convide o leitor a continuar — não com uma conclusão, "
                "mas com uma direção. Tom: íntimo, acolhedor, presente.\n\n"
                "Evite a construção 'Não é X. É Y' no primeiro parágrafo da abertura — se ela aparecer, reescreva "
                "usando uma afirmação direta em vez da estrutura negativa/positiva."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "triade",
            "title": "Sua Tríade: Sol, Lua e Ascendente",
            "queries": [
                f"Sol em {sun['sign_pt']} casa {sun['house']}",
                f"Lua em {moon['sign_pt']} casa {moon['house']}",
                f"Ascendente em {asc['sign_pt']} tríade síntese",
            ],
            "planets_filter": ["Sol", "Lua"],
            "psychological_frame": (
                "Interprete o Sol, a Lua e o Ascendente como uma tríade unificada — não como três posicionamentos "
                "separados. O Sol é para onde você vai, a Lua é de onde você vem, o Ascendente é como você chega. "
                "Sintetize como esses três funcionam juntos e onde criam tensão.\n\n"
                "Termine a seção com uma ou duas frases que ofereçam uma orientação sobre como trabalhar com a tensão "
                "central da tríade — não como resolução, mas como direção. Algo que a pessoa possa carregar saindo "
                "dessa seção."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "mercurio",
            "title": "Mercúrio: Como Você Pensa",
            "queries": [
                f"Mercúrio em {mercury['sign_pt']} casa {mercury['house']}",
                f"como pensa processa informação {mercury['sign_pt']} comunicação",
            ],
            "planets_filter": ["Mercúrio"],
            "psychological_frame": "Mercúrio fala de como você processa informação, aprende, se comunica e organiza o pensamento. Interprete o signo, a casa e os aspectos principais de Mercúrio neste mapa.",
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "lua",
            "title": "Lua: Suas Raízes Emocionais",
            "queries": [
                f"Lua em {moon['sign_pt']} casa {moon['house']} mãe infância",
                f"figura materna {moon['sign_pt']} padrões emocionais família",
                f"Lua aspectos {moon_aspects_text}",
            ],
            "planets_filter": ["Lua"],
            "psychological_frame": "A Lua fala da figura materna ou do cuidador principal na infância, do ambiente familiar, das memórias e dos padrões emocionais que moldaram como você navega o mundo.",
            "depth_instruction": DEPTH_TIER_1,
        },
        {
            "name": "casa_4",
            "title": "Casa 4: Suas Raízes e Sua Casa Interna",
            "queries": casa4_queries,
            "planets_filter": h4_planets_pt if h4_planets_pt else None,
            "psychological_frame": (
                "A Casa 4 é a fundação invisível — a casa interior, a família de origem em seu sentido mais arquetípico, "
                "o lugar de onde você veio e que ainda te habita. É a memória anterior à memória, o solo emocional do qual "
                "você emergiu psiquicamente. Esta seção complementa a Lua: enquanto a Lua é a relação direta com a figura "
                "materna e os padrões emocionais herdados, a Casa 4 é o ambiente, a atmosfera, o terreno do qual essa "
                "relação brotou.\n\n"
                "Se houver planetas na casa 4, interprete-os como forças que moldaram o ambiente da infância e o senso "
                "de lar e pertencimento. Se a casa estiver vazia, interprete o signo da cúspide (IC) e o planeta que rege "
                "esse signo como a chave do ambiente doméstico e familiar. Em qualquer caso, NÃO repita interpretações que "
                "já apareceram na seção de Plutão ou de qualquer outro planeta — aqui o foco é o ambiente, a atmosfera, "
                "o terreno emocional da infância, não a força planetária em si.\n\n"
                "IMPORTANTE: A interpretação da casa quatro em Libra com Plutão não deve assumir harmonia como única "
                "possibilidade. Apresente DUAS manifestações possíveis: (a) um ambiente que mantinha uma aparência de "
                "harmonia por fora enquanto dinâmicas de poder operavam por baixo; (b) um ambiente de conflito aberto, "
                "frequentemente criado por uma figura parental dominante. Use linguagem como 'pode ter sido' ou "
                "'alternativamente' para apresentar as duas possibilidades sem impor uma única leitura."
            ),
            "depth_instruction": DEPTH_TIER_1,
        },
        {
            "name": "sol_saturno",
            "title": "Sol e Saturno: O Pai e as Ferramentas da Vida",
            "queries": [
                f"Sol em {sun['sign_pt']} casa {sun['house']} figura paterna",
                f"Saturno em {saturn['sign_pt']} casa {saturn['house']} pai modelo referência",
                f"Sol Saturno desafios ferramentas vida",
            ],
            "planets_filter": ["Sol", "Saturno"],
            "psychological_frame": (
                "Sol e Saturno juntos falam da figura paterna ou do modelo de referência, e das ferramentas que você "
                "recebeu — ou não recebeu — para enfrentar os desafios da vida. O Sol é quem você está se tornando. "
                "Saturno é onde você aprende através do tempo, do esforço e da repetição.\n\n"
                "Inclua entre as possibilidades a ausência do pai — por morte, abandono, distância emocional ou física. "
                "O Sol na casa oito é um indicador possível de experiências de perda ou transformação relacionadas à "
                "figura paterna. Use linguagem aberta: 'pode ter havido', 'em alguns casos este posicionamento fala de'.\n\n"
                "Esta seção deve terminar com uma orientação concreta e específica — não uma frase genérica, mas algo "
                "que esta pessoa possa carregar sobre como trabalhar com a tensão entre expansão e dúvida do próprio valor.\n\n"
                "Após mencionar o trígono Saturno-Netuno, adicione uma frase de transição que conecte essa observação "
                "à orientação prática final — não salte diretamente do aspecto para a conclusão."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "venus_marte",
            "title": "Vênus e Marte: Como Você Ama e Luta pelo Que Deseja",
            "queries": [
                f"Vênus em {venus['sign_pt']} casa {venus['house']} amor relacionamento",
                f"Marte em {mars['sign_pt']} casa {mars['house']} desejo ação",
                f"Vênus Marte relacionamento como você ama como você luta pelo que quer",
            ],
            "planets_filter": ["Vênus", "Marte"],
            "psychological_frame": (
                "Vênus fala de como você ama, o que você valoriza e como você se relaciona. Marte fala de como você "
                "age, deseja e luta pelo que quer. Juntos descrevem a dinâmica afetiva e relacional desta pessoa.\n\n"
                "O sextil Vênus-Júpiter deve ser interpretado não como abundância financeira mas como uma facilidade "
                "genuína em criar vínculos e em ser reconhecida pelo que oferece relacionalmente. NÃO conecte esse "
                "aspecto a dinheiro — isso cria contradição com o que foi dito sobre Saturno na casa dois."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "jupiter",
            "title": "Júpiter: Onde Você Acredita em Si Mesmo",
            "queries": [
                f"Júpiter em {jupiter['sign_pt']} casa {jupiter['house']}",
                f"onde acredita em si mesmo expansão {jupiter['sign_pt']} dons naturais",
            ],
            "planets_filter": ["Júpiter"],
            "psychological_frame": "Júpiter marca o lugar onde você acredita em si mesmo naturalmente, onde sente que é bom, onde a expansão e a abundância fluem com menos esforço. É o dom que você carrega.",
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "saturno",
            "title": "Saturno: Onde Você Aprende Pela Vida",
            "queries": [
                f"Saturno em {saturn['sign_pt']} casa {saturn['house']}",
                f"dúvida insegurança {saturn['sign_pt']} crescimento tempo esforço repetição",
            ],
            "planets_filter": ["Saturno"],
            "psychological_frame": "Saturno marca o lugar onde você duvida de si mesmo, onde o crescimento vem lentamente, através do erro, da repetição e do tempo. Não é uma fraqueza — é o lugar onde você constrói algo duradouro. Seja honesto sobre a dificuldade sem tirar a esperança.",
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "quiron",
            "title": "Quíron: Sua Ferida e Seu Dom",
            "queries": [
                f"Quíron em {chiron['sign_pt']} casa {chiron['house']}",
                f"ferida integração cura {chiron['sign_pt']}",
            ],
            "planets_filter": ["Quíron"],
            "psychological_frame": "Quíron é a ferida que você carrega — algo que doeu profundamente e que você terá que trabalhar e integrar ao longo da vida. Mas a ferida também é a fonte do dom: é justamente onde você mais sofreu que você tem mais para oferecer aos outros.",
            "depth_instruction": DEPTH_TIER_2,
        },
        {
            "name": "urano",
            "title": "Urano: Onde Você Não Se Encaixa",
            "queries": [
                f"Urano em {uranus['sign_pt']} casa {uranus['house']}",
                f"não se encaixa recusa liberdade ruptura {uranus['sign_pt']}",
            ],
            "planets_filter": ["Urano"],
            "psychological_frame": "Urano marca o lugar onde você sabe que não se encaixa — e se recusa a fingir que sim. É o ponto de maior originalidade e também de maior resistência ao conformismo. Não é patologia: é identidade.",
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "netuno",
            "title": "Netuno: Onde Você Se Dissolve",
            "queries": [
                f"Netuno em {neptune['sign_pt']} casa {neptune['house']}",
                f"ilusão sacrifício dissolução entrega excessiva {neptune['sign_pt']}",
            ],
            "planets_filter": ["Netuno"],
            "psychological_frame": "Netuno marca o lugar onde você tende a dar demais, onde a fronteira entre você e o outro fica turva, onde a ilusão e a auto-ilusão operam. Também é onde sua espiritualidade e sua intuição mais profunda residem. Seja gentil mas honesto.",
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "plutao",
            "title": "Plutão: Onde Você Precisa de Controle",
            "queries": [
                f"Plutão em {pluto['sign_pt']} casa {pluto['house']}",
                f"controle poder medo perder transformação {pluto['sign_pt']}",
            ],
            "planets_filter": ["Plutão"],
            "psychological_frame": "Plutão marca o lugar onde você precisa de controle, onde tem medo de perder o controle, onde existe uma consciência inconsciente de falta ou escassez. É também o lugar de maior transformação possível — mas a transformação de Plutão nunca é suave.",
            "depth_instruction": DEPTH_TIER_2,
        },
        {
            "name": "lilith",
            "title": "Lilith: Onde Você Deve Insistir em Ser Você",
            "queries": [
                f"Lilith em {lilith['sign_pt']} casa {lilith['house']}",
                f"silenciada empurrada para fora insistir caminho próprio {lilith['sign_pt']}",
            ],
            "planets_filter": ["Lilith"],
            "psychological_frame": (
                "Lilith marca o lugar onde você percebe que algo em você é considerado 'errado' pelo mundo — onde o "
                "mundo tenta te silenciar, te normalizar, te empurrar para fora. É exatamente aqui que você deve "
                "insistir em fazer do seu jeito.\n\n"
                "Termine a seção com uma frase conclusiva sobre o que significa, em termos práticos e concretos, ter "
                "Lilith em Touro na casa onze — o que essa pessoa deve parar de negociar e o que deve insistir em preservar."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "nodos",
            "title": "Nodo Sul e Nodo Norte: De Onde Você Vem e Para Onde Vai",
            "queries": [
                f"Nodo Sul em {south_node['sign_pt']} casa {south_node['house']} zona de conforto excesso",
                f"Nodo Norte em {north_node['sign_pt']} casa {north_node['house']} aprender desafio direção",
            ],
            "planets_filter": ["Nodo Norte", "Nodo Sul"],
            "psychological_frame": (
                "O Nodo Sul é sua zona de conforto — o que você faz em excesso, o que lhe vem naturalmente mas onde "
                "você tende a se refugiar. O Nodo Norte é o desafio evolutivo — o que você veio aprender, onde você "
                "precisa crescer mesmo que seja desconfortável.\n\n"
                "Se possível, mencione que o Nodo Sul em Áries compartilha esse signo com a Lua e com Quíron — o "
                "padrão de ação impulsiva e exposição pública vai além do Nodo, está tecido em outras camadas do mapa. "
                "Para o Nodo Norte em Libra, mencione que Plutão também está em Libra — o desafio do encontro com o "
                "outro carrega peso plutoniano, não é um aprendizado suave."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
        {
            "name": "asteroides",
            "title": "Asteróides: Ceres, Vesta, Juno e Palas",
            "queries": [
                f"Ceres em {ceres['sign_pt']} casa {ceres['house']} cuidado nutrição",
                f"Vesta em {vesta['sign_pt']} casa {vesta['house']} dedicação foco sagrado",
                f"Juno em {juno['sign_pt']} casa {juno['house']} parceria compromisso",
                f"Palas em {pallas['sign_pt']} casa {pallas['house']} estratégia sabedoria",
            ],
            "planets_filter": ["Ceres", "Vesta", "Juno", "Palas"],
            "psychological_frame": (
                "Interprete cada asteróide brevemente mas com precisão: Ceres fala de como você nutre e precisa ser "
                "nutrido. Vesta fala de onde você dedica sua chama sagrada. Juno fala de como você se compromete em "
                "parcerias. Palas fala de sua sabedoria estratégica e criativa.\n\n"
                "Ceres está em conjunção com Plutão neste mapa (4 graus de orbe). Essa conjunção deve ser interpretada: "
                "o cuidado e a nutrição (Ceres) estão profundamente entrelaçados com dinâmicas de poder, transformação "
                "e o não-dito (Plutão). O cuidado que você recebeu — e que oferece — raramente é simples ou descomplicado.\n\n"
                "Para Vesta, Juno e Palas, inclua uma frase adicional além da descrição básica — algo que conecte o "
                "posicionamento a um desafio ou recurso concreto para esta pessoa específica, baseado no signo e casa."
            ),
            "depth_instruction": DEPTH_TIER_3,
        },
    ]

    # Apply canonical SECTION_ORDER
    by_name = {s["name"]: s for s in _sections_unordered}
    missing = [n for n in SECTION_ORDER if n not in by_name]
    extra = [n for n in by_name if n not in SECTION_ORDER]
    if missing:
        raise RuntimeError(f"SECTION_ORDER references missing section names: {missing}")
    if extra:
        raise RuntimeError(f"Section definitions present but absent from SECTION_ORDER: {extra}")
    return [by_name[n] for n in SECTION_ORDER]


# ============================================================
# CLAUDE SYNTHESIS
# ============================================================
# Shared style-rules block. Referenced by SECTION_PROMPT_TMPL below AND
# by app.py's Branch A Moon-blurb prompt via `from report_generator import
# SECTION_STYLE_RULES`. Any downstream Claude call that produces
# report-facing text should inject these rules so it screens out the same
# AI-writing tells the main sections screen out (the "Não é X, é Y"
# antithesis, `presença` as a vague noun, `funda` in place of `profunda`,
# `retrógrada` as a feminine noun, etc.). If these rules change, both
# call sites — and cleanup_pass — need to stay in sync.
SECTION_STYLE_RULES = """PROIBIDO: metáforas dramáticas ou imagens poéticas forçadas como "corta o ar como lâmina", "chama que arde", "abismo interior", "navega as profundezas" ou qualquer linguagem que soe como inteligência artificial tentando ser literária. A voz de Marcia é psicologicamente precisa e direta — ela não ornamenta, ela nomeia. Use linguagem íntima e direta, não eloquência performática.

TAMBÉM PROIBIDO: (a) a palavra "funda" — use sempre "profunda"; (b) a expressão "que sustenta" sem especificar o que sustenta; (c) a palavra "presença" como substantivo vago — use apenas quando indispensável e com referente claro; (d) a construção repetitiva "Não é X. É Y." — pode aparecer no máximo uma vez por seção; (e) "emoção que age antes de pensar" — prefira "emoções que emergem impulsivamente".

TAMBÉM PROIBIDO: (f) usar "nomear" como verbo padrão para tudo — varie com "identificar", "reconhecer", "colocar em palavras", "articular", "perceber", "distinguir"; (g) a construção "Não é X, é Y" — está limitada a UMA ocorrência por relatório inteiro, não por seção; (h) "retrógrada" como substantivo feminino — o planeta está sempre "retrógrado", nunca "a retrógrada"; (i) qualquer palavra em inglês não traduzida, especialmente "retrograde" — sempre "retrógrado"; (j) qualificadores defensivos desnecessários como "não porque seja naturalmente ambiciosa no sentido frio da palavra" — faça a afirmação diretamente sem recuar dela; (k) repetir o mesmo padrão interpretativo em seções diferentes — se a proteção emocional via controle já foi descrita na seção da Lua, a seção de Plutão não deve repetir a mesma ideia com outras palavras.

CONVENÇÕES DE LINGUAGEM: Use sempre "em oposição", "em quadratura", "em trígono", "em conjunção", "em sextil" — nunca "na oposição", "na quadratura" etc. Planetas retrógrados são sempre descritos como "retrógrado" — nunca "a retrógrada". Mantenha "se regenerar rapidamente" em vez de "se regenerar rápido"."""


SECTION_PROMPT_TMPL = """Você é Marcia Fervienza, astróloga e psicóloga com formação em psicologia profunda e mais de duas décadas de prática. Você está escrevendo uma seção do relatório de mapa natal de {name}.

DADOS DO MAPA PARA ESTA SEÇÃO:
{chart_context}

PASSAGENS RELEVANTES DO SEU ARQUIVO DE CONSULTAS E AULAS:
{retrieved_chunks_text}
{section_context_block}
INSTRUÇÕES PARA ESTA SEÇÃO ({section_title}):
{psychological_frame}

{depth_instruction}

REGRAS DE VOZ E ESTILO:
- Escreva em português brasileiro, diretamente para "você"
- Tom: íntimo, caloroso, direto — como uma carta de alguém que te conhece profundamente
- Preserve sua profundidade psicológica e síntese interpretativa
- NÃO liste posicionamentos mecanicamente — sintetize-os em narrativa
- Mencione tensões e contradições mas não as resolva ainda — elas serão retomadas no Fio Condutor
- Use as passagens do arquivo como fonte de sua voz e interpretação, não como texto para copiar
- NÃO mencione datas específicas, nomes de clientes ou referências ao passado
- Mantenha o gênero gramaticalmente consistente em todo o texto — use exclusivamente o gênero {gender} ao se referir ao cliente.

ORIENTAÇÃO AO FINAL DE SEÇÕES COM TENSÃO:
Se a seção nomear uma tensão ou contradição interna, termine com uma ou duas frases que ofereçam uma orientação prática — não uma resolução, mas uma bússola. Como essa pessoa pode trabalhar conscientemente com essa tensão? O tom não é de conselho, mas de reconhecimento: use frases como "o que esse posicionamento pede de você é..." ou "a melhor forma de trabalhar com isso é..." Seja específica ao posicionamento — não genérica. Nunca termine uma seção deixando a pessoa apenas no peso da tensão sem nenhuma orientação.

ASPECTOS LISTADOS — JÁ FILTRADOS E PRIORIZADOS:
Os aspectos listados já foram filtrados por prioridade e deduplicados. Não mencione aspectos que não estejam nesta lista. Se dois aspectos na lista tocam o mesmo tema psicológico, sintetize-os numa única passagem coerente em vez de descrevê-los separadamente. Repetição de temas é mais prejudicial do que omissão.

POSICIONAMENTOS ASTROLÓGICOS: Posicionamentos astrológicos podem ser mencionados naturalmente quando ajudam o leitor a se situar — por exemplo, "sua Lua em Áries" ou "com Saturno na sua casa dois". O que deve ser evitado é listar posicionamentos como coordenadas técnicas ou em sequência que pareça inventário. Os planetas devem aparecer como personagens da narrativa, não como dados de um relatório.

{style_rules}

REVISÃO FINAL OBRIGATÓRIA:
Revise o texto antes de retornar: elimine palavras inventadas, corrija o uso incorreto de pronomes reflexivos, e garanta concordância gramatical perfeita.

Escreva apenas o texto da seção. Sem títulos, sem explicações.
"""

FIO_CONDUTOR_PROMPT_TMPL = """Você é Marcia Fervienza. Você acabou de escrever um relatório completo de mapa natal para {name}.

RESUMO DO MAPA:
{full_chart_summary}

RELATÓRIO COMPLETO GERADO ATÉ AGORA:
{full_report_so_far}

Agora escreva o FIO CONDUTOR — a seção final de síntese.

Esta seção deve:
1. Nomear as principais contradições e tensões que apareceram ao longo do relatório
2. Mostrar como essas tensões não são falhas — são o motor desta pessoa
3. Reunir as orientações práticas que foram oferecidas em cada seção e mostrar como elas convergem num único movimento — não repetindo o que foi dito, mas sintetizando
4. Mostrar como as tensões individuais são expressões de uma mesma dinâmica central
5. Incluir explicitamente a dimensão do pai — Sol em Aquário na casa oito, Saturno em Leão retrógrado na casa dois, quadratura Sol-Urano — e como o que faltou nessa referência ecoa nas outras tensões do mapa
6. Terminar com algo concreto e singular que a pessoa possa carregar — não uma previsão, não uma lista, mas uma orientação central que emerge naturalmente de tudo que foi revelado neste mapa

Tom: mais elevado e conclusivo que as outras seções, mas sem ornamentação poética forçada. Profundidade sem dramatismo. A mesma voz direta e íntima do restante do relatório.

Mantenha o gênero gramaticalmente consistente em todo o texto — use exclusivamente o gênero {gender} ao se referir ao cliente.

PROIBIDO também aqui: metáforas dramáticas, "corta o ar como lâmina", "abismo", "chama que arde", "funda" (use "profunda"), construção repetitiva "Não é X. É Y.", "presença" como substantivo vago.

TAMBÉM PROIBIDO: (f) usar "nomear" como verbo padrão para tudo — varie com "identificar", "reconhecer", "colocar em palavras", "articular", "perceber", "distinguir"; (g) a construção "Não é X, é Y" — está limitada a UMA ocorrência por relatório inteiro, não por seção; (h) "retrógrada" como substantivo feminino — o planeta está sempre "retrógrado", nunca "a retrógrada"; (i) qualquer palavra em inglês não traduzida, especialmente "retrograde" — sempre "retrógrado"; (j) qualificadores defensivos desnecessários como "não porque seja naturalmente ambiciosa no sentido frio da palavra" — faça a afirmação diretamente sem recuar dela; (k) repetir o mesmo padrão interpretativo em seções diferentes.

CONVENÇÕES DE LINGUAGEM: Use sempre "em oposição", "em quadratura", "em trígono", "em conjunção", "em sextil" — nunca "na oposição", "na quadratura" etc. Planetas retrógrados são sempre descritos como "retrógrado" — nunca "a retrógrada". Mantenha "se regenerar rapidamente" em vez de "se regenerar rápido".

Extensão: 450-550 palavras.

Escreva apenas o texto. Sem título.
"""


def call_claude(prompt, max_tokens=SECTION_MAX_TOKENS):
    init_clients()
    resp = _anth.messages.create(
        model=CHAT_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def generate_section(section, chart, name, gender, section_context=None, context_instruction=None):
    """
    Generate a section's text. Optionally accepts the text of a previously-generated
    section plus an instruction for how to use it as additional context.
    """
    print(f"  retrieving for: {section['title']}", flush=True)

    # Append sextile-aspect queries to the section's base queries
    base_queries = list(section["queries"])
    sextile_qs = sextile_queries_for_section(section["name"], chart)
    if sextile_qs:
        base_queries.extend(sextile_qs)
        print(f"    + {len(sextile_qs)} sextile query/queries", flush=True)

    # Run all queries, accumulate matches
    all_matches = []
    for q in base_queries:
        ms = retrieve_chunks(q, planets_filter=section.get("planets_filter"))
        all_matches.extend(ms)

    # Dedupe by Pinecone ID, keep highest score
    by_id = {}
    for m in all_matches:
        if m.id not in by_id or m.score > by_id[m.id].score:
            by_id[m.id] = m
    chunks = sorted(by_id.values(), key=lambda x: x.score, reverse=True)[:PER_SECTION_CHUNK_CAP]
    if chunks:
        print(f"    -> {len(chunks)} chunks (top score={chunks[0].score:.2f})", flush=True)
    else:
        print(f"    -> 0 chunks (no matches above threshold {MIN_SCORE})", flush=True)

    chunks_text = format_chunks_for_prompt(chunks)
    chart_ctx = section_chart_context(section["name"], chart)

    # Build optional section-context block
    if section_context and context_instruction:
        section_context_block = (
            "\nCONTEXTO ADICIONAL — SEÇÃO JÁ ESCRITA DO MESMO RELATÓRIO:\n"
            f"{section_context.strip()}\n\n"
            f"USO DESTE CONTEXTO:\n{context_instruction.strip()}\n"
        )
    else:
        section_context_block = ""

    prompt = SECTION_PROMPT_TMPL.format(
        name=name,
        chart_context=chart_ctx,
        retrieved_chunks_text=chunks_text,
        section_context_block=section_context_block,
        section_title=section["title"],
        psychological_frame=section["psychological_frame"],
        depth_instruction=section.get("depth_instruction", DEPTH_TIER_3),
        gender=gender,
        style_rules=SECTION_STYLE_RULES,
    )

    print(f"    calling Claude...", flush=True)
    text = call_claude(prompt, max_tokens=SECTION_MAX_TOKENS)

    # After Claude succeeds, mark this section's filtered aspects as "described"
    # so subsequent sections don't re-describe them. Audited list is what was
    # actually passed to Claude (already excludes previously-described aspects).
    for a in _section_aspect_audit.get(section["name"], []):
        described_aspect_themes.add(_aspect_dedup_key(a))

    return text, chunks


def build_full_chart_summary(chart):
    """Compact summary string used by the fio condutor prompt."""
    p = chart["points"]
    asc = chart["ascendant"]
    mc = chart.get("midheaven", {})
    lines = []
    lines.append(f"Ascendente: {fmt_position(asc)}")
    if mc:
        lines.append(f"Meio-do-Céu: {fmt_position(mc)}")
    for key in ("sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn",
                "uranus", "neptune", "pluto", "chiron", "lilith",
                "north_node", "south_node", "ceres", "vesta", "juno", "pallas"):
        if key in p:
            lines.append(f"{PLANET_LABEL_PT.get(key, key)}: {fmt_position(p[key])} (casa {p[key]['house']})")
    lines.append("")
    lines.append("Aspectos principais:")
    for a in chart.get("aspects", []):
        pa = PLANET_LABEL_PT.get(a["planet_a"], a["planet_a"])
        pb = PLANET_LABEL_PT.get(a["planet_b"], a["planet_b"])
        lines.append(f"  {pa} {a['type_pt']} {pb} (orbe {a['orb']:.1f}°)")
    return "\n".join(lines)


def generate_fio_condutor(name, chart, full_report, gender):
    init_clients()
    summary = build_full_chart_summary(chart)
    prompt = FIO_CONDUTOR_PROMPT_TMPL.format(
        name=name,
        full_chart_summary=summary,
        full_report_so_far=full_report,
        gender=gender,
    )
    return call_claude(prompt, max_tokens=FIO_CONDUTOR_MAX_TOKENS)


# ============================================================
# MAIN
# ============================================================
# ============================================================
# POST-GENERATION CLEANUP PASS
# ============================================================
# Pattern that catches "Não é X. É Y" and "Não é X, é Y" (single sentence-pair).
# - Group 1 = X (the negated thing, anything but . , ! ? before the separator)
# - Group 2 = separator (. or ,)
# - Group 3 = É or é (case preserved for the rewrite)
# - Group 4 = Y (the affirmed thing, anything but . ! ? before the sentence end)
# - Group 5 = sentence ending punctuation (. ! ?)
#
# IGNORECASE on the leading "Não é" — we need to catch both sentence-starting
# "Não é X, é Y" AND mid-sentence "…, não é X, é Y" occurrences. Without this,
# a phrase like "para você, emoção sem clareza não é intimidade, é ameaça"
# would slip through cleanup entirely.
_NEG_AFFIRM_RE = re.compile(
    r"[Nn]ão é ([^.,!?]+?)([.,])\s+(É|é) ([^.!?]+?)([.!?])"
)

# Bare English "retrograde" — match as a whole word, case-insensitive.
_EN_RETROGRADE_RE = re.compile(r"\bretrograde\b", re.IGNORECASE)

# "a retrógrada" / "à retrógrada" used as standalone feminine noun.
# We FLAG these (don't auto-rewrite) because correct restructure needs context.
_FEM_RETROGRADA_RE = re.compile(r"\b([Aa]|[Àà])\s+retrógrada\b")

# Mid-sentence capital "É". Claude has a tic of capitalizing the é-particle
# mid-sentence for emphasis ("A questão É que...", "onde ele se dissolve É
# no trabalho..."). Match a capital É preceded by a Portuguese lowercase
# letter or a mid-sentence connector (comma / semicolon / em-dash) + space,
# but NOT preceded by a sentence terminator. The negative look-behind on
# ".!?" is redundant given the positive look-behind on the lowercase set
# but keeps the intent explicit.
_MID_SENTENCE_UPPERCASE_E_RE = re.compile(
    r"(?<=[a-záéíóúãõçâêôàïüA-Z,;\-—]\s)É\b"
)

# Specific typo / voice fixes that surfaced in reviewed reports. Kept as
# narrow, deterministic substitutions rather than generic pattern rewrites
# to avoid collateral damage on legitimate uses.
_TARGETED_FIXES = [
    # Typo: "gerosa" is not a word — the intended word is "generosa"
    (re.compile(r"\bgerosa\b"), "generosa"),
    # Voice: the Vênus section drifts to third-person "nela"/"ela mesma"
    # about the client. These exact phrasings collapse to direct address.
    (re.compile(r"\breconhecem nela\b"), "reconhecem em você"),
    (re.compile(r"\bentendida naquilo que ela mesma\b"), "entendida naquilo que você mesma"),
]

# House-number normalization. Report body alternates between "casa sete"
# (spelled out) and "casa 7" (digits) — often in the same section. Pick
# digits everywhere: subtitles under section titles already use digits,
# and digits are more scannable in a report that reads as much like a
# reference document as a book. Only touches the "casa <word>" pattern
# (with optional capitalization); "casa 7" stays as-is. Word boundaries
# on both sides guard against accidental matches in other contexts.
_HOUSE_WORD_TO_DIGIT = [
    ("um", "1"), ("uma", "1"),
    ("dois", "2"), ("duas", "2"),
    ("três", "3"),
    ("quatro", "4"),
    ("cinco", "5"),
    ("seis", "6"),
    ("sete", "7"),
    ("oito", "8"),
    ("nove", "9"),
    ("dez", "10"),
    ("onze", "11"),
    ("doze", "12"),
]
_CASA_NORMALIZATION_RES = [
    # IGNORECASE catches "casa Sete" / "Casa Sete" / "casa sete" variants
    # uniformly. Group 1 preserves the original case of the leading letter
    # so "Casa" stays "Casa" and "casa" stays "casa" in the replacement.
    (re.compile(rf"\b(C|c)asa {word}\b", flags=re.IGNORECASE), rf"\1asa {digit}")
    for word, digit in _HOUSE_WORD_TO_DIGIT
]


def cleanup_pass(text: str):
    """
    Run report-wide cleanup on the fully-assembled text.
    Returns (cleaned_text, list_of_change_dicts).
    """
    changes = []

    # ---------- 1. "Não é X. É Y" / "Não é X, é Y" — keep first, rewrite rest ----------
    matches = list(_NEG_AFFIRM_RE.finditer(text))
    if len(matches) > 1:
        # Work in reverse so positions stay valid as we slice.
        for m in reversed(matches[1:]):
            orig = m.group(0)
            y = m.group(4).strip()
            ending = m.group(5)
            # Drop the "Não é X[.,] " portion. Preserve the case of the
            # affirming particle — "É" for sentence-starts, "é" for
            # mid-sentence occurrences — so the rewrite is grammatical.
            affirm = m.group(3)
            rewritten = f"{affirm} {y}{ending}"
            text = text[:m.start()] + rewritten + text[m.end():]
            changes.append({
                "type": "negative_construction",
                "before": orig,
                "after": rewritten,
                "auto_fixed": True,
            })

    # ---------- 2. English "retrograde" → "retrógrado" ----------
    for m in list(_EN_RETROGRADE_RE.finditer(text))[::-1]:
        orig = m.group(0)
        # Preserve capitalization of first letter
        replacement = "Retrógrado" if orig[0].isupper() else "retrógrado"
        text = text[:m.start()] + replacement + text[m.end():]
        changes.append({
            "type": "english_word",
            "before": orig,
            "after": replacement,
            "auto_fixed": True,
        })

    # ---------- 3. "a retrógrada" / "à retrógrada" as standalone noun — FLAG ONLY ----------
    # Restructuring requires knowing the referent (Lua, Vênus, etc.) and
    # surrounding clause; safer to surface for manual review than to guess.
    for m in _FEM_RETROGRADA_RE.finditer(text):
        # Capture a small context window (40 chars each side) for the report
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 40)
        snippet = text[start:end].replace("\n", " ")
        changes.append({
            "type": "feminine_noun_retrograda",
            "before": m.group(0),
            "context": f"…{snippet}…",
            "auto_fixed": False,
            "note": "needs manual restructure — 'retrógrada' used as standalone noun",
        })

    # ---------- 4. Mid-sentence capital É → lowercase é ----------
    mid_e_count = 0
    for m in list(_MID_SENTENCE_UPPERCASE_E_RE.finditer(text))[::-1]:
        text = text[:m.start()] + "é" + text[m.end():]
        mid_e_count += 1
    if mid_e_count:
        changes.append({
            "type": "mid_sentence_uppercase_e",
            "count": mid_e_count,
            "auto_fixed": True,
        })

    # ---------- 5. Targeted typo / voice fixes ----------
    for pat, replacement in _TARGETED_FIXES:
        text, n = pat.subn(replacement, text)
        if n:
            changes.append({
                "type": "targeted_fix",
                "pattern": pat.pattern,
                "replacement": replacement,
                "count": n,
                "auto_fixed": True,
            })

    # ---------- 6. House-number normalization → digit form ----------
    casa_total = 0
    for pat, replacement in _CASA_NORMALIZATION_RES:
        text, n = pat.subn(replacement, text)
        casa_total += n
    if casa_total:
        changes.append({
            "type": "casa_normalization",
            "count": casa_total,
            "form": "digit",
            "auto_fixed": True,
        })

    return text, changes


def _print_cleanup_report(changes: list):
    if not changes:
        print("  ✅ Nothing to clean — report already conforms to global rules.")
        return
    by_type = {}
    for c in changes:
        by_type.setdefault(c["type"], []).append(c)

    auto_count = sum(1 for c in changes if c.get("auto_fixed"))
    flag_count = sum(1 for c in changes if not c.get("auto_fixed"))
    print(f"  Applied: {auto_count} auto-fixes · Flagged: {flag_count} for manual review")

    if "negative_construction" in by_type:
        items = by_type["negative_construction"]
        print(f"\n  [Não é X · É Y] — first kept, {len(items)} rewritten:")
        for c in items:
            print(f"    BEFORE: {c['before']}")
            print(f"    AFTER:  {c['after']}")
            print()

    if "english_word" in by_type:
        items = by_type["english_word"]
        print(f"\n  [English 'retrograde'] — {len(items)} replaced with 'retrógrado':")
        for c in items:
            print(f"    {c['before']!r} → {c['after']!r}")

    if "feminine_noun_retrograda" in by_type:
        items = by_type["feminine_noun_retrograda"]
        print(f"\n  ⚠️  [retrógrada as feminine noun] — {len(items)} flagged:")
        for c in items:
            print(f"    found: {c['before']!r}")
            print(f"    context: {c['context']}")
            print(f"    {c['note']}")
            print()


# ============================================================
# CROSS-SECTION CONTEXT MAP (module-level — used by both CLI & API paths)
# ============================================================
CONTEXT_DEPENDENCIES = {
    "urano": (
        ["lua", "casa_4"],
        (
            "A seção da Lua e da Casa 4 já foram escritas e estabelecem o padrão emocional, a dinâmica familiar "
            "de origem e o ambiente da infância desta pessoa. Use esse contexto EXPLICITAMENTE para interpretar "
            "como as experiências da infância moldaram a relação desta pessoa com a maternidade ou paternidade, "
            "a criatividade e os filhos. A forma como ela foi cuidada — incluindo as dinâmicas de poder da Casa 4 "
            "e o modelo de cuidado da Lua — deve aparecer como fundação visível do que está sendo descrito aqui. "
            "O signo e planeta da cúspide da casa 5 devem complementar ou contradizer o que foi estabelecido "
            "anteriormente, e essa relação deve ser explicada explicitamente."
        ),
    ),
    "venus_marte": (
        "lua",
        (
            "A seção da Lua já foi escrita e estabelece o padrão emocional e a dinâmica familiar de origem "
            "desta pessoa. Use esse contexto para interpretar como esses padrões precoces se repetem ou se "
            "transformam nos relacionamentos adultos — o que a pessoa busca, recria ou tenta reparar num parceiro."
        ),
    ),
}


# ============================================================
# PARALLEL EXECUTION CONFIG
# ============================================================
# Which sections depend on which others. Dependent sections wait for their
# prereqs to finish (and receive the prereq texts as context) before they
# run their own Claude call. All sections not listed here have no
# dependencies and run as soon as a worker is available.
#
# Must be kept in sync with CONTEXT_DEPENDENCIES above — the deps listed
# here are the same prior-section names that CONTEXT_DEPENDENCIES uses to
# look up the context instruction text. SECTION_DEPENDENCIES is the
# topological view (scheduler input); CONTEXT_DEPENDENCIES is the prompt
# wiring view (text input).
SECTION_DEPENDENCIES = {
    "urano":       ["lua", "casa_4"],   # needs Lua + Casa 4 context
    "venus_marte": ["lua"],             # needs Lua context
    # all other sections have no dependencies
}

# Cap concurrent Claude API calls. 6 fits comfortably within Anthropic's
# per-minute rate limits for the Sonnet tier we're using, while giving
# meaningful parallelism (16 sections / 6 workers ≈ 3 sequential rounds
# instead of 16 sequential calls). Override via env var if needed.
PARALLEL_MAX_WORKERS = int(os.environ.get("PARALLEL_MAX_WORKERS", "6"))


# ============================================================
# PUBLIC API: generate_report(chart_dict, ...)
# ============================================================
def generate_report(
    chart: dict,
    sections_only: list = None,
    limit: int = None,
    no_fio: bool = False,
    write_file: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Generate the natal report for a chart dict.

    chart           — AstroAPI.cloud-style chart structure with 'name', 'gender',
                      'points', 'ascendant', 'midheaven', 'aspects'.
    sections_only   — list of section names to generate (skips Fio Condutor).
    limit           — generate only first N sections (skips Fio Condutor).
    no_fio          — explicitly skip Fio Condutor.
    write_file      — also save to OUTPUT_DIR (only useful in CLI usage).
    verbose         — print progress to stdout (CLI uses True, API uses False).

    Returns: {
        "report": str,                # full markdown report
        "name": str,
        "gender": str,
        "sections": list[str],        # section names that were generated
        "elapsed_seconds": float,
        "aspect_audit": dict,         # section_name -> list of aspect labels
        "cleanup_changes": list,      # post-generation cleanup actions
    }
    Raises ValueError on bad input.
    """
    name = chart.get("name", "Cliente")
    gender = chart.get("gender")
    if gender not in ("feminino", "masculino"):
        raise ValueError(
            f"chart must include 'gender' field with value 'feminino' or 'masculino' (got: {gender!r})"
        )

    def log(msg, **kw):
        if verbose:
            print(msg, **kw)

    log(f"=== Generating natal report for: {name} (gênero: {gender}) ===")

    # Reset cross-section tracking for this run (module-level state)
    described_aspect_themes.clear()
    _section_aspect_audit.clear()

    sections = build_sections(chart)

    if sections_only:
        wanted = {s.strip() for s in sections_only if s.strip()}
        sections = [s for s in sections if s["name"] in wanted]
        log(f"Filter sections_only: running {len(sections)} sections: {[s['name'] for s in sections]}")
    elif limit:
        sections = sections[:limit]
        log(f"Limit: running first {len(sections)} sections")

    skip_fio = bool(sections_only) or bool(limit) or no_fio

    full_report = f"# Mapa Natal — {name}\n\n"
    section_texts: dict = {}
    section_lookup = {s["name"]: s for s in sections}
    start = time.time()

    # ---- PRE-COMPUTE PHASE (sequential, fast) ----
    # Walk sections in canonical order to populate per-section aspect dedup
    # state. No LLM calls — just chart-data filtering. After this loop,
    # every section's _section_aspect_audit entry reflects the same dedup
    # behavior it would have had under sequential generation. The parallel
    # phase below can then safely call aspects_for_section_filtered() from
    # multiple threads — it short-circuits to the cached result.
    for sec in sections:
        aspects_for_section_filtered(sec["name"], chart)
        for a in _section_aspect_audit.get(sec["name"], []):
            described_aspect_themes.add(_aspect_dedup_key(a))

    # ---- PARALLEL CLAUDE PHASE ----
    log(f"\n--- Generating {len(sections)} sections in parallel (max {PARALLEL_MAX_WORKERS} workers) ---", flush=True)
    parallel_start = time.time()
    futures: dict = {}

    def _run_section(sec):
        """Executor task: wait for dependencies, build context, call Claude."""
        deps = SECTION_DEPENDENCIES.get(sec["name"], [])
        # Filter deps to those actually present in this run (handles --only / --limit)
        deps = [d for d in deps if d in section_lookup]

        section_context = None
        context_instruction = None
        if deps:
            dep_texts = []
            for dep_name in deps:
                # Block until each dependency's section text is ready.
                # In a ThreadPoolExecutor, Future.result() releases the GIL
                # while waiting, so this doesn't starve other workers.
                dep_text = futures[dep_name].result()
                dep_title = section_lookup[dep_name]["title"]
                dep_texts.append(f"### {dep_title}\n\n{dep_text}")
            section_context = "\n\n".join(dep_texts)
            # Pull the prompt-wiring instruction from CONTEXT_DEPENDENCIES.
            if sec["name"] in CONTEXT_DEPENDENCIES:
                _, context_instruction = CONTEXT_DEPENDENCIES[sec["name"]]

        text, _chunks = generate_section(
            sec, chart, name, gender,
            section_context=section_context,
            context_instruction=context_instruction,
        )
        log(f"  ✓ {sec['title']}  ({len(text.split())} words)", flush=True)
        return text

    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_MAX_WORKERS) as executor:
        # Submit every section at once. Dependent sections block on their
        # prereqs' futures internally; the executor's worker pool naturally
        # caps concurrency.
        for sec in sections:
            futures[sec["name"]] = executor.submit(_run_section, sec)
        # Drain results — if any task raised, this re-raises here.
        for sec in sections:
            section_texts[sec["name"]] = futures[sec["name"]].result()

    parallel_elapsed = time.time() - parallel_start
    log(f"\nParallel section phase complete in {parallel_elapsed:.1f}s", flush=True)

    # ---- ASSEMBLE IN CANONICAL ORDER ----
    # Output ordering follows the `sections` list, not the parallel
    # completion order, so the report reads identically to a sequential run.
    for sec in sections:
        full_report += f"\n## {sec['title']}\n\n{section_texts[sec['name']]}\n"

    if not skip_fio:
        log(f"\n--- Fio Condutor ---", flush=True)
        fio = generate_fio_condutor(name, chart, full_report, gender)
        full_report += f"\n## Fio Condutor\n\n{fio}\n"
        log(f"    {len(fio.split())} words", flush=True)

    # Post-generation cleanup
    log(f"\n--- Post-generation cleanup ---", flush=True)
    full_report, cleanup_changes = cleanup_pass(full_report)
    if verbose:
        _print_cleanup_report(cleanup_changes)

    # Optionally save to disk
    if write_file:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace("/", "_").replace(" ", "_")
        out_path = OUTPUT_DIR / f"{safe_name}_natal_report.txt"
        out_path.write_text(full_report, encoding="utf-8")
        log(f"Saved to: {out_path}")

    elapsed = time.time() - start

    # Build a compact aspect audit (for return + verbose print)
    aspect_audit = {}
    for sec in sections:
        nm = sec["name"]
        used = _section_aspect_audit.get(nm, [])
        aspect_audit[nm] = [
            f"{a['_pa_pt']}-{a['_pb_pt']} {a['type_pt']} (orbe {a['orb']:.1f}°, T{a['_combined_tier']})"
            for a in used
        ]

    if verbose:
        print(f"\n{'='*70}")
        print(f"REPORT COMPLETE — {len(sections)} sections, {elapsed:.0f}s elapsed")
        print(f"{'='*70}\n")
        print(f"{'='*70}")
        print("ASPECT AUDIT — what was passed to each section (post-filter, post-dedup)")
        print(f"{'='*70}")
        for nm, items in aspect_audit.items():
            if not items:
                print(f"  {nm:<14}  (no new aspects)")
            else:
                print(f"  {nm:<14}  ({len(items)} aspect{'s' if len(items)!=1 else ''}):")
                for s in items:
                    print(f"     • {s}")
        print(f"{'='*70}\n")
        print(full_report)

    return {
        "report": full_report,
        "name": name,
        "gender": gender,
        "sections": [s["name"] for s in sections],
        "elapsed_seconds": elapsed,
        "aspect_audit": aspect_audit,
        "cleanup_changes": cleanup_changes,
    }


# ============================================================
# CLI ENTRY (thin wrapper around generate_report)
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("chart_json", help="Path to chart JSON file")
    parser.add_argument("--only", default=None,
                        help="Comma-separated section names to generate (skips fio condutor)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Generate only first N sections (skips fio condutor)")
    parser.add_argument("--no-fio", action="store_true",
                        help="Skip the fio condutor section")
    args = parser.parse_args()

    chart_path = Path(args.chart_json)
    if not chart_path.exists():
        print(f"ERROR: chart JSON not found: {chart_path}")
        sys.exit(1)

    chart = json.loads(chart_path.read_text(encoding="utf-8"))

    sections_only = None
    if args.only:
        sections_only = [s.strip() for s in args.only.split(",") if s.strip()]

    try:
        generate_report(
            chart,
            sections_only=sections_only,
            limit=args.limit,
            no_fio=args.no_fio,
            write_file=True,
            verbose=True,
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
