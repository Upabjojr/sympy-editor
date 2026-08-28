package org.sympy.editor

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.activity.addCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat

/**
 * The whole app: a WebView showing the shared bundle (assets/www, built by
 * mobile/build_www.py).  The bundle is served through WebViewAssetLoader on an
 * https origin, because fetch() and WebAssembly - which Pyodide needs - are
 * not available to file:// pages.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.allowFileAccess = false

        val assets = WebViewAssetLoader.AssetsPathHandler(this)
        val loader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/") { path -> assets.handle(path)?.also { fixMimeType(path, it) } }
            .build()
        web.webViewClient = object : WebViewClientCompat() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                loader.shouldInterceptRequest(request.url)
        }
        onBackPressedDispatcher.addCallback(this) { if (web.canGoBack()) web.goBack() else finish() }

        if (savedInstanceState != null) web.restoreState(savedInstanceState)
        else web.loadUrl("https://appassets.androidplatform.net/assets/www/index.html")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    /** The asset loader guesses MIME types from extensions and misses these. */
    private fun fixMimeType(path: String, response: WebResourceResponse) {
        val type = when (path.substringAfterLast('.', "").lowercase()) {
            "wasm" -> "application/wasm"
            "js", "mjs" -> "text/javascript"
            "css" -> "text/css"
            "json" -> "application/json"
            "whl", "zip" -> "application/zip"
            "woff2" -> "font/woff2"
            "html" -> "text/html"
            else -> return
        }
        response.mimeType = type
    }
}
