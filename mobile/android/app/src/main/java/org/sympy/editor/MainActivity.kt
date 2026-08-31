package org.sympy.editor

import android.annotation.SuppressLint
import android.content.ContentValues
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.widget.FrameLayout
import androidx.activity.addCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/**
 * The whole app: a WebView showing the shared bundle (assets/www, built by
 * mobile/build_www.py), and the Python the page edits with.
 *
 * The editing itself happens in the app's own CPython (Chaquopy: the runtime
 * and SymPy are packaged in the APK, see app/build.gradle.kts), not in the
 * browser - the page uses the "native" backend of editor.js and talks to
 * [PythonBridge] below.  The bundle is served through WebViewAssetLoader on
 * an https origin, because fetch() is not available to file:// pages.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView

    /** Python runs on one thread of its own: a long computation must not
     *  block the interface, and CPython objects belong to their thread. */
    private val pythonThread = Executors.newSingleThreadExecutor()

    /** The app's Python module (sympy_editor_app.py), started on first use. */
    private val pythonApp: PyObject by lazy {
        if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
        Python.getInstance().getModule("sympy_editor_app")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        // Android 15 draws the app edge to edge: keep the page clear of the
        // status bar, the navigation bar, display cutouts and rounded corners
        // (and of the keyboard) by padding a container with the insets; the
        // bars then show the page's own background colour.
        val night = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        val container = FrameLayout(this).apply {
            setBackgroundColor(if (night) Color.parseColor("#1e1e1e") else Color.WHITE)
            addView(web, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        setContentView(container)
        WindowCompat.getInsetsController(window, container).apply {
            isAppearanceLightStatusBars = !night
            isAppearanceLightNavigationBars = !night
        }
        ViewCompat.setOnApplyWindowInsetsListener(container) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout() or WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        // A debug build can be inspected from the desktop (chrome://inspect, or
        // adb forward + CDP): the page and its Python bridge, on the device.
        if ((applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            WebView.setWebContentsDebuggingEnabled(true)
        }
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.allowFileAccess = false
        // The page hands files (the history report) to the app: a WebView
        // cannot download a blob, so they go to Downloads and the share sheet.
        web.addJavascriptInterface(ReportBridge(), "SympyEditorApp")
        web.addJavascriptInterface(PythonBridge(), "SympyEditorPy")
        // Start Python (unpacking its assets on the first launch) while the
        // page loads, so the first edit does not wait for it.
        pythonThread.execute { pythonApp }
        // Without focus on the WebView itself, input.focus() from the page
        // does not bring up the soft keyboard.
        web.isFocusable = true
        web.isFocusableInTouchMode = true
        web.requestFocus(android.view.View.FOCUS_DOWN)

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

    override fun onDestroy() {
        super.onDestroy()
        pythonThread.shutdown()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    /** ``window.SympyEditorPy`` in the page: the native backend of editor.js
     *  hands it JSON messages, each with a request id, and gets the answer
     *  back through ``window.__sympyEditorNative(id, ok, payload)``.  Every
     *  call returns at once and is answered from the Python thread. */
    inner class PythonBridge {
        /** Create the document `id` from `srepr` (`settings` are Document
         *  keyword arguments as JSON); answers with its first snapshot. */
        @JavascriptInterface
        fun newDoc(req: String, id: String, srepr: String, settings: String) =
            answer(req) { pythonApp.callAttr("new_doc", id, srepr, settings).toString() }

        /** Process one front-end message for the document `id`. */
        @JavascriptInterface
        fun handle(req: String, id: String, message: String) =
            answer(req) { pythonApp.callAttr("handle", id, message).toString() }

        /** What the app is running, as JSON (Python and SymPy versions). */
        @JavascriptInterface
        fun version(req: String) = answer(req) { pythonApp.callAttr("version").toString() }

        private fun answer(req: String, work: () -> String) {
            pythonThread.execute {
                var ok = true
                val payload = try {
                    work()
                } catch (e: Throwable) {
                    ok = false
                    e.message ?: e.toString()
                }
                val js = "window.__sympyEditorNative(${JSONObject.quote(req)}, $ok, ${JSONObject.quote(payload)});"
                runOnUiThread { web.evaluateJavascript(js, null) }
            }
        }
    }

    /** ``window.SympyEditorApp`` in the page. */
    inner class ReportBridge {
        /** Save `html` as `name` in Downloads (Android 10+) and offer to share it. */
        @JavascriptInterface
        fun shareHtml(name: String, html: String) = shareFile(name, "text/html", html)

        /** Save `text` as `name` (of MIME type `mime`: the HTML report, the
         *  Python script) in Downloads (Android 10+) and offer to share it. */
        @JavascriptInterface
        fun shareFile(name: String, mime: String, text: String) {
            val safe = name.replace(Regex("[^A-Za-z0-9._-]"), "_")
            val dir = File(cacheDir, "reports").apply { mkdirs() }
            val file = File(dir, safe).apply { writeText(text) }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, safe)
                    put(MediaStore.Downloads.MIME_TYPE, mime)
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                }
                contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)?.let { uri ->
                    contentResolver.openOutputStream(uri)?.use { it.write(text.toByteArray()) }
                }
            }
            val uri = FileProvider.getUriForFile(this@MainActivity, "$packageName.fileprovider", file)
            val send = Intent(Intent.ACTION_SEND).apply {
                type = mime
                putExtra(Intent.EXTRA_STREAM, uri)
                putExtra(Intent.EXTRA_SUBJECT, safe)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            runOnUiThread { startActivity(Intent.createChooser(send, getString(R.string.share_report))) }
        }
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
