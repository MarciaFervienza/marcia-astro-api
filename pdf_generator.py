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

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
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


# ============================================================
# CUSTOM FONT REGISTRATION
# ============================================================
# Bundled TTFs live in ./fonts/ next to this file. Both families are
# OFL-licensed (SIL Open Font License — free for embedding in any output,
# commercial included). Registration is idempotent per process — safe to
# call multiple times; ReportLab warns and skips duplicates.
FONTS_DIR = Path(__file__).parent / "fonts"


def _register_fonts_once():
    """Register EB Garamond + Inter TTFs with ReportLab. Safe to call
    repeatedly; ReportLab silently ignores re-registration attempts. If
    the bundled fonts are missing or corrupt, this raises — the report
    can't ship without them.
    """
    faces = {
        # EB Garamond — elegant serif for headings and display type
        "EBGaramond-Regular":    "EBGaramond-Regular.ttf",
        "EBGaramond-Italic":     "EBGaramond-Italic.ttf",
        "EBGaramond-Bold":       "EBGaramond-Bold.ttf",
        "EBGaramond-BoldItalic": "EBGaramond-BoldItalic.ttf",
        # Inter — clean sans-serif for body copy and UI-like elements
        "Inter-Regular":  "Inter-Regular.ttf",
        "Inter-Italic":   "Inter-Italic.ttf",
        "Inter-Medium":   "Inter-Medium.ttf",
        "Inter-SemiBold": "Inter-SemiBold.ttf",
    }
    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, fname in faces.items():
        if name in registered:
            continue
        path = FONTS_DIR / fname
        if not path.exists():
            raise FileNotFoundError(
                f"required font missing: {path} — cannot render styled PDF"
            )
        pdfmetrics.registerFont(TTFont(name, str(path)))

    # Register the two family groupings so ReportLab's Paragraph <b>/<i>
    # HTML tags dispatch to the correct face automatically. Inter only
    # ships a regular italic in our subset — <b> will fall back to
    # Inter-SemiBold, which is what we want visually.
    registerFontFamily(
        "EBGaramond",
        normal="EBGaramond-Regular",
        bold="EBGaramond-Bold",
        italic="EBGaramond-Italic",
        boldItalic="EBGaramond-BoldItalic",
    )
    registerFontFamily(
        "Inter",
        normal="Inter-Regular",
        bold="Inter-SemiBold",
        italic="Inter-Italic",
        boldItalic="Inter-SemiBold",
    )


_register_fonts_once()

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
# Luxury-book palette. Only ivory + charcoal do the heavy lifting; the
# accent trio (sage / terracotta / gold) is used deliberately sparingly
# — a thin rule under a title, an emphasized word inside body copy, the
# page number. Every request the palette answers should reach for the
# most restrained option first.
COLOR_IVORY     = HexColor("#F8F5EF")  # page background
COLOR_CHARCOAL  = HexColor("#2F2F2F")  # body text
COLOR_SAGE      = HexColor("#A7B3A1")  # secondary accents, subtle rules
COLOR_TERRACOTTA = HexColor("#B97A63")  # emphasis, section titles
COLOR_GOLD      = HexColor("#C7A66A")  # fine flourishes, page numbers

# Retained for backwards-compat with any downstream callers that imported
# the old constant names. Do NOT use these in new code — they resolve to
# the new palette's closest equivalent.
COLOR_RED  = COLOR_TERRACOTTA
COLOR_TEAL = COLOR_SAGE
COLOR_BODY = COLOR_CHARCOAL
COLOR_GREY = HexColor("#8A8579")  # muted stone, for footer / birth-data
COLOR_FOOTER = COLOR_GREY
COLOR_TABLE_GRID = HexColor("#E6DFCE")  # ivory-toned hairline

PAGE_W, PAGE_H = A4
# Generous margins — the whole point of the redesign is white space.
# 2.8cm sides and 2.6cm top leave a Kinfolk-editorial measure at
# ~11.4cm line-length for the body text.
SIDE_MARGIN = 2.8 * cm
TOP_MARGIN = 2.6 * cm
BOTTOM_MARGIN = 2.2 * cm  # leaves room for footer

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
        # --------- Section-body flow ---------
        "section_title": ParagraphStyle(
            name="section_title",
            fontName="EBGaramond-Regular",
            fontSize=22,
            textColor=COLOR_TERRACOTTA,
            spaceBefore=32,
            spaceAfter=6,
            leading=26,
            alignment=TA_LEFT,
        ),
        # Astrological subtitle rendered under the main section title, e.g.
        # "Mercúrio em Capricórnio · Casa 7". Small Inter, muted, so the
        # psychological main heading stays the primary visual anchor.
        "section_subtitle": ParagraphStyle(
            name="section_subtitle",
            fontName="Inter-Regular",
            fontSize=9,
            textColor=COLOR_GREY,
            spaceBefore=0,
            spaceAfter=18,
            leading=13,
            alignment=TA_LEFT,
        ),
        # Pull quote — one sentence, large italic serif, centered, generous
        # leading. Used on standalone "breather" pages between sections.
        "pull_quote": ParagraphStyle(
            name="pull_quote",
            fontName="EBGaramond-Italic",
            fontSize=20,
            textColor=COLOR_CHARCOAL,
            leading=32,
            alignment=TA_CENTER,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "pull_quote_mark": ParagraphStyle(
            name="pull_quote_mark",
            fontName="EBGaramond-Regular",
            fontSize=52,
            textColor=COLOR_GOLD,
            leading=52,
            alignment=TA_CENTER,
            spaceBefore=0,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            name="body",
            fontName="Inter-Regular",
            fontSize=10.5,
            textColor=COLOR_CHARCOAL,
            leading=17,             # generous line-height (~162% of size)
            alignment=TA_JUSTIFY,
            spaceAfter=12,
            firstLineIndent=0,      # editorial: no indents, blank-line separation
        ),

        # --------- Cover page ---------
        "cover_kicker": ParagraphStyle(
            name="cover_kicker",
            fontName="Inter-Medium",
            fontSize=9,
            textColor=COLOR_GOLD,
            alignment=TA_CENTER,
            leading=12,
            spaceAfter=6,
        ),
        "cover_title": ParagraphStyle(
            name="cover_title",
            fontName="EBGaramond-Regular",
            fontSize=44,
            textColor=COLOR_CHARCOAL,
            alignment=TA_CENTER,
            leading=50,
            spaceBefore=6,
            spaceAfter=4,
        ),
        "cover_title_accent": ParagraphStyle(
            name="cover_title_accent",
            fontName="EBGaramond-Italic",
            fontSize=44,
            textColor=COLOR_TERRACOTTA,
            alignment=TA_CENTER,
            leading=50,
            spaceBefore=0,
            spaceAfter=20,
        ),
        "cover_attribution": ParagraphStyle(
            name="cover_attribution",
            fontName="Inter-Regular",
            fontSize=10,
            textColor=COLOR_CHARCOAL,
            alignment=TA_CENTER,
            leading=14,
            spaceAfter=32,
        ),
        "cover_name": ParagraphStyle(
            name="cover_name",
            fontName="EBGaramond-Italic",
            fontSize=26,
            textColor=COLOR_CHARCOAL,
            alignment=TA_CENTER,
            leading=32,
            spaceBefore=4,
            spaceAfter=14,
        ),
        "cover_birth": ParagraphStyle(
            name="cover_birth",
            fontName="Inter-Regular",
            fontSize=10,
            textColor=COLOR_GREY,
            alignment=TA_CENTER,
            leading=15,
            spaceAfter=3,
        ),

        # --------- Chart page ---------
        "chart_page_title": ParagraphStyle(
            name="chart_page_title",
            fontName="EBGaramond-Regular",
            fontSize=15,
            textColor=COLOR_CHARCOAL,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=10,
            leading=19,
        ),
        "chart_page_kicker": ParagraphStyle(
            name="chart_page_kicker",
            fontName="Inter-Medium",
            fontSize=8,
            textColor=COLOR_GOLD,
            alignment=TA_CENTER,
            spaceAfter=4,
            leading=11,
        ),
        "footnote": ParagraphStyle(
            name="footnote",
            fontName="EBGaramond-Italic",
            fontSize=8.5,
            textColor=COLOR_GREY,
            alignment=TA_JUSTIFY,
            leading=13,
            spaceBefore=20,
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
# PAGE CHROME (background + footer, drawn on every page)
# ============================================================
def _draw_page_chrome(canv, doc):
    """Paint the ivory background over the entire A4 canvas, then stamp a
    quiet editorial footer at the bottom. Runs on every page including
    the cover. The Platypus PageTemplate's onPage callback receives the
    canvas *before* the frame flowables are drawn, so the ivory rectangle
    ends up behind everything else naturally.
    """
    canv.saveState()

    # --- Ivory background rectangle -------------------------------
    canv.setFillColor(COLOR_IVORY)
    canv.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # --- Thin gold rule above the footer band ---------------------
    # Sits at 1.6cm from the bottom, 3cm wide, centered — a hairline
    # bookmark that signals "footer starts here" without shouting.
    rule_y = 1.55 * cm
    rule_half = 1.5 * cm
    canv.setStrokeColor(COLOR_GOLD)
    canv.setLineWidth(0.4)
    canv.line(
        PAGE_W / 2 - rule_half, rule_y,
        PAGE_W / 2 + rule_half, rule_y,
    )

    # --- Footer content -------------------------------------------
    # Left: attribution in Inter, muted.
    canv.setFont("Inter-Regular", 8)
    canv.setFillColor(COLOR_GREY)
    canv.drawString(
        SIDE_MARGIN, 1.05 * cm,
        f"Márcia Fervienza  ·  marciafervienza.com",
    )
    # Center: elegant italic serif year — the copyright reads as a mark
    # rather than a legal notice.
    canv.setFont("EBGaramond-Italic", 8)
    canv.drawCentredString(
        PAGE_W / 2, 1.05 * cm,
        f"© {datetime.now().year}",
    )
    # Right: page number in gold, larger, EBGaramond digit.
    canv.setFont("EBGaramond-Regular", 10)
    canv.setFillColor(COLOR_GOLD)
    canv.drawRightString(
        PAGE_W - SIDE_MARGIN, 1.0 * cm,
        str(doc.page),
    )
    canv.restoreState()


# Backwards-compat alias — some earlier code referenced _draw_footer.
_draw_footer = _draw_page_chrome


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


def _fetch_chart_image(url: str, timeout: float = 15.0):
    """Load a chart wheel into a ReportLab-compatible flowable input.

    Returns one of:
      - a ReportLab `Drawing` object (when the input is a local .svg path —
        produced by Kerykeion in app.py and rendered via svglib),
      - raw image bytes (when the input is a raster URL or local PNG/JPEG),
      - None on any failure.

    Local SVG path is the primary code path in production. The raster
    branches remain for backwards-compatibility and out-of-band testing.
    """
    if not url or not url.strip():
        return None
    s = url.strip()

    # Reject inline SVG markup — must be a path or URL
    if s.startswith("<?xml") or s.startswith("<svg"):
        logger.warning("inline SVG markup passed to _fetch_chart_image — need a file path, skipping wheel")
        return None

    # Local file path (absolute or user-relative)
    if s.startswith("/") or s.startswith("~"):
        from pathlib import Path
        p = Path(s).expanduser()
        if not p.exists():
            logger.warning("local chart image not found: %s", p)
            return None

        # SVG → load via svglib into a ReportLab Drawing flowable
        if p.suffix.lower() == ".svg":
            try:
                from svglib.svglib import svg2rlg
            except ImportError as e:
                logger.warning("svglib not installed; cannot render SVG chart wheel: %s", e)
                return None
            try:
                drawing = svg2rlg(str(p))
            except Exception as e:
                logger.warning("svglib failed to parse %s: %s", p, e)
                return None
            if drawing is None:
                logger.warning("svglib returned None for %s", p)
                return None
            return drawing

        # Raster path — read bytes
        try:
            content = p.read_bytes()
        except Exception as e:
            logger.warning("could not read local chart image %s: %s", s, e)
            return None
        if not _looks_like_raster_image(content):
            logger.warning("local file %s is not a raster image", s)
            return None
        return content

    # Remote URL — only raster supported
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
            "URL %s did not return raster image (got %d bytes, first 16: %r); "
            "falling back to aspects-table-only chart page.",
            s, len(r.content), r.content[:16],
        )
        return None
    return r.content


def _chart_image_flowable(chart_image, target_width_pts: float,
                          target_height_pts: float):
    """Wrap a chart image (either a svglib Drawing or raster bytes) into a
    Platypus flowable scaled to fit the target bounding box while preserving
    aspect ratio. Returns None on failure."""
    if chart_image is None:
        return None

    # svglib Drawing branch — duck-typed by the .width attribute (Drawing
    # objects expose .width / .height as floats; bytes / bytearray do not).
    if hasattr(chart_image, "width") and hasattr(chart_image, "height") \
            and not isinstance(chart_image, (bytes, bytearray, memoryview)):
        drawing = chart_image
        try:
            dw, dh = float(drawing.width), float(drawing.height)
        except Exception:
            return None
        if dw <= 0 or dh <= 0:
            return None
        scale = min(target_width_pts / dw, target_height_pts / dh)
        try:
            drawing.scale(scale, scale)
            drawing.width = dw * scale
            drawing.height = dh * scale
            drawing.hAlign = "CENTER"
        except Exception as e:
            logger.warning("could not scale SVG drawing: %s", e)
            return None
        return drawing

    # Raster bytes branch (PNG/JPEG/GIF)
    try:
        img = Image(io.BytesIO(chart_image))
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
        # Header row — quiet sage rule beneath, no colored fill; the
        # column labels themselves are small-caps Inter in charcoal.
        ("BACKGROUND",     (0, 0), (-1, 0), COLOR_IVORY),
        ("TEXTCOLOR",      (0, 0), (-1, 0), COLOR_CHARCOAL),
        ("FONTNAME",       (0, 0), (-1, 0), "Inter-Medium"),
        ("FONTSIZE",       (0, 0), (-1, 0), 8.5),
        ("ALIGN",          (0, 0), (-1, 0), "CENTER"),
        ("BOTTOMPADDING",  (0, 0), (-1, 0), 10),
        ("TOPPADDING",     (0, 0), (-1, 0), 8),
        ("LINEBELOW",      (0, 0), (-1, 0), 0.6, COLOR_SAGE),

        # Body rows — Garamond for the planet names (matches the report's
        # display voice) and Inter for the orb figure (numeric clarity).
        ("FONTNAME",       (0, 1), (0, -1), "EBGaramond-Regular"),
        ("FONTNAME",       (2, 1), (2, -1), "EBGaramond-Regular"),
        ("FONTNAME",       (1, 1), (1, -1), "EBGaramond-Italic"),  # aspect name in italic serif
        ("FONTNAME",       (3, 1), (3, -1), "Inter-Regular"),
        ("FONTSIZE",       (0, 1), (-1, -1), 10),
        ("TEXTCOLOR",      (0, 1), (-1, -1), COLOR_CHARCOAL),
        ("ALIGN",          (0, 1), (0, -1), "LEFT"),
        ("ALIGN",          (1, 1), (1, -1), "CENTER"),
        ("ALIGN",          (2, 1), (2, -1), "LEFT"),
        ("ALIGN",          (3, 1), (3, -1), "RIGHT"),
        ("LEFTPADDING",    (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 10),
        ("TOPPADDING",     (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING",  (0, 1), (-1, -1), 8),

        # Row separators — a thin ivory-toned hairline. No outer box:
        # editorial tables usually let the type breathe against the page.
        ("LINEBELOW",      (0, 1), (-1, -2), 0.25, COLOR_TABLE_GRID),
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

    # Gold small-caps kicker before the wheel — sets the tone before the
    # chart itself lands. Reads as a chapter frontispiece.
    flow.append(Paragraph("O SEU MAPA", styles["chart_page_kicker"]))
    flow.append(Spacer(1, 0.2 * cm))

    # 1) Chart wheel (best-effort — degrades gracefully if the SVG failed).
    # Cap at 11.5cm square. The bigger new margins mean less horizontal
    # room on the page, so we can't push the wheel much larger without
    # squeezing the aspects table below, but 11.5cm still reads
    # substantially bigger than before because there's more surrounding
    # white space.
    target_w_pts = 11.5 * cm
    target_h_pts = 11.5 * cm

    chart_image = _fetch_chart_image(chart_image_url) if chart_image_url else None
    img = (
        _chart_image_flowable(chart_image, target_w_pts, target_h_pts)
        if chart_image is not None else None
    )
    if img is not None:
        flow.append(img)
    else:
        # No wheel — keep breathing room so the table doesn't jump to the top
        flow.append(Spacer(1, 0.6 * cm))

    flow.append(Spacer(1, 1.0 * cm))

    # 2) Aspects table (in-sign only)
    in_sign = get_in_sign_aspects(aspects, points) if aspects else []
    if in_sign:
        flow.append(Paragraph(
            "Aspectos <font face='EBGaramond-Italic'>principais</font>",
            styles["chart_page_title"],
        ))
        # Fine gold rule beneath the title, matched to the wheel width
        flow.append(HRFlowable(
            width=3.0 * cm, thickness=0.4, color=COLOR_GOLD,
            spaceBefore=2, spaceAfter=14, hAlign="CENTER", lineCap="round",
        ))
        flow.append(_aspects_table(in_sign, styles))

    # 3) Footnote
    flow.append(Paragraph(CHART_PAGE_FOOTNOTE, styles["footnote"]))

    flow.append(PageBreak())
    return flow


# ============================================================
# COVER FLOWABLES
# ============================================================
def _cover_flowables(client_name: str, birth_date: str, birth_place: str, styles):
    """Build the list of flowables that fill the cover page.

    Layout, top to bottom:
       [top margin whitespace]
       small gold kicker  — "MAPA NATAL"
       serif title in charcoal  — "Seu"
       serif italic title in terracotta — "Mapa Natal"
       fine gold rule (short, centered)
       attribution in Inter — "Interpretado por Márcia Fervienza"
       [ample space]
       client name in serif italic
       birth date + place in muted Inter
       [logo pinned near the bottom edge]
       [bottom margin]
    """
    flow = []

    # Push content down so the title starts around a third of the way in —
    # editorial covers rarely start at the very top edge.
    flow.append(Spacer(1, 2.4 * cm))

    # Small gold kicker — "MAPA NATAL" in tracked-out caps.
    #
    # Layout notes learned the hard way:
    #  · '&nbsp;' (U+00A0) between the two words renders as a mid-height
    #    dot glyph in Inter-Medium, which read as a decorative middle dot.
    #  · Plain ASCII spaces get normalized by ReportLab's Paragraph XML
    #    parser — runs collapse to single spaces AND the layout engine
    #    strips inter-letter spacing between single-letter "words", so
    #    "M A P A    N A T A L" ends up as "MAPANATAL" with no visible
    #    gaps at all.
    #
    # Solution: use typographic Unicode spaces, which the XML parser
    # treats as content characters (not markup whitespace) and so
    # preserves verbatim. EN SPACE (U+2002 — half an em wide) between
    # each letter reads like a normal tracked space; two EM SPACEs
    # (U+2003 — one em each) between the two words give a comfortable
    # word gap.
    _EN = " "
    _WORD_GAP = "  "
    kicker_text = _EN.join("MAPA") + _WORD_GAP + _EN.join("NATAL")
    flow.append(Paragraph(kicker_text, styles["cover_kicker"]))

    # Two-line serif title, second line in italic terracotta
    flow.append(Paragraph("Seu", styles["cover_title"]))
    flow.append(Paragraph("Mapa Natal", styles["cover_title_accent"]))

    # Fine gold rule — the flourish
    flow.append(HRFlowable(
        width=2.8 * cm, thickness=0.6, color=COLOR_GOLD,
        spaceBefore=2, spaceAfter=18, hAlign="CENTER", lineCap="round",
    ))

    # Attribution — quiet, in Inter
    flow.append(Paragraph(
        "Interpretado por <font face='EBGaramond-Italic'>Márcia Fervienza</font>",
        styles["cover_attribution"],
    ))

    # Breathing space before the client identity block
    flow.append(Spacer(1, 3.6 * cm))

    # Client name in EBGaramond italic — reads as a signature
    flow.append(Paragraph(_escape(client_name or "Cliente"), styles["cover_name"]))

    # Birth data — muted Inter with wide letter-spacing feel via smaller size
    if birth_date:
        flow.append(Paragraph(_escape(birth_date), styles["cover_birth"]))
    if birth_place:
        flow.append(Paragraph(_escape(birth_place), styles["cover_birth"]))

    # Push the logo/wordmark down toward the bottom edge (before the footer band)
    flow.append(Spacer(1, 3.4 * cm))

    # Logo path — but ONLY use it if it's a proper transparent-background
    # PNG. The current logo.png is 99.5% opaque white pixels (a legacy
    # "white block" export), so on ivory it would render as a big charcoal
    # box after any inversion. When a real transparent-background logo is
    # dropped in place, this check flips and it renders normally.
    if _looks_like_transparent_logo(LOGO_PATH):
        try:
            img = Image(str(LOGO_PATH))
            # Smaller than the previous cover — the logo is a maker's mark
            # here, not the headline.
            max_w = 4.0 * cm
            max_h = 2.0 * cm
            iw, ih = float(img.imageWidth), float(img.imageHeight)
            ratio = min(max_w / iw, max_h / ih)
            img.drawWidth = iw * ratio
            img.drawHeight = ih * ratio
            img.hAlign = "CENTER"
            flow.append(img)
        except Exception:
            _append_wordmark(flow, styles)
    else:
        _append_wordmark(flow, styles)

    flow.append(PageBreak())
    return flow


def _append_wordmark(flow, styles):
    """Fallback maker's mark used on the cover when no usable logo file is
    present. Just the initials in a serif, centered — reads as a signature
    rather than a placeholder.
    """
    wordmark_style = ParagraphStyle(
        name="wordmark",
        fontName="EBGaramond-Italic",
        fontSize=18,
        textColor=COLOR_TERRACOTTA,
        alignment=TA_CENTER,
        leading=22,
    )
    flow.append(Paragraph("mf", wordmark_style))


def _looks_like_transparent_logo(path):
    """True when the file at `path` is a PNG with actually-transparent
    background regions. False for the legacy all-white file or when the
    file is missing/unreadable. This is a cheap PIL check that runs once
    per PDF; if PIL isn't installed, assume the logo is good and let
    ReportLab render it.
    """
    if not path or not Path(path).exists():
        return False
    try:
        from PIL import Image as _PILImage
    except ImportError:
        return True  # trust the user
    try:
        with _PILImage.open(str(path)) as im:
            if im.mode not in ("RGBA", "LA", "PA"):
                return False
            # Sample a few corner points — a proper transparent-background
            # logo has fully transparent (alpha=0) corners.
            w, h = im.size
            corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
            corner_alphas = [im.getpixel(pt)[-1] for pt in corners]
            if not all(a == 0 for a in corner_alphas):
                return False
            # And at least 20% of the canvas should be transparent (a real
            # logo has whitespace around it; a "white block" export has
            # almost none).
            band = im.crop((w // 8, h // 8, w * 7 // 8, h * 7 // 8))
            transparent = sum(1 for px in band.getdata() if px[-1] == 0)
            total = band.width * band.height
            return (transparent / total) > 0.2
    except Exception:
        return False


# ============================================================
# PULL-QUOTE ("BREATHER") PAGES
# ============================================================
def _select_pull_quote(paragraphs: list) -> str:
    """Pick one sentence from a section that reads well as a pull quote:
    length 55-140 chars, ends with a period (declarative), preferably from
    a middle paragraph so it's not an intro or a sign-off. Returns the
    empty string if nothing qualifies — the caller then skips inserting
    a breather page for that section.

    The 140-char upper bound is tighter than an earlier version because
    long quotes wrap to 5+ lines at pull-quote size (20pt/32pt leading),
    which can overflow the single-page KeepTogether budget and force an
    orphan continuation page.
    """
    if not paragraphs:
        return ""
    # Prefer sentences from the middle of the section
    candidate_paras = paragraphs[len(paragraphs) // 3 : max(len(paragraphs) // 3 + 1, len(paragraphs) - 1)] \
                      or paragraphs
    # Split each paragraph into sentences.
    sentences = []
    for p in candidate_paras:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])", p.strip())
        sentences.extend(s.strip() for s in parts if s.strip())
    def _qualifies(s):
        if not 55 <= len(s) <= 140:
            return False
        if not s.endswith("."):
            return False
        if "—" in s and len(s) < 90:
            return False
        if "..." in s or "…" in s:
            return False
        return True
    scored = [s for s in sentences if _qualifies(s)]
    if not scored:
        return ""
    scored.sort(key=len)
    return scored[len(scored) // 2]


def _pull_quote_flowables(sentence: str, styles):
    """Full-page breather with the pull quote centered vertically.

    The content stack (top spacer + quote mark + quote text + rule) is
    wrapped in a KeepTogether so ReportLab either fits it entirely on a
    single page or moves the whole unit to the next page. Without this,
    a long quote wrapping to many lines can overflow: ReportLab renders
    the top spacer + first lines on one page, the remaining lines on
    the next page, then the terminal PageBreak triggers yet another
    fresh page — the empty-page bug reported around the Netuno quote.
    """
    if not sentence:
        return []
    quote_stack = KeepTogether([
        # 5cm above instead of 8cm — gives the quote more room to breathe
        # without pushing it past mid-page. Combined with the tighter
        # sentence length cap this fits within a single page comfortably.
        Spacer(1, 5.0 * cm),
        Paragraph("&ldquo;", styles["pull_quote_mark"]),
        Paragraph(_escape(sentence), styles["pull_quote"]),
        Spacer(1, 0.6 * cm),
        HRFlowable(
            width=1.8 * cm, thickness=0.5, color=COLOR_GOLD,
            hAlign="CENTER", lineCap="round",
        ),
    ])
    return [quote_stack, PageBreak()]


# ============================================================
# SECTION FLOWABLES
# ============================================================
# Section-title mapping: which planet key(s) belong to each title's prefix.
# The report_generator's section list uses "<Prefix>: <Psychological Phrase>"
# where <Prefix> is either a single planet, a hyphenated planet pair, or a
# short label ("Casa 4", "Nodo Sul e Nodo Norte", "Asteróides"). We treat
# the psychological phrase as the main heading and derive an astrological
# subtitle from the chart data for the prefix — showing the interpretive
# language first is exactly what the redesign asks for.
_PT_PLANET_TO_KEY = {
    "Sol":     "sun",
    "Lua":     "moon",
    "Mercúrio":"mercury",
    "Vênus":   "venus",
    "Marte":   "mars",
    "Júpiter": "jupiter",
    "Saturno": "saturn",
    "Urano":   "uranus",
    "Netuno":  "neptune",
    "Plutão":  "pluto",
    "Quíron":  "chiron",
    "Lilith":  "lilith",
}


def _subtitle_from_prefix(prefix: str, points: dict) -> str:
    """Produce a Portuguese astrological subtitle from a section-title
    prefix like 'Mercúrio' or 'Sol e Saturno'. Uses the client's actual
    positions from `points` (already computed upstream). Returns an empty
    string when the prefix doesn't map to any known planet — the caller
    then uses the original title as-is with no subtitle line.
    """
    if not points:
        return ""

    # Normalize " e " / " - " / " · " / "-" separators into a single pipe.
    normalized = prefix.replace(" e ", "|").replace(" - ", "|") \
                       .replace(" · ", "|").replace("-", "|")
    parts = [p.strip() for p in normalized.split("|") if p.strip()]

    pieces = []
    for part in parts:
        key = _PT_PLANET_TO_KEY.get(part)
        if not key:
            continue
        p = points.get(key) or {}
        sign_pt = p.get("sign_pt") or ""
        house = p.get("house")
        if sign_pt and house is not None:
            pieces.append(f"{part} em {sign_pt} · Casa {house}")
        elif sign_pt:
            pieces.append(f"{part} em {sign_pt}")

    return "   ·   ".join(pieces)


def _split_section_title(title: str, points: dict):
    """Split a section title into (main_heading, subtitle).

    - 'Mercúrio: Como Você Pensa' → ('Como Você Pensa',
                                     'Mercúrio em Capricórnio · Casa 7')
    - 'Sol e Saturno: O Pai e as Ferramentas da Vida'
                    → ('O Pai e as Ferramentas da Vida',
                       'Sol em Aquário · Casa 8   ·   Saturno em Leão · Casa 2')
    - 'Abertura' → ('Abertura', '')
    - 'Fio Condutor' → ('Fio Condutor', '')
    - 'Casa 4: Suas Raízes e Sua Casa Interna' → ('Suas Raízes e Sua Casa Interna',
                                                  'Casa 4')  (prefix as subtitle
                                                              when it isn't a planet)
    """
    if ":" not in title:
        return title, ""

    prefix, _, rest = title.partition(":")
    prefix = prefix.strip()
    main = rest.strip() or title
    subtitle = _subtitle_from_prefix(prefix, points)
    if not subtitle:
        # Prefix is a non-planet label (Casa 4, Asteróides, Sua Tríade, Nodo Sul e Nodo Norte).
        # Show it as-is under the main heading so the reader still has the
        # astrological anchor.
        subtitle = prefix
    return main, subtitle


def _section_flowables(title: str, paragraphs: list, styles, points: dict):
    """Build the flowables for one section.

    Header layout (per redesign spec):
       elegant serif main heading (the psychological phrase)
       thin gold rule (short, left-aligned) — a bookmark
       small astrological subtitle in Inter
       [breathing space]
       first paragraph
    """
    flow = []
    main_heading, subtitle = _split_section_title(title, points)

    header_parts = [
        Paragraph(_escape(main_heading), styles["section_title"]),
        HRFlowable(
            width=2.4 * cm,
            thickness=0.5,
            color=COLOR_GOLD,
            spaceBefore=2,
            spaceAfter=6,
            hAlign="LEFT",
            lineCap="round",
        ),
    ]
    if subtitle:
        header_parts.append(Paragraph(_escape(subtitle), styles["section_subtitle"]))

    if paragraphs:
        header_parts.append(Paragraph(_escape(paragraphs[0]), styles["body"]))
        rest = paragraphs[1:]
    else:
        rest = []

    flow.append(KeepTogether(header_parts))
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
        chart_image_url   — optional input for the chart wheel. Accepts:
                              * a local .svg path (rendered via svglib into a
                                ReportLab Drawing — the production code path,
                                fed by Kerykeion in app.py),
                              * a local PNG/JPEG/GIF path,
                              * a remote http(s) URL pointing to a raster image.
                            If empty or fails to load, the chart page falls
                            back to showing just the aspects table.
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
        PageTemplate(id="main", frames=[frame], onPage=_draw_page_chrome),
    ])

    story = []
    story.extend(_cover_flowables(client_name, birth_date, birth_place, styles))

    # Chart page — only add it if we have something to show
    if chart_image_url or aspects:
        story.extend(_chart_page_flowables(chart_image_url, aspects or [], points or {}, styles))

    # Section flow with periodic pull-quote breather pages. Every fourth
    # section (skipping Abertura and Fio Condutor which bookend the report)
    # gets a standalone quiet page featuring a real sentence from that
    # section's own text — words already in the report, never generated.
    parsed_points = points or {}
    sections = _parse_sections(report_text)
    _skip_breather_after = {"abertura", "fio condutor"}
    for i, (title, paragraphs) in enumerate(sections):
        story.extend(_section_flowables(title, paragraphs, styles, parsed_points))

        # Insert a breather page after this section? Every fourth non-terminal
        # section counting from Abertura, but never immediately before Fio
        # Condutor (the closing shouldn't be led into by a quote page).
        if i == len(sections) - 1:
            continue  # never after the last section
        title_key = title.split(":")[0].strip().lower()
        if title_key in _skip_breather_after:
            continue
        # Look at the NEXT section — if it's Fio Condutor, no breather
        next_title = sections[i + 1][0].split(":")[0].strip().lower()
        if next_title == "fio condutor":
            continue
        if (i + 1) % 4 == 0:  # after sections 4, 8, 12 (1-indexed effectively)
            quote = _select_pull_quote(paragraphs)
            if quote:
                story.extend(_pull_quote_flowables(quote, styles))

    doc.build(story)
    return buf.getvalue()
