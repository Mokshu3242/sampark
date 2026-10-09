import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.run_registry import RunRegistry
from app.api import routes_runs
from app.api import routes_images

setup_logging()
log = logging.getLogger("flood-watch")

app = FastAPI(title="Flood Watch API", version="0.1.0")

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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"version": app.version, "env": settings.env}

app.include_router(routes_runs.router)
app.include_router(routes_images.router)
