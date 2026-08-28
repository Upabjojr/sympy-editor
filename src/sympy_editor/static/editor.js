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
    finishButton: false, // "Done" button (used by the HTTP server backend)
    preload: true        // Pyodide pages: start loading Python at page load, not at the first edit
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

  /* ------------------------------------------------------------------ */
  /* LaTeX shortcuts in the text field                                   */
  /* ------------------------------------------------------------------ */

  // Greek letters: SymPy name -> character.  The field shows the character
  // (typing "\theta" turns into it at once) and the source sent back uses
  // the name, so "θ" is the same symbol as an existing "theta".
  var GREEK = {
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η",
    theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ", lamda: "λ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ",
    omicron: "ο", pi: "π", rho: "ρ", varrho: "ϱ", sigma: "σ", varsigma: "ς", tau: "τ", upsilon: "υ",
    phi: "φ", varphi: "ϕ", chi: "χ", psi: "ψ", omega: "ω",
    Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lamda: "Λ", Lambda: "Λ", Xi: "Ξ", Pi: "Π", Sigma: "Σ",
    Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω", oo: "∞", infty: "∞"
  };
  // Other "\command" shortcuts: what they become in the field.
  var COMMANDS = {
    cdot: "*", times: "*", le: "<=", leq: "<=", ge: ">=", geq: ">=", ne: "!=", neq: "!=",
    sqrt: "sqrt", sin: "sin", cos: "cos", tan: "tan", cot: "cot", sec: "sec", csc: "csc",
    sinh: "sinh", cosh: "cosh", tanh: "tanh", exp: "exp", log: "log", ln: "log", ell: "l"
  };
  for (var g in GREEK) if (!(g in COMMANDS)) COMMANDS[g] = GREEK[g];
  // character -> SymPy name (first name listed wins: λ is "lamda", ∞ is "oo")
  var GREEK_BACK = {};
  for (var name in GREEK) if (!(GREEK[name] in GREEK_BACK)) GREEK_BACK[GREEK[name]] = name;
  var GREEK_NAMES = Object.keys(GREEK).sort(function (a, b) { return b.length - a.length; });
  var GREEK_NAME_RE = new RegExp("(^|[^A-Za-z0-9_])(" + GREEK_NAMES.join("|") + ")(?![A-Za-z])", "g");
  var GREEK_CHAR_RE = new RegExp("[" + Object.keys(GREEK_BACK).join("") + "]", "g");

  /** SymPy source -> text shown in the field ("theta" -> "θ"). */
  function toDisplay(src) {
    return (src || "").replace(GREEK_NAME_RE, function (m, before, name) { return before + GREEK[name]; });
  }
  /** Text of the field -> SymPy source ("θ" -> "theta", "∞" -> "oo"). */
  function toSource(text) {
    return (text || "").replace(GREEK_CHAR_RE, function (ch) { return GREEK_BACK[ch]; });
  }
  /** Replace complete "\command"s in `text`: those followed by a non-letter,
   *  or that no longer command starts with.  Returns {text, delta} where
   *  delta is the change of length before `cursor`. */
  function expandCommands(text, cursor) {
    var delta = 0;
    var out = text.replace(/\\([A-Za-z]+)/g, function (m, name, offset) {
      var next = text.charAt(offset + m.length);
      if (!(name in COMMANDS)) return m;
      var complete = (next !== "" && !/[A-Za-z]/.test(next)) || !Object.keys(COMMANDS).some(function (c) {
        return c !== name && c.indexOf(name) === 0;
      });
      if (!complete) return m;
      if (offset + m.length <= cursor) delta += COMMANDS[name].length - m.length;
      return COMMANDS[name];
    });
    return { text: out, delta: delta };
  }

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
        btn("unwrap", "Unwrap", "Remove the selected node but keep its argument: cos(θ) → θ (Backspace)");
        btn("delete", "Delete", "Remove the selection entirely (Del)");
        btn("parent", "↑", "Select the enclosing expression (↑)");
        btn("child", "↓", "Select inside: the sub-expression you came from, or the first one; on an atom, a caret after it (↓)");
        sep();
        // General menu: picking an operation applies it to the selection (or
        // the whole expression) at once.
        this.opsSelect = h("select", { class: "se-ops", title: "Transform the selection (or the whole expression)" });
        this.toolbar.appendChild(this.opsSelect);
        // Type menu: the operations specific to the selection's type (Matrix,
        // Integral, Equation...); picking one applies it at once.
        this.typeMenu = h("select", { class: "se-typemenu", hidden: "", title: "Operations specific to the selected type" });
        this.toolbar.appendChild(this.typeMenu);
        sep();
      }
      if (!o.readOnly) btn("keyboard", "⌨", "Open the keyboard: edit the selection, insert at the caret, or edit the whole expression");
      btn("copy", "Copy", "Copy the SymPy source of the selection, or of the whole expression (Ctrl+C / Ctrl+X / Ctrl+V work on selections and carets)");
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

      // The SymPy source line: editable text (Enter applies, Esc reverts) whose
      // selection is linked to the rendering both ways.  The rendering itself is
      // never replaced by code: whole-expression edits happen here.
      this.source = h("code", { class: "se-source", spellcheck: "false",
        title: o.readOnly ? "SymPy source" : "SymPy source: select to select in the formula; edit, then Enter to apply (Esc reverts)" });
      if (!o.readOnly) {
        this.source.setAttribute("contenteditable", "plaintext-only");
        if (!this.source.isContentEditable) this.source.setAttribute("contenteditable", "true");
      }
      this.sourceDirty = false;
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
      // Floating action bar under the selection: the same commands as the
      // toolbar, one click or tap away from the object they act on.
      this.actions = null;
      if (!o.readOnly) {
        var abtn = function (cmd, label, title) { return h("button", { type: "button", "data-cmd": cmd, title: title }, [label]); };
        this.actions = h("div", { class: "se-actions", hidden: "", role: "toolbar" }, [
          abtn("parent", "↑", "Select the enclosing expression"),
          abtn("child", "↓", "Select inside (the sub-expression you came from, or the first one)"),
          abtn("edit", "Edit", "Edit in place"),
          abtn("unwrap", "Unwrap", "Remove this node but keep its argument: cos(θ) → θ"),
          abtn("delete", "Delete", "Remove entirely"),
          abtn("copy", "Copy", "Copy the SymPy source of the selection (Ctrl+C; Ctrl+X cuts, Ctrl+V pastes)")
        ]);
        root.appendChild(this.actions);
      }

      this.input = null;  // the in-place <input> while editing
      // Insertion caret: a point between two arguments of an insertable node
      // (see _gapsOf); typing there inserts a new argument.
      this.caretEl = h("span", { class: "se-caret", "aria-hidden": "true" });
      this.caret = null;      // {path, index, a, b, leftEl, rightEl, top, bottom, height}
      this.inserting = null;  // the caret an open field is inserting at
      this._gapCache = null;
      // Range selection: adjacent arguments of a rangeable node (Add, Mul...),
      // as indices into that node's display-ordered children (see _setRange).
      this.range = null;      // {parent, anchor, focus} or null
      this._editRange = null; // {path, children} while a range is being edited
      this._drag = null;      // pointer drag in progress: {anchor, moved}
      this._suppressClick = false;
      this._pointerType = "mouse";   // of the last pointerdown: touch gets tap-to-edit
      this._boxes = { hover: [], select: [] };   // highlight overlays (see _visualRect)
      this._cameFrom = {};    // ancestor path -> the descendant ↑ was pressed on (for ↓)
      this._stateCount = 0;   // states applied so far (data-seq on the root)

      this.host.appendChild(root);
      root.__sympyEditor = this;   // handy for debugging and tests
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
          if (cmd !== "edit" && cmd !== "keyboard") self.view.focus({ preventScroll: true });
        }
      });
      this.view.addEventListener("mousemove", function (ev) {
        var leaf = self._leafAt(ev);
        var edge = leaf && !self.opts.readOnly ? self._edgeCaretAt(leaf, ev.clientX) : null;
        var gap = edge ? edge.gap : (self.opts.readOnly ? null : self._gapAt(ev.clientX, ev.clientY, leaf));
        self._setHover(gap ? null : leaf);
        self.view.classList.toggle("se-gap", !!gap);
      });
      this.view.addEventListener("scroll", function () { self._gapCache = null; if (self.caret) self._hideCaret(); self._applySelection(); });
      this.view.addEventListener("mouseleave", function () { self._setHover(null); });
      this.view.addEventListener("click", function (ev) { self._onClick(ev); });
      // Dragging (mouse, touch or pen) over the formula selects a range.
      this.view.addEventListener("pointerdown", function (ev) {
        self._pointerType = ev.pointerType || "mouse";
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        var leaf = self._leafAt(ev);
        self._drag = { anchor: leaf ? leaf.getAttribute("data-path") : null, moved: false };
      });
      this.view.addEventListener("pointermove", function (ev) {
        var d = self._drag;
        if (!d || !d.anchor) return;
        if (ev.pointerType === "mouse" && ev.buttons === 0) { self._drag = null; return; }
        var leaf = self._leafAt(ev);
        if (!leaf) return;
        var lp = leaf.getAttribute("data-path");
        if (!d.moved && lp === d.anchor) return;
        d.moved = true;
        self._dragSelect(d.anchor, lp);
        ev.preventDefault();
      });
      var endDrag = function () {
        if (self._drag && self._drag.moved) self._suppressClick = true;
        self._drag = null;
      };
      this.view.addEventListener("pointerup", endDrag);
      this.view.addEventListener("pointercancel", function () { self._drag = null; });
      this.view.addEventListener("dblclick", function (ev) { self._onDblClick(ev); });
      this.root.addEventListener("keydown", function (ev) {
        if (self.symbols && self.symbols.contains(ev.target)) return;
        if (ev.target === self.source) return;
        var t = ev.target;
        if (t === self.input || (t && t.tagName === "SELECT")) return;
        self._onKey(ev);
      });
      this.source.addEventListener("focus", function () {
        if (self.source.querySelector("mark")) self.source.textContent = self.source.textContent;   // plain text to edit
      });
      this.source.addEventListener("input", function () {
        self.sourceDirty = true;
        self.source.classList.add("se-dirty");
        self._setStatus("Enter applies the edited source, Esc reverts it");
      });
      this.source.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); self.commitSource(); }
        else if (ev.key === "Escape") { ev.preventDefault(); self.revertSource(); self.view.focus({ preventScroll: true }); }
      });
      this.source.addEventListener("paste", function (ev) {   // plain text only
        if (!ev.clipboardData) return;
        ev.preventDefault();
        document.execCommand("insertText", false, ev.clipboardData.getData("text/plain"));
      });
      this.source.addEventListener("blur", function () { if (self.sourceDirty) self.commitSource(); });
      document.addEventListener("selectionchange", function () { self._onSourceSelection(); });
      // Copy / cut / paste while the formula has the focus (no clipboard permission needed).
      ["copy", "cut", "paste"].forEach(function (kind) {
        document.addEventListener(kind, function (ev) { self._onClipboard(ev, kind); });
      });
      var applyFromMenu = function (menu) {
        var op = menu.value;
        menu.selectedIndex = 0;
        if (!op) return;
        var msg = { action: "apply", path: self.range ? self.range.parent : (self.selected || "/"), op: op };
        if (self.range) msg.children = self._rangeIndices();
        self.send(msg);
        self.view.focus({ preventScroll: true });
      };
      [this.opsSelect, this.typeMenu].forEach(function (menu) {
        if (!menu) return;
        menu.addEventListener("change", function () { applyFromMenu(menu); });
        menu.addEventListener("keydown", function (ev) { ev.stopPropagation(); });
      });
    }

    /* ---- state ---- */

    /** Apply a snapshot from the backend. */
    async setState(snap) {
      if (!snap) return;
      var same = snap === this.state;   // re-render of the current state (keeps the range)
      this.state = snap;
      this.tree = buildTree(snap.nodes || {});
      if (!same) { this.range = null; this._cameFrom = {}; }
      if (this.editing !== null || this.inserting) this.cancelEdit(true);
      await this._render();
      if (this.state !== snap) return;  // superseded meanwhile
      var sel = this.selected;
      while (sel && !(sel in this.tree)) sel = parentPath(sel);
      this.selected = sel;
      this._fillOps();
      this._fillSymbols();
      this.root.setAttribute("data-seq", String(++this._stateCount));   // lets tests wait for a re-render
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
      this.sourceDirty = false;
      this.source.classList.remove("se-dirty");
      this._gapCache = null;
      this.caret = null;   // the rendering replaced the caret element and the boxes too
      this._boxes = { hover: [], select: [] };
      this._hoverEl = null;
    }

    /** The general dropdown lists the ops that apply everywhere; the type
     *  menu lists those registered for the selection's kinds ("matrix",
     *  "integral"...), labelled with the most specific kind, and is hidden
     *  when there are none. */
    _fillOps() {
      if (!this.opsSelect || !this.state) return;
      var target = this.range ? this.range.parent : (this.selected || "/");
      var node = this.state.nodes ? this.state.nodes[target] : null;
      var kinds = node ? (node.kinds || [node.kind]) : [];
      var ops = this.state.ops || [];
      var general = ops.filter(function (op) { return !op.kinds; });
      var specific = ops.filter(function (op) {
        return op.kinds && op.kinds.some(function (k) { return kinds.indexOf(k) >= 0; });
      });
      var key = kinds.join(",") + "|" + JSON.stringify(ops.map(function (op) { return op.name; }));
      if (key === this._opsKey) return;
      this._opsKey = key;
      this.opsSelect.textContent = "";
      var self = this;
      this.opsSelect.appendChild(h("option", { value: "", disabled: "", selected: "" }, ["Transform \u25BE"]));
      general.forEach(function (op) { self.opsSelect.appendChild(h("option", { value: op.name }, [op.label || op.name])); });
      this.opsSelect.selectedIndex = 0;
      if (!this.typeMenu) return;
      this.typeMenu.textContent = "";
      if (!specific.length) { this.typeMenu.hidden = true; return; }
      var labels = this.state.kind_labels || {};
      var label = labels[kinds[0]] || (node && node.type) || "Type";
      this.typeMenu.appendChild(h("option", { value: "", disabled: "", selected: "" }, [label + " \u25BE"]));
      specific.forEach(function (op) { self.typeMenu.appendChild(h("option", { value: op.name }, [op.label || op.name])); });
      this.typeMenu.selectedIndex = 0;
      this.typeMenu.hidden = false;
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
      if (el && !this.closed) {
        el.classList.add("se-hover");
        var sel = this.view.querySelectorAll(".se-selected");
        var covered = false;   // no hover box on what is already selected
        for (var i = 0; i < sel.length; i++) if (sel[i] === el) covered = true;
        this._drawBoxes("hover", covered ? [] : [this._visualRect(el)]);
      } else {
        this._drawBoxes("hover", []);
      }
    }

    /** The rectangle a rendered node really occupies: KaTeX's inline spans
     *  are one text line tall, while their content (matrices, fractions,
     *  big operators) overflows them, so take the union of the glyph boxes. */
    _visualRect(el) {
      var r = el.getBoundingClientRect();
      var top = r.top, bottom = r.bottom, left = r.left, right = r.right;
      var all = el.querySelectorAll("*");
      for (var i = 0; i < all.length; i++) {
        var b = all[i].getBoundingClientRect();
        if (!b.width && !b.height) continue;
        if (all[i].classList.contains("pstrut") || all[i].classList.contains("vlist-s")) continue;
        if (b.top < top) top = b.top;
        if (b.bottom > bottom) bottom = b.bottom;
        if (b.left < left) left = b.left;
        if (b.right > right) right = b.right;
      }
      return { top: top, bottom: bottom, left: left, right: right, width: right - left, height: bottom - top };
    }

    /** Draw highlight boxes of `kind` ("hover" or "select") around `rects`
     *  (viewport rectangles); an empty list removes them. */
    _drawBoxes(kind, rects) {
      var old = this._boxes[kind];
      for (var i = 0; i < old.length; i++) if (old[i].parentNode) old[i].parentNode.removeChild(old[i]);
      var boxes = [];
      var vr = this.view.getBoundingClientRect();
      var pad = 2;
      for (var j = 0; j < rects.length; j++) {
        var r = rects[j];
        var box = h("span", { class: "se-box se-box-" + kind, "aria-hidden": "true" });
        box.style.left = Math.round(r.left - vr.left + this.view.scrollLeft - pad) + "px";
        box.style.top = Math.round(r.top - vr.top + this.view.scrollTop - pad) + "px";
        box.style.width = Math.round(r.width + 2 * pad) + "px";
        box.style.height = Math.round(r.height + 2 * pad) + "px";
        this.view.appendChild(box);
        boxes.push(box);
      }
      this._boxes[kind] = boxes;
    }

    _unionRect(rects) {
      var u = null;
      for (var i = 0; i < rects.length; i++) {
        var r = rects[i];
        if (!u) { u = { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; continue; }
        u.top = Math.min(u.top, r.top); u.bottom = Math.max(u.bottom, r.bottom);
        u.left = Math.min(u.left, r.left); u.right = Math.max(u.right, r.right);
      }
      if (u) { u.width = u.right - u.left; u.height = u.bottom - u.top; }
      return u;
    }

    _els(path) {
      var esc = (window.CSS && CSS.escape) ? CSS.escape(path) : path;
      return this.view.querySelectorAll('[data-path="' + esc + '"]');
    }

    /** ↓: into the selection - the sub-expression ↑ came from, or the first
     *  one; on an atom, a caret right after it (between the arguments of its
     *  parent, or extending the atom). */
    _selectChild() {
      if (this.range) { this.select(this._displayChildren(this.range.parent)[this.range.focus]); return; }
      if (!this.selected) { this.select("/"); return; }
      var t = this.tree[this.selected];
      if (t && t.children.length) {
        var back = this._cameFrom[this.selected];
        this.select(back && this.tree[back] && isAncestorOrSelf(this.selected, back) ? back
                    : this._displayChildren(this.selected)[0]);
        return;
      }
      if (this.opts.readOnly) return;
      var atom = this.selected, ael = this._els(atom)[0];
      var ap = t ? t.parent : null;
      if (ap && this.state.nodes[ap] && this.state.nodes[ap].insertable) {
        var ags = this._gapsOf(ap);
        for (var gi = 0; gi < ags.length; gi++) {
          if (ags[gi].leftEl === ael) { this._showCaret(Object.assign({}, ags[gi], { attach: "left" }), ags[gi].a); return; }
        }
      }
      var eg = this._extendGap(atom, "after");
      if (eg) this._showCaret(eg.gap, eg.x);
    }

    /** Select the parent of `path`, remembering where we came from (for ↓ and Unwrap). */
    _selectParent(path) {
      var parent = this.tree[path] ? this.tree[path].parent : null;
      if (!parent) return false;
      this._cameFrom[parent] = path;
      this.select(parent);
      return true;
    }

    /** Select a path (null to clear). */
    select(path) {
      this.range = null;
      if (path) this._hideCaret();
      this.selected = (path && (path in this.tree)) ? path : null;
      this._fillOps();
      this._applySelection();
      this._updateToolbar();
    }

    _applySelection() {
      var old = this.view.querySelectorAll(".se-selected");
      for (var i = 0; i < old.length; i++) old[i].classList.remove("se-selected");
      this._drawBoxes("hover", []);
      var rangePaths = this._rangePaths();
      if (rangePaths.length) {
        var rects = [];
        for (var r = 0; r < rangePaths.length; r++) {
          var rels = this._els(rangePaths[r]);
          for (var q = 0; q < rels.length; q++) { rels[q].classList.add("se-selected"); rects.push(this._visualRect(rels[q])); }
        }
        var u = this._unionRect(rects);
        this._drawBoxes("select", u ? [u] : []);
        this._setStatus(this.state.nodes[this.range.parent].type + " range: " + this._rangeSource(rangePaths));
        this._markSource(rangePaths);
        this._placeActions(u);
        return;
      }
      var node = this.selected && this.state && this.state.nodes ? this.state.nodes[this.selected] : null;
      if (node) {
        var els = this._els(this.selected);
        var srects = [];
        for (var j = 0; j < els.length; j++) { els[j].classList.add("se-selected"); srects.push(this._visualRect(els[j])); }
        this._drawBoxes("select", els.length && !els[0].classList.contains("se-editing") ? srects : []);
        this._setStatus(node.type + ": " + node.src + (node.reciprocal ? "  (denominator: the node is 1 over this)" : ""));
        this._markSource([this.selected]);
        this._placeActions(els.length && !els[0].classList.contains("se-editing") ? this._unionRect(srects) : null);
      } else {
        this._drawBoxes("select", []);
        this._markSource([]);
        this._placeActions(null);
      }
      if (!node && !this.closed) {
        this._setStatus(this.annotated ? (this.opts.readOnly ? "" : "Click to select; click between terms to insert")
                                       : "Structure unavailable (plain rendering)");
      }
    }

    _onClick(ev) {
      if (this._suppressClick) { this._suppressClick = false; return; }   // end of a drag
      if (this.closed || (this.input && ev.target === this.input)) return;
      var leaf = this._leafAt(ev);
      this._gapCache = null;
      // The edges of an object give a caret before/after it; its middle selects it.
      var edge = leaf && !this.opts.readOnly ? this._edgeCaretAt(leaf, ev.clientX) : null;
      var gap = edge ? edge.gap : (this.opts.readOnly ? null : this._gapAt(ev.clientX, ev.clientY, leaf));
      if (gap) {
        var same = this.caret && this.caret.path === gap.path && this.caret.index === gap.index && this.caret.extend === gap.extend && this.caret.attach === gap.attach;
        this.select(null);
        this.lastLeaf = null;
        this._showCaret(gap, edge ? edge.x : ev.clientX);
        if (same) { this.beginInsert(""); return; }   // clicking the caret again opens the field (no keyboard needed)
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
      // On a touch screen there are no keys: tapping the selected node again
      // opens the field (the toolbar's ↑ selects the parent instead).
      if (this._pointerType === "touch" && !this.opts.readOnly && this.selected === lp && !this.range) {
        this.beginEdit(lp);
        return;
      }
      // Clicking repeatedly on the same spot walks up the ancestors.
      if (this.selected && this.lastLeaf === lp && isAncestorOrSelf(this.selected, lp)) {
        if (!this._selectParent(this.selected)) this.select(lp);   // from the root, back to the glyph
      } else {
        this.select(lp);
      }
      this.lastLeaf = lp;
      this.view.focus({ preventScroll: true });
    }

    _onDblClick(ev) {
      if (this.opts.readOnly || this.closed || (this.input && ev.target === this.input)) return;
      // A field opened by the second click (caret, touch tap) must not be replaced;
      // touch screens use tap-again instead of double-tap.
      if (this.input || this._pointerType === "touch") return;
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
      } else if (ev.shiftKey && (k === "ArrowLeft" || k === "ArrowRight") && !this.caret) {
        this._extendRange(k === "ArrowRight" ? 1 : -1);          // grow / shrink a range
      } else if (k === "Tab" && (this.selected || this.range) && !ro) {
        if (!this.caretAtSelection(ev.shiftKey)) handled = false;
      } else if (this.range && k === "Escape") {
        this.range = null;
        this._applySelection();
        this._updateToolbar();
      } else if (this.range && k === "Enter") {
        if (!ro) this.beginRangeEdit();
      } else if (this.range && (k === "Delete" || k === "Backspace")) {
        if (!ro) this.send({ action: "delete", path: this.range.parent, children: this._rangeIndices() });
      } else if (this.range && k === "ArrowUp") {
        this.select(this.range.parent);
      } else if (this.range && (k === "ArrowDown" || k === "ArrowLeft" || k === "ArrowRight")) {
        this.select(this._displayChildren(this.range.parent)[this.range.focus]);   // collapse
      } else if (this.range && !ro && !mod && !ev.altKey && k.length === 1) {
        this.beginRangeEdit(k);
      } else if (this.caret && k === "Escape") {
        this._hideCaret();
        this._applySelection();
      } else if (this.caret && k === "Enter") {
        if (!ro) this.beginInsert("");
      } else if (this.caret && (k === "ArrowLeft" || k === "ArrowRight")) {
        this._moveCaret(k === "ArrowLeft" ? -1 : 1);
      } else if (this.caret && k === "ArrowUp") {
        // From a caret, ↑ first selects the object it sits next to (then the usual ancestors).
        var beside = this.caret.leftEl || this.caret.rightEl;
        var container = beside ? beside.getAttribute("data-path") : this.caret.path;
        this._hideCaret();
        this.select(container);
      } else if (this.caret && !ro && !mod && !ev.altKey && k.length === 1) {
        this.beginInsert(k);
      } else if (k === "Enter") {
        if (!ro) this.beginEdit(this.selected || "/");
      } else if (k === "Escape") {
        this.select(null);
      } else if (k === "Backspace") {
        if (!ro && this.selected) this.unwrapSelection();
      } else if (k === "Delete") {
        if (!ro && this.selected && this.selected !== "/") this.send({ action: "delete", path: this.selected });
      } else if (k === "ArrowUp") {
        if (this.selected) this._selectParent(this.selected);
      } else if (k === "ArrowDown") {
        this._selectChild();
      } else if (k === "ArrowLeft" || k === "ArrowRight") {
        this._moveSideways(k === "ArrowLeft" ? -1 : 1);
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
      // The rendering is never replaced by code: the whole expression is
      // edited in the source line.
      if (path === "/" && initial === undefined && this.editSource()) return;
      if (this.editing !== null || this.inserting) this.cancelEdit(true);
      var self = this;
      var original = this.state.nodes[path].src;
      var host = this._els(path)[0] || this.view;
      var input = h("input", {
        class: "se-inline", type: "text", spellcheck: "false", autocomplete: "off",
        "aria-label": "Replacement for " + original + " (SymPy syntax)"
      });
      input.value = toDisplay(initial !== undefined ? initial : original);
      var stash = document.createDocumentFragment();
      while (host.firstChild) stash.appendChild(host.firstChild);
      host.appendChild(input);
      host.classList.add("se-editing");
      this.editing = path;
      this.input = input;
      this._editHost = host;
      this._editStash = stash;
      this._editOriginal = original;
      this._wireField(input, 2);
      input.addEventListener("blur", function () {
        if (self.editing === path && self.input === input) self.commitEdit();
      });
      this.select(path);
      this._drawBoxes("select", []);
      this._placeActions(null);
      this._setStatus("Editing " + this.state.nodes[path].type + " – Enter to apply, Esc to cancel");
      input.focus();
      if (initial === undefined) input.select();
      else if (extend) input.setSelectionRange(input.value.length, input.value.length);
    }

    /** Drop the selected node, keeping the argument the user came up from
     *  (↑ from a child) or the natural one. */
    unwrapSelection() {
      if (!this.selected || this.opts.readOnly) return;
      var msg = { action: "unwrap", path: this.selected };
      var back = this._cameFrom[this.selected];
      if (back && isAncestorOrSelf(this.selected, back) && back !== this.selected) msg.keep = this._argIndex(this.selected, back);
      this.send(msg);
    }

    /** Show the floating action bar under a viewport rectangle (null hides it). */
    _placeActions(rect) {
      if (!this.actions) return;
      if (!rect || this.input || this.closed) { this.actions.hidden = true; this.view.style.paddingBottom = ""; return; }
      var t = this.selected ? this.tree[this.selected] : null;
      var unwrapOk = !!(this.selected && !this.range && this.state.nodes[this.selected] && this.state.nodes[this.selected].nargs);
      var buttons = this.actions.querySelectorAll("button");
      for (var i = 0; i < buttons.length; i++) {
        var cmd = buttons[i].getAttribute("data-cmd");
        buttons[i].disabled = cmd === "parent" ? !(this.range || (t && t.parent))
                            : cmd === "child" ? false
                            : cmd === "unwrap" ? !unwrapOk
                            : cmd === "delete" ? !(this.range || (this.selected && this.selected !== "/"))
                            : false;
      }
      this.actions.hidden = false;
      var rr = this.root.getBoundingClientRect();
      var left = rect.left - rr.left, top = rect.bottom - rr.top + 6;
      var maxLeft = Math.max(0, this.root.clientWidth - this.actions.offsetWidth - 4);
      this.actions.style.left = Math.round(Math.max(0, Math.min(left, maxLeft))) + "px";
      this.actions.style.top = Math.round(top) + "px";
      // Keep the bar inside the formula area so it never covers the source line
      // (measured against the view's own padding, not the room added before).
      this.view.style.paddingBottom = "";
      var vr = this.view.getBoundingClientRect();
      var overflow = (rect.bottom + 6 + this.actions.offsetHeight + 4) - vr.bottom;
      if (overflow > 0) this.view.style.paddingBottom = (parseFloat(getComputedStyle(this.view).paddingBottom) + overflow) + "px";
    }

    /* ---- clipboard ---- */

    /** SymPy source of the selection (node or range), or null. */
    _selectionSource() {
      var paths = this._rangePaths();
      if (paths.length) return this._rangeSource(paths);
      if (this.selected && this.state.nodes[this.selected]) return this.state.nodes[this.selected].src;
      return null;
    }

    _onClipboard(ev, kind) {
      var active = document.activeElement;
      if (active !== this.view || this.closed || !this.state) return;   // fields and the source line keep the native behaviour
      if (kind === "paste") {
        if (this.opts.readOnly) return;
        var text = (ev.clipboardData && ev.clipboardData.getData("text/plain")) || this._clip || "";
        text = text.trim();
        if (!text) return;
        ev.preventDefault();
        if (this.caret) { this.beginInsert(text); this.commitEdit(); }
        else if (this.range) { this.beginRangeEdit(text); this.commitEdit(); }
        else if (this.selected) { this.beginEdit(this.selected, text); this.commitEdit(); }
        else this._setStatus("Select where to paste (a node, or a caret between terms)");
        return;
      }
      var src = this._selectionSource();
      if (!src) return;
      ev.preventDefault();
      if (ev.clipboardData) ev.clipboardData.setData("text/plain", src);
      this._clip = src;
      if (kind === "cut" && !this.opts.readOnly) {
        if (this.range) this.send({ action: "delete", path: this.range.parent, children: this._rangeIndices() });
        else if (this.selected !== "/") this.send({ action: "delete", path: this.selected });
        else this._setStatus("Copied: " + src + " (the whole expression cannot be cut)");
      } else {
        this._setStatus("Copied: " + src);
      }
    }

    /* ---- the source line ---- */

    /** Highlight the source text of `paths` with a <mark> (not the document
     *  selection, which would move the focus into the editable line). */
    _markSource(paths) {
      if (this.sourceDirty || document.activeElement === this.source || !this.state) return;
      var text = this.state.src || "";
      var spans = this.state.spans || {};
      var lo = Infinity, hi = -Infinity;
      for (var i = 0; i < paths.length; i++) {
        var sp = spans[paths[i]];
        if (!sp) { lo = Infinity; break; }
        lo = Math.min(lo, sp[0]); hi = Math.max(hi, sp[1]);
      }
      this.source.textContent = "";
      if (!paths.length || lo === Infinity || hi > text.length) { this.source.textContent = text; return; }
      this.source.appendChild(document.createTextNode(text.slice(0, lo)));
      this.source.appendChild(h("mark", {}, [text.slice(lo, hi)]));
      this.source.appendChild(document.createTextNode(text.slice(hi)));
    }

    /** A selection made in the source line selects the innermost node whose
     *  span contains it. */
    _onSourceSelection() {
      // Only selections the user makes in the source line (it has focus then);
      // the highlight set from the rendering also fires selectionchange.
      if (this.sourceDirty || !this.state || !this.state.spans) return;
      if (document.activeElement !== this.source) return;
      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || !this.source.contains(sel.anchorNode) || !this.source.contains(sel.focusNode)) return;
      var range = sel.getRangeAt(0);
      var pre = document.createRange();
      pre.selectNodeContents(this.source);
      pre.setEnd(range.startContainer, range.startOffset);
      var start = pre.toString().length, end = start + range.toString().length;
      var best = null, bestLen = Infinity;
      for (var path in this.state.spans) {
        var sp = this.state.spans[path];
        var inside = end > start ? (sp[0] <= start && end <= sp[1]) : (sp[0] <= start && start <= sp[1]);
        if (inside && sp[1] - sp[0] < bestLen) { best = path; bestLen = sp[1] - sp[0]; }
      }
      if (end > start) {
        if (best && best !== this.selected) {
          this._hideCaret();
          this.range = null;
          this.selected = best;
          this._fillOps();
          this._applySelection();
          this._updateToolbar();
        }
      } else if (best) {
        var el = this._els(best)[0];
        this._drawBoxes("hover", el ? [this._visualRect(el)] : []);   // a caret in the source: a hover hint
      }
    }

    /** Apply the edited source line as the whole expression. */
    commitSource() {
      var src = toSource(this.source.textContent).trim();
      var same = src === (this.state ? this.state.src : "");
      this.sourceDirty = false;
      this.source.classList.remove("se-dirty");
      if (!src || same) { this.revertSource(); return; }
      this.send({ action: "set", src: src });
    }

    revertSource() {
      this.source.textContent = this.state ? this.state.src : "";
      this.sourceDirty = false;
      this.source.classList.remove("se-dirty");
      this._applySelection();
    }

    /** Put the keyboard in the source line with everything selected. */
    editSource() {
      if (this.opts.readOnly || !this.opts.showSource) return false;
      this.source.focus();
      var sel = window.getSelection();
      if (sel && this.source.firstChild) sel.selectAllChildren(this.source);
      this._setStatus("Editing the whole expression as SymPy source – Enter applies, Esc reverts");
      return true;
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
        .map(function (k) { k.rect = self._visualRect(k.el); return k; })
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
        // The caret takes the height of the argument it follows, like a text
        // cursor after a character - not the union with a taller neighbour.
        push(this._argIndex(p, r.path), l.rect.right, r.rect.left, l.el, r.el, l.rect.top, l.rect.bottom);
      }
      push(node.nargs, last.rect.right, (p === "/" ? Math.max(hr.right, last.rect.right) : last.rect.right) + pad,
        last.el, null, last.rect.top, last.rect.bottom);
      return gaps;
    }

    /** A caret before/after the object whose left/right edge zone contains
     *  `x`: `leaf`, or an ancestor sharing that edge, that is an argument of
     *  an insertable node.  Null when `x` is in the middle of the glyphs
     *  (a click there selects). */
    _edgeCaretAt(leaf, x) {
      if (!leaf || !this.state) return null;
      var path = leaf.getAttribute("data-path");
      var side = null, first = null;
      while (path) {
        var parent = this.tree[path] ? this.tree[path].parent : null;
        var el = this._els(path)[0];
        if (!el) return null;
        var r = this._visualRect(el);
        // A thin strip on the clicked object (its middle is for selecting);
        // going up, the edge only has to be near (scripts add trailing space).
        var zone = side ? 10 : Math.min(5, Math.max(2, r.width * 0.2));
        var here = x <= r.left + zone ? "before" : (x >= r.right - zone ? "after" : null);
        if (!here || (side && here !== side)) break;
        side = here;
        if (!first) first = { path: path, el: el, rect: r };
        var pnode = parent ? this.state.nodes[parent] : null;
        if (pnode && pnode.insertable) {
          var gaps = this._allGaps();
          for (var i = 0; i < gaps.length; i++) {
            var g = gaps[i];
            if (g.path !== parent) continue;
            // Remember which neighbour the caret belongs to: typing attaches to it.
            if (side === "before" ? g.rightEl === el : g.leftEl === el) {
              return { gap: Object.assign({}, g, { attach: side === "before" ? "right" : "left" }), x: side === "before" ? g.b : g.a };
            }
          }
          break;
        }
        // Entries of a matrix/array stay their own: typing at their edge extends them.
        if (!parent || (pnode && pnode.kinds && (pnode.kinds[0] === "matrix" || pnode.kinds[0] === "array") && !pnode.insertable)) break;
        path = parent;   // this edge is also the enclosing node's edge: try one level up
      }
      if (!first || !side) return null;
      return this._extendGap(first.path, side);   // no argument list to insert into: type next to the object itself
    }

    /** An "extend" caret before/after the node at `path`: typing there is
     *  combined with the node (see Document.extend). */
    _extendGap(path, side) {
      var el = this._els(path)[0];
      if (!el) return null;
      var r = this._visualRect(el), x = side === "before" ? r.left : r.right;
      return { gap: { path: path, extend: side, index: 0, a: x, b: x,
                      leftEl: side === "after" ? el : null, rightEl: side === "before" ? el : null,
                      top: r.top, bottom: r.bottom, height: r.height }, x: x };
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
      this._placeActions(null);
      // Vertical extent: the object the caret sits next to (measured now,
      // since the gap may have been computed before a scroll).
      var beside = gap.leftEl || gap.rightEl;
      if (beside) {
        var br = this._visualRect(beside);
        gap.top = br.top; gap.bottom = br.bottom; gap.height = br.height;
      }
      var vr = this.view.getBoundingClientRect();
      var cx = Math.max(gap.a, Math.min(x === undefined ? gap.b : x, gap.b));
      this.caretEl.style.left = Math.round(cx - vr.left + this.view.scrollLeft - 1) + "px";
      this.caretEl.style.top = Math.round(gap.top - vr.top + this.view.scrollTop) + "px";
      this.caretEl.style.height = Math.round(Math.max(12, gap.height)) + "px";
      this.view.appendChild(this.caretEl);
      // A caret and a selection never coexist: with a caret, keys only insert.
      // Lift the selection completely: classes, highlight box, source mark, action bar.
      this.selected = null;
      this.range = null;
      this._applySelection();
      var node = this.state.nodes[gap.path];
      this._setStatus(gap.extend
        ? "Type " + (gap.extend === "before" ? "before " : "after ") + node.type + " " + node.src + " (\"+ 1\" adds, \"y\" multiplies; Enter to apply)"
        : "Type into " + node.type + " " + node.src + " (\"y\" multiplies the neighbour, \"+ y\" adds a term; Enter to apply)");
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
      if (!this.state) return false;
      var child, p;
      if (this.range) {
        var kids = this._displayChildren(this.range.parent);
        child = kids[before ? Math.min(this.range.anchor, this.range.focus) : Math.max(this.range.anchor, this.range.focus)];
        p = this.range.parent;
      } else {
        if (!this.selected) return false;
        child = this.selected;
        p = this.tree[child] ? this.tree[child].parent : null;
        while (p && !(this.state.nodes[p] && this.state.nodes[p].insertable)) {
          child = p;
          p = this.tree[p].parent;
        }
      }
      if (!p) return false;
      var el = this._els(child)[0];
      var gaps = this._gapsOf(p);
      for (var i = 0; i < gaps.length; i++) {
        if (before ? gaps[i].rightEl === el : gaps[i].leftEl === el) {
          this._showCaret(Object.assign({}, gaps[i], { attach: before ? "right" : "left" }), before ? gaps[i].b : gaps[i].a);
          return true;
        }
      }
      return false;
    }

    /** ←/→: the neighbouring sub-expression as displayed; at the end of a
     *  group, continue with the neighbour of the enclosing group. */
    _moveSideways(step) {
      var cur = this.selected;
      while (cur) {
        var parent = this.tree[cur] ? this.tree[cur].parent : null;
        if (!parent) return;
        var sib = this._displayChildren(parent);
        var i = sib.indexOf(cur) + step;
        if (i >= 0 && i < sib.length) { this.select(sib[i]); return; }
        cur = parent;
      }
    }

    /* ---- range selection ---- */

    /** The annotated children of `p`, left to right as displayed. */
    _displayChildren(p) {
      var self = this;
      return (this.tree[p] ? this.tree[p].children : [])
        .map(function (c) { var el = self._els(c)[0]; return { path: c, left: el ? el.getBoundingClientRect().left : 0 }; })
        .sort(function (a, b) { return a.left - b.left; })
        .map(function (k) { return k.path; });
    }

    /** The child of `p` whose subtree contains `path`. */
    _childOf(p, path) {
      var kids = this.tree[p] ? this.tree[p].children : [];
      for (var i = 0; i < kids.length; i++) if (isAncestorOrSelf(kids[i], path)) return kids[i];
      return null;
    }

    _rangePaths() {
      if (!this.range || !this.state) return [];
      var kids = this._displayChildren(this.range.parent);
      var lo = Math.min(this.range.anchor, this.range.focus), hi = Math.max(this.range.anchor, this.range.focus);
      return kids.slice(lo, hi + 1);
    }

    _rangeIndices() {
      var self = this, parent = this.range.parent;
      return this._rangePaths().map(function (c) { return self._argIndex(parent, c); });
    }

    /** SymPy source of the range, from its arguments' sources. */
    _rangeSource(paths) {
      var self = this;
      var type = this.state.nodes[this.range.parent].type;
      var sep = /Add$/.test(type) ? " + " : /Mul$/.test(type) ? "*" : type === "And" ? " & " : type === "Or" ? " | " : ", ";
      return paths.map(function (c) {
        var src = self.state.nodes[c].src;
        return sep === "*" && /[+\-]/.test(src.slice(1)) ? "(" + src + ")" : src;
      }).join(sep);
    }

    _setRange(parent, anchor, focus) {
      this._hideCaret();
      var kids = this._displayChildren(parent);
      if (anchor === focus) { this.select(kids[anchor]); return; }
      this.range = { parent: parent, anchor: anchor, focus: focus };
      this.selected = null;
      this._fillOps();
      this._applySelection();
      this._updateToolbar();
    }

    /** Shift+arrow: grow the range by one sibling (or shrink it back). */
    _extendRange(step) {
      var r = this.range;
      if (!r) {
        if (!this.selected) return;
        var child = this.selected, p = this.tree[child] ? this.tree[child].parent : null;
        while (p && !(this.state.nodes[p] && this.state.nodes[p].rangeable)) { child = p; p = this.tree[p].parent; }
        if (!p) return;
        var i = this._displayChildren(p).indexOf(child);
        if (i < 0) return;
        r = { parent: p, anchor: i, focus: i };
      }
      var n = this._displayChildren(r.parent).length;
      this._setRange(r.parent, r.anchor, Math.max(0, Math.min(n - 1, r.focus + step)));
    }

    /** Drag from glyph `a` to glyph `b`: the range of siblings between them
     *  in their nearest common rangeable ancestor (or that ancestor itself). */
    _dragSelect(a, b) {
      if (isAncestorOrSelf(a, b)) { this.select(a); return; }
      var p = this.tree[a] ? this.tree[a].parent : null;
      while (p) {
        if (isAncestorOrSelf(p, b) && this.state.nodes[p]) {
          var ca = this._childOf(p, a), cb = this._childOf(p, b);
          if (ca === cb) { this.select(ca); return; }
          if (this.state.nodes[p].rangeable) {
            var kids = this._displayChildren(p);
            this._setRange(p, kids.indexOf(ca), kids.indexOf(cb));
          } else {
            this.select(p);
          }
          return;
        }
        p = this.tree[p].parent;
      }
    }

    /** Edit the range in place: one field replaces its arguments. */
    beginRangeEdit(initial) {
      var paths = this._rangePaths();
      if (!paths.length || this.opts.readOnly || this.closed || this.busy) return;
      if (this.editing !== null || this.inserting) this.cancelEdit(true);
      var self = this;
      var parent = this.range.parent;
      var original = this._rangeSource(paths);
      var first = this._els(paths[0])[0], last = this._els(paths[paths.length - 1])[0];
      if (!first || !last) return;
      var children = this._rangeIndices();
      if (first !== last) {   // drop the rendering after the first argument up to the last
        var r = document.createRange();
        r.setStartAfter(first);
        r.setEndAfter(last);
        r.deleteContents();
      }
      var input = h("input", { class: "se-inline", type: "text", spellcheck: "false", autocomplete: "off",
        "aria-label": "Replacement for " + original + " (SymPy syntax)" });
      input.value = toDisplay(initial !== undefined ? initial : original);
      while (first.firstChild) first.removeChild(first.firstChild);
      first.appendChild(input);
      first.classList.add("se-editing");
      this.editing = parent;
      this._editRange = { path: parent, children: children };
      this._editOriginal = original;
      this.input = input;
      this._editHost = null;
      this._editStash = null;
      this._wireField(input, 2);
      input.addEventListener("blur", function () {
        if (self._editRange && self.input === input) self.commitEdit();
      });
      this._drawBoxes("select", []);
      this._drawBoxes("hover", []);
      this._placeActions(null);
      this._setStatus("Editing " + this.state.nodes[parent].type + " range – Enter to apply, Esc to cancel");
      input.focus();
      if (initial === undefined) input.select();
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
      this._wireField(input, 5);
      input.addEventListener("blur", function () {
        if (self.inserting === gap && self.input === input) self.commitEdit();
      });
      this._setStatus((gap.extend ? "Typing next to " : "Inserting into ") + this.state.nodes[gap.path].type + " – Enter to apply, Esc to cancel");
      input.focus();
    }

    /** Sizing, Enter/Escape and live "\\command" expansion for a field. */
    _wireField(input, minWidth) {
      var self = this;
      var fit = function () { input.style.width = Math.max(minWidth, input.value.length + 1) + "ch"; };
      fit();
      input.addEventListener("input", function () {
        var cursor = input.selectionStart;
        var r = expandCommands(input.value, cursor);
        if (r.text !== input.value) {
          input.value = r.text;
          input.setSelectionRange(cursor + r.delta, cursor + r.delta);
        }
        fit();
      });
      input.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key === "Enter") { ev.preventDefault(); self.commitEdit(); }
        else if (ev.key === "Escape") { ev.preventDefault(); self.cancelEdit(); }
      });
    }

    _endEdit() {
      var host = this._editHost, input = this.input, stash = this._editStash;
      this.editing = null;
      this.inserting = null;
      this._editRange = null;
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
      var inserting = this.inserting, editRange = this._editRange;
      var path = this.editing;
      var src = toSource(this.input.value).trim();
      var original = this._editOriginal;
      this._endEdit();
      this._applySelection();
      this.view.focus({ preventScroll: true });
      if (editRange) {
        if (!src || src === original) { this.setState(this.state); return; }   // restore the rendering
        this.send({ action: "replace", path: editRange.path, children: editRange.children, src: src });
        return;
      }
      if (inserting) {
        if (src && inserting.extend) this.send({ action: "extend", path: inserting.path, side: inserting.extend, src: src });
        else if (src) {
          var parent = inserting.path;
          var msg = { action: "insert", path: parent, index: inserting.index, src: src };
          if (inserting.leftEl) msg.left = this._argIndex(parent, inserting.leftEl.getAttribute("data-path"));
          if (inserting.rightEl) msg.right = this._argIndex(parent, inserting.rightEl.getAttribute("data-path"));
          if (inserting.attach) msg.attach = inserting.attach;
          this.send(msg);
        }
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
      var editRange = this._editRange;
      this._endEdit();
      if (editRange) { this.setState(this.state); return; }   // re-render what the field replaced
      this._applySelection();
      if (!silent) this.view.focus({ preventScroll: true });
    }

    /* ---- commands ---- */

    command(cmd) {
      switch (cmd) {
        case "undo": return this.send({ action: "undo" });
        case "redo": return this.send({ action: "redo" });
        case "edit":
          if (this.caret) return this.beginInsert("");
          if (this.range) return this.beginRangeEdit();
          return this.beginEdit(this.selected || "/");
        case "unwrap": return this.unwrapSelection();
        case "delete":
          if (this.range) return this.send({ action: "delete", path: this.range.parent, children: this._rangeIndices() });
          if (this.selected && this.selected !== "/") return this.send({ action: "delete", path: this.selected });
          return;
        case "child": return this._selectChild();
        case "parent": {
          if (this.range) { this.select(this.range.parent); return; }
          if (this.selected) this._selectParent(this.selected);
          return;
        }
        case "keyboard":
          if (this.input) { this.input.focus(); return; }   // bring the keyboard back for the open field
          if (this.caret) return this.beginInsert("");
          if (this.range) return this.beginRangeEdit();
          return this.beginEdit(this.selected || "/");
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
      var text = this._selectionSource() || (this.state ? this.state.src : "");
      this._clip = text;
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
      var range = !!this.range;
      set("undo", dis || !s.can_undo);
      set("redo", dis || !s.can_redo);
      set("edit", dis);
      set("keyboard", dis);
      set("delete", dis || !(range || (this.selected && this.selected !== "/")));
      set("unwrap", dis || range || !this.selected || !(s.nodes && s.nodes[this.selected] && s.nodes[this.selected].nargs));
      set("parent", dis || !(range || (t && t.parent)));
      set("child", dis);
      set("copy", !s.src);
      set("finish", dis);
      if (this.opsSelect) this.opsSelect.disabled = dis;
      if (this.typeMenu) this.typeMenu.disabled = dis;
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
        // Pyodide wants an absolute index URL; relative ones (vendored bundles) are resolved against the page.
        var py = await window.loadPyodide({ indexURL: new URL(cfg.pyodideIndex, document.baseURI).href });
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
    function start(report) {
      if (!ready) ready = init(report).catch(function (e) { ready = null; throw e; });
      return ready;
    }
    return {
      send: async function (msg, report) {
        var handle = await start(report || function () {});
        return JSON.parse(handle(JSON.stringify(msg)));
      },
      /** Load the runtime now (page load) instead of at the first edit. */
      warmup: function (report) { return start(report).then(function () { report(""); }, function (e) { report("Python failed to load: " + e.message); }); }
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
    var backend = make(cfg);
    var editor = new Editor(host, backend, options);
    editor.setState(cfg.snapshot).then(function () {
      if (backend.warmup && editor.opts.preload !== false) {
        backend.warmup(function (text) { if (!editor.input && !editor.busy) editor._setStatus(text); });
      }
    });
    return editor;
  }

  return {
    Editor: Editor,
    backends: backends,
    mount: mount,
    loadKatex: loadKatex,
    toDisplay: toDisplay,
    toSource: toSource,
    expandCommands: expandCommands,
    buildTree: buildTree,
    parentPath: parentPath,
    DEFAULTS: DEFAULTS
  };
})();
