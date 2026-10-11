package com.example

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.example.config.TemplateAppConfig
import com.example.webview.TemplateJsBridge
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class ExampleRobolectricTest {

  @Test
  fun `read app_name and verify template configuration from context`() {
    val context = ApplicationProvider.getApplicationContext<Context>()
    val appName = context.getString(R.string.app_name)
    assertEquals("WebAPK Template", appName)

    val config = TemplateAppConfig.fromContext(context)
    assertEquals("WebAPK Template", config.appName)
    assertEquals("web/index.html", config.webEntryAssetPath)
    assertEquals("file:///android_asset/web/index.html", config.webEntryUrl)
    assertEquals("", config.webStartUrl)
    assertTrue(
      "Expected placeholder assets/web/index.html to exist",
      TemplateAppConfig.verifyEntryAssetExists(context, config.webEntryAssetPath),
    )
    assertTrue(
      "Expected nested JSON asset to exist",
      TemplateAppConfig.verifyEntryAssetExists(context, "web/data/template-manifest.json"),
    )
  }

  @Test
  fun `template js bridge returns valid metadata json and checks assets safely`() {
    val context = ApplicationProvider.getApplicationContext<Context>()
    val config = TemplateAppConfig.fromContext(context)
    val bridge = TemplateJsBridge(context, config)

    val json = JSONObject(bridge.getAppConfigJson())
    assertEquals("WebAPK Template", json.getString("appName"))
    assertEquals("web/index.html", json.getString("webEntryAssetPath"))
    assertTrue(json.getBoolean("isTemplateReady"))

    assertTrue(bridge.isAssetPresent("index.html"))
    assertTrue(bridge.isAssetPresent("css/template.css"))
    assertTrue(bridge.isAssetPresent("js/template.js"))
    assertFalse(bridge.isAssetPresent("nonexistent.html"))
  }
}
