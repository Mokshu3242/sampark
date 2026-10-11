import logging
import threading

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_images, routes_runs
from app.core import storage
from app.core.auth import require_key
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.run_registry import RunRegistry

setup_logging()
log = logging.getLogger("sampark")

app = FastAPI(title="Sampark API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = RunRegistry(settings.runs_dir)

missing = settings.missing_keys()
if missing:
    log.warning(f"Missing keys in .env: {missing}")

RESTORE = {"done": not storage.enabled(), "runs": 0, "error": None}


def _restore():
    try:
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        RESTORE["runs"] = storage.restore_all()
    except Exception as e:                       # never stop the server over storage
        log.exception("Restoring runs failed")
        RESTORE["error"] = str(e)[:200]
    finally:
        RESTORE["done"] = True


@app.on_event("startup")
def startup():
    if storage.enabled():
        threading.Thread(target=_restore, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True, "runs_restored": RESTORE["done"], "restored": RESTORE["runs"],
            "error": RESTORE["error"], "locked": bool(settings.access_key)}


@app.get("/version")
def version():
    return {"version": app.version, "env": settings.env}


@app.get("/auth/check", dependencies=[Depends(require_key)])
def auth_check():
    return {"ok": True, "locked": bool(settings.access_key)}


app.include_router(routes_runs.router)
app.include_router(routes_images.router)