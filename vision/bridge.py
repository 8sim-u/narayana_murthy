
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="WAMSS Central Decision Bridge")

events = []
lock = Lock()


class DetectionEvent(BaseModel):
    camera_id: str
    track_id: str
    objects: list[str]
    confidence: float = 0.0
    zone: str = "UNKNOWN"


@app.get("/health")
def health():
    return {"status": "online", "service": "WAMSS bridge"}


@app.post("/event")
def receive_event(event: DetectionEvent):
    record = event.model_dump()
    record["timestamp"] = datetime.now(timezone.utc).isoformat()

    # Central decision logic
    alerts = []

    if event.zone == "RESTRICTED":
        alerts.append("RESTRICTED_ZONE_DETECTED")

    with lock:
        events.append(record)
        if len(events) > 1000:
            del events[:len(events) - 1000]

    return {
        "received": True,
        "camera_id": event.camera_id,
        "alerts": alerts,
        "decision": "REVIEW_REQUIRED" if alerts else "NO_RULE_TRIGGERED"
    }


@app.get("/events")
def get_events():
    with lock:
        return list(events)
