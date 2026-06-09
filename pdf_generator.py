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
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib.colors import HexColor
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
)

# ============================================================
# DESIGN TOKENS
# ============================================================
COLOR_RED = HexColor("#E03C31")
COLOR_TEAL = HexColor("#239C93")
COLOR_BODY = HexColor("#1A1A1A")
COLOR_GREY = HexColor("#666666")
COLOR_FOOTER = HexColor("#999999")

PAGE_W, PAGE_H = A4
SIDE_MARGIN = 2.2 * cm
TOP_MARGIN = 2.2 * cm
BOTTOM_MARGIN = 2.0 * cm  # leaves room for footer

LOGO_PATH = Path(__file__).parent / "logo.png"


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
) -> bytes:
    """
    Render the natal report into a branded PDF.

    Args:
        report_text  — full markdown report from generate_report().
        client_name  — name to show on the cover.
        birth_date   — birth date string (free-form, shown on cover).
        birth_place  — birth place string (free-form, shown on cover).

    Returns:
        PDF document as bytes.
    """
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

    for title, paragraphs in _parse_sections(report_text):
        story.extend(_section_flowables(title, paragraphs, styles))

    doc.build(story)
    return buf.getvalue()
