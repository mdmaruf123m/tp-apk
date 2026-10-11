package com.example.config

import android.content.Context
import android.os.Build
import com.example.BuildConfig

/**
 * Strongly-typed configuration model representing all build-time and asset parameters injected into
 * this reusable APK Template by an external Python/FastAPI build server.
 */
data class TemplateAppConfig(
  val appName: String,
  val applicationId: String,
  val versionName: String,
  val versionCode: Int,
  val webEntryAssetPath: String,
  val webEntryUrl: String,
  val webStartUrl: String,
  val configuredOrientation: String,
  val configuredTheme: String,
  val allowExternalNetwork: Boolean,
  val enableWebViewDebug: Boolean,
  val deviceSdkInt: Int,
) {
  companion object {
    const val ASSET_URL_PREFIX = "file:///android_asset/"
    const val DEFAULT_WEB_FOLDER = "web"

    /**
     * Normalizes an asset path and ensures it cannot traverse outside the `assets/` root directory
     * via `..` segments or leading slashes.
     */
    fun sanitizeAssetPath(rawPath: String): String {
      val cleaned =
        rawPath
          .trim()
          .replace('\\', '/')
          .removePrefix(ASSET_URL_PREFIX)
          .trimStart('/')

      val segments = mutableListOf<String>()
      for (part in cleaned.split('/')) {
        when (part) {
          "", "." -> continue
          ".." -> {
            // Prevent path traversal outside the root assets directory
            if (segments.isNotEmpty()) {
              segments.removeAt(segments.lastIndex)
            }
          }
          else -> segments.add(part)
        }
      }

      if (segments.isEmpty()) {
        return "$DEFAULT_WEB_FOLDER/index.html"
      }
      return segments.joinToString("/")
    }

    /**
     * Checks whether a given asset URL points to a safe, canonical location inside
     * `file:///android_asset/web/`.
     */
    fun isSafeLocalAssetUrl(url: String): Boolean {
      if (!url.startsWith(ASSET_URL_PREFIX)) return false
      val rawSubPath = url.removePrefix(ASSET_URL_PREFIX).substringBefore('?').substringBefore('#')
      if (rawSubPath.contains("..")) return false
      val normalized = sanitizeAssetPath(rawSubPath)
      return normalized.startsWith("$DEFAULT_WEB_FOLDER/") || normalized == DEFAULT_WEB_FOLDER
    }

    /** Loads the active [TemplateAppConfig] from [BuildConfig] and Android resources. */
    fun fromContext(context: Context): TemplateAppConfig {
      val sanitizedEntry = sanitizeAssetPath(BuildConfig.WEB_ENTRY_PATH)
      return TemplateAppConfig(
        appName = BuildConfig.APP_NAME,
        applicationId = context.packageName,
        versionName = BuildConfig.VERSION_NAME,
        versionCode = BuildConfig.VERSION_CODE,
        webEntryAssetPath = sanitizedEntry,
        webEntryUrl = "$ASSET_URL_PREFIX$sanitizedEntry",
        webStartUrl = BuildConfig.WEB_START_URL,
        configuredOrientation = BuildConfig.CONFIGURED_ORIENTATION,
        configuredTheme = BuildConfig.CONFIGURED_THEME,
        allowExternalNetwork = BuildConfig.ALLOW_EXTERNAL_NETWORK,
        enableWebViewDebug = BuildConfig.ENABLE_WEBVIEW_DEBUG && BuildConfig.DEBUG,
        deviceSdkInt = Build.VERSION.SDK_INT,
      )
    }

    /** Verifies whether the configured HTML entry point exists in `app/src/main/assets/`. */
    fun verifyEntryAssetExists(context: Context, assetPath: String): Boolean {
      val sanitized = sanitizeAssetPath(assetPath)
      return try {
        context.assets.open(sanitized).use { true }
      } catch (_: Exception) {
        false
      }
    }
  }
}
