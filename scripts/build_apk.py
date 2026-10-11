from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ET.register_namespace("android", ANDROID_NS)
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")


def log(message: str) -> None:
    print(message, flush=True)


def ensure_debug_keystore() -> None:
    keystore = ROOT / "debug.keystore"
    if keystore.is_file():
        return
    log("Generating an ephemeral debug signing keystore.")
    subprocess.run(
        [
            "keytool", "-genkeypair", "-v", "-keystore", str(keystore), "-storetype", "JKS",
            "-storepass", "android", "-keypass", "android", "-alias", "androiddebugkey",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=Android Debug,O=Android,C=US", "-noprompt",
        ],
        cwd=ROOT,
        check=True,
    )
    if not keystore.is_file():
        raise RuntimeError("Could not create the debug signing keystore.")


def fetch_payload(job_id: str) -> dict:
    api_url = os.environ.get("BUILD_API_URL", "").strip().rstrip("/")
    token = os.environ.get("INTERNAL_WORKER_TOKEN", "").strip()
    if not api_url or not token:
        raise RuntimeError("BUILD_API_URL and INTERNAL_WORKER_TOKEN must be set in the workflow secrets.")
    url = f"{api_url}/api/internal/jobs/{job_id}/payload"
    request = urllib.request.Request(url, headers={"X-Worker-Token": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"Render API did not provide this job payload (HTTP {exc.code}): {body}") from exc
    if payload.get("job_id") != job_id:
        raise RuntimeError("Render API returned a payload for a different build job.")
    return payload


def decode_file(item: dict | None) -> bytes | None:
    if not item:
        return None
    return base64.b64decode(item["data_base64"], validate=True)


def write_html(payload: dict) -> None:
    config = payload["config"]
    if config.get("resource_type") != "html":
        return
    files = payload.get("files") or {}
    content = decode_file(files.get("html_file"))
    if content is None:
        content = str(config.get("html_code", "")).encode("utf-8")
    target = ROOT / "app/src/main/assets/web/index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    advanced = config.get("advanced") or {}
    custom_css = str(advanced.get("custom_css", ""))
    custom_js = str(advanced.get("custom_javascript", ""))
    if custom_css:
        css = "<style>\n" + custom_css + "\n</style>"
        if re.search(r"</head\s*>", content.decode("utf-8", errors="replace"), re.I):
            text = target.read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"</head\s*>", lambda match: css + match.group(0), text, count=1, flags=re.I)
            target.write_text(text, encoding="utf-8")
    if custom_js:
        script = "<script>\n" + custom_js + "\n</script>"
        text = target.read_text(encoding="utf-8", errors="replace")
        if re.search(r"</body\s*>", text, re.I):
            text = re.sub(r"</body\s*>", lambda match: script + match.group(0), text, count=1, flags=re.I)
        else:
            text += "\n" + script
        target.write_text(text, encoding="utf-8")


def make_square(image: Image.Image, size: int, *, contain: bool = False) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGBA")
    if contain:
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
        return canvas
    return ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def load_image(data: bytes, label: str) -> Image.Image:
    from io import BytesIO
    try:
        image = Image.open(BytesIO(data))
        if image.format not in {"PNG", "JPEG"}:
            raise RuntimeError("Unsupported image format")
        image.load()
        if image.width > 6000 or image.height > 6000 or image.width * image.height > 24_000_000:
            raise RuntimeError(f"{label} dimensions are too large.")
        return image
    except Exception as exc:
        raise RuntimeError(f"{label} must be a valid PNG or JPEG image.") from exc


def apply_icon(data: bytes) -> None:
    image = load_image(data, "App icon")
    drawable = ROOT / "app/src/main/res/drawable"
    # Adaptive-icon foreground uses the Android safe area (about 66% of the canvas).
    fitted = make_square(image, 512)
    inner = make_square(image, 340, contain=True)
    foreground = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    foreground.alpha_composite(inner, ((512 - inner.width) // 2, (512 - inner.height) // 2))
    foreground.save(drawable / "ic_launcher_foreground_asset.png", optimize=True)

    density_sizes = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for density, size in density_sizes.items():
        directory = ROOT / f"app/src/main/res/mipmap-{density}"
        square = fitted.resize((size, size), Image.Resampling.LANCZOS)
        square.save(directory / "ic_launcher.png", optimize=True)
        round_image = square.copy()
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        round_image.putalpha(mask)
        round_image.save(directory / "ic_launcher_round.png", optimize=True)


def apply_splash(data: bytes) -> None:
    image = load_image(data, "Splash image")
    path = ROOT / "app/src/main/res/drawable/ic_splash_custom.png"
    make_square(image, 512, contain=True).save(path, optimize=True)
    xml_path = ROOT / "app/src/main/res/drawable/ic_splash_logo.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<bitmap xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:src="@drawable/ic_splash_custom"\n'
        '    android:gravity="center" />\n',
        encoding="utf-8",
    )


def update_app_name(app_name: str) -> None:
    path = ROOT / "app/src/main/res/values/strings.xml"
    tree = ET.parse(path)
    root = tree.getroot()
    node = root.find("string[@name='app_name']")
    if node is None:
        raise RuntimeError("strings.xml does not define app_name.")
    node.text = app_name
    tree.write(path, encoding="utf-8", xml_declaration=True)


def apply_config(payload: dict) -> list[str]:
    config = payload["config"]
    package = str(config.get("package_name", ""))
    if not PACKAGE_RE.fullmatch(package):
        raise RuntimeError("Invalid Android package name.")
    update_app_name(str(config["app_name"]))
    write_html(payload)
    files = payload.get("files") or {}
    icon = decode_file(files.get("app_icon"))
    splash = decode_file(files.get("splash_image"))
    if icon:
        apply_icon(icon)
    if splash:
        apply_splash(splash)

    website_url = str(config.get("website_url", "")).strip() if config.get("resource_type") == "web" else ""
    if website_url and not website_url.startswith("https://"):
        raise RuntimeError("Only HTTPS website URLs are supported.")
    build_args = [
        "gradle", "assembleDebug", "--no-daemon", "--stacktrace", "--max-workers=2",
        f"-PAPP_NAME={config['app_name']}",
        f"-PPACKAGE_NAME={package}",
        f"-PVERSION_NAME={config.get('version_name', '1.0.0')}",
        f"-PVERSION_CODE={int(config.get('version_code', 1))}",
        f"-PWEB_START_URL={website_url}",
    ]
    color = str(config.get("status_bar_color", "#0F172A"))
    if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        build_args.append(f"-PSPLASH_BG_COLOR={color}")
    return build_args


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip()
    if not job_id:
        raise RuntimeError("JOB_ID was not provided by workflow_dispatch.")
    log(f"Fetching payload for build {job_id}")
    payload = fetch_payload(job_id)
    command = apply_config(payload)
    log("Validated request and prepared Android project assets.")
    ensure_debug_keystore()
    log("Starting Gradle assembleDebug.")
    subprocess.run(command, cwd=ROOT, check=True)
    apk = ROOT / "app/build/outputs/apk/debug/app-debug.apk"
    if not apk.is_file() or apk.stat().st_size == 0:
        raise RuntimeError("Gradle returned without producing the expected APK.")
    log(f"Build succeeded: {apk.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        log(f"Gradle build failed with exit code {exc.returncode}.")
        sys.exit(exc.returncode or 1)
    except Exception as exc:
        log(f"Build failed: {exc}")
        sys.exit(1)
