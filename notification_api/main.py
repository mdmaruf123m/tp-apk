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


class ChatPayload(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    site: str = Field(default="Devlova", max_length=80)
    creator: str = Field(default="Dev Maruf (Maruf Hossain)", max_length=120)
    language: str = Field(default="auto", max_length=20)


class ProviderChatPayload(BaseModel):
    provider: str = Field(pattern="^(openrouter|groq)$")
    api_key: str = Field(min_length=8, max_length=500)
    model: str | None = Field(default=None, max_length=180)
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    language: str = Field(default="auto", max_length=20)


class ProviderModelsPayload(BaseModel):
    provider: str = Field(pattern="^(openrouter|groq)$")
    api_key: str = Field(min_length=8, max_length=500)


def _batch_doc(snapshot: Any) -> dict[str, Any]:
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "notification", "configured": _service_ready()}


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "Devlova Notification API",
        "ok": True,
        "health": "/api/health",
        "configured": _service_ready(),
    }


@app.get("/api/public-config")
def public_config() -> dict[str, Any]:
    return {
        "ok": True,
        "vapidPublicKey": os.getenv("VAPID_PUBLIC_KEY", "").strip(),
    }


@app.post("/api/chat")
async def chat(payload: ChatPayload) -> dict[str, Any]:
    """Public chat surface for the removable Dev Ai UI; the LLM key stays server-side."""
    message = payload.message.strip()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()
    if not api_key:
        return {"ok": True, "reply": _local_chat_reply(message)}
    language_hint = "Reply in Bengali because the user used Bengali." if payload.language.lower() == "bengali" else "Reply in English because the user used English."
    system = "You are a general-purpose AI assistant. Answer in the requested language, and help with writing, explaining, debugging, HTML, CSS, JavaScript and other code. Do not assume knowledge about any particular website or application. " + language_hint
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in payload.history[-8:]:
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:1200]})
    messages.append({"role": "user", "content": message})
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                api_base + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "temperature": 0.35, "max_tokens": 350},
            )
        if response.is_success:
            data = response.json()
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if reply:
                return {"ok": True, "reply": reply}
    except Exception:
        pass
    return {"ok": True, "reply": _local_chat_reply(message)}


@app.post("/api/provider-chat")
async def provider_chat(payload: ProviderChatPayload) -> dict[str, Any]:
    """CORS-safe proxy. The browser-supplied key is used only for this request and is never stored."""
    endpoint = "https://openrouter.ai/api/v1/chat/completions" if payload.provider == "openrouter" else "https://api.groq.com/openai/v1/chat/completions"
    model = payload.model.strip() if payload.provider == "openrouter" and payload.model else "llama-3.3-70b-versatile"
    language = "Bengali" if payload.language.lower() == "bengali" else "English"
    system = f"You are a general-purpose AI assistant. Reply in {language}. Help with questions and writing, explaining, debugging, HTML, CSS, JavaScript and other code. When code is requested, provide complete useful code."
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in payload.history[-8:]:
        role, content = item.get("role"), str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:12000]})
    messages.append({"role": "user", "content": payload.message.strip()})
    headers = {"Authorization": f"Bearer {payload.api_key.strip()}", "Content-Type": "application/json"}
    if payload.provider == "openrouter":
        headers.update({"HTTP-Referer": "https://devlova.pages.dev", "X-Title": "Dev Ai Code"})
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(endpoint, headers=headers, json={"model": model, "messages": messages, "temperature": 0.35, "max_tokens": 2500})
        if not response.is_success:
            detail = response.text[:500]
            raise HTTPException(response.status_code, f"{payload.provider} API error: {detail}")
        data = response.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not reply:
            raise HTTPException(502, "The AI provider returned an empty response")
        return {"ok": True, "reply": reply, "model": model}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"AI provider request failed: {exc}") from exc


@app.post("/api/provider-models")
async def provider_models(payload: ProviderModelsPayload) -> dict[str, Any]:
    """Return provider models using the submitted key transiently; no key is stored."""
    endpoint = "https://openrouter.ai/api/v1/models" if payload.provider == "openrouter" else "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {payload.api_key.strip()}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(endpoint, headers=headers)
        if not response.is_success:
            raise HTTPException(response.status_code, f"{payload.provider} models error: {response.text[:500]}")
        raw = response.json().get("data", [])
        models = []
        for item in raw:
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            models.append({"id": model_id, "name": item.get("name") or model_id, "context": item.get("context_length")})
        models.sort(key=lambda item: item["id"].lower())
        return {"ok": True, "provider": payload.provider, "models": models}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Model list request failed: {exc}") from exc


def _local_chat_reply(message: str) -> str:
    q = message.lower()
    bn = any("\u0980" <= char <= "\u09ff" for char in message)
    return "আমি আপনার প্রশ্নের উত্তর দিতে এবং HTML, CSS, JavaScript বা অন্য code লিখতে সাহায্য করতে পারি।" if bn else "I can answer questions and help write, explain or debug HTML, CSS, JavaScript and other code."


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
    delivered = False
    for snapshot in subscriptions:
        subscription = snapshot.to_dict() or {}
        try:
            await asyncio.to_thread(webpush, subscription_info=subscription["subscription"], data=payload, vapid_private_key=os.environ["VAPID_PRIVATE_KEY"], vapid_claims={"sub": os.environ["VAPID_SUBJECT"]})
            delivered = True
        except WebPushException as exc:
            if getattr(exc.response, "status_code", None) in (404, 410):
                snapshot.reference.delete()
            else:
                raise
    if not delivered:
        raise RuntimeError("User has no active push subscription")


@app.post("/api/push-subscriptions", dependencies=[Depends(_admin_auth)])
def save_subscription(payload: dict[str, Any]) -> dict[str, bool]:
    uid = str(payload.get("uid", "")).strip()
    subscription = payload.get("subscription")
    if not uid or not isinstance(subscription, dict) or not subscription.get("endpoint"):
        raise HTTPException(400, "uid and subscription are required")
    _firestore().collection("push_subscriptions").document(uid).set({"uid": uid, "subscription": subscription, "updated_at": _now()}, merge=True)
    return {"saved": True}
