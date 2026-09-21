import sys
import traceback
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from app.main import create_app
    app = create_app()
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()
    err_msg = traceback.format_exc()
    print("STARTUP ERROR:", err_msg, file=sys.stderr)

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    def fallback(full_path: str):
        return PlainTextResponse(f"Startup Exception:\n\n{err_msg}", status_code=500)
