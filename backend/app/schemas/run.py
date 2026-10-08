from datetime import date
from pydantic import BaseModel, Field, field_validator

MAX_SIDE_DEG = 0.6  # keep runs small enough for reliable processing


class RunRequest(BaseModel):
    name: str = Field(..., description="Human label for this run")
    bbox: list[float] = Field(..., min_length=4, max_length=4,
                              description="[west, south, east, north] in EPSG:4326")
    flood_date: date
    window_days: int = Field(30, ge=6, le=60)

    @field_validator("bbox")
    @classmethod
    def check_bbox(cls, b: list[float]) -> list[float]:
        w, s, e, n = b
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ValueError("bbox must be [west, south, east, north] with west<east and south<north")
        if (e - w) > MAX_SIDE_DEG or (n - s) > MAX_SIDE_DEG:
            raise ValueError(f"bbox too large, max {MAX_SIDE_DEG} degrees per side")
        return b