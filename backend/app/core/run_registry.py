import json, uuid
from datetime import datetime, timezone
from pathlib import Path

class RunRegistry:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def new_run(self, request: dict) -> str:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "request.json").write_text(json.dumps(request, indent=2))
        self.set_status(run_id, "created")
        return run_id

    def set_status(self, run_id: str, status: str, detail: str = "") -> None:
        path = self.runs_dir / run_id / "status.json"
        path.write_text(json.dumps({
            "status": status,
            "detail": detail,
            "updated": datetime.now(timezone.utc).isoformat(),
        }, indent=2))

    def get_status(self, run_id: str) -> dict:
        path = self.runs_dir / run_id / "status.json"
        if not path.exists():
            return {"status": "unknown"}
        return json.loads(path.read_text())