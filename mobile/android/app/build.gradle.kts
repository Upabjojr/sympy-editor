plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "org.sympy.editor"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.sympy.editor"
        minSdk = 24          // the app's CPython (Chaquopy 16) needs Android 7.0
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        // Chaquopy ships a CPython runtime per ABI: these two cover phones,
        // tablets and the emulator (every other ABI is long obsolete).
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }

    // Release signing from the environment (see mobile/README.md); without a
    // keystore the release APK/AAB is built unsigned and can be signed later.
    val keystore = System.getenv("ANDROID_KEYSTORE")
    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    androidResources { noCompress += listOf("wasm", "whl", "zip", "woff2") }
    packaging { resources { excludes += listOf("META-INF/*.kotlin_module") } }
}

// The app's Python: CPython plus SymPy, installed at build time from PyPI.
// src/main/python holds the app's own module (sympy_editor_app.py) and the
// copy of the sympy_editor package that mobile/build.py puts there.
chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("sympy==1.14.0")
            // the bundled add-ons' requirements (addons/*/addon.json "requires";
            // a test keeps this list in step with the manifests)
            install("sympy-matching>=0.0.4")
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
}
