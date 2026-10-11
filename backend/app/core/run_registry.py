import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

STAGES = ["created", "fetching", "mapping", "damage", "cutoff", "done"]
log = logging.getLogger("flood-watch.registry")


class RunRegistry:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def new_run(self, request: dict) -> str:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.create(run_id, request)
        return run_id

    def create(self, run_id: str, request: dict) -> None:
        """Make the run folder for a known ID (the cloud worker uses this)."""
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "request.json").write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
        self.set_status(run_id, "created")

    def write_status(self, run_id: str, data: dict) -> None:
        """Write a status dict as-is (atomic)."""
        path = self.runs_dir / run_id / "status.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def set_status(self, run_id: str, status: str, detail: str = "") -> None:
        """Main stages (STAGES) are remembered; inner phase statuses only update the detail."""
        prev = self.get_status(run_id)
        stage = status if status in STAGES or status == "failed" else prev.get("stage", "created")
        data = {
            "status": status,
            "stage": stage,
            "stage_index": STAGES.index(stage) if stage in STAGES else -1,
            "stage_total": len(STAGES) - 1,
            "detail": detail or status,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        self.write_status(run_id, data)
        if os.getenv("SAMPARK_WORKER") == "1":         # cloud worker: share progress via Mongo
            try:
                from app.core import remote
                remote.push_status(run_id, data)
            except Exception as e:
                log.warning(f"Could not share status: {e}")

    def get_status(self, run_id: str) -> dict:
        path = self.runs_dir / run_id / "status.json"
        for _ in range(3):
            try:
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "unknown"}
            except (json.JSONDecodeError, OSError):
                import time
                time.sleep(0.05)
        return {"status": "unknown"}

    def is_ready(self, run_id: str) -> bool:
        return (self.runs_dir / run_id / "cutoff_stats.json").exists()

    def list_runs(self, limit: int = 30) -> list[dict]:
        runs = []
        for d in sorted((p for p in self.runs_dir.iterdir() if p.is_dir()), reverse=True)[:limit]:
            s = self.get_status(d.name)
            req_p = d / "request.json"
            req = json.loads(req_p.read_text(encoding="utf-8")) if req_p.exists() else {}
            runs.append({"run_id": d.name, "name": req.get("name"), "flood_date": req.get("flood_date"),
                         "status": s.get("status", "unknown"), "ready": self.is_ready(d.name)})
        return runs