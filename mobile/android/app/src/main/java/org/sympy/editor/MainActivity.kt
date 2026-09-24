package org.sympy.editor

import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.net.Uri
import android.print.PrintAttributes
import android.print.PrintManager
import android.view.HapticFeedbackConstants
import android.view.ViewGroup
import android.webkit.RenderProcessGoneDetail
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.addCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
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

    /** The origin the bundle is served on (WebViewAssetLoader's), the only
     *  one this WebView navigates to. */
    private val BUNDLE_HOST = "appassets.androidplatform.net"

    /** The file the page asked to keep, waiting for the user to say where:
     *  Android's own "create document" dialog answers in [saveTo].  Its text
     *  waits in a file of the cache, and the file's name and type go into the
     *  saved instance state: the activity may be recreated, or the process
     *  ended, while the dialog is up, and the answer comes to the new one. */
    private var pending: PendingSave? = null

    /** What ``SympyEditor.openedFile`` is waiting for: the token the page
     *  gave when it asked for a file, or null when nothing was asked.  Kept
     *  across a recreation like [pending]. */
    private var opening: String? = null

    /** The page's renderer died and the activity is being made again: the
     *  dead WebView has no state worth saving. */
    private var renderGone = false

    /** Android's create-document dialog: the page's text goes where the user
     *  says, under the name it asked for. */
    private lateinit var saveTo: ActivityResultLauncher<String>

    /** Android's open-document dialog: what the user picks is read and given
     *  to the page (see [answerOpen]). */
    private lateinit var openFrom: ActivityResultLauncher<Array<String>>

    /** Whether the page asked for full screen.  Android brings the system
     *  bars back whenever the window loses and regains focus (the
     *  notification shade, a dialog, the recents screen), so the wish has to
     *  be remembered and applied again - see [onWindowFocusChanged]. */
    private var wantsFullscreen = false

    /** Whether the page has loaded: a file opened with the app from elsewhere
     *  waits in [arrived] until it has, since there is nobody to hand it to. */
    private var pageReady = false
    private val arrived = ArrayDeque<Pair<String, String>>()

    /** The WebView a report is being printed from, held until it has been
     *  handed to the print service (nothing else refers to it meanwhile). */
    private var printing: WebView? = null

    /** The app's Python module (sympy_editor_app.py), started on first use. */
    private val pythonApp: PyObject by lazy {
        if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
        Python.getInstance().getModule("sympy_editor_app")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        // ONNX Runtime reads this when it starts (the handwriting model, later):
        // no telemetry uploader, no events, no device identifier.  The
        // manifest already keeps its provider and the network out.
        try { android.system.Os.setenv("ORT_DISABLE_TELEMETRY", "1", true) } catch (_: Exception) {}
        super.onCreate(savedInstanceState)
        // Before anything else: the dialogs that keep and open a file must be
        // registered while the activity is being created (AndroidX insists).
        registerPickers()
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

            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                pageReady = true
                while (arrived.isNotEmpty()) arrived.removeFirst().let { (name, text) -> handOver(name, text) }
            }

            /** Only the bundle is shown in this WebView.  The two bridges
             *  above are injected into whatever page it loads, and the
             *  Python one evaluates what it is given: a page from anywhere
             *  else must never get them.  Any other link opens outside. */
            /** The renderer died (the system reclaimed its memory, or it
             *  crashed): the WebView is dead with it, and without this the
             *  app would go too.  The activity is made again with a new one,
             *  and the page opens the sessions it keeps, as at a launch. */
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                if (view !== web || renderGone) return true
                renderGone = true
                pageReady = false
                recreate()
                return true
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if (url.scheme == "https" && url.host == BUNDLE_HOST) return false
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                } catch (e: android.content.ActivityNotFoundException) {
                    // nothing can open it: then nothing does
                }
                return true
            }
        }
        // Back belongs to the page first: it closes the drawer, the history,
        // the help, a chooser, the selection - whatever is open, as Esc does
        // on a keyboard (SympyEditor.back).  The history of the WebView is no
        // help: the page is one document and never navigates.  With nothing
        // left to close the app goes to the background, as Android's own
        // launcher activities do - its state stays where it was.
        onBackPressedDispatcher.addCallback(this) {
            web.evaluateJavascript("!!(window.SympyEditor && window.SympyEditor.back && window.SympyEditor.back())") { closed ->
                if (closed != "true") moveTaskToBack(true)
            }
        }

        // A save or an opening the user was answering when the activity went.
        savedInstanceState?.let { state ->
            val path = state.getString(STATE_PENDING_PATH)
            if (path != null) pending = PendingSave(state.getString(STATE_PENDING_MIME) ?: "*/*", path)
            opening = state.getString(STATE_OPENING)
        }
        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl("https://appassets.androidplatform.net/assets/www/index.html")
        }
        if (savedInstanceState == null) receive(intent)
    }

    /** The app, already running, asked to open a file (singleTask). */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        receive(intent)
    }

    /** Going to the background, where the system may end the app without
     *  another word: the page keeps now what it was about to keep. */
    override fun onPause() {
        super.onPause()
        web.evaluateJavascript("window.SympyEditor && window.SympyEditor.flush && window.SympyEditor.flush();", null)
    }

    /** A file opened with the app from elsewhere - a .sympy file tapped in a
     *  file manager or a mail (VIEW), or something shared to it (SEND: a file,
     *  or a formula as text) - is read and handed to the page, which opens it
     *  in a session of its own. */
    private fun receive(intent: Intent?) {
        if (intent == null) return
        val uri: Uri? = when (intent.action) {
            Intent.ACTION_VIEW -> intent.data
            Intent.ACTION_SEND -> if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_STREAM)
            }
            else -> return
        }
        if (uri == null) {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)
            if (intent.action == Intent.ACTION_SEND && !text.isNullOrBlank()) arrive("shared", text)
            return
        }
        pythonThread.execute {
            try {
                val size = contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length } ?: -1L
                if (size > MAX_OPEN_BYTES) throw java.io.IOException("it is too large to be a formula")
                val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
                    ?: throw java.io.IOException("nothing could be read")
                val name = nameOf(uri)
                runOnUiThread { arrive(name, text) }
            } catch (exc: Exception) {
                report("The file could not be opened: " + (exc.message ?: exc.toString()))
            }
        }
    }

    private fun arrive(name: String, text: String) {
        if (pageReady) handOver(name, text) else arrived.addLast(name to text)
    }

    private fun handOver(name: String, text: String) {
        web.evaluateJavascript("window.SympyEditor && window.SympyEditor.openText(" +
            "${JSONObject.quote(name)}, ${JSONObject.quote(text)});", null)
    }

    override fun onDestroy() {
        // The WebView goes with the activity (its renderer, its bridges):
        // out of the layout first, as WebView.destroy() asks.  The Python
        // thread is the process's and stays - a recreated activity uses it.
        (web.parent as? ViewGroup)?.removeView(web)
        web.destroy()
        printing?.destroy()
        printing = null
        super.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (!renderGone) web.saveState(outState)
        pending?.let {
            outState.putString(STATE_PENDING_PATH, it.path)
            outState.putString(STATE_PENDING_MIME, it.mime)
        }
        opening?.let { outState.putString(STATE_OPENING, it) }
    }

    /** Run `js` in the page, on the UI thread - unless the activity is gone:
     *  a Python answer can arrive after a recreation, for a dead WebView. */
    private fun evaluate(js: String) {
        runOnUiThread { if (!isDestroyed) web.evaluateJavascript(js, null) }
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

        /** Forget the document `id` (the page left the session). */
        @JavascriptInterface
        fun close(req: String, id: String) = answer(req) {
            pythonApp.callAttr("close", id)
            ""
        }

        /** Stop the message being processed, if any.  Answered here, on the
         *  bridge's own thread: on the Python thread it would wait behind the
         *  very computation it is to stop.  Chaquopy takes the GIL for it,
         *  which that computation lets go of every few milliseconds. */
        @JavascriptInterface
        fun interrupt(req: String) = reply(req) { pythonApp.callAttr("interrupt").toString() }

        private fun answer(req: String, work: () -> String) = pythonThread.execute { reply(req, work) }

        private fun reply(req: String, work: () -> String) {
            var ok = true
            val payload = try {
                work()
            } catch (e: Throwable) {
                ok = false
                e.message ?: e.toString()
            }
            evaluate("window.__sympyEditorNative(${JSONObject.quote(req)}, $ok, ${JSONObject.quote(payload)});")
        }
    }

    /** Hide or show the system bars, following [wantsFullscreen]. */
    private fun applyFullscreen() {
        val controller = WindowCompat.getInsetsController(window, web)
        if (wantsFullscreen) {
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
        } else {
            controller.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // The system ignores a hide() from a window without focus and undoes
        // it when focus comes back: ask again every time it does.
        if (hasFocus && wantsFullscreen) applyFullscreen()
    }

    /** A file the page is keeping: its type, and the cache file its text
     *  waits in until the user says where. */
    private data class PendingSave(val mime: String, val path: String)

    /** Register the two dialogs.  They must be registered before the activity
     *  is started, so this is called from [onCreate]. */
    private fun registerPickers() {
        saveTo = registerForActivityResult(ActivityResultContracts.CreateDocument("*/*")) { uri: Uri? ->
            val save = pending
            pending = null
            if (save == null) return@registerForActivityResult
            val waiting = File(save.path)
            try {
                if (uri != null) {                                             // else nothing chosen
                    val bytes = waiting.readBytes()
                    contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
                }
            } catch (exc: Exception) {
                report("The file could not be written: " + (exc.message ?: exc.toString()))
            } finally {
                waiting.delete()
            }
        }
        openFrom = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
            val token = opening
            opening = null
            if (token == null) return@registerForActivityResult
            if (uri == null) { answerOpen(token, null, null); return@registerForActivityResult }
            try {
                val name = nameOf(uri)
                val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
                answerOpen(token, name, text)
            } catch (exc: Exception) {
                answerOpen(token, null, null)
                report("The file could not be read: " + (exc.message ?: exc.toString()))
            }
        }
    }

    /** Where the page's own things are kept: the app's files directory,
     *  one file per name.  A name from the page cannot reach out of it. */
    private fun keepFile(key: String): File {
        val safe = key.replace(Regex("[^A-Za-z0-9._-]"), "_").ifEmpty { "keep" }
        val dir = File(filesDir, "keep").apply { mkdirs() }
        return File(dir, "$safe.json")
    }

    /** What a document's own name is, as its provider gives it. */
    private fun nameOf(uri: Uri): String {
        contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { row ->
            if (row.moveToFirst() && !row.isNull(0)) return row.getString(0)
        }
        return uri.lastPathSegment?.substringAfterLast('/') ?: "formula"
    }

    /** Hand what was opened (or nothing) back to the page that asked. */
    private fun answerOpen(token: String, name: String?, text: String?) {
        val js = if (text == null) {
            "window.SympyEditor.openedFile(${JSONObject.quote(token)});"
        } else {
            "window.SympyEditor.openedFile(${JSONObject.quote(token)}, ${JSONObject.quote(name ?: "")}, " +
                "${JSONObject.quote(text)});"
        }
        evaluate(js)
    }

    /** Say something went wrong, in the page's own status line. */
    private fun report(message: String) {
        val js = "window.SympyEditor && window.SympyEditor.hostError && " +
            "window.SympyEditor.hostError(${JSONObject.quote(message)});"
        evaluate(js)
    }

    /** Print `html` through Android's print service, which also keeps it as
     *  a PDF.  From a WebView of its own, made for it: without scripts, with
     *  no bridge, and going nowhere - the report is static, and self-contained. */
    private fun printReport(name: String, html: String) {
        val view = WebView(this)
        printing = view
        view.settings.javaScriptEnabled = false
        view.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest) = true

            /** A renderer that dies takes the app with it unless every
             *  WebView it served says it has dealt with it - this one too. */
            override fun onRenderProcessGone(v: WebView, detail: RenderProcessGoneDetail): Boolean {
                if (printing === v) printing = null
                v.destroy()
                return true
            }

            override fun onPageFinished(v: WebView, url: String) {
                val manager = getSystemService(Context.PRINT_SERVICE) as? PrintManager
                if (manager == null) {
                    report("This phone has no print service")
                } else {
                    manager.print(name, v.createPrintDocumentAdapter(name), PrintAttributes.Builder().build())
                }
                printing = null
            }
        }
        view.loadDataWithBaseURL("https://$BUNDLE_HOST/print/", html, "text/html", "utf-8", null)
    }

    /** Answer a question of the page's (see ``Host.ask`` in editor.js). */
    private fun answerHost(token: String, value: String?) {
        val js = "window.SympyEditor && window.SympyEditor.hostAnswer(${JSONObject.quote(token)}" +
            (if (value == null) "" else ", ${JSONObject.quote(value)}") + ");"
        evaluate(js)
    }

    /** ``window.SympyEditorApp`` in the page. */
    inner class ReportBridge {
        /** Copy `text` to the system clipboard (the Copy button): Android's
         *  own, which every other app reads. */
        @JavascriptInterface
        fun copyText(text: String) {
            runOnUiThread {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                clipboard?.setPrimaryClip(ClipData.newPlainText("SymPy", text))
            }
        }

        /** What the system clipboard holds, as text (the Paste button),
         *  answered through ``SympyEditor.hostAnswer``.  A WebView's page is
         *  not let read the clipboard at all; the app is, while in front. */
        @JavascriptInterface
        fun pasteText(token: String) {
            runOnUiThread {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                val item = clipboard?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)
                answerHost(token, item?.coerceToText(this@MainActivity)?.toString())
            }
        }

        /** A touch the hand feels: a long press that selected. */
        @JavascriptInterface
        fun haptic(kind: String) {
            runOnUiThread {
                val constant = if (kind == "select") HapticFeedbackConstants.LONG_PRESS else HapticFeedbackConstants.KEYBOARD_TAP
                web.performHapticFeedback(constant)
            }
        }

        /** Print the history report (or keep it as a PDF). */
        @JavascriptInterface
        fun printHtml(name: String, html: String) {
            runOnUiThread { printReport(name.ifBlank { "SymPy history" }, html) }
        }

        /** Full screen for real: the page's own full-screen button asks the
         *  app to take the status and navigation bars away (a swipe from an
         *  edge brings them back transiently).  Without this the WebView
         *  keeps its inset padding and the bars stay: "full screen" would
         *  only mean the page hiding its own furniture. */
        @JavascriptInterface
        fun setFullscreen(on: Boolean) {
            wantsFullscreen = on
            runOnUiThread { applyFullscreen() }
        }

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

        /** Keep `text` as `name`: Android asks where, and writes it there.
         *  This is "Save formula" - sharing is what the history exports do. */
        @JavascriptInterface
        fun saveFile(name: String, mime: String, text: String) {
            // The text waits on disk, not in this activity: the dialog may
            // outlive it (a recreation, or the process ended meanwhile).
            val waiting = try {
                File(File(cacheDir, "saving").apply { mkdirs() }, "pending").apply { writeText(text) }
            } catch (exc: Exception) {
                report("The file could not be written: " + (exc.message ?: exc.toString()))
                return
            }
            runOnUiThread {
                pending = PendingSave(mime, waiting.path)
                try {
                    saveTo.launch(name)
                } catch (exc: Exception) {
                    pending = null
                    waiting.delete()
                    report("No app on this phone can keep a file: " + (exc.message ?: exc.toString()))
                }
            }
        }

        /** Bring the keyboard up for a field the page has just opened.
         *
         *  A WebView shows it by itself when a field is focused in answer to
         *  a tap, but not always for one the page puts there by script (the
         *  LaTeX add-on opens its field in the formula): the page asks, and
         *  the system is told. */
        @JavascriptInterface
        fun showKeyboard() {
            runOnUiThread {
                web.requestFocus()
                val manager = getSystemService(INPUT_METHOD_SERVICE) as? InputMethodManager
                manager?.showSoftInput(web, InputMethodManager.SHOW_IMPLICIT)
            }
        }

        /** What the page has kept under `key` (the sessions, with the history
         *  behind each), answered through ``SympyEditor.keptValue``.
         *
         *  The app's own directory, not the WebView's localStorage: that is
         *  web data, which the system clears without asking and a backup does
         *  not carry.  A session is a piece of the user's work. */
        @JavascriptInterface
        fun keepRead(token: String, key: String) {
            val text = try {
                keepFile(key).takeIf { it.isFile }?.readText()
            } catch (exc: Exception) {
                report("What was kept could not be read: " + (exc.message ?: exc.toString()))
                null
            }
            val js = if (text == null) {
                "window.SympyEditor.keptValue(${JSONObject.quote(token)});"
            } else {
                "window.SympyEditor.keptValue(${JSONObject.quote(token)}, ${JSONObject.quote(text)});"
            }
            evaluate(js)
        }

        /** Keep `text` under `key`, through a temporary file and a rename, so
         *  that an interrupted write leaves what was there before. */
        @JavascriptInterface
        fun keepWrite(key: String, text: String) {
            try {
                val file = keepFile(key)
                val temp = File(file.parentFile, file.name + ".new")
                temp.writeText(text)
                if (!temp.renameTo(file)) {
                    file.writeText(text)
                    temp.delete()
                }
            } catch (exc: Exception) {
                report("What the editor keeps could not be written: " + (exc.message ?: exc.toString()))
            }
        }

        /** Ask for a file to open.  `accept` is what the page will take, as a
         *  list of extensions and MIME types; Android wants MIME types, and
         *  a saved formula is JSON.  The answer goes to
         *  ``SympyEditor.openedFile(token, name, text)``. */
        @JavascriptInterface
        fun openFile(token: String, accept: String) {
            val types = if (accept.contains("json")) {
                arrayOf("application/json", "application/x-sympy-editor+json", "text/plain", "*/*")
            } else {
                arrayOf("*/*")
            }
            runOnUiThread {
                opening = token
                try {
                    openFrom.launch(types)
                } catch (exc: Exception) {
                    opening = null
                    answerOpen(token, null, null)
                    report("No app on this phone can offer a file: " + (exc.message ?: exc.toString()))
                }
            }
        }
    }

    companion object {
        /** The largest file taken as a formula: a saved one is a few kB. */
        private const val MAX_OPEN_BYTES = 20L * 1024 * 1024

        /** Python runs on one thread of its own: a long computation must not
         *  block the interface, and CPython objects belong to their thread.
         *  One for the process, not per activity: a recreated activity (or a
         *  message still running for the one before) must not put a second
         *  thread into the same documents. */
        private val pythonThread = Executors.newSingleThreadExecutor()

        /** Saved instance state: a pending save's text file and type, and
         *  the token of an opening the page waits for. */
        private const val STATE_PENDING_PATH = "sympyEditor.pendingSavePath"
        private const val STATE_PENDING_MIME = "sympyEditor.pendingSaveMime"
        private const val STATE_OPENING = "sympyEditor.opening"
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
