# Natal Report Generator — API

Flask service that wraps the existing natal report generator and exposes it
as an HTTP API for Railway deployment.

## Endpoints

| Method | Path               | Purpose                                |
|--------|--------------------|----------------------------------------|
| GET    | `/health`          | Liveness probe (Railway healthcheck)   |
| POST   | `/generate-report` | Generate a natal report from chart JSON |

## Request format

`POST /generate-report` accepts a JSON body in the AstroAPI.cloud chart shape:

```json
{
  "name": "Cliente Teste",
  "gender": "feminino",
  "points": { "sun": {...}, "moon": {...}, ... },
  "ascendant": {...},
  "midheaven": {...},
  "aspects": [...]
}
```

Optional control fields can be added alongside the chart:

- `sections_only`: `["abertura", "lua", ...]` — only generate these sections (skips Fio Condutor)
- `limit`: integer — generate only first N sections (skips Fio Condutor)
- `no_fio`: boolean — skip the Fio Condutor section

## Response shape

```json
{
  "status": "success",
  "report": "# Mapa Natal — ...\n\n## Abertura\n\n...",
  "meta": {
    "name": "...",
    "gender": "feminino",
    "sections": ["abertura", "triade", ...],
    "elapsed_seconds": 305.2,
    "aspect_audit": { "lua": ["Lua-Saturno trígono ..."], ... },
    "cleanup_changes": [ ... ]
  }
}
```

## Environment variables (required)

Set on Railway's Variables panel:

- `PINECONE_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `PINECONE_INDEX` (optional, defaults to `consultas-db`)
- `ASTROAPI_KEY` (optional, reserved for future use)

For local dev, copy `.env.example` to `.env` and fill in.

## Running locally

```bash
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)   # or use direnv / a launcher
python app.py
# then POST to http://localhost:8000/generate-report
```

## Deployment

- `Procfile` and `railway.json` both define the start command using `gunicorn`.
- Healthcheck path is `/health`.
- Timeout is set to 900s because full reports take ~5 minutes (17 LLM calls).
- One worker, 4 threads — fits in a Hobby plan and matches the bursty workload.
