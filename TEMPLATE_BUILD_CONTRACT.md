# TEMPLATE BUILD CONTRACT (PYTHON BACKEND AUTOMATION)

This document defines the exact, machine-friendly contract between this **Android WebView APK Template** and the external **Python/FastAPI Backend** that copies, populates, configures, and compiles APKs on a Linux server.

The backend developer does **not** need to read or modify Kotlin source code to generate customized APKs. Follow the exact steps and file paths below.

---

## 1. End-to-End Backend Build Workflow

For each incoming APK build job, the Python backend worker should execute this isolated pipeline:

1. **Copy Template Workspace:** Copy this template repository into a unique, isolated temporary directory (e.g., `/tmp/apk-builds/job-<uuid>/`).
2. **Validate & Inject HTML Assets:** Sanitize paths (rejecting `..` path traversal or symlinks) and replace the contents of `app/src/main/assets/web/` with the user's HTML project. Ensure `app/src/main/assets/web/index.html` exists.
3. **Apply App Configuration:** Update `gradle.properties` (or pass `-P` Gradle CLI flags) and update `<string name="app_name">` in `app/src/main/res/values/strings.xml`.
4. **Replace App Icon (Optional):** If the user uploaded a custom icon, generate and overwrite the documented PNG files in `res/drawable/` and `res/mipmap-*/`.
5. **Replace Splash Assets (Optional):** Update `SPLASH_BG_COLOR` in `gradle.properties` and/or overwrite `app/src/main/res/drawable/ic_launcher_foreground_asset.png`.
6. **Execute Headless Gradle Build:** Run `gradle assembleDebug` (or `gradle assembleRelease` when signing environment variables are provided).
7. **Collect Output APK:** Copy `app/build/outputs/apk/debug/app-debug.apk` (or `app/build/outputs/apk/release/app-release.apk`) to the backend's artifact storage and clean up the temporary workspace.

---

## 2. Exact File Replacement & Configuration Contract

### A. HTML Application Injection

- **Target Directory to Replace:**
  ```text
  app/src/main/assets/web/
  ```
- **Required Entry File:**
  ```text
  app/src/main/assets/web/index.html
  ```
  *(Can be customized via `WEB_ENTRY_PATH` in `gradle.properties` if the user's entry file is named differently).*
- **WebView Runtime URL:**
  The Android `MainActivity` loads `file:///android_asset/web/index.html`.
- **Supported Assets:**
  Any nested folder hierarchy (`css/`, `js/`, `images/`, `fonts/`, `data/`, `media/`) is supported. Relative paths (`./style.css`, `assets/logo.png`, `../shared/app.js` within `web/`) work offline out-of-the-box.
- **Security Requirement for Backend:**
  When extracting a user's `.zip` archive into `app/src/main/assets/web/`, validate that every resolved target path starts with the canonical path of `app/src/main/assets/web/` (Zip-Slip prevention) and reject symlinks.

---

### B. App Name, Package ID, Version, SDK, Theme & Orientation

All build configuration parameters are centralized in **`gradle.properties`** at the project root.

#### File 1: `gradle.properties`
Modify these exact keys in `gradle.properties` (or pass them via CLI as `-P<KEY>=<VALUE>`):

```ini
APP_NAME=My Custom App
PACKAGE_NAME=com.client.customapp
VERSION_NAME=1.2.0
VERSION_CODE=12
MIN_SDK=24
TARGET_SDK=36
ORIENTATION=unspecified
APPLICATION_THEME=Theme.AppTemplate
WEB_ENTRY_PATH=web/index.html
ALLOW_EXTERNAL_NETWORK=true
ENABLE_WEBVIEW_DEBUG=false
SPLASH_BG_COLOR=#0F172A
```

#### File 2: `app/src/main/res/values/strings.xml`
Update the `<string name="app_name">` tag (remember to XML-escape `&`, `<`, `>`, `'`, `"`):

```xml
<string name="app_name">My Custom App</string>
```

#### Allowed Parameter Values & Validation Rules
| Variable | Validation Rule |
| :--- | :--- |
| `APP_NAME` | Non-empty UTF-8 string (recommended 1–30 characters). |
| `PACKAGE_NAME` | Valid Android `applicationId` matching `^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$` (at least 2 dot-separated segments, no Java keywords). **Note:** Keep `namespace = "com.example"` unchanged in `app/build.gradle.kts` so internal Kotlin imports do not need moving. |
| `VERSION_NAME` | String, e.g., `1.0.0`. |
| `VERSION_CODE` | Positive integer (`1` to `2100000000`). |
| `MIN_SDK` | Integer `>= 24`. |
| `TARGET_SDK` | Integer `36` (matches installed compileSdk). |
| `ORIENTATION` | One of: `unspecified`, `portrait`, `landscape`, `sensor`, `fullSensor`, `user`. |
| `APPLICATION_THEME` | One of: `Theme.AppTemplate` (standard with status/nav bars) or `Theme.AppTemplate.FullScreen` (immersive fullscreen). |
| `SPLASH_BG_COLOR` | Hex color string `#RRGGBB` or `#AARRGGBB`. |

---

### C. App Icon Replacement

To replace the app launcher icon with a user-supplied square PNG image (e.g., `1024x1024` or `512x512` PNG):

1. **Adaptive Foreground Asset (Required for Android 8.0+ Adaptive Icon & Splash Screen):**
   Overwrite:
   ```text
   app/src/main/res/drawable/ic_launcher_foreground_asset.png
   ```
   *(Recommended size: `512x512` RGBA PNG. `ic_launcher_foreground.xml` automatically centers this asset inside the 66dp adaptive safe zone).*

2. **Adaptive Background Color (Optional):**
   If changing the solid background behind the adaptive icon, update `android:fillColor` in:
   ```text
   app/src/main/res/drawable/ic_launcher_background.xml
   ```

3. **Density-Specific Raster PNGs (Required for Legacy Launchers & Store Extraction):**
   Overwrite `ic_launcher.png` (square PNG) and `ic_launcher_round.png` (circle-masked RGBA PNG) in all 5 mipmap directories:

   | Directory | Dimensions (px) | Files to Overwrite |
   | :--- | :--- | :--- |
   | `app/src/main/res/mipmap-mdpi/` | `48x48` | `ic_launcher.png`, `ic_launcher_round.png` |
   | `app/src/main/res/mipmap-hdpi/` | `72x72` | `ic_launcher.png`, `ic_launcher_round.png` |
   | `app/src/main/res/mipmap-xhdpi/` | `96x96` | `ic_launcher.png`, `ic_launcher_round.png` |
   | `app/src/main/res/mipmap-xxhdpi/` | `144x144` | `ic_launcher.png`, `ic_launcher_round.png` |
   | `app/src/main/res/mipmap-xxxhdpi/` | `192x192` | `ic_launcher.png`, `ic_launcher_round.png` |

---

### D. Splash Screen Replacement

The template includes a unified splash screen implementation (`Theme.AppTemplate` in `app/src/main/res/values/themes.xml`) supporting both Android 12+ (`windowSplashScreen*`) and Android 7–11 (`@drawable/splash_screen_background`).

- **To change the Splash Background Color:**
  Set `SPLASH_BG_COLOR=#RRGGBB` in `gradle.properties` (or pass `-PSPLASH_BG_COLOR=#RRGGBB`).
- **To change the Splash Center Logo:**
  By default, `app/src/main/res/drawable/ic_splash_logo.xml` references `@drawable/ic_launcher_foreground_asset`, so replacing `app/src/main/res/drawable/ic_launcher_foreground_asset.png` updates both the launcher icon and splash screen icon.
  If a separate splash image is desired, save it as `app/src/main/res/drawable/ic_splash_custom.png` and update `android:drawable="@drawable/ic_splash_custom"` inside `app/src/main/res/drawable/ic_splash_logo.xml`.

---

## 3. Headless Build Commands

Run from the root of the isolated build workspace directory:

### Debug APK Build (Default / Testing)
```bash
gradle assembleDebug --no-daemon
```

The repository does not include `gradlew`; the hosted GitHub Actions build workflow installs Gradle 9.3.1 before invoking the build.

### Release APK Build (With Signing Environment Variables)
Provide the keystore credentials via environment variables (never hard-code them in files):
```bash
export ANDROID_KEYSTORE_PATH="/path/to/release.jks"
export ANDROID_KEYSTORE_PASSWORD="keystore_password"
export ANDROID_KEY_ALIAS="upload"
export ANDROID_KEY_PASSWORD="key_password"

gradle assembleRelease --no-daemon
```

---

## 4. Expected Build Output Paths

After a successful Gradle build (`returncode == 0`), locate the generated APK at these deterministic paths relative to the workspace root:

- **Debug APK:**
  ```text
  app/build/outputs/apk/debug/app-debug.apk
  ```
- **Release APK:**
  ```text
  app/build/outputs/apk/release/app-release.apk
  ```
- **Output Metadata JSON (contains versionCode, versionName, applicationId):**
  ```text
  app/build/outputs/apk/debug/output-metadata.json
  app/build/outputs/apk/release/output-metadata.json
  ```

---

## 5. Reference Python Automation Snippet

```python
import os
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

def prepare_and_build_apk(
    template_dir: Path,
    workspace_dir: Path,
    user_html_dir: Path,
    app_name: str,
    package_name: str,
    version_name: str = "1.0.0",
    version_code: int = 1,
    orientation: str = "unspecified",
    splash_bg_color: str = "#0F172A",
) -> Path:
    # 1. Copy template to isolated workspace
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(template_dir, workspace_dir)

    # 2. Replace app/src/main/assets/web/ with user's HTML directory
    target_web_dir = workspace_dir / "app" / "src" / "main" / "assets" / "web"
    shutil.rmtree(target_web_dir)
    shutil.copytree(user_html_dir, target_web_dir)

    if not (target_web_dir / "index.html").is_file():
        raise FileNotFoundError("User HTML project must contain index.html at root")

    # 3. Update app_name in strings.xml
    strings_xml = workspace_dir / "app" / "src" / "main" / "res" / "values" / "strings.xml"
    content = strings_xml.read_text(encoding="utf-8")
    content = content.replace(
        '<string name="app_name">WebAPK Template</string>',
        f'<string name="app_name">{escape(app_name)}</string>',
    )
    strings_xml.write_text(content, encoding="utf-8")

    # 4. Run headless Gradle build with -P overrides
    cmd = [
        "gradle",
        "assembleDebug",
        "--no-daemon",
        f"-PAPP_NAME={app_name}",
        f"-PPACKAGE_NAME={package_name}",
        f"-PVERSION_NAME={version_name}",
        f"-PVERSION_CODE={version_code}",
        f"-PORIENTATION={orientation}",
        f"-PSPLASH_BG_COLOR={splash_bg_color}",
    ]
    subprocess.run(cmd, cwd=workspace_dir, check=True)

    apk_path = workspace_dir / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if not apk_path.is_file():
        raise RuntimeError(f"Expected APK output not found at {apk_path}")
    return apk_path
```
