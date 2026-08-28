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

  //: Keys that extend the selection instead of replacing it (see _onKey).
  var EXTEND_KEYS = ["+", "-", "*", "/", "^", "=", ","];

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
        btn("edit", "Edit", "Edit the selection in place (Enter, double-click, or just start typing; + - * / ^ extend it)");
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

      // Symbols panel: what each name stands for (Symbol, MatrixSymbol with
      // its shape, explicit Matrix...) with controls to change it.
      this.symbols = null;
      if (!o.readOnly && o.symbolsPanel !== false) {
        this.symbolsBody = h("div", { class: "se-symbols-body" });
        this.symbols = h("details", { class: "se-symbols", hidden: "" }, [
          h("summary", { title: "The names in the expression and their types; change a name into a matrix symbol or an explicit matrix" }, ["Symbols"]),
          this.symbolsBody
        ]);
        root.appendChild(this.symbols);
      }

      this.error = h("div", { class: "se-error", role: "alert", hidden: "" });
      root.appendChild(this.error);

      this.input = null;  // the in-place <input> while editing
      // Insertion caret: a point between two arguments of an insertable node
      // (see _gapsOf); typing there inserts a new argument.
      this.caretEl = h("span", { class: "se-caret", "aria-hidden": "true" });
      this.caret = null;      // {path, index, a, b, leftEl, rightEl, top, bottom, height}
      this.inserting = null;  // the caret an open field is inserting at
      this._gapCache = null;

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
      this.view.addEventListener("mousemove", function (ev) {
        var leaf = self._leafAt(ev);
        var gap = self.opts.readOnly ? null : self._gapAt(ev.clientX, ev.clientY, leaf);
        self._setHover(gap ? null : leaf);
        self.view.classList.toggle("se-gap", !!gap);
      });
      this.view.addEventListener("scroll", function () { self._gapCache = null; if (self.caret) self._hideCaret(); });
      this.view.addEventListener("mouseleave", function () { self._setHover(null); });
      this.view.addEventListener("click", function (ev) { self._onClick(ev); });
      this.view.addEventListener("dblclick", function (ev) { self._onDblClick(ev); });
      this.root.addEventListener("keydown", function (ev) {
        if (self.symbols && self.symbols.contains(ev.target)) return;
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
      if (this.editing !== null || this.inserting) this.cancelEdit(true);
      await this._render();
      if (this.state !== snap) return;  // superseded meanwhile
      var sel = this.selected;
      while (sel && !(sel in this.tree)) sel = parentPath(sel);
      this.selected = sel;
      this._fillOps();
      this._fillSymbols();
      this.root.setAttribute("data-seq", String(snap.seq || 0));   // lets tests wait for a re-render
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
      this._gapCache = null;
      this.caret = null;   // the rendering replaced the caret element too
    }

    /** The dropdown offers the ops that apply to the selection (the whole
     *  expression when nothing is selected): those registered for its kind
     *  ("matrix", "scalar"...) in a group of their own, after the general ones. */
    _fillOps() {
      if (!this.opsSelect || !this.state) return;
      var node = this.state.nodes ? this.state.nodes[this.selected || "/"] : null;
      var kind = node ? node.kind : null;
      var ops = (this.state.ops || []).filter(function (op) {
        return !op.kinds || (kind && op.kinds.indexOf(kind) >= 0);
      });
      var key = kind + "|" + JSON.stringify(ops.map(function (op) { return op.name; }));
      if (key === this._opsKey) return;
      this._opsKey = key;
      var current = this.opsSelect.value;
      this.opsSelect.textContent = "";
      var general = ops.filter(function (op) { return !op.kinds; });
      var specific = ops.filter(function (op) { return op.kinds; });
      var self = this;
      var add = function (parent, op) { parent.appendChild(h("option", { value: op.name }, [op.label || op.name])); };
      if (specific.length) {
        var kindLabel = kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Selection";
        var group = h("optgroup", { label: kindLabel });
        specific.forEach(function (op) { add(group, op); });
        this.opsSelect.appendChild(group);
        var rest = h("optgroup", { label: "General" });
        general.forEach(function (op) { add(rest, op); });
        this.opsSelect.appendChild(rest);
      } else {
        general.forEach(function (op) { add(self.opsSelect, op); });
      }
      if (current && ops.some(function (op) { return op.name === current; })) this.opsSelect.value = current;
    }

    /** The symbols panel: one row per name (used in the expression or merely
     *  declared) with controls to change what it stands for, and a last row
     *  to declare a new name - with its type, shape or assumptions - before
     *  typing it. */
    _fillSymbols() {
      if (!this.symbols || !this.state) return;
      var syms = this.state.symbols || [];
      var key = JSON.stringify(syms);
      if (key === this._symbolsKey) return;
      this._symbolsKey = key;
      this.symbolsBody.textContent = "";
      this.symbols.hidden = false;
      var self = this;
      syms.forEach(function (sym) { self.symbolsBody.appendChild(self._symbolRow(sym)); });
      this.symbolsBody.appendChild(this._symbolRow(null));
    }

    /** A row of the symbols panel; `sym` null gives the "declare a new name" row. */
    _symbolRow(sym) {
      var self = this;
      var isNew = !sym;
      var row = h("div", { class: "se-sym" + (isNew ? " se-sym-new" : "") });
      var nameInput = null;
      if (isNew) {
        nameInput = h("input", { type: "text", class: "se-sym-name", placeholder: "name",
          "aria-label": "Name of the new symbol", title: "Declare a name (and what it is) before typing it" });
        row.appendChild(nameInput);
      } else {
        row.appendChild(h("code", {}, [sym.name]));
      }
      var types = ["Symbol", "MatrixSymbol", "Matrix", "Function"];
      if (!isNew && types.indexOf(sym.type) < 0) {
        row.appendChild(h("span", { class: "se-sym-note" }, [sym.type + (sym.type === "IndexedBase" ? " (indexed)" : "")]));
        return row;
      }
      var select = h("select", { title: isNew ? "Type of the new symbol" : "What " + sym.name + " stands for" });
      types.forEach(function (k) {
        select.appendChild(h("option", { value: k }, [k === "Matrix" ? "Matrix (explicit)" : k]));
      });
      select.value = isNew ? "Symbol" : sym.type;
      var shape = (sym && sym.shape) || ["2", "2"];
      var rows = h("input", { type: "text", value: shape[0], title: "Rows", "aria-label": "Rows" });
      var cols = h("input", { type: "text", value: shape[1], title: "Columns", "aria-label": "Columns" });
      var times = h("span", {}, ["\u00d7"]);
      var assume = h("input", { type: "text", class: "se-sym-assume", placeholder: "assumptions",
        title: "Comma-separated SymPy assumptions: positive, real, integer, nonzero...",
        "aria-label": "Assumptions", value: (sym && sym.assumptions) ? sym.assumptions.join(", ") : "" });
      var button = h("button", { type: "button",
        title: isNew ? "Declare the symbol" : "Change " + sym.name + " throughout the expression" }, [isNew ? "Add" : "Set"]);
      var refresh = function () {
        var matrix = select.value === "MatrixSymbol" || select.value === "Matrix";
        rows.hidden = cols.hidden = times.hidden = !matrix;
        assume.hidden = select.value !== "Symbol";
      };
      select.addEventListener("change", refresh);
      var apply = function () {
        if (self.busy || self.closed) return;
        var name = isNew ? nameInput.value.trim() : sym.name;
        if (!name) { nameInput.focus(); return; }
        var msg = { action: isNew ? "declare" : "retype", name: name, type: select.value };
        if (select.value === "MatrixSymbol" || select.value === "Matrix") {
          msg.rows = rows.value.trim();
          msg.cols = cols.value.trim();
        }
        if (select.value === "Symbol") {
          msg.assumptions = assume.value.split(",").map(function (a) { return a.trim(); }).filter(Boolean);
        }
        self.send(msg);
      };
      button.addEventListener("click", apply);
      var inputs = [rows, cols, assume];
      if (nameInput) inputs.push(nameInput);
      inputs.forEach(function (inp) {
        inp.addEventListener("keydown", function (ev) {
          ev.stopPropagation();
          if (ev.key === "Enter") { ev.preventDefault(); apply(); }
        });
      });
      select.addEventListener("keydown", function (ev) { ev.stopPropagation(); });
      [select, rows, times, cols, assume, button].forEach(function (el) { row.appendChild(el); });
      if (!isNew && sym.used === false) {
        row.appendChild(h("span", { class: "se-sym-note" }, ["declared, not used"]));
        var remove = h("button", { type: "button", title: "Forget this declaration" }, ["Remove"]);
        remove.addEventListener("click", function () {
          if (!self.busy && !self.closed) self.send({ action: "undeclare", name: sym.name });
        });
        row.appendChild(remove);
      }
      refresh();
      return row;
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
      if (path) this._hideCaret();
      this.selected = (path && (path in this.tree)) ? path : null;
      this._fillOps();
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
        this._setStatus(node.type + ": " + node.src + (node.reciprocal ? "  (denominator: the node is 1 over this)" : ""));
      } else if (!this.closed) {
        this._setStatus(this.annotated ? (this.opts.readOnly ? "" : "Click a sub-expression to select it, or click between terms to insert one")
                                       : "Structure unavailable (plain rendering)");
      }
    }

    _onClick(ev) {
      if (this.closed || (this.input && ev.target === this.input)) return;
      var leaf = this._leafAt(ev);
      this._gapCache = null;
      var gap = this.opts.readOnly ? null : this._gapAt(ev.clientX, ev.clientY, leaf);
      if (gap) {
        this.select(null);
        this.lastLeaf = null;
        this._showCaret(gap, ev.clientX);
        this.view.focus({ preventScroll: true });
        return;
      }
      if (!leaf) {
        this.select(null);
        this._hideCaret();
        this._applySelection();
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
      } else if (k === "Tab" && this.selected && !ro) {
        if (!this.caretAtSelection(ev.shiftKey)) handled = false;
      } else if (this.caret && k === "Escape") {
        this._hideCaret();
        this._applySelection();
      } else if (this.caret && k === "Enter") {
        if (!ro) this.beginInsert("");
      } else if (this.caret && (k === "ArrowLeft" || k === "ArrowRight")) {
        this._moveCaret(k === "ArrowLeft" ? -1 : 1);
      } else if (this.caret && k === "ArrowUp") {
        var container = this.caret.path;
        this._hideCaret();
        this.select(container);
      } else if (this.caret && !ro && !mod && !ev.altKey && k.length === 1) {
        this.beginInsert(k);
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
      } else if (!ro && !mod && !ev.altKey && EXTEND_KEYS.indexOf(k) >= 0) {
        // An operator extends the selection (the whole expression when nothing
        // is selected): the field opens with its source and the operator, the
        // caret after them, so new terms and factors can be typed without
        // retyping what is there.
        var target = this.selected || "/";
        this.beginEdit(target, this.state.nodes[target].src + " " + k + " ", true);
      } else if (!ro && !mod && !ev.altKey && k.length === 1 && this.selected) {
        this.beginEdit(this.selected, k);   // start replacing the selection with what is typed
      } else {
        handled = false;
      }
      if (handled) { ev.preventDefault(); ev.stopPropagation(); }
    }

    /* ---- in-place editing ---- */

    /** Replace the rendering of `path` with a text field inside the formula.
     *  `initial` (optional) pre-fills the field instead of the node's source;
     *  with `extend` the field keeps the whole text and puts the caret at its
     *  end (an insertion), otherwise the node's source is selected for
     *  replacement. */
    beginEdit(path, initial, extend) {
      if (this.opts.readOnly || this.closed || !this.state || this.busy) return;
      if (!path || !(path in this.state.nodes)) path = "/";
      if (!(path in this.state.nodes)) return;
      if (this.editing !== null || this.inserting) this.cancelEdit(true);
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
      else if (extend) input.setSelectionRange(input.value.length, input.value.length);
    }

    /* ---- insertion caret ---- */

    _argIndex(parent, child) {
      var rest = parent === "/" ? child.slice(1) : child.slice(parent.length + 1);
      return parseInt(rest.split("/")[0], 10);
    }

    /** The insertion points of an insertable node, in display order: before
     *  its first argument, between arguments, after the last.  `index` is
     *  the argument position sent to the backend. */
    _gapsOf(p) {
      var node = this.state && this.state.nodes ? this.state.nodes[p] : null;
      if (!node || !node.insertable || !this.tree[p]) return [];
      var host = this._els(p)[0];
      if (!host) return [];
      var hr = host.getBoundingClientRect();
      var self = this;
      var kids = this.tree[p].children
        .map(function (c) { return { path: c, el: self._els(c)[0] }; })
        .filter(function (k) { return k.el; })
        .map(function (k) { k.rect = k.el.getBoundingClientRect(); return k; })
        .sort(function (a, b) { return a.rect.left - b.rect.left; });
      // Room before the first and after the last argument: generous around
      // the whole expression, a few pixels inside a nested node so that its
      // own operator glyphs (the sign of "- sin(x)") still select the node.
      var pad = p === "/" ? 16 : 4;
      var gaps = [];
      var push = function (index, a, b, leftEl, rightEl, top, bottom) {
        if (a > b) { a = b = (a + b) / 2; }
        gaps.push({ path: p, index: index, a: a, b: b, leftEl: leftEl, rightEl: rightEl,
          top: top, bottom: bottom, height: bottom - top });
      };
      if (!kids.length) {
        push(node.nargs, hr.left, hr.right, null, null, hr.top, hr.bottom);
        return gaps;
      }
      var first = kids[0], last = kids[kids.length - 1];
      push(this._argIndex(p, first.path), (p === "/" ? Math.min(hr.left, first.rect.left) : first.rect.left) - pad,
        first.rect.left, null, first.el, first.rect.top, first.rect.bottom);
      for (var i = 0; i + 1 < kids.length; i++) {
        var l = kids[i], r = kids[i + 1];
        // Only arguments on the same line have a gap between them: the
        // numerator and denominator of a fraction are stacked, not adjacent.
        var overlap = Math.min(l.rect.bottom, r.rect.bottom) - Math.max(l.rect.top, r.rect.top);
        if (overlap < 0.5 * Math.min(l.rect.height, r.rect.height)) continue;
        push(this._argIndex(p, r.path), l.rect.right, r.rect.left, l.el, r.el,
          Math.min(l.rect.top, r.rect.top), Math.max(l.rect.bottom, r.rect.bottom));
      }
      push(node.nargs, last.rect.right, (p === "/" ? Math.max(hr.right, last.rect.right) : last.rect.right) + pad,
        last.el, null, last.rect.top, last.rect.bottom);
      return gaps;
    }

    _allGaps() {
      if (!this._gapCache) {
        var gaps = [];
        for (var p in this.tree) gaps = gaps.concat(this._gapsOf(p));
        this._gapCache = gaps;
      }
      return this._gapCache;
    }

    /** The insertion point at a viewport position, or null.  The innermost
     *  node wins; a glyph (`leaf`) wins over the gaps of its ancestors. */
    _gapAt(x, y, leaf) {
      var leafLen = leaf ? leaf.getAttribute("data-path").length : 0;
      var best = null;
      var gaps = this._allGaps();
      for (var i = 0; i < gaps.length; i++) {
        var g = gaps[i];
        if (x < g.a - 3 || x > g.b + 3 || y < g.top - 6 || y > g.bottom + 6) continue;
        if (g.path.length < leafLen) continue;
        if (!best || g.path.length > best.path.length) best = g;
      }
      return best;
    }

    _showCaret(gap, x) {
      this._hideCaret();
      this.caret = gap;
      var vr = this.view.getBoundingClientRect();
      var cx = Math.max(gap.a, Math.min(x === undefined ? gap.b : x, gap.b));
      this.caretEl.style.left = Math.round(cx - vr.left + this.view.scrollLeft - 1) + "px";
      this.caretEl.style.top = Math.round(gap.top - vr.top + this.view.scrollTop) + "px";
      this.caretEl.style.height = Math.round(Math.max(12, gap.height)) + "px";
      this.view.appendChild(this.caretEl);
      var old = this.view.querySelectorAll(".se-selected");
      for (var i = 0; i < old.length; i++) old[i].classList.remove("se-selected");
      var node = this.state.nodes[gap.path];
      this._setStatus("Insert into " + node.type + " " + node.src + " – type a term (Enter to apply, Esc to cancel)");
      this._updateToolbar();
    }

    _hideCaret() {
      this.caret = null;
      if (this.caretEl.parentNode) this.caretEl.parentNode.removeChild(this.caretEl);
    }

    _moveCaret(step) {
      var cur = this.caret;
      var gaps = this._gapsOf(cur.path);
      var idx = -1;
      for (var i = 0; i < gaps.length; i++) {
        if (gaps[i].index === cur.index && gaps[i].leftEl === cur.leftEl) idx = i;
      }
      var j = idx + step;
      if (j >= 0 && j < gaps.length) this._showCaret(gaps[j], step < 0 ? gaps[j].b : gaps[j].a);
    }

    /** Put the caret right after (before, with `before`) the selection, in
     *  the nearest enclosing node that accepts insertions.  Returns false
     *  when there is none. */
    caretAtSelection(before) {
      if (!this.selected || !this.state) return false;
      var child = this.selected;
      var p = this.tree[child] ? this.tree[child].parent : null;
      while (p && !(this.state.nodes[p] && this.state.nodes[p].insertable)) {
        child = p;
        p = this.tree[p].parent;
      }
      if (!p) return false;
      var el = this._els(child)[0];
      var gaps = this._gapsOf(p);
      for (var i = 0; i < gaps.length; i++) {
        if (before ? gaps[i].rightEl === el : gaps[i].leftEl === el) {
          this._showCaret(gaps[i], before ? gaps[i].b : gaps[i].a);
          return true;
        }
      }
      return false;
    }

    /** Open a field at the caret; Enter inserts what is typed. */
    beginInsert(initial) {
      var gap = this.caret;
      if (!gap || this.opts.readOnly || this.closed || !this.state || this.busy) return;
      if (this.editing !== null || this.inserting) this.cancelEdit(true);
      var self = this;
      var input = h("input", { class: "se-inline", type: "text", spellcheck: "false", autocomplete: "off",
        placeholder: "term", "aria-label": "New term (SymPy syntax)" });
      input.value = initial || "";
      var host = this._els(gap.path)[0];
      if (gap.rightEl && gap.rightEl.parentNode) gap.rightEl.parentNode.insertBefore(input, gap.rightEl);
      else if (gap.leftEl && gap.leftEl.parentNode) gap.leftEl.parentNode.insertBefore(input, gap.leftEl.nextSibling);
      else if (host) host.appendChild(input);
      else return;
      this._hideCaret();
      this.inserting = gap;
      this.input = input;
      this._editHost = null;
      this._editStash = null;
      this._editOriginal = null;
      var fit = function () { input.style.width = Math.max(5, input.value.length + 1) + "ch"; };
      fit();
      input.addEventListener("input", fit);
      input.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key === "Enter") { ev.preventDefault(); self.commitEdit(); }
        else if (ev.key === "Escape") { ev.preventDefault(); self.cancelEdit(); }
      });
      input.addEventListener("blur", function () {
        if (self.inserting === gap && self.input === input) self.commitEdit();
      });
      this._setStatus("Inserting into " + this.state.nodes[gap.path].type + " – Enter to apply, Esc to cancel");
      input.focus();
    }

    _endEdit() {
      var host = this._editHost, input = this.input, stash = this._editStash;
      this.editing = null;
      this.inserting = null;
      this.input = null;
      this._editHost = null;
      this._editStash = null;
      if (host) {
        host.classList.remove("se-editing");
        if (input && input.parentNode === host) host.removeChild(input);
        if (stash) host.appendChild(stash);
      } else if (input && input.parentNode) {
        input.parentNode.removeChild(input);
      }
    }

    commitEdit() {
      if (this.editing === null && !this.inserting) return;
      var inserting = this.inserting;
      var path = this.editing;
      var src = this.input.value.trim();
      var original = this._editOriginal;
      this._endEdit();
      this._applySelection();
      this.view.focus({ preventScroll: true });
      if (inserting) {
        if (src) this.send({ action: "insert", path: inserting.path, index: inserting.index, src: src });
        return;
      }
      if (!src || src === original) return;
      var msg = { action: path === "/" ? "set" : "replace", path: path, src: src };
      var node = this.state && this.state.nodes ? this.state.nodes[path] : null;
      if (node && node.reciprocal) msg.reciprocal = true;
      this.send(msg);
    }

    cancelEdit(silent) {
      if (this.editing === null && !this.inserting) return;
      this._endEdit();
      this._applySelection();
      if (!silent) this.view.focus({ preventScroll: true });
    }

    /* ---- commands ---- */

    command(cmd) {
      switch (cmd) {
        case "undo": return this.send({ action: "undo" });
        case "redo": return this.send({ action: "redo" });
        case "edit": return this.caret ? this.beginInsert("") : this.beginEdit(this.selected || "/");
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
    "from sympy_editor.document import Document",
    "__sympy_editor_docs = {}",
    "def __sympy_editor_new(doc_id, srepr, settings):",
    "    __sympy_editor_docs[doc_id] = Document(srepr, **json.loads(settings))",
    "def __sympy_editor_handle(doc_id, msg):",
    "    return json.dumps(__sympy_editor_docs[doc_id].handle(json.loads(msg)))",
    ""
  ].join("\n");

  /** The one Pyodide interpreter of the page, with SymPy and the editor's
   *  modules loaded: every editor on the page keeps its Document in it.  The
   *  cache lives on window, since each embedded fragment may carry its own
   *  copy of this script. */
  function pyodideRuntime(cfg, report) {
    var shared = window.__sympyEditorPyodide || (window.__sympyEditorPyodide = { runtimes: {}, docs: 0 });
    var key = cfg.pyodideIndex || cfg.pyodideJs || "default";
    if (!shared.runtimes[key]) {
      shared.runtimes[key] = (async function () {
        report("Loading Python runtime (Pyodide)…");
        if (typeof window.loadPyodide !== "function") await loadScript(cfg.pyodideJs);
        var py = await window.loadPyodide({ indexURL: cfg.pyodideIndex });
        report("Loading SymPy…");
        await py.loadPackage("sympy");
        var dir = "/sympy_editor_pkg/sympy_editor";
        py.FS.mkdirTree(dir);
        for (var name in cfg.sources) py.FS.writeFile(dir + "/" + name, cfg.sources[name]);
        py.runPython(PYODIDE_BOOT);
        return { py: py, newDoc: py.globals.get("__sympy_editor_new"), handle: py.globals.get("__sympy_editor_handle") };
      })().catch(function (e) { delete shared.runtimes[key]; throw e; });
    }
    return shared.runtimes[key];
  }

  /** Run the Python Document inside the browser with Pyodide (loaded lazily
   *  on the first edit, once per page).  cfg: {pyodideJs, pyodideIndex, sources, srepr, document}. */
  function pyodideBackend(cfg) {
    var ready = null;
    async function init(report) {
      var runtime = await pyodideRuntime(cfg, report);
      var shared = window.__sympyEditorPyodide;
      var id = "doc" + (++shared.docs);
      runtime.newDoc(id, cfg.srepr, JSON.stringify(cfg.document || {}));
      report("");
      return function (msg) { return runtime.handle(id, msg); };
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
