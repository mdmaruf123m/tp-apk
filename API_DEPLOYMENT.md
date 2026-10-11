# APK Builder API deployment

The API accepts one build at a time. It triggers `.github/workflows/apk-build.yml`; the APK is built on a GitHub-hosted runner, then returned through a short-lived signed download URL. Requests received while an Actions build is queued or running return HTTP `409` with `server_busy`.

## 1. Merge and enable the workflow

1. Merge this change into the repository's default `main` branch. GitHub only allows `workflow_dispatch` for workflows present on the default branch.
2. In **Settings → Actions → General**, allow actions to run for this repository. The workflow needs the repository's normal GitHub-hosted runner and `actions: write` permission to publish the APK artifact.
3. Create a fine-grained GitHub token restricted to `mdmaruf123m/tp-apk` with **Actions: Read and write**. The token is used only by the Render API. Do not place it in `apk-builder.html` or send it to a browser.

## 2. Deploy the Render Blueprint

1. Merge the tested pull request into `main`, then click the **Deploy to Render** button at the top of the repository README (or in the builder's API Connection panel). It opens Render's Blueprint flow for this GitHub repository; connect your Render account/repository if prompted, review the service, and deploy it. Render reads `render.yaml` and creates the `tp-apk-builder-api` Python web service.
2. Use the Free plan for the API only. It does not run Gradle; GitHub Actions does the Android build.
3. Set these environment variables in the Render service:
   - `BUILD_API_KEY`: a long random key that authorized builder users will enter in the page.
   - `GITHUB_TOKEN`: the fine-grained GitHub token described above.
   - `INTERNAL_WORKER_TOKEN`: another long random value, also added below as an Actions secret.
   - `DOWNLOAD_SIGNING_KEY`: a separate long random value used to sign six-hour download URLs.
4. Wait for the Render service to deploy. `GET /api/health` should report `configured: true`.

Generate random values with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Use a different value for each secret. Never commit these values.

## 3. Add GitHub Actions secrets

In GitHub, open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `BUILD_API_URL`: the Render service base URL, for example `https://tp-apk-builder-api.onrender.com` (no `/api/build` suffix).
- `INTERNAL_WORKER_TOKEN`: exactly the value configured on Render.

The browser never receives the GitHub token or the internal worker token. It only sends `BUILD_API_KEY` in an Authorization header.

## 4. Connect the single-file frontend

Open the Render service URL. On `apk-builder.html`, enter the Render service base URL and `BUILD_API_KEY`, then choose **Save API**. The page verifies the key. Press **BUILD APK** to receive a job ID; it polls the job endpoint every four seconds and displays a download link when the run finishes. The signed download link is valid for six hours; the Actions artifact is retained for seven days.

## Runtime notes

- Keep the Render API at **one instance and one Uvicorn worker**. The API uses a process-local submit lock plus GitHub Actions run inspection; scaling the API horizontally is not supported by this implementation.
- Render Free may spin down when idle. The first visit can be slow. Build status and the APK artifact live in GitHub Actions, so the APK is not stored on Render's ephemeral disk.
- The API stores the submitted source/assets in memory until the Actions runner fetches them. A Render restart in that short interval can cause that build to fail; the frontend should submit it again after the failure.
- The shared `BUILD_API_KEY` is access control, not per-user identity. The builder saves it in that browser's local storage; anyone who has the key can submit builds. Use it only on a trusted device, do not publish the key in HTML, and rotate it if exposed. For an open public builder, add actual sign-in or CAPTCHA/rate-limit controls before sharing it broadly.
- The workflow currently builds **debug APKs**. Those APKs are installable for testing but are not a signed release for Play Store publishing.
- Each hosted build creates a temporary debug keystore because the repository intentionally ignores `debug.keystore`. A later APK build is signed with a different temporary key, so Android may require uninstalling the earlier copy before installing a rebuilt APK. Use a securely stored persistent signing key before distributing updateable/release APKs.
- Website mode opens the supplied **HTTPS URL** in the APK's WebView; that app needs internet access and the website is not bundled for offline use. HTML mode packages the pasted single HTML document and optional custom CSS/JavaScript. It does not yet accept a multi-file website ZIP.
- App name, package name, version, website/HTML source, uploaded PNG/JPEG icon, splash image and splash background color are applied. The other behavior/permission toggles and refresh/build-complete webhook fields currently travel in the request but are not implemented by this Android template; do not treat them as active controls yet.
