from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = os.getenv("GITHUB_WORKFLOW_FILE", "apk-build.yml")
REPOSITORY = os.getenv("GITHUB_REPOSITORY", "mdmaruf123m/tp-apk")
GITHUB_API = "https://api.github.com"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_HTML_CHARS = 250_000
DOWNLOAD_TTL_SECONDS = 6 * 60 * 60
REQUIRED_SERVICE_ENV = ("BUILD_API_KEY", "GITHUB_TOKEN", "INTERNAL_WORKER_TOKEN", "DOWNLOAD_SIGNING_KEY")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,49}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")

app = FastAPI(title="APK Builder API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@dataclass
class BuildJob:
    job_id: str
    payload: dict[str, Any] | None
    created_at: float
    payload_claimed: bool = False


jobs: dict[str, BuildJob] = {}
active_job_id: str | None = None
submit_lock = asyncio.Lock()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"Server setup incomplete: {name} is not configured.")
    return value


def _missing_service_env() -> list[str]:
    return [name for name in REQUIRED_SERVICE_ENV if not os.getenv(name, "").strip()]


def _ensure_service_configured() -> None:
    missing = _missing_service_env()
    if missing:
        raise HTTPException(status_code=503, detail=f"Server setup incomplete: {', '.join(missing)} is not configured.")


def _api_key_auth(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("BUILD_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Server setup incomplete: BUILD_API_KEY is not configured.")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing builder API key.")


def _worker_auth(x_worker_token: str | None = Header(default=None)) -> None:
    expected = _required_env("INTERNAL_WORKER_TOKEN")
    if not x_worker_token or not hmac.compare_digest(x_worker_token, expected):
        raise HTTPException(status_code=401, detail="Invalid worker token.")


def _github_headers() -> dict[str, str]:
    token = _required_env("GITHUB_TOKEN")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_path(path: str) -> str:
    return f"/repos/{REPOSITORY}/{path.lstrip('/')}"


async def _github_request(method: str, path: str, **kwargs: Any) -> Any:
    url = path if path.startswith("https://") else GITHUB_API + path
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            response = await client.request(method, url, headers=_github_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach GitHub Actions.") from exc
    if response.status_code >= 400:
        detail = "GitHub Actions API request failed. Check the Render GITHUB_TOKEN permissions."
        if response.status_code == 404:
            detail = "GitHub workflow was not found. Check that apk-build.yml is on the repository's default branch."
        raise HTTPException(status_code=502, detail=detail)
    if response.status_code == 204 or not response.content:
        return None
    if "application/zip" in response.headers.get("content-type", ""):
        return response
    try:
        return response.json()
    except ValueError:
        return response


async def _workflow_runs(status: str | None = None) -> list[dict[str, Any]]:
    query = "?per_page=100"
    if status:
        query += f"&status={status}"
    data = await _github_request(
        "GET", _repo_path(f"actions/workflows/{WORKFLOW_FILE}/runs{query}")
    )
    return data.get("workflow_runs", []) if isinstance(data, dict) else []


def _run_job_id(run: dict[str, Any]) -> str | None:
    title = str(run.get("display_title") or "")
    prefix = "APK build "
    if title.startswith(prefix):
        return title[len(prefix):].strip()
    return None


async def _find_run(job_id: str) -> dict[str, Any] | None:
    for run in await _workflow_runs():
        if _run_job_id(run) == job_id:
            return run
    return None


async def _find_active_run() -> dict[str, Any] | None:
    seen: set[int] = set()
    for state in ("queued", "in_progress"):
        for run in await _workflow_runs(state):
            run_id = int(run.get("id", 0))
            if run_id in seen:
                continue
            seen.add(run_id)
            if run.get("event") == "workflow_dispatch" and run.get("status") in {"queued", "in_progress"}:
                return run
    return None


async def _validate_config(raw: str) -> dict[str, Any]:
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="config must be valid JSON.") from exc
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object.")

    app_name = str(config.get("app_name", "")).strip()
    package_name = str(config.get("package_name", "")).strip()
    if not app_name or len(app_name) > 50 or any(ord(char) < 32 for char in app_name):
        raise HTTPException(status_code=400, detail="App name is required and must be 1–50 printable characters.")
    if not PACKAGE_RE.fullmatch(package_name):
        raise HTTPException(status_code=400, detail="Package name must look like com.example.app.")

    build_variant = str(config.get("build_variant") or "regular").strip().lower()
    if build_variant not in {"regular", "release"}:
        raise HTTPException(status_code=400, detail="build_variant must be 'regular' or 'release'.")
    user_id = str(config.get("user_id") or "").strip()
    project_id = str(config.get("project_id") or "").strip()
    if build_variant == "release" and (not ID_RE.fullmatch(user_id) or not ID_RE.fullmatch(project_id)):
        raise HTTPException(status_code=400, detail="Release builds require valid user_id and project_id.")
    config["build_variant"] = build_variant
    config["user_id"] = user_id
    config["project_id"] = project_id

    version_name = str(config.get("version_name") or "1.0.0").strip()
    if not VERSION_RE.fullmatch(version_name):
        raise HTTPException(status_code=400, detail="Version name contains unsupported characters.")
    try:
        version_code = int(config.get("version_code") or 1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Version code must be a positive integer.") from exc
    if not 1 <= version_code <= 2_100_000_000:
        raise HTTPException(status_code=400, detail="Version code must be between 1 and 2100000000.")

    source = config.get("resource_type")
    if source not in {"web", "html"}:
        raise HTTPException(status_code=400, detail="resource_type must be 'web' or 'html'.")
    if source == "web":
        website = str(config.get("website_url", "")).strip()
        parsed_website = urlparse(website)
        if parsed_website.scheme != "https" or not parsed_website.hostname or parsed_website.username or parsed_website.password or len(website) > 2048:
            raise HTTPException(status_code=400, detail="Website URL must be a valid HTTPS URL.")
        config["website_url"] = website
    else:
        html_code = str(config.get("html_code", ""))
        if not html_code.strip():
            raise HTTPException(status_code=400, detail="HTML source cannot be empty.")
        if len(html_code) > MAX_HTML_CHARS:
            raise HTTPException(status_code=413, detail=f"HTML source exceeds {MAX_HTML_CHARS} characters.")
        config["html_code"] = html_code

    pro_features = config.get("pro_features") or {}
    if not isinstance(pro_features, dict):
        raise HTTPException(status_code=400, detail="pro_features must be an object.")
    feature_access = config.get("feature_access") or {}
    if not isinstance(feature_access, dict):
        raise HTTPException(status_code=400, detail="feature_access must be an object.")
    feature_usage = config.get("feature_usage") or {}
    if not isinstance(feature_usage, dict):
        raise HTTPException(status_code=400, detail="feature_usage must be an object.")
    subscription_active = bool(config.get("subscription_active", False))
    source_feature = "source_web" if source == "web" else "source_html"
    if feature_access.get(source_feature) is True and not subscription_active:
        raise HTTPException(status_code=403, detail="This build source requires an active subscription.")
    for feature_key, label in {
        "branding": "Application branding",
        "status_colors": "Status bar and splash colors",
        "behavior": "Behavior and permissions",
    }.items():
        if feature_usage.get(feature_key) is True and feature_access.get(feature_key) is True and not subscription_active:
            raise HTTPException(status_code=403, detail=f"{label} requires an active subscription.")
    if build_variant == "release" and feature_access.get("release_apk") is True and not bool(config.get("subscription_active", False)):
        raise HTTPException(status_code=403, detail="Release APK builds require an active subscription.")
    advanced = config.get("advanced") or {}
    if not isinstance(advanced, dict):
        raise HTTPException(status_code=400, detail="advanced must be an object.")
    if (str(advanced.get("custom_css", "")).strip() or str(advanced.get("custom_javascript", "")).strip()) and feature_access.get("advanced_html") is True and not bool(config.get("subscription_active", False)):
        raise HTTPException(status_code=403, detail="Advanced HTML options require an active subscription.")
    asset_encryption = bool(pro_features.get("asset_encryption", False))
    if asset_encryption and source != "html":
        raise HTTPException(status_code=400, detail="AES-256 Asset Encryption is available for local HTML projects only.")
    if asset_encryption and feature_access.get("asset_encryption") is True and not subscription_active:
        raise HTTPException(status_code=403, detail="AES-256 Asset Encryption requires an active subscription.")
    config["pro_features"] = {**pro_features, "asset_encryption": asset_encryption}
    config["subscription_active"] = subscription_active
    config["feature_access"] = {key: bool(value is True) for key, value in feature_access.items()}
    config["app_name"] = app_name
    config["package_name"] = package_name
    config["version_name"] = version_name
    config["version_code"] = version_code
    return config


async def _read_upload(upload: UploadFile | None, field_name: str) -> dict[str, str] | None:
    if upload is None or not upload.filename:
        return None
    data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"{field_name} must be 5 MB or smaller.")
    if not data:
        return None
    return {
        "filename": Path(upload.filename).name[:120],
        "content_type": upload.content_type or "application/octet-stream",
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def _signed_download(job_id: str, base_url: str) -> str:
    key = _required_env("DOWNLOAD_SIGNING_KEY").encode("utf-8")
    expires = int(time.time()) + DOWNLOAD_TTL_SECONDS
    message = f"{job_id}:{expires}".encode("utf-8")
    signature = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"{base_url.rstrip('/')}/api/jobs/{job_id}/download?expires={expires}&sig={signature}"


@app.get("/", include_in_schema=False)
async def builder_page() -> FileResponse:
    return FileResponse(ROOT / "apk-builder.html", media_type="text/html")


@app.get("/apk-builder.html", include_in_schema=False)
async def builder_file() -> FileResponse:
    return FileResponse(ROOT / "apk-builder.html", media_type="text/html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    missing = _missing_service_env()
    return {"status": "ok", "configured": not missing, "missing": missing}


@app.get("/api/auth/check", dependencies=[Depends(_api_key_auth)])
async def check_api_key() -> dict[str, str]:
    _ensure_service_configured()
    return {"status": "ok", "message": "Builder API key accepted."}


@app.post("/api/build", status_code=202, dependencies=[Depends(_api_key_auth)])
async def create_build(
    request: Request,
    config: str = Form(...),
    html_file: UploadFile | None = File(default=None),
    app_icon: UploadFile | None = File(default=None),
    splash_image: UploadFile | None = File(default=None),
) -> JSONResponse:
    global active_job_id
    _ensure_service_configured()
    now = time.time()
    for stale_id, stale_job in list(jobs.items()):
        if now - stale_job.created_at > 24 * 60 * 60:
            jobs.pop(stale_id, None)
    clean_config = await _validate_config(config)
    file_payload = {
        "html_file": await _read_upload(html_file, "HTML file"),
        "app_icon": await _read_upload(app_icon, "App icon"),
        "splash_image": await _read_upload(splash_image, "Splash image"),
    }
    total_size = sum(
        len(base64.b64decode(item["data_base64"]))
        for item in file_payload.values()
        if item is not None
    )
    if total_size > MAX_TOTAL_BYTES:
        raise HTTPException(status_code=413, detail="All uploaded files combined must be 10 MB or smaller.")

    if clean_config["resource_type"] == "html" and file_payload["html_file"] is None:
        file_payload["html_file"] = {
            "filename": "index.html",
            "content_type": "text/html",
            "data_base64": base64.b64encode(clean_config["html_code"].encode("utf-8")).decode("ascii"),
        }

    async with submit_lock:
        if active_job_id:
            active_run = await _find_run(active_job_id)
            if active_run is None or active_run.get("status") in {"queued", "in_progress"}:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "server_busy", "message": "Build server is busy. Try again when the current APK build finishes."},
                )
            active_job_id = None

        external_active = await _find_active_run()
        if external_active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "server_busy", "message": "Build server is busy. Try again when the current APK build finishes."},
            )

        job_id = str(uuid.uuid4())
        jobs[job_id] = BuildJob(
            job_id=job_id,
            payload={"config": clean_config, "files": file_payload},
            created_at=time.time(),
        )
        try:
            await _github_request(
                "POST",
                _repo_path(f"actions/workflows/{WORKFLOW_FILE}/dispatches"),
                json={"ref": "main", "inputs": {"job_id": job_id}},
            )
        except Exception:
            jobs.pop(job_id, None)
            raise
        active_job_id = job_id

    base_url = os.getenv("PUBLIC_API_URL", str(request.base_url).rstrip("/"))
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "accepted",
            "status_url": f"{base_url.rstrip('/')}/api/jobs/{job_id}",
            "message": "Build request accepted. The Android runner is preparing your APK.",
        },
    )


@app.get("/api/jobs/{job_id}", dependencies=[Depends(_api_key_auth)])
async def job_status(job_id: str, request: Request) -> dict[str, Any]:
    global active_job_id
    run = await _find_run(job_id)
    if run is None:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Build job was not found.")
        return {"job_id": job_id, "status": "queued", "message": "Request accepted; waiting for GitHub Actions runner."}

    state = run.get("status")
    conclusion = run.get("conclusion")
    if state in {"queued", "in_progress", "waiting", "requested"}:
        normalized = "running" if state == "in_progress" else "queued"
        message = "APK build is running." if normalized == "running" else "Waiting for a build runner."
    elif conclusion == "success":
        normalized = "complete"
        message = "APK build completed."
    elif conclusion == "cancelled" or state == "cancelled":
        normalized = "cancelled"
        message = "APK build was cancelled."
    else:
        normalized = "failed"
        message = f"APK build ended: {conclusion or state or 'unknown error'}."

    result: dict[str, Any] = {
        "job_id": job_id,
        "status": normalized,
        "message": message,
        "run_url": run.get("html_url"),
    }
    if normalized == "complete":
        result["download_url"] = _signed_download(job_id, str(request.base_url))
        result["download_expires_in_seconds"] = DOWNLOAD_TTL_SECONDS
    if normalized in {"complete", "failed", "cancelled"} and active_job_id == job_id:
        active_job_id = None
    return result


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(_api_key_auth)])
async def cancel_build(job_id: str) -> dict[str, Any]:
    """Cancel the matching GitHub Actions run so the Android runner stops too."""
    global active_job_id
    run = await _find_run(job_id)
    if run is None:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Build job was not found.")
        if active_job_id == job_id:
            active_job_id = None
        return {"job_id": job_id, "status": "cancelled", "message": "Queued build cancelled."}
    if run.get("status") in {"completed", "cancelled"}:
        return {"job_id": job_id, "status": "cancelled" if run.get("conclusion") == "cancelled" else "finished", "message": "Build is no longer running."}
    await _github_request("POST", _repo_path(f"actions/runs/{run['id']}/cancel"))
    if active_job_id == job_id:
        active_job_id = None
    return {"job_id": job_id, "status": "cancelling", "message": "Cancellation sent to GitHub Actions."}


@app.get("/api/jobs/{job_id}/download")
async def download_apk(job_id: str, expires: int, sig: str) -> StreamingResponse:
    if expires < int(time.time()) or expires > int(time.time()) + DOWNLOAD_TTL_SECONDS + 60:
        raise HTTPException(status_code=403, detail="Download link has expired.")
    key = _required_env("DOWNLOAD_SIGNING_KEY").encode("utf-8")
    expected = hmac.new(key, f"{job_id}:{expires}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=403, detail="Invalid download link.")

    run = await _find_run(job_id)
    if not run or run.get("status") != "completed" or run.get("conclusion") != "success":
        raise HTTPException(status_code=404, detail="APK artifact is not ready.")
    artifacts_data = await _github_request("GET", _repo_path(f"actions/runs/{run['id']}/artifacts?per_page=100"))
    artifact_name = f"apk-{job_id}"
    artifact = next(
        (item for item in artifacts_data.get("artifacts", []) if item.get("name") == artifact_name and not item.get("expired")),
        None,
    )
    if artifact is None:
        raise HTTPException(status_code=410, detail="The APK artifact has expired or was not uploaded.")

    response = await _github_request("GET", artifact["archive_download_url"])
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        apk_names = [name for name in archive.namelist() if name.lower().endswith(".apk") and not name.startswith("__MACOSX/")]
        if not apk_names:
            raise HTTPException(status_code=410, detail="No APK was found in the build artifact.")
        apk_name = apk_names[0]
        apk_bytes = archive.read(apk_name)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=502, detail="GitHub returned an invalid APK artifact archive.") from exc

    headers = {"Content-Disposition": f'attachment; filename="{Path(apk_name).name}"', "Cache-Control": "private, no-store"}
    return StreamingResponse(io.BytesIO(apk_bytes), media_type="application/vnd.android.package-archive", headers=headers)


@app.get("/api/internal/jobs/{job_id}/payload", dependencies=[Depends(_worker_auth)])
async def worker_payload(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None or job.payload is None:
        raise HTTPException(status_code=404, detail="Build payload is no longer available. Retry the request.")
    job.payload_claimed = True
    payload = job.payload
    job.payload = None
    return {"job_id": job_id, **payload}


@app.get("/api/openapi.json", include_in_schema=False)
async def openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.exception_handler(HTTPException)
async def http_error_json(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        content = detail
    else:
        content = {"message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)
