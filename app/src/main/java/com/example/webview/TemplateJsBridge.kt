package com.example.webview

import android.content.Context
import android.os.Build
import android.webkit.JavascriptInterface
import com.example.config.TemplateAppConfig
import org.json.JSONObject

/**
 * Safe, read-only JavaScript bridge exposed as `window.AndroidTemplateBridge`.
 *
 * Allows either the placeholder test HTML or user-supplied HTML applications to query basic APK
 * metadata (app name, package ID, version, offline/network status) without exposing any dangerous
 * shell or filesystem execution capabilities.
 */
class TemplateJsBridge(
  private val context: Context,
  private val config: TemplateAppConfig,
) {

  @JavascriptInterface
  fun getAppConfigJson(): String {
    return JSONObject().apply {
      put("appName", config.appName)
      put("packageName", config.applicationId)
      put("versionName", config.versionName)
      put("versionCode", config.versionCode)
      put("webEntryAssetPath", config.webEntryAssetPath)
      put("webEntryUrl", config.webEntryUrl)
      put("orientation", config.configuredOrientation)
      put("theme", config.configuredTheme)
      put("allowExternalNetwork", config.allowExternalNetwork)
      put("behavior", JSONObject().apply {
        put("hideTitleBar", config.hideTitleBar)
        put("fullscreen", config.fullscreen)
        put("darkMode", config.darkMode)
        put("codeSecurity", config.codeSecurity)
        put("assetEncryption", config.encryptedAssets)
      })
      put("androidSdkInt", Build.VERSION.SDK_INT)
      put("deviceModel", Build.MODEL ?: "Android")
      put("isTemplateReady", true)
    }.toString()
  }

  @JavascriptInterface
  fun getAppName(): String = config.appName

  @JavascriptInterface
  fun getPackageName(): String = config.applicationId

  @JavascriptInterface
  fun getVersionName(): String = config.versionName

  @JavascriptInterface
  fun getVersionCode(): Int = config.versionCode

  @JavascriptInterface
  fun isAssetPresent(relativePathInWebDir: String): Boolean {
    val sanitized =
      TemplateAppConfig.sanitizeAssetPath("${TemplateAppConfig.DEFAULT_WEB_FOLDER}/$relativePathInWebDir")
    if (!sanitized.startsWith("${TemplateAppConfig.DEFAULT_WEB_FOLDER}/")) {
      return false
    }
    return TemplateAppConfig.verifyEntryAssetExists(context, sanitized)
  }
}
