from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
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


def _safe_path_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
    return cleaned[:120] or fallback


def _release_credentials(user_id: str, project_id: str, master: str) -> tuple[str, str, str]:
    seed = f"{user_id}:{project_id}".encode("utf-8")
    store_password = hmac.new(master.encode("utf-8"), b"store:" + seed, hashlib.sha256).hexdigest()
    key_password = hmac.new(master.encode("utf-8"), b"key:" + seed, hashlib.sha256).hexdigest()
    return store_password, key_password, "release"


def ensure_release_keystore(payload: dict) -> tuple[Path, str, str, str]:
    config = payload["config"]
    repo_dir = Path(os.environ.get("KEYSTORE_REPO_DIR", str(ROOT / "keystore-store"))).resolve()
    if not repo_dir.is_dir():
        token = os.environ.get("KEYSTORE_REPO_TOKEN", "").strip()
        repository = os.environ.get("KEYSTORE_REPOSITORY", "mdmaruf123m/keystore").strip()
        if not token:
            raise RuntimeError("Release signing requires KEYSTORE_REPO_TOKEN to access private keystore storage.")
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        clone_url = f"https://x-access-token:{token}@github.com/{repository}.git"
        subprocess.run(["git", "clone", "--depth", "1", clone_url, str(repo_dir)], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    master = os.environ.get("KEYSTORE_MASTER_SECRET", "").strip()
    user_id = str(config.get("user_id", "")).strip()
    project_id = str(config.get("project_id", "")).strip()
    if not repo_dir.is_dir() or not (repo_dir / ".git").is_dir():
        raise RuntimeError("Release keystore repository checkout is unavailable.")
    if not master or not user_id or not project_id:
        raise RuntimeError("Release signing requires KEYSTORE_MASTER_SECRET, user_id and project_id.")
    user_part = _safe_path_part(user_id, "user")
    project_part = _safe_path_part(project_id, "project")
    folder = repo_dir / "keystore" / user_part / project_part
    folder.mkdir(parents=True, exist_ok=True)
    keystore = folder / "release.keystore"
    store_password, key_password, alias = _release_credentials(user_id, project_id, master)
    if keystore.is_file() and keystore.stat().st_size > 0:
        log(f"Reusing release keystore for user {user_part}, project {project_part}.")
    else:
        log(f"Generating release keystore for user {user_part}, project {project_part}.")
        subprocess.run([
            "keytool", "-genkeypair", "-v", "-keystore", str(keystore), "-storetype", "JKS",
            "-storepass", store_password, "-keypass", key_password, "-alias", alias,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", f"CN={config['app_name']},OU=Devlova,O=Devlova,C=US", "-noprompt",
        ], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        subprocess.run(["git", "-C", str(repo_dir), "add", str(keystore.relative_to(repo_dir))], check=True)
        staged = subprocess.run(["git", "-C", str(repo_dir), "diff", "--cached", "--quiet"], check=False)
        if staged.returncode == 1:
            subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Devlova APK Builder"], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "actions@devlova.app"], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", f"Add release keystore for {user_part}/{project_part}"], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "push", "origin", "main"], check=True)
        elif staged.returncode != 0:
            raise RuntimeError("Could not inspect staged keystore changes.")
    local_copy = ROOT / "release.keystore"
    shutil.copyfile(keystore, local_copy)
    return local_copy, store_password, key_password, alias


def artifact_filename(app_name: str, variant: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", app_name).strip("._-") or "devlova_app"
    return f"{safe[:80]}_{variant}.apk"


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


def encrypt_html_asset(payload: dict) -> str | None:
    """Encrypt the generated local entry HTML with AES-256-GCM.

    The runtime key is injected into BuildConfig for the template to decrypt in memory. This
    protects the packaged asset from casual extraction; it cannot defeat a determined reverse
    engineer because any offline APK must ultimately contain its decryption capability.
    """
    config = payload["config"]
    enabled = bool((config.get("pro_features") or {}).get("asset_encryption", False))
    entry = ROOT / "app/src/main/assets/web/index.html"
    encrypted = ROOT / "app/src/main/assets/web/index.html.enc"
    if not enabled:
        if encrypted.exists():
            encrypted.unlink()
        return None
    if config.get("resource_type") != "html" or not entry.is_file():
        raise RuntimeError("AES-256 Asset Encryption requires a local HTML entry file.")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("AES-256 Asset Encryption requires the cryptography package.") from exc
    key = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, entry.read_bytes(), None)
    encrypted.write_bytes(b"DVL1" + nonce + ciphertext)
    entry.unlink()
    return base64.b64encode(key).decode("ascii")


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
    # Keep the artwork inside Android's adaptive-icon safe zone. The XML
    # foreground is a full 108dp canvas; using a 66dp XML item here would
    # shrink the uploaded logo a second time on modern launchers.
    inner = make_square(image, 340, contain=True)
    foreground = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    foreground.alpha_composite(inner, ((512 - inner.width) // 2, (512 - inner.height) // 2))
    foreground.save(drawable / "ic_launcher_foreground_asset.png", optimize=True)

    # Never use the template's dark placeholder behind a user logo. A
    # light sampled background keeps transparent logos visible without the
    # black square seen on Android home screens.
    bg_image = image.convert("RGBA").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    alpha = bg_image[3] / 255
    channel = tuple(round(bg_image[i] * alpha + 255 * (1 - alpha)) for i in range(3))
    bg_hex = "#%02X%02X%02X" % channel
    (drawable / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:width="108dp" android:height="108dp"\n'
        '    android:viewportWidth="108" android:viewportHeight="108">\n'
        f'    <path android:fillColor="{bg_hex}" android:pathData="M0,0h108v108h-108z" />\n'
        '</vector>\n', encoding="utf-8"
    )

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
    encryption_key = encrypt_html_asset(payload)
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
        "gradle", "assembleRelease" if str(config.get("build_variant", "regular")) == "release" else "assembleDebug", "--no-daemon", "--stacktrace", "--max-workers=2",
        f"-PAPP_NAME={config['app_name']}",
        f"-PPACKAGE_NAME={package}",
        f"-PVERSION_NAME={config.get('version_name', '1.0.0')}",
        f"-PVERSION_CODE={int(config.get('version_code', 1))}",
        f"-PWEB_START_URL={website_url}",
        "-PENCRYPTED_ASSETS=true" if encryption_key else "-PENCRYPTED_ASSETS=false",
    ]
    if encryption_key:
        os.environ["ENCRYPTION_KEY_B64"] = encryption_key
    else:
        os.environ.pop("ENCRYPTION_KEY_B64", None)
    color = str(config.get("status_bar_color", "#0F172A"))
    if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        build_args.append(f"-PSPLASH_BG_COLOR={color}")
    behavior = config.get("behavior") or {}
    behavior_keys = {
        "hide_title_bar": "HIDE_TITLE_BAR", "loading_spinner": "LOADING_SPINNER",
        "fullscreen": "FULLSCREEN", "exit_confirmation": "EXIT_CONFIRMATION",
        "pull_to_refresh": "PULL_TO_REFRESH", "pinch_zoom": "PINCH_ZOOM",
        "media_autoplay": "MEDIA_AUTOPLAY", "pc_mode": "PC_MODE",
        "long_press_menu": "LONG_PRESS_MENU", "camera_access": "CAMERA_ACCESS",
        "microphone_access": "MICROPHONE_ACCESS", "dark_mode": "DARK_MODE",
    }
    for key, gradle_key in behavior_keys.items():
        build_args.append(f"-P{gradle_key}={'true' if bool(behavior.get(key, False)) else 'false'}")
    pro_features = config.get("pro_features") or {}
    build_args.append(f"-PCODE_SECURITY={'true' if bool(pro_features.get('code_security', False)) else 'false'}")
    return build_args


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip()
    if not job_id:
        raise RuntimeError("JOB_ID was not provided by workflow_dispatch.")
    log(f"Fetching payload for build {job_id}")
    payload = fetch_payload(job_id)
    command = apply_config(payload)
    log("Validated request and prepared Android project assets.")
    variant = str(payload["config"].get("build_variant", "regular"))
    if variant == "release":
        keystore, store_password, key_password, alias = ensure_release_keystore(payload)
        os.environ.update({"KEYSTORE_PATH": str(keystore), "STORE_PASSWORD": store_password, "KEY_PASSWORD": key_password, "KEY_ALIAS": alias})
        log("Starting Gradle assembleRelease with the project keystore.")
        subprocess.run(command, cwd=ROOT, check=True, env=os.environ.copy())
        apk = ROOT / "app/build/outputs/apk/release/app-release.apk"
    else:
        ensure_debug_keystore()
        log("Starting Gradle assembleDebug.")
        subprocess.run(command, cwd=ROOT, check=True, env=os.environ.copy())
        apk = ROOT / "app/build/outputs/apk/debug/app-debug.apk"
    if not apk.is_file() or apk.stat().st_size == 0:
        raise RuntimeError("Gradle returned without producing the expected APK.")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    named_apk = dist / artifact_filename(str(payload["config"].get("app_name", "devlova_app")), variant)
    shutil.copyfile(apk, named_apk)
    log(f"Build succeeded: {named_apk.name} ({named_apk.stat().st_size} bytes)")
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
