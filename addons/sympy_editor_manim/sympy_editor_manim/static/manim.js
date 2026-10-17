/*
 * sympy-editor add-on "manim": the history's steps as a Manim animation.
 * A plain script, run once per page with `SympyEditor` in scope.
 *
 * The panel asks Python for the scene (`script`: a Python file for Manim,
 * written without Manim) and shows it, to copy or save; where Python says
 * Manim is installed (`status`), Render runs it (`render`) and the video
 * plays in the panel, with a link to save it.  The script follows the
 * history: a committed change rewrites it when it is shown.
 */
(function () {
  var HELP = "<section><h3>Manim</h3>"
    + "<p>Writes a <a href=\"https://www.manim.community/\" target=\"_blank\" rel=\"noopener\">Manim</a> scene of this session's "
    + "steps: each formula turns into the next, the sub-expressions a change keeps travel to their new place while the rest "
    + "fades, and the editor's label of each step is the caption.</p><ul>"
    + "<li><b>Steps</b>: which part of the history to animate, the whole of it by default.</li>"
    + "<li><b>Style</b>: <i>matching</i> moves the parts the two steps share (Manim's TransformMatchingTex); <i>shapes</i> "
    + "moves glyphs of the same shape; <i>replace</i> morphs the whole formula; <i>write</i> fades the old formula out and "
    + "writes the new one.</li>"
    + "<li><b>Captions</b> puts the history's labels under the formula; <b>Theme</b> is the background; <b>Title</b> a line at the top.</li>"
    + "<li><b>Script</b> shows the Python file - <b>Copy</b> and <b>Save</b> take it; the constants at its top are the same settings, "
    + "to change by hand.  It needs nothing of the editor: <code>manim -ql formula_steps.py FormulaSteps</code> renders it "
    + "wherever Manim, LaTeX and ffmpeg are installed.</li>"
    + "<li><b>Render</b> runs Manim in the editor's own Python, where it is installed (<code>pip install manim</code>, or "
    + "<code>SYMPY_EDITOR_MANIM</code> naming a manim executable), and plays the result here; <b>Save video</b> keeps it.  "
    + "Rendering takes a while - the editor waits, and Interrupt stops it.</li>"
    + "</ul><p>Manim does not run in a browser page nor in the apps: there, the script is what the panel gives.</p></section>";

  function copyText(api, text) {
    var app = window.SympyEditorApp;
    if (app && typeof app.copyText === "function") {
      try { app.copyText(text); return Promise.resolve(); } catch (e) { /* fall through */ }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = api.h("textarea", { style: "position:fixed;opacity:0" });
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      if (ok) resolve(); else reject(new Error("the browser refused"));
    });
  }

  function option(h, value, label, selected) {
    var o = h("option", { value: value }, [label]);
    if (selected) o.selected = true;
    return o;
  }

  SympyEditor.registerAddon("manim", {
    tools: [],
    mount: function (api) {
      var h = api.h, o = api.options || {};
      var defaults = o.defaults || {};
      var destroyed = false, timer = null, shown = false, lastKey = null, status = null, n = 0;

      // -- the settings --------------------------------------------------------
      var from = h("select", { class: "mn-from", title: "The first step of the animation" });
      var to = h("select", { class: "mn-to", title: "The last step of the animation" });
      var style = h("select", { class: "mn-style", title: "How one step becomes the next" });
      (o.styles || ["matching"]).forEach(function (s) { style.appendChild(option(h, s, s, s === defaults.style)); });
      var captions = h("input", { type: "checkbox", class: "mn-captions" });
      captions.checked = defaults.captions !== false;
      var theme = h("select", { class: "mn-theme", title: "The background" });
      (o.themes || ["dark"]).forEach(function (t) { theme.appendChild(option(h, t, t, t === defaults.theme)); });
      var title = h("input", { type: "text", class: "mn-title", placeholder: "Title (none)", title: "A title at the top of the scene" });
      var count = h("span", { class: "mn-count" }, [""]);
      var settings = h("div", { class: "mn-settings" }, [
        h("label", {}, ["Steps ", from, " to ", to]), count,
        h("label", {}, ["Style ", style]),
        h("label", { class: "mn-check" }, [captions, "Captions"]),
        h("label", {}, ["Theme ", theme]),
        h("label", {}, [title])
      ]);

      // -- the script -------------------------------------------------------------
      var scriptBtn = h("button", { type: "button", class: "mn-script", title: "Show the Manim script of these steps" }, ["Script"]);
      var copyBtn = h("button", { type: "button", class: "mn-copy", title: "Copy the script" }, ["Copy"]);
      var saveBtn = h("button", { type: "button", class: "mn-save", title: "Save the script as a .py file" }, ["Save…"]);
      copyBtn.disabled = saveBtn.disabled = true;
      var code = h("pre", { class: "mn-code", hidden: "" });
      var scriptRow = h("div", { class: "mn-row" }, [scriptBtn, copyBtn, saveBtn]);

      // -- the video ----------------------------------------------------------------
      var quality = h("select", { class: "mn-quality", title: "The video's resolution" });
      var qualities = o.qualities || { l: "480p" };
      Object.keys(qualities).forEach(function (q) { quality.appendChild(option(h, q, qualities[q], q === "l")); });
      var format = h("select", { class: "mn-format", title: "The file's format" });
      (o.formats || ["mp4"]).forEach(function (f) { format.appendChild(option(h, f, f, f === "mp4")); });
      var renderBtn = h("button", { type: "button", class: "mn-render", title: "Render the scene with Manim, here" }, ["Render"]);
      renderBtn.disabled = true;
      var note = h("span", { class: "mn-note" }, ["Checking for Manim…"]);
      var renderRow = h("div", { class: "mn-row" }, [renderBtn, quality, format, note]);
      var player = h("div", { class: "mn-player", hidden: "" });
      var element = h("div", { class: "mn-panel" }, [settings, scriptRow, code, renderRow, player]);

      function payload() {
        return { from: from.value === "" ? null : parseInt(from.value, 10), to: to.value === "" ? null : parseInt(to.value, 10),
                 style: style.value, captions: captions.checked, theme: theme.value, title: title.value || null };
      }

      function fillSteps(total, index) {
        var f = from.value, t = to.value, wasLast = t === "" || parseInt(t, 10) === n - 1;
        n = total;
        from.textContent = "";
        to.textContent = "";
        for (var i = 0; i < total; i++) {
          from.appendChild(option(h, String(i), String(i + 1), false));
          to.appendChild(option(h, String(i), String(i + 1), false));
        }
        from.value = (f !== "" && parseInt(f, 10) < total) ? f : "0";
        to.value = (wasLast || t === "" || parseInt(t, 10) >= total) ? String(total - 1) : t;
        count.textContent = total + (total === 1 ? " step" : " steps") + (index != null && index < total - 1 ? " (at " + (index + 1) + ")" : "");
      }

      var current = null;
      function showScript() {
        if (destroyed) return;
        api.call("script", payload(), { quiet: true }).then(function (res) {
          if (destroyed) return;
          current = res;
          code.textContent = res.code;
          code.hidden = false;
          shown = true;
          copyBtn.disabled = saveBtn.disabled = false;
          scriptBtn.textContent = "Script ↻";
          scriptBtn.title = "Write the script again for these settings";
        }, function (e) { api.error("The script could not be written: " + String((e && e.message) || e)); });
      }

      function later(ms) {
        clearTimeout(timer);
        timer = setTimeout(function wait() {
          if (destroyed) return;
          if (api.busy()) { timer = setTimeout(wait, 150); return; }
          showScript();
        }, ms);
      }

      scriptBtn.addEventListener("click", function () { showScript(); });
      copyBtn.addEventListener("click", function () {
        if (!current) return;
        copyText(api, current.code).then(function () { api.status("Copied " + current.name); },
                                         function (e) { api.error("Could not copy: " + String((e && e.message) || e)); });
      });
      saveBtn.addEventListener("click", function () {
        if (!current) return;
        if (!api.saveFile) { api.error("This editor cannot save files"); return; }
        Promise.resolve(api.saveFile(current.name, "text/x-python", current.code)).catch(function (e) {
          api.error("Could not save " + current.name + ": " + String((e && e.message) || e));
        });
      });
      [style, captions, theme, from, to].forEach(function (el) {
        el.addEventListener("change", function () { if (shown) later(0); });
      });
      title.addEventListener("input", function () { if (shown) later(400); });

      function showVideo(res) {
        player.textContent = "";
        var url = "data:" + res.mime + ";base64," + res.data;
        var media = res.mime.indexOf("image/") === 0
          ? h("img", { class: "mn-video", src: url, alt: "The rendered animation" })
          : h("video", { class: "mn-video", controls: "", src: url, playsinline: "" });
        var save = h("a", { class: "mn-save-video", href: url, download: res.name }, ["Save video"]);
        var info = h("span", { class: "mn-info" }, [res.name + ", " + Math.round(res.size / 1024) + " kB, rendered in " + res.seconds + " s"]);
        player.appendChild(media);
        player.appendChild(h("div", { class: "mn-row" }, [save, info]));
        player.hidden = false;
      }

      renderBtn.addEventListener("click", function () {
        if (!status || !status.available) return;
        var msg = Object.assign(payload(), { quality: quality.value, format: format.value });
        renderBtn.disabled = true;
        note.textContent = "Rendering with Manim " + (status.version || "") + "… this takes a while.";
        note.className = "mn-note mn-busy";
        api.call("render", msg).then(function (res) {
          if (destroyed) return;
          renderBtn.disabled = false;
          note.textContent = "Rendered.";
          note.className = "mn-note";
          showVideo(res);
        }, function (e) {
          if (destroyed) return;
          renderBtn.disabled = false;
          note.textContent = "Manim " + (status.version || "") + " is here.";
          note.className = "mn-note";
          api.error("The render failed: " + String((e && e.message) || e));
        });
      });

      api.call("status", {}, { quiet: true }).then(function (res) {
        if (destroyed) return;
        status = res;
        if (res.available) {
          renderBtn.disabled = false;
          note.textContent = "Manim " + (res.version || "") + " is here" + (res.ffmpeg ? "." : "; ffmpeg was not found.");
          note.className = "mn-note";
        } else {
          note.textContent = "No video here: " + (res.hint || "Manim is not installed") + ".";
          note.className = "mn-note mn-missing";
        }
      }, function (e) {
        if (destroyed) return;
        status = { available: false };
        note.textContent = "Could not ask for Manim: " + String((e && e.message) || e);
        note.className = "mn-note mn-missing";
      });

      return {
        element: element,
        title: "Manim",
        help: HELP,
        onState: function (snap) {
          if (!snap || snap.preview || snap.error) return;
          var m = snap.manim;
          if (!m) return;
          var key = m.n + ":" + m.index + ":" + (snap.src || "");
          if (key === lastKey) return;
          lastKey = key;
          fillSteps(m.n, m.index);
          if (shown) later(250);
        },
        destroy: function () { destroyed = true; clearTimeout(timer); }
      };
    }
  });
})();
