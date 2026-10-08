/*
 * The export add-on: the selection (or the whole formula) written out in
 * another language - LaTeX, MathML, Python, C, Fortran, JavaScript,
 * Octave/MATLAB, Julia, Rust, or a whole function - by SymPy's printers on
 * the Python side (method "export", a query).  One tab per format, the
 * format's options under it, then one read-only box per file with Copy and
 * Save.  Copying goes where the editor's own Copy goes (the host app's
 * clipboard, else the browser's), saving through api.saveFile (the host
 * app, the kernel, the share sheet or a download).
 *
 * The panel asks only while its box is open, and only for something it has
 * not asked for already (the formula, the selection, the format and its
 * options make the key): onSelect and onState come often, and a question
 * per redraw would never stop.
 */
SympyEditor.registerAddon("export", {
  mount: function (api) {
    var h = api.h;
    var PY = SympyEditor.python || null;
    var formats = (api.options && api.options.formats) || [];
    var byKey = {};
    for (var i = 0; i < formats.length; i++) byKey[formats[i].key] = formats[i];
    var current = formats.length ? formats[0].key : null;
    var chosen = {};              // format -> {option: value}, what the user set
    var askedKey = null, seq = 0, typingTimer = null;

    var tabs = h("div", { class: "ex-tabs", role: "tablist" });
    var target = h("div", { class: "ex-target" });
    var opts = h("div", { class: "ex-opts" });
    var notes = h("div", { class: "ex-notes", hidden: "" });
    var error = h("div", { class: "ex-error", role: "alert", hidden: "" });
    var files = h("div", { class: "ex-files" });
    var element = h("div", { class: "ex-panel" }, [tabs, target, opts, error, notes, files]);

    /* ---- keeping the format and the options (this editor's keeper) ---- */
    var KEEP = "export-settings";
    if (api.keep) {
      Promise.resolve(api.keep.read(KEEP)).then(function (text) {
        if (!text) return;
        try {
          var kept = JSON.parse(text);
          if (kept && kept.chosen && typeof kept.chosen === "object") chosen = kept.chosen;
          if (kept && byKey[kept.current]) current = kept.current;
          drawTabs(); drawOptions(); refresh();
        } catch (e) { /* a damaged record: the defaults */ }
      }).catch(function () {});
    }
    function remember() {
      if (!api.keep) return;
      try { Promise.resolve(api.keep.write(KEEP, JSON.stringify({ current: current, chosen: chosen }))).catch(function () {}); }
      catch (e) { /* nothing kept */ }
    }

    function optionValues(key) {
      var fmt = byKey[key], out = {}, mine = chosen[key] || {};
      if (!fmt) return out;
      for (var i = 0; i < fmt.options.length; i++) {
        var o = fmt.options[i];
        out[o.name] = Object.prototype.hasOwnProperty.call(mine, o.name) ? mine[o.name] : o.default;
      }
      return out;
    }

    function drawTabs() {
      tabs.textContent = "";
      formats.forEach(function (f) {
        var b = h("button", { type: "button", class: "ex-tab" + (f.key === current ? " ex-on" : ""), role: "tab",
                              "aria-selected": f.key === current ? "true" : "false", "data-format": f.key }, [f.label]);
        b.addEventListener("click", function () {
          if (current === f.key) return;
          current = f.key;
          remember();
          drawTabs(); drawOptions(); refresh();
        });
        tabs.appendChild(b);
      });
    }

    function drawOptions() {
      opts.textContent = "";
      var fmt = byKey[current];
      if (!fmt) return;
      var values = optionValues(current);
      fmt.options.forEach(function (o) {
        var set = function (v) {
          (chosen[current] || (chosen[current] = {}))[o.name] = v;
          remember();
        };
        var field;
        if (o.kind === "choice") {
          field = h("select", { class: "ex-field", "data-option": o.name });
          o.choices.forEach(function (c) {
            var opt = h("option", { value: c[0] }, [c[1]]);
            if (String(c[0]) === String(values[o.name])) opt.selected = true;
            field.appendChild(opt);
          });
          field.addEventListener("change", function () { set(field.value); refresh(); });
          opts.appendChild(h("label", { class: "ex-opt" }, [o.label + " ", field]));
        } else if (o.kind === "bool") {
          field = h("input", { type: "checkbox", class: "ex-check", "data-option": o.name });
          field.checked = !!values[o.name];
          field.addEventListener("change", function () { set(field.checked); refresh(); });
          opts.appendChild(h("label", { class: "ex-opt" }, [field, " " + o.label]));
        } else {
          field = h("input", { type: "text", class: "ex-field ex-text", "data-option": o.name,
                               placeholder: o.placeholder || "", size: "10" });
          field.value = values[o.name] == null ? "" : String(values[o.name]);
          field.addEventListener("input", function () {
            set(field.value);
            clearTimeout(typingTimer);
            typingTimer = setTimeout(refresh, 400);         // a name being typed: ask once it rests
          });
          field.addEventListener("keydown", function (ev) {
            ev.stopPropagation();                          // the editor's keys are not for this field
            if (ev.key === "Enter") { clearTimeout(typingTimer); refresh(); }
          });
          opts.appendChild(h("label", { class: "ex-opt" }, [o.label + " ", field]));
        }
      });
    }

    /* ---- what to export ---- */
    function where() {
      var range = api.range && api.range();
      var idx = api.rangeIndices && api.rangeIndices();
      if (range && range.parent != null && idx && idx.length) return { path: range.parent, children: idx };
      var sel = api.selected();
      return sel ? { path: sel } : { path: "/" };
    }
    function describeTarget(w) {
      var st = api.state();
      if (w.children) return "The selected terms";
      if (w.path && w.path !== "/") {
        var node = api.node(w.path);
        return "The selection" + (node && node.src ? ": " + node.src : "");
      }
      return "The whole formula" + (st && st.src ? ": " + st.src : "");
    }

    function isOpen() {
      var box = element.closest ? element.closest("details") : null;
      return !box || box.open;
    }

    function refresh(force) {
      var st = api.state();
      if (!st || st.preview || !current || !isOpen()) return;
      var w = where();
      target.textContent = describeTarget(w);
      var options = optionValues(current);
      var key = JSON.stringify([st.src, w.path, w.children || null, current, options]);
      if (!force && key === askedKey) return;
      askedKey = key;
      var mine = ++seq;
      element.classList.add("ex-busy");
      var payload = { path: w.path, format: current, options: options };
      if (w.children) payload.children = w.children;
      api.call("export", payload, { quiet: true }).then(function (res) {
        if (mine !== seq) return;                          // a later question is on its way
        element.classList.remove("ex-busy");
        show(res);
      }, function (e) {
        if (mine !== seq) return;
        element.classList.remove("ex-busy");
        askedKey = null;                                   // ask again next time
        show({ files: [], notes: [], error: String((e && e.message) || e) });
      });
    }

    /* ---- the answer ---- */
    function show(res) {
      files.textContent = "";
      notes.textContent = "";
      notes.hidden = !(res.notes && res.notes.length);
      (res.notes || []).forEach(function (n) { notes.appendChild(h("div", { class: "ex-note" }, [n])); });
      error.textContent = res.error || "";
      error.hidden = !res.error;
      (res.files || []).forEach(function (f) { files.appendChild(fileBox(f)); });
    }

    function fileBox(f) {
      var code = h("pre", { class: "ex-code", tabindex: "0", "aria-readonly": "true", "aria-label": f.name });
      if (current === "python" && PY && PY.render) {
        try { PY.render(code, f.code); } catch (e) { code.textContent = f.code; }
      } else {
        code.textContent = f.code;
      }
      var copy = h("button", { type: "button", class: "ex-btn ex-copy", title: "Copy " + f.name + " to the clipboard" }, ["Copy"]);
      var save = h("button", { type: "button", class: "ex-btn ex-save", title: "Save " + f.name + " as a file" }, ["Save"]);
      copy.addEventListener("click", function () {
        copyText(f.code).then(function () { api.status("Copied " + f.name); },
                              function (e) { api.error("Could not copy: " + String((e && e.message) || e)); });
      });
      save.addEventListener("click", function () {
        if (!api.saveFile) { api.error("This editor cannot save files"); return; }
        Promise.resolve(api.saveFile(f.name, f.mime || "text/plain", f.code)).catch(function (e) {
          api.error("Could not save " + f.name + ": " + String((e && e.message) || e));
        });
      });
      var head = h("div", { class: "ex-file-head" }, [h("span", { class: "ex-name" }, [f.name]), h("span", { class: "ex-fill" }), copy, save]);
      return h("div", { class: "ex-file", "data-file": f.name }, [head, code]);
    }

    /* The editor's Copy, for text of our own: the host app's clipboard is
     * the system's (a WebView's may refuse, iOS asks each time), then the
     * browser's, then the old way through a hidden field. */
    function copyText(text) {
      var app = window.SympyEditorApp;
      if (app && typeof app.copyText === "function") {
        try { app.copyText(text); return Promise.resolve(); } catch (e) { /* fall through */ }
      }
      var fallback = function () {
        if (api.editor && typeof api.editor._fallbackCopy === "function") { api.editor._fallbackCopy(text); return; }
        var ta = h("textarea", { style: "position:fixed;opacity:0" });
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) { /* ignore */ }
        document.body.removeChild(ta);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () {}, function () { fallback(); });
      }
      fallback();
      return Promise.resolve();
    }

    drawTabs();
    drawOptions();
    if (!formats.length) { error.textContent = "This SymPy has none of the printers"; error.hidden = false; }

    // Opening the box asks for what changed while it was closed.
    setTimeout(function () {
      var box = element.closest ? element.closest("details") : null;
      if (box) box.addEventListener("toggle", function () { if (box.open) refresh(); });
    }, 0);

    return {
      element: element,
      title: "Export",
      help: "<section><h3>Export</h3><ul>"
        + "<li>The selection - a node, a range of terms, or the <b>whole formula</b> when nothing is selected - written out in another language by SymPy's own printers. The line above the options says which.</li>"
        + "<li>Pick the language with the tabs: <b>LaTeX</b>, <b>MathML</b> (presentation or content markup, inside a <code>&lt;math&gt;</code> element or bare), "
        + "<b>Python</b> (with <code>math</code>, NumPy or mpmath functions, or SymPy source that rebuilds it), <b>C</b> (C89, C99, C11), <b>Fortran</b> (77 to 2008, free or fixed form), "
        + "<b>JavaScript</b>, <b>Octave/MATLAB</b>, <b>Julia</b>, <b>Rust</b>.</li>"
        + "<li><b>Assign to</b> writes an assignment to that variable instead of a bare expression. A matrix in C, JavaScript or Fortran is always written element by element into an array (<code>M</code> unless named).</li>"
        + "<li><b>Function</b> writes a whole function with <code>codegen</code>: its free symbols are the arguments, in alphabetical order; name it in <b>Name</b>, pick the language (C, Fortran 95, Octave, Julia, Rust), with or without the header file. An equation <code>x = …</code> makes <code>x</code> an output argument; a matrix comes out through the array <code>out</code>.</li>"
        + "<li>What a language has no counterpart for (a Bessel function in C, an unevaluated integral) is written as SymPy writes it and listed in a <i>Not supported</i> comment at the top of the code, and in a note above it. When a printer cannot write the expression at all, the panel says why instead.</li>"
        + "<li><b>Copy</b> puts the code on the clipboard; <b>Save</b> offers it as a file, as the editor saves its own (the app's file sheet, a download in a browser).</li>"
        + "<li>Nothing here changes the formula. The tab and the options you pick are remembered.</li>"
        + "</ul></section>",
      onState: function (snap) { if (!snap.preview) refresh(); },
      onSelect: function () { refresh(); },
      commands: { refresh: function () { refresh(true); } },
      destroy: function () { clearTimeout(typingTimer); seq++; }
    };
  }
});
