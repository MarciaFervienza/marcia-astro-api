#!/usr/bin/env python3
"""
Flask API wrapping the natal report generator for Railway deployment.

Endpoints:
    GET  /health           — liveness probe (used by Railway healthcheck)
    POST /generate-report  — accepts chart JSON, returns the full report
"""

import base64
import logging
import os
import traceback

import requests
from flask import Flask, request, jsonify

import report_generator as rg
import pdf_generator as pg

# ============================================================
# CONFIG
# ============================================================
DEFAULT_PORT = int(os.environ.get("PORT", "8000"))

# Optional: cap how big a chart body we accept (defensive)
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(256 * 1024)))  # 256 KB

# Kerykeion chart-wheel configuration. The chart wheel is generated locally
# from Swiss Ephemeris — no external API call, no network dependency, no key
# rotation. The two lists below mirror Marcia's interpretive set exactly:
# the 10 classical planets + Chiron, Mean Lilith, Mean North Lunar Node,
# and the four major asteroids (Ceres, Pallas, Juno, Vesta). Aspects are
# limited to the 5 Ptolemaic ones — no quintile, no semi-aspects, no
# quincunx — matching the report's text-level filtering.
ACTIVE_POINTS = [
    "Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
    "Uranus", "Neptune", "Pluto",
    "Chiron", "Mean_Lilith", "Mean_North_Lunar_Node",
    "Ceres", "Pallas", "Juno", "Vesta",
    # Angular axes — needed so the Asc/MC marks appear on the wheel.
    # Without them the wheel still renders the house cusps correctly
    # (those come from the houses_system), but the labelled Asc and MC
    # arrowheads on the outer ring are absent.
    "Ascendant", "Medium_Coeli",
]
ACTIVE_ASPECTS = [
    {"name": "conjunction", "orb": 10},
    {"name": "opposition",  "orb": 10},
    {"name": "trine",       "orb":  8},
    {"name": "sextile",     "orb":  6},
    {"name": "square",      "orb":  5},
]
# Aspect-line color palette — passed to ChartDrawer's `aspects_settings`.
# Conjunction = neutral grey (it's a fusion, not a tension or harmony).
# Sextile = green and trine = blue — both harmonious aspects, visually
# distinct from each other. Square + opposition = brand red — both are
# tension aspects, and red ties the wheel back to the section titles in
# the rest of the PDF. The inactive aspects must remain in the list so
# Kerykeion's settings model is complete; they're never drawn because
# they're not in ACTIVE_ASPECTS above.
ASPECT_COLORS = [
    {"degree":   0, "name": "conjunction",    "is_major": True,  "color": "#9E9E9E"},
    {"degree":  60, "name": "sextile",        "is_major": True,  "color": "#2E7D32"},
    {"degree":  90, "name": "square",         "is_major": True,  "color": "#E03C31"},
    {"degree": 120, "name": "trine",          "is_major": True,  "color": "#1976D2"},
    {"degree": 180, "name": "opposition",     "is_major": True,  "color": "#E03C31"},
    {"degree":  30, "name": "semi-sextile",   "is_major": False, "color": "#999999"},
    {"degree":  45, "name": "semi-square",    "is_major": False, "color": "#999999"},
    {"degree":  72, "name": "quintile",       "is_major": False, "color": "#999999"},
    {"degree": 135, "name": "sesquiquadrate", "is_major": False, "color": "#999999"},
    {"degree": 144, "name": "biquintile",     "is_major": False, "color": "#999999"},
    {"degree": 150, "name": "quincunx",       "is_major": False, "color": "#999999"},
]
CHART_STYLE = os.environ.get("CHART_STYLE", "modern")  # 'modern' or 'classic'

# Resend (resend.com) for emailing the PDF to the client. Resend's API is
# HTTPS-based, which sidesteps Railway's outbound SMTP egress restrictions
# that blocked port 587 to Gmail. When RESEND_API_KEY + EMAIL_FROM_ADDRESS
# are both set on Railway and the request body contains an `email` field,
# the PDF is mailed inline before the HTTP response returns. If either env
# var is missing or the request omits `email`, no send is attempted and
# the response is unaffected.
#
# IMPORTANT: EMAIL_FROM_ADDRESS must be on a domain that's verified in
# Resend's dashboard (DNS DKIM + SPF records). For initial testing without
# domain verification, set EMAIL_FROM_ADDRESS to "onboarding@resend.dev" —
# Resend allows that as a sender but ONLY delivers to addresses on the
# Resend account, not arbitrary recipients.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
EMAIL_FROM_ADDRESS = os.environ.get("EMAIL_FROM_ADDRESS", "").strip()
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Márcia Fervienza Astrologia")

# Shared-secret auth for /generate-report — set on Railway, also embedded
# in the Wix client's request header so only Wix (and anyone we hand the
# key to manually) can trigger report generation. Comparison is constant-
# time via hmac.compare_digest to avoid leaking the key one character at
# a time through response-time differences.
#
# Fail-closed semantics: if API_SECRET_KEY is unset on the server, every
# /generate-report request is rejected with 401 — better than silently
# allowing all traffic when the env var wasn't set. /health and /env-check
# remain unprotected so Railway's healthcheck and our own diagnostics keep
# working.
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "").strip()
EMAIL_SUBJECT = "Seu Relatório de Mapa Natal — Márcia Fervienza"
EMAIL_BODY_TEMPLATE = """Olá, {client_name}!

Seu Relatório de Mapa Natal está pronto e segue em anexo.

Este relatório foi elaborado a partir de anos de consultas reais e do meu \
framework psicológico integrado à Astrologia. Espero que ele traga clareza, \
reconhecimento e profundidade para a sua jornada de autoconhecimento.

Leia com calma, mais de uma vez se necessário. Cada seção foi escrita para você.

Com carinho,
Márcia Fervienza
marciafervienza.com
"""

# ============================================================
# APP
# ============================================================
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("natal-api")


def _missing_required_keys():
    """Return a list of required env vars that are missing or empty."""
    missing = []
    for k in ("PINECONE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(k):
            missing.append(k)
    return missing


def _generate_chart_svg(chart_data: dict) -> tuple:
    """
    Generate a natal-chart SVG locally via Kerykeion (Swiss Ephemeris).

    No external API call, no network dependency, no auth. The chart is
    computed and rendered in-process in ~1–2s. The SVG is written to a
    fresh tempdir (one per request) so concurrent requests don't collide.

    Configuration:
      - active_points = ACTIVE_POINTS (17 bodies — Marcia's interpretive set)
      - active_aspects = ACTIVE_ASPECTS (5 Ptolemaic only, matching the
        report's text-level filtering)
      - style = CHART_STYLE env var ('modern' or 'classic')
      - online=False — we don't query GeoNames; lat/lng/tz are authoritative

    Returns (svg_path, error_message). On success: (path_to_svg_file, None).
    On failure: (None, reason). Never raises — failure here just means the
    PDF renders without the chart wheel.
    """
    try:
        from kerykeion import AstrologicalSubjectFactory
        from kerykeion.chart_data_factory import ChartDataFactory
        from kerykeion.charts.chart_drawer import ChartDrawer
    except ImportError as e:
        return None, f"kerykeion not installed: {e}"

    dt_str = chart_data.get("datetime", "")
    lat = chart_data.get("latitude")
    lon = chart_data.get("longitude")
    tz = chart_data.get("timezone", "")
    name = chart_data.get("name", "Cliente") or "Cliente"
    # Optional birth_city — when provided, Kerykeion uses it as the location
    # label on the wheel. Without it, reverse-geocoding may return a wrong
    # nearby city (e.g. "Greenwich, GB" for Rio coordinates). When Wix sends
    # real client data this field will be populated from the form.
    city = (chart_data.get("birth_city") or "").strip() or None

    if not all([dt_str, lat is not None, lon is not None, tz]):
        return None, "Missing required fields: datetime, latitude, longitude, timezone"

    try:
        from datetime import datetime
        dt = datetime.fromisoformat(dt_str)
    except Exception as e:
        return None, f"could not parse datetime '{dt_str}': {e}"

    try:
        subject = AstrologicalSubjectFactory.from_birth_data(
            name,
            dt.year, dt.month, dt.day, dt.hour, dt.minute,
            lat=lat, lng=lon, tz_str=tz,
            city=city,
            online=False,
            active_points=ACTIVE_POINTS,
        )
    except Exception as e:
        return None, f"AstrologicalSubject build failed: {e}"

    try:
        kerykeion_chart_data = ChartDataFactory.create_natal_chart_data(
            subject,
            active_points=ACTIVE_POINTS,
            active_aspects=ACTIVE_ASPECTS,
        )
        # aspects_settings overrides Kerykeion's default CSS-variable colors
        # with Marcia's palette (sextile=green, trine=blue, square+opp=red,
        # conjunction=grey).
        chart = ChartDrawer(
            chart_data=kerykeion_chart_data,
            aspects_settings=ASPECT_COLORS,
        )
    except Exception as e:
        return None, f"chart data/drawer build failed: {e}"

    import tempfile
    out_dir = tempfile.mkdtemp(prefix="kerykeion_")
    filename = "natal_wheel"
    try:
        # Wheel-only output (no surrounding data panel or aspect grid — our
        # own aspects table renders below in pdf_generator).
        # remove_css_variables=True inlines actual color values instead of
        # emitting `var(--kerykeion-chart-color-sun)` etc. This is critical
        # because svglib 1.5.x doesn't resolve CSS custom properties — it
        # would silently fall back to default (black) for every glyph and
        # aspect line, destroying the colored aesthetic.
        chart.save_wheel_only_svg_file(
            output_path=out_dir,
            filename=filename,
            style=CHART_STYLE if CHART_STYLE in ("modern", "classic") else "modern",
            remove_css_variables=True,
        )
    except Exception as e:
        return None, f"save_wheel_only_svg_file failed: {e}"

    svg_path = os.path.join(out_dir, f"{filename}.svg")
    if not os.path.exists(svg_path):
        return None, f"SVG file not found at {svg_path}"

    # Post-process: strip aspect-icon overlays. Kerykeion's wheel-only mode
    # ignores its own `show_aspect_icons=False` flag and always overlays a
    # small symbol (△ for trine, □ for square, etc.) in the middle of each
    # aspect line via `<use xlink:href='#orbN' ... />` elements pointing to
    # symbol defs (#orb0/#orb60/#orb90/#orb120/#orb180). The colored line
    # alone is sufficient — drop the overlays. Scoped to #orbN only, never
    # touches planet or sign glyphs (those use names like #Sun, #Aries).
    import re
    try:
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_text = f.read()
        svg_text = re.sub(
            r"<use\b[^>]*\bxlink:href=['\"]#orb\d+['\"][^>]*/>",
            "",
            svg_text,
        )
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_text)
    except Exception:
        # Strip is cosmetic — if it fails, the SVG is still valid, just with
        # the aspect-icon overlays present. Don't fail the request for this.
        pass

    return svg_path, None


# Portuguese month names for formatting the PDF cover's display string from
# the structured birth_date (e.g. "1977-01-24" → "24 de janeiro de 1977").
# Index 0 unused so month numbers index directly.
_PT_MONTHS = (
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _parse_birth_inputs(birth_date_raw, birth_time_raw, unknown):
    """Validate and combine the request's birth_date (YYYY-MM-DD) +
    birth_time (HH:MM) + unknown_birth_time (bool) fields into:
      - the internal ISO datetime string the chart-wheel renderer reads
        from body["datetime"]
      - a Portuguese-formatted display string for the PDF cover
      - a time_estimated flag that's surfaced in the response meta

    When unknown_birth_time is true, birth_time is ignored entirely and
    the datetime is anchored at 00:00:00 — the chart still renders but
    house cusps are approximate; the time_estimated flag warns downstream.

    Returns a dict with one of two shapes:
      success: {"datetime": "1977-01-24T16:07:00",
                "display":  "24 de janeiro de 1977, 16:07",
                "time_estimated": False}
      error:   {"error": "<Portuguese message>", "code": 400}

    All error messages are in Portuguese so they can surface directly to
    the end-user in Wix's error UI without translation.
    """
    import re
    from datetime import datetime as _dt

    birth_date_str = (birth_date_raw or "").strip() if isinstance(birth_date_raw, str) else ""
    birth_time_str = (birth_time_raw or "").strip() if isinstance(birth_time_raw, str) else ""
    unknown = bool(unknown)

    if not birth_date_str:
        return {"error": "Campo 'birth_date' obrigatório no formato AAAA-MM-DD.", "code": 400}

    # Strict YYYY-MM-DD format check — strptime alone would accept e.g.
    # "1977-1-24" which we want to reject for predictability.
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", birth_date_str):
        return {
            "error": f"Data de nascimento inválida (esperado AAAA-MM-DD): {birth_date_str}",
            "code": 400,
        }
    try:
        parsed_date = _dt.strptime(birth_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {
            "error": f"Data de nascimento inválida: {birth_date_str} não é uma data real.",
            "code": 400,
        }

    if unknown:
        # Default to noon (12:00:00), NOT midnight. Local midnight can flip
        # to the previous calendar day when converted to UTC for the Swiss
        # Ephemeris lookup — silently shifting every planet to its previous-
        # day position for births in west-of-Greenwich zones. Noon puts the
        # UTC lookup safely mid-day everywhere on the planet and also
        # minimises the worst-case Moon-position error (Moon moves ~13°/day,
        # so noon is at most ~6.5° off from a true birth-time reading).
        time_iso = "12:00:00"
        time_estimated = True
        display_time = None
    else:
        if not birth_time_str:
            return {
                "error": "Campo 'birth_time' obrigatório no formato HH:MM "
                         "(use unknown_birth_time=true se o horário for desconhecido).",
                "code": 400,
            }
        if not re.match(r"^\d{2}:\d{2}$", birth_time_str):
            return {
                "error": f"Hora de nascimento inválida (esperado HH:MM): {birth_time_str}",
                "code": 400,
            }
        try:
            parsed_time = _dt.strptime(birth_time_str, "%H:%M").time()
        except ValueError:
            return {
                "error": f"Hora de nascimento inválida: {birth_time_str} não é um horário real.",
                "code": 400,
            }
        time_iso = f"{parsed_time.hour:02d}:{parsed_time.minute:02d}:00"
        time_estimated = False
        display_time = f"{parsed_time.hour:02d}:{parsed_time.minute:02d}"

    datetime_iso = f"{birth_date_str}T{time_iso}"
    display = f"{parsed_date.day} de {_PT_MONTHS[parsed_date.month]} de {parsed_date.year}"
    if display_time:
        display += f", {display_time}"

    return {"datetime": datetime_iso, "display": display, "time_estimated": time_estimated}


# =============================================================
# Geocoding — resolve birth_city → (lat, lng, IANA tz name)
# =============================================================
def _geocode_birth_city(city):
    """Resolve a free-form city string into (latitude, longitude, IANA
    timezone name) via Nominatim (geopy) + timezonefinder.

    Returns (lat, lng, tz_str, error). On success: (lat, lng, tz_str, None).
    On failure: (None, None, None, "<Portuguese error message>"). Never
    raises — errors are returned so the endpoint can surface them as a
    clean HTTP 400 with a message the Wix form can display.

    Note on timezones: we return the IANA zone NAME (e.g. "America/Sao_Paulo"),
    NOT a numeric offset. Kerykeion + Swiss Ephemeris apply the zone's
    historical DST and offset rules at the birth date internally — using a
    current-date numeric offset would silently produce wrong charts for
    anyone born during a DST rule change or historical offset shift.
    """
    city = (city or "").strip() if isinstance(city, str) else ""
    if not city:
        return None, None, None, "Campo 'birth_city' obrigatório."

    try:
        from geopy.geocoders import Nominatim
    except ImportError as e:
        return None, None, None, f"Erro de configuração do servidor: geopy não instalado ({e})."

    # Nominatim usage policy requires a distinctive User-Agent.
    geolocator = Nominatim(user_agent="marcia-astro-api/1.0", timeout=15)
    try:
        location = geolocator.geocode(city, language="pt", addressdetails=False)
    except Exception as e:
        return None, None, None, f"Erro ao consultar geolocalização: {e}"

    if location is None:
        return None, None, None, (
            f"Cidade de nascimento não encontrada: {city}. Verifique a grafia."
        )

    lat = float(location.latitude)
    lng = float(location.longitude)

    try:
        from timezonefinder import TimezoneFinder
    except ImportError as e:
        return None, None, None, f"Erro de configuração do servidor: timezonefinder não instalado ({e})."

    tf = TimezoneFinder()
    tz_str = tf.timezone_at(lat=lat, lng=lng)
    if not tz_str:
        return None, None, None, (
            f"Fuso horário não pôde ser determinado para as coordenadas de {city}."
        )

    return lat, lng, tz_str, None


# =============================================================
# Moon-note passages — injected into the report based on the
# moon_analysis outcome (branches A / B / C from the spec).
# Branch D (known time, clear of any cusp) leaves the report unchanged.
# =============================================================
_LUA_SECTION_TITLE = "Lua: Suas Raízes Emocionais"

_MOON_NOTE_BRANCH_A = (
    "Sua Lua: um ponto que merece atenção\n"
    "\n"
    "No dia do seu nascimento, a Lua mudou de signo. Até as "
    "{moon_ingress_local_time} (horário local), ela estava em "
    "{moon_sign_before}; a partir desse momento, passou para "
    "{moon_sign_after}. Como você não informou o horário exato de "
    "nascimento, não é possível determinar com certeza em qual desses "
    "dois signos a sua Lua se encontra.\n"
    "\n"
    "Essa distinção não é um detalhe menor. A Lua descreve a sua vida "
    "emocional, aquilo que lhe traz segurança, o modo como você se "
    "acolhe e busca conforto — e {moon_sign_before} e {moon_sign_after} "
    "falam disso de maneiras muito diferentes. Por isso, prefiro ser "
    "honesta com você a arriscar uma interpretação que pode não lhe "
    "pertencer.\n"
    "\n"
    "Se em algum momento você conseguir recuperar o seu horário de "
    "nascimento — em certidões, registros de maternidade ou com "
    "familiares —, será possível resolver essa questão com precisão e "
    "refinar todo o restante do mapa. Até lá, convido você a ler as "
    "descrições dos dois signos e perceber qual delas ressoa mais "
    "fielmente com a sua experiência interior. Muitas vezes, o próprio "
    "corpo reconhece a verdade que o relógio não registrou."
)

_MOON_NOTE_BRANCH_B = (
    "Uma nota sobre o horário\n"
    "\n"
    "Como você não informou o horário exato de nascimento, os pontos do "
    "mapa que dependem dele — o Ascendente e as casas — não puderam ser "
    "calculados. A posição dos planetas nos signos, no entanto, permanece "
    "confiável. Sua Lua, felizmente, esteve em {moon_sign} ao longo de "
    "todo o dia do seu nascimento, de modo que esse ponto tão importante "
    "da sua vida emocional pode ser lido com segurança."
)

_MOON_NOTE_BRANCH_C = (
    "Sua Lua está próxima de uma mudança de signo\n"
    "\n"
    "Segundo o horário que você informou, sua Lua está em {moon_sign} — "
    "mas por muito pouco. Apenas {minutes_from_cusp} minuto(s) separam o "
    "seu nascimento do momento em que a Lua passou para "
    "{moon_adjacent_sign}.\n"
    "\n"
    "Menciono isso porque horários de nascimento nem sempre são "
    "registrados com precisão absoluta: um relógio adiantado, um "
    "arredondamento na hora do parto, uma anotação feita de memória. Se "
    "houver qualquer margem de dúvida quanto ao seu horário exato, vale "
    "a pena considerar também a descrição de {moon_adjacent_sign} e "
    "perceber qual das duas ressoa mais fielmente com a sua vida "
    "emocional. Se o seu horário estiver correto, no entanto, {moon_sign} "
    "é a sua Lua — e é a partir dela que faço a leitura a seguir."
)


def _replace_lua_section_body(report_text, new_body):
    """Replace the full body of the `## Lua: Suas Raízes Emocionais`
    section with `new_body`, keeping the section title itself intact.
    Used for Branch A when the Moon sign is uncertain — the generated
    Moon reading is discarded because it was written assuming a specific
    sign that we can no longer stand behind.

    If the section title can't be found (report format changed), returns
    the input unchanged rather than corrupting the report.
    """
    import re
    marker = f"## {_LUA_SECTION_TITLE}"
    pattern = re.escape(marker) + r"\n\n(.*?)(?=\n## |\Z)"
    replaced, n = re.subn(
        pattern,
        lambda m: f"{marker}\n\n{new_body}\n",
        report_text,
        count=1,
        flags=re.DOTALL,
    )
    if n == 0:
        logger.warning("could not find %r in report; leaving unchanged", marker)
        return report_text
    return replaced


def _prepend_to_lua_section(report_text, note):
    """Prepend a note to the body of the `## Lua: Suas Raízes Emocionais`
    section, before the existing Moon reading. Used for Branches B and C.

    If the section marker can't be found, returns the input unchanged.
    """
    import re
    marker = f"## {_LUA_SECTION_TITLE}\n\n"
    replaced, n = re.subn(
        re.escape(marker),
        marker + note + "\n\n",
        report_text,
        count=1,
    )
    if n == 0:
        logger.warning("could not find Lua section marker; leaving unchanged")
        return report_text
    return replaced


def _apply_moon_note(report_text, moon_meta, time_estimated):
    """Choose one of Branches A / B / C / D based on moon_meta flags and
    apply the corresponding transformation. Returns the possibly-modified
    report_text. Branch D (known time, clear of cusp) leaves the report
    untouched. Any failure is swallowed with a warning — the report still
    ships, just without the Moon note."""
    try:
        if moon_meta.get("moon_sign_uncertain"):
            note = _MOON_NOTE_BRANCH_A.format(
                moon_ingress_local_time=moon_meta["moon_ingress_local_time"],
                moon_sign_before=moon_meta["moon_sign_before"],
                moon_sign_after=moon_meta["moon_sign_after"],
            )
            return _replace_lua_section_body(report_text, note)
        if time_estimated and not moon_meta.get("moon_sign_uncertain") \
                and moon_meta.get("moon_sign"):
            note = _MOON_NOTE_BRANCH_B.format(moon_sign=moon_meta["moon_sign"])
            return _prepend_to_lua_section(report_text, note)
        if moon_meta.get("moon_near_cusp"):
            note = _MOON_NOTE_BRANCH_C.format(
                moon_sign=moon_meta["moon_sign"],
                minutes_from_cusp=moon_meta["minutes_from_cusp"],
                moon_adjacent_sign=moon_meta["moon_adjacent_sign"],
            )
            return _prepend_to_lua_section(report_text, note)
    except Exception as e:
        logger.warning("Moon note injection failed: %s", e)
    return report_text


def _sanitize_for_filename(s: str) -> str:
    """Reduce an arbitrary client name to a filename-safe token. Drops
    accents/diacritics, replaces whitespace with underscores, and strips
    anything not alphanumeric/dash/underscore. Empty input → 'Cliente'."""
    import unicodedata, re
    if not s or not s.strip():
        return "Cliente"
    norm = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"\s+", "_", norm.strip())
    norm = re.sub(r"[^A-Za-z0-9_\-]", "", norm)
    return norm or "Cliente"


def send_report_email(to_email: str, client_name: str, pdf_bytes: bytes,
                      birth_date: str = "", birth_place: str = ""):
    """Email the natal-report PDF to the client via Resend's HTTPS API.

    Args:
        to_email     — recipient address (validated upstream by the caller)
        client_name  — used in the Portuguese greeting and the attachment
                       filename
        pdf_bytes    — raw PDF bytes to attach (base64-encoded for the JSON
                       payload inside this function)
        birth_date   — currently unused; kept in signature for future use
        birth_place  — same

    Returns True on successful send, or a short error string on failure.
    Never raises — failure is signalled via the return value so the caller
    can put the message in the response meta.
    """
    if not RESEND_API_KEY:
        return "Resend API key not configured (RESEND_API_KEY)"
    if not EMAIL_FROM_ADDRESS:
        return "Sender address not configured (EMAIL_FROM_ADDRESS)"
    if not to_email or "@" not in to_email:
        return f"invalid recipient: {to_email!r}"
    if not pdf_bytes:
        return "no PDF bytes to attach"

    # Format From header per RFC 5322: "Display Name <addr@domain>".
    # Resend accepts both bare addresses and the display-name form.
    from_value = (
        f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
        if EMAIL_FROM_NAME else EMAIL_FROM_ADDRESS
    )

    filename = f"Mapa_Natal_{_sanitize_for_filename(client_name)}.pdf"

    payload = {
        "from": from_value,
        "to": [to_email],
        "subject": EMAIL_SUBJECT,
        "text": EMAIL_BODY_TEMPLATE.format(client_name=client_name or "Cliente"),
        "attachments": [
            {
                "filename": filename,
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            },
        ],
    }

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return "Resend API timed out after 30s"
    except requests.exceptions.RequestException as e:
        return f"network error reaching Resend API: {e}"
    except Exception as e:
        return f"unexpected error calling Resend: {e}"

    if resp.status_code in (200, 201, 202):
        return True

    # Try to surface Resend's error message body if it returned JSON
    try:
        err = resp.json()
        msg = err.get("message") or err.get("error") or str(err)
        name = err.get("name", "")
        return f"Resend API HTTP {resp.status_code}{f' ({name})' if name else ''}: {msg}"
    except Exception:
        return f"Resend API HTTP {resp.status_code}: {(resp.text or '')[:200]}"


@app.route("/health", methods=["GET"])
def health():
    """Lightweight liveness check for Railway."""
    return jsonify({"status": "ok"}), 200


@app.route("/env-check", methods=["GET"])
def env_check():
    """Diagnostic: report whether email-related env vars are visible to the
    running process. Returns booleans + lengths only for the secret — never
    the API key itself — so this is safe to leave exposed. The
    EMAIL_FROM_ADDRESS and EMAIL_FROM_NAME values are shown in full because
    they're not secrets (they're inside every outbound email)."""
    return jsonify({
        "RESEND_API_KEY_set": bool(os.environ.get("RESEND_API_KEY")),
        "RESEND_API_KEY_length": len(os.environ.get("RESEND_API_KEY", "")),
        "EMAIL_FROM_ADDRESS": os.environ.get("EMAIL_FROM_ADDRESS", "(unset)"),
        "EMAIL_FROM_NAME": os.environ.get("EMAIL_FROM_NAME", "(default)"),
        "API_SECRET_KEY_set": bool(os.environ.get("API_SECRET_KEY")),
        "API_SECRET_KEY_length": len(os.environ.get("API_SECRET_KEY", "")),
    }), 200


@app.route("/generate-report", methods=["POST"])
def generate_report_endpoint():
    """Accept chart JSON, generate the report, return as JSON.

    Expected body: { "name": "...", "gender": "feminino" | "masculino",
                     "points": {...}, "ascendant": {...}, "midheaven": {...},
                     "aspects": [...] }
    Optional control fields (top-level, alongside chart):
        "sections_only": ["abertura", "lua", ...]
        "limit":         int
        "no_fio":        bool
    """
    # Shared-secret auth. Fail-closed: if API_SECRET_KEY isn't set on the
    # server, every request is rejected. Constant-time compare on the
    # header to avoid timing-side-channel leaks of the key.
    import hmac
    presented_key = request.headers.get("X-API-Key", "")
    if not API_SECRET_KEY or not presented_key \
            or not hmac.compare_digest(presented_key, API_SECRET_KEY):
        return jsonify({
            "status": "error",
            "message": "Unauthorized",
        }), 401

    missing = _missing_required_keys()
    if missing:
        return jsonify({
            "status": "error",
            "message": f"Server misconfigured — missing env vars: {missing}",
        }), 500

    try:
        body = request.get_json(silent=True)
    except Exception:
        body = None
    if not isinstance(body, dict):
        return jsonify({
            "status": "error",
            "message": "Request body must be valid JSON object with chart data.",
        }), 400

    # Pull out optional control fields without polluting the chart
    sections_only = body.pop("sections_only", None)
    limit = body.pop("limit", None)
    no_fio = bool(body.pop("no_fio", False))

    # New structured birth-data contract: birth_date (YYYY-MM-DD) +
    # birth_time (HH:MM) + unknown_birth_time (bool). The parser
    # validates each field, returns Portuguese error messages on
    # invalid input, and combines into:
    #   - body["datetime"]: ISO string the chart-wheel renderer reads
    #   - birth_date_display: Portuguese string for the PDF cover
    #   - time_estimated: surfaced in the response meta
    birth_date_raw = body.pop("birth_date", None)
    birth_time_raw = body.pop("birth_time", None)
    unknown_birth_time = body.pop("unknown_birth_time", False)
    birth_place = (body.pop("birth_place", "") or "").strip()

    parsed_birth = _parse_birth_inputs(birth_date_raw, birth_time_raw, unknown_birth_time)
    if "error" in parsed_birth:
        return jsonify({
            "status": "error",
            "message": parsed_birth["error"],
        }), parsed_birth["code"]

    body["datetime"] = parsed_birth["datetime"]
    birth_date_display = parsed_birth["display"]
    time_estimated = parsed_birth["time_estimated"]

    # Geocode birth_city → (lat, lng, IANA tz name). Always geocoded fresh
    # from the city string; any latitude/longitude/timezone the caller may
    # still be sending in the body is ignored so we have a single source of
    # truth. Historical-DST correctness is guaranteed by passing the zone
    # NAME to Kerykeion, which resolves the offset at the birth date.
    birth_city = body.get("birth_city")
    lat, lng, tz_str, geo_error = _geocode_birth_city(birth_city)
    if geo_error:
        return jsonify({"status": "error", "message": geo_error}), 400

    body["latitude"] = lat
    body["longitude"] = lng
    body["timezone"] = tz_str

    # Moon-sign analysis — different function depending on whether the birth
    # time was given. Failures fall through with an error-marker in the dict
    # instead of blocking the request; the report just misses the Moon note.
    from datetime import datetime as _dt
    _dt_obj = _dt.fromisoformat(body["datetime"])
    moon_meta = {}
    try:
        import moon_analysis as ma
        if unknown_birth_time:
            moon_meta = ma.detect_moon_ingress(
                _dt_obj.year, _dt_obj.month, _dt_obj.day, lat, lng, tz_str,
            )
        else:
            moon_meta = ma.check_moon_cusp(
                _dt_obj.year, _dt_obj.month, _dt_obj.day,
                _dt_obj.hour, _dt_obj.minute, lat, lng, tz_str,
            )
    except Exception as e:
        logger.warning("moon analysis failed: %s", e)
        moon_meta = {"moon_analysis_error": str(e)}

    # Validate required fields up front (clearer 400 than a deep stack later)
    for required in ("gender", "points", "ascendant", "aspects"):
        if required not in body:
            return jsonify({
                "status": "error",
                "message": f"Chart JSON missing required field: '{required}'",
            }), 400

    try:
        result = rg.generate_report(
            body,
            sections_only=sections_only,
            limit=limit,
            no_fio=no_fio,
            write_file=False,
            verbose=False,
        )
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("generate_report failed")
        return jsonify({
            "status": "error",
            "message": f"Generation failed: {e}",
            "trace": traceback.format_exc() if app.debug else None,
        }), 500

    # Inject the appropriate Moon note into the report based on the ingress
    # / cusp analysis. Runs against the returned report text before it goes
    # into the PDF, so both the PDF and the response's report field carry
    # the note. Never raises — falls back to the original text on error.
    result["report"] = _apply_moon_note(result["report"], moon_meta, time_estimated)

    # Generate the chart-wheel SVG locally via Kerykeion (best-effort). The
    # result is a path to an SVG file in a fresh per-request tempdir.
    # pdf_generator's _fetch_chart_image() handles .svg paths via svglib.
    # We rmtree the tempdir after the PDF is built regardless of outcome.
    chart_svg_path, chart_error = _generate_chart_svg(body)

    # Render the branded PDF. Failures here should NOT poison the response —
    # the markdown report still has full value on its own.
    pdf_b64 = None
    pdf_bytes = None  # kept around for the email path so we don't round-trip via base64
    pdf_error = None
    try:
        pdf_bytes = pg.generate_pdf(
            report_text=result["report"],
            client_name=result["name"],
            birth_date=birth_date_display,
            birth_place=birth_place,
            chart_image_url=chart_svg_path,
            aspects=body.get("aspects", []),
            points=body.get("points", {}),
        )
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    except Exception as e:
        logger.exception("generate_pdf failed")
        pdf_error = str(e)
    finally:
        # Clean up the per-request Kerykeion tempdir so we don't leak under /tmp.
        if chart_svg_path:
            tmp_dir = os.path.dirname(chart_svg_path)
            if tmp_dir and os.path.basename(tmp_dir).startswith("kerykeion_") and os.path.isdir(tmp_dir):
                import shutil
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

    # Make the new birth-data structured fields available downstream:
    # the response meta needs to include time_estimated so callers (Wix)
    # can flag charts where the time defaulted to midnight as approximate.
    # Email the PDF synchronously before returning the response. Adds ~2-3s
    # (Gmail SMTP handshake + send) to the total response time, well within
    # Railway's edge timeout. The earlier background-thread implementation
    # caused worker crashes under gunicorn --preload + --threads, likely a
    # fork/SSL state interaction; inline send is simpler and rock-solid,
    # and lets meta.email_sent reflect actual SMTP outcome (true = Gmail
    # accepted) rather than just "dispatched".
    email_sent = False
    email_error = None
    recipient = (body.get("email") or "").strip()
    if recipient:
        if pdf_bytes is None:  # pdf generation failed → nothing to attach
            email_error = "pdf generation failed; nothing to email"
        elif not RESEND_API_KEY:
            email_error = "Resend API key not configured on server"
        elif not EMAIL_FROM_ADDRESS:
            email_error = "Sender address not configured on server (EMAIL_FROM_ADDRESS)"
        elif "@" not in recipient:
            email_error = f"invalid recipient email: {recipient!r}"
        else:
            try:
                send_result = send_report_email(
                    to_email=recipient,
                    client_name=result["name"],
                    pdf_bytes=pdf_bytes,
                    birth_date=birth_date_display,
                    birth_place=birth_place,
                )
            except Exception as e:
                # send_report_email is built to never raise, but belt-and-
                # suspenders so a bug here can't 500 the whole report.
                logger.exception("send_report_email raised unexpectedly")
                send_result = f"unexpected error: {e}"
            if send_result is True:
                email_sent = True
                logger.info("email sent to %s", recipient)
            else:
                email_error = send_result
                logger.warning("email to %s failed: %s", recipient, send_result)

    return jsonify({
        "status": "success",
        "report": result["report"],
        "pdf_base64": pdf_b64,
        "meta": {
            "name": result["name"],
            "gender": result["gender"],
            "sections": result["sections"],
            "elapsed_seconds": round(result["elapsed_seconds"], 1),
            "aspect_audit": result["aspect_audit"],
            "cleanup_changes": [
                {k: v for k, v in c.items() if k != "trace"}
                for c in result["cleanup_changes"]
            ],
            "pdf_bytes": len(pdf_b64) * 3 // 4 if pdf_b64 else 0,
            "pdf_error": pdf_error,
            "chart_svg_generated": bool(chart_svg_path),
            "chart_svg_error": chart_error or None,
            "chart_style": CHART_STYLE,
            "time_estimated": time_estimated,
            # Geocoded location (lat/lng + resolved IANA zone name) so the
            # caller can verify the geocode landed where they expect.
            "birth_city": birth_city,
            "latitude": lat,
            "longitude": lng,
            "timezone": tz_str,
            # Moon-analysis dict spread here so its keys sit at meta level
            # (moon_sign_uncertain / moon_sign_before / moon_sign_after /
            # moon_ingress_local_time OR moon_sign / moon_sign_abbr, or
            # moon_near_cusp / moon_adjacent_sign / minutes_from_cusp).
            **moon_meta,
            "email_sent": email_sent,
            "email_error": email_error,
        },
    }), 200


@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "service": "natal-report-generator",
        "endpoints": ["GET /health", "POST /generate-report"],
    }), 200


if __name__ == "__main__":
    # Local-only runner. Railway uses gunicorn via Procfile / startCommand.
    app.run(host="0.0.0.0", port=DEFAULT_PORT, debug=False)
