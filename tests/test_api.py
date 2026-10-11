import json
import hashlib
import hmac
import io
import time
import zipfile

import httpx

import pytest
from fastapi.testclient import TestClient

from build_api import main


@pytest.fixture(autouse=True)
def reset_runtime(monkeypatch):
    monkeypatch.setenv("BUILD_API_KEY", "test-builder-key")
    monkeypatch.setenv("GITHUB_TOKEN", "test-github-token")
    monkeypatch.setenv("INTERNAL_WORKER_TOKEN", "test-worker-token")
    monkeypatch.setenv("DOWNLOAD_SIGNING_KEY", "test-signing-key")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mdmaruf123m/tp-apk")
    main.jobs.clear()
    main.active_job_id = None
    yield
    main.jobs.clear()
    main.active_job_id = None


@pytest.fixture
def client():
    return TestClient(main.app)


def html_form(name="Sample App", package="org.example.sample"):
    config = {
        "app_name": name,
        "package_name": package,
        "resource_type": "html",
        "html_code": "<!doctype html><html><body>Hi</body></html>",
        "version_name": "1.0.0",
        "version_code": "1",
        "status_bar_color": "#0F172A",
    }
    return {"config": json.dumps(config)}, {
        "html_file": ("index.html", b"<!doctype html><html><body>Hi</body></html>", "text/html")
    }


def test_health_and_api_key_check(client):
    assert client.get("/api/health").json()["configured"] is True
    assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/auth/check", headers={"Authorization": "Bearer test-builder-key"}).status_code == 200


def test_builder_cross_origin_preflight_allows_authorized_api_requests(client):
    response = client.options(
        "/api/build",
        headers={
            "Origin": "https://builder.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_rejects_invalid_package_name(client):
    data, files = html_form(package="not-a-package")
    response = client.post("/api/build", data=data, files=files, headers={"Authorization": "Bearer test-builder-key"})
    assert response.status_code == 400
    assert "Package name" in response.json()["message"]


def test_accepts_first_job_and_returns_poll_url(client, monkeypatch):
    async def no_active_run():
        return None

    async def dispatch(method, path, **kwargs):
        assert method == "POST"
        assert path.endswith("/actions/workflows/apk-build.yml/dispatches")
        assert kwargs["json"]["ref"] == "main"
        return None

    monkeypatch.setattr(main, "_find_active_run", no_active_run)
    monkeypatch.setattr(main, "_github_request", dispatch)
    data, files = html_form()
    response = client.post("/api/build", data=data, files=files, headers={"Authorization": "Bearer test-builder-key"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["job_id"]
    assert body["status_url"].endswith(f"/api/jobs/{body['job_id']}")
    assert main.active_job_id == body["job_id"]


def test_rejects_second_job_while_first_is_running(client, monkeypatch):
    main.active_job_id = "existing-job"

    async def active_run(job_id):
        return {"id": 42, "status": "in_progress"}

    monkeypatch.setattr(main, "_find_run", active_run)
    data, files = html_form()
    response = client.post("/api/build", data=data, files=files, headers={"Authorization": "Bearer test-builder-key"})
    assert response.status_code == 409
    assert response.json()["code"] == "server_busy"


def test_worker_can_claim_payload_once(client, monkeypatch):
    main.jobs["job-123"] = main.BuildJob(
        job_id="job-123",
        payload={"config": {"app_name": "Sample"}, "files": {}},
        created_at=1,
    )
    headers = {"X-Worker-Token": "test-worker-token"}
    response = client.get("/api/internal/jobs/job-123/payload", headers=headers)
    assert response.status_code == 200
    assert response.json()["config"]["app_name"] == "Sample"
    assert main.jobs["job-123"].payload is None
    assert client.get("/api/internal/jobs/job-123/payload", headers=headers).status_code == 404


def test_signed_download_proxies_apk_from_github_artifact(client, monkeypatch):
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("app-debug.apk", b"fake-apk-bytes")
    zip_response = httpx.Response(
        200,
        headers={"content-type": "application/zip"},
        content=archive_buffer.getvalue(),
    )

    async def completed_run(job_id):
        return {"id": 123, "status": "completed", "conclusion": "success"}

    async def artifact_request(method, path, **kwargs):
        if path.endswith("/actions/runs/123/artifacts?per_page=100"):
            return {"artifacts": [{"name": "apk-job-123", "expired": False, "archive_download_url": "https://github.example/artifact.zip"}]}
        if path == "https://github.example/artifact.zip":
            return zip_response
        raise AssertionError(f"Unexpected GitHub API request: {method} {path}")

    monkeypatch.setattr(main, "_find_run", completed_run)
    monkeypatch.setattr(main, "_github_request", artifact_request)
    expires = int(time.time()) + 300
    signature = hmac.new(
        b"test-signing-key",
        f"job-123:{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()
    response = client.get(f"/api/jobs/job-123/download?expires={expires}&sig={signature}")

    assert response.status_code == 200
    assert response.content == b"fake-apk-bytes"
    assert response.headers["content-type"].startswith("application/vnd.android.package-archive")
