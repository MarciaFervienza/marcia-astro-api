#!/usr/bin/env python3
"""
pdf_generator.py — render a branded PDF from the markdown report text.

Layout:
  - Cover page:  logo centered top, client name in red, birth data in grey,
                 "Mapa Natal" subtitle in teal.
  - Each section: H2 title in red bold sans-serif, thin teal divider rule,
                  body in serif dark grey.
  - Footer on every page (cover included): page number centered,
                                           "Marcia Fervienza © YYYY" left-aligned.

Public API:
    generate_pdf(report_text: str,
                 client_name: str,
                 birth_date: str,
                 birth_place: str) -> bytes
"""

import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# Vendored fallbacks for the small bits we need from report_generator. The
# canonical definitions live there; these locals are used only when the import
# fails (e.g., when a unit test imports pdf_generator without the LLM client
# libraries installed). On Railway the real `report_generator` is always
# importable, so these get overwritten by the imports below.
PLANET_LABEL_PT = {
    "sun": "Sol", "moon": "Lua", "mercury": "Mercúrio", "venus": "Vênus",
    "mars": "Marte", "jupiter": "Júpiter", "saturn": "Saturno", "uranus": "Urano",
    "neptune": "Netuno", "pluto": "Plutão", "chiron": "Quíron", "lilith": "Lilith",
    "north_node": "Nodo Norte", "south_node": "Nodo Sul",
    "ceres": "Ceres", "vesta": "Vesta", "juno": "Juno", "pallas": "Palas",
}
IN_SIGN_ASPECTS = {
    "conjunction": 0, "opposition": 6, "trine": 4, "square": 3, "sextile": 2,
}
_SIGN_ORDER = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]


def _fallback_is_in_sign(sa: str, sb: str, t: str) -> bool:
    if t not in IN_SIGN_ASPECTS:
        return False
    sa = (sa or "").strip().lower()
    sb = (sb or "").strip().lower()
    if sa not in _SIGN_ORDER or sb not in _SIGN_ORDER:
        return False
    ia, ib = _SIGN_ORDER.index(sa), _SIGN_ORDER.index(sb)
    raw = abs(ia - ib)
    return min(raw, 12 - raw) == IN_SIGN_ASPECTS[t]


def _fallback_get_in_sign_aspects(aspects, points=None):
    points = points or {}
    out = []
    for a in aspects or []:
        sa = a.get("planet_a_sign") or (points.get(a.get("planet_a"), {}) or {}).get("sign")
        sb = a.get("planet_b_sign") or (points.get(a.get("planet_b"), {}) or {}).get("sign")
        if sa and sb and _fallback_is_in_sign(sa, sb, a.get("type", "")):
            out.append(a)
    return out


get_in_sign_aspects = _fallback_get_in_sign_aspects

# Now try to import the real definitions and override the fallbacks.
try:
    from report_generator import (
        IN_SIGN_ASPECTS as _RG_IN_SIGN_ASPECTS,
        PLANET_LABEL_PT as _RG_PLANET_LABEL_PT,
        get_in_sign_aspects as _RG_get_in_sign_aspects,
    )
    IN_SIGN_ASPECTS = _RG_IN_SIGN_ASPECTS
    PLANET_LABEL_PT = _RG_PLANET_LABEL_PT
    get_in_sign_aspects = _RG_get_in_sign_aspects
except ImportError:
    pass  # use fallbacks — already defined above

logger = logging.getLogger("pdf-generator")

# requests is imported lazily inside the chart-image fetcher so this module
# remains importable even in environments where it isn't installed.

# ============================================================
# DESIGN TOKENS
# ============================================================
COLOR_RED = HexColor("#E03C31")
COLOR_TEAL = HexColor("#239C93")
COLOR_BODY = HexColor("#1A1A1A")
COLOR_GREY = HexColor("#666666")
COLOR_FOOTER = HexColor("#999999")
COLOR_TABLE_GRID = HexColor("#E5E0D8")

PAGE_W, PAGE_H = A4
SIDE_MARGIN = 2.2 * cm
TOP_MARGIN = 2.2 * cm
BOTTOM_MARGIN = 2.0 * cm  # leaves room for footer

LOGO_PATH = Path(__file__).parent / "logo.png"

# Translate aspect type strings (English keys from AstroAPI / our chart JSON)
# to their Portuguese display labels for the aspects table.
ASPECT_LABEL_PT = {
    "conjunction": "conjunção",
    "opposition": "oposição",
    "trine": "trígono",
    "square": "quadratura",
    "sextile": "sextil",
}

CHART_PAGE_FOOTNOTE = (
    "Este relatório foi sintetizado para oferecer uma leitura coerente e utilizável "
    "do mapa natal como um todo, em vez de cobrir cada aspecto individualmente. "
    "Por essa razão, o texto pode ou não mencionar aspectos específicos de forma "
    "explícita — mas todos foram considerados na composição da personalidade descrita."
)


# ============================================================
# STYLES
# ============================================================
def _styles():
    return {
        "section_title": ParagraphStyle(
            name="section_title",
            fontName="Helvetica-Bold",
            fontSize=17,
            textColor=COLOR_RED,
            spaceBefore=22,
            spaceAfter=4,
            leading=21,
        ),
        "body": ParagraphStyle(
            name="body",
            fontName="Times-Roman",
            fontSize=11,
            textColor=COLOR_BODY,
            leading=16,
            alignment=TA_JUSTIFY,
            spaceAfter=10,
        ),
        "cover_name": ParagraphStyle(
            name="cover_name",
            fontName="Helvetica-Bold",
            fontSize=30,
            textColor=COLOR_RED,
            alignment=TA_CENTER,
            spaceBefore=28,
            spaceAfter=14,
            leading=34,
        ),
        "cover_birth": ParagraphStyle(
            name="cover_birth",
            fontName="Helvetica",
            fontSize=12,
            textColor=COLOR_GREY,
            alignment=TA_CENTER,
            spaceAfter=4,
            leading=16,
        ),
        "cover_subtitle": ParagraphStyle(
            name="cover_subtitle",
            fontName="Helvetica",
            fontSize=18,
            textColor=COLOR_TEAL,
            alignment=TA_CENTER,
            spaceBefore=40,
            leading=22,
        ),
        "chart_page_title": ParagraphStyle(
            name="chart_page_title",
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=COLOR_RED,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=10,
            leading=16,
        ),
        "footnote": ParagraphStyle(
            name="footnote",
            fontName="Times-Italic",
            fontSize=8,
            textColor=COLOR_GREY,
            alignment=TA_JUSTIFY,
            leading=11,
            spaceBefore=16,
        ),
    }


# ============================================================
# MARKDOWN PARSING
# ============================================================
def _parse_sections(report_text: str):
    """
    Parse markdown report into a list of (title, [paragraph, ...]).
    The first H1 line (cover title) is skipped.
    """
    # Split on '## ' at start of a line; first chunk is the H1 block.
    chunks = re.split(r"\n##\s+", report_text)
    sections = []
    for blk in chunks[1:]:
        head, _, rest = blk.partition("\n")
        title = head.strip()
        # Split body into paragraphs at blank lines.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", rest) if p.strip()]
        sections.append((title, paragraphs))
    return sections


def _escape(text: str) -> str:
    """Escape characters that ReportLab's mini-XML paragraph parser cares about."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ============================================================
# FOOTER (drawn by Platypus' onPage callback)
# ============================================================
def _draw_footer(canv, doc):
    canv.saveState()
    canv.setFont("Helvetica", 9)
    canv.setFillColor(COLOR_FOOTER)
    # Centered page number
    canv.drawCentredString(PAGE_W / 2, 1.1 * cm, str(doc.page))
    # Copyright on left
    canv.drawString(SIDE_MARGIN, 1.1 * cm, f"Marcia Fervienza © {datetime.now().year}")
    canv.restoreState()


# ============================================================
# CHART PAGE — PNG image fetch + aspects table
# ============================================================
def _looks_like_raster_image(blob: bytes) -> bool:
    """True for PNG/JPEG/GIF byte signatures. Used to reject HTML error
    pages or SVG content that AstroAPI might return on a wrong endpoint."""
    if not blob or len(blob) < 8:
        return False
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    # JPEG: FF D8 FF
    if blob[:3] == b"\xff\xd8\xff":
        return True
    # GIF: GIF87a or GIF89a
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


def _fetch_chart_image(url: str, timeout: float = 15.0) -> Optional[bytes]:
    """Fetch a chart image (PNG or JPEG) from a URL.

    Returns the raw image bytes on success, or None on any failure. We
    explicitly check the byte signature so an SVG URL accidentally passed
    here doesn't pollute the PDF with garbled content — if AstroAPI only
    returned SVG, this returns None and the chart page falls back to
    showing just the aspects table.

    NOTE: we deliberately do NOT depend on svglib / cairosvg / pycairo.
    The chart image must be a raster format (PNG/JPEG/GIF) that ReportLab's
    Image flowable can embed directly.
    """
    if not url or not url.strip():
        return None
    s = url.strip()
    # Reject inline SVG markup — we don't render SVG anymore
    if s.startswith("<?xml") or s.startswith("<svg"):
        logger.warning("inline SVG passed to _fetch_chart_image — PNG required, skipping wheel")
        return None
    try:
        import requests  # lazy import (in requirements.txt, but import-safe)
    except ImportError:
        logger.warning("requests not installed; skipping chart image")
        return None
    try:
        r = requests.get(s, timeout=timeout)
    except Exception as e:
        logger.warning("chart image fetch failed for %s: %s", s, e)
        return None
    if r.status_code != 200 or not r.content:
        logger.warning("chart image fetch returned HTTP %s for %s", r.status_code, s)
        return None
    if not _looks_like_raster_image(r.content):
        logger.warning(
            "URL %s did not return raster image (got %d bytes, first 16: %r) — "
            "AstroAPI may only have an SVG variant for this chart; falling back "
            "to aspects-table-only chart page.",
            s, len(r.content), r.content[:16],
        )
        return None
    return r.content


def _chart_image_flowable(image_bytes: bytes, target_width_pts: float,
                          target_height_pts: float):
    """Build an Image flowable from raw PNG/JPEG bytes, scaled to fit a
    target bounding box while preserving aspect ratio. Returns None on failure."""
    try:
        img = Image(io.BytesIO(image_bytes))
    except Exception as e:
        logger.warning("ReportLab could not load chart image bytes: %s", e)
        return None
    try:
        iw, ih = float(img.imageWidth), float(img.imageHeight)
    except Exception:
        return None
    if iw <= 0 or ih <= 0:
        return None
    ratio = min(target_width_pts / iw, target_height_pts / ih)
    img.drawWidth = iw * ratio
    img.drawHeight = ih * ratio
    img.hAlign = "CENTER"
    return img


def _aspects_table(in_sign_aspects: list, styles):
    """Build the in-sign aspects table flowable."""
    header = ["Planeta A", "Aspecto", "Planeta B", "Orbe"]
    rows = [header]
    for a in in_sign_aspects:
        pa = a.get("planet_a_pt") or PLANET_LABEL_PT.get(a.get("planet_a", ""), a.get("planet_a", ""))
        pb = a.get("planet_b_pt") or PLANET_LABEL_PT.get(a.get("planet_b", ""), a.get("planet_b", ""))
        asp = (
            a.get("type_pt")
            or ASPECT_LABEL_PT.get(a.get("type", ""), a.get("type", ""))
        )
        try:
            orb = float(a.get("orb", 0.0))
        except (TypeError, ValueError):
            orb = 0.0
        rows.append([pa, asp, pb, f"{orb:.1f}°"])

    table = Table(
        rows,
        colWidths=[4.6 * cm, 3.6 * cm, 4.6 * cm, 2.4 * cm],
        repeatRows=1,
        hAlign="CENTER",
    )
    table.setStyle(TableStyle([
        # Header row
        ("BACKGROUND",   (0, 0), (-1, 0), COLOR_RED),
        ("TEXTCOLOR",    (0, 0), (-1, 0), white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 10),
        ("ALIGN",        (0, 0), (-1, 0), "CENTER"),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 8),
        ("TOPPADDING",   (0, 0), (-1, 0), 7),
        # Body
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 10),
        ("TEXTCOLOR",    (0, 1), (-1, -1), COLOR_BODY),
        ("ALIGN",        (3, 1), (3, -1), "RIGHT"),  # orb column right-aligned
        ("ALIGN",        (1, 1), (1, -1), "CENTER"), # aspect column centered
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 5),
        # Grid
        ("BOX",          (0, 0), (-1, -1), 0.6, COLOR_TEAL),
        ("INNERGRID",    (0, 0), (-1, -1), 0.3, COLOR_TABLE_GRID),
    ]))
    return table


def _chart_page_flowables(
    chart_image_url: str,
    aspects: list,
    points: dict,
    styles,
):
    """Build the second-page flowables: chart wheel image + in-sign aspects
    table + footnote."""
    flow = []

    # 1) Chart wheel (best-effort — degrades gracefully if fetch fails or
    # AstroAPI only has SVG for this chart).
    # Cap to a fixed 11 cm square. Chart wheels are square; 11 cm leaves
    # enough vertical room for a 12-row aspects table plus the footnote on
    # the same page (the frame is ~25.5 cm tall, so this keeps the chart
    # page from spilling onto a second sheet).
    target_w_pts = 11 * cm
    target_h_pts = 11 * cm

    image_bytes = _fetch_chart_image(chart_image_url) if chart_image_url else None
    img = (
        _chart_image_flowable(image_bytes, target_w_pts, target_h_pts)
        if image_bytes else None
    )
    if img is not None:
        flow.append(img)
    else:
        # No wheel — keep some breathing room so the table doesn't jump to the top
        flow.append(Spacer(1, 0.6 * cm))

    flow.append(Spacer(1, 0.8 * cm))

    # 2) Aspects table (in-sign only)
    in_sign = get_in_sign_aspects(aspects, points) if aspects else []
    if in_sign:
        flow.append(Paragraph("Aspectos principais", styles["chart_page_title"]))
        flow.append(_aspects_table(in_sign, styles))

    # 3) Footnote
    flow.append(Paragraph(CHART_PAGE_FOOTNOTE, styles["footnote"]))

    flow.append(PageBreak())
    return flow


# ============================================================
# COVER FLOWABLES
# ============================================================
def _cover_flowables(client_name: str, birth_date: str, birth_place: str, styles):
    """Build the list of flowables that fill the cover page."""
    flow = []

    # Push content down to roughly the upper third of the page
    flow.append(Spacer(1, 2.0 * cm))

    if LOGO_PATH.exists():
        try:
            img = Image(str(LOGO_PATH))
            # Constrain to a comfortable size; preserve aspect ratio.
            max_w = 9.0 * cm
            max_h = 4.5 * cm
            iw, ih = float(img.imageWidth), float(img.imageHeight)
            ratio = min(max_w / iw, max_h / ih)
            img.drawWidth = iw * ratio
            img.drawHeight = ih * ratio
            img.hAlign = "CENTER"
            flow.append(img)
        except Exception:
            # If the logo file is malformed, fall through to text-only cover.
            pass

    # Client name (red, big)
    flow.append(Paragraph(_escape(client_name or "Cliente"), styles["cover_name"]))

    # Birth data (grey, small)
    if birth_date:
        flow.append(Paragraph(_escape(birth_date), styles["cover_birth"]))
    if birth_place:
        flow.append(Paragraph(_escape(birth_place), styles["cover_birth"]))

    # Subtitle (teal)
    flow.append(Paragraph("Mapa Natal", styles["cover_subtitle"]))

    flow.append(PageBreak())
    return flow


# ============================================================
# SECTION FLOWABLES
# ============================================================
def _section_flowables(title: str, paragraphs: list, styles):
    """Build the flowables for one section."""
    flow = []

    # Keep the title + divider + first paragraph together so the title never
    # ends up alone at the bottom of a page.
    header_block = [
        Paragraph(_escape(title), styles["section_title"]),
        HRFlowable(
            width="100%",
            thickness=0.6,
            color=COLOR_TEAL,
            spaceBefore=2,
            spaceAfter=14,
            lineCap="round",
        ),
    ]
    if paragraphs:
        header_block.append(Paragraph(_escape(paragraphs[0]), styles["body"]))
        rest = paragraphs[1:]
    else:
        rest = []

    flow.append(KeepTogether(header_block))
    for p in rest:
        flow.append(Paragraph(_escape(p), styles["body"]))

    return flow


# ============================================================
# PUBLIC API
# ============================================================
def generate_pdf(
    report_text: str,
    client_name: str,
    birth_date: str = "",
    birth_place: str = "",
    chart_image_url: str = "",
    aspects: list = None,
    points: dict = None,
    chart_svg_url: str = "",  # backwards-compatible alias, deprecated
) -> bytes:
    """
    Render the natal report into a branded PDF.

    Args:
        report_text       — full markdown report from generate_report().
        client_name       — name to show on the cover.
        birth_date        — birth date string (free-form, shown on cover).
        birth_place       — birth place string (free-form, shown on cover).
        chart_image_url   — optional URL to a PNG (or JPEG/GIF) of the chart wheel.
                            ReportLab embeds the image directly — no SVG rendering,
                            no svglib / cairosvg dependency. If only SVG is available
                            upstream, leave this empty and the chart page falls back
                            to the aspects table alone.
        aspects           — optional full aspects list (filtered internally to in-sign
                            aspects).
        points            — optional planet positions dict, used to look up signs when
                            the aspects don't carry planet_a_sign / planet_b_sign
                            explicitly.
        chart_svg_url     — deprecated alias accepted for backwards compatibility.
                            If passed (and chart_image_url is empty), it's used as the
                            image URL — but raster signature validation will then
                            reject SVG content and skip the wheel.

    Returns:
        PDF document as bytes.
    """
    # Backwards-compat alias
    if not chart_image_url and chart_svg_url:
        chart_image_url = chart_svg_url
    buf = io.BytesIO()
    styles = _styles()

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        title=f"Mapa Natal — {client_name}",
        author="Marcia Fervienza",
        subject="Mapa Natal",
        leftMargin=SIDE_MARGIN,
        rightMargin=SIDE_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
    )

    frame = Frame(
        SIDE_MARGIN,
        BOTTOM_MARGIN,
        PAGE_W - 2 * SIDE_MARGIN,
        PAGE_H - TOP_MARGIN - BOTTOM_MARGIN,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
    )

    doc.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=_draw_footer),
    ])

    story = []
    story.extend(_cover_flowables(client_name, birth_date, birth_place, styles))

    # Chart page — only add it if we have something to show
    if chart_image_url or aspects:
        story.extend(_chart_page_flowables(chart_image_url, aspects or [], points or {}, styles))

    for title, paragraphs in _parse_sections(report_text):
        story.extend(_section_flowables(title, paragraphs, styles))

    doc.build(story)
    return buf.getvalue()
