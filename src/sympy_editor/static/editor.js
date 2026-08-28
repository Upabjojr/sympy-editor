/*
 * sympy-editor front end.
 *
 * Plain script: no imports/exports, no build step.  It is either inlined in a
 * classic <script> tag (standalone HTML, see html.py) or concatenated in front
 * of widget.js to form the anywidget ES module (see widget.py).  `var` is used
 * for the top-level binding so that inlining the file twice in one page is
 * harmless.
 *
 * Public API (global `SympyEditor`):
 *   new SympyEditor.Editor(hostElement, backend, options)
 *   SympyEditor.mount(hostElement, config)      // config built by html.py
 *   SympyEditor.backends.{http, pyodide, readonly}
 *   SympyEditor.loadKatex(options)
 *
 * A backend is `{ send(message, report) -> Promise<snapshot|null> }`.  The
 * message/snapshot JSON is defined by Document.handle() in document.py.  A
 * backend may return null and push snapshots itself through editor.setState().
 */
var SympyEditor = (function () {
  "use strict";

  var DEFAULTS = {
    katexJs: "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js",
    katexCss: "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css",
    displayMode: true,   // KaTeX display mode (centered, large operators)
    toolbar: true,       // show the button bar
    showSource: true,    // show str(expr) under the rendering
    readOnly: false,     // selection only, no editing
    finishButton: false  // "Done" button (used by the HTTP server backend)
  };

  /* ------------------------------------------------------------------ */
  /* Resource loading                                                    */
  /* ------------------------------------------------------------------ */

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("Failed to load " + src)); };
      document.head.appendChild(s);
    });
  }

  function ensureCss(href) {
    if (!href) return;
    var links = document.querySelectorAll("link[rel=stylesheet]");
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute("href") === href) return;
    }
    var l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    document.head.appendChild(l);
  }

  /** Resolve to the KaTeX module, loading it (once per page) if needed. */
  function loadKatex(opts) {
    opts = Object.assign({}, DEFAULTS, opts || {});
    ensureCss(opts.katexCss);
    if (window.katex) return Promise.resolve(window.katex);
    var cache = window.__sympyEditorLoads || (window.__sympyEditorLoads = {});
    if (!cache[opts.katexJs]) {
      cache[opts.katexJs] = loadScript(opts.katexJs).then(function () {
        if (!window.katex) throw new Error("KaTeX loaded but window.katex is missing");
        return window.katex;
      });
    }
    return cache[opts.katexJs];
  }

  /* ------------------------------------------------------------------ */
  /* Paths and tree                                                      */
  /* ------------------------------------------------------------------ */

  // Paths: "/" is the root, "/1/0" is expr.args[1].args[0].
  function parentPath(p) {
    if (!p || p === "/") return null;
    var i = p.lastIndexOf("/");
    return i === 0 ? "/" : p.slice(0, i);
  }
  function lastIndex(p) {
    return parseInt(p.slice(p.lastIndexOf("/") + 1), 10);
  }
  function isAncestorOrSelf(a, b) {
    return a === b || a === "/" || b.indexOf(a + "/") === 0;
  }

  /** Build {path: {parent, children[]}} from the snapshot's nodes.  Not every
   *  tree node is annotated, so "parent" is the nearest annotated ancestor. */
  function buildTree(nodes) {
    var tree = {};
    var paths = Object.keys(nodes).sort(function (a, b) { return a.length - b.length || (a < b ? -1 : 1); });
    for (var i = 0; i < paths.length; i++) {
      var p = paths[i];
      var q = parentPath(p);
      while (q !== null && !(q in tree)) q = parentPath(q);
      tree[p] = { parent: q, children: [] };
      if (q !== null) tree[q].children.push(p);
    }
    for (var k in tree) {
      tree[k].children.sort(function (a, b) {
        return a.length - b.length || lastIndex(a) - lastIndex(b) || (a < b ? -1 : 1);
      });
    }
    return tree;
  }

  /* ------------------------------------------------------------------ */
  /* DOM helper                                                          */
  /* ------------------------------------------------------------------ */

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
      }
    }
    (children || []).forEach(function (c) {
      el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return el;
  }

  /* ------------------------------------------------------------------ */
  /* Editor                                                              */
  /* ------------------------------------------------------------------ */

  class Editor {
    constructor(host, backend, options) {
      this.host = host;
      this.backend = backend;
      this.opts = Object.assign({}, DEFAULTS, options || {});
      this.state = null;      // last snapshot
      this.tree = {};         // from buildTree
      this.selected = null;   // selected path or null
      this.lastLeaf = null;   // innermost path under the last click
      this.editing = null;    // path being edited in place
      this.busy = false;
      this.closed = false;
      this.annotated = true;
      this._renderSeq = 0;
      this._hoverEl = null;
      this._build();
    }

    /* ---- construction ---- */

    _build() {
      var self = this;
      var o = this.opts;
      var root = h("div", { class: "sympy-editor" });
      this.root = root;
      this.buttons = {};
      this.toolbar = h("div", { class: "se-toolbar", role: "toolbar" });
      var btn = function (cmd, label, title) {
        var b = h("button", { type: "button", "data-cmd": cmd, title: title }, [label]);
        self.toolbar.appendChild(b);
        self.buttons[cmd] = b;
        return b;
      };
      var sep = function () { self.toolbar.appendChild(h("span", { class: "se-sep" })); };

      if (!o.readOnly) {
        btn("undo", "↶", "Undo (Ctrl+Z)");
        btn("redo", "↷", "Redo (Ctrl+Shift+Z, Ctrl+Y)");
        sep();
        btn("edit", "Edit", "Edit the selection in place (Enter, double-click, or just start typing)");
        btn("delete", "Delete", "Remove the selection from its parent (Del)");
        btn("parent", "↑", "Select the enclosing expression (↑)");
        sep();
        this.opsSelect = h("select", { class: "se-ops", title: "Operation to apply to the selection" });
        this.toolbar.appendChild(this.opsSelect);
        btn("apply", "Apply", "Apply the chosen operation to the selection (or the whole expression)");
        sep();
      }
      btn("copy", "Copy", "Copy the SymPy source to the clipboard");
      if (o.finishButton && !o.readOnly) {
        btn("finish", "Done", "Finish editing and hand the expression back to Python");
      }
      this.status = h("span", { class: "se-status", "aria-live": "polite" });
      this.toolbar.appendChild(this.status);
      if (o.toolbar) root.appendChild(this.toolbar);

      this.view = h("div", {
        class: "se-view", tabindex: "0", role: "application",
        "aria-label": "SymPy expression; click to select a sub-expression"
      });
      root.appendChild(this.view);

      this.source = h("code", { class: "se-source", title: "SymPy source (click to edit the whole expression)" });
      if (o.showSource) root.appendChild(this.source);

      this.error = h("div", { class: "se-error", role: "alert", hidden: "" });
      root.appendChild(this.error);

      this.input = null;  // the in-place <input> while editing

      this.host.appendChild(root);
      this._wire();
    }

    _wire() {
      var self = this;
      this.root.addEventListener("click", function (ev) {
        var b = ev.target.closest && ev.target.closest("button[data-cmd]");
        if (b && self.root.contains(b)) {
          ev.preventDefault();
          var cmd = b.getAttribute("data-cmd");
          self.command(cmd);
          if (cmd !== "edit") self.view.focus({ preventScroll: true });
        }
      });
      this.view.addEventListener("mousemove", function (ev) { self._setHover(self._leafAt(ev)); });
      this.view.addEventListener("mouseleave", function () { self._setHover(null); });
      this.view.addEventListener("click", function (ev) { self._onClick(ev); });
      this.view.addEventListener("dblclick", function (ev) { self._onDblClick(ev); });
      this.root.addEventListener("keydown", function (ev) {
        var t = ev.target;
        if (t === self.input || (t && t.tagName === "SELECT")) return;
        self._onKey(ev);
      });
      this.source.addEventListener("click", function () {
        if (!self.opts.readOnly) self.beginEdit("/");
      });
      if (this.opsSelect) {
        this.opsSelect.addEventListener("change", function () { self._updateToolbar(); });
      }
    }

    /* ---- state ---- */

    /** Apply a snapshot from the backend. */
    async setState(snap) {
      if (!snap) return;
      this.state = snap;
      this.tree = buildTree(snap.nodes || {});
      if (this.editing !== null) this.cancelEdit(true);
      await this._render();
      if (this.state !== snap) return;  // superseded meanwhile
      var sel = this.selected;
      while (sel && !(sel in this.tree)) sel = parentPath(sel);
      this.selected = sel;
      this._fillOps();
      this._applySelection();
      this._showError(snap.error);
      if (snap.closed) {
        this.closed = true;
        this.root.classList.add("se-closed");
        this._setStatus("Session closed – the expression was returned to Python.");
      }
      this._updateToolbar();
    }

    async _render() {
      var token = ++this._renderSeq;
      var katex;
      try {
        katex = await loadKatex(this.opts);
      } catch (e) {
        this.view.textContent = this.state.src || "";
        this._showError("KaTeX could not be loaded: " + e.message);
        return;
      }
      if (token !== this._renderSeq) return;
      var base = {
        displayMode: !!this.opts.displayMode,
        output: "html",
        throwOnError: true,
        trust: function (ctx) { return ctx.command === "\\htmlData"; },
        strict: function (code) { return code === "htmlExtension" ? "ignore" : "warn"; }
      };
      this.annotated = true;
      try {
        katex.render(this.state.latex, this.view, base);
      } catch (err) {
        this.annotated = false;
        if (window.console) console.warn("sympy-editor: annotated LaTeX failed to render, using plain LaTeX.", err);
        try {
          katex.render(this.state.latex_plain || "", this.view, Object.assign({}, base, { throwOnError: false }));
        } catch (err2) {
          this.view.textContent = this.state.src || "";
        }
      }
      this.source.textContent = this.state.src || "";
    }

    _fillOps() {
      if (!this.opsSelect) return;
      var ops = this.state.ops || [];
      var key = JSON.stringify(ops.map(function (op) { return op.name; }));
      if (key === this._opsKey) return;
      this._opsKey = key;
      var current = this.opsSelect.value;
      this.opsSelect.textContent = "";
      var self = this;
      ops.forEach(function (op) {
        self.opsSelect.appendChild(h("option", { value: op.name }, [op.label || op.name]));
      });
      if (current) this.opsSelect.value = current;
    }

    /* ---- selection ---- */

    /** Innermost annotated element under a mouse event.  KaTeX stacks
     *  auxiliary spans (fraction/superscript "vlist" struts) on top of the
     *  glyphs, so the event target's ancestors are not reliable: inspect the
     *  whole element stack at the pointer and keep the deepest path. */
    _leafAt(ev) {
      var best = null;
      if (ev && typeof ev.clientX === "number" && document.elementsFromPoint) {
        var stack = document.elementsFromPoint(ev.clientX, ev.clientY);
        for (var i = 0; i < stack.length; i++) {
          var el = stack[i].closest ? stack[i].closest("[data-path]") : null;
          if (!el || !this.view.contains(el)) continue;
          if (!best || el.getAttribute("data-path").length > best.getAttribute("data-path").length) best = el;
        }
      }
      if (!best && ev && ev.target && ev.target.closest) {
        var t = ev.target.closest("[data-path]");
        if (t && this.view.contains(t)) best = t;
      }
      return best;
    }

    _setHover(el) {
      if (this._hoverEl === el) return;
      if (this._hoverEl) this._hoverEl.classList.remove("se-hover");
      this._hoverEl = el;
      if (el && !this.closed) el.classList.add("se-hover");
    }

    _els(path) {
      var esc = (window.CSS && CSS.escape) ? CSS.escape(path) : path;
      return this.view.querySelectorAll('[data-path="' + esc + '"]');
    }

    /** Select a path (null to clear). */
    select(path) {
      this.selected = (path && (path in this.tree)) ? path : null;
      this._applySelection();
      this._updateToolbar();
    }

    _applySelection() {
      var old = this.view.querySelectorAll(".se-selected");
      for (var i = 0; i < old.length; i++) old[i].classList.remove("se-selected");
      var node = this.selected && this.state && this.state.nodes ? this.state.nodes[this.selected] : null;
      if (node) {
        var els = this._els(this.selected);
        for (var j = 0; j < els.length; j++) els[j].classList.add("se-selected");
        this._setStatus(node.type + ": " + node.src);
      } else if (!this.closed) {
        this._setStatus(this.annotated ? (this.opts.readOnly ? "" : "Click a sub-expression to select it")
                                       : "Structure unavailable (plain rendering)");
      }
    }

    _onClick(ev) {
      if (this.closed || (this.input && ev.target === this.input)) return;
      var leaf = this._leafAt(ev);
      if (!leaf) {
        this.select(null);
        this.lastLeaf = null;
        this.view.focus({ preventScroll: true });
        return;
      }
      var lp = leaf.getAttribute("data-path");
      // Clicking repeatedly on the same spot walks up the ancestors.
      if (this.selected && this.lastLeaf === lp && isAncestorOrSelf(this.selected, lp)) {
        var up = this.tree[this.selected] ? this.tree[this.selected].parent : null;
        this.select(up || lp);
      } else {
        this.select(lp);
      }
      this.lastLeaf = lp;
      this.view.focus({ preventScroll: true });
    }

    _onDblClick(ev) {
      if (this.opts.readOnly || this.closed || (this.input && ev.target === this.input)) return;
      var leaf = this._leafAt(ev);
      if (!leaf) return;
      ev.preventDefault();
      var p = leaf.getAttribute("data-path");
      this.select(p);
      this.beginEdit(p);
    }

    _onKey(ev) {
      if (this.closed) return;
      var k = ev.key;
      var mod = ev.ctrlKey || ev.metaKey;
      var ro = this.opts.readOnly;
      var t = this.selected ? this.tree[this.selected] : null;
      var handled = true;
      if (mod && (k === "z" || k === "Z")) {
        if (!ro) this.send({ action: ev.shiftKey ? "redo" : "undo" });
      } else if (mod && (k === "y" || k === "Y")) {
        if (!ro) this.send({ action: "redo" });
      } else if (k === "Enter") {
        if (!ro) this.beginEdit(this.selected || "/");
      } else if (k === "Escape") {
        this.select(null);
      } else if (k === "Delete" || k === "Backspace") {
        if (!ro && this.selected && this.selected !== "/") this.send({ action: "delete", path: this.selected });
      } else if (k === "ArrowUp") {
        if (t && t.parent) this.select(t.parent);
      } else if (k === "ArrowDown") {
        if (!this.selected) this.select("/");
        else if (t && t.children.length) this.select(t.children[0]);
      } else if (k === "ArrowLeft" || k === "ArrowRight") {
        if (t && t.parent) {
          var sib = this.tree[t.parent].children;
          var i = sib.indexOf(this.selected) + (k === "ArrowLeft" ? -1 : 1);
          if (i >= 0 && i < sib.length) this.select(sib[i]);
        }
      } else if (!ro && !mod && !ev.altKey && k.length === 1 && this.selected) {
        this.beginEdit(this.selected, k);   // start replacing the selection with what is typed
      } else {
        handled = false;
      }
      if (handled) { ev.preventDefault(); ev.stopPropagation(); }
    }

    /* ---- in-place editing ---- */

    /** Replace the rendering of `path` with a text field inside the formula.
     *  `initial` (optional) pre-fills the field instead of the node's source. */
    beginEdit(path, initial) {
      if (this.opts.readOnly || this.closed || !this.state || this.busy) return;
      if (!path || !(path in this.state.nodes)) path = "/";
      if (!(path in this.state.nodes)) return;
      if (this.editing !== null) this.cancelEdit(true);
      var self = this;
      var original = this.state.nodes[path].src;
      var host = this._els(path)[0] || this.view;
      var input = h("input", {
        class: "se-inline", type: "text", spellcheck: "false", autocomplete: "off",
        "aria-label": "Replacement for " + original + " (SymPy syntax)"
      });
      input.value = initial !== undefined ? initial : original;
      var stash = document.createDocumentFragment();
      while (host.firstChild) stash.appendChild(host.firstChild);
      host.appendChild(input);
      host.classList.add("se-editing");
      this.editing = path;
      this.input = input;
      this._editHost = host;
      this._editStash = stash;
      this._editOriginal = original;
      var fit = function () { input.style.width = Math.max(2, input.value.length + 1) + "ch"; };
      fit();
      input.addEventListener("input", fit);
      input.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key === "Enter") { ev.preventDefault(); self.commitEdit(); }
        else if (ev.key === "Escape") { ev.preventDefault(); self.cancelEdit(); }
      });
      input.addEventListener("blur", function () {
        if (self.editing === path && self.input === input) self.commitEdit();
      });
      this.select(path);
      this._setStatus("Editing " + this.state.nodes[path].type + " – Enter to apply, Esc to cancel");
      input.focus();
      if (initial === undefined) input.select();
    }

    _endEdit() {
      var host = this._editHost, input = this.input, stash = this._editStash;
      this.editing = null;
      this.input = null;
      this._editHost = null;
      this._editStash = null;
      if (host) {
        host.classList.remove("se-editing");
        if (input && input.parentNode === host) host.removeChild(input);
        if (stash) host.appendChild(stash);
      }
    }

    commitEdit() {
      if (this.editing === null) return;
      var path = this.editing;
      var src = this.input.value.trim();
      var original = this._editOriginal;
      this._endEdit();
      this._applySelection();
      this.view.focus({ preventScroll: true });
      if (!src || src === original) return;
      this.send({ action: path === "/" ? "set" : "replace", path: path, src: src });
    }

    cancelEdit(silent) {
      if (this.editing === null) return;
      this._endEdit();
      this._applySelection();
      if (!silent) this.view.focus({ preventScroll: true });
    }

    /* ---- commands ---- */

    command(cmd) {
      switch (cmd) {
        case "undo": return this.send({ action: "undo" });
        case "redo": return this.send({ action: "redo" });
        case "edit": return this.beginEdit(this.selected || "/");
        case "delete":
          if (this.selected && this.selected !== "/") return this.send({ action: "delete", path: this.selected });
          return;
        case "parent": {
          var t = this.selected ? this.tree[this.selected] : null;
          if (t && t.parent) this.select(t.parent);
          return;
        }
        case "apply":
          if (this.opsSelect && this.opsSelect.value) {
            return this.send({ action: "apply", path: this.selected || "/", op: this.opsSelect.value });
          }
          return;
        case "copy": return this.copySource();
        case "finish": return this.send({ action: "close" });
      }
    }

    /** Send a message to the backend and apply the returned snapshot. */
    async send(msg) {
      if (this.busy || this.closed || !this.backend) return;
      this.busy = true;
      this.root.classList.add("se-busy");
      this._updateToolbar();
      var self = this;
      try {
        var snap = await this.backend.send(msg, function (text) { self._setStatus(text); });
        if (snap) await this.setState(snap);
      } catch (e) {
        this._showError(String((e && e.message) || e));
        this._applySelection();
      } finally {
        this.busy = false;
        this.root.classList.remove("se-busy");
        this._updateToolbar();
      }
    }

    copySource() {
      var text = this.state ? this.state.src : "";
      var self = this;
      var done = function () { self._setStatus("Copied: " + text); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { self._fallbackCopy(text); done(); });
      } else {
        this._fallbackCopy(text);
        done();
      }
    }

    _fallbackCopy(text) {
      var ta = h("textarea", { style: "position:fixed;opacity:0" });
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (e) { /* ignore */ }
      document.body.removeChild(ta);
    }

    /* ---- misc UI ---- */

    _setStatus(text) {
      this.status.textContent = text || "";
      this.status.title = text || "";
    }

    _showError(msg) {
      if (msg) {
        this.error.textContent = msg;
        this.error.hidden = false;
      } else {
        this.error.hidden = true;
        this.error.textContent = "";
      }
    }

    _updateToolbar() {
      var s = this.state || {};
      var b = this.buttons;
      var dis = this.busy || this.closed || !this.state;
      var set = function (name, disabled) { if (b[name]) b[name].disabled = !!disabled; };
      var t = this.selected ? this.tree[this.selected] : null;
      set("undo", dis || !s.can_undo);
      set("redo", dis || !s.can_redo);
      set("edit", dis);
      set("delete", dis || !this.selected || this.selected === "/");
      set("parent", dis || !(t && t.parent));
      set("apply", dis || !(this.opsSelect && this.opsSelect.value));
      set("copy", !s.src);
      set("finish", dis);
      if (this.opsSelect) this.opsSelect.disabled = dis;
    }

    /** Remove the editor from the page. */
    destroy() {
      if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Backends                                                            */
  /* ------------------------------------------------------------------ */

  /** POST JSON messages to a local sympy_editor.serve() server. */
  function httpBackend(cfg) {
    var url = cfg.apiUrl || "/api";
    return {
      send: async function (msg) {
        var r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-SymPy-Editor-Token": cfg.token || "" },
          body: JSON.stringify(msg)
        });
        if (!r.ok) throw new Error("Server error: HTTP " + r.status + " " + r.statusText);
        return r.json();
      }
    };
  }

  var PYODIDE_BOOT = [
    "import json, sys",
    "if '/sympy_editor_pkg' not in sys.path:",
    "    sys.path.insert(0, '/sympy_editor_pkg')",
    "from sympy import sympify",
    "from sympy_editor.document import Document",
    "__sympy_editor_doc = Document(sympify(__sympy_editor_srepr), **json.loads(__sympy_editor_settings))",
    "def __sympy_editor_handle(msg):",
    "    return json.dumps(__sympy_editor_doc.handle(json.loads(msg)))",
    ""
  ].join("\n");

  /** Run the Python Document inside the browser with Pyodide (loaded lazily
   *  on the first edit).  cfg: {pyodideJs, pyodideIndex, sources, srepr, document}. */
  function pyodideBackend(cfg) {
    var ready = null;
    async function init(report) {
      report("Loading Python runtime (Pyodide)…");
      if (typeof window.loadPyodide !== "function") await loadScript(cfg.pyodideJs);
      var py = await window.loadPyodide({ indexURL: cfg.pyodideIndex });
      report("Loading SymPy…");
      await py.loadPackage("sympy");
      var dir = "/sympy_editor_pkg/sympy_editor";
      py.FS.mkdirTree(dir);
      for (var name in cfg.sources) py.FS.writeFile(dir + "/" + name, cfg.sources[name]);
      py.globals.set("__sympy_editor_srepr", cfg.srepr);
      py.globals.set("__sympy_editor_settings", JSON.stringify(cfg.document || {}));
      py.runPython(PYODIDE_BOOT);
      report("");
      return py.globals.get("__sympy_editor_handle");
    }
    return {
      send: async function (msg, report) {
        report = report || function () {};
        if (!ready) {
          ready = init(report).catch(function (e) { ready = null; throw e; });
        }
        var handle = await ready;
        return JSON.parse(handle(JSON.stringify(msg)));
      }
    };
  }

  function readonlyBackend() {
    return {
      send: async function () { throw new Error("This view is read-only."); }
    };
  }

  var backends = { http: httpBackend, pyodide: pyodideBackend, readonly: readonlyBackend };

  /** Create an editor from a config object produced by html.py. */
  function mount(host, cfg) {
    var make = backends[cfg.backend] || readonlyBackend;
    var options = Object.assign({}, cfg.options || {});
    if (cfg.backend === "readonly") options.readOnly = true;
    var editor = new Editor(host, make(cfg), options);
    editor.setState(cfg.snapshot);
    return editor;
  }

  return {
    Editor: Editor,
    backends: backends,
    mount: mount,
    loadKatex: loadKatex,
    buildTree: buildTree,
    parentPath: parentPath,
    DEFAULTS: DEFAULTS
  };
})();
