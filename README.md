# Android WebView APK Template

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/mdmaruf123m/tp-apk/tree/main)

A production-ready, reusable **Android APK Template Project** built with **Kotlin**, **AndroidX**, **Material 3**, and **Android WebView**.

This repository is engineered specifically to be cloned, populated with an HTML5/CSS/JS application, configured, and compiled into a standalone Android APK by an **automated Python/FastAPI backend running headlessly on a Linux server**.

---

## 1. What This Template Is

- **Reusable Native Wrapper:** Packages any static or Single-Page HTML5 application (`index.html`, CSS, JS, images, fonts, JSON, audio/video, and nested directories) into a native Android APK.
- **Offline-First Local Asset Loading:** Loads `file:///android_asset/web/index.html` directly from the APK's internal assets without requiring an internet connection to boot.
- **External API Capable:** Declares `android.permission.INTERNET` and `android.permission.ACCESS_NETWORK_STATE` so HTML applications that call remote REST/GraphQL APIs or load external resources work seamlessly when online.
- **Zero-Touch Configuration:** Centralizes `APP_NAME`, `PACKAGE_NAME`, `VERSION_NAME`, `VERSION_CODE`, `MIN_SDK`, `TARGET_SDK`, `ORIENTATION`, `APPLICATION_THEME`, and `SPLASH_BG_COLOR` inside `gradle.properties` (and supports `-P` CLI overrides), eliminating brittle regex edits across XML or Kotlin files.

---

## 2. Project Structure

```text
.
├── README.md
├── TEMPLATE_BUILD_CONTRACT.md
├── build.gradle.kts
├── settings.gradle.kts
├── gradle.properties
├── gradle/
│   ├── libs.versions.toml
│   └── wrapper/
│       └── gradle-wrapper.properties
└── app/
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── assets/
        │   │   └── web/
        │   │       ├── index.html
        │   │       ├── css/
        │   │       │   └── template.css
        │   │       ├── js/
        │   │       │   └── template.js
        │   │       ├── data/
        │   │       │   └── template-manifest.json
        │   │       └── assets/
        │   │           └── images/
        │   │               └── template-badge.svg
        │   ├── java/com/example/
        │   │   ├── MainActivity.kt
        │   │   ├── config/
        │   │   │   └── TemplateAppConfig.kt
        │   │   ├── webview/
        │   │   │   ├── TemplateJsBridge.kt
        │   │   │   └── TemplateWebViewController.kt
        │   │   └── ui/theme/
        │   │       ├── Color.kt
        │   │       ├── Theme.kt
        │   │       └── Type.kt
        │   └── res/
        │       ├── drawable/
        │       │   ├── ic_launcher_background.xml
        │       │   ├── ic_launcher_foreground.xml
        │       │   ├── ic_launcher_foreground_asset.png
        │       │   ├── ic_splash_logo.xml
        │       │   └── splash_screen_background.xml
        │       ├── mipmap-anydpi-v26/
        │       │   ├── ic_launcher.xml
        │       │   └── ic_launcher_round.xml
        │       ├── mipmap-mdpi/
        │       │   ├── ic_launcher.png
        │       │   └── ic_launcher_round.png
        │       ├── mipmap-hdpi/
        │       │   ├── ic_launcher.png
        │       │   └── ic_launcher_round.png
        │       ├── mipmap-xhdpi/
        │       │   ├── ic_launcher.png
        │       │   └── ic_launcher_round.png
        │       ├── mipmap-xxhdpi/
        │       │   ├── ic_launcher.png
        │       │   └── ic_launcher_round.png
        │       ├── mipmap-xxxhdpi/
        │       │   ├── ic_launcher.png
        │       │   └── ic_launcher_round.png
        │       ├── values/
        │       │   ├── colors.xml
        │       │   ├── strings.xml
        │       │   └── themes.xml
        │       └── xml/
        │           ├── backup_rules.xml
        │           └── data_extraction_rules.xml
        └── test/java/com/example/
            ├── ExampleRobolectricTest.kt
            └── ExampleUnitTest.kt
```

---

## 3. Where HTML Goes

Place the user's web application inside:

```text
app/src/main/assets/web/
```

- The entry file must be `app/src/main/assets/web/index.html` (unless overridden via `WEB_ENTRY_PATH`).
- All relative paths (`./css/style.css`, `js/app.js`, `assets/logo.png`, `fonts/inter.woff2`, `data/config.json`) resolve automatically against `file:///android_asset/web/`.
- Before injecting a user's project, the backend should delete the existing contents of `app/src/main/assets/web/` and extract the user's files into that directory.

---

## 4. How Configuration Works

All customizable build parameters are centralized in **`gradle.properties`** at the root of the repository and read dynamically by **`app/build.gradle.kts`**:

| Property Key | Default Value | Description |
| :--- | :--- | :--- |
| `APP_NAME` | `WebAPK Template` | Launcher label (`@string/app_name` in `res/values/strings.xml` & `BuildConfig.APP_NAME`) |
| `PACKAGE_NAME` | `com.aistudio.webapktemplate.k9x4qm` | Android `applicationId` uniquely identifying the APK on devices and stores |
| `VERSION_NAME` | `1.0.0` | User-visible semantic version string (`versionName`) |
| `VERSION_CODE` | `1` | Positive integer build version code (`versionCode`) |
| `MIN_SDK` | `24` | Minimum supported Android API level (Android 7.0+) |
| `TARGET_SDK` | `36` | Target Android API level |
| `ORIENTATION` | `unspecified` | Activity screen orientation (`unspecified`, `portrait`, `landscape`, `sensor`, `fullSensor`) |
| `APPLICATION_THEME` | `Theme.AppTemplate` | Android theme (`Theme.AppTemplate` or `Theme.AppTemplate.FullScreen`) |
| `WEB_ENTRY_PATH` | `web/index.html` | Path to the HTML entry point relative to `app/src/main/assets/` |
| `ALLOW_EXTERNAL_NETWORK` | `true` | Permit (`true`) or block (`false`) remote `http://` and `https://` requests from WebView |
| `ENABLE_WEBVIEW_DEBUG` | `false` | Enable `chrome://inspect` debugging in debug builds |
| `SPLASH_BG_COLOR` | `#0F172A` | Hex color (`#RRGGBB` or `#AARRGGBB`) for the splash screen background |

> **Note on App Name (`strings.xml`):**
> `app/src/main/res/values/strings.xml` defines `<string name="app_name">WebAPK Template</string>`. When the Python backend updates the app name, it should update both `APP_NAME` in `gradle.properties` and `<string name="app_name">...</string>` in `app/src/main/res/values/strings.xml` (both are documented in `TEMPLATE_BUILD_CONTRACT.md`).

---

## 5. Where Icons & Splash Assets Go

### Launcher Icon Files
- **Adaptive Icon Foreground Asset:** `app/src/main/res/drawable/ic_launcher_foreground_asset.png` (512x512 PNG recommended; centered inside the 66dp safe zone by `ic_launcher_foreground.xml`).
- **Adaptive Icon Background Color:** `app/src/main/res/drawable/ic_launcher_background.xml` (update `android:fillColor="#0F172A"`).
- **Raster Density Fallbacks:**
  - `app/src/main/res/mipmap-mdpi/ic_launcher.png` & `ic_launcher_round.png` (48x48)
  - `app/src/main/res/mipmap-hdpi/ic_launcher.png` & `ic_launcher_round.png` (72x72)
  - `app/src/main/res/mipmap-xhdpi/ic_launcher.png` & `ic_launcher_round.png` (96x96)
  - `app/src/main/res/mipmap-xxhdpi/ic_launcher.png` & `ic_launcher_round.png` (144x144)
  - `app/src/main/res/mipmap-xxxhdpi/ic_launcher.png` & `ic_launcher_round.png` (192x192)

### Splash Screen Assets
- **Splash Center Icon:** Uses `@drawable/ic_splash_logo` (`app/src/main/res/drawable/ic_splash_logo.xml`), which references `@drawable/ic_launcher_foreground_asset` by default so replacing `ic_launcher_foreground_asset.png` updates both the adaptive launcher icon and the splash icon automatically. You can also place a dedicated `ic_splash_custom.png` in `res/drawable/` and point `ic_splash_logo.xml` to it.
- **Splash Background Color:** Controlled by `SPLASH_BG_COLOR` in `gradle.properties` (injected as `@color/splash_background`).

---

## 6. How to Build Locally & Headlessly on Linux

### Prerequisites on the Build Host
This repository does **not** bundle the Android SDK or JDK inside git. The Linux build server must have:
- **JDK 17 or JDK 21**
- **Android SDK** (`ANDROID_SDK_ROOT` or `ANDROID_HOME` configured in environment variables) with API 36 platform and build-tools installed
- **Gradle** (`gradle` on `PATH` or `./gradlew` wrapper)

### Generate a Debug APK
```bash
gradle assembleDebug
```

This repository does not include the Gradle wrapper scripts. The hosted APK workflow installs the pinned Gradle version on its GitHub Actions runner.

You can also pass configuration overrides directly via CLI flags without modifying `gradle.properties`:
```bash
gradle assembleDebug \
  -PAPP_NAME="Acme Field Portal" \
  -PPACKAGE_NAME="com.acme.fieldportal" \
  -PVERSION_NAME="2.4.0" \
  -PVERSION_CODE="24" \
  -PORIENTATION="portrait" \
  -PAPPLICATION_THEME="Theme.AppTemplate"
```

### Generate a Signed Release APK
Set the signing environment variables on the build worker and run `assembleRelease`:
```bash
export ANDROID_KEYSTORE_PATH="/secure/keystores/release.jks"
export ANDROID_KEYSTORE_PASSWORD="your-keystore-password"
export ANDROID_KEY_ALIAS="upload"
export ANDROID_KEY_PASSWORD="your-key-password"

gradle assembleRelease
```
*(Also supports `KEYSTORE_PATH`, `STORE_PASSWORD`, `KEY_ALIAS`, and `KEY_PASSWORD` environment variables).*

---

## 7. Where the Final APK is Generated

- **Debug APK Output Path:**
  ```text
  app/build/outputs/apk/debug/app-debug.apk
  ```
- **Release APK Output Path:**
  ```text
  app/build/outputs/apk/release/app-release.apk
  ```

For full Python backend integration steps and exact file replacement contracts, see **[`TEMPLATE_BUILD_CONTRACT.md`](./TEMPLATE_BUILD_CONTRACT.md)**.


## 8. APK Builder API and Hosted Build Runner

The standalone [`apk-builder.html`](./apk-builder.html) can use the FastAPI service and GitHub Actions workflow added to this repository. Render accepts one request at a time and returns HTTP `409` (`server_busy`) while the first job is queued or running. Accepted requests receive a `job_id`; the page polls status and shows a signed APK download link after the GitHub-hosted runner completes the build.

The Render service hosts the API and frontend only. Android SDK/Gradle builds run in GitHub Actions, so the Render Free API's 512 MB memory limit is not used for compilation. For secrets, setup, limitations and deployment steps, see [`API_DEPLOYMENT.md`](./API_DEPLOYMENT.md).

> The builder API currently supports debug APK builds. A release APK suitable for store distribution needs a separately configured signing workflow.
