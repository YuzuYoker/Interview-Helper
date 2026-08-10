#!/bin/sh
set -e

echo "[entrypoint] waiting for qdrant..."
python - <<'PY'
import time, urllib.request
for _ in range(60):
    try:
        urllib.request.urlopen("http://qdrant:6333/readyz", timeout=2)
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("qdrant not ready after 120s")
PY

echo "[entrypoint] seeding knowledge base if empty..."
python backend/scripts/seed_knowledge.py || echo "[entrypoint] seed skipped/failed, continuing with existing KB"

echo "[entrypoint] starting uvicorn (workers=1, Qdrant single-instance)..."
cd /app/backend
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
