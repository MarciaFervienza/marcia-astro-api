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
import unicodedata
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
    "Chiron", "Mean_Lilith",
    # Nodo Sul (oposto ao Norte) DESENHADO na mandala — decisão da Márcia,
    # 17/07. Ele já era calculado e usado em todo o resto (points, aspectos
    # manuais, texto do relatório); só faltava na lista que alimenta o
    # renderer, então o mapa não o mostrava. É ponto de cálculo, não
    # derivação visual.
    "Mean_North_Lunar_Node", "Mean_South_Lunar_Node",
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

# INTERRUPTOR DO CAMINHO ASSÍNCRONO. Desligado por padrão de propósito:
# enquanto o worker não estiver de pé, enfileirar seria aceitar pedidos
# que ninguém vai processar — pior que demorar. Ligar com FILA_ASSINCRONA=1
# depois que o serviço worker subir; desligar volta ao comportamento de
# hoje sem push.
FILA_ASSINCRONA = os.environ.get("FILA_ASSINCRONA", "").strip().lower() in (
    "1", "true", "sim", "yes", "on")

# Espera máxima tolerada na fila antes de alertar que ninguém está
# consumindo. 15 min é folgado de propósito: a geração leva 2 a 5 minutos
# e uma rajada de pedidos pode enfileirar legitimamente. Abaixo disso o
# alerta viraria ruído, e alerta que vira ruído é alerta que se ignora.
FILA_PARADA_SEGS = int(os.environ.get("FILA_PARADA_SEGS", "900"))

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


_CHAVES_OBRIGATORIAS = ("PINECONE_API_KEY", "OPENAI_API_KEY",
                        "ANTHROPIC_API_KEY")
# Segredos que viajam como CABEÇALHO HTTP. Cabeçalho é ASCII por
# especificação — não é preferência do httpx nem coisa que `-X utf8`
# resolva.
_CHAVES_DE_CABECALHO = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                        "PINECONE_API_KEY", "SENDGRID_API_KEY",
                        "API_SECRET_KEY")


def chaves_malformadas():
    """Segredos com caractere que NÃO cabe num cabeçalho HTTP.

    POR QUE EXISTE (11/08). A ANTHROPIC_API_KEY do serviço worker foi
    colada com uma ASPA CURVA no fim — o editor converteu a aspa reta ao
    copiar. O httpx codifica cabeçalho em ASCII, então toda chamada ao
    Claude morria com:

        'ascii' codec can't encode character '”' in position 109

    A posição é o TAMANHO DA CHAVE, e por isso era idêntica em todos os
    trabalhos. O erro nascia a 16 chamadas de profundidade, dentro de uma
    thread, dentro do SDK — longe da causa. Custou uma hora e uma hipótese
    errada minha (locale ASCII do contêiner; testei, `-X utf8` NÃO
    conserta isto, cabeçalho é ASCII por especificação).

    Agora falha no ARRANQUE, com o nome da variável e o caractere.
    Segredo malformado é a classe de defeito que mais se disfarça: a
    variável está definida, o painel mostra o valor, e só quebra na
    primeira chamada de verdade.
    """
    ruins = []
    for k in _CHAVES_DE_CABECALHO:
        v = os.environ.get(k) or ""
        if not v:
            continue
        try:
            v.encode("ascii")
        except UnicodeEncodeError as e:
            mau = v[e.start]
            ruins.append({
                "variavel": k,
                "posicao": e.start,
                "caractere": repr(mau),
                "nome_unicode": unicodedata.name(mau, "?"),
                "detalhe": (f"{k} tem {mau!r} na posição {e.start} de "
                            f"{len(v)}. Cabeçalho HTTP é ASCII: esta chave "
                            f"NUNCA vai funcionar. Aspa curva no começo ou "
                            f"no fim quase sempre é cópia de um editor que "
                            f"converteu a aspa reta — recole sem aspas."),
            })
            continue
        # Aspas RETAS sobrando também são erro de cópia, e essas passam no
        # ASCII: a chave viaja com aspas e a API devolve 401, que manda a
        # Márcia procurar chave errada em vez de chave mal colada.
        if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
            ruins.append({
                "variavel": k, "posicao": 0, "caractere": repr(v[0]),
                "nome_unicode": "QUOTATION MARK",
                "detalhe": (f"{k} está entre aspas ({v[0]!r}). O valor da "
                            f"variável não leva aspas — elas viajam junto e "
                            f"a API devolve 401."),
            })
    return ruins


def _missing_required_keys():
    """Return a list of required env vars that are missing or empty."""
    missing = []
    for k in _CHAVES_OBRIGATORIAS:
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
            # Fixo em True: semanticamente correto (a mandala flutua no
            # ivory da página). svglib não pinta o background-color do
            # <svg> root de qualquer forma, então visualmente é idêntico
            # ao False no PDF, mas o SVG cru fica limpo.
            transparent_background=True,
        )
    except Exception as e:
        return None, f"chart data/drawer build failed: {e}"

    import tempfile
    out_dir = tempfile.mkdtemp(prefix="kerykeion_")
    filename = "natal_wheel"
    try:
        # Constrained packing. O Kerykeion de fábrica afasta os corpos com uma
        # separação FIXA de 8° (PLANET_MIN_SEPARATION) sem olhar em que casa ou
        # signo o corpo cai — e assim desenha planetas na casa errada e no signo
        # errado. Medido em 1000 mapas sintéticos: 912 mapas com defeito, 1802
        # corpos em signo errado, 1766 em casa errada. Os 5 mapas de clientes
        # reais tinham de 4 a 8 corpos errados cada.
        #
        # O packing troca essa constante por uma separação calculada: uma cadeia
        # global de todos os corpos em ordem zodiacal, cada um confinado à
        # interseção casa ∩ signo (cúspides Placidus REAIS, não asc + i*30), com
        # a separação valendo entre vizinhos no círculo. Mesmos 1000 mapas: zero.
        #
        # Não substitui o renderer: continua sendo o desenho da fábrica, com os
        # glifos da fábrica. Ver wheel_renderer/packing.py e as 7 propriedades em
        # wheel_renderer/props.py (prove_bite.py prova que cada uma morde).
        from wheel_renderer import packing
        packing.install()
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
        finally:
            # O patch é global no módulo draw_modern. Desfazer SEMPRE, mesmo com
            # exceção, senão vaza para a próxima requisição do processo Flask.
            packing.uninstall()
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
        # -------- Glifo da Lua Negra Lilith (espelhado pelo Kerykeion) -----
        # Ver wheel_renderer/packing.fix_lilith_glyph. O corpo calculado está
        # correto (MEAN_APOG); só o símbolo sai invertido. Se a lib mudar o
        # glifo, a correção NÃO é aplicada e isto loga — nunca espelha
        # conteúdo desconhecido.
        from wheel_renderer.packing import fix_lilith_glyph
        svg_text, _lil_ok = fix_lilith_glyph(svg_text)
        if not _lil_ok:
            logger.warning(
                "glifo de Lilith NÃO corrigido: o símbolo do Kerykeion mudou. "
                "Conferir se a lib passou a desenhar a Lua Negra na orientação "
                "certa (aí a correção deve ser removida) ou se o seletor quebrou."
            )
        # -------- Leader lines (planeta → posição real no anel) --------
        # Objetivo: comportamento do Astro Gold — glifos afastados quando
        # planetas estão próximos em grau (o mecanismo interno de
        # PLANET_MIN_SEPARATION do Kerykeion continua funcionando), MAS
        # sem a linha conectando o glifo à posição real. O grau ao lado
        # de cada glifo revela a posição, e o leitor faz a associação.
        #
        # FRÁGIL: este regex depende do padrão específico usado pelo
        # Kerykeion 5.12.8 (modern draw path — draw_modern.py:_draw_indicator_line)
        # que emite as leader lines dentro de um <g kr:node='Indicator' ...>
        # com um <path> dentro. Se a lib for atualizada e o marcador semântico
        # kr:node mudar (ou se o path passar a viver em outra estrutura),
        # este regex vira NO-OP silencioso — as linhas voltam a aparecer no
        # PDF sem quebrar nada. Antes de atualizar kerykeion, rodar um mapa
        # de teste e conferir visualmente.
        svg_text = re.sub(
            r"<g\s+kr:node=['\"]Indicator['\"][^>]*>.*?</g>\s*",
            "",
            svg_text,
            flags=re.DOTALL,
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
from retry_util import _com_retry, _erro_transitorio  # noqa: F401


# =============================================================
# BUSCA DE CIDADE — opções para o formulário escolher (19/07)
#
# O problema medido: `geocode(exactly_one=True)` devolve o PRIMEIRO
# resultado, em silêncio. "Santa Rosa" resolve para a CALIFÓRNIA; Santa
# Rosa (RS) é a terceira opção. Muda o fuso, muda a hora sideral, muda o
# Ascendente e as doze casas — e o relatório sai limpo por todos os
# detectores, porque não há nada errado com o TEXTO. Erro silencioso da
# pior espécie: o produto parece perfeito e descreve outra pessoa.
#
# O `id` é AUTOCONTIDO e assinado, não uma chave de cache: sobrevive a
# restart, não precisa de TTL, e não dá para forjar coordenada.
# =============================================================
def _assina_cidade(payload: str) -> str:
    import hashlib
    import hmac as _hmac
    return _hmac.new((API_SECRET_KEY or "sem-chave").encode(),
                     payload.encode(), hashlib.sha256).hexdigest()[:16]


def _empacota_cidade(lat, lng, tz, rotulo):
    import base64
    payload = f"{lat:.6f}|{lng:.6f}|{tz}|{rotulo}"
    b = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{b}.{_assina_cidade(payload)}"


def _desempacota_cidade(city_id):
    """(lat, lng, tz, rotulo) ou None se a assinatura não confere."""
    import base64
    try:
        b, sig = city_id.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode()
        import hmac as _hmac
        if not _hmac.compare_digest(sig, _assina_cidade(payload)):
            return None
        lat, lng, tz, rotulo = payload.split("|", 3)
        return float(lat), float(lng), tz, rotulo
    except Exception:
        return None


def buscar_cidades(q, limit=6):
    """[{id, rotulo, lat, lng, tz}] — ordem do Nominatim PRESERVADA.

    Decisão da Márcia (19/07): NÃO ordenar por país. Muitas clientes moram
    fora; priorizar Brasil economizaria um clique para a maioria e criaria
    risco de clique errado justamente para quem tem homônima no exterior.
    O rótulo completo (cidade, estado, país) é que desambigua.
    """
    q = (q or "").strip()
    if len(q) < 3:
        return [], None
    # CADEIA DE PROVEDORES (19/07) — ver geocode_util. O Nominatim bloqueia
    # o IP do Railway por política de datacenter; sem queda, isto aqui
    # devolvia 429 para toda cliente nova.
    import geocode_util
    res, _prov, erro = geocode_util.buscar_bruto(q, limit=limit)
    if erro:
        return [], erro
    if not res:
        return [], None
    try:
        from timezonefinder import TimezoneFinder
    except ImportError as e:
        return [], f"timezonefinder não instalado ({e})"
    tf = TimezoneFinder()
    out = []
    for loc in res:
        lat, lng = loc["lat"], loc["lng"]
        tz = tf.timezone_at(lat=lat, lng=lng)
        if not tz:
            continue
        rotulo = loc["rotulo"]
        out.append({"id": _empacota_cidade(lat, lng, tz, rotulo),
                    "rotulo": rotulo, "lat": lat, "lng": lng, "tz": tz})
    return out, None


def _ambiguidade_real(opcoes):
    """Mais de um ESTADO ou PAÍS entre as opções? Devolve o resumo ou None.

    Homônimas dentro do mesmo estado (Nominatim devolve o mesmo município
    duas vezes) NÃO são ambiguidade — mudam metros, não o mapa.
    """
    lugares = set()
    for o in opcoes[:6]:
        partes = [p.strip() for p in o["rotulo"].split(",")]
        lugares.add((partes[-1] if partes else "", o["tz"]))
    return sorted(lugares) if len(lugares) > 1 else None


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

    # CADEIA DE PROVEDORES (19/07) — Nominatim primeiro, Photon quando ele
    # bloqueia o IP. Mesma base OSM; ver geocode_util para a medição.
    # A busca e a geocodificação passam pela MESMA rotina de propósito: se
    # o autocomplete e a geração consultassem provedores diferentes, o
    # city_id escolhido pela cliente poderia não ser onde o mapa é feito.
    import geocode_util
    res, _prov, geo_erro = geocode_util.buscar_bruto(city, limit=1)
    if geo_erro:
        return None, None, None, geo_erro

    if not res:
        return None, None, None, (
            f"Cidade de nascimento não encontrada: {city}. Verifique a grafia."
        )

    lat = res[0]["lat"]
    lng = res[0]["lng"]

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


# =============================================================
# REGISTRO DAS ÚLTIMAS GERAÇÕES (19/07)
#
# Existe por uma pergunta que nenhum de nós dois conseguia responder: se o
# proxy corta em 300s e o cliente desconecta, o e-mail ainda sai?
#
# Sem isto, um e-mail que não chega tem DUAS explicações que levam a
# decisões opostas — a desconexão abortou o processo, ou a geração falhou
# fechada e o e-mail não saiu por desenho. O registro fica em memória e
# sobrevive à desconexão do cliente, porque é o SERVIDOR que escreve.
#
# Só metadado: nome, quando, quanto tempo, desfecho, e se o e-mail saiu.
# Nada de conteúdo de relatório.
_ULTIMAS_GERACOES = _deque(maxlen=25)
_geracoes_lock = _Lock()


def _registra_geracao(**kw):
    try:
        with _geracoes_lock:
            _ULTIMAS_GERACOES.append({"quando": _time.strftime("%H:%M:%S"), **kw})
    except Exception:
        pass


@app.route("/ultimas-geracoes", methods=["GET"])
def ultimas_geracoes_endpoint():
    import hmac
    presented = (request.headers.get("X-API-Key")
                 or request.args.get("api_key") or "")
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    with _geracoes_lock:
        return jsonify({"status": "ok",
                        "geracoes": list(_ULTIMAS_GERACOES)}), 200


def _fila_ou_erro():
    """A fila, ou (None, resposta de erro). Falha ALTA, nunca silenciosa."""
    try:
        import fila as _f
        f = _f.Fila()
        f.criar_tabelas()
        return f, None
    except Exception as exc:
        logger.error("fila indisponível: %s", exc)
        return None, (jsonify({
            "status": "error", "code": "fila_indisponivel",
            "message": f"A fila persistida não está disponível: {exc}"}), 503)


@app.route("/diag-fila", methods=["POST"])
def diag_fila_endpoint():
    """Roda as asserções da fila contra o BANCO REAL, de dentro do servidor.

    Existe porque o DATABASE_URL do Railway é interno: da minha máquina eu
    só alcanço o SQLite dos testes, e "o instrumento não é o produto" já
    custou caro neste projeto. Especialmente a concorrência — FOR UPDATE
    SKIP LOCKED é justamente o ramo que o SQLite NÃO exercita.

    Limpa o que cria: usa ids próprios e apaga no fim.
    """
    import hmac
    import threading as _th
    body = request.get_json(silent=True) or {}
    presented = request.headers.get("X-API-Key") or body.pop("api_key", "")
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    res, criados = [], []

    def ok(nome, cond, det="", exercitada=True):
        """`exercitada=False` marca asserção que passou por VACUIDADE.

        Pedido da Márcia (11/08): a asserção 12 passou com `tocados=0` —
        nenhum trabalho de cliente estava pendente durante o diagnóstico,
        então ela ficou verde sem ter testado nada. Verde por vacuidade é
        a mesma classe da salvaguarda morta e da concorrência com uma
        thread só: o relatório afirma cobertura que não houve. Aqui ela
        passa a se declarar."""
        res.append({"asserção": nome, "passou": bool(cond), "detalhe": det,
                    "exercitada": bool(exercitada)})

    try:
        import fila as _f
        f = _f.Fila()
        f.criar_tabelas()
        ok("criar_tabelas roda (idempotente)", True)
        ok("é Postgres, não SQLite", f.postgres, f"postgres={f.postgres}")

        tid = f.enfileirar({"diag": True}, nome="DIAG", email="diag@x")
        criados.append(tid)
        ok("enfileira", bool(tid))
        ok("nasce PENDENTE", f.buscar(tid)["estado"] == _f.PENDENTE)
        j = f.reivindicar("diag-w1")
        ok("reivindica", j is not None)
        f.concluir(tid, markdown="# diag", chart={"points": {"sun": {}}})
        b = f.buscar(tid)
        ok("conclui e guarda markdown", b["estado"] == _f.OK
           and b["markdown"] == "# diag")
        ok("guarda o chart", (b["chart"] or {}).get("points") == {"sun": {}})

        # CONCORRÊNCIA CONTRA O POSTGRES REAL — o ramo SKIP LOCKED.
        n = int(body.get("n") or 8)
        ids = [f.enfileirar({"diag": i}) for i in range(n)]
        criados += ids
        pegos, erros, alheios, lock = [], [], [], _th.Lock()

        # BARREIRA DE LARGADA. Sem ela, a thread 0 podia esvaziar a fila
        # antes de as outras três começarem: `pegos == n` e `duplicados == 0`
        # ficariam VERDES sem nunca ter havido disputa, e o SKIP LOCKED —
        # o ramo que o SQLite não exercita e a razão de este endpoint
        # existir — não seria exercitado. Todas partem juntas.
        largada = _th.Event()
        por_worker = {}

        def _w(w):
            largada.wait(5)
            try:
                while True:
                    t = f.reivindicar(f"diag-{w}")
                    if not t:
                        return
                    if t["id"] not in ids:
                        # TRABALHO DE CLIENTE. Antes isto era um `return`
                        # seco: o pedido ficava preso em PROCESSANDO com
                        # nenhum worker atrás dele, e com uma tentativa
                        # queimada do teto de 2. O diagnóstico contaminava
                        # produção — dois pedidos reais travaram assim.
                        f.devolver(t["id"], f"diag-{w}")
                        with lock:
                            # guarda o valor de ANTES (reivindicar já
                            # incrementou) para conferir a devolução
                            alheios.append((t["id"], t["tentativas"] - 1))
                        return
                    with lock:
                        pegos.append(t["id"])
                        por_worker[w] = por_worker.get(w, 0) + 1
            except Exception as exc:
                with lock:
                    erros.append(f"{w}: {type(exc).__name__}: {exc}")

        ths = [_th.Thread(target=_w, args=(i,)) for i in range(4)]
        [t.start() for t in ths]
        largada.set()
        [t.join() for t in ths]
        ok("nenhum worker morreu", not erros, "; ".join(erros[:2]))
        ok(f"{n} trabalhos, {n} reivindicações", len(pegos) == n,
           f"pegos={len(pegos)}")
        ok("NENHUM trabalho pego duas vezes",
           len(set(pegos)) == len(pegos),
           f"duplicados={len(pegos) - len(set(pegos))}")
        # A asserção que faltava: sem ela, uma thread sozinha fazendo todo
        # o trabalho produz exatamente o mesmo resultado das quatro
        # cooperando. "Passou" não é o mesmo que "exercitou".
        ok("MAIS DE UM worker disputou (o SKIP LOCKED foi exercitado)",
           len(por_worker) > 1,
           f"distribuição por worker: {dict(sorted(por_worker.items()))}")
        # O diagnóstico não pode custar nada a quem está na fila de
        # verdade. Confere estado E tentativas: devolver deixando a
        # tentativa queimada gastaria o teto de retomadas em silêncio.
        _sujos = []
        for _tid, _tent_antes in dict(alheios).items():
            _t = f.buscar(_tid)
            if (not _t or _t["estado"] != _f.PENDENTE
                    or _t["tentativas"] != _tent_antes):
                _sujos.append(f"{_tid}: estado={_t and _t['estado']} "
                              f"tentativas={_t and _t['tentativas']} "
                              f"(esperado pendente/{_tent_antes})")
        ok("trabalho de cliente tocado por engano foi DEVOLVIDO intacto",
           not _sujos,
           f"tocados={len(dict(alheios))} sujos={_sujos or 'nenhum'}"
           + ("" if alheios else " — NENHUM pendente de cliente durante o "
                                "diagnóstico; quem exercita o devolver é "
                                "prove_fila, offline"),
           exercitada=bool(alheios))

        contagem = f.contagem_por_estado()
    except Exception as exc:
        logger.exception("diag-fila falhou")
        return jsonify({"status": "error", "message": str(exc)[:400],
                        "assercoes": res}), 500
    finally:
        try:
            con = f.con()
            for t in criados:
                con.cursor().execute(f._q("DELETE FROM trabalhos WHERE id=%s"),
                                     (t,))
            if f.postgres:
                con.commit()
        except Exception:
            pass

    falharam = [r for r in res if not r["passou"]]
    vazias = [r["asserção"] for r in res if r["passou"] and not r["exercitada"]]
    return jsonify({"status": "ok" if not falharam else "error",
                    "passaram": len(res) - len(falharam), "de": len(res),
                    # Contadas SEPARADAMENTE de propósito. Somar asserção
                    # vazia ao número de aprovadas é como o relatório passa
                    # a mentir sobre a própria cobertura.
                    "nao_exercitadas": vazias,
                    "assercoes": res, "contagem_antes_da_limpeza": contagem}), \
        (200 if not falharam else 500)


@app.route("/fila", methods=["GET"])
def fila_endpoint():
    """Os últimos trabalhos da fila. Autenticado.

    Sem isto não dá para operar: o id só existia na resposta do
    enfileiramento, e se ela se perdesse o trabalho ficava inalcançável —
    `/status` exige o id e `/diag-fila` só conta. Aconteceu em 11/08.

    A `duracao_s` é o número que a Márcia pediu: quanto leva uma geração de
    ponta a ponta pelo caminho assíncrono real.
    """
    import hmac
    presented = request.headers.get("X-API-Key") or request.args.get("api_key") or ""
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    f, err = _fila_ou_erro()
    if err:
        return err
    try:
        _lim = max(1, min(100, int(request.args.get("limite") or 20)))
    except Exception:
        _lim = 20
    _esp = f.espera_do_mais_antigo()
    return jsonify({
        "status": "ok",
        "contagem": f.contagem_por_estado(),
        "espera_do_mais_antigo_s": None if _esp is None else round(_esp, 1),
        "trabalhos": f.recentes(_lim),
    }), 200


@app.route("/reenfileirar", methods=["POST"])
def reenfileirar_endpoint():
    """Cria um trabalho NOVO a partir do payload guardado de outro.

    Existe porque o teto de retomadas é 2, por desenho: trabalho que falha
    em laço não pode ficar tentando para sempre. Mas quando a causa foi
    ambiente — e não o pedido —, os trabalhos que esgotaram o teto estão
    corretos e só precisam rodar de novo. Foi o caso de 11/08: os três
    primeiros morreram por locale ASCII no contêiner do worker, não por
    nada que a cliente tenha enviado.

    Reenfileira a partir do PAYLOAD GUARDADO, nunca de um payload
    reconstruído à mão: reconstruir é como a fixture mentiu quatro vezes
    num dia só. O pedido que roda tem de ser byte a byte o que a pessoa
    enviou.

    Corpo: {id}. Devolve o id novo.
    """
    import hmac
    body = request.get_json(silent=True) or {}
    presented = request.headers.get("X-API-Key") or body.pop("api_key", "")
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    tid = (body.get("id") or "").strip()
    if not tid:
        return jsonify({"status": "error", "message": "Informe 'id'."}), 400
    f, err = _fila_ou_erro()
    if err:
        return err
    t = f.buscar(tid)
    if not t:
        return jsonify({"status": "error",
                        "message": f"Trabalho {tid!r} não encontrado."}), 404
    if not t.get("payload"):
        return jsonify({"status": "error",
                        "message": "O trabalho não tem payload guardado — "
                                   "não dá para reenfileirar sem ele."}), 409
    # Um trabalho ainda em curso não pode ser duplicado: seriam dois
    # relatórios para a mesma cliente, que é o modo de falha contra o qual
    # o heartbeat existe.
    if t["estado"] in ("pendente", "processando") and not body.get("forcar"):
        return jsonify({
            "status": "error", "code": "ainda_em_curso",
            "message": f"O trabalho está em {t['estado']!r}. Reenfileirar "
                       f"agora produziria DOIS relatórios para a mesma "
                       f"pessoa. Use 'forcar': true se for mesmo isso.",
        }), 409

    novo = f.enfileirar(t["payload"], nome=t.get("nome"), email=t.get("email"))
    logger.info("REENFILEIRADO %s → %s (estado anterior: %s, tentativas %s)",
                tid, novo, t["estado"], t["tentativas"])
    return jsonify({
        "status": "accepted", "id": novo, "origem": tid,
        "estado_da_origem": t["estado"],
        "status_url": f"/status/{novo}",
    }), 202


@app.route("/diag-geocache", methods=["GET"])
def diag_geocache_endpoint():
    """Estado do cache de cidades. Existe porque o tempo NÃO prova nada.

    Medindo da minha máquina, consulta com cache e sem cache levam o mesmo
    tempo — a latência é dominada pela ida e volta até o Railway, não pelo
    geocoding. Duas respostas idênticas também não provam: o provedor
    devolveria idêntico. `consultas_evitadas` é o único sinal que
    distingue "o cache funciona" de "eu presumi que funciona".
    """
    import hmac
    presented = request.headers.get("X-API-Key") or request.args.get("api_key") or ""
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    try:
        import geo_cache
        c = geo_cache.cache()
        if c is None:
            return jsonify({"status": "ok", "ligado": False,
                            "motivo": "sem DATABASE_URL ou banco fora"}), 200
        est = c.estatisticas()
        # `q` opcional: diz se ESTA consulta já está guardada, e sob que
        # chave. É como se confere que a normalização casa o que devia.
        q = (request.args.get("q") or "").strip()
        detalhe = {}
        if q:
            detalhe = {"consulta": q, "chave": geo_cache.normaliza(q),
                       "guardada": c.buscar(q) is not None}
        return jsonify({"status": "ok", "ligado": True, **est, **detalhe}), 200
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)[:300]}), 500


@app.route("/remontar-pdf", methods=["POST"])
def remontar_pdf_endpoint():
    """DEGRAU 3 da recuperação: markdown editado à mão → PDF → cliente.

    NÃO regera texto. Reusa o chart guardado, então mandala, tabela de
    posições, painel de elementos e índice saem IDÊNTICOS — só o texto
    muda. É o único degrau sem substituto humano: quando o relatório
    resiste à regeneração, alguém tem de editar e mandar.

    Corpo: {id, markdown, forcar?}
    """
    import hmac
    body = request.get_json(silent=True) or {}
    presented = request.headers.get("X-API-Key") or body.pop("api_key", "")
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    tid = (body.get("id") or "").strip()
    markdown = body.get("markdown") or ""
    if not tid or not markdown.strip():
        return jsonify({"status": "error",
                        "message": "Informe 'id' e 'markdown'."}), 400

    f, err = _fila_ou_erro()
    if err:
        return err
    t = f.buscar(tid)
    if not t:
        return jsonify({"status": "error",
                        "message": f"Trabalho {tid!r} não encontrado."}), 404
    chart = t.get("chart") or {}
    if not chart.get("points"):
        return jsonify({"status": "error",
                        "message": "O trabalho não tem chart guardado — não "
                                   "dá para remontar sem ele."}), 409

    # GUARDA CONTRA COLAGEM ERRADA. A Márcia é a autoridade sobre o texto,
    # então isto NÃO bloqueia por padrão — mas colar o markdown do
    # relatório errado é um acidente barato de detectar e caro de descobrir
    # depois. Com `forcar: true` ela passa por cima.
    divergencia = None
    try:
        import revisao_lingua as _rl
        divergencia = _rl.divergencia_de_invariante(t.get("markdown") or "",
                                                    markdown)
    except Exception:
        pass
    if divergencia and not body.get("forcar"):
        return jsonify({
            "status": "error", "code": "invariante_divergente",
            "message": ("O markdown enviado tem conteúdo astrológico "
                        "diferente do original. Se a edição foi intencional, "
                        "reenvie com \"forcar\": true."),
            "divergencia": divergencia}), 409

    payload = t.get("payload") or {}
    corpo = dict(chart)
    corpo.setdefault("name", t.get("nome") or chart.get("name"))
    _pdf_lint = []
    try:
        _svg, _svg_err = _generate_chart_svg(corpo)
    except Exception as exc:
        _svg, _svg_err = None, str(exc)
    try:
        pdf_bytes = pg.generate_pdf(
            report_text=markdown,
            client_name=corpo.get("name") or "",
            birth_date=payload.get("birth_date", ""),
            birth_place=payload.get("birth_city", ""),
            birth_time=payload.get("birth_time", ""),
            latitude=chart.get("latitude"), longitude=chart.get("longitude"),
            chart_image_url=_svg or "",
            aspects=chart.get("aspects", []), points=chart.get("points", {}),
            lint_out=_pdf_lint)
    except Exception as exc:
        logger.exception("remontar-pdf: generate_pdf falhou")
        return jsonify({"status": "error",
                        "message": f"Falha ao montar o PDF: {exc}"}), 500

    destinatario = body.get("email") or t.get("email")
    enviado, erro_email = False, None
    if destinatario:
        try:
            r = send_report_email(destinatario, corpo.get("name") or "",
                                  pdf_bytes, payload.get("birth_date", ""))
            enviado = (r is True)
            if not enviado:
                erro_email = str(r)
        except Exception as exc:
            erro_email = str(exc)
    _registra_geracao(nome=corpo.get("name"), desfecho="remontado_a_mao",
                      email_enviado=enviado)
    return jsonify({
        "status": "success", "id": tid, "email_enviado": enviado,
        "email_erro": erro_email, "pdf_bytes": len(pdf_bytes),
        "pdf_lint": _pdf_lint, "svg_erro": _svg_err,
        "invariante_divergente": divergencia,
    }), 200


@app.route("/buscar-cidade", methods=["GET", "POST"])
def buscar_cidade_endpoint():
    """Opções de cidade para o formulário. Consome: ?q=santa+rosa

    Devolve até 6 opções com `id` autocontido e assinado. O formulário
    manda o `id` escolhido em `city_id` na geração — o servidor não
    geocodifica de novo.
    """
    import hmac
    _q = request.args.get("q")
    if _q is None and request.method == "POST":
        _b = request.get_json(silent=True) or {}
        _q = _b.get("q")
        _k = _b.get("api_key")
    else:
        _k = None
    presented = request.headers.get("X-API-Key") or _k or request.args.get("api_key") or ""
    if not API_SECRET_KEY or not presented \
            or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    if not _q or len(_q.strip()) < 3:
        return jsonify({"status": "ok", "opcoes": []}), 200
    ops, err = buscar_cidades(_q)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    return jsonify({"status": "ok", "q": _q, "opcoes": ops}), 200


@app.route("/health", methods=["GET"])
def health():
    """Lightweight liveness check for Railway.

    `commit` expõe o SHA que o Railway realmente buildou
    (RAILWAY_GIT_COMMIT_SHA, injetado pela plataforma). É a prova contra o
    deploy fantasma: comparar com `git rev-parse HEAD` local em vez de
    confiar que o push virou deploy. Não é segredo — o repo é público."""
    return jsonify({
        "status": "ok",
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    }), 200


# =============================================================
# report_for — RECONHECER O RÓTULO, NÃO SÓ A CHAVE (19/07)
#
# O Wix Forms não separa rótulo de valor: o que a cliente lê é o que é
# enviado. Antes disso a API tinha um mapa de chaves curtas e um DEFAULT
# SILENCIOSO para "a" — qualquer valor não reconhecido virava segunda
# pessoa. "Para presentear alguém" produziria um relatório em "você"
# quando deveria falar da pessoa em terceira. Sem erro e sem alerta.
#
# A tradução fica AQUI, e não na automação do Wix, porque na automação ela
# desalinha em silêncio: no dia em que o rótulo for reescrito para melhorar
# a redação, a automação segue traduzindo o texto antigo.
#
# Casamento por PALAVRA-CHAVE sobre o texto normalizado (sem acento, sem
# caixa), para sobreviver a reescrita de rótulo.
_MODE_CHAVES = {
    "": "a", "a": "a", "meu": "a", "para_mim": "a", "self": "a",
    "b": "b", "presente": "b", "para_ela": "b", "gift": "b",
    "c": "c", "sobre_outro": "c", "sobre_outra_pessoa": "c",
    "para_eu_ler": "c", "about_other": "c",
}

# ORDEM IMPORTA: (c) é testada antes de (b) porque "para outra pessoa, para
# eu ler" contém as marcas das duas, e a distintiva é quem LÊ.
_MODE_PADROES = [
    ("c", r"eu\s+(?:vou\s+)?ler|para\s+mim\s+ler|sobre\s+(?:outr|algu)|"
          r"presente(?:ar)?.*\bpara\s+eu\b|entender\s+(?:melhor\s+)?(?:outr|algu)"),
    ("b", r"presente|presentear|de\s+outra\s+pessoa|para\s+outra\s+pessoa|"
          r"para\s+(?:ela|ele)\s+ler|dar\s+de\s+presente|para\s+algu[eé]m"),
    ("a", r"\bmeu\b|\bminha\b|para\s+mim|de\s+mim|sobre\s+mim|"
          r"o\s+meu\s+mapa|eu\s+mesm[ao]"),
]


def _resolver_report_for(bruto):
    """(modo, incerto). `incerto` True quando o valor veio preenchido e
    NÃO foi reconhecido — aí quem chama decide, em vez de assumir."""
    import re
    import unicodedata
    if not bruto:
        return "a", False
    chave = bruto.strip().lower()
    if chave in _MODE_CHAVES:
        return _MODE_CHAVES[chave], False
    norm = "".join(c for c in unicodedata.normalize("NFD", chave)
                   if unicodedata.category(c) != "Mn")
    norm = re.sub(r"\s+", " ", norm)
    for modo, padrao in _MODE_PADROES:
        if re.search(padrao, norm):
            return modo, False
    return "a", True


def _host_do_dsn(dsn):
    """Host do DSN, sem usuário nem senha. Diagnóstico não vaza segredo."""
    if not dsn:
        return "(unset)"
    try:
        corpo = dsn.split("://", 1)[1]
        if "@" in corpo:
            corpo = corpo.split("@", 1)[1]
        return corpo.split("/", 1)[0]
    except Exception:
        return "(ilegível)"


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
        # Fila persistida. Só o SCHEME e o host, nunca a senha.
        "DATABASE_URL_set": bool(os.environ.get("DATABASE_URL")),
        "DATABASE_URL_scheme": (
            (os.environ.get("DATABASE_URL", "").split("://", 1) or [""])[0]
            or "(unset)"),
        "DATABASE_URL_host": _host_do_dsn(os.environ.get("DATABASE_URL", "")),
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





# ==================================================================
# CASCATA DE FILTRO DE ASPECTOS — FONTE ÚNICA (extraído 19/07).
#
# Quarta rotina de produção presa dentro de generate_report_endpoint.
# Consequência MEDIDA: a fixture aceitava 4 aspectos da Helena que produção
# DESCARTA (mercury-mars, mars-lilith, juno-mars por out_of_sign_dissociated;
# chiron-lilith por applying). Mente na direção perigosa: "Mercúrio quadratura
# Marte" passaria limpo na varredura local e seria acusado em produção. As
# outras três mentiras da fixture faziam ver defeito onde não havia; esta
# esconderia defeito real.
#
# unknown_birth_time era lido do closure por _normalize_applying — agora é
# parâmetro explícito.
# ==================================================================
def filter_aspects(raw_aspects, points, unknown_birth_time=False):
    """Aplica a cascata de orbes/regras. Devolve (kept, dropped)."""
    _raw_aspects = raw_aspects
    body = {"points": points}
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

    return kept, dropped

# ==================================================================
# REGRA DOS 5° — FONTE ÚNICA (extraído 19/07).
#
# Estava aninhada dentro de generate_report_endpoint, portanto invisível
# aos testes: a fixture entregava charts SEM `_house_moves`, e toda
# varredura local acusava a frase de fronteira que o prompt exige. Foi a
# terceira vez que o instrumento mentiu por reimplementar produção.
#
# Muta `points` no lugar (grava house_geometric, reatribui house) e
# devolve a lista de movimentos. R3: um cálculo, um lugar.
# ==================================================================
def apply_five_degree_rule(body, unknown_birth_time=False):
    """Aplica a regra dos 5° a body["points"]; devolve _house_moves."""
    _house_moves = []
    if not unknown_birth_time and body.get("cusps") and body.get("points"):
        _SIGN_ORDER_5 = ["aries","taurus","gemini","cancer","leo","virgo",
                         "libra","scorpio","sagittarius","capricorn","aquarius","pisces"]
        def _abs5(d):
            try:
                return _SIGN_ORDER_5.index((d.get("sign") or "").lower())*30.0 + float(d.get("degrees"))
            except (ValueError, TypeError, AttributeError):
                return None
        _cusp_abs = {}
        for _n in range(1, 13):
            _c = body["cusps"].get(str(_n))
            _a = _abs5(_c) if _c else None
            if _a is not None:
                _cusp_abs[_n] = _a
        if len(_cusp_abs) == 12:
            for _pk, _pd in body["points"].items():
                _pos = _abs5(_pd)
                _h = _pd.get("house")
                # A casa GEOMÉTRICA (a que a mandala desenha) é preservada
                # ANTES de qualquer re-atribuição: a tabela de posições do PDF
                # mostra a geométrica, o texto lê a da regra dos 5°. Sem isto
                # a tabela herdaria a casa de leitura e contradiria o desenho.
                if _h:
                    _pd["house_geometric"] = _h
                if _pos is None or not _h:
                    continue
                _nxt = (_h % 12) + 1
                _gap = (_cusp_abs[_nxt] - _pos) % 360.0
                # CONDIÇÃO DE SIGNO (refinamento da Márcia, 17/07): a
                # fronteira de SIGNO barra a regra. O corpo só é lido na casa
                # seguinte se ele e a cúspide seguinte estiverem no MESMO
                # signo. Caso do erro: Juno em Gêmeos foi lida na casa 8 cuja
                # cúspide está em Câncer — regência diferente, leitura errada.
                # Fica na 7.
                _same_sign = (int(_pos // 30) == int(_cusp_abs[_nxt] // 30))
                if 0.0 < _gap < 5.0 and _same_sign:
                    _pd["house"] = _nxt
                    _house_moves.append({
                        "planet": _pk, "from_house": _h, "to_house": _nxt,
                        "gap_to_cusp": round(_gap, 2),
                    })
            # Passa adiante para o report_generator: o TEXTO nomeia a
            # fronteira ("na fronteira entre 7 e 8, com mais força na 8") em
            # vez de só afirmar a casa nova. Assim ele para de contradizer a
            # mandala, que desenha na casa geométrica.
            body["_house_moves"] = _house_moves
            if _house_moves:
                logger.info("regra dos 5°: %d corpo(s) re-atribuído(s): %s",
                            len(_house_moves),
                            [f"{m['planet']} {m['from_house']}→{m['to_house']}" for m in _house_moves])
    return _house_moves

# ==================================================================
# ASPECTOS DE ASTERÓIDES E NODOS — FONTE ÚNICA (extraído 19/07).
#
# Isto vivia ANINHADO dentro de generate_report_endpoint, portanto
# inalcançável por qualquer teste. A fixture de testes então computava
# aspectos por conta própria, via NatalAspects do Kerykeion — que NÃO
# gera aspectos de asteróides. Resultado: a fixture dizia que Juno da
# Helena não tinha aspecto nenhum, e uma varredura local acusou
# 'mercúrio quadratura Juno' como inventada. Ela é real: orbe 0,4°.
# O defeito era do instrumento, não do produto.
#
# Regra R3: um cálculo, um lugar. Testes importam ESTA função.
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

@app.route("/revisar-texto", methods=["POST"])
def revisar_texto_endpoint():
    """MEDIÇÃO da passada de revisão sobre TEXTO QUE JÁ EXISTE (19/07).

    Por que existe. A medição óbvia — gerar de novo com a passada ligada —
    NÃO mede o que a Márcia quer: gerar produz texto NOVO, com malformações
    diferentes. Os defeitos que queremos testar estão nos relatórios ATUAIS.
    Este caminho recebe o texto pronto e devolve o revisado, então a
    comparação é direta: destes defeitos, quantos ela pega.

    Body: {"api_key", "texto", "genero", "granularidade"}
      granularidade: "frase" | "paragrafo" (padrão) | "secao"

    Não gera PDF, não manda e-mail, não toca em nada do fluxo do cliente.
    """
    import hmac
    import time as _t
    presented = request.headers.get("X-API-Key", "")
    body = request.get_json(silent=True) or {}
    if isinstance(body, dict) and set(body.keys()) == {"data"} and isinstance(body["data"], dict):
        body = body["data"]
    presented = presented or body.pop("api_key", "")
    if not API_SECRET_KEY or not hmac.compare_digest(presented, API_SECRET_KEY):
        return jsonify({"error": "unauthorized"}), 401

    texto = body.get("texto") or ""
    if not texto.strip():
        return jsonify({"status": "error", "message": "campo 'texto' vazio"}), 400
    genero = body.get("genero") or "feminino"
    gran = body.get("granularidade") or "paragrafo"
    if gran not in ("frase", "paragrafo", "secao"):
        return jsonify({"status": "error",
                        "message": "granularidade: frase | paragrafo | secao"}), 400
    modo = body.get("modo") or "revisar"     # "revisar" | "detectar"
    try:
        import revisao_lingua as rl
        import report_generator as rg
        rg.init_clients()
        t0 = _t.time()
        if modo == "detectar":
            # FLAG-ONLY: aponta frases sem sentido, não altera nada.
            achados = rl.detectar_sem_sentido(
                texto, call_claude_fn=rg.call_claude, granularidade=gran)
            _p, _i = rl._fatiar(texto, gran)
            return jsonify({
                "status": "ok", "modo": "detectar", "granularidade": gran,
                "chamadas": sum(1 for i in _i if len(_p[i].strip()) >= 40),
                "segundos": round(_t.time() - t0, 1),
                "achados": achados,
            }), 200
        _partes, _idx = rl._fatiar(texto, gran)
        n_chamadas = sum(1 for i in _idx if len(_partes[i].strip()) >= 40)
        revisado, log = rl.revisar_texto(
            texto, genero=genero, call_claude_fn=rg.call_claude,
            granularidade=gran)
        return jsonify({
            "status": "ok",
            "granularidade": gran,
            "chamadas": n_chamadas,
            "segundos": round(_t.time() - t0, 1),
            "alterados": sum(1 for x in log if x["status"] == "revisado"),
            "recusados": sum(1 for x in log if x["status"] == "recusado"),
            "rejeitados_invariante": sum(1 for x in log
                                         if x["status"] == "rejeitado_invariante"),
            "erros": sum(1 for x in log if x["status"] == "erro_chamada"),
            "log": log[:80],
            "texto": revisado,
        }), 200
    except Exception as e:
        logger.exception("revisar-texto failed")
        return jsonify({"status": "error", "message": str(e)[:300]}), 500


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

    # Segredo com caractere impossível em cabeçalho: recusa AQUI, com o
    # nome da variável, em vez de morrer 16 chamadas depois dentro do SDK.
    _malf = chaves_malformadas()
    if _malf:
        logger.error("CHAVE MALFORMADA: %s", [m["detalhe"] for m in _malf])
        return jsonify({
            "status": "error", "code": "chave_malformada",
            "message": "Variável de ambiente malformada no servidor.",
            "detalhes": _malf,
        }), 500

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
        str(body.get("birth_date") or "")[:20],
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

    # ==================================================================
    # CAMINHO ASSÍNCRONO — 202 + fila, em vez de segurar a conexão.
    #
    # Por que existe: a geração mediu 129s mínimo, 216s mediana, 272s
    # máximo. O proxy do Railway corta em 300s. A mediana já está a um
    # terço do teto e a máxima a 28s dele — não é margem, é sorte. (O
    # e-mail SAI mesmo com a conexão cortada, isso está provado; o que se
    # perde é a resposta, e com ela a única forma de o formulário saber
    # o que aconteceu.)
    #
    # LIGADO POR VARIÁVEL, não por deploy: `FILA_ASSINCRONA=1`. Enquanto
    # estiver desligada, o caminho da cliente é EXATAMENTE o de hoje —
    # geração na própria requisição. Assim o assíncrono só entra quando o
    # worker estiver de pé, e volta atrás mudando uma variável, sem push.
    # `assincrono` no corpo força um dos dois lados para teste.
    _async = body.pop("assincrono", None)
    if _async is None:
        _async = FILA_ASSINCRONA
    if _async:
        f, err = _fila_ou_erro()
        if err:
            # NÃO cai para o síncrono em silêncio: se a fila está fora,
            # a Márcia precisa saber, e não descobrir por um relatório
            # que demorou 4 minutos quando devia ter voltado em 1s.
            return err
        body["_ctx"] = {"ip": _client_ip, "ua": _ua}
        tid = f.enfileirar(body, nome=body.get("name"), email=body.get("email"))
        logger.info("ENFILEIRADO %s name=%r email=%r", tid,
                    (body.get("name") or "")[:60], _email_norm[:60])
        # FILA PARADA — ninguém está consumindo (11/08).
        #
        # Aconteceu de verdade: o serviço worker subiu com o comando
        # herdado do railway.json da raiz (`gunicorn app:app`) e virou uma
        # SEGUNDA CÓPIA DA API. Verde no painel, healthcheck passando, e
        # nunca tocou na fila. Dois trabalhos presos 2,8 dias.
        #
        # `retomar_orfaos` não cobre isto: ele conserta worker que morreu
        # NO MEIO do trabalho. Worker que nunca existiu deixa os pedidos em
        # PENDENTE, sem heartbeat, invisíveis para ele por definição.
        #
        # A checagem mora aqui, no caminho web, porque o worker é
        # justamente quem não está de pé — um vigia hospedado no vigiado
        # não vigia nada. A dedupe do _send_failure_alert é desejável aqui:
        # uma rajada de pedidos não merece uma rajada de e-mails.
        try:
            _espera = f.espera_do_mais_antigo()
            if _espera is not None and _espera > FILA_PARADA_SEGS:
                logger.error("FILA PARADA: o pedido mais antigo espera há "
                             "%.0f min", _espera / 60)
                _send_failure_alert(
                    "fila_parada",
                    RuntimeError(
                        f"o pedido PENDENTE mais antigo está esperando há "
                        f"{_espera / 60:.0f} minutos — nenhum worker está "
                        f"consumindo. Confira se o serviço worker está com o "
                        f"comando 'python worker.py' (ver api/railway.worker.json): "
                        f"com o comando herdado da raiz ele sobe como uma "
                        f"segunda cópia da API e fica VERDE sem consumir nada."),
                    {"name": body.get("name"), "email": body.get("email"),
                     "birth_date": birth_date_raw,
                     "birth_city": body.get("birth_city"),
                     "ip": _client_ip, "ua": _ua})
        except Exception as _exc:                      # noqa: BLE001
            logger.warning("checagem de fila parada falhou: %s", _exc)
        return jsonify({
            "status": "accepted",
            "id": tid,
            "status_url": f"/status/{tid}",
            "message": ("Recebemos o seu pedido. O relatório leva alguns "
                        "minutos e chega por e-mail quando ficar pronto."),
        }), 202

    _corpo, _http = executar_geracao(body, {"ip": _client_ip, "ua": _ua})
    return jsonify(_corpo), _http


@app.route("/status/<tid>", methods=["GET"])
def status_trabalho_endpoint(tid):
    """Estado de um trabalho da fila.

    Autenticado: o markdown e o mapa da pessoa saem por aqui, e um id de
    16 hex não é credencial. Sem a chave, devolve só o estado — o
    bastante para um formulário mostrar 'ficou pronto' sem expor o texto.
    """
    import hmac
    presented = request.headers.get("X-API-Key") or request.args.get("api_key") or ""
    autorizado = bool(API_SECRET_KEY and presented
                      and hmac.compare_digest(presented, API_SECRET_KEY))
    f, err = _fila_ou_erro()
    if err:
        return err
    t = f.buscar((tid or "").strip())
    if not t:
        return jsonify({"status": "error",
                        "message": f"Trabalho {tid!r} não encontrado."}), 404
    corpo = {"status": "ok", "id": t["id"], "estado": t["estado"],
             "tentativas": t["tentativas"], "criado_em": t["criado_em"],
             "atualizado_em": t["atualizado_em"]}
    if autorizado:
        corpo.update({
            "motivo_falha": t.get("motivo_falha"),
            "markdown": t.get("markdown"),
            "meta": t.get("meta"),
            "tem_chart": bool((t.get("chart") or {}).get("points")),
            "nome": t.get("nome"), "email": t.get("email"),
        })
    return jsonify(corpo), 200


def executar_geracao(body, ctx=None):
    """NÚCLEO DA GERAÇÃO — a ÚNICA implementação, chamada por dois caminhos.

    Extraído do endpoint em 19/07. Motivo: o worker da fila e o
    /generate-report precisam gerar EXATAMENTE o mesmo relatório. Duas
    implementações seria a R3 — a classe de defeito em que o teste e o
    produto divergem em silêncio e o teste passa enquanto o produto erra.

    O corte é ADMISSÃO × GERAÇÃO. Fica de fora, no endpoint, tudo que
    depende do `request` HTTP e que a fila já resolveu no enfileiramento:
    autenticação, variáveis de ambiente, forma do corpo e limite de taxa.
    Se o limite entrasse aqui, o worker contaria a mesma pessoa duas vezes
    — uma ao enfileirar e outra ao gerar.

    `body` é MUTADO no lugar: ao voltar, carrega points/ascendant/aspects/
    cusps já calculados. É desse dicionário que a fila guarda o `chart`.

    ctx: {"ip", "ua"} — só para log e alerta. O worker passa o que foi
    registrado no enfileiramento, não o IP de quem chamou o worker.

    Devolve `(corpo, http)` — o mesmo par que o Flask espera. Quem chama
    decide se serializa (endpoint) ou traduz para a fila (worker, via
    executar_geracao_para_fila).
    """
    ctx = ctx or {}
    _client_ip = ctx.get("ip", "?")
    _ua = ctx.get("ua", "?")
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
        return ({
            "status": "error",
            "message": parsed_birth["error"],
        }), parsed_birth["code"]

    body["datetime"] = parsed_birth["datetime"]
    birth_date_display = parsed_birth["display"]
    time_estimated = parsed_birth["time_estimated"]
    unknown_time_note = parsed_birth.get("unknown_time_note", "")

    # Geocode birth_city → (lat, lng, IANA tz name). Always geocoded fresh
    # from the city string; any latitude/longitude/timezone the caller may
    # still be sending in the body is ignored so we have a single source of
    # truth. Historical-DST correctness is guaranteed by passing the zone
    # NAME to Kerykeion, which resolves the offset at the birth date.
    birth_city = body.get("birth_city")

    # CAMINHO PREFERIDO: o formulário mandou o city_id que a pessoa
    # ESCOLHEU em /buscar-cidade. O servidor NÃO geocodifica de novo —
    # assim não há chance de o que ela escolheu divergir do que o servidor
    # resolve. O id é autocontido e assinado.
    _city_id = body.get("city_id")
    _escolhida = _desempacota_cidade(_city_id) if _city_id else None
    if _city_id and not _escolhida:
        logger.warning("city_id inválido ou adulterado")
        return ({"status": "error",
                        "message": "Identificador de cidade inválido. "
                                   "Selecione a cidade novamente."}), 400
    if _escolhida:
        lat, lng, tz_str, birth_city = _escolhida
        body["birth_city"] = birth_city
        geo_error = None
    else:
        lat, lng, tz_str, geo_error = _geocode_birth_city(birth_city)

    if geo_error:
        # BURACO CORRIGIDO (19/07): isto era um `return` limpo, então o
        # alerta nunca disparava. O cliente pagava, via o erro, e a Márcia
        # não ficava sabendo.
        _send_failure_alert("geocode_nao_encontrado",
                            RuntimeError(geo_error[:300]),
                            {"name": body.get("name"), "email": body.get("email"),
                             "birth_date": birth_date_raw,
                             "birth_city": birth_city,
                             "ip": _client_ip, "ua": _ua})
        return ({"status": "error", "message": geo_error}), 400

    # AMBIGUIDADE REAL — recusa em vez de escolher em silêncio.
    # "Santa Rosa" resolve para a Califórnia; Santa Rosa (RS) é a terceira
    # opção. Mapa errado com aparência perfeita é pior que recusa visível —
    # mesma lógica da falha fechada de língua (decisão da Márcia, 19/07).
    if not _escolhida:
        _ops, _err_busca = buscar_cidades(birth_city)
        _amb = _ambiguidade_real(_ops) if _ops else None
        if _amb:
            logger.warning("cidade AMBÍGUA recusada: %r → %s", birth_city, _amb)
            _send_failure_alert(
                "cidade_ambigua", RuntimeError(f"{birth_city!r}: {_amb}"),
                {"name": body.get("name"), "email": body.get("email"),
                 "birth_date": birth_date_raw, "birth_city": birth_city,
                 "ip": _client_ip, "ua": _ua,
                 "opcoes": [o["rotulo"] for o in _ops[:6]]})
            return ({
                "status": "error", "code": "cidade_ambigua",
                "message": ("Encontramos mais de uma cidade com esse nome. "
                            "Responda este e-mail informando o estado e o "
                            "país de nascimento e eu gero o seu relatório."),
                "opcoes": [{"rotulo": o["rotulo"], "id": o["id"]}
                           for o in _ops[:6]],
            }), 422

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
            return ({
                "status": "error",
                "message": f"Failed to compute chart from birth data: {e}",
            }), 400

    # Validate required fields up front (clearer 400 than a deep stack later)
    for required in ("gender", "points", "ascendant", "aspects"):
        if required not in body:
            return ({
                "status": "error",
                "message": f"Chart JSON missing required field: '{required}'",
            }), 400

    # ==================================================================
    # VOZ E IDADE — dois interruptores DESACOPLADOS (decisão da Márcia 17/07)
    #
    # VOZ (formulário, campo report_for):
    #   (a) "meu"          → segunda pessoa (padrão de hoje)
    #   (b) "presente"     → de outra pessoa, para ELA ler → segunda pessoa
    #   (c) "sobre_outro"  → de outra pessoa, para o REMETENTE ler → terceira
    # Campo opcional `relationship` (filho, neta, esposa…) — usável na voz
    # (c); sem ele, nome puro e NUNCA se assume parentesco.
    #
    # CONTEÚDO (idade, derivada da data de nascimento): criança ≤12 /
    # adolescente 13–17 / adulto ≥18. As regras por seção estão pendentes
    # (a Márcia define sobre a lista); por ora só a faixa é computada e
    # exposta no meta.
    #
    # TRAVA: sujeito MENOR (<18) força a voz (c) — terceira pessoa para o
    # responsável — independente do que o formulário disser.
    # ==================================================================
    _raw_for = str(body.pop("report_for", "") or "").strip()
    # PARENTESCO DESLIGADO (decisão da Márcia, 11/08): cortado do
    # formulário porque complicava mais do que ajudava. O modo (c) usa
    # SEMPRE só o nome do sujeito.
    #
    # O campo continua sendo ACEITO — um payload legado não pode virar 400
    # —, mas o valor é DESCARTADO. Antes ele era honrado: um `relationship`
    # sobrando num mapeamento velho do Wix faria o relatório dizer "sua
    # filha Helena" e desfaria a decisão sem ninguém ver. É a mesma família
    # do rótulo que dessincroniza quando o formulário muda e a tradução
    # não. Decisão registrada em código vale mais que decisão registrada
    # só na cabeça.
    _rel_bruto = str(body.pop("relationship", "") or "").strip().lower()[:40]
    _relationship = ""
    if _rel_bruto:
        # Não recusa: o relatório sai certo, só sem o parentesco. Mas
        # avisa, porque significa que ALGO ainda está mandando o campo —
        # e é isso que a Márcia precisa saber para ir consertar na origem.
        logger.warning("relationship=%r recebido e DESCARTADO — o campo foi "
                       "cortado do formulário em 11/08", _rel_bruto)
        _send_failure_alert(
            "relationship_descartado",
            RuntimeError(f"relationship={_rel_bruto!r} chegou à API depois de "
                         f"o campo ter sido cortado do formulário; o valor foi "
                         f"ignorado e o relatório saiu só com o nome"),
            {"name": body.get("name"), "email": body.get("email"),
             "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
             "ip": _client_ip, "ua": _ua})
    _mode, _mode_incerto = _resolver_report_for(_raw_for)
    if _mode_incerto:
        # NÃO recusa: voz errada é defeito visível e corrigível, não um
        # mapa de outra pessoa — a recusa aqui custaria mais do que
        # protege. Mas TAMBÉM não fica em silêncio, que era o defeito
        # antigo: o valor não reconhecido virava "meu" sem ninguém saber.
        logger.warning("report_for NÃO RECONHECIDO: %r — assumindo 'meu'", _raw_for)
        _send_failure_alert(
            "report_for_nao_reconhecido",
            RuntimeError(f"report_for={_raw_for!r} não casou nenhum padrão; "
                         f"o relatório saiu em SEGUNDA pessoa por padrão"),
            {"name": body.get("name"), "email": body.get("email"),
             "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
             "ip": _client_ip, "ua": _ua, "report_for": _raw_for})

    _age = None
    try:
        from datetime import date as _date_cls
        _age = (_date_cls.today() - _dt_obj.date()).days // 365
    except Exception:
        pass
    _age_bracket = (None if _age is None else
                    "crianca" if _age <= 12 else
                    "adolescente" if _age < 18 else "adulto")

    # TRAVA DE IDADE (mudou em 17/07, tarde): menor de 18 NÃO GERA.
    # Não existe versão para menores — "mapa infantil" é disciplina própria
    # que a Márcia não oferece; o produto é leitura de autoconhecimento
    # para adultos. Fail-closed: recusa clara + alerta para o executivo@
    # (tratamento de reembolso se algum pagamento escapar do bloqueio do
    # Wix — o formulário valida ANTES de cobrar; a API é a rede final).
    #
    # BYPASS INTERNO DE QA: a restrição é de venda, não de laboratório.
    # Mesmo mecanismo da isenção de rate limit (e-mails internos): o Lucca
    # segue como mapa de teste — as frases defeituosas catalogadas estão no
    # relatório dele, e a regeneração dele prova que as correções mordem.
    _age_gate_bypassed = False
    if _age is not None and _age < 18:
        _req_email = (body.get("email") or "").strip().lower()
        if _req_email in _RATE_EXEMPT_EMAILS:
            _age_gate_bypassed = True
            logger.info("age gate: sujeito menor (%d anos) — BYPASS interno de QA (%s)",
                        _age, _req_email)
        else:
            logger.warning("age gate: sujeito menor (%d anos) — geração recusada", _age)
            _send_failure_alert("age_gate_refusal",
                               ValueError(f"sujeito menor de 18 ({_age} anos)"), {
                "name": body.get("name"), "email": body.get("email"),
                "birth_date": body.get("birth_date") or body.get("datetime"),
                "birth_city": body.get("birth_city"),
                "ip": _client_ip, "ua": _ua,
            })
            return ({
                "status": "refused",
                "reason": "underage_subject",
                "message": (
                    "Esta leitura de mapa natal é um trabalho de "
                    "autoconhecimento para maiores de 18 anos. Não geramos "
                    "relatórios para menores. Se houve um pagamento, entre "
                    "em contato para o reembolso: executivo@marciafervienza.com."
                ),
            }), 403

    # A checagem parentesco × gênero do sujeito saiu junto (11/08). Com o
    # parentesco desligado, `_relationship` é SEMPRE vazio e a condição
    # nunca podia ser verdadeira: era salvaguarda morta, que é pior que
    # salvaguarda ausente — o log diz que está protegido. O que sobrou no
    # lugar é o rastro do valor descartado, em `relationship_descartado`.

    body["_voice"] = {
        "person": "terceira" if _mode == "c" else "segunda",
        "mode": _mode,
        "name": (body.get("name") or "").strip(),
        "relationship": _relationship,
        "age": _age,
        "age_bracket": _age_bracket,          # registro; não muda conteúdo
        "age_gate_bypassed": _age_gate_bypassed,   # True só em QA interno
    }

    # Sinalizar ao report_generator: hora desconhecida + info de ingresso lunar.
    # Essas chaves com underscore são consumidas em report_generator.py para
    # reformular seções que dependem de hora (abertura/triade/lua/casa_4) e para
    # inserir o disclaimer no topo do relatório. Não vão para a resposta.
    body["_unknown_birth_time"] = unknown_birth_time
    body["_moon_meta"] = moon_meta

    # ==================================================================
    # REGRA DOS 5° (regra de LEITURA, decisão da Marcia 16/07):
    # um planeta a menos de 5° da cúspide da casa seguinte é LIDO na casa
    # seguinte. Vale para as 12 cúspides, incluindo ângulos (corpo a <5°
    # do ASC lê na casa 1) — assunção da Marcia, a corrigir se preciso.
    #
    # É interpretativa, não visual: a MANDALA não muda (o wheel desenha na
    # longitude real, do lado real da cúspide — caminho separado, direto do
    # subject Kerykeion). O que muda é points[*]["house"], a fonte única que
    # alimenta seções, clusters parentais, queries do RAG e partial_coverage
    # — ajustando aqui, todas as camadas downstream leem a MESMA casa e não
    # há verifier para brigar (a validação de cúspide confere signo de
    # cúspide, não casa de planeta; não é afetada).
    #
    # Precisão: posições vêm de sign+degrees arredondados a 0.1° — um corpo
    # a exatamente 5.0° da cúspide pode cair dos dois lados do limiar. A
    # regra usa < 5.0 estrito. Requer cusps; sem hora não há casas nem regra.
    # ==================================================================
    _house_moves = apply_five_degree_rule(body, unknown_birth_time)

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
    # (extraído para o nível do módulo — ver _compute_missing_aspects acima)

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
    # ENCANAMENTO DE LÍNGUA — LIGADO POR PADRÃO (19/07, decisão da Márcia).
    #
    # Medido contra o texto CRU dos 5 mapas de QA: ~1 defeito de língua por
    # relatório, todos na classe que só leitura alcança ("responcer", frase
    # que não fecha, referente ausente). O detector é FLAG-ONLY e não
    # corrompe; a correção é REGENERAR a seção, que produz texto novo; e a
    # falha fechada protege o cliente se não limpar.
    #
    # `revisao_lingua: false` no corpo desliga, para diagnóstico.
    body["_revisao_lingua"] = body.get("revisao_lingua", True) is not False
    # Captura por estágio para a medição de ONDE os defeitos surgem.
    body["_debug_estagios"] = bool(body.get("debug_estagios"))
    # Teto de tentativas do encanamento, exposto para medir se 3 é o número
    # certo. Faixa 1..6 — acima disso o custo em tempo estoura o proxy.
    try:
        _tent = int(body.get("lingua_tentativas") or 3)
        body["_lingua_tentativas"] = max(1, min(6, _tent))
    except Exception:
        body["_lingua_tentativas"] = 3

    kept, dropped = filter_aspects(
        _raw_aspects, body.get('points') or {}, unknown_birth_time)
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
    except UnicodeError as e:
        # NÃO É ERRO DA CLIENTE (11/08). UnicodeEncodeError é subclasse de
        # ValueError, então o `except ValueError` abaixo — que existe para
        # entrada inválida e devolve 400 com a mensagem crua — engolia
        # falhas de CODIFICAÇÃO e as reportava como se a cliente tivesse
        # errado. Os três primeiros trabalhos do worker falharam assim:
        # "'ascii' codec can't encode character '”'", registrado na
        # fila como 400, sem traceback, sem alerta. Uma hora de diagnóstico
        # olhando para o lugar errado.
        #
        # Cai no caminho de 500: traceback completo, alerta, e a Márcia
        # sabe que é defeito do servidor e não da pessoa que preencheu.
        logger.exception("erro de codificação na geração")
        _send_failure_alert("generate_report_unicode", e, {
            "name": body.get("name"), "email": body.get("email"),
            "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
            "ip": _client_ip, "ua": _ua,
        })
        return ({
            "status": "error",
            "message": f"Erro de codificação no servidor: {e}",
            "trace": traceback.format_exc(),
        }), 500
    except ValueError as e:
        return ({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("generate_report failed")
        _send_failure_alert("generate_report", e, {
            "name": body.get("name"), "email": body.get("email"),
            "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
            "ip": _client_ip, "ua": _ua,
        })
        return ({
            "status": "error",
            "message": f"Generation failed: {e}",
            "trace": traceback.format_exc() if app.debug else None,
        }), 500

    # Inject the appropriate Moon note into the report based on the ingress
    # / cusp analysis. Runs against the returned report text before it goes
    # into the PDF, so both the PDF and the response's report field carry
    # the note. Never raises — falls back to the original text on error.
    # ============================================================
    # FALHA FECHADA DE LÍNGUA (19/07, decisão da Márcia)
    #
    # "Prefiro gerar à mão do que mandar defeito." Se o encanamento de
    # língua não conseguiu limpar o relatório dentro do teto de tentativas,
    # ele NÃO SAI: sem PDF, sem e-mail para o cliente, e alerta para o
    # executivo@ com o mapa, a seção e a frase apontada.
    #
    # Vem ANTES de tudo o mais de propósito — nada é construído nem enviado
    # a partir de um texto que o próprio sistema já sabe que está quebrado.
    # BUG CORRIGIDO ANTES DE QUALQUER USO (19/07): a primeira versão lia
    # `result["meta"]["falha_lingua"]`. `generate_report` devolve
    # `falha_lingua` no TOPO do dicionário — não há chave "meta" ali. A
    # verificação lia None SEMPRE, e a falha fechada nunca disparava.
    # Detector de segurança morto é pior que ausente: o log diz que está
    # protegido. Foi a medição que expôs, não a leitura do código.
    _falha_lingua = result.get("falha_lingua")
    if _falha_lingua:
        _rl_log = result.get("revisao_lingua") or {}
        _pend = []
        for _r in (_rl_log.get("rodadas") or [])[-1:]:
            for _a in _r.get("achados", []):
                _sec = None
                try:
                    import revisao_lingua as _rlm
                    _loc = _rlm.secao_da_frase(result.get("report") or "",
                                               _a.get("frase", ""))
                    _sec = _loc[0] if _loc else None
                except Exception:
                    pass
                _pend.append({"secao": _sec, "frase": _a.get("frase", "")[:200],
                              "motivo": _a.get("motivo", "")[:160]})
        logger.error("FALHA FECHADA DE LÍNGUA: %s", _falha_lingua[:400])
        _send_failure_alert(
            "lingua_falha_fechada",
            RuntimeError(_falha_lingua[:400]),
            {"name": body.get("name"), "email": body.get("email"),
             "birth_date": birth_date_raw, "birth_city": body.get("birth_city"),
             "ip": _client_ip, "ua": _ua,
             "pendencias": _pend,
             "regeneracoes": _rl_log.get("regeneracoes"),
             "rodadas": len(_rl_log.get("rodadas") or [])})
        _registra_geracao(nome=body.get("name"),
                          desfecho="falha_fechada_lingua", email_enviado=False,
                          rodadas=len(_rl_log.get("rodadas") or []),
                          regeneracoes=_rl_log.get("regeneracoes"),
                          por_rodada=[len(r.get("achados", []))
                                      for r in (_rl_log.get("rodadas") or [])],
                          pendencias=[p.get("frase", "")[:110] for p in _pend])
        return ({
            "status": "error",
            "code": "lingua_falha_fechada",
            "message": ("O relatório não passou na verificação de língua e "
                        "não foi enviado. A Márcia foi avisada e vai gerar "
                        "este mapa manualmente."),
            # O MARKDOWN VAI JUNTO, mesmo recusado. É dele que o degrau 3
            # (POST /remontar-pdf) vive: a Márcia corrige a frase apontada
            # em `pendencias` e remonta o PDF sem regerar o texto. Sem esta
            # chave a fila guardaria markdown=None e a recusa custaria uma
            # geração inteira em vez de uma edição de uma frase.
            "report": result.get("report"),
            "meta": {"falha_lingua": _falha_lingua,
                     "pendencias": _pend,
                     "regeneracoes": _rl_log.get("regeneracoes"),
                     "rodadas": len(_rl_log.get("rodadas") or [])},
        }), 422

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
    # fora do try: precisa existir no jsonify mesmo se generate_pdf falhar
    _pdf_lint = []
    try:
        # Se o cliente não passou birth_place explícito, usar a cidade que
        # foi de fato geocoded — dá transparência sobre o que foi calculado.
        cover_place = birth_place or (body.get("birth_city") or "").strip()
        _birth_time_display = "" if unknown_birth_time else (body.get("birth_time") or "").strip()
        # Lint do artefato: asserção sobre o texto que o PDF de fato
        # renderiza (markdown vazado, frase colada). Vai no meta como
        # pdf_lint — o gate pré-testers exige lista vazia.
        pdf_bytes = pg.generate_pdf(
            report_text=result["report"],
            client_name=result["name"],
            birth_date=birth_date_display,
            birth_place=cover_place,
            birth_note=unknown_time_note,
            birth_time=_birth_time_display,
            latitude=body.get("latitude"),
            longitude=body.get("longitude"),
            chart_image_url=chart_svg_path,
            aspects=body.get("aspects", []),
            points=body.get("points", {}),
            time_unknown=unknown_birth_time,
            aspects_row_separators=bool(body.pop("aspects_row_separators", False)),
            lint_out=_pdf_lint,
        )
        if _pdf_lint:
            logger.warning("PDF_LINT %d violação(ões): %s", len(_pdf_lint),
                           [f"{v['kind']}@{v['section']}" for v in _pdf_lint])
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
                # Registra do lado do SERVIDOR: sobrevive à desconexão do
                # cliente, e é assim que se descobre se o e-mail saiu mesmo
                # quando o proxy já cortou a resposta.
                _rl_ok = result.get("revisao_lingua") or {}
                _registra_geracao(
                    nome=body.get("name"), desfecho="ok", email_enviado=True,
                    rodadas=len(_rl_ok.get("rodadas") or []),
                    regeneracoes=_rl_ok.get("regeneracoes"),
                    por_rodada=[len(r.get("achados", []))
                                for r in (_rl_ok.get("rodadas") or [])])
                logger.info("email sent to %s", recipient)
            else:
                email_error = send_result
                logger.warning("email to %s failed: %s", recipient, send_result)

    return ({
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
            # Lint do ARTEFATO (pdf_generator.lint_final_text): markdown
            # vazado e frases coladas no texto que o PDF renderiza. O gate
            # pré-testers exige [].
            "pdf_lint": _pdf_lint,
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
            # Regra dos 5° (leitura de casa): quais corpos foram re-atribuídos
            # à casa seguinte antes da síntese. A mandala não é afetada.
            "house_reading_moves": _house_moves,
            # Voz e idade (interruptores desacoplados): o que foi decidido
            # para esta geração, incluindo a trava de menor.
            "voice": {k: v for k, v in (body.get("_voice") or {}).items()},
            # Rastro do campo cortado do formulário (11/08). Não-nulo
            # significa que ALGO ainda está mandando `relationship` — o
            # valor foi ignorado, mas a origem precisa ser consertada.
            "relationship_descartado": _rel_bruto or None,
            "report_for_bruto": _raw_for,
            "report_for_reconhecido": not _mode_incerto,
            # Repetição quase-verbatim entre seções (janela de 12 palavras).
            # Gate pré-testers exige [].
            "stage_timings": result.get("stage_timings", {}),
            "repetition_lint": result.get("repetition_lint", []),
            # spell_lint em modo FLAG-ONLY: reporta palavras fora do
            # dicionário pt-BR + domain_lexicon.txt. Vira gate quando a
            # whitelist estabilizar sobre relatórios limpos.
            "spell_lint": result.get("spell_lint", []),
            "crutch_lint": result.get("crutch_lint", []),
            # Encanamento de língua: detectar → regenerar → redetectar.
            # `falha_lingua` não-nulo significa que o relatório NÃO SAIU.
            "revisao_lingua": result.get("revisao_lingua", {}),
            "estagios": result.get("estagios", {}),
            "falha_lingua": result.get("falha_lingua"),
            # Vocabulário rebuscado (flag-only): palavras de baixa frequência
            # para a Márcia triar. O que ela banir vai para o léxico.
            "rare_word_lint": result.get("rare_word_lint", []),
            "sign_divergences": result.get("sign_divergences", []),
            "correction_rewrites": result.get("correction_rewrites", []),
            "partial_coverage": result.get("partial_coverage", []),
            "verifier_log": result.get("verifier_log", []),
            # Remissão: dono do aspecto, direção temporal e teto (11/08).
            "remissao_lint": result.get("remissao_lint", []),
            "remissoes": result.get("remissoes", []),
            "aspectos_sem_dono": result.get("aspectos_sem_dono", []),
            # Prova de EXECUÇÃO do verificador (ran/error/contagens) — um
            # verifier_log vazio é ambíguo entre "0 violações" e "crashou".
            "verifier": result.get("verifier"),
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


# ======================================================================
# PONTE PARA A FILA — o que o worker chama.
#
# Nada aqui reimplementa geração: `executar_geracao_para_fila` só TRADUZ
# o par (corpo, http) do núcleo para o vocabulário da fila. Se um dia
# alguém for tentado a "ajustar só um detalhe para o worker", o ajuste
# tem de entrar no núcleo — é a única implementação, por decisão.
# ======================================================================

def _enviar_email_bruto(assunto, texto, destino=None):
    """Envio direto, SEM a dedupe de `_send_failure_alert`.

    A dedupe é certa no endpoint (uma exceção repetida em rajada não
    merece 40 e-mails) e ERRADA no worker: lá o alerta é o único canal, e
    dois trabalhos DIFERENTES que falham do mesmo jeito têm a mesma
    assinatura — a dedupe engoliria o segundo, que é justamente o cliente
    que ninguém mais veria. Devolve True/False; nunca levanta."""
    destino = destino or _ALERT_RECIPIENT
    if not SENDGRID_API_KEY or not EMAIL_FROM_ADDRESS:
        logger.warning("alerta não enviado: SendGrid/from não configurados")
        return False
    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            json={
                "personalizations": [{"to": [{"email": destino}]}],
                "from": {"email": EMAIL_FROM_ADDRESS,
                         "name": EMAIL_FROM_NAME or EMAIL_FROM_ADDRESS},
                "subject": assunto,
                "content": [{"type": "text/plain", "value": texto}],
            },
            headers={"Authorization": f"Bearer {SENDGRID_API_KEY}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        if 200 <= resp.status_code < 300:
            return True
        logger.warning("alerta falhou: HTTP %d %s", resp.status_code,
                       (resp.text or "")[:200])
        return False
    except Exception as exc:
        logger.warning("alerta levantou: %s", exc)
        return False


def _alerta_com_retry(assunto, texto, tentativas=4):
    """Alerta com backoff. No worker não há resposta HTTP para reportar
    erro — se o SendGrid estiver instável, uma tentativa só perde o aviso
    em silêncio, que é o modo de falha exato que a fila existe para
    fechar. O resumo diário é a rede DEPOIS desta."""
    espera = 2.0
    for i in range(tentativas):
        if _enviar_email_bruto(assunto, texto):
            if i:
                logger.info("alerta enviado na tentativa %d", i + 1)
            return True
        if i < tentativas - 1:
            _time.sleep(espera)
            espera *= 2
    logger.error("ALERTA PERDIDO após %d tentativas: %s", tentativas, assunto)
    return False


def alertar_falha_de_trabalho(tid, payload, resultado):
    """Um trabalho da fila falhou. Manda o que a Márcia precisa para
    decidir entre remontar (degrau 3) e gerar à mão."""
    payload = payload or {}
    resultado = resultado or {}
    _motivo = (resultado.get("erro") or resultado.get("falha_lingua")
               or "motivo não informado")
    _pend = ((resultado.get("meta") or {}).get("pendencias")) or []
    _linhas = [
        f"Trabalho {tid} FALHOU.",
        "",
        f"nome:       {payload.get('name', '?')}",
        f"email:      {payload.get('email', '?')}",
        f"nascimento: {payload.get('birth_date', '?')} {payload.get('birth_time', '')}",
        f"cidade:     {payload.get('birth_city', '?')}",
        f"http:       {resultado.get('http', '?')}",
        f"código:     {resultado.get('codigo') or '—'}",
        "",
        f"--- Motivo ---\n{str(_motivo)[:1200]}",
    ]
    if _pend:
        _linhas.append("\n--- Frases apontadas ---")
        for p in _pend[:12]:
            _linhas.append(f"  [{p.get('secao') or '?'}] {str(p.get('frase',''))[:180]}")
            if p.get("motivo"):
                _linhas.append(f"      motivo: {str(p['motivo'])[:140]}")
    if resultado.get("markdown"):
        _linhas.append(
            "\n--- Recuperação ---\n"
            "O texto FOI gerado e está guardado na fila. Para remontar o PDF "
            "depois de corrigir a frase, sem regerar nada:\n"
            f"  POST /remontar-pdf  {{\"id\": \"{tid}\", \"markdown\": \"<texto corrigido>\"}}\n"
            f"O markdown atual sai em GET /status/{tid}.")
    else:
        _linhas.append("\n--- Recuperação ---\n"
                       "NÃO há texto guardado (a falha veio antes da geração). "
                       "Este mapa precisa ser gerado do zero.")
    return _alerta_com_retry(
        f"[Mapa Natal] Trabalho {tid} falhou — {payload.get('name') or 'sem nome'}",
        "\n".join(_linhas))


RESUMO_INTERVALO_SEGS = 24 * 3600


def talvez_resumo_diario(fila, estado):
    """Resumo diário das falhas. É a rede DEPOIS do alerta: se um alerta
    se perdeu (SendGrid fora do ar durante as 4 tentativas), a falha
    reaparece aqui. Silêncio total nos dois canais é o que não pode
    acontecer — por isso o resumo sai MESMO com zero falhas: um resumo
    que só chega quando há problema é indistinguível de um resumo que
    parou de funcionar."""
    agora = _time.time()
    ultimo = estado.get("ultimo_resumo") or 0.0
    if ultimo and agora - ultimo < RESUMO_INTERVALO_SEGS:
        return False
    if not ultimo:
        # Primeira volta depois de subir: marca o relógio e NÃO manda,
        # senão todo redeploy do worker dispara um resumo.
        estado["ultimo_resumo"] = agora
        return False
    desde = agora - RESUMO_INTERVALO_SEGS
    try:
        falhas = fila.falhados_desde(desde)
        contagem = fila.contagem_por_estado()
    except Exception as exc:
        logger.warning("resumo diário não conseguiu ler a fila: %s", exc)
        return False
    _linhas = [f"Resumo das últimas 24h.", "",
               f"Fila agora: {contagem or '(vazia)'}", "",
               f"Falhas no período: {len(falhas)}"]
    for f in falhas[:40]:
        _linhas.append(f"  {f['id']}  {f.get('nome') or '?'}  "
                       f"<{f.get('email') or '?'}>  {str(f.get('motivo') or '')[:120]}")
    if not falhas:
        _linhas.append("  nenhuma.")
    estado["ultimo_resumo"] = agora
    return _alerta_com_retry(
        f"[Mapa Natal] Resumo 24h — {len(falhas)} falha(s)", "\n".join(_linhas))


def executar_geracao_para_fila(payload, ctx=None):
    """Adaptador do worker. Chama o MESMO núcleo e traduz o resultado.

    `chart` sai daqui porque `executar_geracao` muta o dicionário no
    lugar: ao voltar, ele carrega points/ascendant/aspects/cusps. É esse
    dicionário que a fila guarda, e é dele que o degrau 3 remonta o PDF
    (a tabela de aspectos e a mandala precisam do mapa, não só do texto).
    """
    chart = dict(payload or {})
    corpo, http = executar_geracao(chart, ctx or {})
    meta = corpo.get("meta") or {}
    return {
        "ok": http == 200,
        "http": http,
        "codigo": corpo.get("code"),
        # O TRACEBACK VAI JUNTO quando existe (11/08). Os três primeiros
        # trabalhos falharam com uma mensagem de uma linha e nenhum rastro:
        # "'ascii' codec can't encode character '”' in position 109". Sem
        # saber ONDE, a posição 109 não diz nada, e eu passei a diagnosticar
        # por eliminação. Motivo de falha sem rastro é motivo pela metade.
        "erro": None if http == 200 else (
            (corpo.get("message") or f"HTTP {http}")
            + (f"\n\n--- traceback ---\n{corpo['trace']}"
               if corpo.get("trace") else "")),
        # Presente inclusive na recusa por língua — ver o comentário na
        # falha fechada. Sem isto o degrau 3 não teria o que remontar.
        "markdown": corpo.get("report"),
        "chart": chart,
        "meta": meta,
        "falha_lingua": meta.get("falha_lingua"),
        "email_enviado": bool(meta.get("email_sent")),
    }


@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "service": "natal-report-generator",
        "endpoints": ["GET /health", "POST /generate-report"],
    }), 200


if __name__ == "__main__":
    # Local-only runner. Railway uses gunicorn via Procfile / startCommand.
    app.run(host="0.0.0.0", port=DEFAULT_PORT, debug=False)
