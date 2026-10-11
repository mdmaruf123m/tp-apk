package com.example

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.widget.Toast
import android.Manifest
import android.content.pm.PackageManager
import android.webkit.ValueCallback
import android.webkit.WebView
import android.util.Base64
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.example.config.TemplateAppConfig
import com.example.ui.theme.AppTemplateTheme
import com.example.webview.TemplateWebViewCallbacks
import com.example.webview.TemplateWebViewController
import java.nio.charset.StandardCharsets
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * UI state representing the status of the embedded local HTML application.
 *
 * The application launches directly into [Ready] with zero artificial splash delay; [Error] is only
 * displayed if the configured `assets/web/index.html` is missing or encounters a fatal main-frame
 * load failure.
 */
sealed interface WebContainerState {
  data object Ready : WebContainerState

  data class Error(
    val failingUrl: String,
    val errorCode: Int,
    val description: String,
    val isEntryAssetMissing: Boolean,
  ) : WebContainerState
}

/**
 * Production-ready host Activity for the reusable Android WebView APK Template.
 *
 * Responsibilities:
 * 1. Initialize and configure the embedded [WebView] for modern HTML5/CSS/JS apps.
 * 2. Load the local HTML entry point (`file:///android_asset/web/index.html` by default).
 * 3. Handle Android system back navigation (`webView.canGoBack()` -> `webView.goBack()`).
 * 4. Handle `<input type="file">` uploads safely via Android ActivityResultContracts.
 * 5. Handle missing assets, main-frame load errors, and renderer crashes gracefully.
 * 6. Preserve WebView state across configuration changes and lifecycle transitions.
 */
class MainActivity : ComponentActivity() {

  private var pendingFileChooserCallback: ValueCallback<Array<Uri>>? = null
  private var webViewInstance: WebView? = null
  private var savedWebViewBundle: Bundle? = null

  private val fileChooserLauncher =
    registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
      val callback = pendingFileChooserCallback
      pendingFileChooserCallback = null
      if (callback == null) return@registerForActivityResult

      if (result.resultCode == Activity.RESULT_OK) {
        val data = result.data
        val uris =
          when {
            data?.clipData != null -> {
              val clip = data.clipData!!
              Array(clip.itemCount) { idx -> clip.getItemAt(idx).uri }
            }
            data?.data != null -> arrayOf(data.data!!)
            else -> null
          }
        callback.onReceiveValue(uris)
      } else {
        callback.onReceiveValue(null)
      }
    }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    savedWebViewBundle = savedInstanceState?.getBundle(KEY_WEBVIEW_STATE)
    enableEdgeToEdge()

    val appConfig = TemplateAppConfig.fromContext(applicationContext)
    applyWindowBehavior(appConfig)
    val requestedPermissions = buildList {
      if (appConfig.cameraAccess) add(Manifest.permission.CAMERA)
      if (appConfig.microphoneAccess) add(Manifest.permission.RECORD_AUDIO)
    }.toTypedArray()
    if (requestedPermissions.isNotEmpty() && requestedPermissions.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) {
      requestPermissions(requestedPermissions, REQUEST_MEDIA_PERMISSIONS)
    }

    setContent {
      AppTemplateTheme(darkTheme = true) {
        WebApkTemplateHost(
          config = appConfig,
          savedStateBundle = savedWebViewBundle,
          onWebViewCreated = { webViewInstance = it },
          onLaunchFileChooser = { callback -> launchFilePicker(callback) },
        )
      }
    }
  }

  override fun onSaveInstanceState(outState: Bundle) {
    super.onSaveInstanceState(outState)
    val webBundle = Bundle()
    webViewInstance?.saveState(webBundle)
    outState.putBundle(KEY_WEBVIEW_STATE, webBundle)
  }

  override fun onDestroy() {
    pendingFileChooserCallback?.onReceiveValue(null)
    pendingFileChooserCallback = null
    webViewInstance?.apply {
      stopLoading()
      webChromeClient = null
      destroy()
    }
    webViewInstance = null
    super.onDestroy()
  }

  private fun applyWindowBehavior(config: TemplateAppConfig) {
    val immersive = config.hideTitleBar || config.fullscreen
    window.decorView.systemUiVisibility = if (immersive) {
      View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
        View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
        View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
    } else {
      View.SYSTEM_UI_FLAG_LAYOUT_STABLE
    }
  }

  override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    if (requestCode == REQUEST_MEDIA_PERMISSIONS && grantResults.any { it != PackageManager.PERMISSION_GRANTED }) {
      Toast.makeText(this, "Some website permissions were not granted.", Toast.LENGTH_SHORT).show()
    }
  }

  private fun launchFilePicker(callback: ValueCallback<Array<Uri>>): Boolean {
    pendingFileChooserCallback?.onReceiveValue(null)
    pendingFileChooserCallback = callback
    return try {
      val intent =
        Intent(Intent.ACTION_GET_CONTENT).apply {
          addCategory(Intent.CATEGORY_OPENABLE)
          type = "*/*"
          putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
      fileChooserLauncher.launch(Intent.createChooser(intent, null))
      true
    } catch (_: Exception) {
      pendingFileChooserCallback = null
      callback.onReceiveValue(null)
      false
    }
  }

  companion object {
    private const val KEY_WEBVIEW_STATE = "template_webview_state_bundle"
    private const val REQUEST_MEDIA_PERMISSIONS = 7401
  }
}

@Composable
fun WebApkTemplateHost(
  config: TemplateAppConfig,
  savedStateBundle: Bundle?,
  onWebViewCreated: (WebView) -> Unit,
  onLaunchFileChooser: (ValueCallback<Array<Uri>>) -> Boolean,
) {
  val context = LocalContext.current
  val lifecycleOwner = LocalLifecycleOwner.current

  val initialAssetExists =
    remember(config.webEntryAssetPath) {
      TemplateAppConfig.verifyEntryAssetExists(context, config.webEntryAssetPath)
    }

  var containerState by remember {
    mutableStateOf<WebContainerState>(
      if (initialAssetExists) {
        WebContainerState.Ready
      } else {
        WebContainerState.Error(
          failingUrl = config.webEntryUrl,
          errorCode = -404,
          description = context.getString(R.string.error_missing_entry_point),
          isEntryAssetMissing = true,
        )
      }
    )
  }

  var canGoBackInWebView by remember { mutableStateOf(false) }
  var webViewGeneration by remember { mutableIntStateOf(0) }
  var activeWebView by remember { mutableStateOf<WebView?>(null) }

  var lastBackPressAt by remember { mutableStateOf(0L) }
  // Intercept Android system back button/gesture according to the builder setting.
  BackHandler(enabled = containerState is WebContainerState.Ready) {
    val webView = activeWebView
    if (webView != null && webView.canGoBack()) {
      webView.goBack()
      canGoBackInWebView = webView.canGoBack()
    } else {
      val activity = context as? Activity
      if (config.exitConfirmation) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastBackPressAt < 2000L) activity?.finish()
        else {
          lastBackPressAt = now
          Toast.makeText(context, "Press back again to exit", Toast.LENGTH_SHORT).show()
        }
      } else activity?.finish()
    }
  }

  // Tie WebView pause/resume to the Android Activity lifecycle
  DisposableEffect(lifecycleOwner, activeWebView) {
    val observer = LifecycleEventObserver { _, event ->
      when (event) {
        Lifecycle.Event.ON_RESUME -> {
          activeWebView?.onResume()
          activeWebView?.resumeTimers()
        }
        Lifecycle.Event.ON_PAUSE -> {
          activeWebView?.onPause()
          activeWebView?.pauseTimers()
        }
        else -> Unit
      }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
  }

  Surface(
    modifier =
      Modifier.fillMaxSize()
        .background(MaterialTheme.colorScheme.background)
        .windowInsetsPadding(WindowInsets.safeDrawing)
        .testTag("template_root_container"),
    color = MaterialTheme.colorScheme.background,
  ) {
    Box(modifier = Modifier.fillMaxSize()) {
      if (initialAssetExists || containerState is WebContainerState.Ready) {
        val callbacks =
          remember(config) {
            object : TemplateWebViewCallbacks {
              override fun onHistoryStateChanged(canGoBack: Boolean) {
                canGoBackInWebView = canGoBack
              }

              override fun onMainFrameLoadSuccess(url: String) {
                if (containerState is WebContainerState.Error) {
                  containerState = WebContainerState.Ready
                }
              }

              override fun onMainFrameLoadError(
                failingUrl: String,
                errorCode: Int,
                description: String,
              ) {
                val existsNow =
                  TemplateAppConfig.verifyEntryAssetExists(context, config.webEntryAssetPath)
                containerState =
                  WebContainerState.Error(
                    failingUrl = failingUrl,
                    errorCode = errorCode,
                    description = description,
                    isEntryAssetMissing = !existsNow,
                  )
              }

              override fun onShowFileChooser(
                filePathCallback: ValueCallback<Array<Uri>>,
              ): Boolean {
                return onLaunchFileChooser(filePathCallback)
              }

              override fun onRendererGone() {
                // Automatically recreate the WebView instance if the OS reclaims the renderer
                activeWebView?.destroy()
                activeWebView = null
                webViewGeneration += 1
              }
            }
          }

        androidx.compose.runtime.key(webViewGeneration) {
          AndroidView(
            modifier = Modifier.fillMaxSize().testTag("template_webview"),
            factory = { ctx ->
              TemplateWebViewController.createConfiguredWebView(
                context = ctx,
                config = config,
                callbacks = callbacks,
              ).also { webView ->
                activeWebView = webView
                onWebViewCreated(webView)
                if (savedStateBundle != null && !savedStateBundle.isEmpty) {
                  webView.restoreState(savedStateBundle)
                } else {
                  loadConfiguredEntry(ctx, webView, config)
                }
              }
            },
            update = { webView -> canGoBackInWebView = webView.canGoBack() },
          )
        }
      }

      // Render diagnostic error overlay only if the entry asset is missing or main frame failed
      val currentState = containerState
      if (currentState is WebContainerState.Error) {
        WebLoadErrorFallbackScreen(
          config = config,
          errorState = currentState,
          onRetry = {
            val assetNowExists =
              TemplateAppConfig.verifyEntryAssetExists(context, config.webEntryAssetPath)
            if (assetNowExists) {
              containerState = WebContainerState.Ready
              val webView = activeWebView
              if (webView != null) {
                webView.reload()
              } else {
                webViewGeneration += 1
              }
            } else {
              containerState = currentState.copy(isEntryAssetMissing = true)
            }
          },
          onLoadEntryHome = {
            val assetNowExists =
              TemplateAppConfig.verifyEntryAssetExists(context, config.webEntryAssetPath)
            if (assetNowExists) {
              containerState = WebContainerState.Ready
                activeWebView?.let { loadConfiguredEntry(context, it, config) }
            }
          },
        )
      }
    }
  }
}

/** Loads a remote URL normally, or decrypts the local AES-GCM entry only in memory. */
private fun loadConfiguredEntry(context: android.content.Context, webView: WebView, config: TemplateAppConfig) {
  if (config.webStartUrl.isNotBlank() || !config.encryptedAssets) {
    webView.loadUrl(config.webStartUrl.ifBlank { config.webEntryUrl })
    return
  }
  try {
    val blob = context.assets.open("${TemplateAppConfig.DEFAULT_WEB_FOLDER}/index.html.enc").use { it.readBytes() }
    require(blob.size > 16 && String(blob, 0, 4, StandardCharsets.US_ASCII) == "DVL1")
    val nonce = blob.copyOfRange(4, 16)
    val ciphertext = blob.copyOfRange(16, blob.size)
    val key = Base64.decode(config.encryptionKeyBase64, Base64.DEFAULT)
    require(key.size == 32)
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
    val html = String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8)
    webView.loadDataWithBaseURL(config.webEntryUrl, html, "text/html", "UTF-8", config.webEntryUrl)
  } catch (error: Exception) {
    android.util.Log.e("WebApkTemplate", "Could not decrypt encrypted HTML entry", error)
    webView.loadDataWithBaseURL(config.webEntryUrl, "<h1>Unable to open encrypted app</h1>", "text/html", "UTF-8", config.webEntryUrl)
  }
}

@Composable
private fun WebLoadErrorFallbackScreen(
  config: TemplateAppConfig,
  errorState: WebContainerState.Error,
  onRetry: () -> Unit,
  onLoadEntryHome: () -> Unit,
) {
  Box(
    modifier =
      Modifier.fillMaxSize()
        .background(MaterialTheme.colorScheme.background)
        .padding(24.dp)
        .testTag("template_error_screen"),
    contentAlignment = Alignment.Center,
  ) {
    Card(
      modifier = Modifier.fillMaxWidth().widthIn(max = 520.dp),
      shape = RoundedCornerShape(20.dp),
      colors =
        CardDefaults.cardColors(
          containerColor = MaterialTheme.colorScheme.surface,
        ),
      elevation = CardDefaults.cardElevation(defaultElevation = 6.dp),
    ) {
      Column(
        modifier = Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
      ) {
        Row(
          verticalAlignment = Alignment.CenterVertically,
          horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
          Icon(
            imageVector = Icons.Default.Warning,
            contentDescription = stringResource(R.string.error_title_load_failed),
            tint = MaterialTheme.colorScheme.error,
            modifier = Modifier.size(32.dp),
          )
          Column {
            Text(
              text = stringResource(R.string.error_title_load_failed),
              style = MaterialTheme.typography.titleLarge,
              fontWeight = FontWeight.Bold,
              color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
              text = config.appName,
              style = MaterialTheme.typography.bodySmall,
              color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f),
            )
          }
        }

        Text(
          text =
            if (errorState.isEntryAssetMissing) {
              stringResource(R.string.error_missing_entry_point)
            } else {
              errorState.description
            },
          style = MaterialTheme.typography.bodyMedium,
          color = MaterialTheme.colorScheme.onSurface,
        )

        HorizontalDivider(color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f))

        Text(
          text = stringResource(R.string.error_diagnostics_heading),
          style = MaterialTheme.typography.labelLarge,
          color = MaterialTheme.colorScheme.primary,
          fontWeight = FontWeight.SemiBold,
        )

        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
          DiagnosticRow("Expected Asset", "app/src/main/assets/${config.webEntryAssetPath}")
          DiagnosticRow("Target URL", errorState.failingUrl)
          DiagnosticRow("Error Code", errorState.errorCode.toString())
          DiagnosticRow("Application ID", config.applicationId)
          DiagnosticRow("Version", "${config.versionName} (${config.versionCode})")
        }

        Spacer(modifier = Modifier.height(4.dp))

        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
          Button(
            onClick = onRetry,
            modifier = Modifier.weight(1f).height(48.dp).testTag("error_retry_button"),
          ) {
            Icon(
              imageVector = Icons.Default.Refresh,
              contentDescription = null,
              modifier = Modifier.size(18.dp),
            )
            Spacer(modifier = Modifier.size(8.dp))
            Text(text = stringResource(R.string.error_action_retry))
          }

          OutlinedButton(
            onClick = onLoadEntryHome,
            modifier = Modifier.weight(1f).height(48.dp).testTag("error_home_button"),
          ) {
            Icon(
              imageVector = Icons.Default.Home,
              contentDescription = null,
              modifier = Modifier.size(18.dp),
            )
            Spacer(modifier = Modifier.size(8.dp))
            Text(text = stringResource(R.string.error_action_load_home))
          }
        }
      }
    }
  }
}

@Composable
private fun DiagnosticRow(label: String, value: String) {
  Column(modifier = Modifier.fillMaxWidth()) {
    Text(
      text = label,
      style = MaterialTheme.typography.labelSmall,
      color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f),
    )
    Text(
      text = value,
      style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
      color = MaterialTheme.colorScheme.onSurface,
    )
  }
}
