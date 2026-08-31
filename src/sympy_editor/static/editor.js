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
 *   SympyEditor.backends.{http, pyodide, native, readonly}
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
    preload: true,       // Pyodide pages: start loading Python at page load, not at the first edit
    zoom: 1,             // initial magnification of the formula (1 = the CSS size)
    minZoom: 0.4,
    maxZoom: 4,
    rememberZoom: false, // keep the zoom in localStorage across page loads (the mobile app does)
    previewDelay: 250,   // ms after the last keystroke in the source line before it is previewed
    workingAfter: 400,   // ms a request may take before the spinner overlay appears
    interruptAfter: 2000, // ms after which the overlay offers to interrupt the computation
    sessions: false,     // a list of sessions (expressions with their own history) kept in localStorage
    unevaluated: false,  // the "unevaluated" toggle starts on: transformations build Determinant(M), Integral(f, x)... rather than computing
    animate: true,       // animate a change: the old parts in red turn into the new ones in green
    animateDuration: 1600 // ms: a quarter to show what goes (red), the rest to move it and fade the new in (green)
  };
  var SESSIONS_KEY = "sympy-editor:sessions";

  // The history report: a self-contained page (KaTeX pre-rendered, its CSS
  // and fonts inlined), see Editor.buildReport.
  var REPORT_CSS = [
    "body { --fg: #1f2328; --bg: #ffffff; margin: 0; padding: 1.5rem; font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--fg); }",
    "@media (prefers-color-scheme: dark) { body { --fg: #e6e6e6; --bg: #1e1e1e; } .step, .transition .before { border-color: #444; } .meta, .transition .what, .step code, footer { color: #a0a0a0; } }",
    "main { max-width: 60rem; margin: 0 auto; }",
    "h1 { font-size: 1.3rem; margin: 0 0 0.3rem; } .meta { color: #656d76; font-size: 0.9rem; margin: 0 0 1.5rem; }",
    ".step { border: 1px solid #d0d7de; border-radius: 0.6rem; padding: 0.8rem 1rem; margin: 0; overflow-x: auto; }",
    ".step h2 { font-size: 0.85rem; font-weight: 600; margin: 0 0 0.4rem; color: #656d76; text-transform: uppercase; letter-spacing: 0.04em; }",
    ".step .formula { font-size: 1.25em; } .step code { display: block; margin-top: 0.5rem; font-size: 0.8rem; color: #656d76; white-space: pre-wrap; word-break: break-word; }",
    ".transition { display: grid; grid-template-columns: 2.5rem 1fr; align-items: center; gap: 0.3rem 0.6rem; margin: 0.4rem 0 0.4rem 1rem; }",
    ".transition .arrow { grid-row: 1 / 3; font-size: 1.8rem; text-align: center; color: #3b82f6; }",
    ".transition .what { font-weight: 600; } .transition .before { font-size: 0.9em; padding: 0.3rem 0.6rem; border-left: 3px solid #d0d7de; overflow-x: auto; }",
    ".transition .before .label { display: block; font-size: 0.75rem; color: #656d76; margin-bottom: 0.2rem; }",
    ".rep-added, .rep-removed { font-weight: bold; }",
    ".rep-added { color: #1a7f37; } .rep-removed { color: #d1242f; }",
    /* One tinted box per changed region, carried by the outermost mark
     * (rep-box, see _renderMarked).  An inline background paints the line
     * box only - a tall fraction or matrix then keeps a band across its
     * middle and pokes out above and below - so the box is inline-block,
     * whose box is the node's whole visual extent.  The tint is solid: with
     * one box per region nothing overlaps, and nothing stacks darker. */
    ".rep-box { display: inline-block; border-radius: 0.15em; padding: 0 0.05em; }",
    ".rep-added.rep-box { background: #e8f2eb; } .rep-removed.rep-box { background: #fae9ea; }",
    ".rep-kept { color: var(--fg); font-weight: normal; }",
    "@media (prefers-color-scheme: dark) { .rep-added { color: #3fb950; } .rep-removed { color: #ff7b72; }",
    "  .rep-added.rep-box { background: #233726; } .rep-removed.rep-box { background: #422d2b; } }",
    ".katex-display { margin: 0.3em 0; text-align: left; } .katex-display > .katex { text-align: left; }",
    "footer { margin-top: 2rem; font-size: 0.8rem; color: #656d76; }"
  ].join("\n");
  // The in-page guide (the toolbar's "?"): every gesture, key and tool.
  var HELP_HTML = [
    '<div class="se-help-cols">',
    "<section><h3>Selecting</h3><ul>",
    "<li>Click the middle of anything to select it; click the same spot again for the enclosing expression.</li>",
    "<li><kbd>\u2191</kbd> enclosing, <kbd>\u2193</kbd> inside, <kbd>\u2190</kbd>/<kbd>\u2192</kbd> siblings, <kbd>Esc</kbd> deselects (the same arrows sit in the toolbar and under the selection).</li>",
    "<li>Drag across terms to select a range; <kbd>Shift</kbd>+<kbd>\u2190</kbd>/<kbd>\u2192</kbd> grows and shrinks it.</li>",
    "<li>The line under the tools names the selection: its type and SymPy form.</li>",
    "</ul></section>",
    "<section><h3>Editing</h3><ul>",
    "<li>Just type over a selection to replace it; <kbd>Enter</kbd> or a double-click edits its existing text in place.</li>",
    "<li><b>Delete</b> removes the selection. Deleting the whole expression empties the view: type the new one right there.</li>",
    "<li><b>Unwrap</b> (<kbd>Backspace</kbd>) removes the node but keeps an argument: cos(\u03b8) \u2192 \u03b8; it asks which one when there is a choice.</li>",
    "<li><b>Isolate</b> keeps only the selection; <b>Copy</b>/<b>Paste</b> and <kbd>Ctrl</kbd>+<kbd>C</kbd>/<kbd>X</kbd>/<kbd>V</kbd> work on selections and carets.</li>",
    "<li><kbd>Ctrl</kbd>+<kbd>Z</kbd> undoes, <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> redoes.</li>",
    "<li>The source line under the formula is the whole expression as SymPy text: edit it there too.</li>",
    "</ul></section>",
    "<section><h3>Typing between things</h3><ul>",
    "<li>Click between two terms, or at the left/right edge of an object: a caret appears. <kbd>Tab</kbd>/<kbd>Shift</kbd>+<kbd>Tab</kbd> put it after/before the selection; <kbd>\u2190</kbd>/<kbd>\u2192</kbd> walk it through the formula like a text cursor.</li>",
    "<li>What you type is spliced in: operators as written, nothing between means multiplication (cos(t) after x gives x\u22c5cos(t)), <b>+</b>/<b>\u2212</b> add and subtract at the level of the sum, and a typed comma adds a function argument.</li>",
    "<li>Next to a matrix entry or a power's base the caret extends that object: \u201c+ 1\u201d adds to it, \u201cy\u201d multiplies it.</li>",
    "<li>LaTeX shortcuts in any field: \\theta becomes \u03b8 as you type (Greek letters, \\infty, \\le\u2026).</li>",
    "</ul></section>",
    "<section><h3>Operators</h3><ul>",
    "<li>Click an operator itself (<b>+</b>, <b>\u2212</b>, <b>\u22c5</b>, <b>=</b>\u2026) to select it; a small palette appears.</li>",
    "<li>Type <kbd>+</kbd> <kbd>-</kbd> <kbd>*</kbd> <kbd>/</kbd> <kbd>^</kbd> <kbd>=</kbd> to change it; <kbd>Del</kbd> removes it \u2014 side by side, the two multiply (x + y \u2192 xy).</li>",
    "<li>In a sum, * binds just the two terms (x + y + z \u2192 xy + z); in a product, + splits it there (x\u22c5y\u22c5z \u2192 x + yz).</li>",
    "</ul></section>",
    "<section><h3>Applying functions</h3><ul>",
    "<li><b>Transform \u25be</b> holds the general operations; a second menu appears with operations for the selection's type (Matrix, Integral, Equation\u2026). Picking one applies it at once, to the selection or, with nothing selected, to the whole expression.</li>",
    "<li><b>Methods \u25be</b> lists everything the selected object's class can do \u2014 .det(), .T, .diff()\u2026 \u2014 one pick calls it. A Lambda is itself a function: <b>( ) apply</b> evaluates it at the arguments you give.</li>",
    "<li>The <b>function box</b> searches all of SymPy: pick a function and fill the parameters it asks for; \u201cdiff(x)\u201d, \u201c.T\u201d, \u201cdet()\u201d typed in full apply as written.</li>",
    "<li><b>unevaluated</b> builds the symbolic form (Determinant, Integral, sin(0)\u2026) instead of computing it; <i>Evaluate (doit)</i> computes it later.</li>",
    "<li>The <b>Symbols</b> panel under the formula declares new names and changes what a name stands for (symbol, function, matrix, assumptions).</li>",
    "</ul></section>",
    "<section><h3>History and sessions</h3><ul>",
    "<li><b>History</b> shows every step and what changed (green: what a step brought, red: what it lost); tap a step to go back to it.</li>",
    "<li>From there, save the history as a self-contained web page or as a Python script that rebuilds every step.</li>",
    "<li><b>\u2630</b> lists the sessions, where the page keeps several.</li>",
    "</ul></section>",
    "<section><h3>On a phone or tablet</h3><ul>",
    "<li>Tap to select; tap the selected node again to edit it.</li>",
    "<li>Tap a gap for a caret, tap the caret again to insert; tap an operator for its palette.</li>",
    "<li>Drag to select a range; two fingers zoom; <b>\u2328</b> opens the keyboard for the selection.</li>",
    "</ul></section>",
    "<section><h3>Zoom and full screen</h3><ul>",
    "<li><kbd>Ctrl</kbd>+wheel, <kbd>Ctrl</kbd>+<kbd>+</kbd>/<kbd>\u2212</kbd>/<kbd>0</kbd>, pinch, or the \u2212/100%/+ buttons.</li>",
    "<li>The faint button in the top-right corner of the editing area gives the formula the whole window; it (or <kbd>Esc</kbd>, with nothing selected) comes back.</li>",
    "</ul></section>",
    "</div>"
  ].join("");
  var ZOOM_KEY = "sympy-editor:zoom";
  var ZOOM_STEP = 1.2;

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

  // Paths: "/" is the root, "/1/0" is expr.args[1].args[0].  A named step is
  // a virtual part of the view tree: "/n" and "/d" the numerator and
  // denominator of what is shown as a fraction, "/neg" the product after a
  // leading minus (see printer.view_parts).
  var PART_ORDER = { neg: 0, n: 0, d: 1 };
  // What KaTeX shows for the operator between two arguments, and the key
  // (SymPy operator) each stands for.  A glyph is selectable: typing another
  // operator over it changes how the two arguments are combined.
  var OPERATOR_GLYPHS = { "+": "+", "\u2212": "-", "-": "-", "\u22c5": "*", "\u00b7": "*", "\u00d7": "*",
                          "=": "=", "<": "<", ">": ">", "\u2264": "<", "\u2265": ">", "\u2260": "=",
                          "\u2227": "&", "\u2228": "|" };
  var OPERATOR_KEYS = "+-*/^=<>&|";
  function parentPath(p) {
    if (!p || p === "/") return null;
    var i = p.lastIndexOf("/");
    return i === 0 ? "/" : p.slice(0, i);
  }
  /** The last step of a path as a sort key: an argument index, or the
   *  display order of a part. */
  function lastIndex(p) {
    var step = p.slice(p.lastIndexOf("/") + 1);
    return step in PART_ORDER ? PART_ORDER[step] : parseInt(step, 10);
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

  /** What changed between two node tables (path -> {src, type, nargs}):
   *  what is kept is not coloured, the rest is (red on the old side, green
   *  on the new).  The two trees are aligned from the root down: in two
   *  corresponding containers, children with the same expression are
   *  kept with everything inside them (in any order: SymPy reorders
   *  terms), the remaining children that are containers of the same type
   *  are paired up in order and aligned in turn, and the rest goes / comes
   *  as a whole.  So unwrapping cos(x)**2 in sin(x)**2 + cos(x)**2 colours
   *  cos(x)**2 (its exponent included) and cos(x), not the sum; 2x typed
   *  into 3x colours the 2 and the 3.  A kept node inside a coloured one
   *  is drawn in the normal colour.  `map` gives, for every old path that
   *  is kept or aligned, the corresponding new path (a term that moved is
   *  found again). */
  function diffNodes(oldNodes, newNodes) {
    var ot = buildTree(oldNodes), nt = buildTree(newNodes);
    var oldKept = {}, newKept = {}, map = {};
    Object.keys(oldNodes).forEach(function (p) { oldKept[p] = false; });
    Object.keys(newNodes).forEach(function (p) { newKept[p] = false; });
    // two identical expressions: the same structure, node for node
    var keepPair = function (op, np) {
      oldKept[op] = true; newKept[np] = true; map[op] = np;
      var oc = ot[op].children, nc = nt[np].children;
      for (var i = 0; i < oc.length; i++) {
        if (i < nc.length) keepPair(oc[i], nc[i]); else oldKept[oc[i]] = true;
      }
      for (var j = oc.length; j < nc.length; j++) newKept[nc[j]] = true;
    };
    var container = function (nodes, p) { return nodes[p].nargs > 0; };
    var align = function (op, np) {
      oldKept[op] = true;
      newKept[np] = true;
      map[op] = np;
      var oc = ot[op].children, nc = nt[np].children, used = {}, restOld = [];
      oc.forEach(function (c) {
        for (var k = 0; k < nc.length; k++) {
          if (!used[k] && newNodes[nc[k]].src === oldNodes[c].src) { used[k] = true; keepPair(c, nc[k]); return; }
        }
        restOld.push(c);
      });
      restOld.forEach(function (c) {
        if (!container(oldNodes, c)) return;
        for (var k = 0; k < nc.length; k++) {
          if (!used[k] && container(newNodes, nc[k]) && newNodes[nc[k]].type === oldNodes[c].type) { used[k] = true; align(c, nc[k]); return; }
        }
      });
    };
    if ("/" in oldNodes && "/" in newNodes) {
      if (oldNodes["/"].src === newNodes["/"].src) keepPair("/", "/");
      else if (oldNodes["/"].type === newNodes["/"].type && container(oldNodes, "/") && container(newNodes, "/")) align("/", "/");
    }
    return { oldKept: oldKept, newKept: newKept, map: map };
  }

  /** Where the selection goes after a change: what replaced the selected
   *  node (the result of the edit or transformation), the node itself
   *  where it was kept (a term SymPy moved is followed), else the nearest
   *  ancestor that survived.  Null when nothing was selected. */
  function selectionAfter(selected, oldNodes, newNodes) {
    if (!selected || !oldNodes || !newNodes) return null;
    var diff = diffNodes(oldNodes, newNodes);
    if (diff.oldKept[selected] && diff.map[selected]) return diff.map[selected];
    var nt = buildTree(newNodes);
    var addedTop = Object.keys(newNodes).filter(function (p) {
      if (diff.newKept[p]) return false;
      var q = nt[p].parent;
      return q === null || diff.newKept[q];
    });
    // the ancestor that survived, and what is new under it
    var anc = selected;
    while (anc !== null && !(diff.oldKept[anc] && diff.map[anc])) anc = parentPath(anc);
    var under = anc === null ? null : diff.map[anc];
    var mine = under === null ? addedTop : addedTop.filter(function (p) { return nt[p].parent === under; });
    if (!mine.length) mine = addedTop;
    if (mine.length === 1) return mine[0];
    if (mine.length > 1) {                    // several new pieces: what holds them all
      var common = mine[0];
      while (common !== null && !mine.every(function (p) { return isAncestorOrSelf(common, p); })) common = nt[common].parent;
      return common !== null ? common : mine[0];
    }
    return under;
  }

  // The navigation arrows as one drawing, rotated.  As text glyphs they come
  // from whichever installed font happens to have them, which differs per
  // platform: in most UI fonts the horizontal pair is twice as wide as the
  // vertical one, and a fallback font gives them another weight and baseline
  // as well - the four buttons then look like two different sets.
  function arrowSvg(dir) {
    var deg = { up: 0, right: 90, down: 180, left: 270 }[dir];
    return '<svg class="se-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
      '<g transform="rotate(' + deg + ' 8 8)" fill="none" stroke="currentColor" stroke-width="1.7" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M8 13.2V3.2"/><path d="M3.9 7.3 8 3.2l4.1 4.1"/></g></svg>';
  }

  /** The full-screen icon: four corner brackets pointing out (or, once the
   *  formula fills the window, in).  Drawn like the arrows, so it matches
   *  them on every platform. */
  function expandSvg(full) {
    var out = "M2.8 6.2V2.8h3.4M9.8 2.8h3.4v3.4M13.2 9.8v3.4H9.8M6.2 13.2H2.8V9.8";
    var back = "M6.2 2.8v3.4H2.8M13.2 6.2H9.8V2.8M9.8 13.2V9.8h3.4M2.8 9.8h3.4v3.4";
    return '<svg class="se-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
      '<path fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
      'stroke-linejoin="round" d="' + (full ? back : out) + '"/></svg>';
  }

  /** Give `boxCls` to the marked elements (`cls`) that have no marked
   *  ancestor: one tinted box per changed region, over the node's whole
   *  visual extent, instead of an inline background per level (which paints
   *  the line box only - a fraction or matrix is then half covered). */
  function markBoxes(root, cls, boxCls) {
    var marked = root.querySelectorAll("." + cls);
    for (var i = 0; i < marked.length; i++) {
      var parent = marked[i].parentNode;
      if (!parent || !parent.closest || !parent.closest("." + cls)) marked[i].classList.add(boxCls);
    }
  }

  // A box whose content is an expression, a name or a number - never prose.
  // On a touch keyboard that means no shifted first letter, no autocorrect,
  // no prediction and no spelling underline.
  function noAutoCaps(el) {
    if (!el.hasAttribute("autocapitalize")) el.setAttribute("autocapitalize", "off");
    if (!el.hasAttribute("autocorrect")) el.setAttribute("autocorrect", "off");
    if (!el.hasAttribute("autocomplete")) el.setAttribute("autocomplete", "off");
    if (!el.hasAttribute("spellcheck")) el.setAttribute("spellcheck", "false");
    return el;
  }

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
      }
    }
    // What is typed here is code, never prose: a touch keyboard must not come
    // up shifted (x, not X), nor autocorrect or predict.  Every text box in
    // the editor gets this unless it asks for something else.
    if (tag === "input" && (!attrs || !attrs.type || attrs.type === "text")) noAutoCaps(el);
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
      // The tools sit in their own strip: on a narrow screen it scrolls
      // sideways instead of wrapping onto several rows.
      this.tools = h("div", { class: "se-tools" });
      this.toolbar.appendChild(this.tools);
      var btn = function (cmd, label, title) {
        var b = h("button", { type: "button", "data-cmd": cmd, title: title }, [label]);
        self.tools.appendChild(b);
        self.buttons[cmd] = b;
        return b;
      };
      var sep = function () { self.tools.appendChild(h("span", { class: "se-sep" })); };
      // The arrows are drawn, not typed: one icon rotated four ways (arrowSvg).
      var arrowBtn = function (cmd, dir, title) {
        var b = btn(cmd, "", title);
        b.innerHTML = arrowSvg(dir);
        b.setAttribute("aria-label", title);
        return b;
      };
      var brk = function () { self.tools.appendChild(h("span", { class: "se-break" })); };

      // The tools come in three rows of related blocks (a `sep` divides the
      // blocks of a row, a `brk` starts the next row):
      //   1. the session and its timeline - sessions drawer, undo/redo,
      //      history view, Done - and the zoom;
      //   2. the selection - navigation, then what to do with it;
      //   3. what to apply - the transform menus and the function box.
      if (o.sessions && !o.readOnly) btn("drawer", "\u2630", "Sessions and history");
      if (!o.readOnly) {
        btn("undo", "↶", "Undo (Ctrl+Z)");
        btn("redo", "↷", "Redo (Ctrl+Shift+Z, Ctrl+Y)");
        sep();
        btn("history", "History", "View the history of this session: every step, what changed and what produced it - and save it as a web page or a Python script");
        btn("help", "?", "How to use the editor: every gesture, key and tool");
        if (o.finishButton) btn("finish", "Done", "Finish editing and hand the expression back to Python");
        sep();
      }
      // Zoom: the formula's size (also Ctrl+wheel, Ctrl+plus/minus/0, pinch);
      // the three buttons form one block that wraps as a unit.
      var zoomBlock = h("span", { class: "se-zoom" });
      this.tools.appendChild(zoomBlock);
      var zoomBtn = function (cmd, label, title) { var b = btn(cmd, label, title); zoomBlock.appendChild(b); return b; };
      zoomBtn("zoomout", "\u2212", "Zoom out (Ctrl+minus, Ctrl+wheel, pinch)");
      zoomBtn("zoomreset", "100%", "Reset the zoom (Ctrl+0)");
      zoomBtn("zoomin", "+", "Zoom in (Ctrl+plus, Ctrl+wheel, pinch)");
      if (!o.readOnly) {
        brk();
        arrowBtn("parent", "up", "Select the enclosing expression (↑)");
        arrowBtn("child", "down", "Select inside: the sub-expression you came from, or the first one; on an atom, a caret after it (↓)");
        arrowBtn("left", "left", "Select the previous sibling, or move the caret left (←)");
        arrowBtn("right", "right", "Select the next sibling, or move the caret right (→)");
        sep();
        btn("edit", "Edit", "Edit the selection in place (Enter, double-click, or just start typing)");
        btn("unwrap", "Unwrap", "Remove the selected node but keep its argument: cos(θ) → θ (Backspace)");
        btn("delete", "Delete", "Remove the selection entirely (Del)");
        btn("isolate", "Isolate", "Keep only the selection: it becomes the whole expression (Ctrl+Shift+I)");
        sep();
        btn("keyboard", "⌨", "Open the keyboard: edit the selection, insert at the caret, or edit the whole expression");
      }
      btn("copy", "Copy", "Copy the SymPy source of the selection, or of the whole expression (Ctrl+C / Ctrl+X / Ctrl+V work on selections and carets)");
      if (!o.readOnly) {
        btn("paste", "Paste", "Paste the clipboard over the selection, or at the caret (Ctrl+V)");
        brk();
        // General menu: picking an operation applies it to the selection (or
        // the whole expression) at once.
        this.opsSelect = h("select", { class: "se-ops", title: "Transform the selection (or the whole expression)" });
        this.tools.appendChild(this.opsSelect);
        // Type menu: the operations specific to the selection's type (Matrix,
        // Integral, Equation...); picking one applies it at once.
        this.typeMenu = h("select", { class: "se-typemenu", hidden: "", title: "Operations specific to the selected type" });
        this.tools.appendChild(this.typeMenu);
        // Methods menu: what the selected object's class can do (the root
        // expression when nothing is selected); picking one calls it, asking
        // for parameters first when it needs any.  Each snapshot carries the
        // lists of the types it introduces (see _fillMethods).
        this.methodsMenu = h("select", { class: "se-methods", hidden: "",
          title: "Methods of the selection's class (of the whole expression when nothing is selected): pick one to call it; a method with parameters asks for them" });
        this.tools.appendChild(this.methodsMenu);
        this._methodsCache = {};   // entries by type name, from the snapshots (Document piggybacks new types)
        sep();
        // Function box: search SymPy's functions; a picked function that needs
        // parameters asks for them (see _showFnForm).
        this.fnInput = h("input", { class: "se-fn", type: "text", placeholder: "SymPy function… (search)",
          title: "Apply any SymPy function or method to the selection (or the whole expression): type to search, Enter to pick; functions with parameters ask for them",
          spellcheck: "false", autocomplete: "off" });
        this.tools.appendChild(this.fnInput);
        // Unevaluated: a transformation or a function builds its symbolic
        // form (Determinant(M), Derivative(f, x)...) instead of computing.
        this.lazyBox = h("input", { type: "checkbox", class: "se-lazy-box" });
        this.lazyBox.checked = !!this.opts.unevaluated;
        var lazyLabel = h("label", { class: "se-lazy", title: "Keep the result unevaluated: the Determinant, Integral, Derivative, sin(0)... is built, not computed (Evaluate applies it later); a transformation without such a form is applied as usual" },
          [this.lazyBox, "unevaluated"]);
        this.lazyBox.addEventListener("change", function () {
          self._setStatus(self.lazyBox.checked ? "Unevaluated: transformations and functions build their symbolic form (Determinant, Integral...) - Evaluate computes it later"
                                               : "Transformations and functions compute their result");
        });
        this.tools.appendChild(lazyLabel);
        this.fnMenu = h("div", { class: "se-fn-menu", hidden: "", role: "listbox" });
        this.fnForm = h("div", { class: "se-fn-form", hidden: "" });
        this._fnNames = [];
        this._fnSigs = {};
        this._fnActive = -1;
      }
      this.status = h("span", { class: "se-status", "aria-live": "polite" });
      this.toolbar.appendChild(this.status);
      if (o.toolbar) root.appendChild(this.toolbar);

      this.view = h("div", {
        class: "se-view", tabindex: "0", role: "application",
        "aria-label": "SymPy expression; click to select a sub-expression"
      });
      // The view sits on a stage: a wide formula scrolls inside the view, so
      // anything that must stay pinned to the corner of the editing area (the
      // full-screen button) lives beside it, not in it.
      this.stage = h("div", { class: "se-stage" }, [this.view]);
      root.appendChild(this.stage);
      this.fullBtn = h("button", { type: "button", class: "se-fullbtn",
        title: "Full screen: the formula alone, as large as the window (F, or Esc to come back)",
        "aria-label": "Full screen" });
      this.fullBtn.innerHTML = expandSvg(false);
      this.fullBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        self.setFullscreen(!self.fullscreen);
        self.view.focus({ preventScroll: true });
      });
      this.stage.appendChild(this.fullBtn);
      this.fullscreen = false;
      this.zoom = 1;
      this._applyZoom(this._initialZoom());

      // The SymPy source line: editable text (Enter applies, Esc reverts) whose
      // selection is linked to the rendering both ways.  The rendering itself is
      // never replaced by code: whole-expression edits happen here.
      this.source = noAutoCaps(h("code", { class: "se-source",
        title: o.readOnly ? "SymPy source" : "SymPy source: select to select in the formula; edit, then Enter to apply (Esc reverts)" }));
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

      // Sessions and history live in a lateral drawer (the ☰ button), out of
      // the widget: several expressions, each with its own undo history, kept
      // in localStorage (needs a backend with openDocument, i.e. Pyodide).
      this.sessions = null;
      this.drawer = null;
      if (o.sessions && !o.readOnly) {
        var close = h("button", { type: "button", class: "se-drawer-close", title: "Close" }, ["\u2715"]);
        close.addEventListener("click", function () { self.closeDrawer(); });
        this.sessionsBody = h("div", { class: "se-sessions" });
        this.historyBody = h("div", { class: "se-history" });
        // The history belongs to a session: it is a sub-tab inside the card
        // of the current session (see _fillSessions), under the session list.
        this.subtabs = h("div", { class: "se-subtabs", role: "tablist" }, [
          h("button", { type: "button", role: "tab", "data-tab": "history", class: "se-subtab" }, ["History"])
        ]);
        this.subtabs.addEventListener("click", function (ev) {
          var tab = ev.target.closest("[data-tab]");
          if (tab) self.showDrawerTab(tab.classList.contains("se-subtab-current") ? "sessions" : tab.getAttribute("data-tab"));
        });
        this.historyPane = h("div", { class: "se-drawer-pane", "data-pane": "history", hidden: "" }, [this.historyBody]);
        this.drawer = h("aside", { class: "se-drawer", hidden: "", role: "dialog", "aria-label": "Sessions" }, [
          h("div", { class: "se-drawer-head" }, [h("strong", {}, ["Sessions"]), close]),
          this.sessionsBody
        ]);
        this.backdrop = h("div", { class: "se-backdrop", hidden: "" });
        this.backdrop.addEventListener("click", function () { self.closeDrawer(); });
        this.sessions = this.drawer;
        root.appendChild(this.backdrop);
        root.appendChild(this.drawer);
      }
      this._sessionsReady = false;
      this._history = null;   // {labels, index} of the current session, from the last export

      this.error = h("div", { class: "se-error", role: "alert", hidden: "" });
      root.appendChild(this.error);
      // Floating action bar under the selection: the same commands as the
      // toolbar, one click or tap away from the object they act on.
      this.actions = null;
      if (!o.readOnly) {
        var abtn = function (cmd, label, title) { return h("button", { type: "button", "data-cmd": cmd, title: title }, [label]); };
        var aArrow = function (cmd, dir, title) {
          var b = abtn(cmd, "", title);
          b.innerHTML = arrowSvg(dir);
          b.setAttribute("aria-label", title);
          return b;
        };
        this.actions = h("div", { class: "se-actions", hidden: "", role: "toolbar" }, [
          aArrow("left", "left", "Select the previous sibling"),
          aArrow("right", "right", "Select the next sibling"),
          aArrow("parent", "up", "Select the enclosing expression"),
          aArrow("child", "down", "Select inside (the sub-expression you came from, or the first one)"),
          abtn("edit", "Edit", "Edit in place"),
          abtn("unwrap", "Unwrap", "Remove this node but keep its argument: cos(θ) → θ"),
          abtn("delete", "Delete", "Remove entirely"),
          abtn("isolate", "Isolate", "Keep only this: it becomes the whole expression"),
          abtn("copy", "Copy", "Copy the SymPy source of the selection (Ctrl+C; Ctrl+X cuts, Ctrl+V pastes)"),
          abtn("paste", "Paste", "Paste the clipboard over the selection (Ctrl+V)")
        ]);
        root.appendChild(this.actions);
        // The palette shown under a selected operator: what it can become.
        var obtn = function (op, label, title) { return h("button", { type: "button", "data-op": op, title: title }, [label]); };
        this.opBar = h("div", { class: "se-opbar", hidden: "", role: "toolbar", "aria-label": "Operator" }, [
          obtn("+", "+", "Add (+)"), obtn("-", "\u2212", "Subtract (-)"), obtn("*", "\u00d7", "Multiply (*)"),
          obtn("/", "\u00f7", "Divide (/)"), obtn("^", "^", "Power (^)"), obtn("=", "=", "Equation (=)"),
          obtn("", "Delete", "Remove the operator: side by side, the two multiply (Del)")
        ]);
        this.opBar.addEventListener("click", function (ev) {
          var b = ev.target.closest("button[data-op]");
          if (!b || !self.junction) return;
          self.setOperator(b.getAttribute("data-op"));
          self.view.focus({ preventScroll: true });
        });
        root.appendChild(this.opBar);
        // Asked before unwrapping a node that has more than one argument to
        // choose from: x**2 can leave the base or the exponent (see _askKeep).
        this.keepMenu = h("div", { class: "se-keep", hidden: "", role: "toolbar",
                                   "aria-label": "Which part to keep" });
        this.keepMenu.addEventListener("keydown", function (ev) {
          var buttons = self.keepMenu.querySelectorAll("button");
          var at = Array.prototype.indexOf.call(buttons, document.activeElement);
          if (ev.key === "Escape") {
            ev.preventDefault(); ev.stopPropagation();
            self._hideKeep(); self.view.focus({ preventScroll: true });
          } else if ((ev.key === "Enter" || ev.key === " ") && document.activeElement && document.activeElement.tagName === "BUTTON") {
            ev.stopPropagation();   // the button's own click handler applies it
          } else if ((ev.key === "ArrowRight" || ev.key === "ArrowLeft") && buttons.length) {
            ev.preventDefault(); ev.stopPropagation();
            var next = (at + (ev.key === "ArrowRight" ? 1 : buttons.length - 1) + buttons.length) % buttons.length;
            buttons[next].focus({ preventScroll: true });
          }
        });
        root.appendChild(this.keepMenu);
      }

      this.input = null;  // the in-place <input> while editing
      // Insertion caret: a point between two arguments of an insertable node
      // (see _gapsOf); typing there inserts a new argument.
      this.caretEl = h("span", { class: "se-caret", "aria-hidden": "true" });
      this.caret = null;      // {path, index, a, b, leftEl, rightEl, top, bottom, height}
      // A selected operator glyph: {path, left, right, el, text} - the node
      // whose arguments `left` and `right` it joins (see _operatorAt).
      this.junction = null;
      this.inserting = null;  // the caret an open field is inserting at
      this._gapCache = null;
      // Range selection: adjacent arguments of a rangeable node (Add, Mul...),
      // as indices into that node's display-ordered children (see _setRange).
      this.range = null;      // {parent, anchor, focus} or null
      this._editRange = null; // {path, children} while a range is being edited
      this._drag = null;      // pointer drag in progress: {anchor, moved}
      this._pointers = {};    // pointers currently down (id -> {x, y}), for pinching
      this._pinch = null;     // {dist, zoom} while two pointers are down
      this._pan = null;       // {x, left, moved} while a drag scrolls the view sideways
      this._suppressClick = false;
      this._pointerType = "mouse";   // of the last pointerdown: touch gets tap-to-edit
      this._boxes = { hover: [], select: [] };   // highlight overlays (see _visualRect)
      this._cameFrom = {};    // ancestor path -> the descendant ↑ was pressed on (for ↓)
      this._stateCount = 0;   // states applied so far (data-seq on the root)

      // Loading overlay: shown in front of everything while Python loads.
      this.loading = false;
      this.interruptBtn = h("button", { type: "button", class: "se-interrupt", hidden: "",
        title: "Stop the computation (the expression stays as it was)" }, ["Interrupt"]);
      this.interruptBtn.addEventListener("click", function () { self.interrupt(); });
      this.overlay = h("div", { class: "se-loading", hidden: "", role: "status", "aria-live": "polite" }, [
        h("div", { class: "se-spinner" }), h("div", { class: "se-loading-text" }, ["Loading…"]), this.interruptBtn
      ]);
      this.committed = null;   // the last snapshot that is not a preview (see _previewSource)
      if (this.fnMenu) { root.appendChild(this.fnMenu); root.appendChild(this.fnForm); }
      root.appendChild(this.overlay);
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
          // Back to the formula - unless the command put the focus in a field
          // (Delete on the whole expression edits in the source line: taking
          // the focus away would blur it and bring the expression back).
          var active = document.activeElement;
          if (cmd !== "edit" && cmd !== "keyboard" && active !== self.source && active !== self.input && active !== self.emptyField) self.view.focus({ preventScroll: true });
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
      // Zoom with Ctrl/Cmd + wheel (a trackpad pinch arrives the same way); a
      // plain wheel over a formula wider than the view scrolls it sideways
      // (the view never scrolls vertically) and reaches the page at the ends.
      this.view.addEventListener("wheel", function (ev) {
        var unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 100 : 1;
        if (ev.ctrlKey || ev.metaKey) {
          ev.preventDefault();
          self.setZoom(self.zoom * Math.exp(-ev.deltaY * unit * 0.002), ev.clientX);
          return;
        }
        if (self.view.scrollWidth <= self.view.clientWidth) return;
        var before = self.view.scrollLeft;
        self.view.scrollLeft = before + (ev.deltaX || ev.deltaY) * unit;
        if (self.view.scrollLeft !== before) ev.preventDefault();
      }, { passive: false });
      // Two fingers pinch the formula, not the page: the browser must be told
      // before it takes the gesture (one finger still scrolls the page
      // vertically, see touch-action in the CSS).
      this.view.addEventListener("touchstart", function (ev) { if (ev.touches.length >= 2) ev.preventDefault(); }, { passive: false });
      this.view.addEventListener("touchmove", function (ev) { if (self._pinch) ev.preventDefault(); }, { passive: false });
      this.view.addEventListener("mouseleave", function () { self._setHover(null); });
      this.view.addEventListener("click", function (ev) { self._onClick(ev); });
      // Dragging (mouse, touch or pen) over the formula selects a range.
      this.view.addEventListener("pointerdown", function (ev) {
        self._pointerType = ev.pointerType || "mouse";
        self._clearChangeMarks();
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        self._pointers[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
        if (Object.keys(self._pointers).length === 2) {   // a second finger: a pinch, no longer a drag
          self._drag = null;
          self._endPan();
          self._pinch = { dist: self._pointerSpread(), zoom: self.zoom };
          return;
        }
        var leaf = self._leafAt(ev);
        if (!leaf && self.view.scrollWidth > self.view.clientWidth) {
          // Empty space of a formula wider than the view: dragging scrolls it.
          self._drag = null;
          self._pan = { x: ev.clientX, left: self.view.scrollLeft, moved: false, id: ev.pointerId };
          if (ev.pointerType === "mouse" && self.view.setPointerCapture) {
            try { self.view.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
          }
          return;
        }
        self._drag = { anchor: leaf ? leaf.getAttribute("data-path") : null, moved: false };
      });
      this.view.addEventListener("pointermove", function (ev) {
        if (self._pointers[ev.pointerId]) self._pointers[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
        if (self._pinch) {
          if (Object.keys(self._pointers).length < 2) return;
          self.setZoom(self._pinch.zoom * self._pointerSpread() / self._pinch.dist, self._pointerCentre());
          ev.preventDefault();
          return;
        }
        if (self._pan) {
          if (ev.pointerType === "mouse" && ev.buttons === 0) { self._endPan(); return; }
          var dx = ev.clientX - self._pan.x;
          if (Math.abs(dx) > 3) { self._pan.moved = true; self.view.classList.add("se-panning"); }
          self.view.scrollLeft = self._pan.left - dx;
          if (self._pan.moved) ev.preventDefault();
          return;
        }
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
      var endPointer = function (ev, cancelled) {
        delete self._pointers[ev.pointerId];
        if (self._pinch && Object.keys(self._pointers).length < 2) {
          self._pinch = null;
          self._pointers = {};              // the finger left behind must not start anything
          self._suppressClick = true;
        }
        if (self._pan) { if (self._pan.moved && !cancelled) self._suppressClick = true; self._endPan(); }
        if (self._drag && self._drag.moved && !cancelled) self._suppressClick = true;
        self._drag = null;
      };
      this.view.addEventListener("pointerup", function (ev) { endPointer(ev, false); });
      this.view.addEventListener("pointercancel", function (ev) { endPointer(ev, true); });
      this.view.addEventListener("dblclick", function (ev) { self._onDblClick(ev); });
      this.root.addEventListener("keydown", function (ev) {
        if (self.drawer && self.drawer.contains(ev.target)) return;   // Esc is handled at the document level while it is open
        if (self.symbols && self.symbols.contains(ev.target)) return;
        if (ev.target === self.source || ev.target === self.fnInput || (self.fnForm && self.fnForm.contains(ev.target))) return;
        if (self.loading) { ev.preventDefault(); return; }
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
        self._schedulePreview();
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
      // Listeners on the document (removed by destroy(): a notebook creates
      // and disposes of many editors, and each would otherwise stay alive).
      this._docListeners = [];
      var onDocument = function (kind, fn) { document.addEventListener(kind, fn); self._docListeners.push([kind, fn]); };
      onDocument("selectionchange", function () { self._onSourceSelection(); });
      // Copy / cut / paste while the formula has the focus (no clipboard permission needed).
      ["copy", "cut", "paste"].forEach(function (kind) {
        onDocument(kind, function (ev) { self._onClipboard(ev, kind); });
      });
      var applyFromMenu = function (menu) {
        var op = menu.value;
        menu.selectedIndex = 0;
        if (!op) return;
        var path = self.range ? self.range.parent : (self.selected || "/");
        var spec = (self.state.ops || []).filter(function (o) { return o.name === op; })[0];
        if (spec && spec.params && spec.params.length) return self._askOpParams(spec, path, menu);
        var msg = { action: "apply", path: path, op: op };
        if (self.lazy()) msg.lazy = true;
        if (self.range) msg.children = self._rangeIndices();
        self.send(msg);
        self.view.focus({ preventScroll: true });
      };
      if (this.fnInput) {
        this.fnInput.addEventListener("focus", function () { self._loadFunctions(); self._filterFn(); });
        this.fnInput.addEventListener("input", function () { self._filterFn(); });
        this.fnInput.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== self.fnInput) self._hideFnMenu(); }, 150); });
        this.fnInput.addEventListener("keydown", function (ev) {
          ev.stopPropagation();
          var items = self.fnMenu.querySelectorAll(".se-fn-item");
          if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
            ev.preventDefault();
            if (!items.length) return;
            self._fnActive = (self._fnActive + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
            self._highlightFn();
          } else if (ev.key === "Enter") {
            ev.preventDefault();
            var text = self.fnInput.value.trim();
            if (/\(/.test(text)) { self._hideFnMenu(); self.callFunction(text); return; }   // typed with arguments: as is
            var pick = self._fnActive >= 0 && items[self._fnActive] ? items[self._fnActive].getAttribute("data-name") : text;
            if (pick) self._pickFn(pick);
          } else if (ev.key === "Escape") {
            ev.preventDefault();
            self._hideFnMenu();
            self._hideFnForm();
            self.fnInput.value = "";
            self.view.focus({ preventScroll: true });
          }
        });
        this.fnMenu.addEventListener("mousedown", function (ev) { ev.preventDefault(); });   // keep the focus in the box
        this.fnMenu.addEventListener("click", function (ev) {
          var item = ev.target.closest(".se-fn-item");
          if (item) self._pickFn(item.getAttribute("data-name"));
        });
      }
      [this.opsSelect, this.typeMenu].forEach(function (menu) {
        if (!menu) return;
        menu.addEventListener("change", function () { applyFromMenu(menu); });
        menu.addEventListener("keydown", function (ev) { ev.stopPropagation(); });
      });
      if (this.methodsMenu) {
        this.methodsMenu.addEventListener("change", function () {
          var name = self.methodsMenu.value;
          self.methodsMenu.selectedIndex = 0;
          if (!name) return;
          delete self._fnSigs["." + name];   // a method's signature depends on the type: never reuse another's
          self._pickFn("." + name);
        });
        this.methodsMenu.addEventListener("keydown", function (ev) { ev.stopPropagation(); });
      }
    }

    /* ---- state ---- */

    /** Apply a snapshot from the backend. */
    async setState(snap) {
      if (!snap) return;
      if (snap.export) { this._storeSession(snap); return; }   // the answer to a save, not a new state
      if (snap.preview) {
        // The source line being typed: a string that does not parse leaves
        // the rendering as it is and only marks the line.
        if (snap.error) { this.source.classList.add("se-invalid"); this._setStatus(snap.error); return; }
        this.source.classList.remove("se-invalid");
      } else {
        this.committed = snap;
        if (this._sessionsReady && !snap.error) this._scheduleSessionSave();
        this._endEmptyInput();
      }
      var same = snap === this.state;   // re-render of the current state (keeps the range)
      var previous = this.state && !this.state.preview ? this.state : this.committed;
      this.state = snap;
      this._hideKeep();
      this.tree = buildTree(snap.nodes || {});
      if (!same) { this.range = null; this._cameFrom = {}; }
      // An open field is dropped without cancelEdit(): that would re-render
      // on its own (a second, re-entrant setState) - the render below is enough.
      if (this.editing !== null || this.inserting) this._endEdit();
      await this._render();
      if (this.state !== snap) return;  // superseded meanwhile
      var sel = this.selected;
      if (sel && !same && !snap.preview && previous && previous.nodes && previous !== snap) {
        // a change: paths move with SymPy's reordering, so follow the node, not its path
        sel = selectionAfter(sel, previous.nodes, snap.nodes || {});
      }
      while (sel && !(sel in this.tree)) sel = parentPath(sel);
      this.selected = sel;
      if (snap.methods) {
        for (var mt in snap.methods) this._methodsCache[mt] = snap.methods[mt] || [];
      }
      this._fillOps();
      this._fillSymbols();
      if (snap.functions && this.fnInput && !this._functionsLoaded) {
        this._functionsLoaded = true;
        this._fnNames = snap.functions;
        this._fnSigs = snap.signatures || {};
        if (document.activeElement === this.fnInput) this._filterFn();
      }
      if (snap.signature && this.fnInput) {
        this._fnSigs[snap.signature.name] = snap.signature;
        // After the request that brought it has settled: a function without
        // parameters is applied at once, which is a send of its own.
        var self = this, sig = snap.signature;
        setTimeout(function () { self._showFnForm(sig); }, 0);
      }
      this.root.setAttribute("data-seq", String(++this._stateCount));   // lets tests wait for a re-render
      this._applySelection();
      this._showError(snap.error);
      if (snap.note && !snap.error) this._setStatus(snap.note);   // e.g. a name read as SymPy's function
      else if (snap.preview) this._setStatus("Previewing the edited source – Enter applies it, Esc reverts");
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
      var before = this._captureRendering();   // for the change animation (null when there is nothing to animate)
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
      this.view.classList.remove("se-empty");
      if (this.emptyField) {               // the field of the empty view survives the preview rendered above it
        var field = this.emptyField, pos = field.selectionStart, focused = document.activeElement === field || !field.parentNode;
        this.view.appendChild(field);
        this.view.classList.add("se-typing");
        if (focused) { field.focus({ preventScroll: true }); try { field.setSelectionRange(pos, pos); } catch (e) { /* ignore */ } }
      }
      if (!this.state.preview) {           // a preview leaves the line being typed alone
        this.source.textContent = this.state.src || "";
        this.sourceDirty = false;
        this.source.classList.remove("se-dirty");
      }
      this._gapCache = null;
      this.caret = null;   // the rendering replaced the caret element and the boxes too
      this._boxes = { hover: [], select: [] };
      this._hoverEl = null;
      if (before) this._animateChange(before);
    }

    /* ---- change animation ---- */

    /** What is on screen before a re-render, when the coming state is a
     *  committed change of the expression: the old rendering (cloned) and
     *  the box of every node. */
    _captureRendering() {
      var prev = this._shown;      // {snap, nodes} of the rendering on screen
      this._shown = { snap: this.state, nodes: this.state.nodes || {} };
      if (!this.opts.animate || !prev || !prev.snap || !this.annotated ||
          (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)) {
        this._committedCapture = null;
        return null;
      }
      var self = this;
      var capture = function () {
        var disp = self.view.querySelector(".katex-display") || self.view.querySelector(".katex");
        if (!disp) return null;
        var rects = {};
        var els = disp.querySelectorAll("[data-path]");
        for (var i = 0; i < els.length; i++) {
          var p = els[i].getAttribute("data-path");
          if (!(p in rects)) rects[p] = self._visualRect(els[i]);
        }
        var vr = self.view.getBoundingClientRect(), dr = disp.getBoundingClientRect();
        return { clone: disp.cloneNode(true), rects: rects, nodes: prev.nodes, srepr: prev.snap.srepr,
                 left: dr.left - vr.left + self.view.scrollLeft, top: dr.top - vr.top + self.view.scrollTop, width: dr.width };
      };
      if (this.state.preview) {
        // Previews are not animated, but the committed rendering the first
        // one replaces is kept: the commit animates from it.
        if (!prev.snap.preview) this._committedCapture = capture();
        return null;
      }
      var before = prev.snap.preview ? this._committedCapture : capture();
      this._committedCapture = null;
      if (!before || before.srepr === this.state.srepr) return null;   // nothing changed (or a preview was reverted)
      return before;
    }

    /** Old parts that disappear (red) move to their replacements and fade
     *  out; new parts (green) fade in; kept parts slide from their old place
     *  to their new one.  Two ghosts animate over the real rendering, which
     *  stays in place (invisible, still clickable) and is revealed at the
     *  end with its new parts still green - until the formula is touched. */
    _animateChange(before) {
      var self = this;
      var disp = this.view.querySelector(".katex-display") || this.view.querySelector(".katex");
      if (!disp) return;
      var oldNodes = before.nodes, newNodes = this.state.nodes || {};
      var diff = diffNodes(oldNodes, newNodes);
      var oldKept = diff.oldKept, newKept = diff.newKept;
      var oldRectOf = function (path) {   // where the same expression was (same path first)
        var src = newNodes[path].src;
        if (path in oldNodes && oldNodes[path].src === src) return before.rects[path];
        for (var p in oldNodes) if (oldNodes[p].src === src && before.rects[p]) return before.rects[p];
        return null;
      };
      var topMost = function (paths, flags) {   // those without an ancestor in the same set
        return paths.filter(function (p) {
          var q = parentPath(p);
          while (q !== null) { if (flags[q]) return false; q = parentPath(q); }
          return true;
        });
      };
      var oldPaths = Object.keys(oldNodes), newPaths = Object.keys(newNodes);
      var removed = oldPaths.filter(function (p) { return !oldKept[p]; });
      var added = newPaths.filter(function (p) { return !newKept[p]; });
      if (!removed.length && !added.length) return;
      var flip = topMost(newPaths.filter(function (p) { return newKept[p]; }), newKept);
      var ms = this.opts.animateDuration, ease = "cubic-bezier(0.2, 0.7, 0.2, 1)";
      var vr = this.view.getBoundingClientRect(), dr = disp.getBoundingClientRect();
      var ghost = function (source, left, top, width) {
        var g = source.cloneNode(true);
        g.classList.add("se-ghost");
        g.style.left = Math.round(left) + "px";
        g.style.top = Math.round(top) + "px";
        g.style.width = Math.round(width) + "px";
        var all = g.querySelectorAll("*");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.remove("se-selected", "se-hover", "se-editing", "se-added", "se-added-box");
          if (all[i].hasAttribute("data-path")) { all[i].setAttribute("data-ghost", all[i].getAttribute("data-path")); all[i].removeAttribute("data-path"); }
          if (all[i].classList.contains("se-inline")) all[i].parentNode.removeChild(all[i]);
        }
        return g;
      };
      var byGhost = function (g, path) { return g.querySelector('[data-ghost="' + ((window.CSS && CSS.escape) ? CSS.escape(path) : path) + '"]'); };
      var animations = [];
      var run = function (el, frames) { try { animations.push(el.animate(frames, { duration: ms, easing: ease, fill: "forwards" })); } catch (e) { /* no Web Animations */ } };
      // the old ghost: only its removed parts are visible (red), moving to their replacements
      var oldGhost = ghost(before.clone, before.left, before.top, before.width);
      oldGhost.classList.add("se-ghost-old");
      oldPaths.forEach(function (p) { var el = byGhost(oldGhost, p); if (el) el.classList.add(oldKept[p] ? "se-kept" : "se-removed"); });
      this.view.appendChild(oldGhost);
      var removedTop = removed.filter(function (p) { var q = parentPath(p); while (q !== null) { if (!oldKept[q]) return false; q = parentPath(q); } return true; });
      removedTop.forEach(function (p) {
        var el = byGhost(oldGhost, p);
        if (!el) return;
        var from = before.rects[p];
        var to = null;
        if (p in newNodes) { var real = self._els(p)[0]; if (real) to = self._visualRect(real); }
        var dx = to && from ? (to.left + to.width / 2) - (from.left + from.width / 2) : 0;
        var dy = to && from ? (to.top + to.height / 2) - (from.top + from.height / 2) : 0;
        run(el, [{ transform: "translate(0, 0)", opacity: 1, offset: 0 }, { transform: "translate(0, 0)", opacity: 1, offset: 0.25 },
                 { transform: "translate(" + dx + "px, " + dy + "px)", opacity: 0, offset: 1 }]);
      });
      // the new ghost: new parts fade in (green), kept parts slide from where they were
      var newGhost = ghost(disp, dr.left - vr.left + this.view.scrollLeft, dr.top - vr.top + this.view.scrollTop, dr.width);
      newGhost.classList.add("se-ghost-new");
      this.view.appendChild(newGhost);
      var addedTop = added.filter(function (p) { var q = parentPath(p); while (q !== null) { if (!newKept[q]) return false; q = parentPath(q); } return true; });
      newPaths.forEach(function (p) { var el = byGhost(newGhost, p); if (el && newKept[p]) el.classList.add("se-kept"); });
      addedTop.forEach(function (p) {
        var el = byGhost(newGhost, p);
        if (el) { el.classList.add("se-added"); run(el, [{ opacity: 0, offset: 0 }, { opacity: 0, offset: 0.45 }, { opacity: 1, offset: 1 }]); }
      });
      flip.forEach(function (p) {
        var el = byGhost(newGhost, p), was = oldRectOf(p), real = self._els(p)[0];
        if (!el || !was || !real) return;
        var now = self._visualRect(real);
        var dx = was.left - now.left, dy = was.top - now.top;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
        run(el, [{ transform: "translate(" + dx + "px, " + dy + "px)", offset: 0 }, { transform: "translate(" + dx + "px, " + dy + "px)", offset: 0.25 },
                 { transform: "translate(0, 0)", offset: 1 }]);
      });
      // meanwhile the real rendering is invisible (but in place, so clicks work); at the end it shows its new parts green
      disp.classList.add("se-changing");
      newPaths.forEach(function (p) { var els = self._els(p); for (var i = 0; i < els.length; i++) els[i].classList.add(newKept[p] ? "se-kept" : "se-added"); });
      // The outermost new nodes carry the tint, as one box each (se-added-box).
      addedTop.forEach(function (p) { var els = self._els(p); for (var i = 0; i < els.length; i++) els[i].classList.add("se-added-box"); });
      var done = false;
      var finish = function () {
        if (done) return;
        done = true;
        disp.classList.remove("se-changing");
        if (oldGhost.parentNode) oldGhost.parentNode.removeChild(oldGhost);
        if (newGhost.parentNode) newGhost.parentNode.removeChild(newGhost);
      };
      this._finishAnimation = finish;
      Promise.all(animations.map(function (a) { return a.finished.catch(function () {}); })).then(finish, finish);
      setTimeout(finish, ms + 200);
    }

    /** The green marks of the last change go when the formula is touched. */
    _clearChangeMarks() {
      if (this._finishAnimation) { this._finishAnimation(); this._finishAnimation = null; }
      var marked = this.view.querySelectorAll(".se-added, .se-added-box");
      for (var i = 0; i < marked.length; i++) marked[i].classList.remove("se-added", "se-added-box");
    }

    /** The general dropdown lists the ops that apply everywhere; the type
     *  menu lists those registered for the selection's kinds ("matrix",
     *  "integral"...), labelled with the most specific kind, and is hidden
     *  when there are none. */
    _fillOps() {
      if (!this.opsSelect || !this.state) return;
      var target = this.range ? this.range.parent : (this.selected || "/");
      var node = this.state.nodes ? this.state.nodes[target] : null;
      this._fillMethods(target, node);
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

    /** The methods menu: the public methods and properties of the class of
     *  the node at `target` (fetched from the backend once per type name and
     *  cached).  Picking one goes through the function box flow - signature,
     *  parameter form when needed, then the call. */
    _fillMethods(target, node) {
      if (!this.methodsMenu) return;
      var tname = node && !this.range ? node.type : null;
      var entries = tname ? this._methodsCache[tname] : null;
      if (!entries || !entries.length) { this.methodsMenu.hidden = true; this._methodsKey = null; return; }
      if (this._methodsKey !== tname) {
        this._methodsKey = tname;
        this.methodsMenu.textContent = "";
        this.methodsMenu.appendChild(h("option", { value: "", disabled: "", selected: "" }, ["Methods \u25BE"]));
        for (var i = 0; i < entries.length; i++) {
          var e = entries[i];
          this.methodsMenu.appendChild(h("option", { value: e.name, title: e.doc || "" },
            [e.label || (e.property ? "." + e.name : "." + e.name + "()")]));
        }
        this.methodsMenu.selectedIndex = 0;
      }
      this.methodsMenu.hidden = false;
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
      // Only what is actually drawn counts: KaTeX draws square roots and
      // stretchy symbols with a 400em-wide SVG clipped by an overflow-hidden
      // wrapper (.hide-tail), so a clipping element contributes its own box
      // and nothing inside it; struts and vlist spacers contribute nothing.
      var clips = [];
      var all = el.querySelectorAll("*");
      for (var i = 0; i < all.length; i++) {
        var node = all[i];
        var clipped = false;
        for (var c = 0; c < clips.length; c++) if (clips[c].contains(node)) { clipped = true; break; }
        if (clipped) continue;
        if (node.classList.contains("pstrut") || node.classList.contains("vlist-s")) continue;
        // vlist rows are zero-height positioning wrappers that start a strut
        // height above their content (the content itself is measured below).
        if (node.parentElement && node.parentElement.classList.contains("vlist")) continue;
        var b = node.getBoundingClientRect();
        if (!b.width || !b.height) continue;    // draws nothing
        if (node.classList.contains("hide-tail") || node.classList.contains("stretchy") || getComputedStyle(node).overflow !== "visible") clips.push(node);
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
      this._hideKeep();
      this.range = null;
      this.junction = null;
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
      var j = this.junction;
      if (j && (!j.el.isConnected || !this.view.contains(j.el))) j = this.junction = null;   // re-rendered: gone
      if (this.opBar) this.opBar.hidden = !j;
      if (j) {
        j.el.classList.add("se-selected");
        var jr = this._visualRect(j.el);
        this._drawBoxes("select", [jr]);
        var jn = this.state.nodes[j.path];
        this._setStatus("Operator " + j.text + " in " + jn.type + " " + jn.src
                        + " (type + - * / ^ = to change it; Delete removes it, the two then multiply)");
        this._markSource([]);
        this._placeActions(null);
        this._positionBar(this.opBar, jr);
        return;
      }
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
        this._setStatus(node.type + ": " + node.src);
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
      if (this.loading) return;
      if (this.closed || (this.input && ev.target === this.input)) return;
      if (this.view.classList.contains("se-empty")) { this.beginEmptyInput(); return; }   // everything was deleted: type here
      var leaf = this._leafAt(ev);
      this._gapCache = null;
      var junction = this.opts.readOnly ? null : this._operatorAt(ev);
      if (junction) {
        this.selectJunction(junction);
        this.lastLeaf = null;
        this.view.focus({ preventScroll: true });
        return;
      }
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
      if (this.helpView && k === "Escape") { ev.preventDefault(); this.closeHelp(); return; }
      var mod = ev.ctrlKey || ev.metaKey;
      var ro = this.opts.readOnly;
      var t = this.selected ? this.tree[this.selected] : null;
      var handled = true;
      if (!ro && !mod && !ev.altKey && k.length === 1 && this.view.classList.contains("se-empty")) {
        ev.preventDefault();
        this.beginEmptyInput(k);                                   // everything was deleted: type the new expression here
        return;
      }
      if (mod && (k === "z" || k === "Z")) {
        if (!ro) this.send({ action: ev.shiftKey ? "redo" : "undo" });
      } else if (mod && (k === "y" || k === "Y")) {
        if (!ro) this.send({ action: "redo" });
      } else if (mod && (k === "+" || k === "=" || k === "-" || k === "_" || k === "0")) {
        this.setZoom(k === "0" ? 1 : this.zoom * ((k === "-" || k === "_") ? 1 / ZOOM_STEP : ZOOM_STEP));
      } else if (mod && ev.shiftKey && (k === "i" || k === "I")) {
        this.isolateSelection();
      } else if (ev.shiftKey && (k === "ArrowLeft" || k === "ArrowRight") && !this.caret) {
        this._extendRange(k === "ArrowRight" ? 1 : -1);          // grow / shrink a range
      } else if (k === "Tab" && (this.selected || this.range) && !ro) {
        if (!this.caretAtSelection(ev.shiftKey)) handled = false;
      } else if (this.junction && k === "Escape") {
        this.select(null);
      } else if (this.junction && (k === "Delete" || k === "Backspace")) {
        if (!ro) this.setOperator("");
      } else if (this.junction && !mod && !ev.altKey && OPERATOR_KEYS.indexOf(k) >= 0) {
        if (!ro) this.setOperator(k);
      } else if (this.junction && k === "ArrowUp") {
        this.select(this.junction.path);
      } else if (this.junction && (k === "ArrowLeft" || k === "ArrowRight" || k === "ArrowDown")) {
        var jj = this.junction, jkids = this.tree[jj.path].children;
        this.select(jkids[k === "ArrowLeft" ? jj.leftIndex : jj.rightIndex]);
      } else if (this.junction && k === "Enter") {
        // nothing to edit in place: the palette (or a key) changes it
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
        this._selectBesideCaret();   // ↑ first selects the object the caret sits next to (then the ancestors)
      } else if (this.caret && k === "ArrowDown") {
        // nothing to go into from a caret
      } else if (this.caret && !ro && !mod && !ev.altKey && k.length === 1) {
        this.beginInsert(k);
      } else if (k === "Enter") {
        if (!ro) this.beginEdit(this.selected || "/");
      } else if (k === "Escape") {
        if (!this.selected && this.fullscreen) this.setFullscreen(false);
        else this.select(null);
      } else if ((k === "Backspace" || k === "Delete") && this.selected === "/" && !ro) {
        this.editSource("");                     // the whole expression: start over in the source line
      } else if (k === "Backspace") {
        if (!ro && this.selected) this.unwrapSelection();
      } else if (k === "Delete") {
        if (!ro && this.selected && this.selected !== "/") this.send({ action: "delete", path: this.selected });
      } else if (k === "ArrowUp") {
        if (this.selected) this._selectParent(this.selected);
      } else if (k === "ArrowDown") {
        this._selectChild();
      } else if (k === "ArrowLeft" || k === "ArrowRight") {
        if (this.selected) this._moveSideways(k === "ArrowLeft" ? -1 : 1);
        else this._caretAtEnd(k === "ArrowLeft" ? "start" : "end");
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
      // edited in the source line (started over with what was typed).
      if (path === "/" && this.editSource(initial)) return;
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

    /** The selection (node or range) becomes the whole expression. */
    isolateSelection() {
      if (this.opts.readOnly) return;
      if (this.range) return this.send({ action: "isolate", path: this.range.parent, children: this._rangeIndices() });
      if (this.selected && this.selected !== "/") return this.send({ action: "isolate", path: this.selected });
    }

    /** Drop the selected node, keeping the argument the user came up from
     *  (↑ from a child) or the natural one. */
    unwrapSelection() {
      if (!this.selected || this.opts.readOnly) return;
      var msg = { action: "unwrap", path: this.selected };
      var back = this._cameFrom[this.selected];
      var from = (back && isAncestorOrSelf(this.selected, back) && back !== this.selected)
        ? this._childKey(this.selected, back) : null;   // the child ↑ was pressed on
      var node = this.state && this.state.nodes ? this.state.nodes[this.selected] : null;
      var choices = (node && node.keep_choices) || [];
      // Several arguments could stand alone: ask which one to leave rather
      // than picking for the user.  The one ↑ came from starts focused, so
      // ↑, Backspace, Enter keeps it - and any other can be chosen instead.
      if (choices.length > 1) return this._askKeep(this.selected, choices, from);
      if (from !== null) msg.keep = from;
      this.send(msg);
    }

    _hideKeep() {
      if (this.keepMenu && !this.keepMenu.hidden) { this.keepMenu.hidden = true; this.keepMenu.textContent = ""; }
      this._keepAsked = null;
    }

    /** Ask which argument to leave behind: a node with several of them - a
     *  power (base or exponent), a sum, a fraction - has no natural one, so
     *  the user picks instead of the editor guessing.  Escape, or the ✕,
     *  leaves the expression alone. */
    _askKeep(path, choices, focused) {
      var self = this;
      if (!this.keepMenu) return this.send({ action: "unwrap", path: path, keep: focused === null ? undefined : focused });
      this._keepAsked = path;
      this.keepMenu.textContent = "";
      this.keepMenu.appendChild(h("span", { class: "se-keep-label" }, ["Keep"]));
      choices.forEach(function (choice) {
        var label = choice.src.length > 18 ? choice.src.slice(0, 17) + "…" : choice.src;
        var b = h("button", { type: "button", title: "Unwrap, leaving " + choice.src }, [label]);
        b.addEventListener("click", function () {
          self._hideKeep();
          self.send({ action: "unwrap", path: path, keep: choice.key });
          self.view.focus({ preventScroll: true });
        });
        self.keepMenu.appendChild(b);
      });
      var cancel = h("button", { type: "button", class: "se-keep-cancel", title: "Keep the expression as it is (Escape)" }, ["\u2715"]);
      cancel.addEventListener("click", function () { self._hideKeep(); self.view.focus({ preventScroll: true }); });
      this.keepMenu.appendChild(cancel);
      this.keepMenu.hidden = false;
      this._placeKeep(path);
      var buttons = this.keepMenu.querySelectorAll("button");
      var at = 0;
      if (focused !== null && focused !== undefined) {
        for (var i = 0; i < choices.length; i++) if (String(choices[i].key) === String(focused)) at = i;
      }
      if (buttons[at]) buttons[at].focus({ preventScroll: true });
    }

    /** The chooser above the selection - the action bar is under it. */
    _placeKeep(path) {
      var el = this._els(path)[0];
      if (!el || !this.keepMenu) { this._hideKeep(); return; }
      var rect = el.getBoundingClientRect(), rr = this.root.getBoundingClientRect();
      var left = rect.left - rr.left;
      var maxLeft = Math.max(0, this.root.clientWidth - this.keepMenu.offsetWidth - 4);
      var top = rect.top - rr.top - this.keepMenu.offsetHeight - 6;
      if (top < 0) top = rect.bottom - rr.top + 6;   // no room above: under it, over the action bar
      this.keepMenu.style.left = Math.round(Math.max(0, Math.min(left, maxLeft))) + "px";
      this.keepMenu.style.top = Math.round(top) + "px";
    }

    /** Show the floating action bar under a viewport rectangle (null hides it). */
    _placeActions(rect) {
      if (!this.actions) return;
      if (!rect || this.input || this.closed) { this.actions.hidden = true; this.view.style.paddingBottom = ""; return; }
      var t = this.selected ? this.tree[this.selected] : null;
      var selNode = this.selected && !this.range ? this.state.nodes[this.selected] : null;
      var unwrapOk = !!(selNode && (selNode.nargs || selNode.parts));
      var buttons = this.actions.querySelectorAll("button");
      for (var i = 0; i < buttons.length; i++) {
        var cmd = buttons[i].getAttribute("data-cmd");
        buttons[i].disabled = cmd === "parent" ? !(this.range || (t && t.parent))
                            : cmd === "child" ? false
                            : cmd === "paste" ? false
                            : cmd === "unwrap" ? !unwrapOk
                            : cmd === "delete" ? !(this.range || this.selected)
                            : cmd === "isolate" ? !(this.range || (this.selected && this.selected !== "/"))
                            : false;
      }
      this.actions.hidden = false;
      this._positionBar(this.actions, rect);
    }

    /** Place a floating bar under `rect` (a selection) - under the formula's
     *  line rather than right under the selection, so the bar never covers
     *  what is below it (the denominator under a selected numerator) - unless
     *  the formula goes on much further down. */
    _positionBar(bar, rect) {
      bar.hidden = false;
      var bottom = rect.bottom;
      var formula = this.view.querySelector(".katex");
      if (formula) {
        var fb = formula.getBoundingClientRect().bottom;
        if (fb > bottom && fb - bottom < 160) bottom = fb;
      }
      var rr = this.root.getBoundingClientRect();
      var left = rect.left - rr.left, top = bottom - rr.top + 6;
      var maxLeft = Math.max(0, this.root.clientWidth - bar.offsetWidth - 4);
      bar.style.left = Math.round(Math.max(0, Math.min(left, maxLeft))) + "px";
      bar.style.top = Math.round(top) + "px";
      // Keep the bar inside the formula area so it never covers the source line
      // (measured against the view's own padding, not the room added before).
      this.view.style.paddingBottom = "";
      var vr = this.view.getBoundingClientRect();
      var overflow = (bottom + 6 + bar.offsetHeight + 4) - vr.bottom;
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
        if (!text.trim()) return;
        ev.preventDefault();
        this._pasteText(text);
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
        else this.editSource("");                // cutting everything: start over in the source line
      } else {
        this._setStatus("Copied: " + src);
      }
    }

    /** Progress from a backend: loading messages block the UI behind an overlay. */
    _report(text) {
      if (text && /loading|waiting/i.test(text)) this._showLoading(text);
      else if (!text) this._hideLoading();
      else this._setStatus(text);
    }

    _showLoading(text) {
      this.loading = true;
      this.overlay.querySelector(".se-loading-text").textContent = text || "Loading…";
      this.overlay.hidden = false;
      if (this.root.contains(document.activeElement) && document.activeElement !== document.body) document.activeElement.blur();
      this._setStatus(text || "");
    }

    _hideLoading() {
      if (!this.loading) return;
      this.loading = false;
      this.overlay.hidden = true;
      this._applySelection();
    }

    /** Paste `text` where the selection is: spliced at a caret like typing,
     *  over a range or a node - or, with the whole expression selected, as
     *  the new expression at once (typing there goes to the source line for
     *  editing; a paste is complete and applies). */
    _pasteText(text) {
      text = (text || "").trim();
      if (!text) return;
      if (this.caret) { this.beginInsert(text); this.commitEdit(); }
      else if (this.range) { this.beginRangeEdit(text); this.commitEdit(); }
      else if (this.selected === "/") this.send({ action: "set", src: toSource(text) });
      else if (this.selected) { this.beginEdit(this.selected, text); this.commitEdit(); }
      else this._setStatus("Select where to paste (a node, or a caret between terms)");
    }

    /** The Paste button: the system clipboard when readable, else the last copy made here. */
    pasteClipboard() {
      var self = this;
      var apply = function (text) {
        if (!(text || "").trim()) { self._setStatus("Nothing to paste (copy something first, or use Ctrl+V)"); return; }
        self._pasteText(text);
      };
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText().then(apply, function () { apply(self._clip); });
      } else {
        apply(this._clip);
      }
    }

    /** Ask the backend for SymPy's function names once (for the box's autocompletion). */
    _loadFunctions() {
      if (this._functionsLoaded || this._functionsRequested || !this.backend) return;
      this._functionsRequested = true;
      var self = this;
      Promise.resolve(this.backend.send({ action: "functions" }, function (text) { self._report(text); })).then(function (snap) {
        self._hideLoading();
        if (snap) self.setState(snap);   // the widget backend answers through its trait instead
      }, function () { self._hideLoading(); self._functionsRequested = false; });
    }

    /** The node the function box acts on: the range's parent, the selection or the root. */
    _fnTarget() {
      return this.range ? this.range.parent : (this.selected || "/");
    }

    _filterFn() {
      if (!this.fnInput || !this._functionsLoaded) return;
      var q = this.fnInput.value.trim().replace(/\(.*$/, "").toLowerCase();
      var names = this._fnNames;
      var starts = [], contains = [];
      for (var i = 0; i < names.length; i++) {
        var n = names[i], l = n.toLowerCase();
        if (!q) { if (starts.length < 12) starts.push(n); continue; }
        if (l.indexOf(q) === 0) starts.push(n);
        else if (l.indexOf(q) >= 0) contains.push(n);
      }
      var list = starts.concat(contains).slice(0, 12);
      this.fnMenu.textContent = "";
      var self = this;
      list.forEach(function (name) {
        var sig = self._fnSigs[name];
        var item = h("div", { class: "se-fn-item", role: "option", "data-name": name }, [
          h("span", { class: "se-fn-name" }, [name]),
          h("span", { class: "se-fn-doc" }, [sig && sig.doc ? sig.doc : ""])
        ]);
        self.fnMenu.appendChild(item);
      });
      this._fnActive = list.length ? 0 : -1;
      this._highlightFn();
      this.fnMenu.hidden = !list.length;
      this._placeUnder(this.fnMenu, this.fnInput);
    }

    _highlightFn() {
      var items = this.fnMenu.querySelectorAll(".se-fn-item");
      for (var i = 0; i < items.length; i++) items[i].classList.toggle("se-active", i === this._fnActive);
    }

    _hideFnMenu() { if (this.fnMenu) this.fnMenu.hidden = true; }
    _hideFnForm() { if (this.fnForm) { this.fnForm.hidden = true; this.fnForm.textContent = ""; } }

    _placeUnder(panel, anchor) {
      var rr = this.root.getBoundingClientRect(), ar = anchor.getBoundingClientRect();
      panel.style.top = Math.round(ar.bottom - rr.top + 4) + "px";
      panel.style.left = Math.round(Math.max(0, Math.min(ar.left - rr.left, this.root.clientWidth - panel.offsetWidth - 4))) + "px";
    }

    /** A function was chosen: apply it, or ask for its parameters first. */
    _pickFn(name) {
      this._hideFnMenu();
      this.fnInput.value = name;
      var sig = this._fnSigs[name];
      if (sig) { this._showFnForm(sig); return; }
      var msg = { action: "signature", name: name, path: this._fnTarget() };
      if (this.range) msg.children = this._rangeIndices();
      this.send(msg);   // the answer (snapshot.signature) opens the form
    }

    /** Ask an op for the values it declares (`params`), then apply it: the
     *  array tools - permute, contract, diagonal - want axes. */
    _askOpParams(spec, path, anchor) {
      var self = this;
      if (!this.fnForm) {   // no function box on this page: let the backend say what is missing
        return this.send({ action: "apply", path: path, op: spec.name });
      }
      this._showFnForm({ name: spec.label.replace(/…\s*$/, ""), params: spec.params, doc: spec.doc || "",
                         callable: true, hinted: true },
        function (values) {
          var msg = { action: "apply", path: path, op: spec.name, args: values };
          if (self.lazy()) msg.lazy = true;
          if (self.range) msg.children = self._rangeIndices();
          self.send(msg);
        }, anchor);
    }

    /** Ask for the parameters of `sig` (or apply at once when none is required).
     *  `onApply(values)` replaces the default "call the function"; `anchor` is
     *  the element the form is placed under (the function box by default). */
    _showFnForm(sig, onApply, anchor) {
      var needs = sig.callable && sig.params.some(function (p) { return !p.optional; });
      if (!needs) { this._hideFnForm(); if (onApply) onApply([]); else this.callFunction(sig.name); return; }
      var self = this;
      var node = this.state.nodes[this._fnTarget()] || {};
      var free = node.free || [];
      this.fnForm.textContent = "";
      this.fnForm.appendChild(h("div", { class: "se-fn-title" }, [sig.name + "(" + (node.src ? node.src.slice(0, 30) + (node.src.length > 30 ? "…" : "") : "…") + ", …)"]));
      if (sig.doc) this.fnForm.appendChild(h("div", { class: "se-fn-docline" }, [sig.doc]));
      var controls = [];
      sig.params.forEach(function (prm, i) {
        var ctrl;
        if (prm.kind === "symbol" && free.length) {
          ctrl = h("select", {});
          if (prm.optional) ctrl.appendChild(h("option", { value: "" }, ["(default)"]));
          free.forEach(function (name) { ctrl.appendChild(h("option", { value: name }, [name])); });
        } else {
          ctrl = h("input", { type: "text", spellcheck: "false", placeholder: prm.default !== null && prm.default !== undefined ? String(prm.default) : (prm.optional ? "(optional)" : "") });
        }
        ctrl.addEventListener("keydown", function (ev) {
          ev.stopPropagation();
          if (ev.key === "Enter") { ev.preventDefault(); apply(); }
          else if (ev.key === "Escape") { ev.preventDefault(); self._hideFnForm(); self.view.focus({ preventScroll: true }); }
        });
        controls.push({ prm: prm, ctrl: ctrl });
        self.fnForm.appendChild(h("label", { class: "se-fn-field" }, [h("span", {}, [prm.name + (prm.optional ? "" : " *")]), ctrl]));
      });
      var apply = function () {
        var values = controls.map(function (c) { return (c.ctrl.value || "").trim(); });
        var parts = [], gap = false;
        for (var i = 0; i < controls.length; i++) {
          var prm = controls[i].prm, v = values[i];
          if (!v) {
            var later = values.slice(i + 1).some(function (x) { return x; });
            if (later && prm.default !== null && prm.default !== undefined && sig.hinted) { parts.push(String(prm.default)); continue; }
            gap = true;
            continue;
          }
          if (gap && !sig.hinted && !prm.varargs) parts.push(prm.name + "=" + v);
          else parts.push(v);
        }
        if (onApply) {
          self._hideFnForm();
          onApply(values);
          self.view.focus({ preventScroll: true });
          return;
        }
        var base = sig.name.replace(/^\./, "");
        var call;
        if ((base === "integrate" || base === "summation") && values[0] && values[1] && values[2]) {
          call = sig.name + "((" + values[0] + ", " + values[1] + ", " + values[2] + "))";
        } else {
          call = sig.name + "(" + parts.join(", ") + ")";
        }
        self._hideFnForm();
        self.callFunction(call);
      };
      var buttons = h("div", { class: "se-fn-buttons" }, [
        h("button", { type: "button", class: "se-fn-apply" }, ["Apply"]),
        h("button", { type: "button", class: "se-fn-cancel" }, ["Cancel"])
      ]);
      buttons.querySelector(".se-fn-apply").addEventListener("click", apply);
      buttons.querySelector(".se-fn-cancel").addEventListener("click", function () { self._hideFnForm(); self.view.focus({ preventScroll: true }); });
      this.fnForm.appendChild(buttons);
      this.fnForm.hidden = false;
      this._placeUnder(this.fnForm, anchor || this.fnInput);
      if (controls.length) controls[0].ctrl.focus();
    }

    /** Whether results are wanted unevaluated (the toolbar's toggle). */
    lazy() {
      return !!(this.lazyBox && this.lazyBox.checked);
    }

    /** Apply "name(args)" / ".method(args)" from the function box to the selection. */
    callFunction(text) {
      text = (text || "").trim();
      if (!text || this.opts.readOnly || this.closed) return;
      var msg = { action: "call", path: this._fnTarget(), func: text };
      if (this.lazy()) msg.lazy = true;
      if (this.range) msg.children = this._rangeIndices();
      this.fnInput.value = "";
      this._hideFnMenu();
      this.send(msg);
      this.view.focus({ preventScroll: true });
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
      var base = this.committed || this.state;
      var same = src === (base ? base.src : "");
      clearTimeout(this._previewTimer);
      this._previewSrc = null;
      this.sourceDirty = false;
      this.source.classList.remove("se-dirty");
      this.source.classList.remove("se-invalid");
      if (!src && this.view.classList.contains("se-empty")) {
        // Everything was deleted on purpose: the line stays empty (and dirty)
        // until something is typed, or Esc brings the expression back.
        this.sourceDirty = true;
        this.source.classList.add("se-dirty");
        this._setStatus("Everything removed: type the new expression – Enter applies, Esc restores the previous one");
        return;
      }
      if (!src || same) {
        this.revertSource();
        if (!src) this._setStatus("Empty: the previous expression is back (an expression cannot be empty)");
        return;
      }
      this.send({ action: "set", src: src });
    }

    revertSource() {
      var base = this.committed || this.state;
      this.source.textContent = base ? base.src : "";
      this.sourceDirty = false;
      this.source.classList.remove("se-dirty");
      this.source.classList.remove("se-invalid");
      clearTimeout(this._previewTimer);
      this._previewSrc = null;
      this._endEmptyInput();
      this.view.classList.remove("se-empty");
      if (this.state && this.state.preview && this.committed) { this.setState(this.committed); return; }   // back to what is committed
      this._applySelection();
    }

    /** Preview the source line (debounced) while it is typed: a string that
     *  parses is rendered at once, without being committed (Enter does that). */
    _schedulePreview() {
      var self = this;
      if (this.opts.readOnly || !this.backend) return;
      clearTimeout(this._previewTimer);
      this._previewTimer = setTimeout(function () { self._previewSource(); }, this.opts.previewDelay);
    }

    async _previewSource() {
      if (!this.sourceDirty || this.closed || !this.backend) return;
      var src = toSource(this.source.textContent).trim();
      if (!src) { this.source.classList.remove("se-invalid"); this.view.classList.add("se-empty"); return; }
      if (src === this._previewSrc) return;                              // shown already (or on its way)
      if (this._previewing) { this._previewAgain = true; return; }       // one at a time; the latest text goes next
      this._previewing = true;
      this._previewSrc = src;
      try {
        var snap = await this.backend.send({ action: "preview", src: src }, function () {});
        if (snap && this.sourceDirty) await this.setState(snap);         // unless Enter committed meanwhile
      } catch (e) {
        this._previewSrc = null;
      } finally {
        this._previewing = false;
        if (this._previewAgain) { this._previewAgain = false; this._previewSource(); }
      }
    }

    /** The empty formula area takes the new expression in a field of its
     *  own: what is typed is mirrored in the source line and previewed above
     *  the field; Enter applies it, Esc brings the previous expression back.
     *  Returns false when there is nothing to open (read-only, not empty). */
    beginEmptyInput(initial) {
      if (this.opts.readOnly || this.closed || !this.view.classList.contains("se-empty")) return false;
      if (this.emptyField) {
        this.emptyField.focus();
        if (initial) { this.emptyField.value += initial; this.emptyField.dispatchEvent(new Event("input")); }
        return true;
      }
      var self = this;
      var input = h("input", { class: "se-inline se-inline-empty", type: "text", spellcheck: "false", autocomplete: "off",
        placeholder: "expression", "aria-label": "The new expression (SymPy syntax)" });
      input.value = initial || "";
      this.view.appendChild(input);
      this.view.classList.add("se-typing");
      this.emptyField = input;
      this._wireField(input, 10);                                  // sizing and "\command" expansion (its Enter/Esc do nothing here)
      input.addEventListener("input", function () {
        self.source.textContent = input.value;                     // the line follows; a parsable text is previewed
        self.sourceDirty = true;
        self.source.classList.add("se-dirty");
        self._schedulePreview();
      });
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          var src = toSource(input.value).trim();
          if (!src) return;
          self._endEmptyInput();
          self.source.textContent = src;
          self.send({ action: "set", src: src });
        } else if (ev.key === "Escape") {
          ev.preventDefault();
          self._endEmptyInput();
          self.revertSource();
          self.view.focus({ preventScroll: true });
        }
      });
      input.addEventListener("blur", function () {
        // Leaving a non-empty field applies it, like leaving the source line
        // does - checked after the fact: a preview re-rendering the view
        // takes the field out and puts it back (with the focus) at once.
        setTimeout(function () {
          if (self.emptyField !== input || self.closed || !input.parentNode || document.activeElement === input) return;
          if (toSource(input.value).trim()) input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
        }, 0);
      });
      this._setStatus("Everything removed: type the new expression – Enter applies, Esc restores the previous one");
      if (initial) input.dispatchEvent(new Event("input"));
      input.focus();
      return true;
    }

    _endEmptyInput() {
      var field = this.emptyField;
      if (!field) return;
      this.emptyField = null;
      if (field.parentNode) field.parentNode.removeChild(field);
      this.view.classList.remove("se-typing");
    }

    /** Put the keyboard in the source line with everything selected. */
    editSource(text) {
      if (this.opts.readOnly || !this.opts.showSource) return false;
      var sel = window.getSelection();
      if (text !== undefined) {
        // Start over: the line holds only `text` (possibly nothing) until Enter applies it.
        this.source.textContent = text;
        this.sourceDirty = true;
        this.source.classList.add("se-dirty");
        if (!text) {
          // The formula is gone until something is typed - in a field where it was.
          this.select(null);
          this.view.classList.add("se-empty");
          if (!this.beginEmptyInput()) this.source.focus();
          return true;
        }
        this.source.focus();
        this._schedulePreview();
        if (sel && this.source.firstChild) sel.collapse(this.source.firstChild, this.source.firstChild.length);
        this._setStatus("Editing the whole expression – Enter applies, Esc restores the previous one");
      } else {
        this.source.focus();
        if (sel && this.source.firstChild) sel.selectAllChildren(this.source);
        this._setStatus("Editing the whole expression as SymPy source – Enter applies, Esc reverts");
      }
      return true;
    }

    /* ---- insertion caret ---- */

    _argIndex(parent, child) {
      var rest = parent === "/" ? child.slice(1) : child.slice(parent.length + 1);
      return parseInt(rest.split("/")[0], 10);
    }

    /** The step from `parent` to `child`: an argument index, or the name of
     *  a virtual part ("n", "d", "neg"). */
    _childKey(parent, child) {
      var rest = parent === "/" ? child.slice(1) : child.slice(parent.length + 1);
      var step = rest.split("/")[0];
      return step in PART_ORDER ? step : parseInt(step, 10);
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
      this.junction = null;
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

    /** The operator glyph under the pointer - the "+" of a sum, the "\u22c5" of a
     *  product, "=", "\u2227"... - as a junction {path, left, right, leftIndex,
     *  rightIndex, el, text}: the node whose arguments (SymPy indices `left`,
     *  `right`; display indices `leftIndex`, `rightIndex`) it joins.  The
     *  "\u2212" shown before a negative term is the operator of the enclosing
     *  sum.  Null when the pointer is not on an operator. */
    _operatorAt(ev) {
      if (!this.state || !this.state.nodes || typeof ev.clientX !== "number" || !document.elementsFromPoint) return null;
      var stack = document.elementsFromPoint(ev.clientX, ev.clientY);
      var glyph = null, text = "";
      for (var i = 0; i < stack.length; i++) {
        var el = stack[i];
        if (!this.view.contains(el) || el === this.view || el.querySelector("[data-path]")) continue;
        text = (el.textContent || "").trim();
        if (!text) continue;                 // struts and spacing carry no glyph
        // The topmost glyph under the pointer decides: an operator lower in
        // the stack (its box can stretch over the neighbouring space) must
        // not steal a click that lands on a letter.
        if (Object.prototype.hasOwnProperty.call(OPERATOR_GLYPHS, text)) glyph = el;
        break;
      }
      if (!glyph) return null;
      var owner = glyph.closest("[data-path]");
      if (!owner || !this.view.contains(owner)) return null;
      var p = owner.getAttribute("data-path");
      var gr = this._visualRect(glyph), cx = (gr.left + gr.right) / 2;
      var rightPath = null;
      if (OPERATOR_GLYPHS[text] === "-" && this.tree[p] && this.tree[p].parent
          && Math.abs(gr.left - this._visualRect(owner).left) < 3
          && /Add$/.test(this.state.nodes[this.tree[p].parent].type)) {
        rightPath = p; p = this.tree[p].parent;               // the sign of a negative term
      }
      var self = this;
      var kids = this._displayChildren(p);
      var els = kids.map(function (c) { var e = self._els(c)[0]; return e ? { path: c, rect: self._visualRect(e) } : null; });
      var left = -1, right = -1;
      for (var k = 0; k < els.length; k++) {
        if (!els[k]) continue;
        var r = els[k].rect;
        var sameLine = Math.min(r.bottom, gr.bottom) - Math.max(r.top, gr.top) > 0;
        if (rightPath ? els[k].path === rightPath : (sameLine && r.left >= cx - 1)) { right = k; break; }
        if (sameLine && r.right <= cx + 1) left = k;
      }
      if (left < 0 || right < 0) return null;
      return { path: p, el: glyph, text: text, leftIndex: left, rightIndex: right,
               left: this._argIndex(p, kids[left]), right: this._argIndex(p, kids[right]) };
    }

    selectJunction(j) {
      this._hideKeep();
      this._hideCaret();
      this.selected = null;
      this.range = null;
      this.junction = j;
      this._fillOps();
      this._applySelection();
      this._updateToolbar();
    }

    /** Change the selected operator (`op` is a key of OPERATOR_KEYS or ""
     *  for none: the two arguments then multiply). */
    setOperator(op) {
      var j = this.junction;
      if (!j || this.opts.readOnly) return;
      var msg = { action: "operator", path: j.path, left: j.left, right: j.right, op: op };
      if (this.lazy()) msg.lazy = true;
      this.selected = j.path;                  // what the change leaves is selected afterwards
      this.junction = null;
      this.send(msg);
    }

    /** Children of `p` in reading order: left to right on a line, a higher
     *  line before a lower one (the numerator before the denominator). */
    _readingChildren(p) {
      var self = this;
      var kids = (this.tree[p] ? this.tree[p].children : [])
        .map(function (c) { var el = self._els(c)[0]; return { path: c, rect: el ? self._visualRect(el) : null }; })
        .filter(function (k) { return k.rect; });
      kids.sort(function (a, b) {
        var overlap = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (overlap >= 0.5 * Math.min(a.rect.height, b.rect.height)) return a.rect.left - b.rect.left;
        return a.rect.top - b.rect.top;
      });
      return kids.map(function (k) { return k.path; });
    }

    /** Every place a caret can be, in reading order, like the positions of a
     *  text cursor: the gaps of insertable nodes and, elsewhere, before and
     *  after each argument (extend carets).  Coinciding positions are
     *  merged: a gap wins over an extend caret, the innermost extend caret
     *  over an outer one (what an edge click gives). */
    _caretPositions() {
      var self = this;
      var list = [];
      var gapWith = function (parent, side, el) {
        var gaps = self._gapsOf(parent);
        for (var i = 0; i < gaps.length; i++) {
          if (side === "before" ? gaps[i].rightEl === el : gaps[i].leftEl === el) {
            return { gap: Object.assign({}, gaps[i], { attach: side === "before" ? "right" : "left" }), x: side === "before" ? gaps[i].b : gaps[i].a };
          }
        }
        return null;
      };
      var edge = function (parent, side, kid) {
        var el = self._els(kid)[0];
        var pnode = self.state.nodes[parent];
        var pos = pnode && pnode.insertable ? gapWith(parent, side, el) : null;
        return pos || self._extendGap(kid, side);
      };
      var walk = function (p) {
        var kids = self._readingChildren(p);
        for (var i = 0; i < kids.length; i++) {
          var before = edge(p, "before", kids[i]);
          if (before) list.push(before);
          walk(kids[i]);
          var after = edge(p, "after", kids[i]);
          if (after) list.push(after);
        }
      };
      // The formula's own ends: without them a root that has no argument
      // gaps (a matrix) would offer no position outside itself, and the
      // caret could neither start before it nor leave it leftwards.
      var head = this._extendGap("/", "before"), tail = this._extendGap("/", "after");
      if (head) list.push(head);
      walk("/");
      if (tail) list.push(tail);
      // merge coinciding positions (the same gap is reached as "after the
      // left argument" and "before the right one") - only on the same line:
      // the rows of a matrix may align vertically without being one place
      var sameGap = function (g, h) {
        return g.path === h.path && !!g.extend === !!h.extend && (g.extend ? g.extend === h.extend : g.index === h.index);
      };
      var sameLine = function (g, h) {
        return Math.min(g.bottom, h.bottom) - Math.max(g.top, h.top) > 0;
      };
      var out = [];
      for (var i = 0; i < list.length; i++) {
        var pos = list[i], last = out[out.length - 1];
        if (last && (sameGap(last.gap, pos.gap) || (Math.abs(last.x - pos.x) < 1.5 && sameLine(last.gap, pos.gap)))) {
          var better = (!pos.gap.extend && last.gap.extend) ||
            (!!pos.gap.extend === !!last.gap.extend && pos.gap.path.length > last.gap.path.length);
          if (better) out[out.length - 1] = pos;
          continue;
        }
        out.push(pos);
      }
      return out;
    }

    /** ←/→ at a caret: the previous/next caret position of the formula -
     *  out of the current node at its ends, into a composite neighbour. */
    _moveCaret(step) {
      var at = this._caretIndex();
      if (!at) return;
      var j = at.index + step;
      if (j < 0 || j >= at.count) return;
      var g = at.list[j].gap;
      this._showCaret(g, g.extend ? at.list[j].x : (step < 0 ? g.b : g.a));   // the near end of a gap
    }

    /** A caret at the first or the last position of the formula. */
    _caretAtEnd(which) {
      if (this.opts.readOnly || !this.state) return;
      var list = this._caretPositions();
      if (!list.length) return;
      var pos = list[which === "start" ? 0 : list.length - 1];
      this._showCaret(pos.gap, pos.gap.extend ? pos.x : (which === "start" ? pos.gap.b : pos.gap.a));
    }

    /** Where the caret is among the positions of the formula: {index, count}. */
    _caretIndex() {
      var cur = this.caret;
      if (!cur) return null;
      var list = this._caretPositions();
      var idx = -1, best = Infinity, mid = (cur.a + cur.b) / 2;
      for (var i = 0; i < list.length; i++) {
        var g = list[i].gap;
        var same = g.path === cur.path && !!g.extend === !!cur.extend && (g.extend ? g.extend === cur.extend : g.index === cur.index);
        var d = Math.abs(list[i].x - mid);
        if (same && d < best) { idx = i; best = d; }
      }
      if (idx < 0) for (var k = 0; k < list.length; k++) { var dk = Math.abs(list[k].x - mid); if (dk < best) { idx = k; best = dk; } }
      return { index: idx, count: list.length, list: list };
    }

    /** The sibling ←/→ would select from `path` (null at the ends). */
    _sidewaysTarget(path, step) {
      var cur = path;
      while (cur) {
        var parent = this.tree[cur] ? this.tree[cur].parent : null;
        if (!parent) return null;
        var sib = this._displayChildren(parent);
        var i = sib.indexOf(cur) + step;
        if (i >= 0 && i < sib.length) return sib[i];
        cur = parent;
      }
      return null;
    }

    /** ↑ at a caret: the object the caret is attached to (then its ancestors). */
    _selectBesideCaret() {
      var c = this.caret;
      var beside = c.attach === "right" ? (c.rightEl || c.leftEl) : (c.leftEl || c.rightEl);
      var path = beside ? beside.getAttribute("data-path") : c.path;
      this._hideCaret();
      this.select(path);
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
      var target = this._sidewaysTarget(this.selected, step);
      if (target) this.select(target);
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
      this.junction = null;
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
        case "isolate": return this.isolateSelection();
        case "delete":
          if (this.junction) return this.setOperator("");
          if (this.range) return this.send({ action: "delete", path: this.range.parent, children: this._rangeIndices() });
          if (this.selected === "/") return this.editSource("");
          if (this.selected) return this.send({ action: "delete", path: this.selected });
          return;
        case "child":
          if (this.caret) return;    // nothing to go into from a caret
          return this._selectChild();
        case "drawer": return this.toggleDrawer();
        case "history": return this.showHistory();
        case "help": return this.showHelp();
        case "report": return this.exportReport();
        case "left":
        case "right": {
          var step = cmd === "left" ? -1 : 1;
          if (this.caret) return this._moveCaret(step);
          if (this.range) return this.select(this._displayChildren(this.range.parent)[this.range.focus]);
          if (this.selected) return this._moveSideways(step);
          return this._caretAtEnd(step < 0 ? "start" : "end");
        }
        case "parent": {
          if (this.caret) return this._selectBesideCaret();
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
        case "paste": return this.pasteClipboard();
        case "zoomin": return this.setZoom(this.zoom * ZOOM_STEP);
        case "zoomout": return this.setZoom(this.zoom / ZOOM_STEP);
        case "zoomreset": return this.setZoom(1);
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
      // A request that takes a while gets the spinner overlay, and after a
      // few seconds the offer to interrupt it (where the backend can).
      var working = setTimeout(function () { self._showLoading(self._workingText(msg)); }, this.opts.workingAfter);
      var offer = setTimeout(function () {
        if (self.backend.interrupt && (!self.backend.canInterrupt || self.backend.canInterrupt())) self.interruptBtn.hidden = false;
      }, this.opts.interruptAfter);
      var wasSrepr = this.state ? this.state.srepr : null;
      try {
        var snap = await this.backend.send(msg, function (text) { self._report(text); });
        if (snap) await this.setState(snap);
        if ((msg.action === "apply" || msg.action === "call") && snap && !snap.error && snap.srepr === wasSrepr) {
          this._setStatus("No change: " + this._workingText(msg).replace(/^Computing /, "").replace(/…$/, "") + " leaves the expression as it is");
        }
      } catch (e) {
        this._showError(String((e && e.message) || e));
        this._applySelection();
      } finally {
        clearTimeout(working);
        clearTimeout(offer);
        this.interruptBtn.hidden = true;
        this.interruptBtn.disabled = false;
        this._hideLoading();
        this.busy = false;
        this.root.classList.remove("se-busy");
        this._updateToolbar();
      }
    }

    _workingText(msg) {
      if (msg.action === "apply") {
        var op = (this.state && this.state.ops || []).filter(function (o) { return o.name === msg.op; })[0];
        return "Computing " + (op ? op.label : msg.op) + "…";
      }
      if (msg.action === "call") return "Computing " + msg.func + "…";
      return "Working…";
    }

    /** Stop the request in progress (the Interrupt button of the overlay). */
    interrupt() {
      if (!this.busy || !this.backend || !this.backend.interrupt) return;
      this.interruptBtn.disabled = true;
      this._showLoading("Interrupting…");
      var self = this;
      Promise.resolve(this.backend.interrupt()).then(function (ok) {
        if (ok === false) self._setStatus("This computation cannot be interrupted here");
      }, function () {});
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

    /* ---- sessions ---- */

    _loadSessions() {
      try {
        var store = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "null");
        if (store && Array.isArray(store.list)) return store;
      } catch (e) { /* no storage, or garbage */ }
      return { current: null, list: [] };
    }

    _saveSessions(store) {
      this._sessionStore = store;
      try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(store)); } catch (e) { /* no storage */ }
    }

    _currentSession() {
      var store = this._sessionStore || this._loadSessions();
      return store.list.filter(function (s) { return s.id === store.current; })[0] || null;
    }

    /** Open the current session (or start one from the expression shown). */
    async _initSessions() {
      if (!this.sessions || !this.backend || !this.backend.openDocument) return;
      var store = this._loadSessions();
      var cur = store.list.filter(function (s) { return s.id === store.current; })[0];
      if (cur && cur.state) {
        try {
          await this.setState(await this.backend.openDocument(cur.state, this._report.bind(this)));
          if (cur.empty) this.editSource("");
        } catch (e) {
          this._showError("The session could not be opened: " + ((e && e.message) || e));
        }
      } else {
        cur = { id: "s" + Date.now(), name: "", updated: Date.now(), state: null };
        store.list.push(cur);
        store.current = cur.id;
      }
      this._saveSessions(store);
      this._sessionsReady = true;
      this._fillSessions();
      this._scheduleSessionSave();
    }

    _scheduleSessionSave() {
      var self = this;
      clearTimeout(this._sessionSaveTimer);
      this._sessionSaveTimer = setTimeout(function () { self._saveSession(); }, 800);
    }

    /** Ask the backend for the document's history and store it (setState
     *  gets the answer, flagged `export`, and calls _storeSession). */
    _saveSession() {
      if (!this._sessionsReady || this.closed || !this.backend) return;
      var self = this;
      Promise.resolve(this.backend.send({ action: "export" }, function () {})).then(function (snap) {
        if (snap) self._storeSession(snap);
      }, function () {});
    }

    _storeSession(snap) {
      var store = this._sessionStore || this._loadSessions();
      var cur = store.list.filter(function (s) { return s.id === store.current; })[0];
      if (!cur) return;
      cur.state = snap.export;
      if (cur.empty && snap.export && snap.export.history.length > 1) cur.empty = false;   // something was typed
      cur.name = cur.empty ? "(empty)" : (snap.src || "").slice(0, 60);
      cur.updated = Date.now();
      if (snap.history) this._history = snap.history;
      this._saveSessions(store);
      this._fillSessions();
    }

    toggleDrawer() {
      if (!this.drawer) return;
      if (this.drawer.hidden) this.openDrawer(); else this.closeDrawer();
    }

    /** "history" opens the current session's history sub-tab; anything else closes it. */
    showDrawerTab(name) {
      if (!this.drawer) return;
      var tabs = this.subtabs.querySelectorAll("[data-tab]");
      for (var i = 0; i < tabs.length; i++) tabs[i].classList.toggle("se-subtab-current", tabs[i].getAttribute("data-tab") === name);
      this.historyPane.hidden = name !== "history";
      if (name === "history") this._renderHistory();
    }

    /** Render the formulas of the history rows (once per row), each step as
     *  a diff: the previous formula with what went in red, this one with what
     *  came in green. */
    _renderHistory() {
      var hist = this._history;
      if (!hist || !hist.steps || this.historyPane.hidden || !window.katex) return;
      var self = this;
      var rows = this.historyBody.querySelectorAll(".se-step[data-index]");
      var opts = { displayMode: false, output: "html", throwOnError: false, trust: function (ctx) { return ctx.command === "\\htmlData"; },
                   strict: function (code) { return code === "htmlExtension" ? "ignore" : "warn"; } };
      var render = function (holder, step, marks, cls) {
        holder.textContent = "";
        try { katex.render(step.latex, holder, opts); } catch (e) { holder.textContent = "?"; }
        var els = holder.querySelectorAll("[data-path]");
        for (var i = 0; i < els.length; i++) {
          var p = els[i].getAttribute("data-path");
          els[i].setAttribute("data-hpath", p);      // not a node of the formula being edited
          els[i].removeAttribute("data-path");
          if (marks) els[i].classList.add(marks[p] ? "se-diff-kept" : cls);
        }
        if (marks) markBoxes(holder, cls, "se-diff-box");
      };
      for (var r = 0; r < rows.length; r++) {
        var row = rows[r];
        if (row.getAttribute("data-rendered")) continue;
        var i = parseInt(row.getAttribute("data-index"), 10);
        var step = hist.steps[i], prev = i > 0 ? hist.steps[i - 1] : null;
        var formulas = row.querySelector(".se-step-formulas");
        if (!step || !formulas) continue;
        if (prev) {
          var diff = diffNodes(prev.nodes, step.nodes);
          var before = h("span", { class: "se-step-before" }), after = h("span", { class: "se-step-after" });
          render(before, prev, diff.oldKept, "se-diff-removed");
          render(after, step, diff.newKept, "se-diff-added");
          formulas.appendChild(before);
          formulas.appendChild(h("span", { class: "se-step-arrow" }, ["\u2192"]));
          formulas.appendChild(after);
        } else {
          var only = h("span", { class: "se-step-after" });
          render(only, step, null, "");
          formulas.appendChild(only);
        }
        row.setAttribute("data-rendered", "1");
      }
    }

    openDrawer() {
      if (!this.drawer) return;
      this._fillSessions();
      this.backdrop.hidden = false;
      this.drawer.hidden = false;
      var self = this;
      requestAnimationFrame(function () { self.drawer.classList.add("se-open"); self.backdrop.classList.add("se-open"); });
      // Esc closes it wherever the focus is (its buttons come and go as the list is redrawn).
      if (!this._drawerKey) {
        this._drawerKey = function (ev) { if (ev.key === "Escape") { ev.preventDefault(); self.closeDrawer(); } };
        document.addEventListener("keydown", this._drawerKey);
      }
      if (this._sessionsReady) this._saveSession();   // brings the history list up to date
    }

    closeDrawer() {
      if (!this.drawer || this.drawer.hidden) return;
      if (this._drawerKey) { document.removeEventListener("keydown", this._drawerKey); this._drawerKey = null; }
      this.drawer.classList.remove("se-open");
      this.backdrop.classList.remove("se-open");
      this.drawer.hidden = true;
      this.backdrop.hidden = true;
      this.view.focus({ preventScroll: true });
    }

    /* ---- the history report ---- */

    /** The KaTeX stylesheet with its fonts inlined as data URIs (fetched
     *  once per page): what makes a report self-contained. */
    async _katexCssInline() {
      var cache = window.__sympyEditorKatexInline || (window.__sympyEditorKatexInline = {});
      var href = this.opts.katexCss;
      if (!cache[href]) {
        cache[href] = (async function () {
          var base = new URL(href, document.baseURI);
          var css = await (await fetch(base.href)).text();
          var fonts = {};
          var re = /url\((?:"|')?(fonts\/[^)"']+\.woff2)(?:"|')?\)/g, m;
          while ((m = re.exec(css))) fonts[m[1]] = true;
          await Promise.all(Object.keys(fonts).map(async function (rel) {
            var blob = await (await fetch(new URL(rel, base).href)).blob();
            fonts[rel] = await new Promise(function (resolve, reject) {
              var r = new FileReader();
              r.onload = function () { resolve(r.result); };
              r.onerror = reject;
              r.readAsDataURL(blob);
            });
          }));
          // Keep the woff2 face only, as a data URI (the woff/ttf fallbacks
          // would be dead links).  The src declaration is replaced up to the
          // next ";" or "}" - never past it: in minified CSS the last
          // declaration of a block has no ";", and running over the "}" would
          // swallow the following @font-face rules whole (every face but the
          // first was lost, and \left[ fell back to a normal-height bracket).
          return css.replace(/src:\s*url\((?:"|')?(fonts\/[^)"']+\.woff2)(?:"|')?\)\s*format\((?:"|')?woff2(?:"|')?\)[^;}]*/g, function (all, rel) {
            return "src:url(" + fonts[rel] + ") format(\"woff2\")";
          });
        })().catch(function (e) { delete cache[href]; throw e; });
      }
      return cache[href];
    }

    /** KaTeX HTML for `latex`, the nodes not in `kept` marked with `cls`
     *  (all of them plain when `kept` is null), no data-path attributes. */
    _renderMarked(latex, kept, cls) {
      var div = document.createElement("div");
      div.innerHTML = katex.renderToString(latex, { displayMode: true, output: "html", throwOnError: false,
        trust: function (ctx) { return ctx.command === "\\htmlData"; },
        strict: function (code) { return code === "htmlExtension" ? "ignore" : "warn"; } });
      var els = div.querySelectorAll("[data-path]");
      for (var i = 0; i < els.length; i++) {
        var p = els[i].getAttribute("data-path");
        if (kept) els[i].classList.add(kept[p] ? "rep-kept" : cls);
        els[i].removeAttribute("data-path");
      }
      markBoxes(div, cls, "rep-box");
      return div.innerHTML;
    }

    /** The self-contained HTML report of the current history: every step
     *  rendered (what came in green), and between two steps what produced
     *  the change and the previous formula with what went in red. */
    async buildReport() {
      var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
      var snap = await this.backend.send({ action: "export" }, function () {});
      var hist = snap && snap.history ? snap.history : this._history;
      if (!hist || !hist.steps) throw new Error("No history to report");
      if (snap && snap.history) this._history = snap.history;
      var css = await this._katexCssInline();
      var sess = this._currentSession();
      var title = "SymPy editor \u2014 history" + (sess && sess.name ? " of " + sess.name : "");
      var out = [];
      for (var i = 0; i < hist.steps.length; i++) {
        var step = hist.steps[i], prev = i > 0 ? hist.steps[i - 1] : null, diff = null;
        if (prev) {
          diff = diffNodes(prev.nodes, step.nodes);
          out.push('<div class="transition"><div class="arrow">\u2193</div><div class="what">' + esc((hist.actions && hist.actions[i]) || "Edit") + "</div>" +
            '<div class="before"><span class="label">from (what went is red)</span>' + this._renderMarked(prev.latex, diff.oldKept, "rep-removed") + "</div></div>");
        }
        out.push('<section class="step" data-index="' + i + '"' + (i === hist.index ? ' data-current="1"' : "") + "><h2>Step " + (i + 1) + (i === hist.index ? " \u2014 current" : "") +
          (i === 0 ? " \u2014 start" : "") + '</h2><div class="formula">' + this._renderMarked(step.latex, diff ? diff.newKept : null, "rep-added") +
          "</div><code>" + esc(hist.labels[i]) + "</code></section>");
      }
      var when = new Date();
      return "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
        "<title>" + esc(title) + "</title><style>\n" + css + "\n" + REPORT_CSS + "\n</style></head><body><main>" +
        "<h1>" + esc(title) + "</h1><p class=\"meta\">" + esc(when.toLocaleString()) + " \u00b7 " + hist.steps.length + " step" + (hist.steps.length === 1 ? "" : "s") +
        " \u00b7 green: what a step brought, red: what the previous one lost</p>" + out.join("\n") +
        "<footer>Generated by sympy-editor. This file is self-contained (KaTeX rendering and fonts included) and works offline.</footer></main></body></html>\n";
    }

    /** The Python script reproducing the history (built by the document). */
    async buildPython() {
      var sess = this._currentSession();
      var title = "SymPy editor \u2014 history" + (sess && sess.name ? " of " + sess.name : "");
      var snap = await this.backend.send({ action: "script", title: title }, function () {});
      if (!snap || !snap.script) throw new Error("No history to export");
      return snap.script;
    }

    /** Download the report (built unless given) - or hand it to the app / the share sheet. */
    async exportReport(html) {
      if (this.busy || this.closed || !this.backend) return;
      this._setStatus("Building the report\u2026");
      try {
        if (!html) html = await this.buildReport();
        await this._exportFile(this._exportName("html"), "text/html", html, "Report");
      } catch (e) {
        this._showError("The report could not be built: " + ((e && e.message) || e));
      }
    }

    /** Download the history as a Python script - or hand it to the app / the share sheet. */
    async exportPython() {
      if (this.busy || this.closed || !this.backend) return;
      this._setStatus("Building the script\u2026");
      try {
        var text = await this.buildPython();
        await this._exportFile(this._exportName("py"), "text/x-python", text, "Script");
      } catch (e) {
        this._showError("The script could not be built: " + ((e && e.message) || e));
      }
    }

    _exportName(ext) {
      return "sympy-editor-history-" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + "." + ext;
    }

    /** Hand `text` to the app's share sheet, the Web Share API or a download, in that order. */
    async _exportFile(name, mime, text, what) {
      var app = window.SympyEditorApp;
      if (app && (app.shareFile || (mime === "text/html" && app.shareHtml))) {       // the Android app: save or share
        if (app.shareFile) app.shareFile(name, mime, text); else app.shareHtml(name, text);
        this._setStatus(what + " ready: choose where to save or share it");
        return;
      }
      var file = null;
      try { file = new File([text], name, { type: mime }); } catch (e) { /* no File constructor */ }
      if (file && navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
        try { await navigator.share({ files: [file], title: name }); this._setStatus(what + " shared"); return; }
        catch (e) { if (e && e.name === "AbortError") { this._setStatus(""); return; } }
      }
      var url = URL.createObjectURL(new Blob([text], { type: mime }));
      var a = h("a", { href: url, download: name });
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      this._setStatus(what + " downloaded: " + name);
    }

    /* ---- the history view ---- */

    /** Show the history report in the page: every step with what changed,
     *  a click on a step opens it in the editor, buttons save the report as
     *  a web page or the history as a Python script. */
    async showHistory() {
      if (this.busy || this.closed || !this.backend) return;
      var self = this;
      this.closeDrawer();
      this._setStatus("Building the history view\u2026");
      var html;
      try {
        html = await this.buildReport();
      } catch (e) {
        this._showError("The history could not be shown: " + ((e && e.message) || e));
        return;
      }
      this._setStatus("");
      this.closeHistory();
      this.closeHelp();
      var frame = h("iframe", { class: "se-history-frame", title: "History" });
      var saveHtml = h("button", { type: "button", "data-save": "html", title: "A self-contained web page: works offline, KaTeX rendering and fonts included" }, ["Save as web page"]);
      var savePy = h("button", { type: "button", "data-save": "py", title: "A Python script rebuilding every step with SymPy" }, ["Save as Python"]);
      var close = h("button", { type: "button", class: "se-history-close", title: "Close (Esc)", "aria-label": "Close" }, ["\u2715"]);
      var head = h("div", { class: "se-history-head" }, [
        h("span", { class: "se-history-title" }, ["History", h("small", {}, ["tap a step to open it"])]), saveHtml, savePy, close]);
      var view = h("div", { class: "se-history-view", role: "dialog", "aria-label": "History" }, [head, frame]);
      saveHtml.addEventListener("click", function () { self.exportReport(html); });
      savePy.addEventListener("click", function () { self.exportPython(); });
      close.addEventListener("click", function () { self.closeHistory(); });
      frame.addEventListener("load", function () {
        var d = frame.contentDocument;
        if (!d) return;
        var style = d.createElement("style");
        style.textContent = ".step[data-index] { cursor: pointer; } .step[data-index]:hover { border-color: #3b82f6; }";
        d.head.appendChild(style);
        var steps = d.querySelectorAll(".step[data-index]");
        for (var i = 0; i < steps.length; i++) {
          steps[i].addEventListener("click", function (ev) {
            var index = parseInt(ev.currentTarget.getAttribute("data-index"), 10);
            self.closeHistory();
            if (!isNaN(index)) self.gotoStep(index);
          });
        }
        d.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { ev.preventDefault(); self.closeHistory(); } });
        var current = d.querySelector('.step[data-current="1"]');
        if (current && current.scrollIntoView) current.scrollIntoView({ block: "center" });
      });
      this._historyKey = function (ev) { if (ev.key === "Escape") { ev.preventDefault(); self.closeHistory(); } };
      view.addEventListener("keydown", this._historyKey);        // (the editor's own handler stops Esc from reaching the document)
      document.addEventListener("keydown", this._historyKey);
      this.historyView = view;
      this.root.appendChild(view);
      frame.srcdoc = html;
      close.focus();
    }

    /** The guide in a box: what showHelp shows the toolbar's "?" opens.
     *  Static content (HELP_HTML), same overlay dress as the history view. */
    showHelp() {
      if (this.helpView) { this.closeHelp(); return; }    // the button toggles it
      var self = this;
      this.closeDrawer();
      this.closeHistory();
      var close = h("button", { type: "button", class: "se-history-close", title: "Close (Esc)", "aria-label": "Close" }, ["\u2715"]);
      var head = h("div", { class: "se-history-head" }, [
        h("span", { class: "se-history-title" }, ["How to use the editor"]), close]);
      var body = h("div", { class: "se-help-body" });
      body.innerHTML = HELP_HTML;
      var view = h("div", { class: "se-history-view se-help-view", role: "dialog", "aria-label": "How to use the editor" }, [head, body]);
      close.addEventListener("click", function () { self.closeHelp(); });
      this._helpKey = function (ev) { if (ev.key === "Escape") { ev.preventDefault(); self.closeHelp(); } };
      view.addEventListener("keydown", this._helpKey);           // (the editor's own handler stops Esc from reaching the document)
      document.addEventListener("keydown", this._helpKey);
      this.helpView = view;
      this.root.appendChild(view);
      close.focus();
    }

    closeHelp() {
      if (!this.helpView) return;
      if (this._helpKey) { document.removeEventListener("keydown", this._helpKey); this._helpKey = null; }
      if (this.helpView.parentNode) this.helpView.parentNode.removeChild(this.helpView);
      this.helpView = null;
      this.view.focus({ preventScroll: true });
    }

    closeHistory() {
      if (!this.historyView) return;
      if (this._historyKey) { document.removeEventListener("keydown", this._historyKey); this._historyKey = null; }
      if (this.historyView.parentNode) this.historyView.parentNode.removeChild(this.historyView);
      this.historyView = null;
      this.view.focus({ preventScroll: true });
    }

    /** Jump to step `index` of the current session's history. */
    gotoStep(index) {
      if (this.busy || this.closed) return;
      this.send({ action: "goto", index: index });
    }

    async openSession(id) {
      if (this.busy || !this._sessionsReady) return;
      var store = this._sessionStore || this._loadSessions();
      var sess = store.list.filter(function (s) { return s.id === id; })[0];
      if (!sess || id === store.current) return;
      clearTimeout(this._sessionSaveTimer);
      this.busy = true;
      this._updateToolbar();
      try {
        var saved = await this.backend.send({ action: "export" }, function () {});   // the one we leave, up to date
        if (saved) this._storeSession(saved);
        var state = sess.state || { history: [this.state.srepr], index: 0, symbols: this.state.declared || [] };
        var snap = await this.backend.openDocument(state, this._report.bind(this));
        if (snap && snap.error) throw new Error(snap.error);
        store.current = id;                 // only once the document is open: a failure leaves the pointer alone
        this._saveSessions(store);
        this._history = null;
        this.busy = false;
        this.select(null);
        this._hideCaret();
        await this.setState(snap);
        this.closeDrawer();                                             // the session is open: back to its formula
        if (sess.empty) this.editSource("");                            // an empty session: type the formula
      } catch (e) {
        this._showError("The session could not be opened: " + ((e && e.message) || e));
      } finally {
        this._hideLoading();
        this.busy = false;
        this._updateToolbar();
        this._fillSessions();
      }
    }

    /** A new session from `start`: "empty" (an empty formula to type into),
     *  "current" (a copy of the current expression) or an example's srepr -
     *  with a fresh history. */
    newSession(start) {
      if (!this._sessionsReady || !this.state) return;
      var store = this._sessionStore || this._loadSessions();
      var sess = { id: "s" + Date.now(), name: "", updated: Date.now(), state: null, empty: false };
      if (!start || start === "empty") {
        sess.empty = true;                     // a placeholder 0 hidden by the empty state until something is typed
        sess.name = "(empty)";
        sess.state = { history: ["Integer(0)"], index: 0, symbols: [] };
      } else if (start === "current") {
        sess.name = (this.state.src || "").slice(0, 60);
        sess.state = { history: [this.state.srepr], index: 0, symbols: this.state.declared || [] };
      } else {
        sess.state = { history: [start], index: 0, symbols: [] };
      }
      store.list.push(sess);
      this._saveSessions(store);
      this.openSession(sess.id);
    }

    /** The chooser under "New session": empty (default), a copy, the examples. */
    _showSessionPicker(anchor) {
      var self = this;
      var old = this.sessionsBody.querySelector(".se-session-picker");
      if (old) { old.parentNode.removeChild(old); return; }
      var picker = h("div", { class: "se-session-picker", role: "listbox" });
      var choice = function (label, detail, start, isDefault) {
        var b = h("button", { type: "button", class: "se-choice" + (isDefault ? " se-choice-default" : ""), "data-start": start }, [
          h("span", { class: "se-choice-name" }, [label]), h("code", { class: "se-choice-src" }, [detail || ""])]);
        b.addEventListener("click", function () { self.newSession(start); });
        picker.appendChild(b);
      };
      choice("Empty formula", "type the expression in the source line", "empty", true);
      choice("Copy of the current expression", (this.state.src || "").slice(0, 60), "current", false);
      var examples = this.opts.examples || [];
      if (examples.length) picker.appendChild(h("div", { class: "se-choice-head" }, ["Examples"]));
      examples.forEach(function (ex) { choice(ex.name, ex.src, ex.srepr, false); });
      anchor.parentNode.insertBefore(picker, anchor.nextSibling);
      picker.querySelector(".se-choice-default").focus();
    }

    deleteSession(id) {
      var store = this._sessionStore || this._loadSessions();
      if (store.list.length < 2) return;                       // the last session stays
      var rest = store.list.filter(function (s) { return s.id !== id; });
      store.list = rest;
      this._saveSessions(store);
      if (id === store.current) {
        store.current = null;                                  // openSession() may then switch to it
        this._saveSessions(store);
        var latest = rest.slice().sort(function (a, b) { return b.updated - a.updated; })[0];
        this.openSession(latest.id);
      } else {
        this._fillSessions();
      }
    }

    _fillSessions() {
      if (!this.sessions) return;
      var self = this;
      var store = this._sessionStore || this._loadSessions();
      var body = this.sessionsBody;
      body.textContent = "";
      var list = store.list.slice().sort(function (a, b) { return b.updated - a.updated; });
      if (this.buttons.drawer) this.buttons.drawer.title = "Sessions (" + list.length + ") and history";
      list.forEach(function (sess) {
        var current = sess.id === store.current;
        var when = new Date(sess.updated || 0);
        var row = h("div", { class: "se-session" + (current ? " se-session-current" : ""), "data-id": sess.id });
        var head = h("div", { class: "se-session-row" });
        row.appendChild(head);
        head.appendChild(h("code", { title: sess.name }, [sess.name || "(new)"]));
        head.appendChild(h("span", { class: "se-session-when" }, [when.toLocaleDateString() + " " + when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })]));
        var open = h("button", { type: "button", "data-open": sess.id, title: "Open this session" }, [current ? "Current" : "Open"]);
        open.disabled = current || !self._sessionsReady;
        open.addEventListener("click", function () { self.openSession(sess.id); });
        head.appendChild(open);
        if (!current) {   // the whole row is the target (a phone has no room for aiming at a small button)
          row.setAttribute("role", "button");
          row.setAttribute("tabindex", "0");
          row.title = "Open this session";
          head.addEventListener("click", function (ev) { if (!ev.target.closest("button")) self.openSession(sess.id); });
          row.addEventListener("keydown", function (ev) { if (ev.key === "Enter" && ev.target === row) self.openSession(sess.id); });
        }
        var del = h("button", { type: "button", "data-delete": sess.id, title: "Delete this session (click twice)" }, ["Delete"]);
        del.disabled = list.length < 2;
        del.addEventListener("click", function () {
          if (del.getAttribute("data-armed")) { self.deleteSession(sess.id); return; }
          del.setAttribute("data-armed", "1");
          del.textContent = "Sure?";
          setTimeout(function () { del.removeAttribute("data-armed"); del.textContent = "Delete"; }, 3000);
        });
        head.appendChild(del);
        if (current) {   // the current session's card holds its sub-tabs: the history
          var steps = self._history && self._history.labels ? self._history.labels.length : 0;
          self.subtabs.querySelector("[data-tab=history]").textContent = "History" + (steps ? " (" + steps + ")" : "");
          row.appendChild(h("div", { class: "se-session-sub" }, [self.subtabs, self.historyPane]));
        }
        body.appendChild(row);
      });
      var add = h("button", { type: "button", class: "se-session-new", title: "Start a new session: an empty formula, a copy of this one, or an example" }, ["New session\u2026"]);
      add.disabled = !this._sessionsReady;
      var addRow = h("div", { class: "se-session se-session-add" }, [add]);
      add.addEventListener("click", function () { self._showSessionPicker(addRow); });
      body.appendChild(addRow);
      // The history of the current session: one row per step, the current one marked.
      var hist = this.historyBody;
      hist.textContent = "";
      var h_ = this._history;
      var tools = h("div", { class: "se-history-tools" });
      var report = h("button", { type: "button", class: "se-history-report", title: "View the whole history with what each step changed; save it as a web page or a Python script" }, ["View\u2026"]);
      report.addEventListener("click", function () { self.showHistory(); });
      tools.appendChild(report);
      hist.appendChild(tools);
      if (!h_ || !h_.labels) { hist.appendChild(h("div", { class: "se-step se-step-note" }, ["(open the drawer again after an edit)"])); return; }
      h_.labels.forEach(function (label, i) {
        var row = h("button", { type: "button", class: "se-step" + (i === h_.index ? " se-step-current" : ""), "data-index": String(i),
          title: i === h_.index ? "The current expression" : "Go to this step" }, [
          h("span", { class: "se-step-no" }, [String(i + 1)]),
          h("span", { class: "se-step-body" }, [
            h("span", { class: "se-step-formulas" }),     // filled by _renderHistory: previous (red) -> this (green)
            h("code", {}, [label]),
            h("span", { class: "se-step-action" }, [i === 0 ? "Start" : (h_.actions && h_.actions[i]) || "Edit"])
          ])
        ]);
        row.disabled = i === h_.index;
        row.addEventListener("click", function () { self.gotoStep(i); });
        hist.appendChild(row);
      });
      this._renderHistory();
    }

    /* ---- full screen ---- */

    /** Give the formula the whole window: the editor becomes a fixed panel
     *  over the page, the editing area takes all the height that is left and
     *  the formula is drawn larger.  Everything else on the page is simply
     *  covered.  The corner button and Esc (when nothing is selected) come
     *  back. */
    setFullscreen(on) {
      on = !!on;
      if (on === this.fullscreen) return;
      this.fullscreen = on;
      this.root.classList.toggle("se-full", on);
      if (this.fullBtn) {
        this.fullBtn.innerHTML = expandSvg(on);
        var title = on ? "Leave full screen (Esc)"
                       : "Full screen: the formula alone, as large as the window";
        this.fullBtn.setAttribute("title", title);
        this.fullBtn.setAttribute("aria-label", title);
      }
      // the view changed size: the overlay boxes and the caret are placed in
      // pixels, so they have to be measured again
      this._gapCache = null;
      if (this.caret) this._hideCaret();
      this._applySelection();
      this._setStatus(on ? "Full screen - Esc or the corner button comes back" : "");
    }

    /* ---- zoom and sideways scrolling ---- */

    _initialZoom() {
      var z = this.opts.zoom;
      if (this.opts.rememberZoom) {
        try { var saved = parseFloat(localStorage.getItem(ZOOM_KEY)); if (saved > 0) z = saved; } catch (e) { /* no storage */ }
      }
      return z;
    }

    _applyZoom(zoom) {
      var o = this.opts;
      zoom = Math.max(o.minZoom, Math.min(o.maxZoom, +zoom || 1));
      this.zoom = Math.round(zoom * 1000) / 1000;
      this.view.style.setProperty("--se-zoom", String(this.zoom));   // the CSS multiplies the base font size by it
      if (this.buttons.zoomreset) this.buttons.zoomreset.textContent = Math.round(this.zoom * 100) + "%";
    }

    /** Magnify the formula to `zoom` (1 = the CSS size), keeping the content
     *  under viewport x `anchorX` (default: the middle of the view) in place. */
    setZoom(zoom, anchorX) {
      var vr = this.view.getBoundingClientRect();
      var ax = (anchorX === undefined ? vr.left + vr.width / 2 : anchorX) - vr.left;
      var content = this.view.scrollLeft + ax;
      var old = this.zoom;
      this._applyZoom(zoom);
      if (this.zoom === old) return;
      this.view.scrollLeft = content * this.zoom / old - ax;
      this._gapCache = null;
      if (this.caret) this._hideCaret();
      this._applySelection();
      this._updateToolbar();
      if (this.opts.rememberZoom) {
        try { localStorage.setItem(ZOOM_KEY, String(this.zoom)); } catch (e) { /* no storage */ }
      }
    }

    _pointerSpread() {
      var pts = Object.keys(this._pointers).map(function (id) { return this._pointers[id]; }, this);
      if (pts.length < 2) return 1;
      return Math.max(1, Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y));
    }

    _pointerCentre() {
      var pts = Object.keys(this._pointers).map(function (id) { return this._pointers[id]; }, this);
      return pts.length ? pts.reduce(function (sum, p) { return sum + p.x; }, 0) / pts.length : undefined;
    }

    _endPan() {
      if (this._pan && this.view.releasePointerCapture && this._pan.id !== undefined) {
        try { this.view.releasePointerCapture(this._pan.id); } catch (e) { /* not captured */ }
      }
      this._pan = null;
      this.view.classList.remove("se-panning");
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
      set("delete", dis || !(range || this.selected || this.junction));
      set("unwrap", dis || range || !this.selected || !(s.nodes && s.nodes[this.selected] && (s.nodes[this.selected].nargs || s.nodes[this.selected].parts)));
      set("isolate", dis || !(range || (this.selected && this.selected !== "/")));
      set("parent", dis || !(range || (t && t.parent) || this.caret));
      set("child", dis || !!this.caret);
      // ←/→: at a caret, the previous/next position (none at the ends); on a
      // selection, a sibling at some level; otherwise a caret at either end.
      var at = !dis && this.caret ? this._caretIndex() : null;
      set("left", dis || (at ? at.index <= 0 : (this.selected && !range ? !this._sidewaysTarget(this.selected, -1) : false)));
      set("right", dis || (at ? at.index >= at.count - 1 : (this.selected && !range ? !this._sidewaysTarget(this.selected, 1) : false)));
      set("copy", !s.src);
      set("paste", dis);
      set("history", dis);
      set("zoomin", this.zoom >= this.opts.maxZoom);
      set("zoomout", this.zoom <= this.opts.minZoom);
      set("zoomreset", this.zoom === 1);
      if (this.fnInput) this.fnInput.disabled = dis;
      set("finish", dis);
      if (this.opsSelect) this.opsSelect.disabled = dis;
      if (this.typeMenu) this.typeMenu.disabled = dis;
    }

    /** Remove the editor from the page. */
    destroy() {
      this.closeDrawer();
      (this._docListeners || []).forEach(function (l) { document.removeEventListener(l[0], l[1]); });
      this._docListeners = [];
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
      },
      /** Stop the message being processed: its request then answers with the error. */
      interrupt: async function () {
        var r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-SymPy-Editor-Token": cfg.token || "" },
          body: JSON.stringify({ action: "interrupt" })
        });
        return r.ok ? (await r.json()).interrupted : false;
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

  var PYODIDE_DIR = "/sympy_editor_pkg/sympy_editor";

  // The worker's script: Pyodide, SymPy and the Documents live in a worker
  // thread, so a long computation leaves the page responsive and can be
  // stopped by terminating the worker (see pyodideRuntime).
  var PYODIDE_WORKER = [
    "var newDoc = null, handle = null;",
    "self.onmessage = async function (e) {",
    "  var m = e.data;",
    "  try {",
    "    if (m.type === 'init') {",
    "      self.postMessage({ type: 'progress', text: 'Loading Python runtime (Pyodide)…' });",
    "      importScripts(m.pyodideJs);",
    "      var py = await self.loadPyodide({ indexURL: m.indexURL });",
    "      self.postMessage({ type: 'progress', text: 'Loading SymPy…' });",
    "      if (m.sympyWheel) { await py.loadPackage('mpmath'); await py.loadPackage(m.sympyWheel); }",
    "      else await py.loadPackage('sympy');",
    "      py.FS.mkdirTree(m.dir);",
    "      for (var name in m.sources) py.FS.writeFile(m.dir + '/' + name, m.sources[name]);",
    "      py.runPython(m.boot);",
    "      newDoc = py.globals.get('__sympy_editor_new');",
    "      handle = py.globals.get('__sympy_editor_handle');",
    "      self.postMessage({ type: 'done', req: m.req });",
    "    } else if (m.type === 'newDoc') {",
    "      newDoc(m.id, m.srepr, m.settings);",
    "      self.postMessage({ type: 'done', req: m.req });",
    "    } else if (m.type === 'handle') {",
    "      self.postMessage({ type: 'done', req: m.req, json: handle(m.id, m.msg) });",
    "    }",
    "  } catch (err) {",
    "    self.postMessage({ type: 'error', req: m.req, message: String((err && err.message) || err) });",
    "  }",
    "};",
    ""
  ].join("\n");

  function interruptedError() {
    var e = new Error("Interrupted: Python was stopped and restarts (the undo history is gone)");
    e.interrupted = true;
    return e;
  }

  /** Pyodide loaded in the page itself (the fallback when a worker cannot be
   *  created, e.g. by Chrome for a file:// page): no interruption possible. */
  async function pyodideInPage(cfg, report) {
    report("Loading Python runtime (Pyodide)…");
    if (typeof window.loadPyodide !== "function") await loadScript(cfg.pyodideJs);
    var py = await window.loadPyodide({ indexURL: new URL(cfg.pyodideIndex, document.baseURI).href });
    report("Loading SymPy…");
    if (cfg.sympyWheel) { await py.loadPackage("mpmath"); await py.loadPackage(new URL(cfg.sympyWheel, document.baseURI).href); }
    else await py.loadPackage("sympy");
    py.FS.mkdirTree(PYODIDE_DIR);
    for (var name in cfg.sources) py.FS.writeFile(PYODIDE_DIR + "/" + name, cfg.sources[name]);
    py.runPython(PYODIDE_BOOT);
    return { newDoc: py.globals.get("__sympy_editor_new"), handle: py.globals.get("__sympy_editor_handle") };
  }

  /** One Python runtime (a worker, or the page) holding the Documents of
   *  every editor that shares it.  `interrupt()` terminates the worker; the
   *  next request starts a new one and re-creates the Documents from their
   *  last committed state (`docs`), so only the undo history is lost. */
  function makeRuntime(cfg) {
    var rt = { docs: {}, worker: null, ready: null, inPage: null, pending: {}, req: 0, report: function () {} };

    function post(msg) {
      return new Promise(function (resolve, reject) {
        var id = ++rt.req;
        rt.pending[id] = { resolve: resolve, reject: reject };
        rt.worker.postMessage(Object.assign({ req: id }, msg));
      });
    }

    function failAll(err) {
      var pending = rt.pending;
      rt.pending = {};
      for (var k in pending) pending[k].reject(err);
    }

    function spawn() {
      var worker;
      try {
        worker = new Worker(URL.createObjectURL(new Blob([PYODIDE_WORKER], { type: "text/javascript" })));
      } catch (e) {
        return null;
      }
      worker.onmessage = function (e) {
        var m = e.data;
        if (m.type === "progress") { rt.report(m.text); return; }
        var p = rt.pending[m.req];
        if (!p) return;
        delete rt.pending[m.req];
        if (m.type === "error") p.reject(new Error(m.message)); else p.resolve(m.json);
      };
      worker.onerror = function (e) { failAll(new Error((e && e.message) || "The Python worker failed")); };
      return worker;
    }

    rt.start = function (report) {
      if (report) rt.report = report;
      if (rt.ready) return rt.ready;
      rt.ready = (async function () {
        rt.worker = spawn();
        if (rt.worker) {
          try {
            await post({ type: "init", pyodideJs: new URL(cfg.pyodideJs, document.baseURI).href,
              indexURL: new URL(cfg.pyodideIndex, document.baseURI).href,
              sympyWheel: cfg.sympyWheel ? new URL(cfg.sympyWheel, document.baseURI).href : "",
              dir: PYODIDE_DIR, sources: cfg.sources, boot: PYODIDE_BOOT });
            return;
          } catch (e) {
            if (window.console) console.warn("sympy-editor: Python could not start in a worker, using the page instead.", e);
            rt.worker.terminate();
            rt.worker = null;
          }
        }
        rt.inPage = await pyodideInPage(cfg, rt.report);
      })().catch(function (e) { rt.ready = null; throw e; });
      return rt.ready;
    };

    rt.canInterrupt = function () { return !!rt.worker; };

    rt.interrupt = function () {
      if (!rt.worker) return false;
      var worker = rt.worker;
      rt.worker = null;
      rt.ready = null;
      for (var id in rt.docs) rt.docs[id].created = false;
      worker.terminate();
      failAll(interruptedError());
      return true;
    };

    rt.newDoc = function (id, srepr, settings) {
      rt.docs[id] = { srepr: srepr, settings: settings || {}, declared: null, last: null, created: false };
      return rt.ensureDoc(id);
    };

    rt.ensureDoc = async function (id) {
      var d = rt.docs[id];
      if (d.created) return;
      var settings = Object.assign({}, d.settings);
      if (d.created === false && d.last) { delete settings.history; delete settings.index; }   // re-created: from its last state
      if (d.declared) settings.symbols = d.declared;
      if (rt.inPage) rt.inPage.newDoc(id, d.srepr, JSON.stringify(settings));
      else await post({ type: "newDoc", id: id, srepr: d.srepr, settings: JSON.stringify(settings) });
      d.created = true;
    };

    rt.handle = async function (id, msgJson) {
      await rt.start();          // a new worker after an interruption
      await rt.ensureDoc(id);
      var json = rt.inPage ? rt.inPage.handle(id, msgJson) : await post({ type: "handle", id: id, msg: msgJson });
      var snap = JSON.parse(json);
      var d = rt.docs[id];
      if (!snap.preview && snap.srepr) { d.srepr = snap.srepr; d.declared = snap.declared || null; d.last = snap; }
      return snap;
    };
    return rt;
  }

  /** The runtime of the page for these URLs (cached on window: each embedded
   *  fragment may carry its own copy of this script). */
  function pyodideRuntime(cfg, report) {
    var shared = window.__sympyEditorPyodide || (window.__sympyEditorPyodide = { runtimes: {}, docs: 0 });
    var key = cfg.pyodideIndex || cfg.pyodideJs || "default";
    if (!shared.runtimes[key]) shared.runtimes[key] = makeRuntime(cfg);
    var rt = shared.runtimes[key];
    return rt.start(report).then(function () { return rt; });
  }

  /** Run the Python Document in the browser with Pyodide (in a worker, loaded
   *  once per page).  cfg: {pyodideJs, pyodideIndex, sources, srepr, document}. */
  function pyodideBackend(cfg) {
    var rt = null, id = null, ready = null;
    function start(report) {
      if (!ready) {
        ready = (async function () {
          rt = await pyodideRuntime(cfg, report);
          id = "doc" + (++window.__sympyEditorPyodide.docs);
          await rt.newDoc(id, cfg.srepr, cfg.document || {});
          report("");
        })().catch(function (e) { ready = null; throw e; });
      }
      return ready;
    }
    return {
      send: async function (msg, report) {
        await start(report || function () {});
        try {
          return await rt.handle(id, JSON.stringify(msg));
        } catch (e) {
          if (!e.interrupted) throw e;
          // The document comes back from its last committed state at the next
          // request; meanwhile, that state with the error and no history.
          var last = rt.docs[id].last || cfg.snapshot;
          return Object.assign({}, last, { error: e.message, can_undo: false, can_redo: false, seq: (last.seq || 0) + 1 });
        }
      },
      canInterrupt: function () { return !!rt && rt.canInterrupt(); },
      interrupt: function () { return !!rt && rt.interrupt(); },
      /** Switch to a document built from `state` (Document kwargs: history,
       *  index, symbols - a session), returning its snapshot. */
      openDocument: async function (state, report) {
        await start(report || function () {});
        id = "doc" + (++window.__sympyEditorPyodide.docs);
        var history = state && state.history;
        var srepr = history && history.length ? history[Math.min(state.index || 0, history.length - 1)] : cfg.srepr;
        await rt.newDoc(id, srepr, Object.assign({}, cfg.document || {}, state || {}));
        return rt.handle(id, JSON.stringify({ action: "snapshot" }));
      },
      /** Load the runtime now (page load) instead of at the first edit. */
      warmup: function (report) { return start(report).then(function () { report(""); }, function (e) { report("Python failed to load: " + e.message); }); }
    };
  }

  /** The host application's own Python: the page hands JSON messages to the
   *  object it injected (`window.SympyEditorPy`) and gets snapshots back
   *  through `window.__sympyEditorNative`.  The Android app runs CPython and
   *  SymPy natively (mobile/android, Chaquopy) - nothing to download, no
   *  WebAssembly, and the same Document code as everywhere else. */
  function nativeBackend(cfg) {
    var pending = {}, seq = 0, docId = null, started = null;
    window.__sympyEditorNative = function (req, ok, payload) {
      var p = pending[req];
      if (!p) return;
      delete pending[req];
      if (!ok) { p.reject(new Error(payload)); return; }
      try { p.resolve(JSON.parse(payload)); } catch (e) { p.reject(e); }
    };
    function call(method, args) {
      return new Promise(function (resolve, reject) {
        var host = window.SympyEditorPy;
        if (!host || typeof host[method] !== "function") {
          reject(new Error("This page needs the app's Python (window.SympyEditorPy)"));
          return;
        }
        var req = "r" + (++seq);
        pending[req] = { resolve: resolve, reject: reject };
        try {
          host[method].apply(host, [req].concat(args));
        } catch (e) {
          delete pending[req];
          reject(e);
        }
      });
    }
    function newDoc(srepr, state) {
      docId = "doc" + (++seq);
      return call("newDoc", [docId, srepr, JSON.stringify(Object.assign({}, cfg.document || {}, state || {}))]);
    }
    function start(report) {
      if (!started) {
        report("Starting Python\u2026");
        started = newDoc(cfg.srepr, null).then(function (snap) { report(""); return snap; },
                                               function (e) { started = null; throw e; });
      }
      return started;
    }
    return {
      send: async function (msg, report) {
        await start(report || function () {});
        return call("handle", [docId, JSON.stringify(msg)]);
      },
      /** Switch to a document built from `state` (a session), as Pyodide does. */
      openDocument: async function (state, report) {
        await start(report || function () {});
        var history = state && state.history;
        var srepr = history && history.length ? history[Math.min(state.index || 0, history.length - 1)] : cfg.srepr;
        return newDoc(srepr, state);
      },
      warmup: function (report) {
        return start(report).then(function () { report(""); },
                                  function (e) { report("Python failed to start: " + e.message); });
      }
    };
  }

  function readonlyBackend() {
    return {
      send: async function () { throw new Error("This view is read-only."); }
    };
  }

  var backends = { http: httpBackend, pyodide: pyodideBackend, native: nativeBackend, readonly: readonlyBackend };

  /** Create an editor from a config object produced by html.py. */
  function mount(host, cfg) {
    var make = backends[cfg.backend] || readonlyBackend;
    var options = Object.assign({}, cfg.options || {});
    if (cfg.backend === "readonly") options.readOnly = true;
    if (cfg.examples) options.examples = cfg.examples;     // what a new session can start from
    var backend = make(cfg);
    var editor = new Editor(host, backend, options);
    editor.setState(cfg.snapshot).then(function () {
      var warm = Promise.resolve();
      if (backend.warmup && editor.opts.preload !== false) {
        editor._showLoading("Loading Python runtime…");
        warm = backend.warmup(function (text) { editor._report(text); }).then(function () {
          editor._hideLoading();
          if (backend.canInterrupt && !backend.canInterrupt()) editor._setStatus("Python runs in the page (no worker): long computations cannot be interrupted here");
        });
      }
      warm.then(function () { return editor._initSessions(); });
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
