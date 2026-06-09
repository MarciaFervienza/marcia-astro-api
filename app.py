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

# AstroAPI for fetching the chart wheel SVG. Both are optional — if not set,
# the PDF still renders correctly without a chart wheel.
ASTROAPI_BASE_URL = os.environ.get("ASTROAPI_BASE_URL", "https://api.astroapi.cloud")
ASTROAPI_CHART_PATH = os.environ.get("ASTROAPI_CHART_PATH", "/v1/natal/charts")
ASTROAPI_TIMEOUT = float(os.environ.get("ASTROAPI_TIMEOUT", "30"))

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


def _fetch_chart_image_url(chart: dict):
    """
    Call AstroAPI to get a raster (PNG/JPEG) URL for the chart wheel.

    We deliberately ask for PNG so the PDF embedder can use it directly —
    no svglib / cairosvg / pycairo needed. AstroAPI variants differ; this
    helper tries the documented PNG variants in order of preference and
    falls back to whatever URL the server returned that points to a
    raster (URLs ending in .png or .jpg are accepted as a final fallback).

    Returns (image_url, error_message). On success: (url, ""). On failure: ("", reason).
    Never raises — a failure here downgrades gracefully to a PDF without
    the wheel, so the report still ships.
    """
    astroapi_key = os.environ.get("ASTROAPI_KEY", "").strip()
    if not astroapi_key:
        return "", "ASTROAPI_KEY not configured"

    url = ASTROAPI_BASE_URL.rstrip("/") + "/" + ASTROAPI_CHART_PATH.lstrip("/")
    headers = {
        "Authorization": f"Bearer {astroapi_key}",
        "Content-Type": "application/json",
    }
    # Ask for PNG explicitly. Different AstroAPI shapes support different
    # hints; we set them all and let the server pick what it understands.
    payload = dict(chart)
    payload.setdefault("format", "png")
    payload.setdefault("image_format", "png")
    payload.setdefault("output_format", "png")
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=ASTROAPI_TIMEOUT)
    except Exception as e:
        return "", f"AstroAPI request failed: {e}"

    if resp.status_code != 200:
        return "", f"AstroAPI returned HTTP {resp.status_code}"

    try:
        data = resp.json()
    except Exception:
        return "", "AstroAPI response was not valid JSON"

    # PNG-specific keys first (preferred)
    for key in ("png_url", "chart_png_url", "image_url", "chart_image_url"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip(), ""

    # Generic URL keys — accept only if the URL looks like a raster
    for key in ("chart_url", "url"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            low = v.strip().lower().split("?", 1)[0]
            if low.endswith((".png", ".jpg", ".jpeg", ".gif")):
                return v.strip(), ""

    # SVG-only response: not usable for our PNG-only embedder
    has_svg = any(
        isinstance(data.get(k), str) and data[k].strip()
        for k in ("svg_url", "chart_svg_url", "svg", "chart_svg")
    )
    if has_svg:
        return "", "AstroAPI returned only SVG; PDF will show aspects table without wheel"

    return "", f"AstroAPI response had no chart image URL (keys: {list(data.keys())[:6]})"


@app.route("/health", methods=["GET"])
def health():
    """Lightweight liveness check for Railway."""
    return jsonify({"status": "ok"}), 200


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

    # PDF-only metadata fields (free-form strings shown on the cover)
    birth_date = body.pop("birth_date", "") or ""
    birth_place = body.pop("birth_place", "") or ""

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

    # Fetch the chart-wheel PNG URL from AstroAPI (best-effort).
    # `body` at this point still contains the chart fields (gender, points, etc.)
    # so we can pass it directly.
    chart_image_url, image_error = _fetch_chart_image_url(body)

    # Render the branded PDF. Failures here should NOT poison the response —
    # the markdown report still has full value on its own.
    pdf_b64 = None
    pdf_error = None
    try:
        pdf_bytes = pg.generate_pdf(
            report_text=result["report"],
            client_name=result["name"],
            birth_date=birth_date,
            birth_place=birth_place,
            chart_image_url=chart_image_url,
            aspects=body.get("aspects", []),
            points=body.get("points", {}),
        )
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    except Exception as e:
        logger.exception("generate_pdf failed")
        pdf_error = str(e)

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
            "chart_image_fetched": bool(chart_image_url),
            "chart_image_error": image_error or None,
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
