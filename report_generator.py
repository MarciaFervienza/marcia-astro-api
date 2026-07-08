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


def compute_parental_clusters(chart):
    """Compute two independent clusters that count converging factors for
    parental accentuation. Two design principles:

      1. NÃO se usa 'casa=progenitor'. Os clusters medem funções simbólicas
         (autoridade/estrutura via Sol+Saturno; cuidado/vínculo via Lua).
      2. Aspectos SÓ contam se envolverem luminar (Sol ou Lua). Aspectos
         geracionais (Saturno-Urano/Netuno/Plutão e transpessoal-transpessoal)
         NÃO contam — indicam colorido geracional, não história pessoal.
      3. Cada cluster dispara com 2 OU MAIS fatores presentes.
      4. Aspectos duros = conjunção, oposição, quadratura.
      5. Posições de casa contam por si (não são geracionais); ficam
         suprimidas quando a hora é desconhecida.

    Fator de contexto (casa 4): Plutão/Saturno/Urano na 4 NÃO disparam
    cluster; se algum cluster já disparou, reforçam a leitura como
    ambiente doméstico. Se nenhum cluster disparou, ficam silenciosos.

    Retorna dict com fatores, contadores, flags de trigger e uma lista
    de aspectos GERACIONAIS explicitamente EXCLUÍDOS (para transparência
    e auditoria no meta da resposta).
    """
    p = chart.get("points") or {}
    aspects = chart.get("aspects") or []
    time_unknown = _time_is_unknown(chart)
    moon_meta = _moon_ingress_meta(chart)
    moon_uncertain = time_unknown and bool(moon_meta.get("moon_sign_uncertain"))

    HARD_TYPES = {"conjunction", "opposition", "square"}

    def _find_hard(k1, k2):
        """Retorna o dict do aspecto duro entre k1 e k2 se existir."""
        for a in aspects:
            if a.get("type") not in HARD_TYPES:
                continue
            pa = a.get("planet_a")
            pb = a.get("planet_b")
            if (pa == k1 and pb == k2) or (pa == k2 and pb == k1):
                return a
        return None

    def _fmt_aspect(label, aspect):
        atype = aspect.get("type_pt") or aspect.get("type")
        orb = aspect.get("orb", 0.0)
        return f"{label} {atype} (orbe {orb:.1f}°)"

    # ----- CLUSTER 1 — Autoridade/estrutura (Sol + Saturno, sem Lua) -----
    c1 = []
    if not time_unknown:
        sh = (p.get("sun") or {}).get("house")
        if sh == 8:
            c1.append("Sol na casa 8")
        elif sh == 12:
            c1.append("Sol na casa 12")
    for k, pt_label in (("saturn", "Sol-Saturno"),
                        ("pluto", "Sol-Plutão"),
                        ("uranus", "Sol-Urano")):
        a = _find_hard("sun", k)
        if a:
            c1.append(_fmt_aspect(pt_label, a))
    if not time_unknown:
        sath = (p.get("saturn") or {}).get("house")
        if sath == 12:
            c1.append("Saturno na casa 12")

    # ----- CLUSTER 2 — Cuidado/vínculo (Lua, sem Sol) -----
    c2 = []
    for k, pt_label in (("pluto", "Lua-Plutão"),
                        ("saturn", "Lua-Saturno"),
                        ("neptune", "Lua-Netuno"),
                        ("uranus", "Lua-Urano")):
        a = _find_hard("moon", k)
        if a:
            c2.append(_fmt_aspect(pt_label, a))
    if not time_unknown:
        mh = (p.get("moon") or {}).get("house")
        if mh == 8:
            c2.append("Lua na casa 8")
        elif mh == 12:
            c2.append("Lua na casa 12")
    # Lua em queda/exílio — só se o signo for confiável (não moon_uncertain)
    if not moon_uncertain:
        msign = ((p.get("moon") or {}).get("sign") or "").lower()
        if msign == "scorpio":
            c2.append("Lua em Escorpião (queda)")
        elif msign == "capricorn":
            c2.append("Lua em Capricórnio (exílio)")

    # ----- Casa 4: reforço contextual (NÃO dispara sozinho) -----
    h4 = []
    if not time_unknown:
        for k, pt_label in (("pluto", "Plutão"), ("saturn", "Saturno"), ("uranus", "Urano")):
            if (p.get(k) or {}).get("house") == 4:
                h4.append(f"{pt_label} na casa 4")

    # ----- Aspectos GERACIONAIS explicitamente excluídos (auditoria) -----
    excluded = []
    GEN_PAIRS = [
        ("saturn", "uranus", "Saturno-Urano"),
        ("saturn", "neptune", "Saturno-Netuno"),
        ("saturn", "pluto", "Saturno-Plutão"),
        ("uranus", "neptune", "Urano-Netuno"),
        ("uranus", "pluto", "Urano-Plutão"),
        ("neptune", "pluto", "Netuno-Plutão"),
    ]
    for k1, k2, label in GEN_PAIRS:
        a = _find_hard(k1, k2)
        if a:
            excluded.append(_fmt_aspect(label, a) + " — geracional, sem luminar")

    return {
        "cluster_1_authority": {
            "factors": c1,
            "count": len(c1),
            "triggered": len(c1) >= 2,
        },
        "cluster_2_care": {
            "factors": c2,
            "count": len(c2),
            "triggered": len(c2) >= 2,
        },
        "house_4_context": h4,
        "excluded_generational_aspects": excluded,
        "house_factors_suppressed_by_unknown_time": time_unknown,
    }


# ----- Frases-âncora fixas para os textos dos clusters -----
_CLUSTER_1_ANCHOR = (
    "Há uma concentração significativa de indicadores ligados à função de autoridade e estrutura na sua "
    "formação. Essa área do mapa está acentuada, e costuma corresponder a uma relação marcante com quem "
    "exerceu esse papel estruturante — pode ter sido ausência, perda ou distância, ou o oposto: uma "
    "presença exigente, rígida, que pesou. Veja o que ressoa com a sua experiência."
)

_CLUSTER_2_ANCHOR = (
    "Há uma concentração significativa de indicadores ligados à função de cuidado e ao vínculo de "
    "segurança emocional da infância. Essa área do mapa está acentuada, e costuma corresponder a uma "
    "experiência marcante com a principal figura de cuidado — pode ter sido ausência, instabilidade ou "
    "ruptura, ou uma presença que sufocava, invadia ou cobrava. Veja o que ressoa com a sua experiência."
)

_CLUSTERS_BOTH_ANCHOR_FIO = (
    "Há indicadores convergentes tanto na função de autoridade e estrutura quanto na função de cuidado "
    "e vínculo. Quando o mapa acumula os dois, costuma apontar para uma infância marcada por transformações "
    "profundas na estrutura familiar — perdas, rupturas, ou mudanças que reorganizaram o campo emocional. "
    "A natureza específica disso é sua para reconhecer: o mapa mostra a intensidade, não o enredo."
)


def _cluster_addendum_for_section(clusters, which):
    """Gera o addendum para o psychological_frame das seções de Sol/Saturno
    ou Lua com base no cluster que disparou. which ∈ {'sol_saturno', 'lua'}.
    Retorna string vazia se o cluster respectivo não disparou.

    O addendum instrui o Claude a usar a frase-âncora como ABERTURA da
    seção (não literal — desenvolve na voz do relatório) e deixa a
    interpretação sempre em modo de possibilidade a reconhecer, nunca
    afirmando 'seu pai' ou 'sua mãe' nem a natureza específica.
    """
    if which == "sol_saturno":
        cl = clusters.get("cluster_1_authority") or {}
        if not cl.get("triggered"):
            return ""
        factors = cl.get("factors") or []
        anchor = _CLUSTER_1_ANCHOR
        cluster_label = "AUTORIDADE E ESTRUTURA"
    elif which == "lua":
        cl = clusters.get("cluster_2_care") or {}
        if not cl.get("triggered"):
            return ""
        factors = cl.get("factors") or []
        anchor = _CLUSTER_2_ANCHOR
        cluster_label = "CUIDADO E VÍNCULO"
    else:
        return ""

    factors_list = "\n".join(f"  · {f}" for f in factors)

    return (
        f"\n\n[CLUSTER DE ACENTUAÇÃO {cluster_label} — DISPAROU]\n"
        f"Este mapa acumula {len(factors)} indicadores convergentes nesta função "
        f"(limiar de disparo: 2 ou mais):\n"
        f"{factors_list}\n\n"
        f"FRASE-ÂNCORA (use como PONTO DE PARTIDA para esta seção, "
        f"desenvolvendo na voz do relatório — não copie literalmente, mas "
        f"preserve a estrutura de possibilidade a reconhecer):\n"
        f"\"{anchor}\"\n\n"
        f"REGRAS PARA O DESENVOLVIMENTO:\n"
        f"(a) NUNCA escreva 'seu pai', 'sua mãe', 'seu pai foi', 'sua mãe era'. "
        f"Refira-se sempre como 'quem exerceu essa função', 'a figura que "
        f"estruturou/cuidou', 'esse papel na sua história'.\n"
        f"(b) SEMPRE apresente como POSSIBILIDADE. Use 'pode ter sido', "
        f"'costuma corresponder a', 'em muitos casos aparece como'. NUNCA "
        f"afirme uma versão única (nem 'ausência' nem 'excesso') como se "
        f"fosse fato — a geometria mostra intensidade, não enredo.\n"
        f"(c) ABRA o leque de possibilidades opostas (ex.: ausência OU "
        f"presença invasiva) e convide o leitor a reconhecer qual ressoa.\n"
        f"(d) Esta seção é o ÚNICO lugar do relatório onde esta função é "
        f"desenvolvida — o Fio Condutor apenas referenciará brevemente. Não "
        f"deixe para depois.\n"
    )


def find_aspect(chart, key_a, key_b, aspect_type):
    """Retorna o dict do aspecto entre key_a e key_b do tipo aspect_type se
    ele existir na lista chart["aspects"] (já filtrada in-sign), em qualquer
    ordem. Caso contrário retorna None.

    Usado por instruções condicionais em build_sections que só devem ser
    emitidas quando o aspecto REALMENTE existe no mapa — evitando a família
    de erro em que uma instrução hardcoded referia um aspecto do Cliente
    Teste como se todo mapa tivesse.
    """
    if not aspect_type:
        return None
    for a in chart.get("aspects", []):
        if a.get("type") != aspect_type:
            continue
        pa = a.get("planet_a")
        pb = a.get("planet_b")
        if (pa == key_a and pb == key_b) or (pa == key_b and pb == key_a):
            return a
    return None


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


def _time_is_unknown(chart):
    """True quando o endpoint sinalizou que o horário de nascimento não foi
    informado. Nesse caso o Ascendente/MC/casas foram calculados a partir do
    default meio-dia e NÃO são confiáveis — o contexto entregue ao Claude
    precisa omitir tudo que depende de hora, e as seções que dependem dela
    são reformuladas ou puladas. A flag é injetada em app.py antes de chamar
    generate_report."""
    return bool(chart.get("_unknown_birth_time", False))


def _moon_ingress_meta(chart):
    """Retorna o dict de meta lunar computado em app.py (via detect_moon_ingress
    quando a hora é desconhecida, check_moon_cusp quando é conhecida). Usado
    para reformular a seção da Lua quando ela mudou de signo no dia."""
    return chart.get("_moon_meta", {}) or {}


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

    When time is unknown (chart["_unknown_birth_time"]=True) all "na casa N"
    references are stripped and the Ascendente/MC lines are omitted from the
    abertura/triade contexts — nothing that depends on birth time reaches
    Claude, so Claude can't write about it.
    """
    p = chart["points"]
    asc = chart["ascendant"]
    mc = chart.get("midheaven", {})
    time_unknown = _time_is_unknown(chart)
    moon_meta = _moon_ingress_meta(chart)
    moon_uncertain = time_unknown and bool(moon_meta.get("moon_sign_uncertain"))

    # fmt_pos_local: hora conhecida → "signo grau° na casa N"; desconhecida → só "signo grau°"
    def _pl(planet):
        base = fmt_position(planet)
        if time_unknown:
            return base
        return f"{base} na casa {planet['house']}"

    # Prefixo de reforço em CADA seção quando a hora é desconhecida — sem isso
    # o Claude ainda pode inferir/repetir posições de casa vindas dos trechos
    # autorais recuperados do Pinecone (que normalmente incluem "casa N").
    # Colocar isso na frente de cada bloco de contexto é a garantia de que a
    # regra chegue à consciência do modelo em cada chamada, não só nas seções
    # de abertura/triade/lua onde a NOTA aparece explicitamente.
    # SIGNOS REAIS de todos os planetas — visível em cada seção como
    # referência anti-alucinação. Sem isso o modelo pode escrever "Plutão
    # em Escorpião" por associação com regência quando o Plutão real está
    # em outro signo. Também suprime a Lua quando o signo é indeterminado.
    _signs_ref_bits = []
    for _k in ("sun","moon","mercury","venus","mars","jupiter","saturn",
               "uranus","neptune","pluto","chiron","lilith",
               "north_node","south_node","ceres","vesta","juno","pallas"):
        if _k not in p:
            continue
        if _k == "moon" and moon_uncertain:
            _signs_ref_bits.append("Lua INDETERMINADO")
            continue
        _signs_ref_bits.append(f"{PLANET_LABEL_PT.get(_k, _k)} {p[_k]['sign_pt']}")
    _signs_ref_prefix = (
        "[SIGNOS REAIS DE TODOS OS PLANETAS NESTE MAPA — use SEMPRE estes "
        "signos, NUNCA o signo de regência/domicílio/exaltação]: "
        + ", ".join(_signs_ref_bits) + ".\n\n"
    )

    _tunk_prefix = _signs_ref_prefix + ((
        "[REGRA ABSOLUTA PARA ESTA SEÇÃO] O horário de nascimento é desconhecido. "
        "NUNCA mencione a casa astrológica de nenhum planeta ou ponto — não escreva "
        "'na casa X', 'da casa X', 'casa X', nem versões por extenso ('casa quatro', "
        "'oitava casa' etc.). Mesmo que os trechos autorais abaixo façam essa "
        "menção, VOCÊ NÃO PODE reproduzi-la. Trabalhe apenas por signo e por "
        "aspectos. Interprete o que os trechos dizem sobre o planeta em geral, "
        "descartando qualquer referência a casa. Também NÃO mencione o "
        "Ascendente, Meio-do-Céu, Descendente ou Fundo do Céu (Imum Coeli).\n\n"
    ) if time_unknown else "")

    # Compute filtered aspects for this section once (also records into _section_aspect_audit)
    filtered_aspects = aspects_for_section_filtered(section_name, chart)
    aspects_line = fmt_filtered_aspects(filtered_aspects)

    if section_name == "abertura":
        if moon_uncertain:
            # Ascendente ausente E signo da Lua indeterminado. Não podemos
            # apresentar tensão Sol×Lua (dependeria de saber o signo da Lua)
            # nem coerência entre eles. Foco só no Sol.
            return _tunk_prefix + (
                f"Sol: {fmt_position(p['sun'])}\n"
                f"[Lua: signo INDETERMINADO — pode estar em "
                f"{moon_meta.get('moon_sign_before')} ou em "
                f"{moon_meta.get('moon_sign_after')}. A nota no topo do "
                f"relatório já apresentou as duas leituras possíveis.]\n\n"
                f"[REGRA CRÍTICA PARA ESTA ABERTURA: NÃO nomeie um signo "
                f"específico para a Lua em nenhum momento. NÃO declare "
                f"'tensão entre Sol e Lua' nem 'coerência entre Sol e Lua' — "
                f"qualquer afirmação assim tomaria partido por um dos dois "
                f"signos possíveis. Escreva uma abertura calorosa focada na "
                f"direção e qualidade do Sol. Se tocar a vida emocional, "
                f"refira-a genericamente ('sua sensibilidade interior', "
                f"'sua vida afetiva') sem ancorar em signo lunar.]"
            )
        if time_unknown:
            # Lua estável no dia — signo é confiável, só casas/Asc ausentes.
            return _tunk_prefix + (
                f"Sol: {fmt_position(p['sun'])}\n"
                f"Lua: {fmt_position(p['moon'])}\n"
                f"[NOTA: Este mapa foi calculado sem horário de nascimento. "
                f"Ascendente e casas não estão disponíveis. Não os mencione — "
                f"trabalhe apenas com signos e aspectos planetários.]"
            )
        return _tunk_prefix + (
            f"Sol: {_pl(p['sun'])}\n"
            f"Lua: {_pl(p['moon'])}\n"
            f"Ascendente: {fmt_position(asc)}\n"
            f"Meio-do-Céu: {fmt_position(mc)}"
        )

    if section_name == "triade":
        if moon_uncertain:
            # Vira uma seção SOMENTE do Sol — a Lua está indeterminada e não
            # dá pra sintetizar dupla Sol×Lua sem escolher um signo.
            return _tunk_prefix + (
                f"Sol: {fmt_position(p['sun'])}\n\n"
                f"Aspectos relevantes do Sol (filtrados): {aspects_line}\n\n"
                f"[REGRA CRÍTICA: escreva SOMENTE sobre o Sol nesta seção — "
                f"sua vitalidade, direção de vida, identidade consciente, "
                f"o herói interno. NÃO mencione a Lua com um signo específico. "
                f"Se precisar tocar 'de onde você vem' emocionalmente, use "
                f"linguagem genérica sem ancorar em signo lunar. Termine com "
                f"uma orientação sobre como habitar essa direção solar.]"
            )
        if time_unknown:
            # Lua estável — dá para fazer Sol+Lua sem Ascendente.
            return _tunk_prefix + (
                f"Sol: {fmt_position(p['sun'])}\n"
                f"Lua: {fmt_position(p['moon'])}\n\n"
                f"Aspectos relevantes de Sol e Lua (filtrados, priorizados, sem duplicatas): {aspects_line}\n\n"
                f"[NOTA: Este mapa foi calculado sem horário de nascimento. Não interprete "
                f"como uma tríade — não há Ascendente. Sintetize a dupla Sol/Lua e como "
                f"os dois se articulam. NÃO mencione o Ascendente em nenhuma parte do texto.]"
            )
        return _tunk_prefix + (
            f"Sol: {_pl(p['sun'])}\n"
            f"Lua: {_pl(p['moon'])}\n"
            f"Ascendente: {fmt_position(asc)}\n"
            f"Meio-do-Céu: {fmt_position(mc)}\n\n"
            f"Aspectos relevantes da tríade (filtrados, priorizados, sem duplicatas): {aspects_line}"
        )

    if section_name == "mercurio":
        return _tunk_prefix + (
            f"Mercúrio: {_pl(p['mercury'])}\n"
            f"Aspectos relevantes de Mercúrio (filtrados): {aspects_line}\n\n"
            f"NOTA DE ESTILO PARA ESTA SEÇÃO: Avoid doubling the same verb in sequence — "
            f"'precisa conhecer a fundo, precisa poder sustentar' should be restructured. "
            f"Evite a construção 'como onde' — use sempre 'como o lugar onde' ou reescreva a frase."
        )

    if section_name == "lua":
        if moon_uncertain:
            # Mudou de signo no dia + hora desconhecida — o signo é indeterminado.
            # Descrever só por aspectos; a leitura dos dois signos possíveis
            # é acoplada depois pela lógica de Branch A em app.py.
            return _tunk_prefix + (
                f"[NOTA: A hora de nascimento é desconhecida E a Lua mudou de signo "
                f"neste dia (de {moon_meta.get('moon_sign_before')} para "
                f"{moon_meta.get('moon_sign_after')} às {moon_meta.get('moon_ingress_local_time')} "
                f"horário local). O signo da Lua é INDETERMINADO. NÃO mencione um signo "
                f"específico para a Lua. Trabalhe APENAS com os aspectos lunares — o que "
                f"eles revelam sobre a vida emocional, a figura materna, os padrões "
                f"herdados — independentemente de qual dos dois signos seja o dela.]\n\n"
                f"Aspectos relevantes da Lua (filtrados): {aspects_line}"
            )
        return _tunk_prefix + (
            f"Lua: {_pl(p['moon'])}\n"
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
        return _tunk_prefix + "\n".join(lines)

    if section_name == "sol_saturno":
        return _tunk_prefix + (
            f"Sol: {_pl(p['sun'])}\n"
            f"Saturno: {_pl(p['saturn'])}\n"
            f"Aspectos relevantes Sol/Saturno (filtrados): {aspects_line}"
        )

    if section_name == "venus_marte":
        return _tunk_prefix + (
            f"Vênus: {_pl(p['venus'])}\n"
            f"Marte: {_pl(p['mars'])}\n"
            f"Aspectos relevantes Vênus/Marte (filtrados): {aspects_line}"
        )

    if section_name == "jupiter":
        return _tunk_prefix + (
            f"Júpiter: {_pl(p['jupiter'])}\n"
            f"Aspectos relevantes de Júpiter (filtrados): {aspects_line}"
        )

    if section_name == "saturno":
        return _tunk_prefix + (
            f"Saturno: {_pl(p['saturn'])}\n"
            f"Aspectos relevantes de Saturno (filtrados): {aspects_line}"
        )

    if section_name == "quiron":
        return _tunk_prefix + (
            f"Quíron: {_pl(p['chiron'])}\n"
            f"Aspectos relevantes de Quíron (filtrados): {aspects_line}"
        )

    if section_name == "urano":
        return _tunk_prefix + (
            f"Urano: {_pl(p['uranus'])}\n"
            f"Aspectos relevantes de Urano (filtrados): {aspects_line}"
        )

    if section_name == "netuno":
        return _tunk_prefix + (
            f"Netuno: {_pl(p['neptune'])}\n"
            f"Aspectos relevantes de Netuno (filtrados): {aspects_line}"
        )

    if section_name == "plutao":
        return _tunk_prefix + (
            f"Plutão: {_pl(p['pluto'])}\n"
            f"Aspectos relevantes de Plutão (filtrados): {aspects_line}"
        )

    if section_name == "lilith":
        # Se a Lua tem signo indeterminado, aspectos Lua-Lilith são
        # intrinsecamente incertos e não devem chegar ao Claude nesta seção.
        # Filtramos aqui para que a única lista visível seja confiável.
        lilith_aspects = filtered_aspects
        if moon_uncertain:
            lilith_aspects = [
                a for a in filtered_aspects
                if "moon" not in (a.get("planet_a"), a.get("planet_b"))
            ]
        lilith_line = fmt_filtered_aspects(lilith_aspects)
        return _tunk_prefix + (
            f"Lilith: {_pl(p['lilith'])}\n"
            f"Aspectos relevantes de Lilith (filtrados): {lilith_line}"
            + (
                "\n[Aspectos com a Lua foram omitidos porque o signo da Lua "
                "é indeterminado neste mapa.]"
                if moon_uncertain and any(
                    "moon" in (a.get("planet_a"), a.get("planet_b"))
                    for a in filtered_aspects
                ) else ""
            )
        )

    if section_name == "nodos":
        # Descobrir dinamicamente que outros corpos compartilham signo com
        # cada Nodo, para o Claude ter DADOS reais em vez de inventar. Se
        # nenhum outro corpo compartilha, o Claude não pode inventar — a
        # linha abaixo dirá isso explicitamente.
        ns_sign = p["south_node"]["sign_pt"]
        nn_sign = p["north_node"]["sign_pt"]
        shared_with_ns = [
            PLANET_LABEL_PT.get(k, k)
            for k in ("sun","moon","mercury","venus","mars","jupiter","saturn",
                      "uranus","neptune","pluto","chiron","lilith","ceres",
                      "vesta","juno","pallas")
            if p.get(k, {}).get("sign_pt") == ns_sign
        ]
        shared_with_nn = [
            PLANET_LABEL_PT.get(k, k)
            for k in ("sun","moon","mercury","venus","mars","jupiter","saturn",
                      "uranus","neptune","pluto","chiron","lilith","ceres",
                      "vesta","juno","pallas")
            if p.get(k, {}).get("sign_pt") == nn_sign
        ]
        # Se a hora é desconhecida e a Lua tem signo indeterminado, ela NÃO
        # pode aparecer como "compartilhando signo" com nada — o signo dela
        # ao meio-dia default pode ser diferente do real.
        if moon_uncertain:
            moon_pt = PLANET_LABEL_PT.get("moon", "Lua")
            shared_with_ns = [x for x in shared_with_ns if x != moon_pt]
            shared_with_nn = [x for x in shared_with_nn if x != moon_pt]

        def _fmt_shared(shared, sign):
            if not shared:
                return f"NENHUM outro corpo do mapa está em {sign}. NÃO invente compartilhamentos."
            return f"Também está(ão) em {sign}: {', '.join(shared)}."

        return _tunk_prefix + (
            f"Nodo Sul: {_pl(p['south_node'])}\n"
            f"  ↳ {_fmt_shared(shared_with_ns, ns_sign)}\n"
            f"Nodo Norte: {_pl(p['north_node'])}\n"
            f"  ↳ {_fmt_shared(shared_with_nn, nn_sign)}\n"
            f"Aspectos relevantes dos Nodos (filtrados): {aspects_line}\n\n"
            f"[REGRA CRÍTICA ANTI-ALUCINAÇÃO PARA ESTA SEÇÃO] Só é permitido "
            f"afirmar que um corpo compartilha signo com o Nodo Sul ou Nodo "
            f"Norte se esse corpo estiver EXPLICITAMENTE listado acima como "
            f"'Também está(ão) em <signo>'. Se a linha diz 'NENHUM outro "
            f"corpo do mapa está em <signo>', NÃO invente compartilhamentos, "
            f"não escreva 'a Lua também está em X', 'Quíron reforça esse "
            f"padrão', 'Vênus vibra no mesmo signo', ou qualquer variação "
            f"que sugira coincidência de signos entre corpos que os dados "
            f"não confirmam. Trabalhe apenas com o Nodo Sul e o Nodo Norte "
            f"nos seus signos verdadeiros. Também: NUNCA escreva '[planeta] "
            f"em [signo]' para um planeta específico com signo específico a "
            f"não ser que esse planeta esteja de fato nesse signo no chart "
            f"(dados fornecidos no início desta seção e nos POSICIONAMENTOS "
            f"ASTROLÓGICOS)."
        )

    if section_name == "asteroides":
        return _tunk_prefix + (
            f"Ceres: {_pl(p['ceres'])}\n"
            f"Vesta: {_pl(p['vesta'])}\n"
            f"Juno: {_pl(p['juno'])}\n"
            f"Palas: {_pl(p['pallas'])}\n"
            f"Aspectos relevantes dos asteróides (filtrados, orbe < 2°): {aspects_line}"
        )

    return ""


# ============================================================
# SECTION DEFINITIONS — built dynamically from chart
# ============================================================
def build_sections(chart):
    p = chart["points"]
    asc = chart["ascendant"]

    # Time-unknown branches: skip and rewrite sections that assume Ascendente/casas.
    # The flag comes from app.py (endpoint stashes it on the chart dict before
    # calling generate_report). moon_meta carries the Branch A ingress info from
    # the same source.
    time_unknown = _time_is_unknown(chart)
    moon_meta = _moon_ingress_meta(chart)

    # Clusters de acentuação parental. Calculados uma vez e usados em três
    # locais downstream: (1) addendum ao psychological_frame de sol_saturno,
    # (2) addendum ao psychological_frame de lua, (3) menção breve no Fio
    # Condutor via _parental_dynamics_context. Também expostos no meta da
    # resposta para auditoria. Guardo no próprio chart para outros consumidores
    # acessarem sem recomputar.
    parental_clusters = compute_parental_clusters(chart)
    chart["_parental_clusters"] = parental_clusters
    moon_uncertain = time_unknown and bool(moon_meta.get("moon_sign_uncertain"))

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

    # Abertura + Tríade dependem se a hora é conhecida — cada uma tem duas variantes.
    if moon_uncertain:
        # Ingresso lunar + hora desconhecida: signo da Lua é indeterminado.
        # Abertura foca no Sol e evita tomar partido de qualquer signo lunar.
        # Triade vira uma seção SOMENTE do Sol (dupla Sol×Lua não é possível
        # sem saber o signo da Lua).
        _abertura_section = {
            "name": "abertura",
            "title": "Abertura",
            "queries": [
                f"Sol em {sun['sign_pt']} identidade vitalidade direção",
                f"quem é essa pessoa {sun['sign_pt']}",
            ],
            "planets_filter": ["Sol"],
            "psychological_frame": (
                "Escreva uma abertura calorosa focada na direção e qualidade do Sol. "
                "O primeiro parágrafo deve criar reconhecimento — como se alguém que te conhece "
                "profundamente estivesse dizendo 'eu te vejo'. Tom íntimo, acolhedor.\n\n"
                "REGRA CRÍTICA: este mapa foi calculado SEM horário de nascimento E a Lua mudou "
                "de signo no dia — o signo da Lua é INDETERMINADO. NÃO mencione um signo específico "
                "para a Lua em nenhum momento. NÃO declare 'tensão entre Sol e Lua' nem 'coerência "
                "entre Sol e Lua' — qualquer afirmação assim tomaria partido por um dos signos "
                "possíveis. Se precisar tocar a vida emocional, refira-a genericamente "
                "('sua sensibilidade interior', 'sua vida afetiva'). Também NÃO mencione o "
                "Ascendente. Evite 'Não é X. É Y'."
            ),
            "depth_instruction": DEPTH_TIER_3,
        }
        _triade_section = {
            "name": "triade",
            "title": "Sol: Sua Vitalidade e Direção",
            "queries": [
                f"Sol em {sun['sign_pt']} direção de vida vitalidade",
                f"Sol em {sun['sign_pt']} identidade consciente propósito",
                f"quem é essa pessoa Sol em {sun['sign_pt']}",
            ],
            "planets_filter": ["Sol"],
            "psychological_frame": (
                "Interprete o Sol como o herói interno — a vitalidade, a direção de vida, a identidade "
                "consciente, o eixo onde a pessoa se desenvolve.\n\n"
                "REGRA CRÍTICA: escreva SOMENTE sobre o Sol. NÃO mencione um signo específico para a "
                "Lua — o signo lunar é indeterminado neste mapa (a Lua mudou de signo no dia e a hora "
                "é desconhecida). NÃO chame esta seção de 'tríade' nem 'dupla Sol/Lua'. Se precisar "
                "referir-se ao que a Lua representa (vida emocional, sensibilidade, herança materna), "
                "use termos genéricos sem ancorar em signo lunar.\n\n"
                "Termine com uma ou duas frases de orientação sobre como habitar essa direção solar."
            ),
            "depth_instruction": DEPTH_TIER_3,
        }
    elif time_unknown:
        # Hora desconhecida mas a Lua ficou no mesmo signo o dia todo —
        # dá pra fazer Sol+Lua sem o Ascendente.
        _abertura_section = {
            "name": "abertura",
            "title": "Abertura",
            "queries": [
                f"Sol em {sun['sign_pt']} Lua em {moon['sign_pt']} síntese identidade",
                f"quem é essa pessoa {sun['sign_pt']} {moon['sign_pt']}",
            ],
            "planets_filter": ["Sol", "Lua"],
            "psychological_frame": (
                "Escreva uma abertura calorosa que funcione como uma porta de entrada, não como uma análise imediata. "
                "O primeiro parágrafo deve criar uma sensação de reconhecimento — como se alguém que te conhece "
                "profundamente estivesse dizendo 'eu te vejo'. Apenas no segundo parágrafo comece a nomear as tensões "
                "centrais do mapa. Termine com uma frase que convide o leitor a continuar. Tom: íntimo, acolhedor, presente.\n\n"
                "IMPORTANTE: este mapa foi calculado SEM horário de nascimento. NÃO mencione o Ascendente em nenhum "
                "momento — ele não pôde ser calculado. Trabalhe só com o Sol e a Lua e suas dinâmicas. Não use as "
                "expressões 'como você se apresenta', 'primeira impressão', 'máscara social' ou variações que "
                "descrevem o papel do Ascendente. Evite a construção 'Não é X. É Y'."
            ),
            "depth_instruction": DEPTH_TIER_3,
        }
        _triade_section = {
            "name": "triade",
            "title": "Sol e Lua: O Núcleo Emocional-Vital",
            "queries": [
                f"Sol em {sun['sign_pt']}",
                f"Lua em {moon['sign_pt']} vida emocional",
                f"Sol Lua síntese {sun['sign_pt']} {moon['sign_pt']}",
            ],
            "planets_filter": ["Sol", "Lua"],
            "psychological_frame": (
                "Interprete o Sol e a Lua como o núcleo do mapa — a dupla vital-emocional. O Sol é para onde você "
                "vai, a Lua é de onde você vem. Sintetize como esses dois funcionam juntos e onde criam tensão.\n\n"
                "IMPORTANTE: este mapa foi calculado SEM horário de nascimento. NÃO mencione o Ascendente em nenhum "
                "momento — ele não pôde ser calculado. NÃO chame esta seção de 'tríade'; ela é uma dupla Sol/Lua. "
                "NÃO use frases do tipo 'como você chega', 'como se apresenta', 'primeira impressão' — essas "
                "descrevem o Ascendente e não se aplicam aqui.\n\n"
                "Termine com uma ou duas frases de orientação sobre como trabalhar com a tensão central Sol/Lua."
            ),
            "depth_instruction": DEPTH_TIER_3,
        }
    else:
        _abertura_section = {
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
        }
        _triade_section = {
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
        }

    _sections_unordered = [
        _abertura_section,
        _triade_section,
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
        # Seção da Lua — três variantes dependendo do que a análise lunar detectou:
        # (i) hora conhecida OU (ii) hora desconhecida sem ingresso — usa signo + aspectos
        # (iii) hora desconhecida COM ingresso — usa APENAS aspectos, signo é indeterminado
        {
            "name": "lua",
            "title": "Lua: Suas Raízes Emocionais",
            "queries": (
                # Quando o signo da Lua é INDETERMINADO (ingress no dia), buscamos
                # material por AFECTOS, sem âncora em signo — os aspectos são o
                # que temos de sólido.
                [
                    f"Lua aspectos vida emocional figura materna padrões",
                    f"Lua aspectos {moon_aspects_text}",
                    f"figura materna padrões emocionais família aspectos lunares",
                ]
                if moon_uncertain else
                [
                    f"Lua em {moon['sign_pt']} mãe infância" + ("" if time_unknown else f" casa {moon['house']}"),
                    f"figura materna {moon['sign_pt']} padrões emocionais família",
                    f"Lua aspectos {moon_aspects_text}",
                ]
            ),
            "planets_filter": ["Lua"],
            "psychological_frame": (
                # Base COMUM às três branches: descrição da função lunar +
                # regra permanente anti-"sua mãe" + instrução de profundidade
                # plena. As branches (i)/(ii)/(iii) adicionam suas ressalvas.
                (
                    "A Lua fala da função de cuidado e do vínculo primário de segurança emocional — "
                    "a principal figura de cuidado da infância, o ambiente afetivo, as memórias e os "
                    "padrões que moldaram como você navega o mundo. Ela descreve suas necessidades "
                    "emocionais básicas, o modo como você se sente segura, o padrão de nutrição (o "
                    "que te alimenta emocionalmente e o que te esvazia), a sua resposta instintiva, o "
                    "que te acolhe e o que te desestabiliza.\n\n"

                    "REGRA PERMANENTE E ABSOLUTA — LINGUAGEM SOBRE FIGURAS PARENTAIS: NUNCA, em "
                    "nenhuma hipótese, escreva 'sua mãe', 'a sua mãe', 'sua mamãe', 'seu pai', 'seus "
                    "pais' ou 'seus progenitores' como afirmação sobre a experiência real desta "
                    "pessoa. Refira-se SEMPRE como 'a principal figura de cuidado', 'o vínculo "
                    "primário', 'o cuidador principal', 'a figura que exerceu essa função', 'quem "
                    "cuidou'. Essa regra é INDEPENDENTE de qualquer outra instrução: mesmo que outros "
                    "trechos deste prompt sugiram uma leitura sobre a experiência materna, use SEMPRE "
                    "a linguagem funcional. Você não conhece a biografia da pessoa e não pode "
                    "afirmá-la — o mapa mostra função e intensidade, não enredo.\n\n"

                    "Leia a Lua com profundidade PLENA — não simplifique. Explore o signo (quando "
                    "conhecido), a casa (quando conhecida), os aspectos reais deste mapa, a relação "
                    "desta pessoa com a segurança emocional, o padrão de nutrição interior e "
                    "exterior, o que a estabiliza e o que a desregula. Termine com uma orientação "
                    "concreta e específica ao mapa desta pessoa.\n\n"
                )
                +
                # Ressalvas específicas por branch:
                (
                    # (iii) hora desconhecida + Lua mudou de signo no dia
                    (
                        "RESSALVA — SIGNO INDETERMINADO: neste mapa a hora de nascimento é "
                        "desconhecida E a Lua mudou de signo neste dia. "
                        f"Ela pode ter estado em {moon_meta.get('moon_sign_before')} ou em "
                        f"{moon_meta.get('moon_sign_after')} — não sabemos com certeza. Portanto:\n"
                        "- NÃO afirme um signo específico para a Lua.\n"
                        "- NÃO use frases como 'sua Lua em X' ou 'a Lua em X traz'.\n"
                        "- Trabalhe APENAS com os ASPECTOS lunares — o que eles revelam sobre a vida "
                        "emocional, a função de cuidado, os padrões herdados — independentemente do "
                        "signo.\n"
                        "- NÃO mencione casas (a hora é desconhecida).\n"
                        "- Uma leitura condensada dos dois signos possíveis será apresentada em "
                        "seguida (não escreva essa parte — outro trecho do sistema fará isso)."
                    ) if moon_uncertain else
                    # (ii) hora desconhecida, Lua ficou no mesmo signo
                    (
                        "RESSALVA — HORA DESCONHECIDA: a hora de nascimento é desconhecida. "
                        "Interprete a Lua pelo SIGNO e pelos ASPECTOS — NÃO mencione a casa da Lua "
                        f"(não pôde ser calculada). Felizmente a Lua esteve em {moon['sign_pt']} "
                        "durante todo o dia do nascimento, então o signo é confiável."
                    ) if time_unknown else
                    # (i) hora conhecida — sem ressalva adicional
                    ""
                )
            ) + _cluster_addendum_for_section(parental_clusters, "lua"),
            "depth_instruction": DEPTH_TIER_1,
        },
        {
            "name": "casa_4",
            "title": "Casa 4: Suas Raízes e Sua Casa Interna",
            "queries": casa4_queries,
            "planets_filter": h4_planets_pt if h4_planets_pt else None,
            "psychological_frame": (
                "A Casa 4 é a fundação invisível — a casa interior, a família de origem em seu sentido "
                "mais arquetípico, o lugar de onde você veio e que ainda te habita. É a memória anterior "
                "à memória, o solo emocional do qual você emergiu psiquicamente. Esta seção complementa "
                "a Lua: enquanto a Lua fala da função de cuidado e do vínculo primário, a Casa 4 é o "
                "ambiente, a atmosfera, o terreno do qual esse vínculo brotou.\n\n"

                "REGRA CRÍTICA ANTI-ALUCINAÇÃO: os dados no início desta seção listam explicitamente "
                "qual é o signo do IC (cúspide da Casa 4) NESTE mapa, e quais planetas (se algum) "
                "estão de fato posicionados na Casa 4. Você SÓ pode interpretar o que está listado. "
                "NUNCA assuma um signo diferente do IC listado. NUNCA assuma planetas na Casa 4 que "
                "não estejam listados. Se a listagem diz 'Casa 4: sem planetas listados', trabalhe com "
                "o IC e o planeta regente do signo do IC — não invente Plutão, Saturno ou qualquer "
                "outro corpo na Casa 4.\n\n"

                "Se HOUVER planetas na Casa 4 (listados nos dados), interprete-os como forças que "
                "moldaram o ambiente da infância e o senso de lar e pertencimento. Se a casa estiver "
                "VAZIA, interprete o signo da cúspide (IC) e o planeta que rege esse signo como a "
                "chave do ambiente doméstico e familiar. Em qualquer caso, NÃO repita interpretações "
                "que já apareceram na seção de Plutão ou de qualquer outro planeta — aqui o foco é o "
                "ambiente, a atmosfera, o terreno emocional da infância, não a força planetária em "
                "si.\n\n"

                "Se algum planeta REALMENTE presente na Casa 4 tem múltiplas manifestações possíveis "
                "(Plutão pode indicar harmonia aparente sobre dinâmicas de poder OU conflito "
                "explícito; Saturno pode indicar estrutura rígida OU frieza afetiva; Urano pode "
                "indicar instabilidade OU liberdade genuína; Netuno pode indicar difusão OU "
                "espiritualidade familiar), apresente as manifestações possíveis sem impor uma única "
                "leitura. Use linguagem como 'pode ter sido' ou 'alternativamente'.\n\n"

                "LINGUAGEM: use 'a principal figura de cuidado', 'quem exerceu esse papel', 'o "
                "vínculo primário', 'quem cuidou' — NUNCA 'seu pai', 'sua mãe', 'seus pais'. Regra "
                "permanente que vale para esta seção."
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
                "Sol e Saturno juntos falam da função de autoridade e estrutura na sua história — "
                "quem exerceu esse papel estruturante, o modelo de referência que você internalizou, "
                "e as ferramentas que você recebeu para enfrentar os desafios da vida. O Sol é quem "
                "você está se tornando: sua vitalidade, sua direção, seu propósito, o eixo em torno "
                "do qual você organiza sua identidade consciente. Saturno é onde você aprende através "
                "do tempo, do esforço e da repetição — é a autoridade internalizada, o senso de "
                "merecimento (ou de falta dele), a relação com limite, disciplina e responsabilidade, "
                "o medo estrutural, e onde a estrutura psíquica se forma pela contenção. Leia estes "
                "dois com profundidade PLENA: signo, casa (quando disponível), aspectos reais deste "
                "mapa, e a interação entre eles.\n\n"

                "REGRA PERMANENTE E ABSOLUTA — LINGUAGEM SOBRE FIGURAS PARENTAIS: NUNCA, em nenhuma "
                "hipótese, escreva 'seu pai', 'sua mãe', 'seu papai', 'sua mamãe', 'seus pais' ou "
                "'seus progenitores' como afirmação sobre a experiência real desta pessoa. Refira-se "
                "SEMPRE como 'a função de autoridade', 'quem exerceu esse papel estruturante', 'a "
                "figura de autoridade internalizada', 'o modelo de referência', 'o papel de autoridade "
                "na sua história'. Essa regra é INDEPENDENTE de qualquer outra instrução: mesmo que "
                "outros trechos deste prompt sugiram uma leitura sobre a experiência parental, use "
                "SEMPRE a linguagem funcional. Isso vale mesmo quando o mapa 'pede' claramente uma "
                "leitura sobre a origem — o mapa mostra função e intensidade, você não conhece a "
                "história biográfica da pessoa e não pode afirmá-la.\n\n"

                "Esta seção deve terminar com uma orientação concreta e específica ao mapa desta "
                "pessoa — não uma frase genérica, mas algo que ela possa carregar sobre como trabalhar "
                "com a dinâmica identificada aqui (a tensão específica entre Sol e Saturno neste "
                "mapa, seus signos, seus aspectos reais)."
            ) + _cluster_addendum_for_section(parental_clusters, "sol_saturno"),
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
                "age, deseja e luta pelo que quer. Juntos descrevem a dinâmica afetiva e relacional desta pessoa."
                + (
                    # Só emite a instrução do sextil Vênus-Júpiter se ele
                    # EXISTIR na lista de aspectos in-sign deste mapa. A
                    # referência hardcoded a "Saturno na casa 2" (que era
                    # específica ao Cliente Teste) foi removida — a diretriz
                    # de não interpretar esse sextil como abundância
                    # financeira é regra geral da tradição da Marcia, não
                    # depende de outro planeta.
                    "\n\nO sextil Vênus-Júpiter (orbe "
                    f"{find_aspect(chart, 'venus', 'jupiter', 'sextile')['orb']:.1f}°) presente neste mapa "
                    "deve ser interpretado NÃO como abundância financeira, mas como uma facilidade genuína "
                    "em criar vínculos e em ser reconhecida pelo que se oferece relacionalmente. Não conecte "
                    "esse aspecto a dinheiro nem a sorte material."
                    if find_aspect(chart, "venus", "jupiter", "sextile")
                    else ""
                )
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
            "queries": (
                # Sem hora: só signo. Com hora: signo + casa como âncora da busca.
                [
                    f"Lilith em {lilith['sign_pt']}",
                    f"silenciada empurrada para fora insistir caminho próprio {lilith['sign_pt']}",
                ]
                if time_unknown else
                [
                    f"Lilith em {lilith['sign_pt']} casa {lilith['house']}",
                    f"silenciada empurrada para fora insistir caminho próprio {lilith['sign_pt']}",
                ]
            ),
            "planets_filter": ["Lilith"],
            "psychological_frame": (
                "Lilith marca o lugar onde você percebe que algo em você é considerado 'errado' pelo "
                "mundo — onde o mundo tenta te silenciar, te normalizar, te empurrar para fora. É "
                "exatamente aqui que você deve insistir em fazer do seu jeito.\n\n"

                "REGRA CRÍTICA ANTI-ALUCINAÇÃO: os dados desta seção listam o signo real de Lilith "
                "NESTE mapa"
                + (" (a casa NÃO está disponível — hora desconhecida)"
                   if time_unknown else " e sua casa real")
                + ", e a lista de aspectos in-sign que Lilith de fato faz. Você SÓ pode trabalhar com "
                "esses dados. NUNCA assuma Lilith em Touro nem em casa 11 (configurações de mapas "
                "específicos, não deste). Leia o signo real, "
                + ("os aspectos reais (sem casa)"
                   if time_unknown else "a casa real e os aspectos reais")
                + " deste mapa.\n\n"

                + (
                    "RESSALVA — HORA DESCONHECIDA: a hora não é conhecida, portanto NÃO afirme casa "
                    "para Lilith. Trabalhe por signo + aspectos.\n\n"
                    if time_unknown else ""
                )
                + (
                    "RESSALVA ADICIONAL — LUA INCERTA: neste mapa a Lua tem signo indeterminado (ela "
                    "mudou de signo no dia do nascimento). Se algum dos aspectos listados envolve "
                    "Lua-Lilith, IGNORE esse aspecto específico — ele é intrinsecamente incerto pela "
                    "mesma razão que o signo da Lua é. Trabalhe apenas com aspectos de Lilith com "
                    "outros corpos (não a Lua).\n\n"
                    if moon_uncertain else ""
                )
                + "Termine a seção com uma frase conclusiva sobre o que significa, em termos "
                "práticos e concretos, ter Lilith no SIGNO real deste mapa"
                + (" (sem afirmar casa)" if time_unknown else " e na CASA real deste mapa")
                + " — o que essa pessoa deve parar de negociar e o que deve insistir em preservar. A "
                "conclusão deve derivar dos dados REAIS listados no início desta seção, nunca de uma "
                "configuração assumida."
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
                "O Nodo Sul é sua zona de conforto — o que você faz em excesso, o que lhe vem naturalmente mas "
                "onde você tende a se refugiar. O Nodo Norte é o desafio evolutivo — o que você veio aprender, "
                "onde você precisa crescer mesmo que seja desconfortável.\n\n"
                "REGRA ANTI-ALUCINAÇÃO: os dados no início desta seção listam explicitamente que outros corpos "
                "(se houver) estão no mesmo signo do Nodo Sul e do Nodo Norte. Você SÓ pode mencionar "
                "compartilhamento de signo entre Nodo e outro corpo se esse corpo estiver EXPLICITAMENTE listado. "
                "Se a linha 'Também está(ão) em X: ...' diz 'NENHUM outro corpo do mapa está em X', você NÃO PODE "
                "escrever frases como 'a Lua também está em Áries', 'Quíron compartilha esse signo', 'Vênus "
                "reforça esse padrão', ou qualquer variação sugerindo coincidência de signos que os dados não "
                "confirmam. Isso é uma regra absoluta — a alucinação de posicionamento planetário é o erro mais "
                "grave possível num relatório astrológico e não é aceitável em nenhuma forma."
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
                "parcerias. Palas fala de sua sabedoria estratégica e criativa."
                + (
                    # Só emite a instrução da conjunção Ceres-Plutão se ela
                    # EXISTIR na lista de aspectos in-sign deste mapa. A
                    # versão anterior (hardcoded) instruía o modelo a
                    # interpretar essa conjunção como fato em TODOS os mapas
                    # — enquanto ela existe só no Cliente Teste. Se não
                    # existe, Ceres é lida normalmente pelos aspectos e
                    # posicionamento reais dela.
                    "\n\nCeres está em conjunção com Plutão neste mapa (orbe "
                    f"{find_aspect(chart, 'ceres', 'pluto', 'conjunction')['orb']:.1f}°). Essa "
                    "conjunção deve ser interpretada: o cuidado e a nutrição (Ceres) estão profundamente "
                    "entrelaçados com dinâmicas de poder, transformação e o não-dito (Plutão). O cuidado "
                    "que você recebeu — e que oferece — raramente é simples ou descomplicado."
                    if find_aspect(chart, "ceres", "pluto", "conjunction")
                    else ""
                )
                + (
                    "\n\nPara Vesta, Juno e Palas, inclua uma frase adicional além da descrição básica — algo que "
                    "conecte o posicionamento a um desafio ou recurso concreto para esta pessoa específica, baseado no "
                    "signo e casa."
                )
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

    ordered = [by_name[n] for n in SECTION_ORDER]

    # Se a hora é desconhecida, PULAR seções que dependem inteiramente da hora:
    # - casa_4: a lista de planetas na casa 4 e o IC não podem ser determinados
    #   sem hora — qualquer texto que essa seção produzisse estaria trabalhando
    #   com um cálculo default (meio-dia) que não tem correspondência com o
    #   mapa real da pessoa. Melhor omitir do que gerar conteúdo enganoso.
    if time_unknown:
        ordered = [s for s in ordered if s["name"] not in ("casa_4",)]

    return ordered


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

TAMBÉM PROIBIDO: (a) a palavra "funda" — use sempre "profunda"; (b) a expressão "que sustenta" sem especificar o que sustenta; (c) a palavra "presença" como substantivo vago — use apenas quando indispensável e com referente claro; (d) a construção antitética "Não é X. É Y." ou "não é X, é Y" — **ZERO ocorrências permitidas por relatório** (essa cadência é uma marca clássica de escrita gerada por IA; use uma afirmação direta ou reformule totalmente); (e) "emoção que age antes de pensar" — prefira "emoções que emergem impulsivamente".

Alternativas concretas para o padrão "não é X, é Y":
  · Em vez de "não é frescura, é o que sustenta" → "é o que genuinamente te sustenta" ou "isso te sustenta de verdade"
  · Em vez de "não é frieza, é filtro" → "funciona como um filtro" ou "opera como um filtro racional"
  · Em vez de "não é ingenuidade, é inteligência" → "é uma inteligência específica" ou "essa forma de olhar é uma inteligência real"
  · Em vez de "não é fraqueza, é sensibilidade" → "é sensibilidade" ou "essa sensibilidade é um recurso, não uma vulnerabilidade"
Sempre dá para dizer o que se quer afirmar SEM negar antes o oposto. Reescreva.

TAMBÉM PROIBIDO: (f) usar "nomear" como verbo padrão para tudo — varie com "identificar", "reconhecer", "colocar em palavras", "articular", "perceber", "distinguir"; (g) [reforço da alínea d] a construção "Não é X, é Y" em QUALQUER forma — inclusive versões alongadas ("Isso não é apenas X, é também Y"), invertidas ("Y, e não X"), ou com negação em outra ordem ("Aqui não há X, há Y") — TODAS são proibidas; (h) "retrógrada" como substantivo feminino — o planeta está sempre "retrógrado", nunca "a retrógrada"; (i) qualquer palavra em inglês não traduzida, especialmente "retrograde" — sempre "retrógrado"; (j) qualificadores defensivos desnecessários como "não porque seja naturalmente ambiciosa no sentido frio da palavra" — faça a afirmação diretamente sem recuar dela; (k) repetir o mesmo padrão interpretativo em seções diferentes — se a proteção emocional via controle já foi descrita na seção da Lua, a seção de Plutão não deve repetir a mesma ideia com outras palavras; (l) [REGRA ABSOLUTA — ALUCINAÇÃO DE POSICIONAMENTO] JAMAIS afirmar que um planeta ou ponto está em um signo se esse dado não vier do bloco "DADOS DO MAPA PARA ESTA SEÇÃO" fornecido no início desta chamada, ou dos POSICIONAMENTOS ASTROLÓGICOS listados. NUNCA escrever "Sol em X", "Lua em Y", "Vênus em Z", "Quíron em W", etc. com um signo que não conste explicitamente nos dados. NUNCA escrever que um planeta "compartilha signo com" outro, "reforça o padrão de", "vibra no mesmo tom que", "faz eco a" outro planeta a não ser que os SIGNOS DE AMBOS estejam explicitamente informados no bloco de dados e coincidam. A alucinação de posicionamento é o erro mais grave possível num relatório astrológico. Em caso de dúvida — omita.

(m) [REGÊNCIA ≠ POSIÇÃO REAL] A regência, o domicílio, a exaltação, a queda ou o exílio de um planeta NUNCA devem ser confundidos com o signo em que esse planeta está POSICIONADO neste mapa. Plutão rege Escorpião — isso NÃO significa que Plutão está em Escorpião. Marte é domiciliado em Áries e exaltado em Capricórnio — isso NÃO significa que Marte está em Áries ou em Capricórnio. Vênus rege Touro e Libra — isso NÃO significa que Vênus está em Touro ou Libra. Quíron não tem regência canônica — nunca associe Quíron a Áries "por default". O signo real de CADA planeta e ponto vem SEMPRE do bloco "DADOS DO MAPA PARA ESTA SEÇÃO" acima e da lista POSICIONAMENTOS ASTROLÓGICOS. Se você se pegar escrevendo o signo de regência de um planeta em vez do signo real dele, PARE, releia os dados, e corrija. Este é o vetor mais comum de alucinação: substituir a posição real por associação simbólica genérica. NUNCA fazer isso.

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

DINÂMICA PARENTAL / DE APEGO — dados reais deste mapa (use APENAS o que está listado aqui):
{parental_dynamics_context}

RELATÓRIO COMPLETO GERADO ATÉ AGORA:
{full_report_so_far}

Agora escreva o FIO CONDUTOR — a seção final de síntese.

Esta seção deve:
1. Nomear as principais contradições e tensões que apareceram ao longo do relatório
2. Mostrar como essas tensões não são falhas — são o motor desta pessoa
3. Reunir as orientações práticas que foram oferecidas em cada seção e mostrar como elas convergem num único movimento — não repetindo o que foi dito, mas sintetizando
4. Mostrar como as tensões individuais são expressões de uma mesma dinâmica central
5. Amarrar explicitamente a dinâmica parental / de apego à síntese. Isso é obrigatório porque o enquadramento de apego atravessa todo o relatório. A figura paterna é lida pelo Sol (identidade, direção vital) e por Saturno (autoridade, estrutura, limite). A figura materna é lida pela Lua (nutrição, segurança emocional, receptividade). Use a seção "DINÂMICA PARENTAL / DE APEGO" acima para decidir qual figura recebe mais peso NESTE mapa específico: pode ser predominantemente paterna, predominantemente materna, ambas com pesos comparáveis, ou marcada pela ausência de uma delas. Deixe os dados guiarem — se Sol e Saturno estão intensamente aspectados (especialmente por aspectos difíceis), a dimensão paterna tende a dominar; se a Lua carrega os aspectos mais tensos, é a dimensão materna; se ambos, ambas. REGRA CRÍTICA: você só pode afirmar posicionamento (signo, casa, retrogradação, aspecto) que esteja EXPLICITAMENTE listado na "DINÂMICA PARENTAL / DE APEGO" acima. NUNCA invente configurações. Se a hora é desconhecida (verá isso indicado na seção parental), não afirme casas. Se o signo da Lua está marcado como indeterminado, não afirme signo para a Lua — trabalhe só com aspectos dela.
6. Terminar com algo concreto e singular que a pessoa possa carregar — não uma previsão, não uma lista, mas uma orientação central que emerge naturalmente de tudo que foi revelado neste mapa

Tom: mais elevado e conclusivo que as outras seções, mas sem ornamentação poética forçada. Profundidade sem dramatismo. A mesma voz direta e íntima do restante do relatório.

Mantenha o gênero gramaticalmente consistente em todo o texto — use exclusivamente o gênero {gender} ao se referir ao cliente.

PROIBIDO também aqui: metáforas dramáticas, "corta o ar como lâmina", "abismo", "chama que arde", "funda" (use "profunda"), construção repetitiva "Não é X. É Y.", "presença" como substantivo vago.

TAMBÉM PROIBIDO: (f) usar "nomear" como verbo padrão para tudo — varie com "identificar", "reconhecer", "colocar em palavras", "articular", "perceber", "distinguir"; (g) a construção "Não é X, é Y" — está limitada a UMA ocorrência por relatório inteiro, não por seção; (h) "retrógrada" como substantivo feminino — o planeta está sempre "retrógrado", nunca "a retrógrada"; (i) qualquer palavra em inglês não traduzida, especialmente "retrograde" — sempre "retrógrado"; (j) qualificadores defensivos desnecessários como "não porque seja naturalmente ambiciosa no sentido frio da palavra" — faça a afirmação diretamente sem recuar dela; (k) repetir o mesmo padrão interpretativo em seções diferentes; (m) [REGÊNCIA ≠ POSIÇÃO REAL] NUNCA confundir regência/domicílio/exaltação com signo real de posição. Plutão rege Escorpião mas pode estar em qualquer signo; use SEMPRE o signo real listado no RESUMO DO MAPA acima, jamais o signo de regência.

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


def _parental_dynamics_context(chart):
    """Enumera os dados REAIS relativos à dinâmica parental / de apego para
    o Fio Condutor. Substitui a antiga instrução hardcoded que assumia a
    configuração paterna do Cliente Teste (Sol Aquário casa 8, Saturno Leão
    retrógrado casa 2, quadratura Sol-Urano).

    Dois eixos:
      - PATERNO: Sol + Saturno (identidade/autoridade)
      - MATERNO: Lua (nutrição/segurança emocional)

    Para cada corpo lista signo, casa (só se hora conhecida), retrogradação
    e aspectos com outros corpos (só os in-sign que sobreviveram ao filtro).
    Também aplica a regra do ramo sem-hora: se moon_uncertain, o signo da
    Lua vira 'INDETERMINADO' — Claude deve trabalhar só com aspectos.
    """
    p = chart.get("points") or {}
    time_unknown = _time_is_unknown(chart)
    mm = _moon_ingress_meta(chart)
    moon_uncertain = time_unknown and bool(mm.get("moon_sign_uncertain"))

    def _fmt_body(key, label):
        b = p.get(key) or {}
        sign = b.get("sign_pt", "?")
        retr = " retrógrado" if b.get("retrograde") else ""
        house = b.get("house")
        house_part = f" na casa {house}" if house and not time_unknown else ""
        # Suprime signo/casa da Lua se incerta
        if key == "moon" and moon_uncertain:
            return f"  {label}: signo INDETERMINADO (ingresso lunar no dia; use só aspectos abaixo)"
        return f"  {label}: {sign}{retr}{house_part}"

    # Aspectos envolvendo cada corpo (só os in-sign que passaram no filtro)
    def _aspects_of(key):
        pt_label = PLANET_LABEL_PT.get(key, key)
        out = []
        for a in chart.get("aspects") or []:
            if a.get("planet_a") != key and a.get("planet_b") != key:
                continue
            other_key = a["planet_b"] if a["planet_a"] == key else a["planet_a"]
            other_label = PLANET_LABEL_PT.get(other_key, other_key)
            other_sign = p.get(other_key, {}).get("sign_pt", "?")
            asp_type = a.get("type_pt", a.get("type", "?"))
            orb = a.get("orb", 0)
            # Se a Lua é incerta e o outro corpo é a Lua, indica só o aspecto
            if moon_uncertain and other_key == "moon":
                out.append(f"    · {asp_type} com Lua (signo indeterminado), orbe {orb:.1f}°")
            else:
                out.append(f"    · {asp_type} com {other_label} (em {other_sign}), orbe {orb:.1f}°")
        if not out:
            return "    · nenhum aspecto in-sign com outros corpos"
        return "\n".join(out)

    parts = []
    parts.append("EIXO PATERNO — figuras/estruturas de autoridade:")
    parts.append(_fmt_body("sun", "Sol"))
    parts.append("  Aspectos in-sign do Sol:")
    parts.append(_aspects_of("sun"))
    parts.append(_fmt_body("saturn", "Saturno"))
    parts.append("  Aspectos in-sign de Saturno:")
    parts.append(_aspects_of("saturn"))
    parts.append("")
    parts.append("EIXO MATERNO — nutrição / segurança emocional:")
    parts.append(_fmt_body("moon", "Lua"))
    parts.append("  Aspectos in-sign da Lua:")
    parts.append(_aspects_of("moon"))

    if time_unknown:
        parts.append("")
        parts.append("[HORA DESCONHECIDA] Este mapa foi calculado sem horário de "
                     "nascimento. NÃO afirme casas (Ascendente e Casas não puderam "
                     "ser calculados).")
    if moon_uncertain:
        parts.append("[LUA INCERTA] A Lua mudou de signo no dia do nascimento. "
                     "NÃO afirme um signo para a Lua em qualquer parte do texto — "
                     "trabalhe só pelos aspectos dela.")

    # Clusters de acentuação parental — apenas menção breve no Fio Condutor.
    # O desenvolvimento profundo já foi feito na seção de Sol/Saturno (cluster 1)
    # e/ou na seção de Lua (cluster 2). Aqui referencia sem repetir. Se ambos
    # dispararam, este é o único lugar onde a leitura 'ambos' aparece.
    clusters = chart.get("_parental_clusters") or {}
    c1 = clusters.get("cluster_1_authority") or {}
    c2 = clusters.get("cluster_2_care") or {}
    h4 = clusters.get("house_4_context") or []
    if c1.get("triggered") or c2.get("triggered"):
        parts.append("")
        parts.append("CLUSTERS DE ACENTUAÇÃO PARENTAL — instruções para o Fio Condutor:")
        if c1.get("triggered") and c2.get("triggered"):
            parts.append(
                f"  · Ambos os clusters dispararam. Cluster 1 (autoridade/estrutura, "
                f"{c1['count']} fatores) já foi desenvolvido na seção Sol/Saturno; "
                f"Cluster 2 (cuidado/vínculo, {c2['count']} fatores) já foi "
                f"desenvolvido na seção Lua. NÃO repita esses desenvolvimentos. "
                f"Este é o ÚNICO lugar onde a leitura combinada dos dois deve "
                f"aparecer. Use como PONTO DE PARTIDA (na sua voz, não literal):"
            )
            parts.append(f"    \"{_CLUSTERS_BOTH_ANCHOR_FIO}\"")
        elif c1.get("triggered"):
            parts.append(
                f"  · Cluster 1 (autoridade/estrutura, {c1['count']} fatores) "
                f"disparou e foi desenvolvido na seção Sol/Saturno. NO Fio Condutor, "
                f"apenas referencie brevemente essa acentuação como parte da síntese, "
                f"SEM repetir o desenvolvimento nem a frase-âncora."
            )
        elif c2.get("triggered"):
            parts.append(
                f"  · Cluster 2 (cuidado/vínculo, {c2['count']} fatores) disparou e "
                f"foi desenvolvido na seção Lua. No Fio Condutor, apenas referencie "
                f"brevemente essa acentuação como parte da síntese, SEM repetir o "
                f"desenvolvimento nem a frase-âncora."
            )
        if h4:
            parts.append(
                f"  · Reforço contextual (ambiente doméstico): {', '.join(h4)}. "
                f"Se coerente com a síntese, mencione brevemente que o ambiente "
                f"doméstico refletiu essa dinâmica; NÃO trate como cluster separado."
            )
        parts.append(
            "  · REGRAS GERAIS: nunca escreva 'seu pai/sua mãe'; sempre "
            "'quem exerceu essa função'. Sempre modo de possibilidade; o mapa "
            "mostra a intensidade, não o enredo."
        )

    return "\n".join(parts)


def generate_fio_condutor(name, chart, full_report, gender):
    init_clients()
    summary = build_full_chart_summary(chart)
    parental_ctx = _parental_dynamics_context(chart)
    prompt = FIO_CONDUTOR_PROMPT_TMPL.format(
        name=name,
        full_chart_summary=summary,
        parental_dynamics_context=parental_ctx,
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


_SIGN_LABELS_PT = {
    "Áries", "Aries", "Touro", "Gêmeos", "Gemeos", "Câncer", "Cancer",
    "Leão", "Leao", "Virgem", "Libra", "Escorpião", "Escorpiao",
    "Sagitário", "Sagitario", "Capricórnio", "Capricornio",
    "Aquário", "Aquario", "Peixes",
}

_SIGN_CANONICAL_PT = {
    "aries": "Áries", "touro": "Touro", "gêmeos": "Gêmeos", "gemeos": "Gêmeos",
    "câncer": "Câncer", "cancer": "Câncer", "leão": "Leão", "leao": "Leão",
    "virgem": "Virgem", "libra": "Libra", "escorpião": "Escorpião",
    "escorpiao": "Escorpião", "sagitário": "Sagitário", "sagitario": "Sagitário",
    "capricórnio": "Capricórnio", "capricornio": "Capricórnio",
    "aquário": "Aquário", "aquario": "Aquário", "peixes": "Peixes",
}

_PLANET_PT_TO_KEY = {
    "Sol": "sun", "Lua": "moon", "Mercúrio": "mercury", "Mercurio": "mercury",
    "Vênus": "venus", "Venus": "venus", "Marte": "mars", "Júpiter": "jupiter",
    "Jupiter": "jupiter", "Saturno": "saturn", "Urano": "uranus",
    "Netuno": "neptune", "Plutão": "pluto", "Plutao": "pluto",
    "Quíron": "chiron", "Quiron": "chiron", "Lilith": "lilith",
    "Nodo Norte": "north_node", "Nodo Sul": "south_node",
    "Ceres": "ceres", "Vesta": "vesta", "Juno": "juno",
    "Palas": "pallas", "Pallas": "pallas",
}


def verify_planet_signs(text, chart, moon_uncertain=False):
    """Varre o texto do relatório procurando toda afirmação da forma
    '[planeta] em [signo]' e a confronta com o `chart["points"]`. Retorna
    uma lista de divergências (cada uma com o snippet, o planeta, o signo
    afirmado e o signo real) mais o texto CORRIGIDO — cada afirmação
    incorreta tem o signo substituído pelo signo real.

    Casos especiais:
    - Se `moon_uncertain=True`, qualquer afirmação de signo para a Lua é
      considerada divergência (o signo é indeterminado) — a substituição
      remove o "em X" e deixa só o nome do planeta.
    - Constatações genéricas do tipo "quem tem Sol em Câncer" ou "Sol em
      Câncer é..." em contexto arquetípico (não referente ao mapa da
      pessoa) NÃO são flagadas — só quando o texto se refere ao mapa da
      pessoa via "seu/sua/o seu/a sua" ou "sua" antes do planeta, ou
      quando não há artigo pessoal mas o contexto é claramente do mapa
      (nas frases "Seu Sol em X" / "A sua Lua em Y").

    Retorna (corrected_text, list_of_divergences).
    """
    if not text or not chart:
        return text, []

    points = chart.get("points") or {}
    divergences = []

    planet_alt = "|".join(sorted(_PLANET_PT_TO_KEY.keys(), key=len, reverse=True))
    sign_alt = "|".join(sorted(_SIGN_LABELS_PT, key=len, reverse=True))

    # PADRÃO A — "Planeta em Signo" e "Planeta está/estão em Signo"
    #   Exemplos: "Sol em Câncer", "Sua Lua em Áries", "Mercúrio está em Gêmeos"
    pattern_forward = re.compile(
        rf"(?P<prefix>(?:[Ss]eu|[Ss]ua|[Oo] seu|[Aa] sua|[Oo]|[Aa])\s+)?"
        rf"(?P<planet>{planet_alt})"
        rf"(?:\s+est(?:á|ão))?\s+em\s+"
        rf"(?P<sign>{sign_alt})"
        rf"(?!\w)"
    )

    # PADRÃO B — "Signo no/na Planeta" (invertido, contração em+o/em+a)
    #   Exemplos: "Libra no Nodo Sul", "Áries na Lua", "Câncer no Sol"
    pattern_reverse = re.compile(
        rf"(?P<sign>{sign_alt})\s+n[oa]s?\s+"
        rf"(?P<planet>{planet_alt})"
        rf"(?!\w)"
    )

    # PADRÃO C — "Planeta1, Planeta2 e Planeta3 (também) (estão) em Signo"
    #   Exemplos: "Marte, Ceres e Vesta em Libra"
    #             "A Lua e Quíron também estão em Áries"
    pattern_multi = re.compile(
        rf"(?P<group>(?:{planet_alt})(?:\s*,\s*(?:{planet_alt}))+"
        rf"(?:\s+e\s+(?:{planet_alt}))?"
        rf"|(?:{planet_alt})\s+e\s+(?:{planet_alt}))"
        rf"(?:\s+(?:tamb[ée]m))?"
        rf"(?:\s+est(?:á|ão))?\s+em\s+"
        rf"(?P<sign>{sign_alt})"
        rf"(?!\w)",
    )

    def _canon_planet_key(name):
        for pt_name, key in _PLANET_PT_TO_KEY.items():
            if name.lower() == pt_name.lower():
                return key, pt_name
        return None, name

    def _canon_sign(s):
        return _SIGN_CANONICAL_PT.get(s.lower(), s)

    corrected = text

    # Helper: registrar divergência e aplicar substituição
    def _handle_claim(match_start, match_end, planet_written, claimed_sign, prefix=""):
        key, planet_pt_canon = _canon_planet_key(planet_written)
        if not key:
            return None
        actual_sign = points.get(key, {}).get("sign_pt")
        if not actual_sign:
            return None
        claimed_norm = _canon_sign(claimed_sign)

        # Moon uncertain: qualquer signo afirmado à Lua é divergência
        if moon_uncertain and key == "moon":
            snippet = text[max(0, match_start-60):min(len(text), match_end+60)].replace("\n", " ").strip()
            divergences.append({
                "planet": planet_pt_canon,
                "claimed_sign": claimed_norm,
                "actual_sign": "INDETERMINADO (moon_uncertain)",
                "context": snippet,
                "match": text[match_start:match_end],
                "action": "stripped_sign",
            })
            # Substituição: só o planeta, sem afirmação de signo
            return f"{prefix}{planet_written}"

        if claimed_norm == actual_sign:
            return None  # Confere

        snippet = text[max(0, match_start-60):min(len(text), match_end+60)].replace("\n", " ").strip()
        divergences.append({
            "planet": planet_pt_canon,
            "claimed_sign": claimed_norm,
            "actual_sign": actual_sign,
            "context": snippet,
            "match": text[match_start:match_end],
            "action": "sign_replaced",
        })
        return f"{prefix}{planet_written} em {actual_sign}"

    # Ordem de aplicação: primeiro multi (mais específico), depois reverse,
    # depois forward. Todas trabalham de trás para frente sobre o texto
    # sempre re-lido para evitar cascatas.
    for pattern in (pattern_multi, pattern_reverse, pattern_forward):
        matches = list(pattern.finditer(corrected))
        for m in reversed(matches):
            claimed_sign = m.group("sign")
            if pattern is pattern_multi:
                # Múltiplos planetas na mesma afirmação — verificar CADA UM
                group_text = m.group("group")
                planets_raw = re.split(r"\s*,\s*|\s+e\s+", group_text)
                any_wrong = False
                fixed_planets = []
                for pw in planets_raw:
                    key, canon = _canon_planet_key(pw)
                    if not key:
                        fixed_planets.append(pw)
                        continue
                    actual = points.get(key, {}).get("sign_pt")
                    claimed_norm = _canon_sign(claimed_sign)
                    if moon_uncertain and key == "moon":
                        # Registrar e MARCAR pra remoção do grupo
                        divergences.append({
                            "planet": canon,
                            "claimed_sign": claimed_norm,
                            "actual_sign": "INDETERMINADO (moon_uncertain)",
                            "context": corrected[max(0, m.start()-60):min(len(corrected), m.end()+60)].replace("\n", " ").strip(),
                            "match": m.group(0),
                            "action": "removed_from_multi",
                        })
                        any_wrong = True
                        continue  # remove esse planeta do grupo
                    if actual and actual != claimed_norm:
                        divergences.append({
                            "planet": canon,
                            "claimed_sign": claimed_norm,
                            "actual_sign": actual,
                            "context": corrected[max(0, m.start()-60):min(len(corrected), m.end()+60)].replace("\n", " ").strip(),
                            "match": m.group(0),
                            "action": "removed_from_multi",
                        })
                        any_wrong = True
                        continue  # remove esse planeta do grupo
                    fixed_planets.append(pw)
                if any_wrong:
                    # Reconstruir a afirmação só com os planetas que de fato
                    # estão nesse signo. Se sobrar 0 ou 1, ajusta redação.
                    if not fixed_planets:
                        # Ninguém está nesse signo — remove a claim inteira
                        replacement = "[afirmação removida: nenhum corpo neste signo]"
                    elif len(fixed_planets) == 1:
                        replacement = f"{fixed_planets[0]} está em {_canon_sign(claimed_sign)}"
                    else:
                        replacement = ", ".join(fixed_planets[:-1]) + f" e {fixed_planets[-1]} estão em {_canon_sign(claimed_sign)}"
                    corrected = corrected[:m.start()] + replacement + corrected[m.end():]
                continue
            # PADRÃO forward ou reverse — trata cada match individualmente
            planet_written = m.group("planet")
            prefix = ""
            try:
                prefix = m.group("prefix") or ""
            except (IndexError, re.error):
                prefix = ""
            replacement = _handle_claim(
                m.start(), m.end(),
                planet_written, claimed_sign, prefix=prefix,
            )
            if replacement is not None:
                corrected = corrected[:m.start()] + replacement + corrected[m.end():]

    return corrected, divergences


def cleanup_pass(text: str):
    """
    Run report-wide cleanup on the fully-assembled text.
    Returns (cleaned_text, list_of_change_dicts).
    """
    changes = []

    # ---------- 1. "Não é X. É Y" / "Não é X, é Y" — strip ALL ----------
    # Antes deixávamos a PRIMEIRA ocorrência passar e reescrevíamos as demais,
    # mas a Marcia reportou que mesmo uma única aparição desse padrão em cada
    # relatório está lida como marca de IA (Claude adora essa cadência
    # antitética). Zerar totalmente: cada ocorrência vira só a afirmação Y,
    # descartando a negação X, preservando a caixa do 'é'/'É'.
    matches = list(_NEG_AFFIRM_RE.finditer(text))
    for m in reversed(matches):
        orig = m.group(0)
        y = m.group(4).strip()
        ending = m.group(5)
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

    # Disclaimer sobre hora desconhecida — inserido no topo, ANTES de qualquer
    # seção que dependa da hora. Duas variantes conforme a Lua tenha ou não
    # mudado de signo no dia (moon_meta vem do endpoint via chart["_moon_meta"]).
    if _time_is_unknown(chart):
        _mm = _moon_ingress_meta(chart)
        if _mm.get("moon_sign_uncertain"):
            # A Lua tem dois signos possíveis. As duas descrições ficam AQUI,
            # no topo, para não contaminar Abertura/Sol-Lua/Lua com premissa
            # contraditória. Os blurbs em si (condensados de trechos autorais)
            # são gerados depois em _apply_moon_note (app.py, Branch A), que
            # substitui o marcador <<MOON_BLURBS>>. Se a geração dos blurbs
            # falhar, o marcador é apenas removido e a nota fica íntegra.
            full_report += (
                "## Uma nota importante sobre este mapa\n\n"
                "Você não informou o horário exato de nascimento, então este relatório foi elaborado sem "
                "os pontos que dependem dele. Especificamente:\n\n"
                "- **O Ascendente e as casas astrológicas não puderam ser calculados.** Nenhuma seção fala "
                "sobre como você se apresenta ao mundo, sobre a divisão dos setores da vida por casa, ou "
                "sobre a posição por casa de cada planeta.\n"
                "- **A posição planetária nos signos permanece confiável** para o Sol, Mercúrio, Vênus, "
                "Marte, Júpiter, Saturno, Urano, Netuno, Plutão, Quíron, Lilith, nodos e asteróides. "
                "Esses cálculos independem da hora.\n"
                "- **A Lua muda de signo no dia do seu nascimento** e o signo específico dela é "
                "indeterminado sem a hora exata. Ela pode ter estado em "
                f"**{_mm.get('moon_sign_before')}** (até as **{_mm.get('moon_ingress_local_time')}** "
                f"horário local) ou em **{_mm.get('moon_sign_after')}** (a partir desse horário). "
                "Nenhuma seção deste relatório assume um desses dois signos — nem a abertura, nem a "
                "seção do Sol, nem a seção da Lua. A vida emocional é lida pelos aspectos que a Lua "
                "faz (que independem do signo). Abaixo estão as duas descrições possíveis do signo "
                "da Lua para você reconhecer qual ressoa com sua experiência interior.\n\n"
                "<<MOON_BLURBS>>\n\n"
                "Se em algum momento você recuperar o horário exato — em certidão, registro de "
                "maternidade, com familiares —, todo o mapa pode ser refeito com precisão.\n"
            )
        else:
            full_report += (
                "## Uma nota importante sobre este mapa\n\n"
                "Você não informou o horário exato de nascimento, então este relatório foi elaborado sem "
                "os pontos que dependem dele. Especificamente:\n\n"
                "- **O Ascendente e as casas astrológicas não puderam ser calculados.** Nenhuma seção fala "
                "sobre como você se apresenta ao mundo, sobre a divisão dos setores da vida por casa, ou "
                "sobre a posição por casa de cada planeta.\n"
                "- **A posição planetária nos signos permanece confiável** — Sol, Lua, Mercúrio, Vênus, "
                "Marte, Júpiter, Saturno, Urano, Netuno, Plutão, Quíron, Lilith, nodos e asteróides. Esses "
                "cálculos independem da hora.\n"
                f"- **A Lua, felizmente, permaneceu em {_mm.get('moon_sign')} durante todo o dia do seu "
                "nascimento**, de modo que a leitura da vida emocional pode ser feita com segurança.\n\n"
                "Se em algum momento você recuperar o horário exato — em certidão, registro de maternidade, "
                "com familiares —, todo o mapa pode ser refeito com precisão.\n"
            )

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

    # Verificação anti-alucinação de posicionamento planetário.
    # Varre TODAS as afirmações "[planeta] em [signo]" contra os dados
    # reais do chart e (a) reescreve o signo quando afirmado errado,
    # (b) remove afirmações de signo para a Lua quando o signo é
    # indeterminado. Divergências são retornadas para o meta da resposta
    # via app.py, para o operador saber que uma alucinação foi capturada.
    _mm = _moon_ingress_meta(chart)
    _moon_uncertain = _time_is_unknown(chart) and bool(_mm.get("moon_sign_uncertain"))
    full_report, sign_divergences = verify_planet_signs(
        full_report, chart, moon_uncertain=_moon_uncertain,
    )
    if sign_divergences and verbose:
        print(f"\n[VERIFICADOR DE SIGNOS] {len(sign_divergences)} divergência(s) corrigida(s):")
        for d in sign_divergences:
            print(f"  · {d['planet']}: afirmado '{d['claimed_sign']}', real '{d['actual_sign']}' — ação: {d['action']}")
            print(f"    contexto: …{d['context'][:100]}…")

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
        "sign_divergences": sign_divergences,
        "parental_clusters": chart.get("_parental_clusters"),
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
