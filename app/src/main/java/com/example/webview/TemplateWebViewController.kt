package com.example.webview

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import android.util.Log
import android.view.MotionEvent
import android.view.ViewGroup
import android.content.pm.PackageManager
import android.webkit.ConsoleMessage
import android.webkit.GeolocationPermissions
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.PermissionRequest
import com.example.config.TemplateAppConfig
import java.io.ByteArrayInputStream
import java.nio.charset.StandardCharsets

/**
 * Callback interface informing the host Activity/UI about WebView navigation state and fatal entry
 * point errors.
 */
interface TemplateWebViewCallbacks {
  fun onHistoryStateChanged(canGoBack: Boolean)
  fun onMainFrameLoadSuccess(url: String)
  fun onMainFrameLoadError(failingUrl: String, errorCode: Int, description: String)
  fun onShowFileChooser(filePathCallback: ValueCallback<Array<Uri>>): Boolean
  fun onRendererGone()
}

/**
 * Factory and configurator for a production-hardened Android [WebView] tailored to local HTML/CSS/JS
 * applications packaged in `app/src/main/assets/web/`.
 */
object TemplateWebViewController {
  private const val TAG = "TemplateWebView"
  const val JS_BRIDGE_NAME = "AndroidTemplateBridge"

  @SuppressLint("SetJavaScriptEnabled")
  fun createConfiguredWebView(
    context: Context,
    config: TemplateAppConfig,
    callbacks: TemplateWebViewCallbacks,
  ): WebView {
    if (config.enableWebViewDebug && !config.codeSecurity) {
      WebView.setWebContentsDebuggingEnabled(true)
    }

    return WebView(context).apply {
      layoutParams =
        ViewGroup.LayoutParams(
          ViewGroup.LayoutParams.MATCH_PARENT,
          ViewGroup.LayoutParams.MATCH_PARENT,
        )
      setBackgroundColor(Color.parseColor("#0F172A"))
      isVerticalScrollBarEnabled = true
      isHorizontalScrollBarEnabled = false

      settings.apply {
        // Core HTML5 / SPA / JS / Storage support
        javaScriptEnabled = true
        domStorageEnabled = true
        databaseEnabled = true
        javaScriptCanOpenWindowsAutomatically = false
        supportMultipleWindows()

        // Responsive viewport & UTF-8 encoding
        useWideViewPort = true
        loadWithOverviewMode = true
        defaultTextEncodingName = StandardCharsets.UTF_8.name()
        textZoom = 100

        // Zoom is explicitly controlled by the builder toggle.
        setSupportZoom(config.pinchZoom)
        builtInZoomControls = config.pinchZoom
        displayZoomControls = false

        // Media & caching
        mediaPlaybackRequiresUserGesture = !config.mediaAutoplay
        if (config.pcMode) {
          userAgentString = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
          @Suppress("DEPRECATION")
          forceDark = if (config.darkMode) WebSettings.FORCE_DARK_ON else WebSettings.FORCE_DARK_OFF
        }
        cacheMode = WebSettings.LOAD_DEFAULT
        mixedContentMode = if (config.codeSecurity) WebSettings.MIXED_CONTENT_NEVER_ALLOW else WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE

        // Local asset file access:
        // Note: file:///android_asset/ works even when allowFileAccess is false on modern Android,
        // but keeping allowFileAccess = true and disabling universal/file-URL cross-origin access
        // ensures maximum compatibility across API 24–36 for sub-assets (fonts, JSON, images, CSS,
        // ES modules) inside file:///android_asset/web/ while blocking arbitrary filesystem reads.
        allowFileAccess = true
        allowContentAccess = true
        @Suppress("DEPRECATION")
        allowFileAccessFromFileURLs = true
        @Suppress("DEPRECATION")
        allowUniversalAccessFromFileURLs = false
      }

      // Remote websites are third-party origins; do not expose the native JS bridge to them.
      if (config.webStartUrl.isBlank()) {
        addJavascriptInterface(TemplateJsBridge(context.applicationContext, config), JS_BRIDGE_NAME)
      }

      if (!config.longPressMenu) {
        setOnLongClickListener { true }
      }
      if (config.pullToRefresh) {
        var downY = 0f
        setOnTouchListener { view, event ->
          when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> downY = event.y
            MotionEvent.ACTION_UP -> if (event.y - downY > 150f && view.scrollY == 0) reload()
          }
          false
        }
      }
      webViewClient = createSafeWebViewClient(context.applicationContext, config, callbacks)
      webChromeClient = createWebChromeClient(context.applicationContext, config, callbacks)
    }
  }

  private fun createSafeWebViewClient(
    context: Context,
    config: TemplateAppConfig,
    callbacks: TemplateWebViewCallbacks,
  ): WebViewClient {
    return object : WebViewClient() {

      override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
        super.onPageStarted(view, url, favicon)
        callbacks.onHistoryStateChanged(view?.canGoBack() == true)
        if (config.loadingSpinner) view?.evaluateJavascript(LOADING_SPINNER_SCRIPT, null)
      }

      override fun onPageFinished(view: WebView?, url: String?) {
        super.onPageFinished(view, url)
        callbacks.onHistoryStateChanged(view?.canGoBack() == true)
        if (config.loadingSpinner) view?.evaluateJavascript(REMOVE_LOADING_SPINNER_SCRIPT, null)
        if (url != null) {
          callbacks.onMainFrameLoadSuccess(url)
        }
      }

      override fun doUpdateVisitedHistory(view: WebView?, url: String?, isReload: Boolean) {
        super.doUpdateVisitedHistory(view, url, isReload)
        callbacks.onHistoryStateChanged(view?.canGoBack() == true)
      }

      override fun shouldInterceptRequest(
        view: WebView?,
        request: WebResourceRequest?,
      ): WebResourceResponse? {
        val uri = request?.url ?: return null
        val urlString = uri.toString()

        // Guard against arbitrary file:// access outside file:///android_asset/web/
        if (uri.scheme.equals("file", ignoreCase = true)) {
          if (!TemplateAppConfig.isSafeLocalAssetUrl(urlString)) {
            Log.w(TAG, "Blocked unauthorized local file request: $urlString")
            return createForbiddenResponse("Access outside android_asset/web is prohibited.")
          }
        }

        // If external network access is disabled via ALLOW_EXTERNAL_NETWORK=false, block http/https
        if (
          !config.allowExternalNetwork &&
            (uri.scheme.equals("http", ignoreCase = true) ||
              uri.scheme.equals("https", ignoreCase = true))
        ) {
          Log.w(TAG, "Blocked external network request (ALLOW_EXTERNAL_NETWORK=false): $urlString")
          return createForbiddenResponse("External network requests are disabled in this APK build.")
        }

        return super.shouldInterceptRequest(view, request)
      }

      override fun shouldOverrideUrlLoading(
        view: WebView?,
        request: WebResourceRequest?,
      ): Boolean {
        val uri = request?.url ?: return false
        val scheme = uri.scheme?.lowercase() ?: return false
        val urlString = uri.toString()

        return when (scheme) {
          "file" -> {
            // Only permit in-WebView navigation to safe local assets inside android_asset/web/
            if (TemplateAppConfig.isSafeLocalAssetUrl(urlString)) {
              false
            } else {
              Log.w(TAG, "Blocked unsafe file:// navigation: $urlString")
              true
            }
          }
          "http", "https" -> {
            if (!config.allowExternalNetwork) {
              Log.w(TAG, "Blocked external navigation (ALLOW_EXTERNAL_NETWORK=false): $urlString")
              true
            } else {
              // Allow in-app navigation or external browser opening
              false
            }
          }
          "mailto", "tel", "sms", "geo", "intent" -> {
            launchExternalSchemeIntent(context, uri)
            true
          }
          else -> true
        }
      }

      override fun onReceivedError(
        view: WebView?,
        request: WebResourceRequest?,
        error: WebResourceError?,
      ) {
        super.onReceivedError(view, request, error)
        if (request?.isForMainFrame == true) {
          val failingUrl = request.url?.toString() ?: config.webEntryUrl
          val errorCode = error?.errorCode ?: ERROR_UNKNOWN
          val description = error?.description?.toString() ?: "Unknown WebView load error"
          Log.e(TAG, "Main frame load error ($errorCode) on $failingUrl: $description")
          callbacks.onMainFrameLoadError(
            failingUrl = failingUrl,
            errorCode = errorCode,
            description = description,
          )
        }
      }

      override fun onRenderProcessGone(
        view: WebView?,
        detail: RenderProcessGoneDetail?,
      ): Boolean {
        Log.e(TAG, "WebView render process gone (didCrash=${detail?.didCrash()})")
        callbacks.onRendererGone()
        return true
      }
    }
  }

  private fun createWebChromeClient(context: Context, config: TemplateAppConfig, callbacks: TemplateWebViewCallbacks): WebChromeClient {
    return object : WebChromeClient() {
      override fun onConsoleMessage(consoleMessage: ConsoleMessage?): Boolean {
        if (consoleMessage != null) {
          Log.d(
            TAG,
            "[JS ${consoleMessage.messageLevel()}] ${consoleMessage.message()} " +
              "(${consoleMessage.sourceId()}:${consoleMessage.lineNumber()})",
          )
        }
        return super.onConsoleMessage(consoleMessage)
      }

      override fun onPermissionRequest(request: PermissionRequest?) {
        if (request == null) return
        val allowed = request.resources.filter { resource ->
          (resource == PermissionRequest.RESOURCE_VIDEO_CAPTURE && config.cameraAccess && context.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) ||
            (resource == PermissionRequest.RESOURCE_AUDIO_CAPTURE && config.microphoneAccess && context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
        }
        if (allowed.size == request.resources.size) request.grant(allowed.toTypedArray()) else request.deny()
      }

      override fun onShowFileChooser(
        webView: WebView?,
        filePathCallback: ValueCallback<Array<Uri>>?,
        fileChooserParams: FileChooserParams?,
      ): Boolean {
        if (filePathCallback == null) return false
        return callbacks.onShowFileChooser(filePathCallback)
      }

      override fun onGeolocationPermissionsShowPrompt(
        origin: String?,
        callback: GeolocationPermissions.Callback?,
      ) {
        callback?.invoke(origin, false, false)
      }
    }
  }

  private fun launchExternalSchemeIntent(context: Context, uri: Uri) {
    try {
      val intent =
        Intent(Intent.ACTION_VIEW, uri).apply {
          addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
      context.startActivity(intent)
    } catch (e: ActivityNotFoundException) {
      Log.w(TAG, "No activity found on device to handle external URI: $uri", e)
    }
  }

  private val LOADING_SPINNER_SCRIPT = """
    (function(){if(document.getElementById('__devlova_loading'))return;var s=document.createElement('style');s.id='__devlova_loading';s.textContent='@keyframes devlovaSpin{to{transform:rotate(360deg)}}#__devlova_loader{position:fixed;z-index:2147483647;top:18px;left:50%;width:28px;height:28px;margin-left:-14px;border:3px solid rgba(37,99,235,.2);border-top-color:#2563eb;border-radius:50%;animation:devlovaSpin .7s linear infinite}';document.documentElement.appendChild(s);var d=document.createElement('div');d.id='__devlova_loader';(document.body||document.documentElement).appendChild(d)})()
  """.trimIndent()
  private const val REMOVE_LOADING_SPINNER_SCRIPT = "document.getElementById('__devlova_loader')?.remove();document.getElementById('__devlova_loading')?.remove();"

  private fun createForbiddenResponse(reason: String): WebResourceResponse {
    val stream = ByteArrayInputStream(reason.toByteArray(StandardCharsets.UTF_8))
    return WebResourceResponse(
      "text/plain",
      StandardCharsets.UTF_8.name(),
      403,
      "Forbidden",
      mapOf("Cache-Control" to "no-store"),
      stream,
    )
  }
}
