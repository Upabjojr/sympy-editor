/*
 * SPDX-License-Identifier: AGPL-3.0-or-later
 * Copyright (c) 2026 Francesco Bonazzi
 *
 * sympy-editor add-on "handwriting": write a formula by hand.
 *
 * A writing area under the formula: a box of its own size that scrolls, over a
 * canvas that grows to the right and down as the ink nears those edges, with a
 * small button in the middle of each edge there is more ink beyond (the rest
 * of the edge still writes).  Each stroke is kept as points [x, y, t] (canvas pixels, milliseconds
 * from the first stroke); a pause after the pen lifts sends them to Python
 * (method "recognize"), where math-ocr's stroke model reads them.  The
 * readings come back as LaTeX, best first, each with what SymPy makes of it
 * and the options of that reading - an ambiguity's alternatives, a constant's
 * switch.  The pad holds the whole formula: Apply makes it the editor's.  Every
 * edit in the pad - ink, selections, typing - can be taken back and put back.  Two fingers never write:
 * they pinch the area to zoom it and drag it to scroll, and the strokes keep
 * the canvas's own coordinates whatever the zoom.  In full screen the panel
 * covers the page: the tools on top, the writing area, and the readings in a
 * sheet at the bottom that folds away.
 *
 * The pad is also a LaTeX editor: the LaTeX in the box is drawn in it, under
 * the ink, and each piece of the drawing knows the piece of the text it came
 * from (LatexMap).  It opens with the editor's formula.  A tap selects a
 * piece; writing over the selection makes it a hole - the piece gone from
 * sight, the room it took left to write in, growing as the ink nears its
 * edges - and the reading of the ink takes that piece's place in the text.
 * Written anywhere else, the ink is free: its reading goes after the formula.
 * The selected piece can be typed over too, in the LaTeX line above the pad.
 */
SympyEditor.registerAddon("handwriting", (function () {
  // Bring an element into sight, gently unless motion is to be kept down;
  // "end": what lies above it stays in sight when it fits.
  function reveal(el, block) {
    if (!el || !el.scrollIntoView) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    try { el.scrollIntoView({ block: block || "nearest", behavior: reduce ? "auto" : "smooth" }); }
    catch (e) { el.scrollIntoView(false); }
  }

  // The editor's icons (editor.js: expandSvg, chevronSvg), drawn the same way.
  function expandIcon(full) {
    var out = "M2.8 6.2V2.8h3.4M9.8 2.8h3.4v3.4M13.2 9.8v3.4H9.8M6.2 13.2H2.8V9.8";
    var back = "M6.2 2.8v3.4H2.8M13.2 6.2H9.8V2.8M9.8 13.2V9.8h3.4M2.8 9.8h3.4v3.4";
    return '<svg class="ink-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
      '<path fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" d="' +
      (full ? back : out) + '"/></svg>';
  }
  // The editor's arrow (editor.js: arrowSvg): one icon, rotated four ways.
  function arrowIcon(dir, size) {
    var deg = { up: 0, right: 90, down: 180, left: 270 }[dir];
    var dims = size ? ' width="' + size + '" height="' + size + '" style="vertical-align: -0.2em"' : "";
    return '<svg class="ink-icon" viewBox="0 0 16 16"' + dims + ' aria-hidden="true" focusable="false">' +
      '<g transform="rotate(' + deg + ' 8 8)" fill="none" stroke="currentColor" stroke-width="1.7" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M8 13.2V3.2"/><path d="M3.9 7.3 8 3.2l4.1 4.1"/></g></svg>';
  }
  function chevronIcon(dir) {
    var deg = { up: 0, right: 90, down: 180, left: 270 }[dir];
    return '<svg class="ink-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
      '<path transform="rotate(' + deg + ' 8 8)" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round" d="M3.5 10.2 8 5.7l4.5 4.5"/></svg>';
  }
  // Ink nearing the right or bottom edge makes room beyond it: within a
  // quarter of the box's width (height) of it, on screen, and never less than
  // EDGE px - early enough that the pen need not reach the very edge first.
  // The tools' icons, drawn as the editor draws its own (16 x 16, the text's
  // colour, round ends): a glyph would come from whichever font has it.
  var TOOL_PATHS = {
    undo: "M6 3.4 2.9 6.5 6 9.6M3.2 6.5h6.4a3.7 3.7 0 0 1 0 7.4H6.8",
    redo: "M10 3.4l3.1 3.1L10 9.6M12.8 6.5H6.4a3.7 3.7 0 0 0 0 7.4h2.8",
    erase: "M2.7 10.3 8.9 4.1a1.3 1.3 0 0 1 1.8 0l2.3 2.3a1.3 1.3 0 0 1 0 1.8l-5 5H5.4ZM5.8 7.2l4 4M7.9 13.2h5.6",
    clear: "M2.9 4.5h10.2M6.2 4.5V3.1h3.6v1.4M4.3 4.5l.7 8.7a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.7-8.7M6.9 6.9v4.7M9.1 6.9v4.7",
    read: "M3 2.8h10a1.2 1.2 0 0 1 1.2 1.2v8a1.2 1.2 0 0 1-1.2 1.2H3A1.2 1.2 0 0 1 1.8 12V4A1.2 1.2 0 0 1 3 2.8ZM5.2 5.5h5.6M8 5.5v5.2",
    pen: "M3.2 12.8l.9-3.3 7.1-7.1a1.5 1.5 0 0 1 2.1 2.1l-7.1 7.1ZM9.9 3.3l2.8 2.8",
    select: "M4.2 2.6v10.1l2.7-2.5 1.9 4.1 1.8-.8-1.9-4.1 3.7-.3Z",
    done: "M3.2 8.6l3.1 3.1 6.5-7",
    type: "M2.2 4.5h11.6v7H2.2ZM4.6 6.9h.01M6.9 6.9h.01M9.1 6.9h.01M11.4 6.9h.01M5.4 9.2h5.2",
    load: "M8 2.5v6.8M4.9 6.3 8 9.3l3.1-3M3 12.8h10"
  };
  // `size`: pixels, for the guide - which the panel's stylesheet does not reach
  function toolIcon(name, size) {
    var d = name === "full" ? "M2.8 6.2V2.8h3.4M9.8 2.8h3.4v3.4M13.2 9.8v3.4H9.8M6.2 13.2H2.8V9.8" : TOOL_PATHS[name];
    var dims = size ? ' width="' + size + '" height="' + size + '" style="vertical-align: -0.2em"' : "";
    return '<svg class="ink-icon" viewBox="0 0 16 16"' + dims + ' aria-hidden="true" focusable="false">' +
      '<path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" d="' + d + '"/></svg>';
  }
  // The model's NOTICE, as text in the guide (never as markup)
  function noticeHtml(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function toolButton(h, name, title, extra) {
    var b = h("button", Object.assign({ type: "button", class: "ink-tool ink-" + name, title: title, "aria-label": title }, extra || {}));
    b.innerHTML = toolIcon(name);
    return b;
  }
  // ---- the LaTeX in the pad: which piece of the text each rendered piece is ----
  var LatexMap = (function () {
    var HOLE = "\u0001";
    // operators, relations and punctuation keep KaTeX's spacing: never wrapped
    var BARE = /^(?:[+\-=<>,;:!?|()\[\]/*.']|\\(?:cdot|times|pm|mp|div|le|leq|ge|geq|ne|neq|approx|to|rightarrow|equiv|sim|in|notin|subset|cup|cap|wedge|vee|,|;|:|!| |quad|qquad|ldots|cdots|dots|mid))$/;
    var ARGS = { "\\frac": 2, "\\dfrac": 2, "\\tfrac": 2, "\\binom": 2, "\\sqrt": 1, "\\overline": 1, "\\underline": 1,
                 "\\hat": 1, "\\bar": 1, "\\vec": 1, "\\dot": 1, "\\ddot": 1, "\\tilde": 1, "\\widehat": 1, "\\widetilde": 1 };
    var OPAQUE = { "\\mathrm": 1, "\\mathbf": 1, "\\mathit": 1, "\\mathbb": 1, "\\mathcal": 1, "\\mathfrak": 1, "\\mathsf": 1,
                   "\\mathtt": 1, "\\boldsymbol": 1, "\\operatorname": 1, "\\text": 1, "\\textrm": 1, "\\textbf": 1, "\\textit": 1 };
    // big operators take their limits above and below only as themselves: never wrapped alone
    var BIGOP = /^\\(?:sum|prod|coprod|int|iint|iiint|oint|bigcup|bigcap|bigoplus|bigotimes|bigvee|bigwedge|lim|limsup|liminf|max|min|sup|inf)$/;
    var FUNCS = /^\\(?:a?(?:sin|cos|tan|cot|sec|csc)h?|arc(?:sin|cos|tan)|log|ln|lg|exp|det|arg|deg|dim|gcd|ker)$/;

    function parse(text) {
      var i = 0, n = text.length;
      function fail(why) { throw new Error(why + " at " + i); }
      function skip() { while (i < n && /\s/.test(text[i])) i++; }
      function token() {
        var s = i, c = text[i];
        if (c === "\\") {
          if (/[A-Za-z]/.test(text[i + 1] || "")) { i += 2; while (i < n && /[A-Za-z]/.test(text[i])) i++; }
          else i = Math.min(n, i + 2);
        } else if (/[0-9]/.test(c)) { i++; while (i < n && /[0-9.]/.test(text[i])) i++; }
        else i++;
        return { s: s, e: i, t: text.slice(s, i) };
      }
      function isCmd(at, name) { return text.startsWith(name, at) && !/[A-Za-z]/.test(text[at + name.length] || ""); }
      // {...}: not wrapped itself (its braces belong to whoever holds it); what is inside is
      function group() {
        var open = i; i++;
        var kids = seq("}");
        if (text[i] !== "}") fail("missing }");
        i++;
        return { s: open, e: i, kids: [inner(open + 1, i - 1, kids)] };
      }
      function inner(s, e, kids) {
        if (kids.length === 1 && kids[0].s === s && kids[0].e === e) return kids[0];   // one piece: it wraps itself
        return { s: s, e: e, kids: kids, wrap: kids.length > 0 };
      }
      function arg() {                          // a command's argument or a script: a group, or one token (braced when written out)
        skip();
        if (i >= n) fail("missing argument");
        if (text[i] === "{") return group();
        if (text[i] === "}") fail("missing argument");
        var t = /[0-9]/.test(text[i]) ? (i++, { s: i - 1, e: i, t: text[i - 1] }) : token();     // \frac12: one digit each
        return { s: t.s, e: t.e, kids: [], wrap: t.t !== HOLE, hole: t.t === HOLE, braces: true };
      }
      function atom() {
        skip();
        var s = i, c = text[i];
        if (c === "{") return group();
        if (isCmd(i, "\\left")) {
          i += 5; skip(); token();
          var kids = seq("\\right");
          if (!isCmd(i, "\\right")) fail("missing \\right");
          i += 6; skip(); if (i < n) token();
          return { s: s, e: i, kids: kids, wrap: true };
        }
        if (isCmd(i, "\\begin")) {
          var m = /^\\begin\s*\{([^}]*)\}/.exec(text.slice(i));
          if (!m) fail("bad \\begin");
          var depth = 0, re = new RegExp("\\\\(begin|end)\\s*\\{" + m[1].replace(/[*]/g, "\\*") + "\\}", "g");
          re.lastIndex = i;
          for (var mm; (mm = re.exec(text));) {
            depth += mm[1] === "begin" ? 1 : -1;
            if (!depth) { i = re.lastIndex; return { s: s, e: i, kids: [], wrap: true }; }
          }
          fail("missing \\end");
        }
        var t = token();
        if (t.t === HOLE) return { s: s, e: i, kids: [], hole: true };
        if (ARGS[t.t] || OPAQUE[t.t]) {
          var args = [];
          skip();
          if (t.t === "\\sqrt" && text[i] === "[") {        // the index
            var open = i; i++;
            var ik = seq("]");
            if (text[i] !== "]") fail("missing ]");
            i++;
            args.push(inner(open + 1, i - 1, ik));
          }
          for (var k = 0; k < (ARGS[t.t] || 1); k++) args.push(arg());
          return { s: s, e: i, kids: OPAQUE[t.t] ? [] : args, wrap: true };
        }
        if (FUNCS.test(t.t)) {                              // a function takes what it applies to along
          var scripts = scriptsAfter();
          skip();
          var a = (i < n && text[i] !== "}" && text[i] !== "]" && !isCmd(i, "\\right")) ? atom() : null;
          return { s: s, e: a ? a.e : i, kids: a ? scripts.concat([a]) : scripts, wrap: true };
        }
        return { s: s, e: i, kids: [], wrap: !BARE.test(t.t) && !BIGOP.test(t.t) };
      }
      function scriptsAfter() {                 // ^ and _ after a base; i left just past the last one
        var out = [];
        for (;;) {
          var at = i;
          skip();
          if (i < n && (text[i] === "^" || text[i] === "_")) { i++; out.push(arg()); }
          else { i = at; return out; }
        }
      }
      function seq(stop) {
        var out = [];
        for (;;) {
          skip();
          if (i >= n) break;
          if (stop === "}" && text[i] === "}") break;
          if (stop === "]" && text[i] === "]") break;
          if (stop === "\\right" && isCmd(i, "\\right")) break;
          if (text[i] === "}") fail("unexpected }");
          var base = null;
          if (text[i] !== "^" && text[i] !== "_") base = atom();
          var scripts = scriptsAfter();
          if (scripts.length) out.push({ s: base ? base.s : scripts[0].s - 1, e: i, kids: (base ? [base] : []).concat(scripts), wrap: true });
          else out.push(base);
        }
        return out;
      }
      var kids = seq(null);
      if (i < n) fail("unexpected " + text[i]);
      return { s: 0, e: n, kids: kids, wrap: false };
    }
    function emit(text, node, holeTex) {
      if (node.hole) return node.braces ? "{" + holeTex + "}" : holeTex;
      var out = "", at = node.s;
      node.kids.forEach(function (k) { out += text.slice(at, k.s) + emit(text, k, holeTex); at = k.e; });
      out += text.slice(at, node.e);
      if (node.wrap) out = "\\htmlData{ls=" + node.s + ",le=" + node.e + "}{" + out + "}";
      if (node.braces) out = "{" + out + "}";
      return out;
    }
    // text with each piece wrapped; a HOLE character in it becomes holeTex.  null: not parsed.
    function annotate(text, holeTex) {
      try { return emit(text, parse(text), holeTex || ""); } catch (e) { return null; }
    }
    // the piece of text from s to e, as parsed: whether it is a bare argument (written without braces)
    function nodeAt(text, s, e) {
      var root, found = null;
      try { root = parse(text); } catch (err) { return null; }
      (function walk(nd) {
        if (found) return;
        if (nd !== root && nd.s === s && nd.e === e && (nd.wrap || nd.hole)) { found = nd; return; }
        nd.kids.forEach(walk);
      })(root);
      return found;
    }
    return { HOLE: HOLE, parse: parse, annotate: annotate, nodeAt: nodeAt };
  })();

  var EDGE = 60;
  var MIN_ZOOM = 0.5, MAX_ZOOM = 4;
  var MAX_W = 6000, MAX_H = 4000;   // px: as far as the canvas grows

  return {
    mount: function (api) {
      var h = api.h;
      var status = (api.options && api.options.status) || { available: false, reason: "The add-on's Python said nothing about its model" };
      var canRead = !!status.available;

      // ---- the parts ------------------------------------------------------------
      var canvas = h("canvas", { class: "ink-canvas", "aria-label": "Writing area: write a formula with a pen, a finger or the mouse" });
      var formula = h("div", { class: "ink-formula", "aria-hidden": "true" });     // the LaTeX in the box, under the ink
      var layer = h("div", { class: "ink-layer" }, [formula, canvas]);
      var pad = h("div", { class: "ink-pad", tabindex: "0" }, [layer]);      // it takes the arrow keys, as the formula does
      var fullBtn = h("button", { type: "button", class: "ink-fullbtn" });
      var stage = h("div", { class: "ink-stage" }, [pad, fullBtn]);
      var strips = {};
      ["left", "right", "up", "down"].forEach(function (dir) {
        var title = "Scroll the writing area " + dir;
        var b = h("button", { type: "button", class: "ink-scroll ink-scroll-" + dir, hidden: "", title: title, "aria-label": title, tabindex: "-1" });
        b.innerHTML = chevronIcon(dir);
        b.addEventListener("click", function (ev) { ev.preventDefault(); scrollPage(dir); });
        strips[dir] = b;
        stage.appendChild(b);
      });
      var selectBtn = toolButton(h, "select", "Select: a tap selects a piece of the formula, a second tap what holds it", { "aria-pressed": "true" });
      var penBtn = toolButton(h, "pen", "Pen: writes - with a piece selected, in its place; with the cursor, there; with neither, after the formula", { "aria-pressed": "false" });
      var eraseBtn = toolButton(h, "erase", "Eraser: the strokes the pointer passes over go", { "aria-pressed": "false" });
      var modes = h("div", { class: "ink-modes", role: "group", "aria-label": "What a tap or a stroke on the pad does" }, [selectBtn, penBtn, eraseBtn]);
      // the formula panel's arrows, in its order and words
      var arrowBtns = {};
      [["up", "Select the enclosing piece (\u2191)"],
       ["down", "Select inside: the piece you came from, or the first one; on a piece with nothing inside, a cursor after it (\u2193)"],
       ["left", "Select the previous piece beside it, or move the cursor left (\u2190)"],
       ["right", "Select the next piece beside it, or move the cursor right (\u2192)"]].forEach(function (a) {
        var b = h("button", { type: "button", class: "ink-arrow ink-arrow-" + a[0], title: a[1], "aria-label": a[1] });
        b.innerHTML = arrowIcon(a[0]);
        arrowBtns[a[0]] = b;
      });
      var arrows = h("div", { class: "ink-arrows", role: "group", "aria-label": "Moving the selection" },
                     [arrowBtns.up, arrowBtns.down, arrowBtns.left, arrowBtns.right]);
      var doneBtn = toolButton(h, "done", "Done: the reading of the ink into the formula, and the ink goes", { disabled: "" });
      var undoBtn = toolButton(h, "undo", "Undo: take back the last edit - ink, selection, typing", { disabled: "" });
      var redoBtn = toolButton(h, "redo", "Redo: put back what Undo took", { disabled: "" });
      var clearBtn = toolButton(h, "clear", "Clear: take all the ink away (Undo brings it back)", { disabled: "" });
      var readBtn = toolButton(h, "read", "Read: read what is written now");
      var zoomOutBtn = h("button", { type: "button", class: "ink-zoom-out", title: "Zoom out (Ctrl+wheel, pinch)", "aria-label": "Zoom out" }, ["\u2212"]);
      var zoomLevelBtn = h("button", { type: "button", class: "ink-zoom-level", title: "Reset the zoom", "aria-label": "Reset the zoom" }, ["100%"]);
      var zoomInBtn = h("button", { type: "button", class: "ink-zoom-in", title: "Zoom in (Ctrl+wheel, pinch)", "aria-label": "Zoom in" }, ["+"]);
      var zooms = h("div", { class: "ink-zooms", role: "group", "aria-label": "Zoom" }, [zoomOutBtn, zoomLevelBtn, zoomInBtn]);
      var bar = h("div", { class: "ink-bar" }, [modes, arrows, doneBtn, undoBtn, redoBtn, clearBtn, readBtn, zooms]);

      var note = h("div", { class: "ink-note", "aria-live": "polite" });
      var cands = h("div", { class: "ink-cands", role: "listbox", "aria-label": "Readings, best first" });
      var field = h("input", { class: "ink-latex", type: "text", spellcheck: "false", autocomplete: "off", autocapitalize: "off",
        placeholder: "LaTeX - type here, or write in the pad below", "aria-label": "The formula as LaTeX" });
      var typeBtn = toolButton(h, "type", "Type over the selected piece (its LaTeX, selected in this line) - or at the cursor", { disabled: "" });
      var loadBtn = toolButton(h, "load", "The editor's formula, into the pad");
      var latexRow = h("div", { class: "ink-latexrow" }, [field, typeBtn, loadBtn]);
      var readingOf = h("div", { class: "ink-reading-of" });
      var nestRow = h("div", { class: "ink-nest", role: "group", "aria-label": "What the ink is read together with" });                 // what the reading below is of: the piece written, or the formula
      var src = h("code", { class: "ink-src", title: "What SymPy gets" });
      var ambig = h("div", { class: "ink-ambig" });
      var consts = h("div", { class: "ink-consts" });
      var parseLabel = h("div", { class: "ink-parse-label" }, ["Where this LaTeX can be read more than one way:"]);
      var parseBlock = h("div", { class: "ink-parse", hidden: "" }, [parseLabel, ambig, consts]);
      var wasFormula = h("span", { class: "ink-was" });
      var nowFormula = h("span", { class: "ink-now" });
      var keepBtn = h("button", { type: "button", class: "ink-keep", title: "Leave the formula as it now is" }, ["Keep"]);
      var undoApplied = h("button", { type: "button", class: "ink-undo-applied", title: "The formula as it was, in the editor too" }, ["Undo the change"]);
      var appliedRow = h("div", { class: "ink-applied", hidden: "" }, [
        h("div", { class: "ink-applied-was" }, [h("span", { class: "ink-applied-label" }, ["from (what went is red)"]), wasFormula]),
        h("div", { class: "ink-applied-now" }, [h("span", { class: "ink-applied-label" }, ["to (what came is green)"]), nowFormula]),
        h("div", { class: "ink-applied-ask" }, [keepBtn, undoApplied])]);
      var apply = h("button", { type: "button", class: "ink-apply", disabled: "",
        title: "The pad's formula becomes the editor's (Enter in the LaTeX line does the same)" }, ["Apply to the formula"]);
      var sheetChevron = h("span", { class: "ink-sheet-chevron", "aria-hidden": "true" });
      var sheetSummary = h("span", { class: "ink-sheet-summary" });
      var sheetHead = h("button", { type: "button", class: "ink-sheet-head", "aria-expanded": "true",
        title: "Fold the readings away, or bring them back" }, [sheetChevron, sheetSummary]);
      var actions = h("div", { class: "ink-actions" }, [apply]);
      var sheetBody = h("div", { class: "ink-sheet-body" }, [note, nestRow, cands, readingOf, src, parseBlock, appliedRow, actions]);
      var sheet = h("div", { class: "ink-sheet" }, [sheetHead, sheetBody]);
      var element = h("div", { class: "ink-panel", "data-strokes": "0", "data-zoom": "1.00", "data-hole": "", "data-sel": "", "data-mode": "select" }, [bar, latexRow, stage, sheet]);

      // ---- state -------------------------------------------------------------------
      var strokes = [];                 // [[[x, y, t], ...], ...]
      var past = [], future = [];       // the pad as it was before each edit, and before each undo
      var pending = null;               // the pad before the stroke being written: an edit once it is written
      var shownText = "";               // the LaTeX line as last known, before an input changes it
      var typedAt = 0;                  // when it was last typed in: typing in one go is one edit
      var current = null, currentId = null, t0 = 0;
      var width = 0, height = 0;        // the canvas, in CSS pixels
      var dpr = 1;
      var timer = null, readTimer = null, seq = 0;
      var katex = null;
      var last = null;                  // the reading shown
      var picks = { choices: {}, constants: {} };   // the options picked for the text in the box
      var full = false, folded = false, pageOverflow = null;
      var zoom = 1;                     // on-screen pixels per canvas pixel
      var touches = {};                 // the fingers down, by pointer id: {x, y} on the page
      var gesture = null;               // two fingers or more: where the pinch began
      var blocked = false;              // a finger left from a pinch: it writes nothing until all have lifted
      var dirty = false;                // ink not read since it changed
      var mode = "select";              // what a tap or a stroke on the pad does: "select" (from the start), "pen" or "erase"
      var cameFrom = null;              // the piece the up arrow came from: where the down arrow goes back to
      var erase = null;                 // an erasing drag: {id, at, removed: [{index, stroke}]}
      var sel = null;                   // the selected piece of the text: {s, e} (s === e: a place in it)
      var hole = null;                  // where the ink goes: {free} - anywhere, its reading after the text - or {s, e, base, rect, ...}
      var chosen = null;                // the text the chosen reading put in the hole's place
      var chosenLatex = null;           // that reading's own LaTeX (without the braces or spaces it went in with)
      var piecePicked = false;          // an option of that reading picked by hand: its LaTeX goes in when the hole closes
      var pieces = [];                  // the drawn pieces: {s, e, rect} in the canvas's pixels
      var formulaBox = null;            // the whole drawing, likewise; null: nothing drawn
      var tap = null;                   // a pointer down on the formula: a tap, or a drag that writes
      var touched = false;              // edited in the pad: it no longer follows the editor's formula

      api.katex().then(function (k) { katex = k; renderFormula(); }, function () {});

      // ---- the writing area ----------------------------------------------------------
      var ctx = canvas.getContext("2d");
      // width, height: the canvas in its own pixels, those the strokes are
      // kept in; on screen it is zoom times that.
      function applySize() {
        var cssW = width * zoom, cssH = height * zoom;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        if (cssW * cssH * dpr * dpr > 16e6) dpr = Math.sqrt(16e6 / (cssW * cssH));   // what a phone's canvas takes
        canvas.style.width = cssW + "px";
        canvas.style.height = cssH + "px";
        canvas.width = Math.max(1, Math.round(cssW * dpr));
        canvas.height = Math.max(1, Math.round(cssH * dpr));
        layoutFormula();
        updateStrips();
      }
      function fitPad() {                 // never smaller than the box it scrolls in
        var w = Math.max(width, pad.clientWidth / zoom), hh = Math.max(height, pad.clientHeight / zoom);
        if (w !== width || hh !== height) { width = w; height = hh; applySize(); } else updateStrips();
      }
      function grow(x, y) {               // room beyond ink that nears the right or bottom edge
        var w = width, hh = height;
        var nearX = Math.max(EDGE, pad.clientWidth * 0.25) / zoom, nearY = Math.max(EDGE, pad.clientHeight * 0.25) / zoom;
        // past the margin, and room beyond it: the next point does not grow it again
        if (x > width - nearX) w = Math.min(MAX_W, Math.ceil(x + nearX + Math.max(160, pad.clientWidth * 0.6) / zoom));
        if (y > height - nearY) hh = Math.min(MAX_H, Math.ceil(y + nearY + Math.max(120, pad.clientHeight * 0.6) / zoom));
        if (w > width || hh > height) { width = Math.max(width, w); height = Math.max(height, hh); applySize(); }
      }
      function growToFit() {
        var maxX = 0, maxY = 0;
        strokes.forEach(function (s) { s.forEach(function (p) { maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]); }); });
        if (formulaBox) { maxX = Math.max(maxX, formulaBox.x + formulaBox.w); maxY = Math.max(maxY, formulaBox.y + formulaBox.h); }
        grow(maxX, maxY);
      }
      // The canvas with no ink: the box at a zoom of 1 - magnified, and so
      // still scrolled about, when zoomed in; still covering the box when
      // zoomed out.  Not the box at this zoom: zoomed in, that would leave
      // nothing to scroll to.
      function base() {
        width = pad.clientWidth / Math.min(zoom, 1);
        height = pad.clientHeight / Math.min(zoom, 1);
      }
      function refit() {                  // as large as the ink left needs, no larger: the base, and room beyond the ink
        base();
        applySize();
        growToFit();
      }
      function shrink() {                 // no ink: back to the base, from its start
        pad.scrollLeft = 0;
        pad.scrollTop = 0;
        refit();
      }
      function redraw() {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(dpr * zoom, 0, 0, dpr * zoom, 0, 0);
        drawMarks();
        ctx.lineWidth = 2.2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.strokeStyle = getComputedStyle(canvas).color || "#1f2328";
        var all = current ? strokes.concat([current]) : strokes;
        for (var s = 0; s < all.length; s++) {
          var pts = all[s];
          ctx.beginPath();
          ctx.moveTo(pts[0][0], pts[0][1]);
          if (pts.length === 1) ctx.lineTo(pts[0][0] + 0.1, pts[0][1]);      // a dot
          for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
          ctx.stroke();
        }
        if (erase && erase.at) {          // where the eraser is
          ctx.save();
          ctx.lineWidth = 1.2 / zoom;
          ctx.setLineDash([3 / zoom, 3 / zoom]);
          ctx.strokeStyle = "rgba(127, 127, 127, 0.9)";
          ctx.beginPath();
          ctx.arc(erase.at[0], erase.at[1], eraserRadius(), 0, 2 * Math.PI);
          ctx.stroke();
          ctx.restore();
        }
      }
      function updateStrips() {
        var maxX = pad.scrollWidth - pad.clientWidth, maxY = pad.scrollHeight - pad.clientHeight;
        strips.left.hidden = !(maxX > 1 && pad.scrollLeft > 1);
        strips.right.hidden = !(maxX > 1 && pad.scrollLeft < maxX - 1);
        strips.up.hidden = !(maxY > 1 && pad.scrollTop > 1);
        strips.down.hidden = !(maxY > 1 && pad.scrollTop < maxY - 1);
      }
      function smoothScroll() { return !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); }
      // room on the canvas up to x, y (its own pixels): somewhere to scroll to
      function ensureRoom(x, y) {
        var w = Math.min(MAX_W, Math.max(width, Math.ceil(x))), hh = Math.min(MAX_H, Math.max(height, Math.ceil(y)));
        if (w > width || hh > height) { width = w; height = hh; applySize(); }
      }
      // a rectangle of the canvas (its own pixels) in the middle of what is in sight
      function centerOn(r) {
        if (!r) return;
        var cx = r.x + r.w / 2, cy = r.y + r.h / 2;
        ensureRoom(cx + pad.clientWidth / (2 * zoom), cy + pad.clientHeight / (2 * zoom));
        pad.scrollTo({ left: Math.max(0, cx * zoom - pad.clientWidth / 2), top: Math.max(0, cy * zoom - pad.clientHeight / 2),
                       behavior: smoothScroll() ? "smooth" : "auto" });
      }
      // A stroke that ends near an edge of what is in sight: the pad scrolls on a little that way,
      // so that the next one has room.
      function followStroke(stroke) {
        var xs = stroke.map(function (p) { return p[0]; }), ys = stroke.map(function (p) { return p[1]; });
        var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs), minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
        var viewL = pad.scrollLeft / zoom, viewT = pad.scrollTop / zoom, viewW = pad.clientWidth / zoom, viewH = pad.clientHeight / zoom;
        var mx = Math.max(48, pad.clientWidth * 0.15) / zoom, my = Math.max(36, pad.clientHeight * 0.15) / zoom;
        var dx = 0, dy = 0;
        if (maxX > viewL + viewW - mx) dx = viewW * 0.3;
        else if (minX < viewL + mx && pad.scrollLeft > 1) dx = -viewW * 0.3;
        if (maxY > viewT + viewH - my) dy = viewH * 0.3;
        else if (minY < viewT + my && pad.scrollTop > 1) dy = -viewH * 0.3;
        if (!dx && !dy) return;
        ensureRoom(viewL + viewW + Math.max(dx, 0), viewT + viewH + Math.max(dy, 0));
        pad.scrollBy({ left: dx * zoom, top: dy * zoom, behavior: smoothScroll() ? "smooth" : "auto" });
      }
      function scrollPage(dir) {
        var dx = dir === "left" ? -1 : dir === "right" ? 1 : 0, dy = dir === "up" ? -1 : dir === "down" ? 1 : 0;
        var smooth = !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
        pad.scrollBy({ left: dx * Math.max(40, pad.clientWidth * 0.7), top: dy * Math.max(40, pad.clientHeight * 0.7),
                       behavior: smooth ? "smooth" : "auto" });
      }
      pad.addEventListener("scroll", updateStrips);
      var resizer = window.ResizeObserver ? new ResizeObserver(fitPad) : null;
      if (resizer) resizer.observe(pad); else window.addEventListener("resize", fitPad);

      function point(ev) {                // in the canvas's own pixels, whatever the zoom
        var r = canvas.getBoundingClientRect();
        return [Math.round((ev.clientX - r.left) / zoom * 10) / 10, Math.round((ev.clientY - r.top) / zoom * 10) / 10, Math.round(ev.timeStamp - t0)];
      }

      // ---- the formula in the pad ------------------------------------------------------
      var TRUST = { throwOnError: false, displayMode: false, output: "html", trust: function (c) { return c.command === "\\htmlData"; } };
      function source() { return field.value; }
      // the text drawn: with free ink on the pad, the formula as it was before the ink's reading went in
      function drawnText() { return hole && hole.free ? hole.base : field.value; }
      function setText(v) { field.value = v; shownText = v; }
      function fmt(v) { return String(Math.round(Math.max(0, v) * 1000) / 1000); }
      function emPx() { return parseFloat(getComputedStyle(formula).fontSize) || 24; }
      // hole: {free: true, base, s, e} - ink written anywhere, its reading after the text -
      // or {s, e, base, rect, ...} - a piece of the text given way to room to write in.
      function renderFormula() {
        var open = hole && !hole.free;
        var text = open ? hole.base.slice(0, hole.s) + LatexMap.HOLE + hole.base.slice(hole.e) : hole ? hole.base : source();
        if (!katex || !text.trim()) { formula.textContent = ""; layoutFormula(); return; }
        var holeTex = "";
        if (open) {                        // a transparent rule as large as the room asked for, its top where the piece's was
          var em = emPx();
          holeTex = "\\htmlData{inkhole=1}{\\rule[-" + fmt((hole.hPx - hole.aPx) / em / hole.scaleH) + "em]{" +
                    fmt(hole.wPx / em / hole.scaleW) + "em}{" + fmt(hole.hPx / em / hole.scaleH) + "em}}";
          // at the cursor, room either side: the space to write in not flush against its neighbours
          if (hole.s === hole.e) holeTex = "\\hspace{0.5em}" + holeTex + "\\hspace{0.5em}";
        }
        var tex = LatexMap.annotate(text, holeTex);
        if (tex === null && open) {        // not parsed: nowhere to show the hole - the ink is free instead
          hole = { free: true, base: hole.base, s: hole.base.length, e: hole.base.length, picks: hole.picks };
          renderFormula();
          return;
        }
        try { formula.innerHTML = katex.renderToString("\\displaystyle " + (tex === null ? text : tex), TRUST); }
        catch (e) { formula.textContent = text; }
        layoutFormula();
        if (open && hole.rect && !hole.calibrated) {   // an em in a script is not an em in the text: once, to the size asked
          hole.calibrated = true;
          var rw = hole.rect.w / hole.wPx, rh = hole.rect.h / hole.hPx;
          if (rw > 0 && rh > 0 && (Math.abs(rw - 1) > 0.05 || Math.abs(rh - 1) > 0.05)) { hole.scaleW *= rw; hole.scaleH *= rh; renderFormula(); }
        }
      }
      // What a piece covers: the glyphs, rules and drawings in it (a wrapper's own box
      // is not where KaTeX puts what is in a script or a fraction).
      function unionRect(root) {
        var l = Infinity, tp = Infinity, r = -Infinity, b = -Infinity;
        (function walk(el) {
          var tag = el.tagName.toLowerCase();
          if (tag === "svg" || !el.firstElementChild) {
            var cls = tag === "svg" ? "" : el.className;
            if (tag === "svg" || (/\b(rule|frac-line|overline-line|underline-line|hline)\b/.test(cls) || (el.textContent.replace(/[\s​]/g, "") && !/\bvlist-s\b/.test(cls)))) {
              var q = el.getBoundingClientRect();
              if (q.width > 0 || q.height > 0) { l = Math.min(l, q.left); tp = Math.min(tp, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom); }
            }
            return;
          }
          for (var k = el.firstElementChild; k; k = k.nextElementSibling) walk(k);
        })(root);
        return l === Infinity ? null : { left: l, top: tp, width: r - l, height: b - tp };
      }
      function layoutFormula() {
        formula.style.transform = zoom === 1 ? "" : "scale(" + zoom + ")";
        pieces = [];
        formulaBox = null;
        if (formula.firstElementChild) {
          var c = canvas.getBoundingClientRect();
          var box = function (q) { return { x: (q.left - c.left) / zoom, y: (q.top - c.top) / zoom, w: q.width / zoom, h: q.height / zoom }; };
          var all = unionRect(formula);
          if (all) formulaBox = box(all);
          var els = formula.querySelectorAll("[data-ls]");
          for (var i = 0; i < els.length; i++) {
            var q = unionRect(els[i]);
            if (q) pieces.push({ s: +els[i].getAttribute("data-ls"), e: +els[i].getAttribute("data-le"), rect: box(q) });
          }
          var he = hole && !hole.free ? formula.querySelector("[data-inkhole] .rule") : null;
          if (he) {
            var hr = box(he.getBoundingClientRect());
            if (hole.rect) {                                                    // the ink stays in the hole it was written in
              moveInk(hr.x - hole.rect.x, hr.y - hole.rect.y);
              if (hole.away) { hole.away.dx += hr.x - hole.rect.x; hole.away.dy += hr.y - hole.rect.y; }
            }
            hole.rect = hr;
          }
        }
        element.setAttribute("data-hole", !hole ? "" : hole.free ? "free" : hole.s + "," + hole.e);
        element.setAttribute("data-sel", sel ? sel.s + "," + sel.e : "");
        if (formulaBox) grow(formulaBox.x + formulaBox.w, formulaBox.y + formulaBox.h);
        for (var ak in arrowBtns) arrowBtns[ak].disabled = !!hole || !formulaBox;
        redraw();
      }
      function moveInk(dx, dy) {           // the ink on the pad (the history keeps copies of its own)
        if (Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05) return;
        strokes.concat(current ? [current] : []).forEach(function (s) { s.forEach(function (p) { p[0] += dx; p[1] += dy; }); });
      }
      function inside(p, r, m) { return p[0] >= r.x - m && p[0] <= r.x + r.w + m && p[1] >= r.y - m && p[1] <= r.y + r.h + m; }
      function pieceAt(p) {                // the smallest piece under the pointer
        var best = null, m = 3 / zoom;
        pieces.forEach(function (q) { if (inside(p, q.rect, m) && (!best || q.rect.w * q.rect.h < best.rect.w * best.rect.h)) best = q; });
        return best;
      }
      function enclosing(r) {              // the smallest piece holding r and more; the whole text last
        var best = null, n = drawnText().length;
        pieces.forEach(function (q) {
          if (q.s <= r.s && q.e >= r.e && (q.s < r.s || q.e > r.e) && (!best || q.e - q.s < best.e - best.s)) best = q;
        });
        if (best) return { s: best.s, e: best.e };
        return r.s > 0 || r.e < n ? { s: 0, e: n } : null;
      }
      // A place in the text: a line where it is drawn, as tall as what is beside it.
      function caretRect(pos) {
        var best = null, x = null;
        pieces.forEach(function (q) {
          var at = q.e === pos ? q.rect.x + q.rect.w : q.s === pos ? q.rect.x : null;
          if (at !== null && (!best || q.rect.w * q.rect.h < best.rect.w * best.rect.h)) { best = q; x = at; }
        });
        if (best) return { x: x, y: best.rect.y, w: 0, h: best.rect.h };
        if (!formulaBox) return null;
        if (pos >= source().replace(/\s+$/, "").length) return { x: formulaBox.x + formulaBox.w, y: formulaBox.y, w: 0, h: formulaBox.h };
        if (pos <= source().length - source().replace(/^\s+/, "").length) return { x: formulaBox.x, y: formulaBox.y, w: 0, h: formulaBox.h };
        return null;
      }
      function rectFor(r) {
        if (!r || !formulaBox) return null;
        if (r.s === r.e) return caretRect(r.s);
        if (r.s === 0 && r.e === drawnText().length) return formulaBox;
        for (var i = 0; i < pieces.length; i++) if (pieces[i].s === r.s && pieces[i].e === r.e) return pieces[i].rect;
        var l = Infinity, tp = Infinity, rr = -Infinity, b = -Infinity;         // a range of pieces: all of them
        pieces.forEach(function (q) {
          if (q.s >= r.s && q.e <= r.e) {
            l = Math.min(l, q.rect.x); tp = Math.min(tp, q.rect.y); rr = Math.max(rr, q.rect.x + q.rect.w); b = Math.max(b, q.rect.y + q.rect.h);
          }
        });
        return l === Infinity ? null : { x: l, y: tp, w: rr - l, h: b - tp };
      }
      function writesAt(p) {               // free ink anywhere; in a hole, in it or near the ink written in it
        if (!hole) return false;
        if (hole.free) return true;
        if (hole.rect && inside(p, hole.rect, 28 / zoom)) return true;
        return strokes.some(function (s) { return s.some(function (q) { return Math.abs(q[0] - p[0]) < 40 / zoom && Math.abs(q[1] - p[1]) < 40 / zoom; }); });
      }
      function accent() { return (getComputedStyle(element).getPropertyValue("--se-accent") || "").trim() || "9, 105, 218"; }
      function roundRect(x, y, w, hh) {
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, y, w, hh, 4); else ctx.rect(x, y, w, hh);
      }
      function drawMarks() {
        var a = accent();
        ctx.save();
        ctx.lineWidth = 1.2 / zoom;
        if (sel && !hole) {
          var sr = rectFor(sel);
          if (sr && sel.s === sel.e) {     // the cursor: a line
            ctx.strokeStyle = "rgba(" + a + ", 0.95)";
            ctx.lineWidth = 2 / zoom;
            ctx.beginPath();
            ctx.moveTo(sr.x + 1 / zoom, sr.y - 2 / zoom);
            ctx.lineTo(sr.x + 1 / zoom, sr.y + sr.h + 2 / zoom);
            ctx.stroke();
          } else if (sr) {
            ctx.fillStyle = "rgba(" + a + ", 0.16)";
            ctx.strokeStyle = "rgba(" + a + ", 0.7)";
            roundRect(sr.x - 3, sr.y - 2, sr.w + 6, sr.h + 4);
            ctx.fill();
            ctx.stroke();
          }
        }
        if (hole && hole.free && hole.placement) {  // free ink: the piece its place is against, outlined
          var pr = rectFor(hole.placement.target);
          if (pr) {
            ctx.save();
            ctx.setLineDash([4 / zoom, 3 / zoom]);
            ctx.strokeStyle = "rgba(" + a + ", 0.75)";
            roundRect(pr.x - 3, pr.y - 2, pr.w + 6, pr.h + 4);
            ctx.stroke();
            ctx.restore();
          }
        }
        if (hole && !hole.free && hole.rect) {
          var hr = hole.rect;
          ctx.setLineDash([5 / zoom, 4 / zoom]);
          ctx.fillStyle = "rgba(" + a + ", 0.05)";
          ctx.strokeStyle = "rgba(" + a + ", 0.8)";
          roundRect(hr.x - 4, hr.y - 4, hr.w + 8, hr.h + 8);
          ctx.fill();
          ctx.stroke();
        }
        ctx.restore();
      }
      // A piece becomes a hole: out of sight, and as much room as it took (more for a
      // small one) to write in.
      function openHole(r) {
        if (hole || !canRead || !r) return false;
        var text = source(), rect = rectFor(r), node = LatexMap.nodeAt(text, r.s, r.e);
        var hPx = Math.max(rect ? rect.h + 12 : 0, 56);             // a little larger than the piece, never small
        clearTimeout(timer);
        strokes = []; current = null;
        element.setAttribute("data-strokes", "0");
        hole = { s: r.s, e: r.e, base: text, free: false, braces: !!(node && node.braces), rect: null, calibrated: false,
                 wPx: Math.max(rect ? rect.w + 30 : 0, 110), hPx: hPx, aPx: hPx * 0.72, scaleW: 1, scaleH: 1,
                 picks: { choices: Object.assign({}, picks.choices), constants: Object.assign({}, picks.constants) } };
        sel = null;
        chosen = null;
        touched = true;
        clearReadings();
        renderFormula();
        changedTools();
        centerOn(hole.rect);               // the room to write in, in the middle of the pad
        return true;
      }
      // Ink anywhere: the formula stays as it is, the reading goes after it.
      function openFree() {
        if (hole || !canRead) return false;
        var text = source();
        hole = { free: true, base: text, s: text.length, e: text.length,
                 picks: { choices: Object.assign({}, picks.choices), constants: Object.assign({}, picks.constants) } };
        sel = null;
        chosen = null;
        if (text.trim()) touched = true;
        renderFormula();
        changedTools();
        return true;
      }
      function boxOf(list) {
        var b = null;
        list.forEach(function (s) { s.forEach(function (p) {
          if (!b) b = { minX: p[0], minY: p[1], maxX: p[0], maxY: p[1] };
          else { b.minX = Math.min(b.minX, p[0]); b.minY = Math.min(b.minY, p[1]); b.maxX = Math.max(b.maxX, p[0]); b.maxY = Math.max(b.maxY, p[1]); }
        }); });
        return b;
      }
      function near(p, q, m) {
        return !!(p && q && p.minX - m <= q.maxX && p.maxX + m >= q.minX && p.minY - m <= q.maxY && p.maxY + m >= q.minY);
      }
      // A stroke written where the ink was before it was brought into the room, and not beside
      // it where it is now: brought along the same way - what is written in one go stays together.
      function followAway(stroke) {
        if (!hole || hole.free || !hole.away || !hole.rect) return;
        var b = boxOf([stroke]), r = hole.rect, room = { minX: r.x, minY: r.y, maxX: r.x + r.w, maxY: r.y + r.h };
        if (!near(b, hole.away.box, 120 / zoom) || near(b, boxOf(strokes), 40 / zoom) || near(b, room, 10 / zoom)) return;
        stroke.forEach(function (p) { p[0] += hole.away.dx; p[1] += hole.away.dy; });
        var a = hole.away.box;
        a.minX = Math.min(a.minX, b.minX); a.minY = Math.min(a.minY, b.minY); a.maxX = Math.max(a.maxX, b.maxX); a.maxY = Math.max(a.maxY, b.maxY);
      }
      function fitHole() {                 // the ink inside it, and it as large as the ink needs, with room beyond
        if (!hole || hole.free || !hole.rect || !strokes.length) return;
        var r = hole.rect, m = 34 / zoom, minX = Infinity, minY = Infinity, inkR = -Infinity, inkB = -Infinity;
        strokes.forEach(function (s) { s.forEach(function (p) {
          minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]); inkR = Math.max(inkR, p[0]); inkB = Math.max(inkB, p[1]);
        }); });
        var dx = 0, dy = 0;
        if (inkR < r.x || minX > r.x + r.w || inkB < r.y || minY > r.y + r.h) {
          // written away from the room made for it (the selection was elsewhere on the pad): the ink into it -
          // and the next strokes written there after it, the same way (followAway)
          dx = r.x + 12 / zoom - minX;
          dy = r.y + 12 / zoom - minY;
          hole.away = { dx: dx, dy: dy, box: { minX: minX, minY: minY, maxX: inkR, maxY: inkB } };
        } else {
          // begun on a small piece, the ink may stand out above or left of the room: in it
          if (minX < r.x + 6 / zoom) dx = r.x + 12 / zoom - minX;
          if (minY < r.y + 6 / zoom) dy = r.y + 12 / zoom - minY;
        }
        if (dx || dy) moveInk(dx, dy);
        var maxX = r.x + hole.wPx, maxY = r.y + hole.hPx;
        strokes.forEach(function (s) { s.forEach(function (p) { maxX = Math.max(maxX, p[0] + m); maxY = Math.max(maxY, p[1] + m); }); });
        if (maxX - r.x > hole.wPx + 1 || maxY - r.y > hole.hPx + 1) {
          hole.wPx = Math.max(hole.wPx, maxX - r.x);
          hole.hPx = Math.max(hole.hPx, maxY - r.y);
          renderFormula();
        }
      }
      // The ink's reading into the text for good (the text as it was, with none): the ink goes.
      function commitHole() {
        if (!hole) return;
        // The options go on with the text, as written: the whole formula's as they were
        // (moved past the piece that changed), and the piece's own where it now stands in
        // the text - an option is known by the place of what it is of ("rule@start-end");
        // a constant's switch, by its name.
        var kept = hole.picks || { choices: {}, constants: {} };
        var next = { choices: Object.assign({}, kept.choices), constants: Object.assign({}, kept.constants) };
        if (pieceMode()) {
          var at = hole.s + Math.max(0, chosen.indexOf(chosenLatex)), delta = chosen.length - (hole.e - hole.s);
          next.choices = shiftChoices(kept.choices, hole.s, hole.e, delta);
          Object.keys(picks.choices || {}).forEach(function (k) {
            var m = /^(.*)@(\d+)-(\d+)$/.exec(k);
            if (m) next.choices[m[1] + "@" + (+m[2] + at) + "-" + (+m[3] + at)] = picks.choices[k];
          });
          Object.assign(next.constants, picks.constants);
        }
        piecePicked = false;
        var was = hole;
        hole = null;
        clearTimeout(timer);
        strokes = []; current = null;
        element.setAttribute("data-strokes", "0");
        if (chosen === null) setText(was.base);
        if (chosen === null) sel = was.free ? null : { s: was.s, e: was.e };
        else {                             // the piece it put in, spaces aside
          var lead = chosen.length - chosen.replace(/^\s+/, "").length;
          sel = { s: was.s + lead, e: was.s + chosen.replace(/\s+$/, "").length };
        }
        chosen = null;
        chosenLatex = null;
        clearReadings();
        picks = next;
        renderFormula();
        refit();
        changedTools();
        reread();
      }
      function dropHole() {                // the text typed over: the ink goes, the text is what counts
        hole = null;
        chosen = null;
        clearTimeout(timer);
        strokes = []; current = null;
        element.setAttribute("data-strokes", "0");
        cands.textContent = "";
      }
      // A tap near the left or right edge of a piece, or beside the formula: the cursor
      // there, a place in the text.  null: no place.
      function caretAt(t) {
        var p = t.at;
        if (t.hit) {
          var r = t.hit.rect, edge = Math.min(10 / zoom, r.w * 0.3), tol = 2 / zoom;
          var left = p[0] <= r.x + edge, right = !left && p[0] >= r.x + r.w - edge;
          if (!left && !right) return null;
          // the outermost piece holding it whose edge that also is: after x^{2}, not after its 2
          var best = t.hit;
          pieces.forEach(function (q) {
            if (q.s > t.hit.s || q.e < t.hit.e) return;
            var same = left ? Math.abs(q.rect.x - r.x) <= tol : Math.abs(q.rect.x + q.rect.w - (r.x + r.w)) <= tol;
            if (same && q.rect.w * q.rect.h > best.rect.w * best.rect.h) best = q;
          });
          return left ? best.s : best.e;
        }
        if (formulaBox && p[1] >= formulaBox.y - 24 / zoom && p[1] <= formulaBox.y + formulaBox.h + 24 / zoom) {
          if (p[0] > formulaBox.x + formulaBox.w) return source().length;
          if (p[0] < formulaBox.x) return 0;
        }
        return null;
      }
      function tapAt(t) {
        if (hole) { record(); commitHole(); return; }                  // the ink's reading in first
        var pos = caretAt(t);
        var next = pos !== null ? { s: pos, e: pos }                   // at an edge, or beside the formula: the cursor
                 : !t.hit ? null                                       // nothing there: nothing selected
                 : sel && sel.s !== sel.e && t.hit.s === sel.s && t.hit.e === sel.e ? enclosing(sel) || sel   // again: what holds it
                 : { s: t.hit.s, e: t.hit.e };
        if ((next ? next.s + "," + next.e : "") === (sel ? sel.s + "," + sel.e : "")) return;
        record();
        sel = next;
        if (sel && !cands.children.length) note.textContent = sel.s === sel.e
          ? "Cursor: with the Pen, what is written goes in here - or type here with the keyboard button"
          : "Selected: with the Pen, write over it - or type over it with the keyboard button";
        layoutFormula();
        changedTools();
      }
      function changedTools() {
        doneBtn.disabled = !hole;
        typeBtn.disabled = !sel || !!hole;
        for (var k in arrowBtns) arrowBtns[k].disabled = !!hole || !formulaBox;
      }
      function sameRange(a, b) { return !!a && !!b && a.s === b.s && a.e === b.e; }
      // The pieces right inside a range (the whole text for null): the children of a piece.
      function childrenOf(r) {
        var box = r || { s: 0, e: source().length }, list = [];
        pieces.forEach(function (q) {
          if (q.s >= box.s && q.e <= box.e && !(q.s <= box.s && q.e >= box.e) && !list.some(function (o) { return sameRange(o, q); })) list.push(q);
        });
        return list.filter(function (q) {
          return !list.some(function (o) { return o !== q && o.s <= q.s && o.e >= q.e && (o.s < q.s || o.e > q.e); });
        }).sort(function (a, b) { return a.s - b.s; });
      }
      function smallestHolding(r) {        // the smallest piece holding r (r itself included); null: only the whole text
        var best = null;
        pieces.forEach(function (q) { if (q.s <= r.s && q.e >= r.e && (!best || q.e - q.s < best.e - best.s)) best = q; });
        return best;
      }
      // Dragged from one piece to another: the range of pieces beside each other that holds both.
      function rangeBetween(a, b) {
        var n = source().length, holder = smallestHolding({ s: Math.min(a.s, b.s), e: Math.max(a.e, b.e) });
        if (holder && (sameRange(holder, a) || sameRange(holder, b))) return { s: holder.s, e: holder.e };
        var kids = childrenOf(holder || { s: 0, e: n });
        var ka = kids.filter(function (k) { return k.s <= a.s && k.e >= a.e; })[0], kb = kids.filter(function (k) { return k.s <= b.s && k.e >= b.e; })[0];
        if (!ka || !kb) return holder ? { s: holder.s, e: holder.e } : { s: 0, e: n };
        return { s: Math.min(ka.s, kb.s), e: Math.max(ka.e, kb.e) };
      }
      function setSelection(next) {
        if (!next || sameRange(next, sel)) return false;
        record();
        sel = next;
        layoutFormula();
        changedTools();
        return true;
      }
      // The arrows: up to what holds the selection, down back inside, left and right to the
      // pieces beside it - or, with the cursor, to the next place in the text.
      function navigate(dir) {
        if (hole || !formulaBox) return;
        var n = source().length, next = null;
        if (!sel) {
          if (dir === "left") next = { s: 0, e: 0 };
          else if (dir === "right") next = { s: n, e: n };
          else if (dir === "down") { var first = childrenOf(null)[0]; if (first) next = { s: first.s, e: first.e }; }
        } else if (sel.s === sel.e) {
          var pos = sel.s, marks = [0, n];
          pieces.forEach(function (q) { marks.push(q.s, q.e); });
          marks = marks.filter(function (v, i) { return marks.indexOf(v) === i; }).sort(function (a, b) { return a - b; });
          if (dir === "right") { var r = marks.filter(function (v) { return v > pos; })[0]; if (r !== undefined) next = { s: r, e: r }; }
          else if (dir === "left") { var l = marks.filter(function (v) { return v < pos; }).pop(); if (l !== undefined) next = { s: l, e: l }; }
          else if (dir === "up") {
            var around = null;
            pieces.forEach(function (q) { if (q.s < pos && q.e > pos && (!around || q.e - q.s < around.e - around.s)) around = q; });
            next = around ? { s: around.s, e: around.e } : { s: 0, e: n };
          }
        } else if (dir === "up") {
          var up = enclosing(sel);
          if (up) { cameFrom = { s: sel.s, e: sel.e }; next = up; }
        } else if (dir === "down") {
          var kids = childrenOf(sel);
          if (!kids.length) next = { s: sel.e, e: sel.e };
          else {
            var back = cameFrom && kids.filter(function (k) { return k.s <= cameFrom.s && k.e >= cameFrom.e; })[0];
            var into = back || kids[0];
            next = { s: into.s, e: into.e };
          }
        } else {
          var sibs = childrenOf(enclosing(sel) || { s: 0, e: n });
          if (dir === "right") { var nx = sibs.filter(function (q) { return q.s >= sel.e; })[0]; next = nx ? { s: nx.s, e: nx.e } : { s: sel.e, e: sel.e }; }
          else { var pv = sibs.filter(function (q) { return q.e <= sel.s; }).pop(); next = pv ? { s: pv.s, e: pv.e } : { s: sel.s, e: sel.s }; }
          cameFrom = null;
        }
        setSelection(next);
      }
      // Shift with left and right: the range of pieces beside each other shrinks and grows at its end.
      function extendRange(dir) {
        if (hole || !sel || sel.s === sel.e) return;
        var n = source().length, sibs = childrenOf(enclosing(sel) || { s: 0, e: n });
        var within = sibs.filter(function (q) { return q.s >= sel.s && q.e <= sel.e; });
        if (dir === "right") { var nx = sibs.filter(function (q) { return q.s >= sel.e; })[0]; if (nx) setSelection({ s: sel.s, e: nx.e }); }
        else if (within.length > 1) setSelection({ s: sel.s, e: within[within.length - 2].e });
      }
      ["up", "down", "left", "right"].forEach(function (d) { arrowBtns[d].addEventListener("click", function () { navigate(d); }); });
      pad.addEventListener("keydown", function (ev) {
        var dir = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" }[ev.key];
        if (!dir || ev.altKey || ev.ctrlKey || ev.metaKey) return;
        ev.preventDefault();
        ev.stopPropagation();                         // the editor's own keys are not for the pad
        if (ev.shiftKey && (dir === "left" || dir === "right")) extendRange(dir); else navigate(dir);
      });
      doneBtn.addEventListener("click", function () { if (hole) { record(); commitHole(); } });
      typeBtn.addEventListener("click", function () {   // the selected piece's LaTeX, selected in the line: typing replaces it
        if (!sel || hole) return;
        field.focus();
        field.setSelectionRange(sel.s, sel.e);
      });
      function editorLatex() {
        var st = api.state && api.state();
        return st && typeof st.latex_plain === "string" ? st.latex_plain : "";
      }
      function loadFromEditor() {
        if (hole) dropHole();
        setText(editorLatex());
        sel = null;
        touched = false;
        clearReadings();
        renderFormula();
        refit();
        changedTools();
        reread();
      }
      loadBtn.addEventListener("click", function () { record(); loadFromEditor(); });

      // ---- erasing: a stroke goes when the eraser passes within reach of it ----------
      var coarse = !!(window.matchMedia && window.matchMedia("(any-pointer: coarse)").matches);
      function eraserRadius() { return (coarse ? 18 : 10) / zoom; }     // px on screen, in the canvas's own pixels
      function nearSegment(qx, qy, a, b, r) {
        var dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy;
        var u = len ? Math.max(0, Math.min(1, ((qx - a[0]) * dx + (qy - a[1]) * dy) / len)) : 0;
        var ex = a[0] + u * dx - qx, ey = a[1] + u * dy - qy;
        return ex * ex + ey * ey <= r * r;
      }
      function reaches(stroke, from, to, r) {
        // the eraser's path from one sample to the next, in steps of half its reach: a quick
        // drag passes over a stroke between two samples
        var steps = Math.max(1, Math.ceil(Math.hypot(to[0] - from[0], to[1] - from[1]) / (r / 2)));
        for (var k = 0; k <= steps; k++) {
          var qx = from[0] + (to[0] - from[0]) * k / steps, qy = from[1] + (to[1] - from[1]) * k / steps;
          if (stroke.length === 1 && nearSegment(qx, qy, stroke[0], stroke[0], r)) return true;
          for (var i = 1; i < stroke.length; i++) if (nearSegment(qx, qy, stroke[i - 1], stroke[i], r)) return true;
        }
        return false;
      }
      function eraseTo(p) {
        var from = erase.at || p, r = eraserRadius(), gone = false;
        for (var i = strokes.length - 1; i >= 0; i--) {
          if (reaches(strokes[i], from, p, r)) {
            erase.removed.push({ index: i, stroke: strokes[i] });  // the index at the time: undo puts each back in its place
            strokes.splice(i, 1);
            gone = true;
          }
        }
        erase.at = p;
        if (gone) element.setAttribute("data-strokes", String(strokes.length));
        redraw();
      }
      function finishErase() {
        var e = erase;
        erase = null;
        if (!e.removed.length) { redraw(); return; }
        push(e.before);
        refit();
        changed(300);                     // what is left is read again
      }
      function setMode(m) {
        mode = m;
        [["select", selectBtn], ["pen", penBtn], ["erase", eraseBtn]].forEach(function (x) {
          x[1].setAttribute("aria-pressed", x[0] === m ? "true" : "false");
          x[1].classList.toggle("ink-on", x[0] === m);
        });
        canvas.classList.toggle("ink-erasing", m === "erase");
        canvas.classList.toggle("ink-selecting", m === "select");
        element.setAttribute("data-mode", m);
      }
      selectBtn.addEventListener("click", function () {
        // back to selecting before a stroke was written: the room made for it goes, the cursor or selection back
        if (hole && !hole.free && !strokes.length) commitHole();
        setMode("select");
      });
      penBtn.addEventListener("click", function () {
        setMode("pen");
        // the cursor placed or a piece selected: the room to write in, there, at once
        if (!hole && canRead && sel && rectFor(sel)) { record(); openHole(sel); }
      });
      eraseBtn.addEventListener("click", function () { setMode(mode === "erase" ? "pen" : "erase"); });

      // ---- zooming and scrolling: two fingers, or a pinch on a trackpad ------------
      function zoomTo(z) {                // the new zoom, the canvas still covering the box
        z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
        if (Math.abs(z - zoom) < 1e-3) return false;
        zoom = z;
        width = Math.max(width, pad.clientWidth / zoom);
        height = Math.max(height, pad.clientHeight / zoom);
        applySize();
        element.setAttribute("data-zoom", zoom.toFixed(2));
        updateZoomButtons();
        return true;
      }
      var ZOOM_STEP = 1.2;                // the editor's step
      function updateZoomButtons() {
        zoomLevelBtn.textContent = Math.round(zoom * 100) + "%";
        zoomOutBtn.disabled = zoom <= MIN_ZOOM + 1e-3;
        zoomInBtn.disabled = zoom >= MAX_ZOOM - 1e-3;
      }
      // zoomed about the middle of what is in sight: what is there stays there
      function zoomAboutMiddle(z) {
        var cx = pad.clientWidth / 2, cy = pad.clientHeight / 2;
        var inkX = (pad.scrollLeft + cx) / zoom, inkY = (pad.scrollTop + cy) / zoom;
        if (!zoomTo(z)) return;
        pad.scrollLeft = inkX * zoom - cx;
        pad.scrollTop = inkY * zoom - cy;
      }
      zoomOutBtn.addEventListener("click", function () { zoomAboutMiddle(zoom / ZOOM_STEP); });
      zoomInBtn.addEventListener("click", function () { zoomAboutMiddle(zoom * ZOOM_STEP); });
      zoomLevelBtn.addEventListener("click", function () { zoomAboutMiddle(1); });
      function padPoint(x, y) {           // a point of the page, in the box's own coordinates
        var r = pad.getBoundingClientRect();
        return { x: x - r.left - pad.clientLeft, y: y - r.top - pad.clientTop };
      }
      function pinch() {
        var ids = Object.keys(touches), a = touches[ids[0]], b = touches[ids[1]];
        var m = padPoint((a.x + b.x) / 2, (a.y + b.y) / 2);
        return { dist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), x: m.x, y: m.y };
      }
      function startGesture() {
        if (erase) finishErase();
        tap = null;
        if (current) {                    // what a first finger began is not a stroke - nor the room it made
          current = null;
          currentId = null;
          if (pending) { var before = pending; pending = null; restore(before); }
          refit();
          redraw();
        }
        var s = pinch();
        gesture = { dist: s.dist, zoom: zoom, inkX: (pad.scrollLeft + s.x) / zoom, inkY: (pad.scrollTop + s.y) / zoom };
      }
      function moveGesture() {
        var s = pinch();
        zoomTo(gesture.zoom * s.dist / gesture.dist);
        pad.scrollLeft = gesture.inkX * zoom - s.x;                         // the ink under the fingers stays under them
        pad.scrollTop = gesture.inkY * zoom - s.y;
      }
      pad.addEventListener("wheel", function (ev) {
        if (!ev.ctrlKey) return;                                           // a pinch on a trackpad; the wheel alone scrolls
        ev.preventDefault();
        var p = padPoint(ev.clientX, ev.clientY), inkX = (pad.scrollLeft + p.x) / zoom, inkY = (pad.scrollTop + p.y) / zoom;
        if (!zoomTo(zoom * Math.exp(-ev.deltaY * 0.01))) return;
        pad.scrollLeft = inkX * zoom - p.x;
        pad.scrollTop = inkY * zoom - p.y;
      }, { passive: false });

      // One finger, a pen or the mouse writes.  A second finger never does:
      // the stroke the first one began is dropped, and the fingers pinch and
      // drag until every one of them has lifted.
      canvas.addEventListener("pointerdown", function (ev) {
        if (ev.pointerType === "touch") {
          touches[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
          try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
          if (Object.keys(touches).length >= 2) { ev.preventDefault(); startGesture(); return; }
          if (blocked) return;
        }
        if (current || erase || tap) return;                               // a palm beside a pen
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        var at = point(ev);
        if (mode === "select") {                                           // Select: a tap selects, a drag a range
          ev.preventDefault();
          try { pad.focus({ preventScroll: true }); } catch (e) { /* no focus to take */ }
          try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
          tap = { id: ev.pointerId, at: at, hit: pieceAt(at), moved: false };
          return;
        }
        if (!canRead) return;
        if (mode === "erase" || (ev.pointerType === "pen" && (ev.buttons & 32))) {  // the Eraser, or a pen turned round
          ev.preventDefault();
          clearTimeout(timer);
          try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
          erase = { id: ev.pointerId, at: null, removed: [], before: snapshot() };
          eraseTo(at);
          return;
        }
        // the Pen: with a piece selected, what is written - wherever on the pad - takes its
        // place (the piece gives way to room to write in, and the ink goes into it); with
        // nothing selected, the ink is free.  A room open takes every stroke until it is done.
        ev.preventDefault();
        pending = snapshot();
        if (!hole) { if (sel && rectFor(sel)) openHole(sel); else openFree(); }
        clearTimeout(timer);
        if (!strokes.length) t0 = ev.timeStamp;
        try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
        current = [point(ev)];
        currentId = ev.pointerId;
        redraw();
      });
      canvas.addEventListener("pointermove", function (ev) {
        if (touches[ev.pointerId]) {
          touches[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
          if (gesture) { ev.preventDefault(); moveGesture(); return; }
        }
        if (tap && ev.pointerId === tap.id) {                              // a drag is no tap: from piece to piece, a range
          var q = point(ev);
          if (!tap.moved && Math.hypot(q[0] - tap.at[0], q[1] - tap.at[1]) > 8 / zoom) { tap.moved = true; tap.before = snapshot(); }
          if (tap.moved && tap.hit && !hole) {
            var over = pieceAt(q);
            if (over) {
              var range = rangeBetween(tap.hit, over);
              if (!sameRange(range, sel)) { sel = range; layoutFormula(); changedTools(); }
            }
          }
          return;
        }
        if (erase && ev.pointerId === erase.id) {
          var samples = (ev.getCoalescedEvents && ev.getCoalescedEvents()) || [];
          if (!samples.length) samples = [ev];
          for (var j = 0; j < samples.length; j++) eraseTo(point(samples[j]));
          return;
        }
        if (!current || ev.pointerId !== currentId) return;
        var evs = (ev.getCoalescedEvents && ev.getCoalescedEvents()) || [];
        if (!evs.length) evs = [ev];
        for (var i = 0; i < evs.length; i++) current.push(point(evs[i]));
        var p = current[current.length - 1];
        grow(p[0], p[1]);
        redraw();
      });
      function endStroke(ev) {
        if (!current || ev.pointerId !== currentId) return;
        followAway(current);
        strokes.push(current);
        if (pending) { push(pending); pending = null; }
        current = null;
        currentId = null;
        fitHole();
        followStroke(strokes[strokes.length - 1]);
        changed(700);                     // a pause: the formula may be finished
      }
      function lift(ev) {
        var finger = !!touches[ev.pointerId];
        delete touches[ev.pointerId];
        if (finger && (gesture || blocked)) {
          var left = Object.keys(touches).length;
          if (left >= 2) startGesture();                                   // one of three lifted: on from where the fingers are
          else {
            gesture = null;
            blocked = left > 0;                                            // the finger left behind writes nothing
            if (!left && dirty) { clearTimeout(timer); timer = setTimeout(recognize, 700); }   // the reading the first finger put off
          }
          return;
        }
        if (tap && ev.pointerId === tap.id) {
          var was = tap;
          tap = null;
          if (!was.moved) tapAt(was);
          else if (was.before && !sameRange(was.before.sel, sel) && (was.before.sel || sel)) push(was.before);   // a range dragged: one edit
          return;
        }
        if (erase && ev.pointerId === erase.id) { finishErase(); return; }
        endStroke(ev);
      }
      canvas.addEventListener("pointerup", lift);
      canvas.addEventListener("pointercancel", lift);

      // ---- undo, redo, clear: every edit in the pad -------------------------------------
      // The pad as it was - its text, selection, room to write in, ink - is kept before
      // each edit; Undo puts it back, and Redo what Undo took.
      var HISTORY = 200;
      function copyStroke(s) { return s.map(function (p) { return p.slice(); }); }
      function copyHole(x) {
        var c = {};
        for (var k in x) if (k !== "rect") c[k] = x[k];
        c.rect = null;                    // measured again where it is drawn: the ink is where it was
        return c;
      }
      function snapshot(text) {
        return { text: text === undefined ? field.value : text, sel: sel ? { s: sel.s, e: sel.e } : null,
                 hole: hole ? copyHole(hole) : null, strokes: strokes.map(copyStroke), chosen: chosen,
                 chosenLatex: chosenLatex, piecePicked: piecePicked, picks: JSON.parse(JSON.stringify(picks)) };
      }
      function push(st) {
        hideApplied();                    // an edit of its own: what the applying did is answered for
        past.push(st);
        if (past.length > HISTORY) past.shift();
        future = [];
        sheetBody.style.minHeight = "";   // an edit of its own: the readings may take less room again
        updateHistory();
      }
      // Going back and forth through the history, the readings under the pad never take
      // less room than they did: taking it would move the pad, the line and the tools
      // (the page scrolls back up to fill it) under the finger about to press Undo again.
      function holdSheet() {
        var h = sheetBody.offsetHeight, held = parseFloat(sheetBody.style.minHeight) || 0;
        if (h > held) sheetBody.style.minHeight = h + "px";
      }
      function record(text) { push(snapshot(text)); }
      function updateHistory() {
        undoBtn.disabled = !past.length;
        redoBtn.disabled = !future.length;
      }
      function restore(st) {
        hideApplied();
        clearTimeout(readTimer);
        seq++;
        current = null; currentId = null; erase = null; tap = null; pending = null;
        typedAt = 0;
        setText(st.text);
        sel = st.sel ? { s: st.sel.s, e: st.sel.e } : null;
        hole = st.hole ? copyHole(st.hole) : null;
        strokes = st.strokes.map(copyStroke);
        chosen = st.chosen;
        chosenLatex = st.chosenLatex === undefined ? null : st.chosenLatex;
        piecePicked = !!st.piecePicked;
        touched = true;
        element.setAttribute("data-strokes", String(strokes.length));
        clearTimeout(timer);
        picks = st.picks ? JSON.parse(JSON.stringify(st.picks)) : { choices: {}, constants: {} };
        if (!strokes.length) cands.textContent = "";   // readings of ink no longer there; otherwise they stay until the ink is read again
        renderFormula();
        refit();
        changedTools();
        updateHistory();
        clearBtn.disabled = !strokes.length;
        dirty = strokes.length > 0;
        if (strokes.length) timer = setTimeout(function () { recognize(true); }, 250);   // the ink read again, the text kept as it was
        else reread();
      }
      // The editor's own count of what it has done: an applying is taken back in it only
      // while nothing else has been done there since.
      function editorSeq() {
        var st = api.state && api.state();
        return st && typeof st.seq === "number" ? st.seq : null;
      }
      function undo() {
        if (!past.length) return;
        holdSheet();
        var st = past.pop(), now = snapshot();
        if (st.applied && st.applied.seq !== null && editorSeq() === st.applied.seq) {
          now.reapply = true;                      // Redo applies it again
          api.send({ action: "undo" }).then(null, function () {});   // past the applying: the formula in the editor back too
        }
        future.push(now);
        restore(st);
      }
      function redo() {
        if (!future.length) return;
        holdSheet();
        var st = future.pop(), now = snapshot();
        if (st.reapply) {
          now.applied = { seq: null };
          api.send({ action: "redo" }).then(function () { now.applied.seq = editorSeq(); }, function () {});
        }
        past.push(now);
        restore(st);
      }
      function changed(delay) {
        element.setAttribute("data-strokes", String(strokes.length));
        dirty = strokes.length > 0;
        updateHistory();
        clearBtn.disabled = !strokes.length;
        redraw();
        changedTools();
        clearTimeout(timer);
        if (strokes.length) { timer = setTimeout(recognize, delay); return; }
        seq++;
        clearReadings();
        if (hole) { chosen = null; chosenLatex = null; setText(hole.base); }
        reread();
      }
      function clearInk() {
        if (!strokes.length) return false;
        record();
        strokes = [];
        shrink();
        changed(0);
        return true;
      }
      undoBtn.addEventListener("click", undo);
      redoBtn.addEventListener("click", redo);
      clearBtn.addEventListener("click", clearInk);
      readBtn.addEventListener("click", function () { recognize(false); });

      // ---- reading -----------------------------------------------------------------------
      function clearReadings() {
        clearTimeout(timer);
        cands.textContent = "";
        nestRow.textContent = "";
        picks = { choices: {}, constants: {} };
        note.textContent = canRead ? "" : status.reason;
        note.className = "ink-note" + (canRead ? "" : " error");
      }
      function reset() {                  // nothing: no text, no hole, no readings
        seq++;
        clearReadings();
        setText("");
        hole = null;
        sel = null;
        chosen = null;
        touched = false;
        renderFormula();
        changedTools();
        show(null);
      }
      function typeset(el, tex, fallback) {
        el.textContent = "";
        if (katex && tex) {
          try { el.innerHTML = katex.renderToString(tex, { throwOnError: false, displayMode: false, output: "html" }); return; }
          catch (e) { /* the text, then */ }
        }
        el.textContent = fallback || tex || "";
      }
      // The piece of the formula free ink is written against, drawn into the ink as a
      // stand-in the model reads as \Delta - a triangle no wider than tall, in the piece's
      // place, before the ink - so that the model reads the ink's relation to it (a fraction
      // over it, its exponent, a product) as a whole; the \Delta is then the piece.
      var STAND_IN = /\\Delta(?![A-Za-z])/g;
      function standIn(r, list) {
        var hh = r.h * 0.8, w = Math.min(r.w, hh * 1.2), x0 = r.x + (r.w - w) / 2, y0 = r.y + (r.h - hh) / 2, pts = [], tt = 0;
        var line = function (ax, ay, bx, by) {
          var n = Math.max(2, Math.round(Math.hypot(bx - ax, by - ay) / 2));
          for (var i = pts.length ? 1 : 0; i <= n; i++) { pts.push([ax + (bx - ax) * i / n, ay + (by - ay) * i / n, tt]); tt += 8; }
        };
        line(x0, y0 + hh, x0 + w / 2, y0);
        line(x0 + w / 2, y0, x0 + w, y0 + hh);
        line(x0 + w, y0 + hh, x0, y0 + hh);
        var shift = tt + 250;
        return [pts].concat(list.map(function (s) { return s.map(function (p) { return [p[0], p[1], p[2] + shift]; }); }));
      }
      // A reading with the stand-in once: the piece in its place (grouped where it is more than
      // one symbol - in parentheses under a script).  null: not a reading of the ink with the piece.
      function nestIn(c, piece) {
        if ((c.latex.match(STAND_IN) || []).length !== 1) return null;
        var single = /^(?:[A-Za-z0-9]|\\[A-Za-z]+|\{[^{}]*\})$/.test(piece.trim());
        var put = function (tex) {
          return tex.replace(/\\Delta(?![A-Za-z])(\s*[\^_])?/, function (all, script) {
            if (script) return (single ? piece : "\\left(" + piece + "\\right)") + script;
            return single ? piece : "{" + piece + "}";
          });
        };
        return { latex: put(c.latex), display: put(c.display || c.latex), score: c.score, nested: true };
      }
      function recognize(keep) {          // keep: the text as it is (restored by Undo), not the best reading
        clearTimeout(timer);
        if (!strokes.length || !canRead) return;
        dirty = false;
        var my = ++seq;
        element.classList.add("ink-busy");
        note.textContent = "Reading…";
        note.className = "ink-note";
        updateSummary();
        var pl = hole && hole.free ? placeInk(strokes) : null;
        if (hole && hole.free) {
          hole.nestOptions = pl && pl.options ? pl.options : [];
          if (hole.picked === "none") pl = null;                                   // alone, as asked
          else if (hole.picked && pl) {
            if (hole.nestOptions.some(function (o) { return sameRange(o, hole.picked); })) pl = Object.assign({}, pl, { nest: hole.picked });
            else hole.picked = null;                                                // no longer one of them
          }
          hole.placement = pl;
          redraw();
          renderNestRow();
        }
        var nestAt = pl && pl.nest ? rectFor(pl.nest) : null;
        var failed = function (e) {
          if (my !== seq) return;
          element.classList.remove("ink-busy");
          note.textContent = String((e && e.message) || e);
          note.className = "ink-note error";
          updateSummary();
        };
        // quiet: the editor's overlay would cover the area while one writes on
        if (!nestAt) {
          api.call("recognize", { strokes: pl && pl.read ? pl.read : strokes }, { quiet: true })
            .then(function (res) { if (my === seq) showCandidates(res, keep); }, failed);
          return;
        }
        api.call("recognize", { strokes: standIn(nestAt, strokes) }, { quiet: true }).then(function (res) {
          if (my !== seq) return;
          var piece = hole.base.slice(pl.nest.s, pl.nest.e), nested = [];
          res.candidates.forEach(function (c) { var n = nestIn(c, piece); if (n && !nested.some(function (o) { return o.latex === n.latex; })) nested.push(n); });
          if (nested.length) {
            hole.placement = { kind: "nest", target: pl.nest, s: pl.nest.s, e: pl.nest.e };
            redraw();
            showCandidates({ candidates: nested, ms: res.ms }, keep);
            return;
          }
          // the model did not read the ink with the piece: the rules of thumb, and the ink read alone
          api.call("recognize", { strokes: pl.read ? pl.read : strokes }, { quiet: true })
            .then(function (res2) { if (my === seq) showCandidates(res2, keep); }, failed);
        }, failed);
      }
      function renderNestRow() {
        nestRow.textContent = "";
        if (!hole || !hole.free || !hole.nestOptions || !hole.nestOptions.length || !strokes.length) return;
        var reading = hole.placement && hole.placement.kind === "nest" ? hole.placement.target : null;
        nestRow.appendChild(h("span", { class: "ink-nest-label" }, ["What is written is read with:"]));
        hole.nestOptions.forEach(function (r) {
          var text = hole.base.slice(r.s, r.e);
          var b = h("button", { type: "button", class: "ink-nest-option", "data-s": String(r.s), "data-e": String(r.e),
                                title: "Read the ink together with " + text.trim() });
          typeset(b, text, text);
          var on = hole.picked && hole.picked !== "none" ? sameRange(hole.picked, r) : !hole.picked && !!reading && sameRange(reading, r);
          b.classList.toggle("ink-chosen", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
          b.addEventListener("click", function () { pickNest({ s: r.s, e: r.e }); });
          nestRow.appendChild(b);
        });
        var alone = h("button", { type: "button", class: "ink-nest-option ink-nest-alone", title: "Read the ink alone, after the formula" }, ["alone"]);
        alone.classList.toggle("ink-chosen", hole.picked === "none");
        alone.setAttribute("aria-pressed", hole.picked === "none" ? "true" : "false");
        alone.addEventListener("click", function () { pickNest("none"); });
        nestRow.appendChild(alone);
      }
      // A piece picked by hand (or "none": alone) to read the ink with: read again with it.
      function pickNest(r) {
        if (!hole || !hole.free) return;
        if (r === "none" ? hole.picked === "none" : hole.picked && hole.picked !== "none" && sameRange(hole.picked, r)) return;
        record();
        hole.picked = r;
        recognize(false);
      }
      function showCandidates(res, keep) {
        element.classList.remove("ink-busy");
        cands.textContent = "";
        renderNestRow();
        if (!res.candidates.length) { note.textContent = "Nothing could be read"; show(null); return; }
        note.textContent = "Read in " + res.ms + " ms" + (res.candidates.length > 1 ? " — the best reading first, pick another if it is the one" : "");
        res.candidates.forEach(function (c, i) {
          var b = h("button", { type: "button", class: "ink-cand", role: "option", title: c.latex });
          typeset(b, c.display || c.latex, c.latex);      // the delimiters sized (\left, \right): easier on the eye
          b.addEventListener("click", function () {
            record();
            choose(c, b);
            // picked by hand: on to its LaTeX, its options and the buttons that put it in
            // (the first reading, chosen for the writer, leaves the page where it is)
            requestAnimationFrame(function () { reveal(actions, "end"); });
          });
          cands.appendChild(b);
          if (i === 0 && keep !== true) choose(c, b);
          else if (keep === true && chosenLatex !== null && c.latex === chosenLatex) markChosen(b);
        });
        if (keep === true) {
          if (chosenLatex !== null && !cands.querySelector(".ink-chosen")) addEdited(chosenLatex);
          reread();
        }
      }
      // A hole with a reading chosen for it: what is shown and read is that piece alone.
      function pieceMode() { return !!hole && chosen !== null && chosenLatex !== null; }
      function readingText() { return pieceMode() ? chosenLatex : field.value; }
      // The reading as it goes in the hole's place: braced where a bare argument was, apart
      // from a command name before or after it, after the formula with a space.
      function fitPiece(latex) {
        var before = hole.base.slice(0, hole.s), after = hole.base.slice(hole.e), piece = latex;
        if (hole.free) return (!before.trim() || /\s$/.test(before) ? "" : " ") + piece;
        if (hole.s === hole.e)             // at the cursor: a new piece, apart from what is either side of it
          return (before && !/[\s{(\[]$/.test(before) ? " " : "") + piece + (after && !/^[\s})\]]/.test(after) ? " " : "");
        if (hole.braces) return "{" + piece + "}";
        if (/\\[A-Za-z]+$/.test(before) && /^[A-Za-z]/.test(piece)) piece = " " + piece;
        if (/\\[A-Za-z]+$/.test(piece) && /^[A-Za-z]/.test(after)) piece += " ";
        return piece;
      }
      // An option of the formula, known by the place in the text of what it is of, where it
      // stands once the text from s to e has changed length by delta: past it, moved;
      // around it, stretched; before it, as it was; within it, gone with what it was of.
      function shiftChoices(choices, s, e, delta) {
        var out = {};
        Object.keys(choices || {}).forEach(function (k) {
          var m = /^(.*)@(\d+)-(\d+)$/.exec(k);
          if (!m) return;
          var a = +m[2], b = +m[3];
          if (b <= s) out[k] = choices[k];
          else if (a >= e) out[m[1] + "@" + (a + delta) + "-" + (b + delta)] = choices[k];
          else if (a <= s && b >= e) out[m[1] + "@" + a + "-" + (b + delta)] = choices[k];
        });
        return out;
      }
      // Free ink, with a formula already drawn: what its place against the formula means.
      // A bar under a piece with ink under the bar: a fraction over it (a bar over a piece,
      // ink over the bar: over it); smaller, at a piece's top-right corner: its exponent, at
      // its bottom-right: its subscript; beside a piece: after it (before it, on its left) -
      // a product.  null: nowhere in particular (after the formula).
      function placeInkAt(list) {
        if (!hole || !hole.free || !hole.base.trim() || !formulaBox || !pieces.length || !list.length) return null;
        var tol = 6 / zoom, I = boxOf(list);
        for (var i = 0; i < list.length; i++) {
          var S = boxOf([list[i]]), sw = S.maxX - S.minX, sh = S.maxY - S.minY;
          if (sw < 18 / zoom || sw < 3 * Math.max(sh, 1 / zoom)) continue;
          var rest = list.filter(function (s, k) { return k !== i; });
          if (!rest.length) continue;
          var R = boxOf(rest), under = R.minY >= S.maxY - tol, over = R.maxY <= S.minY + tol;
          if (!under && !over) continue;
          var bar = null, bars = [];
          pieces.forEach(function (q) {
            var r = q.rect, shared = Math.min(r.x + r.w, S.maxX) - Math.max(r.x, S.minX);
            if (shared < 0.5 * r.w) return;
            var gap = under ? S.minY - (r.y + r.h) : r.y - S.maxY;
            if (gap < -tol || gap > Math.max(r.h, 24 / zoom)) return;
            bars.push(q);
            if (!bar || r.w * r.h > bar.rect.w * bar.rect.h) bar = q;
          });
          if (bar) return { kind: under ? "over" : "under", target: { s: bar.s, e: bar.e }, s: bar.s, e: bar.e, read: rest, nest: { s: bar.s, e: bar.e }, others: bars.slice().sort(function (p1, p2) { return p2.rect.w * p2.rect.h - p1.rect.w * p1.rect.h; }) };
        }
        var near = function (q) { return q.rect.y - I.maxY < q.rect.h && I.minY - (q.rect.y + q.rect.h) < q.rect.h; };
        var tolX = Math.max(12 / zoom, 0.25 * (I.maxX - I.minX));
        var lefts = pieces.filter(function (q) { return q.rect.x + q.rect.w <= I.minX + tolX && near(q); });
        if (lefts.length) {
          // each piece on the ink's left, as what the ink would be to it - its exponent (the ink
          // small, high), its subscript (small, low) or what comes after it - and how near the ink
          // is to where that would be written, in the piece's own heights: the nearest is the one
          var ih = I.maxY - I.minY;
          var scored = lefts.map(function (q) {
            var r = q.rect, kind = "after", ax = r.x + r.w, ay = r.y + r.h / 2, by = (I.minY + I.maxY) / 2;
            var close = I.minX - (r.x + r.w) <= Math.max(28 / zoom, r.w);          // a script is written right by its piece
            if (close && ih <= 0.9 * r.h && I.maxY <= r.y + 0.55 * r.h) { kind = "sup"; ay = r.y + 0.25 * r.h; by = I.maxY; }
            else if (close && ih <= 0.9 * r.h && I.minY >= r.y + 0.45 * r.h) { kind = "sub"; ay = r.y + 0.75 * r.h; by = I.minY; }
            return { q: q, kind: kind, d: Math.hypot(I.minX - ax, by - ay) / Math.max(r.h, 1) };
          }).sort(function (x1, x2) { return x1.d - x2.d; });
          var best = scored[0], bq = best.q;
          var alike = scored.filter(function (o) { return o.d <= best.d + 1; }).map(function (o) { return o.q; });
          if (best.kind !== "after") return { kind: best.kind, target: { s: bq.s, e: bq.e }, s: bq.s, e: bq.e, nest: { s: bq.s, e: bq.e }, others: alike };
          // after it: after all that ends where it ends (not inside an exponent the piece is the end of)
          var outer = bq;
          pieces.forEach(function (q) {
            if (q.s <= bq.s && q.e >= bq.e && Math.abs(q.rect.x + q.rect.w - (bq.rect.x + bq.rect.w)) <= 2 / zoom && q.e - q.s > outer.e - outer.s) outer = q;
          });
          return { kind: "after", target: { s: outer.s, e: outer.e }, s: outer.e, e: outer.e, nest: { s: bq.s, e: bq.e }, others: alike };
        }
        var rights = pieces.filter(function (q) { return q.rect.x >= I.maxX - tolX && near(q); });
        if (rights.length) {
          var start = Math.min.apply(null, rights.map(function (q) { return q.rect.x; }));
          var atStart = rights.filter(function (q) { return Math.abs(q.rect.x - start) <= 2 / zoom; });
          var first = atStart.reduce(function (a, b) { return b.e - b.s > a.e - a.s ? b : a; });
          var talls = Math.max.apply(null, atStart.map(function (q) { return q.rect.h; }));
          var firstInner = atStart.filter(function (q) { return q.rect.h >= 0.6 * talls; }).reduce(function (a, b) { return b.e - b.s < a.e - a.s ? b : a; });
          return { kind: "before", target: { s: first.s, e: first.e }, s: first.s, e: first.s, nest: { s: firstInner.s, e: firstInner.e }, others: atStart };
        }
        return null;
      }
      // The pieces free ink might be read together with: the one picked first, the others
      // at the same edge (or under the same bar), and what holds it, up to the whole formula.
      function placeInk(list) {
        var pl = placeInkAt(list);
        if (!pl || !pl.nest) return pl;
        var out = [];
        var add = function (r) { if (r && !out.some(function (o) { return o.s === r.s && o.e === r.e; })) out.push({ s: r.s, e: r.e }); };
        add(pl.nest);
        (pl.others || []).forEach(add);        // nearest first
        for (var up = pl.nest, k = 0; k < 4 && up; k++) { up = enclosing(up); add(up); }
        pl.options = out.slice(0, 5);
        return pl;
      }
      // The text a reading goes in as, at the place free ink stands.
      function placedText(pl, latex) {
        var base = hole.base, b = base.slice(pl.target.s, pl.target.e);
        var single = /^(?:[A-Za-z0-9]|\\[A-Za-z]+|\{[^{}]*\})$/.test(b.trim());
        var based = single ? b : "\\left(" + b + "\\right)";
        if (pl.kind === "sup") return (/_\{[^{}]*\}$/.test(b) ? b : based) + "^{" + latex + "}";
        if (pl.kind === "sub") return (/\^\{[^{}]*\}$/.test(b) ? b : based) + "_{" + latex + "}";
        if (pl.kind === "over") return "\\frac{" + b + "}{" + latex + "}";
        if (pl.kind === "under") return "\\frac{" + latex + "}{" + b + "}";
        if (pl.kind === "nest") {            // the piece taken into the reading: the reading in its place
          var pre = base.slice(0, pl.s), post = base.slice(pl.e);
          return (pre && /[A-Za-z0-9}]$/.test(pre) && /^[A-Za-z0-9\\]/.test(latex) ? " " : "") + latex +
                 (post && /^[A-Za-z0-9\\]/.test(post) ? " " : "");
        }
        var before = base.slice(0, pl.s), after = base.slice(pl.s);
        if (pl.kind === "before") return latex + (after && !/^[\s})\]]/.test(after) ? " " : "");
        return (before && !/[\s{(\[]$/.test(before) ? " " : "") + latex + (after && !/^[\s})\]]/.test(after) ? " " : "");
      }
      function putPiece(latex) {
        if (hole.free && hole.placement) {            // free ink with a place against the formula: in there
          var pl = hole.placement, rep = placedText(pl, latex);
          hole.s = pl.s;
          hole.e = pl.e;
          chosen = rep;
          chosenLatex = latex;
          setText(hole.base.slice(0, pl.s) + rep + hole.base.slice(pl.e));
          return;
        }
        if (hole.free) hole.s = hole.e = hole.base.length;
        var piece = fitPiece(latex);
        chosen = piece;
        chosenLatex = latex;
        setText(hole.base.slice(0, hole.s) + piece + hole.base.slice(hole.e));
      }
      function markChosen(button) {
        var old = cands.querySelectorAll(".ink-cand-edit");
        for (var k = 0; k < old.length; k++) old[k].remove();
        var all = cands.querySelectorAll(".ink-cand");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle("ink-chosen", all[i] === button);
          all[i].setAttribute("aria-selected", all[i] === button ? "true" : "false");
        }
        if (!button) return;
        var edit = h("button", { type: "button", class: "ink-cand-edit", title: "Edit this reading's LaTeX", "aria-label": "Edit this reading's LaTeX" });
        edit.innerHTML = toolIcon("pen");
        edit.addEventListener("click", function () { editReading(); });
        button.parentNode.insertBefore(edit, button.nextSibling);
      }
      function choose(c, button) {
        markChosen(button);
        picks = { choices: {}, constants: {} };
        piecePicked = false;
        if (hole) { putPiece(c.latex); if (c.nested) reread(); else show(c.reading); return; }   // the reading of what is written (with the piece it took in)
        chosen = c.latex;
        chosenLatex = null;
        setText(c.latex);
        show(c.reading);
      }
      // A reading typed over: in the row as a reading of its own, chosen.
      function addEdited(latex) {
        var old = cands.querySelector(".ink-edited");
        if (old) old.remove();
        var b = h("button", { type: "button", class: "ink-cand ink-edited", role: "option", title: latex });
        typeset(b, latex, latex);
        b.addEventListener("click", function () {
          record();
          markChosen(b);
          picks = { choices: {}, constants: {} };
          piecePicked = false;
          if (hole) putPiece(latex); else { chosen = latex; setText(latex); }
          reread();
        });
        cands.appendChild(b);
        markChosen(b);
        return b;
      }
      var editor = null;                  // the box a reading is typed over in, while open
      function closeEditor() { if (editor) { editor.remove(); editor = null; } }
      function editReading() {
        closeEditor();
        var input = h("input", { class: "ink-cand-input", type: "text", spellcheck: "false", autocomplete: "off", autocapitalize: "off",
          "aria-label": "The reading's LaTeX" });
        input.value = readingText();
        var ok = toolButton(h, "done", "Use this LaTeX");
        var cancel = h("button", { type: "button", class: "ink-cand-cancel", title: "Leave the reading as it was" }, ["Cancel"]);
        editor = h("div", { class: "ink-cand-editor" }, [input, ok, cancel]);
        cands.parentNode.insertBefore(editor, cands.nextSibling);
        var use = function () {
          var latex = input.value.trim();
          closeEditor();
          if (!latex || latex === readingText()) return;
          record();
          picks = { choices: {}, constants: {} };
          piecePicked = false;
          if (hole) putPiece(latex); else { chosen = latex; setText(latex); }
          addEdited(latex);
          renderFormula();
          reread();
        };
        ok.addEventListener("click", use);
        cancel.addEventListener("click", closeEditor);
        input.addEventListener("keydown", function (ev) {
          ev.stopPropagation();                                   // the editor's keys are not for the box
          if (ev.key === "Enter") { ev.preventDefault(); use(); }
          else if (ev.key === "Escape") { ev.preventDefault(); closeEditor(); }
        });
        input.focus();
        input.select();
      }
      function reread() {
        var my = ++seq;
        if (hole && !pieceMode()) { show(null); return; }        // a hole with nothing read in it yet: no reading
        var text = readingText();
        if (!text.trim()) { show(null); return; }
        api.call("read", { latex: text, choices: picks.choices, constants: picks.constants }, { quiet: true })
          .then(function (res) { if (my === seq) show(res.reading); }, function () {});
      }
      field.addEventListener("input", function () {
        if (Date.now() - typedAt > 1000) record(shownText);   // the text before this run of typing
        typedAt = Date.now();
        shownText = field.value;
        for (var i = 0; i < cands.children.length; i++) cands.children[i].classList.remove("ink-chosen");
        picks = { choices: {}, constants: {} };     // new text: the old picks do not apply to it
        if (hole) dropHole();
        closeEditor();
        sel = null;
        touched = true;
        seq++;                                     // a reading still on its way is of the text before: dropped
        last = null;                               // nor is the one shown this text's: nothing to put in until this one is read
        updateInsert();
        renderFormula();
        refit();
        changedTools();
        clearTimeout(readTimer);
        readTimer = setTimeout(reread, 400);
      });
      field.addEventListener("keydown", function (ev) {
        ev.stopPropagation();                        // the editor's keys are not for the box
        if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && /^[zy]$/i.test(ev.key)) {   // the pad's history, typing in it included
          ev.preventDefault();
          if (ev.key.toLowerCase() === "y" || ev.shiftKey) redo(); else undo();
          return;
        }
        if (ev.key === "Enter") { ev.preventDefault(); applyToEditor(); }
      });

      function placementWords() {
        var pl = hole.placement;
        if (!pl) return "What is written, to go after the formula:";
        var what = hole.base.slice(pl.target.s, pl.target.e).trim();
        if (what.length > 32) what = what.slice(0, 31) + "\u2026";
        return "What is written, " + ({ after: "after ", before: "before ", sup: "as the exponent of ", sub: "as the subscript of ",
                                        over: "under a bar, as a fraction over ", under: "over a bar, as the numerator over ",
                                        nest: "read together with " }[pl.kind]) + what + ":";
      }
      function show(reading) {
        last = reading || null;
        readingOf.textContent = !reading ? ""
          : !pieceMode() ? (hole ? "What is written:" : "The formula:")
          : hole.free ? (!hole.base.trim() ? "What is written:" : placementWords())
          : hole.s === hole.e ? "What is written, at the cursor:"
          : "What is written, in the selected piece's place:";
        if (!reading) src.textContent = "";
        else if (reading.ok) src.textContent = reading.src;
        else src.textContent = reading.error || "This could not be read";
        src.className = "ink-src" + (reading && !reading.ok ? " error" : "");
        options(reading);
        updateInsert();
        updateSummary();
      }
      // The reading's options, as the LaTeX panel offers them: a menu per
      // ambiguity with the whole expression under each alternative, a switch
      // per constant name.  A pick reads the text again with it.
      function options(reading) {
        ambig.textContent = "";
        consts.textContent = "";
        parseBlock.hidden = true;
        if (!reading || !reading.ok) return;
        picks.choices = Object.assign({}, reading.choices || {});   // every decision, so the next pick changes only itself
        (reading.ambiguities || []).forEach(function (a) {
          var sel = h("select", { class: "ink-choice", title: "How to read " + a.fragment });
          a.options.forEach(function (o, i) {
            var opt = h("option", { value: String(i) }, [o.invalid ? "(not a reading)" : o.src]);
            if (o.invalid) opt.disabled = true;
            if (i === a.choice) opt.selected = true;
            sel.appendChild(opt);
          });
          sel.addEventListener("change", function () { picks.choices[a.key] = parseInt(sel.value, 10); if (pieceMode()) piecePicked = true; reread(); });
          ambig.appendChild(h("label", { class: "ink-point" }, [h("code", { class: "ink-fragment" }, [a.fragment]), " → ", sel]));
        });
        (reading.constants || []).forEach(function (c) {
          var box = h("input", { type: "checkbox" });
          box.checked = !!c.on;
          box.addEventListener("change", function () { picks.constants[c.name] = box.checked; if (pieceMode()) piecePicked = true; reread(); });
          consts.appendChild(h("label", { class: "ink-const", title: c.label }, [box, " ", h("code", {}, [c.name]), " is " + c.value + " (" + c.label + ")"]));
        });
        parseBlock.hidden = !ambig.children.length && !consts.children.length;
      }

      // Apply: nothing to put in without a reading of the text, nor when the pad shows the editor's formula as it is.
      function updateInsert() {
        apply.disabled = !(last && last.ok) || (!hole && field.value === editorLatex());
      }
      function updateSummary() {
        note.title = note.textContent;
        sheetSummary.textContent = last && last.ok ? last.src : (note.textContent || "Readings");
        sheetChevron.innerHTML = chevronIcon(folded ? "up" : "down");
        sheetHead.setAttribute("aria-expanded", folded ? "false" : "true");
        sheet.classList.toggle("ink-folded", folded);
      }
      sheetHead.addEventListener("click", function () { folded = !folded; updateSummary(); });

      // What an applying did: the formula as it was and as it now is, to keep or to take back.
      // As the history shows a step: the formula before and after, what went marked red in
      // the one and what came marked green in the other (the editor's own diff and marking).
      function showApplied(was, now) {
        wasFormula.setAttribute("data-latex", was.plain);
        nowFormula.setAttribute("data-latex", now.plain);
        var ed = api.editor, diff = null;
        try { diff = ed && ed._diffNodes && was.nodes && now.nodes ? ed._diffNodes(was.nodes, now.nodes) : null; }
        catch (e) { diff = null; }
        if (ed && ed._renderMarked && was.latex && now.latex) {
          try {
            wasFormula.innerHTML = ed._renderMarked(was.latex, diff && diff.oldKept, "rep-removed");
            nowFormula.innerHTML = ed._renderMarked(now.latex, diff && diff.newKept, "rep-added");
          } catch (e) { typeset(wasFormula, was.plain, was.plain); typeset(nowFormula, now.plain, now.plain); }
        } else {
          typeset(wasFormula, was.plain, was.plain);
          typeset(nowFormula, now.plain, now.plain);
        }
        appliedRow.hidden = false;
      }
      function editorStep() {             // the formula as the editor has it now: to mark a change with
        var st = api.state && api.state();
        return { latex: st && st.latex, plain: (st && st.latex_plain) || "", nodes: st && st.nodes };
      }
      function hideApplied() { appliedRow.hidden = true; }
      keepBtn.addEventListener("click", hideApplied);
      undoApplied.addEventListener("click", function () { hideApplied(); undo(); });
      function applyToEditor() {
        if (apply.disabled) return;
        var before = snapshot(), wasStep = editorStep();
        if (hole) { closeEditor(); commitHole(); }   // the ink's reading into the formula first: one edit with the applying
        var payload = { latex: field.value, path: "/", choices: picks.choices, constants: picks.constants };
        api.call("insert", payload).then(function () {
          // the view stays as it is - the pad, full screen or not, where it was on the page
          before.applied = { seq: editorSeq() };   // Undo past it takes the formula in the editor back as well
          push(before);                            // Undo brings the pad back as it was, ink and all
          holdSheet();                             // the readings, emptied and read anew, take no less room meanwhile
          strokes = []; current = null;
          element.setAttribute("data-strokes", "0");
          reset();
          loadFromEditor();                        // the pad shows the formula as the editor now has it
          setMode("select");                       // and is back to selecting in it, not writing
          note.textContent = "Applied.";
          updateSummary();
          showApplied(wasStep, editorStep());      // what it did, to keep or to take back
        }, function (e) {
          note.textContent = String((e && e.message) || e);
          note.className = "ink-note error";
          updateSummary();
        });
      }
      apply.addEventListener("click", applyToEditor);

      // ---- full screen --------------------------------------------------------------------
      function onKey(ev) {
        if (ev.key !== "Escape") return;
        ev.preventDefault();
        ev.stopPropagation();
        setFull(false);
      }
      function setFull(on) {
        on = !!on;
        if (on === full) return;
        full = on;
        element.classList.toggle("ink-full", on);
        fullBtn.innerHTML = expandIcon(on);
        var title = on ? "Leave full screen (Esc)" : "Full screen: the writing area as large as the screen";
        fullBtn.setAttribute("title", title);
        fullBtn.setAttribute("aria-label", title);
        var page = document.documentElement;
        if (on) {
          pageOverflow = page.style.overflow;
          page.style.overflow = "hidden";            // the page under the panel stays put
          document.addEventListener("keydown", onKey, true);
        } else {
          page.style.overflow = pageOverflow || "";
          document.removeEventListener("keydown", onKey, true);
        }
        if (api.editor && api.editor._nativeFullscreen) api.editor._nativeFullscreen(on);   // the Android app hides its bars
        updateInsert();
        setTimeout(fitPad, 0);
      }
      fullBtn.addEventListener("click", function (ev) { ev.preventDefault(); setFull(!full); });
      fullBtn.innerHTML = expandIcon(false);
      fullBtn.setAttribute("title", "Full screen: the writing area as large as the screen");
      fullBtn.setAttribute("aria-label", "Full screen");

      setMode(mode);                     // the Pen, pressed from the start
      if (!canRead) canvas.classList.add("ink-off");
      reset();
      loadFromEditor();                  // the editor's formula, to edit
      setTimeout(fitPad, 0);

      return {
        element: element,
        title: "Handwriting",
        help: "<section><h3>The tools</h3><ul>"
          + "<li>" + toolIcon("select", 16) + " <b>Select</b>, " + toolIcon("pen", 16) + " <b>Pen</b> and " + toolIcon("erase", 16) + " <b>Eraser</b> say what a tap or a stroke on the pad does - one at a time, the pressed one.</li>"
          + "<li>The pad starts with <b>Select</b>. " + arrowIcon("up", 16) + " " + arrowIcon("down", 16) + " " + arrowIcon("left", 16) + " " + arrowIcon("right", 16) + " go as the formula panel's arrows: up to what holds the selection, down back inside (on a piece with nothing inside, a cursor after it), left and right to the pieces beside it - or, with the cursor, to the next place; with nothing selected, a cursor at the start or the end. The arrow keys do the same once the pad has been tapped, and Shift with left and right shrinks and grows a range.</li>"
          + "<li>With <b>Select</b>, a tap selects a piece of the formula, a second tap what holds it; a drag from one piece to another selects the pieces beside each other between them; a tap near the left or right edge of a piece, or beside the formula, puts the cursor there; a tap on nothing else clears both.</li>"
          + "<li>With the <b>Pen</b> and a piece selected, what is written - anywhere on the pad - takes its place: the piece gives way to room to write in, the ink goes into it, the room grows as the ink nears its edges, and the reading takes the piece's place in the LaTeX. Pressing the Pen with a piece selected (or the cursor placed) makes that room at once, and every stroke goes there until Done. With nothing selected the ink is free, and where it is written against the formula says where its reading goes: beside a piece, after it (before it, on its left) as a product; smaller at a piece's top-right corner, its exponent - at its bottom-right, its subscript; under a bar drawn under a piece, a fraction over that piece (over a bar over it, a fraction over the ink). The piece is outlined, and the line over the reading says it. What is written is read together with that piece - the piece drawn into the ink as a stand-in the model reads - so that a bar and what is under it read as a fraction over the piece, a small letter at its corner as its exponent; the rules of thumb above are for when that reading does not take the piece in. When it might be another piece, the row over the readings offers them - and <i>alone</i>, the ink read by itself after the formula: a tap reads the ink again with the one tapped. Written nowhere in particular, the reading goes after the formula.</li>"
          + "<li>With the <b>Pen</b> and the cursor, what is written - anywhere on the pad - goes in at the cursor, as a new piece of the formula, in a room made for it there. The keyboard button beside the LaTeX line puts the typing cursor at the same place.</li>"
          + "<li>With the <b>Eraser</b>, every stroke the pointer passes over goes. A pen turned round erases too.</li>"
          + "<li>" + toolIcon("done", 16) + " <b>Done</b> puts the reading of the ink into the formula for good, and the ink goes; with Select, a tap does the same.</li>"
          + "<li>" + toolIcon("undo", 16) + " <b>Undo</b> and " + toolIcon("redo", 16) + " <b>Redo</b> go back and forth through every edit in the pad: strokes, the eraser, selections, readings put in, typing in the LaTeX line (Ctrl+Z and Ctrl+Y there too), applying - past an applying, the formula in the editor goes back too, unless it has been edited there since.</li>"
          + "<li>" + toolIcon("clear", 16) + " <b>Clear</b> takes all the ink away.</li>"
          + "<li><b>\u2212 100% +</b> zoom the pad out and in about the middle of what is in sight; the level goes back to 100%.</li>"
          + "<li>" + toolIcon("read", 16) + " <b>Read</b> reads what is written now, without waiting for the pause.</li>"
          + "<li>" + toolIcon("type", 16) + " <b>Type</b>, beside the LaTeX line: the selected piece's LaTeX, selected there, to type over. " + toolIcon("load", 16) + " <b>From the editor</b>: the editor's formula into the pad again.</li>"
          + "<li>" + toolIcon("full", 16) + " <b>Full screen</b>, in the area's corner: the writing area as large as the screen, the tools on top and the readings in a sheet at the bottom that folds away. Esc or the same button comes back.</li>"
          + "</ul></section>"
          + "<section><h3>The formula in the pad</h3><ul>"
          + "<li>The pad opens with the editor's formula, drawn from the LaTeX in the line above it: type there, or write in the pad - the two mix.</li>"
          + "<li><b>Apply to the formula</b> makes the pad's formula the editor's, and the view stays as it is (the pad back to Select); Enter in the LaTeX line does the same. What it did is then shown - the formula as it was, and as it now is - to <b>Keep</b> or to <b>Undo the change</b>, which takes it back in the editor too.</li>"
          + "</ul></section>"
          + "<section><h3>Writing by hand</h3><ul>"
          + "<li>A moment after the pen lifts, what is written is read. The best reading comes first: pick the one you wrote, or press the pen beside it to edit its LaTeX. The line under the readings is what SymPy gets of what is written - of that piece alone while it is being written, of the whole formula otherwise - with a menu for each part that can be read more than one way and a switch for each constant name. An option picked for a piece stays with it in the formula.</li>"
          + "<li>Nearing the right or the bottom edge, the area makes room beyond it, and a stroke ending near an edge of what is in sight scrolls the pad on a little; a small button in the middle of an edge scrolls it that way, and so does the wheel. A room made to write in comes into the middle of the pad.</li>"
          + "<li>Two fingers never write: pinch to zoom the area in or out, drag with two fingers to move it about (a pinch on a trackpad zooms too).</li>"
          + "<li>The reading is done by math-ocr's stroke model. It reads one formula at a time, and mixes up look-alike glyphs most (<code>1</code> and <code>|</code>, <code>V</code> and <code>v</code>).</li>"
          + "</ul></section>"
          + (status.notice ? '<section><h3>About the model</h3><p style="white-space: pre-wrap">' + noticeHtml(status.notice) + "</p></section>" : ""),
        onState: function () {           // the editor's formula, followed until the pad is edited
          if (!touched && !hole && !strokes.length && field.value !== editorLatex()) loadFromEditor();
        },
        destroy: function () {
          setFull(false);
          clearTimeout(timer);
          clearTimeout(readTimer);
          if (resizer) resizer.disconnect(); else window.removeEventListener("resize", fitPad);
        }
      };
    }
  };
})());
