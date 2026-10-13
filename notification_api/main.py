from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from typing import Any

import firebase_admin
import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import credentials, firestore
from pydantic import BaseModel, Field, field_validator
from pywebpush import WebPushException, webpush

app = FastAPI(title="Devlova Notification API", version="1.0.0")

def _origins() -> list[str]:
    value = os.getenv("CORS_ORIGINS", "*").strip()
    return [item.strip() for item in value.split(",") if item.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

bearer = HTTPBearer(auto_error=False)
_db = None
_send_tasks: dict[str, asyncio.Task[Any]] = {}


def _now() -> int:
    return int(time.time() * 1000)


def _service_ready() -> bool:
    return bool(os.getenv("NOTIFICATION_API_KEY") and os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))


def _firestore():
    global _db
    if _db is not None:
        return _db
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise HTTPException(503, "FIREBASE_SERVICE_ACCOUNT_JSON is not configured")
    try:
        if raw.startswith("base64:"):
            raw = base64.b64decode(raw[7:]).decode()
        service_account = json.loads(raw)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(service_account))
        _db = firestore.client()
        return _db
    except Exception as exc:
        raise HTTPException(503, f"Firestore initialization failed: {exc}") from exc


def _admin_auth(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    expected = os.getenv("NOTIFICATION_API_KEY", "").strip()
    if not expected or not creds or creds.scheme.lower() != "bearer" or creds.credentials != expected:
        raise HTTPException(401, "Invalid or missing notification API key")


class BatchPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="regular", pattern="^(regular|push)$")
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(min_length=1, max_length=5000)
    image: str | None = Field(default=None, max_length=2000)
    user_ids: list[str] = Field(min_length=1)
    batch_size: int = Field(default=50, ge=1, le=500)

    @field_validator("user_ids")
    @classmethod
    def unique_users(cls, value: list[str]) -> list[str]:
        result = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not result:
            raise ValueError("user_ids must contain at least one user")
        return result


class BatchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    image: str | None = Field(default=None, max_length=2000)
    user_ids: list[str] | None = None
    batch_size: int | None = Field(default=None, ge=1, le=500)


def _batch_doc(snapshot: Any) -> dict[str, Any]:
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "notification", "configured": _service_ready()}


@app.post("/api/upload-image", dependencies=[Depends(_admin_auth)])
async def upload_image(file: UploadFile = File(...)) -> dict[str, str]:
    key = os.getenv("IMGBB_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "IMGBB_API_KEY is not configured")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, "Image must be 8 MB or smaller")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.imgbb.com/1/upload",
            params={"key": key},
            data={"name": file.filename or "notification-image"},
            files={"image": (file.filename or "image", content, file.content_type or "application/octet-stream")},
        )
    payload = response.json()
    if not response.is_success or not payload.get("success"):
        raise HTTPException(502, "ImgBB upload failed")
    return {"url": payload["data"]["url"]}


@app.get("/api/batches", dependencies=[Depends(_admin_auth)])
def list_batches() -> list[dict[str, Any]]:
    docs = _firestore().collection("notification_batches").order_by("created_at", direction=firestore.Query.DESCENDING).stream()
    return [_batch_doc(doc) for doc in docs]


@app.get("/api/batches/{batch_id}", dependencies=[Depends(_admin_auth)])
def get_batch(batch_id: str) -> dict[str, Any]:
    snapshot = _firestore().collection("notification_batches").document(batch_id).get()
    if not snapshot.exists:
        raise HTTPException(404, "Batch not found")
    return _batch_doc(snapshot)


@app.post("/api/batches", dependencies=[Depends(_admin_auth)])
def create_batch(payload: BatchPayload) -> dict[str, Any]:
    db = _firestore()
    ref = db.collection("notification_batches").document()
    data = payload.model_dump()
    data.update({"status": "pending", "sent_count": 0, "failed_count": 0, "created_at": _now(), "updated_at": _now()})
    ref.set(data)
    return {"id": ref.id, **data}


@app.patch("/api/batches/{batch_id}", dependencies=[Depends(_admin_auth)])
def update_batch(batch_id: str, payload: BatchPatch) -> dict[str, Any]:
    ref = _firestore().collection("notification_batches").document(batch_id)
    snapshot = ref.get()
    if not snapshot.exists:
        raise HTTPException(404, "Batch not found")
    current = snapshot.to_dict() or {}
    if current.get("status") not in ("pending", "failed"):
        raise HTTPException(409, "Only pending or failed batches can be edited")
    data = payload.model_dump(exclude_unset=True)
    if "user_ids" in data:
        data["user_ids"] = list(dict.fromkeys(x.strip() for x in data["user_ids"] if x.strip()))
    data["updated_at"] = _now()
    ref.update(data)
    return _batch_doc(ref.get())


@app.delete("/api/batches/{batch_id}", dependencies=[Depends(_admin_auth)])
def delete_batch(batch_id: str) -> dict[str, bool]:
    ref = _firestore().collection("notification_batches").document(batch_id)
    snapshot = ref.get()
    if not snapshot.exists:
        raise HTTPException(404, "Batch not found")
    if (snapshot.to_dict() or {}).get("status") == "sending":
        raise HTTPException(409, "A sending batch cannot be removed")
    ref.delete()
    return {"deleted": True}


@app.post("/api/batches/{batch_id}/send", dependencies=[Depends(_admin_auth)])
async def send_batch(batch_id: str) -> dict[str, Any]:
    ref = _firestore().collection("notification_batches").document(batch_id)
    snapshot = ref.get()
    if not snapshot.exists:
        raise HTTPException(404, "Batch not found")
    data = snapshot.to_dict() or {}
    if data.get("status") == "sending":
        raise HTTPException(409, "Batch is already sending")
    ref.update({"status": "sending", "started_at": _now(), "updated_at": _now()})
    task = asyncio.create_task(_process_batch(batch_id))
    _send_tasks[batch_id] = task
    return {"id": batch_id, "status": "sending"}


async def _process_batch(batch_id: str) -> None:
    db = _firestore()
    ref = db.collection("notification_batches").document(batch_id)
    data = ref.get().to_dict() or {}
    user_ids = data.get("user_ids", [])
    total = len(user_ids)
    sent = failed = 0
    for offset in range(0, total, int(data.get("batch_size", 50))):
        chunk = user_ids[offset:offset + int(data.get("batch_size", 50))]
        for user_id in chunk:
            try:
                if data.get("kind") == "push":
                    await _send_push_to_user(db, user_id, data)
                else:
                    db.collection("notifications").add({"uid": user_id, "title": data["title"], "description": data["description"], "image": data.get("image"), "read": False, "batch_id": batch_id, "created_at": _now()})
                sent += 1
            except Exception:
                failed += 1
            ref.update({"sent_count": sent, "failed_count": failed, "processed_count": sent + failed, "progress": round((sent + failed) * 100 / total) if total else 100, "updated_at": _now()})
        await asyncio.sleep(0)
    ref.update({"status": "sent" if failed == 0 else "failed", "finished_at": _now(), "updated_at": _now()})
    _send_tasks.pop(batch_id, None)


async def _send_push_to_user(db: Any, user_id: str, data: dict[str, Any]) -> None:
    subscriptions = db.collection("push_subscriptions").where("uid", "==", user_id).stream()
    payload = json.dumps({"title": data["title"], "description": data["description"], "image": data.get("image"), "url": "/notifications"})
    for snapshot in subscriptions:
        subscription = snapshot.to_dict() or {}
        try:
            webpush(subscription_info=subscription["subscription"], data=payload, vapid_private_key=os.environ["VAPID_PRIVATE_KEY"], vapid_claims={"sub": os.environ["VAPID_SUBJECT"]})
        except WebPushException as exc:
            if getattr(exc.response, "status_code", None) in (404, 410):
                snapshot.reference.delete()
            else:
                raise


@app.post("/api/push-subscriptions", dependencies=[Depends(_admin_auth)])
def save_subscription(payload: dict[str, Any]) -> dict[str, bool]:
    uid = str(payload.get("uid", "")).strip()
    subscription = payload.get("subscription")
    if not uid or not isinstance(subscription, dict) or not subscription.get("endpoint"):
        raise HTTPException(400, "uid and subscription are required")
    _firestore().collection("push_subscriptions").document(uid).set({"uid": uid, "subscription": subscription, "updated_at": _now()}, merge=True)
    return {"saved": True}
