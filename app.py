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
ASTROAPI_CHART_PATH = os.environ.get("ASTROAPI_CHART_PATH", "/api/chart/natal/png")
ASTROAPI_TIMEOUT = float(os.environ.get("ASTROAPI_TIMEOUT", "30"))
ASTROAPI_CHART_WIDTH = int(os.environ.get("ASTROAPI_CHART_WIDTH", "800"))
ASTROAPI_CHART_HEIGHT = int(os.environ.get("ASTROAPI_CHART_HEIGHT", "800"))

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
    Call AstroAPI's natal-PNG endpoint and persist the returned PNG to a
    tempfile.

    AstroAPI's /api/chart/natal/png endpoint returns the chart wheel image
    bytes DIRECTLY in the response body (not a JSON envelope with a URL
    inside). Auth is via the X-Api-Key header. The endpoint expects the
    raw chart inputs (datetime, lat/lon, timezone) — NOT the computed-points
    structure used by the rest of our pipeline. The Wix caller is responsible
    for forwarding those raw fields alongside the computed chart.

    Returns (path, error_message). On success: (local_path, "") — the path
    points at a tempfile that pdf_generator's _fetch_chart_image() can read
    directly. On failure: ("", reason). Never raises — failure here just
    means the PDF renders without the chart wheel.
    """
    astroapi_key = os.environ.get("ASTROAPI_KEY", "").strip()
    if not astroapi_key:
        return "", "ASTROAPI_KEY not configured"

    # AstroAPI's PNG renderer needs the raw birth inputs, not derived points.
    required = ("datetime", "latitude", "longitude", "timezone")
    missing = [f for f in required if chart.get(f) in (None, "")]
    if missing:
        return "", f"chart payload missing required fields for AstroAPI PNG: {missing}"

    url = ASTROAPI_BASE_URL.rstrip("/") + "/" + ASTROAPI_CHART_PATH.lstrip("/")
    # AstroAPI checks Referer/Origin against the key's allow-list. Set them
    # to the Railway service's public hostname so the call clears the policy.
    # Both headers can be overridden via env vars in case the Railway domain
    # changes or a custom domain is added.
    referer = os.environ.get("ASTROAPI_REFERER", "https://web-production-6c77f.up.railway.app")
    origin = os.environ.get("ASTROAPI_ORIGIN", referer)
    headers = {
        "X-Api-Key": astroapi_key,
        "Content-Type": "application/json",
        "Referer": referer,
        "Origin": origin,
    }
    payload = {
        "datetime": chart.get("datetime"),
        "latitude": chart.get("latitude"),
        "longitude": chart.get("longitude"),
        "timezone": chart.get("timezone"),
        "width": ASTROAPI_CHART_WIDTH,
        "height": ASTROAPI_CHART_HEIGHT,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=ASTROAPI_TIMEOUT)
    except Exception as e:
        return "", f"AstroAPI request failed: {e}"

    if resp.status_code != 200:
        # Try to surface AstroAPI's error message if it returned JSON
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error") or err
        except Exception:
            msg = (resp.text or "")[:200]
        return "", f"AstroAPI returned HTTP {resp.status_code}: {msg}"

    # Response body should be a PNG. Validate before writing.
    body = resp.content
    if not body or len(body) < 8 or not body[:8].startswith(b"\x89PNG\r\n\x1a\n"):
        ctype = (resp.headers.get("content-type") or "?")
        return "", f"AstroAPI response did not look like PNG (content-type={ctype}, first 8 bytes={body[:8]!r})"

    # Persist to a tempfile so pdf_generator can read it as a local path.
    # The caller is responsible for cleaning up after the PDF is built.
    import tempfile
    try:
        fd, path = tempfile.mkstemp(prefix="astrochart_", suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(body)
    except Exception as e:
        return "", f"could not save PNG to tempfile: {e}"
    return path, ""


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

    # Fetch the chart-wheel PNG from AstroAPI (best-effort). The result is a
    # local tempfile path that pdf_generator's _fetch_chart_image() will read
    # directly. We clean it up after the PDF is built regardless of outcome.
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
    finally:
        # Clean up the AstroAPI PNG tempfile so we don't leak it under /tmp.
        if chart_image_url and chart_image_url.startswith("/") and os.path.exists(chart_image_url):
            try:
                os.unlink(chart_image_url)
            except Exception:
                pass

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
