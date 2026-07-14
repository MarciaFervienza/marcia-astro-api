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

# SendGrid Web API (HTTPS) for emailing the PDF to the client. Railway
# blocks outbound SMTP submission ports (both 587 and 465 time out at the
# TCP layer, confirmed on this project), so any smtplib path — Gmail,
# Google Workspace, or otherwise — is dead in the water here. SendGrid's
# Web API delivers over HTTPS to api.sendgrid.com, which Railway allows
# freely. The message payload is the same shape as Gmail SMTP would be:
# the From header still reads "EMAIL_FROM_NAME <EMAIL_FROM_ADDRESS>", the
# PDF attaches as application/pdf, and reply_to routes replies to the
# executive inbox.
#
# EMAIL_FROM_ADDRESS must be on a domain whose sender authentication is
# verified in SendGrid's dashboard (SPF + DKIM DNS records). Otherwise
# SendGrid returns 403 with a "from address does not match a verified
# Sender Identity" error, which we surface in email_error.
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "").strip()
EMAIL_FROM_ADDRESS = os.environ.get("EMAIL_FROM_ADDRESS", "").strip()
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO", "").strip()
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
EMAIL_SUBJECT = "Seu Mapa Natal chegou."
EMAIL_BODY_TEMPLATE = """Olá, {client_name},

Que alegria te entregar isso. Seu Mapa Natal está pronto — ele segue em anexo.

Cada seção foi escrita pensando em você. O que está nesse relatório vem \
de anos de consultas reais e do trabalho que eu venho desenvolvendo há \
mais de duas décadas, integrando Astrologia e Psicologia profunda. \
Não é um texto para ser lido com pressa: leia com calma, volte às partes \
que te tocarem mais de uma vez, deixe as coisas assentarem.

Espero que traga clareza, reconhecimento e alguma companhia na sua \
jornada de se conhecer melhor.

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


# ============================================================
# RATE LIMIT — janela deslizante de 24h por e-mail e por IP.
# Estado em memória (dict → deque de timestamps). Aceitável nesta fase:
# Railway roda instância única e resetar em redeploy não é problema —
# quem estourou o limite pode esperar o próximo dia.
# ============================================================
from collections import deque as _deque
from threading import Lock as _Lock
import time as _time

_RATE_WINDOW_SECS = 24 * 3600
_RATE_MAX_PER_EMAIL = 2
_RATE_MAX_PER_IP = 4
_RATE_EXEMPT_EMAILS = {
    "marcia.fervienza@gmail.com",
    "executivo@marciafervienza.com",
}
_rate_email_hits = {}   # email_normalized → deque[timestamp]
_rate_ip_hits    = {}   # ip → deque[timestamp]
_rate_lock = _Lock()


def _rate_check(email_norm, ip):
    """Consulta e REGISTRA um hit para o par (email, ip). Retorna None se
    permitido, ou uma string com o motivo do bloqueio se estourou o limite.
    Emails de teste em _RATE_EXEMPT_EMAILS pulam tanto a contagem por
    e-mail quanto a por IP (para não bloquear nossos testes durante o dia).
    """
    if email_norm and email_norm in _RATE_EXEMPT_EMAILS:
        return None
    now = _time.time()
    cutoff = now - _RATE_WINDOW_SECS
    with _rate_lock:
        # Prune + count e-mail
        if email_norm:
            dq_e = _rate_email_hits.setdefault(email_norm, _deque())
            while dq_e and dq_e[0] < cutoff:
                dq_e.popleft()
            if len(dq_e) >= _RATE_MAX_PER_EMAIL:
                return f"email:{email_norm} atingiu {_RATE_MAX_PER_EMAIL} em 24h ({len(dq_e)} hits registrados)"
        # Prune + count IP
        if ip and ip != "?":
            dq_i = _rate_ip_hits.setdefault(ip, _deque())
            while dq_i and dq_i[0] < cutoff:
                dq_i.popleft()
            if len(dq_i) >= _RATE_MAX_PER_IP:
                return f"ip:{ip} atingiu {_RATE_MAX_PER_IP} em 24h ({len(dq_i)} hits registrados)"
        # Passou: registra o hit em ambos
        if email_norm:
            _rate_email_hits[email_norm].append(now)
        if ip and ip != "?":
            _rate_ip_hits[ip].append(now)
    return None


_RATE_LIMIT_MESSAGE_PT = (
    "Limite de relatórios atingido. Escreva para "
    "executivo@marciafervienza.com se precisar de ajuda."
)


# ============================================================
# FAILURE ALERT — quando o pipeline de geração levanta exceção
# não tratada, mandamos um e-mail para executivo@marciafervienza.com
# com o contexto suficiente pra diagnosticar. Dedupe por assinatura
# (tipo + linha final da stack) em janela de 10 min evita rajada.
# Fire-and-forget: falha no envio do alerta é só logada.
# ============================================================
_ALERT_RECIPIENT = "executivo@marciafervienza.com"
_ALERT_DEDUPE_WINDOW_SECS = 10 * 60
_alert_last_sent = {}     # signature → timestamp
_alert_dedupe_lock = _Lock()


def _send_failure_alert(stage, exc, request_ctx):
    """Envia alerta de falha para _ALERT_RECIPIENT via SendGrid HTTPS.
    stage: string ("generate_report" | "generate_pdf" | outro).
    exc: a exceção capturada.
    request_ctx: dict com {name,email,birth_date,birth_city,ip,ua}.
    Nunca levanta. Deduplica por assinatura em janela curta."""
    try:
        import traceback as _tb
        tb_str = _tb.format_exc()
        # Assinatura pra dedupe: tipo + última linha significativa
        _tb_lines = [ln for ln in tb_str.strip().splitlines() if ln.strip()]
        _sig = f"{stage}:{type(exc).__name__}:{_tb_lines[-1][:200] if _tb_lines else ''}"
        now = _time.time()
        with _alert_dedupe_lock:
            last = _alert_last_sent.get(_sig, 0)
            if now - last < _ALERT_DEDUPE_WINDOW_SECS:
                logger.info("failure alert deduped (sig sent %ds ago)", int(now - last))
                return
            _alert_last_sent[_sig] = now
        if not SENDGRID_API_KEY or not EMAIL_FROM_ADDRESS:
            logger.warning("failure alert not sent: SendGrid/from not configured")
            return
        from datetime import datetime as _dtu
        _when = _dtu.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        # Últimas ~30 linhas de traceback (suficiente pra diagnóstico, não estoura)
        _tb_tail = "\n".join(tb_str.strip().splitlines()[-30:])
        _text = (
            f"Falha no pipeline /generate-report — estágio: {stage}\n"
            f"Timestamp: {_when}\n"
            f"Exceção: {type(exc).__name__}: {exc}\n\n"
            f"--- Contexto da requisição ---\n"
            f"name:       {request_ctx.get('name','?')}\n"
            f"email:      {request_ctx.get('email','?')}\n"
            f"birth_date: {request_ctx.get('birth_date','?')}\n"
            f"birth_city: {request_ctx.get('birth_city','?')}\n"
            f"ip:         {request_ctx.get('ip','?')}\n"
            f"ua:         {request_ctx.get('ua','?')}\n\n"
            f"--- Traceback (últimas 30 linhas) ---\n{_tb_tail}\n"
        )
        payload = {
            "personalizations": [{"to": [{"email": _ALERT_RECIPIENT}]}],
            "from": {"email": EMAIL_FROM_ADDRESS, "name": EMAIL_FROM_NAME or EMAIL_FROM_ADDRESS},
            "subject": f"[Mapa Natal API] Falha em {stage} — {type(exc).__name__}",
            "content": [{"type": "text/plain", "value": _text}],
        }
        try:
            resp = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={
                    "Authorization": f"Bearer {SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if 200 <= resp.status_code < 300:
                logger.info("failure alert sent to %s (stage=%s)", _ALERT_RECIPIENT, stage)
            else:
                logger.warning(
                    "failure alert send failed: HTTP %d %s",
                    resp.status_code, (resp.text or "")[:200],
                )
        except Exception as _send_err:
            logger.warning("failure alert send raised: %s", _send_err)
    except Exception as _alert_err:
        # Nunca deixar o alerta afetar o path principal
        logger.warning("_send_failure_alert internal error: %s", _alert_err)


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
        display += f", às {display_time}"
    # Extra: quando a hora é desconhecida, retornamos uma nota para o rodapé
    # da capa deixando explícito que o mapa foi calculado ao meio-dia default.
    # A capa renderiza essa linha separadamente quando não vazia.
    unknown_time_note = (
        "Horário desconhecido — mapa calculado para meio-dia (12:00)"
        if time_estimated else ""
    )

    return {
        "datetime": datetime_iso,
        "display": display,
        "time_estimated": time_estimated,
        "unknown_time_note": unknown_time_note,
    }


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

# Branch A only: appendix stitched onto the end of the invitation paragraph.
# The two blurbs are generated dynamically from the Pinecone RAG library for
# whichever adjacent-sign pair the ingress falls between, condensed by Claude
# into 2-4 sentences each. If blurb generation fails for any reason, the
# appendix is simply skipped and Branch A ships as before — the invitation
# still stands, just without the concrete descriptions.
_MOON_BLURB_APPENDIX = (
    "\n\n"
    "Se a sua Lua estiver em {moon_sign_before}: {moon_blurb_before}\n"
    "\n"
    "Se a sua Lua estiver em {moon_sign_after}: {moon_blurb_after}"
)


_MOON_BLURB_PROMPT = """Você é Márcia Fervienza escrevendo para uma cliente cujo horário exato de nascimento é desconhecido. A Lua mudou de signo no dia do nascimento dela, então ela pode ter nascido com Lua em {sign_before} ou com Lua em {sign_after}. Você precisa descrever brevemente cada uma dessas duas possibilidades para que ela possa se reconhecer.

Sua tarefa: escrever DUAS descrições breves (2 a 4 frases cada, no máximo 4) da vida emocional interior de cada possibilidade. NÃO escreva do zero. Condense os trechos autorais abaixo, mantendo sua voz.

Foco EXCLUSIVO: o que traz segurança emocional, como essa Lua se acolhe, do que ela precisa emocionalmente. NADA sobre mãe, infância, aspectos, casas ou outros planetas — só o estado emocional interno da própria Lua no signo. Escreva em segunda pessoa (você).

Cada descrição precisa ser específica o bastante para que uma leitora possa dizer "sim, é isso" ou "não, não é isso". Evite generalidades. Contraste implicitamente com o outro signo — as duas descrições precisam soar diferentes.

{style_rules}

IMPORTANTE — este relatório JÁ USOU a construção "Não é X, é Y" em outra seção. Ela NÃO pode aparecer aqui. Diga a mesma coisa afirmativamente, sem o antônimo. Se estiver tentado a escrever "Não é frescura, é o que sustenta", escreva "O que genuinamente te sustenta é isso mesmo". Rejeite todo escafolde antitético — "não é frieza, é X" / "não é distância, é X" / qualquer variação. Também rejeite o gancho "Aqui não há Y" seguido de afirmação como forma disfarçada do mesmo padrão.

Trechos autorais para Lua em {sign_before}:
{chunks_before}

Trechos autorais para Lua em {sign_after}:
{chunks_after}

Formato obrigatório da resposta (respeite exatamente estas etiquetas — o parser depende delas):

BLURB_ANTES:
<2 a 4 frases sobre a Lua em {sign_before}, foco emocional interno>

BLURB_DEPOIS:
<2 a 4 frases sobre a Lua em {sign_after}, foco emocional interno>
"""


def _generate_moon_sign_blurbs(sign_before_pt, sign_after_pt):
    """Retrieve Marcia's authored natal-Moon material for each of the two
    adjacent-sign candidates from Pinecone (same retrieval helpers the
    report generator already uses), then have Claude condense each into a
    short emotional-life description in Marcia's voice.

    Both blurbs are produced in a single Claude call so the model can
    contrast the two signs against each other. Cost: ~4-6 Pinecone
    queries + 1 Claude call; adds roughly 5-10s to the request.

    Returns (blurb_before, blurb_after). Raises on any failure — the
    caller is expected to catch and fall back to the invitation-only
    version of Branch A.
    """
    from report_generator import (
        retrieve_chunks, format_chunks_for_prompt, call_claude,
        SECTION_STYLE_RULES,
    )

    def _fetch_for_sign(sign_pt):
        # Same query pattern as the Lua section itself (report_generator.py
        # lines 737-744), minus the house/aspects tail — for Branch A we
        # don't know the house, and aspects belong to the fixed material.
        queries = [
            f"Lua em {sign_pt} vida emocional segurança",
            f"Lua em {sign_pt} como se acolhe conforto",
            f"Lua em {sign_pt} necessidades emocionais",
        ]
        by_id = {}
        for q in queries:
            for m in retrieve_chunks(q, planets_filter=["Lua"]):
                if m.id not in by_id or m.score > by_id[m.id].score:
                    by_id[m.id] = m
        # Keep the top 8 chunks — enough context for Claude, not so much
        # that the prompt bloats and slows the call.
        chunks = sorted(by_id.values(), key=lambda x: x.score, reverse=True)[:8]
        return chunks

    chunks_before = _fetch_for_sign(sign_before_pt)
    chunks_after = _fetch_for_sign(sign_after_pt)
    if not chunks_before or not chunks_after:
        raise RuntimeError(
            f"insufficient chunks: before={len(chunks_before)} after={len(chunks_after)}"
        )

    prompt = _MOON_BLURB_PROMPT.format(
        sign_before=sign_before_pt,
        sign_after=sign_after_pt,
        chunks_before=format_chunks_for_prompt(chunks_before),
        chunks_after=format_chunks_for_prompt(chunks_after),
        style_rules=SECTION_STYLE_RULES,
    )
    text = call_claude(prompt, max_tokens=800)

    # Strict-label parsing. The prompt asks for BLURB_ANTES: / BLURB_DEPOIS:
    # so we split on those exact tokens; any prose before/after is dropped.
    import re
    m_before = re.search(
        r"BLURB_ANTES\s*:\s*(.+?)(?=\n\s*BLURB_DEPOIS\s*:|\Z)",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    m_after = re.search(
        r"BLURB_DEPOIS\s*:\s*(.+?)\Z",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    if not m_before or not m_after:
        raise RuntimeError(f"could not parse blurbs from Claude output: {text[:300]!r}")
    return m_before.group(1).strip(), m_after.group(1).strip()


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


def _append_to_lua_section(report_text, appendix):
    """Anexa `appendix` ao FINAL do corpo da seção `## Lua: Suas Raízes
    Emocionais` (isto é, imediatamente antes do próximo `## ` ou do fim do
    documento). Usado pelo Branch A para acoplar os blurbs dos dois signos
    depois da leitura por aspectos que o Claude já gerou.

    Se o marcador da seção não for encontrado, retorna o texto sem alteração
    para não corromper o relatório.
    """
    import re
    marker = f"## {_LUA_SECTION_TITLE}"
    # Localizar a seção e capturar seu corpo até o próximo ## ou fim.
    pattern = re.escape(marker) + r"\n\n(.*?)(?=\n## |\Z)"
    match = re.search(pattern, report_text, flags=re.DOTALL)
    if not match:
        logger.warning("could not find Lua section marker for append; leaving unchanged")
        return report_text
    body = match.group(1).rstrip()
    replaced = f"{marker}\n\n{body}\n{appendix}\n"
    return report_text[:match.start()] + replaced + report_text[match.end():]


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
    ships, just without the Moon note.

    Branch A additionally runs report_generator.cleanup_pass() over the
    entire modified report after the two Moon-sign blurbs are stitched
    in. Cleanup ran earlier inside rg.generate_report() over the pre-
    blurb text, so any "Não é X, é Y" occurrences the blurbs might have
    introduced would slip past the "1 per report" quota unless we
    re-scan. Running cleanup_pass again also normalizes English
    "retrograde" and flags leftover "a retrógrada" occurrences in the
    blurbs.
    """
    try:
        if moon_meta.get("moon_sign_uncertain"):
            # Branch A — hora desconhecida + Lua mudou de signo.
            #
            # O relatório vem do report_generator já com um placeholder
            # <<MOON_BLURBS>> DENTRO do disclaimer no topo. Aqui geramos os
            # dois blurbs de signo e substituímos o placeholder por eles.
            # Colocar as descrições dos dois signos no TOPO (dentro da nota
            # importante) — e não numa seção separada — é essencial porque:
            #  (i) elas contextualizam TODAS as seções seguintes com a
            #      leitura correta ("seu signo lunar pode ser X ou Y"), em vez
            #      de a informação aparecer só depois de Abertura/Triade/Sol
            #      terem sido lidas;
            #  (ii) evitam que a leitora leia a Abertura pressupondo um dos
            #      signos e depois descubra na Lua que era o outro.
            before = moon_meta["moon_sign_before"]
            after = moon_meta["moon_sign_after"]
            try:
                blurb_before, blurb_after = _generate_moon_sign_blurbs(before, after)
                blurbs_block = _MOON_BLURB_APPENDIX.format(
                    moon_sign_before=before,
                    moon_blurb_before=blurb_before,
                    moon_sign_after=after,
                    moon_blurb_after=blurb_after,
                ).strip()
            except Exception as e:
                logger.warning(
                    "Moon sign blurbs failed for %s / %s (%s); "
                    "shipping Branch A with placeholder stripped",
                    before, after, e,
                )
                blurbs_block = ""

            # Substituir <<MOON_BLURBS>> pelo bloco (ou remover o marcador
            # de vez se a geração falhou — a nota permanece coerente).
            if "<<MOON_BLURBS>>" in report_text:
                modified = report_text.replace("<<MOON_BLURBS>>", blurbs_block)
                # Se removemos totalmente o marcador vazio, também tirar as
                # linhas em branco extras que sobraram.
                if not blurbs_block:
                    import re as _re
                    modified = _re.sub(r"\n\n\n+", "\n\n", modified)
            else:
                # Fallback: se por algum motivo o placeholder não veio do
                # report_generator, cair no comportamento antigo (anexar à Lua).
                logger.warning("<<MOON_BLURBS>> placeholder missing; falling back to Lua-append")
                if blurbs_block:
                    modified = _append_to_lua_section(report_text, "\n\n" + blurbs_block)
                else:
                    modified = report_text

            # Safety-net cleanup — re-scan the full modified report so any
            # "Não é X, é Y" leaked into the blurbs is caught by the same
            # global-quota rule that ran earlier on the pre-blurb text.
            try:
                from report_generator import cleanup_pass
                modified, extra_changes = cleanup_pass(modified)
                if extra_changes:
                    moon_meta["blurb_cleanup_changes"] = [
                        {k: v for k, v in c.items() if k != "trace"}
                        for c in extra_changes
                    ]
                    logger.info(
                        "Branch A cleanup rewrote %d Claude tell(s) in blurbs",
                        len(extra_changes),
                    )
            except Exception as e:
                logger.warning(
                    "post-blurb cleanup_pass failed: %s (shipping as-is)", e
                )
            return modified
        # Branch B (hora desconhecida sem ingresso) removido — o disclaimer
        # gerado por report_generator no topo do relatório já cobre a
        # necessidade. Não fazemos nada aqui neste caso; seguimos.
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
    """Email the natal-report PDF to the client via SendGrid's Web API.

    HTTPS POST to https://api.sendgrid.com/v3/mail/send with a JSON body
    that carries the PDF as a base64-encoded attachment. On success
    SendGrid returns HTTP 202 Accepted (no body). On failure it returns
    4xx/5xx with a JSON `{"errors": [{"message": ..., "field": ...}]}`
    body that we forward into email_error verbatim so the failure mode
    is visible to the caller.

    From header: "EMAIL_FROM_NAME <EMAIL_FROM_ADDRESS>"
    Reply-To:    EMAIL_REPLY_TO (routes replies to the executive inbox)
    Subject:     EMAIL_SUBJECT (Portuguese, defined at module scope)
    Body:        EMAIL_BODY_TEMPLATE (Portuguese, greeting + sign-off)
    Attachment:  Mapa_Natal_<sanitized-name>.pdf, application/pdf

    Args:
        to_email     — recipient address (validated upstream by the caller)
        client_name  — used in the greeting and the attachment filename
        pdf_bytes    — raw PDF bytes; base64-encoded into the JSON payload
        birth_date   — currently unused; kept in signature for future use
        birth_place  — same

    Returns True on successful send, or a short error string on failure.
    Never raises — failure is signalled via the return value so the caller
    can put the message in the response meta.
    """
    if not SENDGRID_API_KEY:
        return "SendGrid API key not configured (SENDGRID_API_KEY)"
    if not EMAIL_FROM_ADDRESS:
        return "Sender address not configured (EMAIL_FROM_ADDRESS)"
    if not to_email or "@" not in to_email:
        return f"invalid recipient: {to_email!r}"
    if not pdf_bytes:
        return "no PDF bytes to attach"

    filename = f"Mapa_Natal_{_sanitize_for_filename(client_name)}.pdf"

    payload = {
        "personalizations": [
            {"to": [{"email": to_email}]},
        ],
        "from": {
            "email": EMAIL_FROM_ADDRESS,
            "name": EMAIL_FROM_NAME or EMAIL_FROM_ADDRESS,
        },
        "subject": EMAIL_SUBJECT,
        "content": [
            {
                "type": "text/plain",
                "value": EMAIL_BODY_TEMPLATE.format(client_name=client_name or "Cliente"),
            },
        ],
        "attachments": [
            {
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
                "type": "application/pdf",
                "filename": filename,
                "disposition": "attachment",
            },
        ],
    }
    # reply_to is optional — omit the key entirely if not configured,
    # rather than sending an empty-string address which SendGrid rejects.
    if EMAIL_REPLY_TO:
        payload["reply_to"] = {"email": EMAIL_REPLY_TO}

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return "SendGrid API timed out after 30s"
    except requests.exceptions.RequestException as e:
        return f"network error reaching SendGrid API: {e}"
    except Exception as e:
        return f"unexpected error calling SendGrid: {e}"

    # 202 Accepted is the success case; 200 OK is also treated as success
    # in case SendGrid ever changes semantics.
    if resp.status_code in (200, 202):
        return True

    # Surface SendGrid's structured error body — it usually contains the
    # exact reason (unverified sender, bad address format, expired API
    # key, etc.) which we want the caller to see in email_error.
    try:
        err_body = resp.json()
        if isinstance(err_body, dict) and err_body.get("errors"):
            msgs = "; ".join(
                (e.get("message") or str(e))
                + (f" (field: {e['field']})" if e.get("field") else "")
                for e in err_body["errors"]
            )
            return f"SendGrid HTTP {resp.status_code}: {msgs}"
        return f"SendGrid HTTP {resp.status_code}: {err_body}"
    except Exception:
        return f"SendGrid HTTP {resp.status_code}: {(resp.text or '')[:300]}"


@app.route("/health", methods=["GET"])
def health():
    """Lightweight liveness check for Railway."""
    return jsonify({"status": "ok"}), 200


@app.route("/env-check", methods=["GET"])
def env_check():
    """Diagnostic: report whether email-related env vars are visible to the
    running process. Returns booleans + lengths only for the secrets — never
    the values themselves — so this is safe to leave exposed. GMAIL_USER
    and EMAIL_FROM_NAME are returned in full because they're not secrets
    (they're stamped on every outbound message)."""
    return jsonify({
        "SENDGRID_API_KEY_set": bool(os.environ.get("SENDGRID_API_KEY")),
        "SENDGRID_API_KEY_length": len(os.environ.get("SENDGRID_API_KEY", "")),
        "EMAIL_FROM_ADDRESS": os.environ.get("EMAIL_FROM_ADDRESS", "(unset)"),
        "EMAIL_REPLY_TO": os.environ.get("EMAIL_REPLY_TO", "(unset)"),
        "EMAIL_FROM_NAME": os.environ.get("EMAIL_FROM_NAME", "(default)"),
        "API_SECRET_KEY_set": bool(os.environ.get("API_SECRET_KEY")),
        "API_SECRET_KEY_length": len(os.environ.get("API_SECRET_KEY", "")),
    }), 200


@app.route("/diag-retrieval", methods=["POST"])
def diag_retrieval_endpoint():
    """DEV-ONLY: exercita o retrieval RAW para uma lista de queries.
    Body: {"queries":[{"q":"...","planets_filter":["Júpiter"] or null}], "top_k":10, "sample_meta":true}
    Retorna: para cada query, top_k matches com id, score, metadata; e (opcional) 3 chunks de exemplo com metadata completa."""
    import hmac
    presented_key = request.headers.get("X-API-Key", "")
    _body = request.get_json(silent=True) or {}
    if isinstance(_body, dict) and set(_body.keys()) == {"data"} and isinstance(_body["data"], dict):
        _body = _body["data"]
    presented_key = presented_key or _body.pop("api_key", "")
    if not API_SECRET_KEY or not hmac.compare_digest(presented_key, API_SECRET_KEY):
        return jsonify({"error":"unauthorized"}),401
    import report_generator as rg
    rg.init_clients()
    top_k = int(_body.get("top_k", 10))
    queries = _body.get("queries", [])
    out = []
    for spec in queries:
        q = spec.get("q","")
        pf = spec.get("planets_filter") or None
        emb = rg._oai.embeddings.create(model=rg.EMBED_MODEL, input=q)
        qvec = emb.data[0].embedding
        matches = []
        # consultation
        cf = {"reading_type":{"$eq":"natal"}}
        if pf: cf["planets"] = {"$in": pf}
        try:
            r1 = rg._index.query(vector=qvec, top_k=top_k, filter=cf, include_metadata=True)
            for m in r1.matches:
                matches.append({"src":"consult","id":m.id,"score":round(m.score,3),"meta":m.metadata or {}})
        except Exception as e:
            matches.append({"error":f"consult query failed: {e}"})
        # class
        clf = {"content_type":{"$in":["class_lecture","class_foundations"]}}
        if pf: clf["planets"] = {"$in": pf}
        try:
            r2 = rg._index.query(vector=qvec, top_k=top_k, filter=clf, include_metadata=True)
            for m in r2.matches:
                matches.append({"src":"class","id":m.id,"score":round(m.score,3),"meta":m.metadata or {}})
        except Exception as e:
            matches.append({"error":f"class query failed: {e}"})
        matches.sort(key=lambda x: x.get("score",0), reverse=True)
        # For each match, extract identifying fields + text preview
        summarized = []
        for m in matches[:top_k]:
            if "error" in m:
                summarized.append(m); continue
            meta = m["meta"] or {}
            summarized.append({
                "src": m["src"], "id": m["id"], "score": m["score"],
                "planets": meta.get("planets"),
                "signs": meta.get("signs"),
                "houses": meta.get("houses"),
                "aspects": meta.get("aspects"),
                "reading_type": meta.get("reading_type"),
                "content_type": meta.get("content_type"),
                "youtube_id": meta.get("youtube_id"),
                "text_preview": (meta.get("text") or meta.get("chunk_text") or meta.get("content") or "")[:280],
                "meta_keys": sorted(list(meta.keys())),
            })
        out.append({"query": q, "planets_filter": pf, "results": summarized})
    # Sample metadata structure
    sample = None
    if _body.get("sample_meta"):
        try:
            r = rg._index.query(vector=[0.0]*1536, top_k=3, include_metadata=True)
            sample = [{"id": m.id, "meta_keys": sorted(list((m.metadata or {}).keys())), "meta": m.metadata} for m in r.matches]
        except Exception as e:
            sample = {"error": str(e)}
    return jsonify({"queries": out, "sample_meta": sample}), 200


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
    # server, every request is rejected. Constant-time compare via
    # hmac.compare_digest to avoid timing side-channel leaks.
    #
    # A chave pode chegar em duas posições, nessa ordem:
    #   1) header HTTP `X-API-Key` (padrão preferido, usado pelos testes)
    #   2) campo `api_key` no corpo JSON (fallback para clientes que não
    #      suportam headers customizados — ex.: Wix Automations)
    #
    # Se veio pelo body, é IMEDIATAMENTE removida via body.pop antes de
    # qualquer downstream, para não vazar em logs/eco de payload.
    import hmac
    body = request.get_json(silent=True) or {}
    # Wix Automations "Send HTTP" envelopa o corpo em {"data": {...}}. Se
    # detectarmos exatamente essa forma (uma única chave 'data' que é
    # dict), desembrulhamos in-place antes de tudo — assim o resto do
    # pipeline não precisa saber a origem.
    if isinstance(body, dict) and set(body.keys()) == {"data"} and isinstance(body["data"], dict):
        body = body["data"]
    key_from_body = body.pop("api_key", None) if isinstance(body, dict) else None
    presented_key = request.headers.get("X-API-Key") or key_from_body or ""
    if not API_SECRET_KEY or not presented_key \
            or not hmac.compare_digest(presented_key, API_SECRET_KEY):
        # Log de tentativa 401 — nunca inclui a chave, só metadados de
        # rastreamento pra distinguir "chave ausente" de "chave errada"
        # e ver de onde veio a chamada.
        _reason = (
            "no_key_sent" if not presented_key
            else "server_key_unset" if not API_SECRET_KEY
            else "key_mismatch"
        )
        _ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        _ua = (request.headers.get("User-Agent", "?") or "?")[:120]
        _key_len = len(presented_key)
        # Logar SÓ as chaves do body (nunca valores) pra descobrir se o
        # cliente está mandando "apiKey" / "api-key" / algo aninhado.
        _body_keys = list(body.keys())[:20] if isinstance(body, dict) else "not-json"
        logger.warning(
            "AUTH 401 reason=%s key_len=%d ip=%s ua=%s content_type=%s body_keys=%r",
            _reason, _key_len, _ip, _ua,
            request.headers.get("Content-Type", "?"),
            _body_keys,
        )
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

    # `body` já foi obtido na auth acima (com api_key removida via pop).
    if not isinstance(body, dict) or not body:
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
    unknown_time_note = parsed_birth.get("unknown_time_note", "")

    # AUDITORIA DE CHAMADA — registra origem e identidade da requisição para
    # rastrear payloads misteriosos (ex.: dois "Cliente Teste → executivo@"
    # em 2026-07-10). NUNCA loga a api_key (já foi extraída de body/header
    # e não aparece aqui). NUNCA loga o body inteiro (contém pontos do
    # mapa, potencialmente sensíveis). Só metadados de identidade + origem.
    _client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    _ua = (request.headers.get("User-Agent", "?") or "?")[:120]
    _key_via = "header" if request.headers.get("X-API-Key") else ("body" if key_from_body else "?")
    logger.info(
        "REQ /generate-report name=%r email=%r birth_date=%r city=%r ip=%s ua=%s key_via=%s",
        (body.get("name") or "")[:80],
        (body.get("email") or "")[:80],
        birth_date_raw[:20] if birth_date_raw else "",
        (body.get("birth_city") or "")[:80],
        _client_ip, _ua, _key_via,
    )

    # Rate-limit por e-mail e IP (janela deslizante de 24h). Aplicado ANTES
    # de geocoding/kerykeion/geração/e-mail — se estourou, nada de trabalho
    # nem de disparo. E-mails de teste (Marcia/executivo) são isentos.
    _email_norm = (body.get("email") or "").strip().lower()
    _rate_reason = _rate_check(_email_norm, _client_ip)
    if _rate_reason:
        logger.warning("RATE 429 %s ua=%s", _rate_reason, _ua)
        return jsonify({
            "status": "error",
            "message": _RATE_LIMIT_MESSAGE_PT,
        }), 429

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

    # Se o payload NÃO trouxer o mapa astral pré-calculado (points/ascendant/
    # midheaven/aspects) — caso típico de clientes que só sabem os dados de
    # nascimento crus, como a Wix Automation vinda do form — computamos aqui
    # server-side via Kerykeion + Swiss Ephemeris, usando as mesmas
    # coordenadas geocodificadas acima. Requer birth_date + birth_time (ou
    # unknown_birth_time=True) + birth_city + gender.
    _needs_chart = any(k not in body for k in ("points", "ascendant", "midheaven", "aspects"))
    if _needs_chart:
        try:
            from kerykeion import AstrologicalSubjectFactory, NatalAspects
            _ACTIVE = [
                "Sun","Moon","Mercury","Venus","Mars","Jupiter","Saturn",
                "Uranus","Neptune","Pluto","Chiron","Mean_Lilith",
                "Mean_North_Lunar_Node","Mean_South_Lunar_Node",
                "Ceres","Pallas","Juno","Vesta",
            ]
            _SIGN_EN = {"Ari":"aries","Tau":"taurus","Gem":"gemini","Can":"cancer","Leo":"leo","Vir":"virgo","Lib":"libra","Sco":"scorpio","Sag":"sagittarius","Cap":"capricorn","Aqu":"aquarius","Pis":"pisces"}
            _SIGN_PT = {"Ari":"Áries","Tau":"Touro","Gem":"Gêmeos","Can":"Câncer","Leo":"Leão","Vir":"Virgem","Lib":"Libra","Sco":"Escorpião","Sag":"Sagitário","Cap":"Capricórnio","Aqu":"Aquário","Pis":"Peixes"}
            _HN = {"First_House":1,"Second_House":2,"Third_House":3,"Fourth_House":4,"Fifth_House":5,"Sixth_House":6,"Seventh_House":7,"Eighth_House":8,"Ninth_House":9,"Tenth_House":10,"Eleventh_House":11,"Twelfth_House":12}
            _KER_TO_KEY = {"Sun":"sun","Moon":"moon","Mercury":"mercury","Venus":"venus","Mars":"mars","Jupiter":"jupiter","Saturn":"saturn","Uranus":"uranus","Neptune":"neptune","Pluto":"pluto","Chiron":"chiron","Mean_Lilith":"lilith","Mean_North_Lunar_Node":"north_node","Mean_South_Lunar_Node":"south_node","Ceres":"ceres","Vesta":"vesta","Juno":"juno","Pallas":"pallas"}
            _ASPECT_PT = {"conjunction":"conjunção","opposition":"oposição","trine":"trígono","square":"quadratura","sextile":"sextil"}

            _hour = _dt_obj.hour if not unknown_birth_time else 12
            _min  = _dt_obj.minute if not unknown_birth_time else 0
            _subj = AstrologicalSubjectFactory.from_birth_data(
                (body.get("name") or "Cliente"),
                _dt_obj.year, _dt_obj.month, _dt_obj.day, _hour, _min,
                lat=lat, lng=lng, tz_str=tz_str, online=False, active_points=_ACTIVE,
            )
            def _pl(p):
                return {
                    "sign": _SIGN_EN[p.sign], "sign_pt": _SIGN_PT[p.sign],
                    "house": _HN.get(p.house, 0),
                    "degrees": round(float(p.position), 1),
                    "retrograde": bool(getattr(p, "retrograde", False)),
                }
            body["points"] = {
                "sun":_pl(_subj.sun),"moon":_pl(_subj.moon),"mercury":_pl(_subj.mercury),
                "venus":_pl(_subj.venus),"mars":_pl(_subj.mars),"jupiter":_pl(_subj.jupiter),
                "saturn":_pl(_subj.saturn),"uranus":_pl(_subj.uranus),"neptune":_pl(_subj.neptune),
                "pluto":_pl(_subj.pluto),"chiron":_pl(_subj.chiron),
                "lilith":_pl(_subj.mean_lilith),
                "north_node":_pl(_subj.mean_north_lunar_node),
                "south_node":_pl(_subj.mean_south_lunar_node),
                "ceres":_pl(_subj.ceres),"vesta":_pl(_subj.vesta),
                "juno":_pl(_subj.juno),"pallas":_pl(_subj.pallas),
            }
            body["ascendant"] = {
                "sign": _SIGN_EN[_subj.first_house.sign],
                "sign_pt": _SIGN_PT[_subj.first_house.sign],
                "degrees": round(float(_subj.first_house.position), 1),
            }
            body["midheaven"] = {
                "sign": _SIGN_EN[_subj.tenth_house.sign],
                "sign_pt": _SIGN_PT[_subj.tenth_house.sign],
                "degrees": round(float(_subj.tenth_house.position), 1),
            }
            # Cúspides das 12 casas — necessárias para a camada Parte 4
            # (validação de afirmações "casa N em <signo>" / "<signo> na
            # cúspide da casa N"). Formato consistente com ascendant/mc.
            _HOUSES_ATTR = [
                ("1", _subj.first_house),  ("2", _subj.second_house),
                ("3", _subj.third_house),  ("4", _subj.fourth_house),
                ("5", _subj.fifth_house),  ("6", _subj.sixth_house),
                ("7", _subj.seventh_house),("8", _subj.eighth_house),
                ("9", _subj.ninth_house),  ("10", _subj.tenth_house),
                ("11", _subj.eleventh_house),("12", _subj.twelfth_house),
            ]
            body["cusps"] = {
                num: {
                    "sign":    _SIGN_EN[h.sign],
                    "sign_pt": _SIGN_PT[h.sign],
                    "degrees": round(float(h.position), 1),
                }
                for num, h in _HOUSES_ATTR
            }
            _asps = []
            for a in NatalAspects(_subj).relevant_aspects:
                if a.aspect not in _ASPECT_PT:
                    continue
                pa = _KER_TO_KEY.get(a.p1_name)
                pb = _KER_TO_KEY.get(a.p2_name)
                if not pa or not pb:
                    continue
                _mv = getattr(a, "aspect_movement", "") or ""
                _applying = True if _mv == "Applying" else (False if _mv == "Separating" else None)
                _asps.append({
                    "planet_a": pa, "planet_b": pb,
                    "type": a.aspect, "type_pt": _ASPECT_PT[a.aspect],
                    "orb": round(float(a.orbit), 2),
                    "applying": _applying,
                })
            body["aspects"] = _asps
            logger.info(
                "chart auto-computed: %d points, ASC=%s, MC=%s, %d aspects",
                len(body["points"]), body["ascendant"]["sign_pt"],
                body["midheaven"]["sign_pt"], len(_asps),
            )
        except Exception as e:
            logger.exception("chart auto-computation failed")
            return jsonify({
                "status": "error",
                "message": f"Failed to compute chart from birth data: {e}",
            }), 400

    # Validate required fields up front (clearer 400 than a deep stack later)
    for required in ("gender", "points", "ascendant", "aspects"):
        if required not in body:
            return jsonify({
                "status": "error",
                "message": f"Chart JSON missing required field: '{required}'",
            }), 400

    # Sinalizar ao report_generator: hora desconhecida + info de ingresso lunar.
    # Essas chaves com underscore são consumidas em report_generator.py para
    # reformular seções que dependem de hora (abertura/triade/lua/casa_4) e para
    # inserir o disclaimer no topo do relatório. Não vão para a resposta.
    body["_unknown_birth_time"] = unknown_birth_time
    body["_moon_meta"] = moon_meta

    # ==================================================================
    # ASPECTOS AUSENTES — CALCULAR ANTES DO FILTRO
    #
    # Kerykeion's NatalAspects.relevant_aspects só computa aspectos entre
    # planetas + Quíron + Lilith. NUNCA gera aspectos envolvendo
    # asteróides (Ceres, Vesta, Juno, Palas) nem Nodos (Norte, Sul).
    # Como a prática da Marcia interpreta esses aspectos, precisamos
    # calculá-los manualmente e adicionar à lista ANTES da cascata do
    # filtro — para que passem pelas mesmas regras de orbe, in-sign,
    # aplicativo, etc. Assim continuam sendo fonte única de verdade.
    #
    # Escopo dos pares calculados:
    #   · asteróides × planetas principais (Sol → Plutão)
    #   · Nodos × planetas principais
    #   · Nodos × asteróides
    # NÃO calculamos:
    #   · asteróide × asteróide (regra da Marcia limita a 4° e não usamos)
    #   · asteróide × Quíron/Lilith (regra da Marcia proíbe totalmente)
    #   · Nodo Norte × Nodo Sul (definicional, sempre 180°)
    #
    # NOTA sobre applying: o payload não carrega velocidades angulares,
    # então applying vira None nos aspectos manuais. As regras "só se
    # aplicativo" acima de 8° ficam conservadoras (na dúvida, descarta).
    # Aspectos abaixo do threshold passam pelo mesmo caminho de qualquer
    # outro aspecto.
    # ==================================================================
    _POINTS_SIGN_ORDER = [
        "aries", "taurus", "gemini", "cancer", "leo", "virgo",
        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
    ]
    # Só três tipos são considerados para asteróides e Nodos:
    # conjunção, oposição, quadratura. Trígono e sextil desses corpos
    # NÃO são interpretados na prática da Marcia — não computar.
    _ASPECT_PT_LABELS = {
        "conjunction": "conjunção", "sextile": "sextil", "square": "quadratura",
        "trine": "trígono", "opposition": "oposição",
    }
    # Ângulos + orbes máximos para o cálculo manual dos aspectos ausentes.
    # Para asteróides/Nodos: conj 6° · opp 6° · quadratura 4°. Trígono/
    # sextil ficam de fora deliberadamente.
    _MANUAL_ASPECT_SPECS = [
        ("conjunction",   0, 6.0),
        ("opposition",  180, 6.0),
        ("square",       90, 4.0),
    ]

    def _abs_pos(pdict):
        """Retorna a posição absoluta em graus 0-360 a partir de sign + degrees."""
        if not isinstance(pdict, dict):
            return None
        sign = (pdict.get("sign") or "").lower()
        deg = pdict.get("degrees")
        if sign not in _POINTS_SIGN_ORDER or deg is None:
            return None
        try:
            return _POINTS_SIGN_ORDER.index(sign) * 30.0 + float(deg)
        except (ValueError, TypeError):
            return None

    def _compute_missing_aspects(points):
        """Computa aspectos que o Kerykeion não gera. Só conjunção/oposição/
        quadratura, com orbes 6°/6°/4° — trígono e sextil de asteróides e
        Nodos não são interpretados na prática da Marcia. Retorna lista no
        mesmo formato dos aspectos do payload."""
        ASTEROIDS = ["ceres", "vesta", "juno", "pallas"]
        MAIN_PLANETS = ["sun", "moon", "mercury", "venus", "mars",
                        "jupiter", "saturn", "uranus", "neptune", "pluto"]
        NODES = ["north_node", "south_node"]

        pairs = []
        for a in ASTEROIDS:
            for p in MAIN_PLANETS:
                pairs.append((a, p))
        for n in NODES:
            for p in MAIN_PLANETS:
                pairs.append((n, p))
        for n in NODES:
            for a in ASTEROIDS:
                pairs.append((n, a))

        out = []
        for pa_key, pb_key in pairs:
            pos_a = _abs_pos(points.get(pa_key))
            pos_b = _abs_pos(points.get(pb_key))
            if pos_a is None or pos_b is None:
                continue

            # Distância angular circular
            raw = abs(pos_a - pos_b)
            dist = min(raw, 360.0 - raw)

            # Testar SÓ conjunção/oposição/quadratura contra a distância.
            # Escolher o de menor orbe entre os três; se nenhum estiver
            # dentro do seu orbe máximo específico, o par não forma aspecto.
            best = None  # (type, orb, max_orb)
            for atype, angle, max_orb in _MANUAL_ASPECT_SPECS:
                orb = abs(dist - angle)
                if orb <= max_orb:
                    if best is None or orb < best[1]:
                        best = (atype, orb, max_orb)

            if best is None:
                continue

            atype, orb, _max = best
            out.append({
                "planet_a": pa_key,
                "planet_b": pb_key,
                "type": atype,
                "type_pt": _ASPECT_PT_LABELS[atype],
                "orb": round(orb, 2),
                "applying": None,  # sem velocidade nos points do payload
            })
        return out

    # Aspectos que já vieram do cliente (Kerykeion) — planetas + Quíron + Lilith
    _client_aspects = body.get("aspects") or []
    # Aspectos calculados manualmente — asteróides + Nodos
    _computed_aspects = _compute_missing_aspects(body.get("points") or {})

    # Dedupe: se o cliente já mandou algum desses pares (improvável mas defensivo),
    # não sobrescrever. Chave é o par + tipo, independente da ordem dos corpos.
    def _pair_key(a):
        pa = a.get("planet_a", "")
        pb = a.get("planet_b", "")
        return (frozenset((pa, pb)), a.get("type"))
    _existing_keys = {_pair_key(a) for a in _client_aspects}
    _computed_new = [a for a in _computed_aspects if _pair_key(a) not in _existing_keys]

    _raw_aspects = _client_aspects + _computed_new
    _n_client = len(_client_aspects)
    _n_computed_added = len(_computed_new)

    logger.info(
        "aspects: %d from client + %d computed manually = %d total pre-filter",
        _n_client, _n_computed_added, len(_raw_aspects),
    )

    # ==================================================================
    # FILTRO DE ASPECTOS — ÚNICA FONTE DE VERDADE PARA TODO O PIPELINE
    #
    # Executa numa cascata determinística. Cada aspecto que sobrevive tem:
    #   {planet_a, planet_b, type, type_pt, orb,
    #    applying: True|False|None,          # aplicativo (True) / separativo (False) / indeterminado
    #    weight:   "dominant"|"very_strong"|"strong"|"moderate"|"weak"|"conjunction_only",
    #    strength: float 0-1                 # força geométrica pura (só orbe)
    #   }
    #
    # A lista filtrada é escrita de volta em body["aspects"] e usada por:
    #   · report_generator (texto interpretativo, contexto Claude)
    #   · pdf_generator (tabela de aspectos in-sign na página 2)
    #   · verify_planet_signs (verificador anti-alucinação)
    # Todos consomem A MESMA lista — evita o bug histórico onde tabela e
    # texto interpretativo usavam listas diferentes.
    #
    # NOTA sobre `strength`: neste passo o valor é PURAMENTE geométrico
    # (função monotônica do orbe). NÃO codifica hierarquia planetária
    # nem importância do par de corpos — essa camada de "pesos por par"
    # é planejada para uma rodada futura.
    # ==================================================================
    from report_generator import is_in_sign_aspect as _is_in_sign

    # _raw_aspects já montado acima combinando cliente + computados manualmente
    _points = body.get("points") or {}

    # ----- Constantes do filtro -----
    _PLANETS = {"sun","moon","mercury","venus","mars","jupiter","saturn","uranus","neptune","pluto"}
    _TRANSPERSONAL = {"uranus","neptune","pluto"}
    _ASTEROIDS = {"ceres","vesta","juno","pallas"}
    _CHIRON_LILITH = {"chiron","lilith"}
    _NODES = {"north_node","south_node"}
    # Corpos "menor" nos quais a conjunção com planeta é limitada a 5°.
    # (Nodos e asteróides tinham regra específica no bloco antigo, mas agora
    # são interceptados no bloco novo com orbes 6°/6°/4°.)
    _MINOR_SPECIAL = {"chiron","lilith"}

    # Regras específicas de asteróides/Nodos:
    #   · SÓ conjunção (max 6°), oposição (max 6°), quadratura (max 4°)
    #   · Trígono e sextil NÃO são interpretados na prática da Marcia
    #   · applying=None NÃO descarta esses aspectos (orbes já apertadas
    #     tornam o critério aplicativo irrelevante)
    _ASTEROID_NODE_ALL = _ASTEROIDS | _NODES
    _ASTEROID_NODE_ORB_MAX = {"conjunction": 6.0, "opposition": 6.0, "square": 4.0}

    # Orbe padrão máximo por tipo de aspecto (planetas entre si)
    _ORB_MAX = {
        "conjunction": 12.0,
        "opposition":  10.0,
        "square":      10.0,
        "trine":        8.0,
        "sextile":      6.0,
    }
    # Acima deste orbe, o aspecto SÓ passa se applying==True
    _APPLYING_REQUIRED_ABOVE = {
        "conjunction": 8.0,
        "opposition":  8.0,
        "square":      8.0,
    }

    def _weight_and_strength(orb):
        """Peso categórico (dominant → conjunction_only) + strength geométrica linear.
        strength = 1 - orb/12, clampado em [0, 1] — vai a 1 no aspecto exato."""
        s = max(0.0, min(1.0, 1.0 - orb / 12.0))
        s = round(s, 3)
        if orb < 2.0:  return "dominant",         s
        if orb < 4.0:  return "very_strong",      s
        if orb < 6.0:  return "strong",           s
        if orb < 8.0:  return "moderate",         s
        if orb < 10.0: return "weak",             s
        return           "conjunction_only",      s

    def _normalize_applying(a):
        """Aceita: bool, None, 'Applying'/'Separating' string, ausente.
        Retorna True/False/None. Trata Lua como incerta em mapas sem hora."""
        # Regra especial: aspectos envolvendo Lua em unknown_birth_time são
        # intrinsecamente incertos — Lua move ~13°/dia, o valor de applying
        # calculado para meio-dia default não é confiável.
        if unknown_birth_time and ("moon" in (a.get("planet_a"), a.get("planet_b"))):
            return None
        v = a.get("applying")
        if v is True or v is False:
            return v
        if isinstance(v, str):
            vl = v.lower()
            if vl in ("applying", "aplicativo", "aplicando"): return True
            if vl in ("separating", "separativo", "separando"): return False
        return None  # ausente ou irreconhecível

    kept = []
    dropped = []

    def _drop(a, reason, **extras):
        dropped.append({
            **{k: a.get(k) for k in ("planet_a","planet_b","type","orb")},
            "reason": reason, **extras,
        })

    for a in _raw_aspects:
        pa = a.get("planet_a")
        pb = a.get("planet_b")
        atype = a.get("type")
        orb = float(a.get("orb", 0.0) or 0.0)
        applying = _normalize_applying(a)

        # Etapa 1: aspecto in-sign obrigatório (regra pré-existente)
        sa = (_points.get(pa) or {}).get("sign")
        sb = (_points.get(pb) or {}).get("sign")
        if not (sa and sb) or not _is_in_sign(sa, sb, atype):
            _drop(a, "out_of_sign_dissociated")
            continue

        # Etapa 2: pares proibidos (asteróide × Quíron/Lilith — ignorar totalmente)
        if (pa in _ASTEROIDS and pb in _CHIRON_LILITH) or \
           (pb in _ASTEROIDS and pa in _CHIRON_LILITH):
            _drop(a, "forbidden_pair_asteroid_x_chiron_or_lilith")
            continue

        # Etapa 3: INTERCEPTAR aspectos envolvendo asteróide ou Nodo — regras
        # específicas se aplicam ANTES da cascata geral:
        #   · SÓ conjunção (max 6°) / oposição (max 6°) / quadratura (max 4°)
        #   · Trígono e sextil descartados por não serem interpretados
        #   · applying=None NÃO descarta (orbes apertadas já garantem
        #     relevância — critério aplicativo fica irrelevante aqui)
        #   · Salta a etapa 5 (applying threshold) — não se aplica
        if pa in _ASTEROID_NODE_ALL or pb in _ASTEROID_NODE_ALL:
            allowed = _ASTEROID_NODE_ORB_MAX.get(atype)
            if allowed is None:
                # Trígono ou sextil (ou tipo desconhecido) — não interpretar
                _drop(a, "asteroid_or_node_aspect_type_not_used",
                      aspect_type=atype,
                      allowed=list(_ASTEROID_NODE_ORB_MAX.keys()))
                continue
            if orb > allowed:
                _drop(a, "asteroid_or_node_orb_exceeded",
                      limit=allowed, aspect_type=atype)
                continue
            # Passou. Peso e força; applying é preservado como veio (geralmente
            # None nos calculados manualmente, ou o valor do payload se veio).
            weight, strength = _weight_and_strength(orb)
            kept.append({
                **a,
                "applying": applying,
                "weight": weight,
                "strength": strength,
            })
            continue

        # Etapa 4: orbe máximo por tipo de aspecto (padrão entre planetas /
        # Quíron / Lilith — asteróides e Nodos já foram tratados acima)
        max_orb_std = _ORB_MAX.get(atype)
        if max_orb_std is None:
            _drop(a, "unknown_aspect_type")
            continue

        # Etapa 4: restrições específicas de PARES DE CORPOS
        # (Asteróides e Nodos já foram tratados na etapa 3 acima — aqui
        # tratamos apenas Quíron/Lilith e pares planeta-planeta.)
        #
        # 4a — conjunção entre asteróides: regra INATIVA na prática atual
        # porque não computamos asteróide × asteróide. Documentada para
        # o caso de aparecer via payload; se aparecer, aplica máx 4°.
        if atype == "conjunction" and pa in _ASTEROIDS and pb in _ASTEROIDS:
            if orb > 4.0:
                _drop(a, "asteroid_conj_orb_over_4", limit=4.0)
                continue

        # 4b — conjunção de PLANETA com Quíron ou Lilith: máx 5°
        elif atype == "conjunction" and (
            (pa in _PLANETS and pb in _MINOR_SPECIAL) or
            (pb in _PLANETS and pa in _MINOR_SPECIAL)
        ):
            if orb > 5.0:
                _drop(a, "planet_x_chiron_or_lilith_conj_orb_over_5", limit=5.0)
                continue

        # 4c — QUALQUER aspecto entre dois transpessoais: máx 5°
        elif pa in _TRANSPERSONAL and pb in _TRANSPERSONAL:
            if orb > 5.0:
                _drop(a, "transpersonal_x_transpersonal_orb_over_5", limit=5.0)
                continue

        # 4d — caso geral: aplicar orbe padrão do tipo
        else:
            if orb > max_orb_std:
                _drop(a, "standard_orb_exceeded", limit=max_orb_std)
                continue

        # Etapa 5: regra do "só se aplicativo" nas faixas altas
        # (conjunções/oposições/quadraturas 8°-limite exigem applying=True;
        #  se applying==None → conservador → descarta)
        appl_threshold = _APPLYING_REQUIRED_ABOVE.get(atype)
        if appl_threshold is not None and orb > appl_threshold:
            if applying is not True:
                _drop(a, "above_applying_threshold_not_applying",
                      threshold=appl_threshold, applying=applying)
                continue

        # Sobreviveu — anotar peso e força e manter
        weight, strength = _weight_and_strength(orb)
        kept.append({
            **a,
            "applying": applying,
            "weight": weight,
            "strength": strength,
        })

    body["aspects"] = kept
    body["_dropped_aspects"] = dropped
    logger.info(
        "aspects filter: %d raw → %d kept (%d dropped)",
        len(_raw_aspects), len(kept), len(dropped),
    )

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
        _send_failure_alert("generate_report", e, {
            "name": body.get("name"), "email": body.get("email"),
            "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
            "ip": _client_ip, "ua": _ua,
        })
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
        # Se o cliente não passou birth_place explícito, usar a cidade que
        # foi de fato geocoded — dá transparência sobre o que foi calculado.
        cover_place = birth_place or (body.get("birth_city") or "").strip()
        pdf_bytes = pg.generate_pdf(
            report_text=result["report"],
            client_name=result["name"],
            birth_date=birth_date_display,
            birth_place=cover_place,
            birth_note=unknown_time_note,
            chart_image_url=chart_svg_path,
            aspects=body.get("aspects", []),
            points=body.get("points", {}),
            time_unknown=unknown_birth_time,
        )
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    except Exception as e:
        logger.exception("generate_pdf failed")
        pdf_error = str(e)
        _send_failure_alert("generate_pdf", e, {
            "name": body.get("name"), "email": body.get("email"),
            "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
            "ip": _client_ip, "ua": _ua,
        })
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
        elif not SENDGRID_API_KEY:
            email_error = "SendGrid API key not configured on server (SENDGRID_API_KEY)"
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
            # Auditoria do filtro in-sign — quantos aspectos vieram no payload
            # bruto e quantos foram descartados por serem dissociados, mais a
            # lista completa dos descartados (par de corpos, tipo, orbe) para
            # verificação visual.
            "aspects_from_client_count": _n_client,
            "aspects_computed_manually_count": _n_computed_added,
            "aspects_raw_count": len(_raw_aspects),
            "aspects_kept_count": len(kept),
            "aspects_kept": kept,
            "aspects_dropped": dropped,
            # Divergências entre afirmações de "[planeta] em [signo]" no texto
            # gerado e os dados reais do chart. Cada item foi CORRIGIDO no
            # texto antes de sair (signo substituído; ou "em X" removido se
            # for a Lua num mapa moon_uncertain). Lista fica exposta pro
            # operador auditar cada correção feita.
            "sign_divergences": result.get("sign_divergences", []),
            "correction_rewrites": result.get("correction_rewrites", []),
            "partial_coverage": result.get("partial_coverage", []),
            "verifier_log": result.get("verifier_log", []),
            "parental_clusters": result.get("parental_clusters"),
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
