import com.google.gms.googleservices.GoogleServicesPlugin.MissingGoogleServicesStrategy

plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.kotlin.compose)
  alias(libs.plugins.google.devtools.ksp)
  alias(libs.plugins.secrets)
  alias(libs.plugins.google.services)
}

// ==============================================================================
// CENTRALIZED BACKEND CONFIGURATION EXTRACTION
// Reads from gradle.properties or CLI (-PKEY=VALUE) or Environment Variables
// ==============================================================================
fun getConfigProperty(key: String, defaultValue: String): String {
  return (project.findProperty(key)?.toString()?.trim()?.takeIf { it.isNotEmpty() })
    ?: System.getenv(key)?.trim()?.takeIf { it.isNotEmpty() }
    ?: defaultValue
}
fun javaStringLiteral(value: String): String = buildString {
  append('"')
  value.forEach { character ->
    when (character) {
      '\\' -> append("\\\\")
      '"' -> append("\\\"")
      '\n' -> append("\\n")
      '\r' -> append("\\r")
      '\t' -> append("\\t")
      else -> append(character)
    }
  }
  append('"')
}

val configAppName = getConfigProperty("APP_NAME", "WebAPK Template")
val configPackageName = getConfigProperty("PACKAGE_NAME", "com.aistudio.webapktemplate.k9x4qm")
val configVersionName = getConfigProperty("VERSION_NAME", "1.0.0")
val configVersionCode = getConfigProperty("VERSION_CODE", "1").toIntOrNull() ?: 1
val configMinSdk = getConfigProperty("MIN_SDK", "24").toIntOrNull() ?: 24
val configTargetSdk = getConfigProperty("TARGET_SDK", "36").toIntOrNull() ?: 36
val configOrientation = getConfigProperty("ORIENTATION", "unspecified")
val configAppTheme = getConfigProperty("APPLICATION_THEME", "Theme.AppTemplate")
val configWebEntryPath = getConfigProperty("WEB_ENTRY_PATH", "web/index.html")
val configWebStartUrl = getConfigProperty("WEB_START_URL", "")
val configAllowExternalNetwork = getConfigProperty("ALLOW_EXTERNAL_NETWORK", "true").toBoolean()
val configEnableWebViewDebug = getConfigProperty("ENABLE_WEBVIEW_DEBUG", "false").toBoolean()
val configSplashBgColor = getConfigProperty("SPLASH_BG_COLOR", "#0F172A")

android {
  // Namespace remains fixed for internal R class and source package stability
  namespace = "com.example"
  compileSdk { version = release(36) { minorApiLevel = 1 } }

  defaultConfig {
    applicationId = configPackageName
    minSdk = configMinSdk
    targetSdk = configTargetSdk
    versionCode = configVersionCode
    versionName = configVersionName

    testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

    // Inject dynamic Manifest placeholders so AndroidManifest.xml never requires manual edits
    manifestPlaceholders["appOrientation"] = configOrientation
    manifestPlaceholders["appTheme"] = "@style/$configAppTheme"

    // Inject dynamic string & color resources if overridden via CLI or gradle.properties
    resValue("color", "splash_background", configSplashBgColor)

    // Expose template configuration to Kotlin runtime via BuildConfig
    buildConfigField("String", "APP_NAME", javaStringLiteral(configAppName))
    buildConfigField("String", "WEB_ENTRY_PATH", javaStringLiteral(configWebEntryPath))
    buildConfigField("String", "WEB_START_URL", javaStringLiteral(configWebStartUrl))
    buildConfigField("String", "CONFIGURED_ORIENTATION", javaStringLiteral(configOrientation))
    buildConfigField("String", "CONFIGURED_THEME", javaStringLiteral(configAppTheme))
    buildConfigField("boolean", "ALLOW_EXTERNAL_NETWORK", configAllowExternalNetwork.toString())
    buildConfigField("boolean", "ENABLE_WEBVIEW_DEBUG", configEnableWebViewDebug.toString())
  }

  // Do not remove or modify these signingConfigs. They are necessary for building, installing,
  // and updating Android apps in AI Studio, and also support headless backend release signing
  // via ANDROID_KEYSTORE_PATH / KEYSTORE_PATH environment variables.
  signingConfigs {
    create("release") {
      val keystorePath =
        System.getenv("KEYSTORE_PATH")
          ?: System.getenv("ANDROID_KEYSTORE_PATH")
          ?: "${rootDir}/my-upload-key.jks"
      storeFile = file(keystorePath)
      storePassword =
        System.getenv("STORE_PASSWORD") ?: System.getenv("ANDROID_KEYSTORE_PASSWORD")
      keyAlias =
        System.getenv("KEY_ALIAS") ?: System.getenv("ANDROID_KEY_ALIAS") ?: "upload"
      keyPassword =
        System.getenv("KEY_PASSWORD") ?: System.getenv("ANDROID_KEY_PASSWORD")
    }
    create("debugConfig") {
      storeFile = file("${rootDir}/debug.keystore")
      storePassword = "android"
      keyAlias = "androiddebugkey"
      keyPassword = "android"
    }
  }

  buildTypes {
    release {
      isCrunchPngs = false
      isMinifyEnabled = false
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
      // Do not remove or modify this signingConfig assignment. It is necessary for Android apps in
      // AI Studio.
      signingConfig = signingConfigs.getByName("release")
    }
    debug {
      // Do not remove or modify this signingConfig assignment. It is necessary for Android apps in
      // AI Studio.
      signingConfig = signingConfigs.getByName("debugConfig")
    }
  }
  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_11
    targetCompatibility = JavaVersion.VERSION_11
  }
  buildFeatures {
    compose = true
    buildConfig = true
    resValues = true
  }
  testOptions { unitTests { isIncludeAndroidResources = true } }
  dependenciesInfo {
    includeInApk = false
    includeInBundle = true
  }
}

// Configure the Secrets Gradle Plugin to use .env and .env.example files
// to match the convention used in Web projects.
secrets {
  propertiesFileName = ".env"
  defaultPropertiesFileName = ".env.example"
  ignoreList.add("FIREBASE_APPCHECK_DEBUG_TOKEN")
}

googleServices { missingGoogleServicesStrategy = MissingGoogleServicesStrategy.WARN }

// Unused dependencies are commented out to keep APK size minimal and headless builds fast.
dependencies {
  implementation(platform(libs.androidx.compose.bom))
  // implementation(platform(libs.firebase.bom))
  // implementation(libs.accompanist.permissions)
  implementation(libs.androidx.activity.compose)
  // implementation(libs.androidx.camera.camera2)
  // implementation(libs.androidx.camera.core)
  // implementation(libs.androidx.camera.lifecycle)
  // implementation(libs.androidx.camera.view)
  implementation(libs.androidx.compose.material.icons.core)
  implementation(libs.androidx.compose.material.icons.extended)
  implementation(libs.androidx.compose.material3)
  implementation(libs.androidx.compose.ui)
  implementation(libs.androidx.compose.ui.graphics)
  implementation(libs.androidx.compose.ui.tooling.preview)
  implementation(libs.androidx.core.ktx)
  // implementation(libs.androidx.datastore.preferences)
  implementation(libs.androidx.lifecycle.runtime.compose)
  implementation(libs.androidx.lifecycle.runtime.ktx)
  implementation(libs.androidx.lifecycle.viewmodel.compose)
  // implementation(libs.androidx.navigation.compose)
  // implementation(libs.androidx.room.ktx)
  // implementation(libs.androidx.room.runtime)
  // implementation(libs.coil.compose)
  // implementation(libs.converter.moshi)
  // implementation(libs.firebase.ai)
  // implementation(libs.firebase.firestore)
  // implementation(libs.firebase.auth)
  // implementation(libs.androidx.credentials)
  // implementation(libs.androidx.credentials.play.services)
  // implementation(libs.googleid)
  // implementation(libs.firebase.appcheck.recaptcha)
  // implementation(libs.firebase.appcheck.debug)
  implementation(libs.kotlinx.coroutines.android)
  implementation(libs.kotlinx.coroutines.core)
  // implementation(libs.logging.interceptor)
  // implementation(libs.moshi.kotlin)
  // implementation(libs.okhttp)
  // implementation(libs.play.services.location)
  // implementation(libs.retrofit)
  testImplementation(libs.androidx.compose.ui.test.junit4)
  testImplementation(libs.androidx.core)
  testImplementation(libs.androidx.junit)
  testImplementation(libs.junit)
  testImplementation(libs.kotlinx.coroutines.test)
  testImplementation(libs.robolectric)
  androidTestImplementation(platform(libs.androidx.compose.bom))
  androidTestImplementation(libs.androidx.compose.ui.test.junit4)
  androidTestImplementation(libs.androidx.espresso.core)
  androidTestImplementation(libs.androidx.junit)
  androidTestImplementation(libs.androidx.runner)
  debugImplementation(libs.androidx.compose.ui.test.manifest)
  debugImplementation(libs.androidx.compose.ui.tooling)
  // "ksp"(libs.androidx.room.compiler)
  // "ksp"(libs.moshi.kotlin.codegen)
}
