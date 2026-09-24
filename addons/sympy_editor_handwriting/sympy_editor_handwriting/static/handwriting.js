/*
 * SPDX-License-Identifier: AGPL-3.0-or-later
 * Copyright (c) 2026 Francesco Bonazzi
 *
 * sympy-editor add-on "handwriting": write on the formula by hand.
 *
 * There is no pad of its own: the ink goes on the editor's own formula.  The
 * Pen, in the editor's tools, takes the pointer and gives the formula area
 * room to write in; with it off nothing here touches the editor, which is the
 * editor it was.  Each stroke is kept as points [x, y, t] (the view's own
 * pixels, milliseconds from the first stroke); a pause after the pen lifts
 * sends them to Python (method "write"), where math-ocr's stroke model reads
 * them.
 *
 * Where a reading goes is the editor's own answer: over the selected
 * sub-expression (or the selected range), at the cursor, or - with neither -
 * against the piece of the formula the ink is written by, which is drawn into
 * the strokes as a stand-in (its box, or a triangle read as \Delta) so that the model
 * reads the ink together with it: a fraction over it, its exponent, a
 * product.  Python puts that piece's LaTeX in the stand-in's place.
 *
 * The best reading goes into the formula at once - the formula changes, the
 * ink goes - and the panel under the editor holds what came of it: the
 * formula before and after, marked as the history marks a step, to keep or to
 * take back; the other readings, to pick another; the pieces the ink can be
 * read with; and the ways the LaTeX itself can be read.
 */
SympyEditor.registerAddon("handwriting", (function () {
  // The tools' icons, drawn as the editor draws its own (16 x 16, the text's
  // colour, round ends).
  var TOOL_PATHS = {
    pen: "M3.2 12.8l.9-3.3 7.1-7.1a1.5 1.5 0 0 1 2.1 2.1l-7.1 7.1ZM9.9 3.3l2.8 2.8",
    erase: "M2.7 10.3 8.9 4.1a1.3 1.3 0 0 1 1.8 0l2.3 2.3a1.3 1.3 0 0 1 0 1.8l-5 5H5.4ZM5.8 7.2l4 4M7.9 13.2h5.6",
    clear: "M2.9 4.5h10.2M6.2 4.5V3.1h3.6v1.4M4.3 4.5l.7 8.7a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.7-8.7M6.9 6.9v4.7M9.1 6.9v4.7",
    undo: "M3.2 7.6h6.4a3.2 3.2 0 0 1 0 6.4H6.1M6.1 4.4 3 7.6l3.1 3.2",
    redo: "M12.8 7.6H6.4a3.2 3.2 0 0 0 0 6.4h3.5M9.9 4.4 13 7.6l-3.1 3.2"
  };
  function toolIcon(name, size) {
    // se-icon is what the editor sizes its own tools' icons with; hw-icon is
    // for this add-on's own rules (the guide draws them in a line of text).
    var dims = size ? ' width="' + size + '" height="' + size + '" style="vertical-align: -0.2em"' : "";
    return '<svg class="' + (size ? "hw-icon" : "se-icon hw-icon") + '" viewBox="0 0 16 16"' + dims +
      ' aria-hidden="true" focusable="false">' +
      '<path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" d="' +
      TOOL_PATHS[name] + '"/></svg>';
  }
  function plain(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function downIcon() {
    return '<svg class="se-icon hw-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
      '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ' +
      'd="M8 3v9M4.2 8.2 8 12l3.8-3.8"/></svg>';
  }
  /** What can read strokes in the page itself, when the add-on's Python
   *  cannot or is not the one asked: the app's own reader (the host bridge,
   *  Apple's Vision behind it) or the browser's Handwriting Recognition API.
   *  Both read *text*, a line at a time - they know nothing of fractions or
   *  exponents - so what comes back is the reading of what was written, for
   *  the LaTeX reader to turn into SymPy.
   *
   *  Answers `{label, read(strokes) -> Promise<[{latex, raw}]>}`, or null. */
  function hostReader() {
    var app = window.SympyEditorApp;
    if (app && app.recognizeInk) {
      return {
        label: "this device's own reader",
        read: function (strokes) {
          return new Promise(function (resolve, reject) {
            var token = "i" + Date.now() + Math.random().toString(36).slice(2, 6);
            hostReader.waiting[token] = { resolve: resolve, reject: reject };
            try { app.recognizeInk(token, JSON.stringify(strokes)); }
            catch (e) { delete hostReader.waiting[token]; reject(e); }
          });
        }
      };
    }
    if (typeof navigator.createHandwritingRecognizer === "function") {
      return {
        label: "the browser's own reader",
        read: async function (strokes) {
          var recognizer = hostReader.browser;
          if (!recognizer) {
            recognizer = hostReader.browser = await navigator.createHandwritingRecognizer({
              languages: ["en"], recognitionType: "text", inputType: "mouse", alternatives: 4
            });
          }
          var drawing = recognizer.startDrawing({ recognitionType: "text", inputType: "mouse", alternatives: 4 });
          strokes.forEach(function (points) {
            var stroke = new window.HandwritingStroke();
            points.forEach(function (p) { stroke.addPoint({ x: p[0], y: p[1], t: Math.round(p[2]) }); });
            drawing.addStroke(stroke);
          });
          var said = await drawing.getPrediction();
          return (said || []).map(function (p) { return { latex: p.text, raw: p.text }; });
        }
      };
    }
    return null;
  }
  //: Where a host answers a reading of its own (SympyEditor.inkRead below).
  hostReader.waiting = {};
  hostReader.browser = null;

  /** A host that read the ink answers here - `SympyEditor.inkRead(token,
   *  json)`, beside the editor's own openedFile and keptValue: the token it
   *  was given, and what it made of the strokes ({"candidates": [{"latex"}]}
   *  as JSON, or {"error": "..."} when it could not read them). */
  function inkRead(token, json) {
    var waiting = hostReader.waiting[token];
    if (!waiting) return false;
    delete hostReader.waiting[token];
    var said = null;
    try { said = JSON.parse(json || "null"); } catch (e) { said = null; }
    if (!said || said.error) waiting.reject(new Error((said && said.error) || "The reader said nothing"));
    else waiting.resolve(said.candidates || []);
    return true;
  }
  if (window.SympyEditor && !window.SympyEditor.inkRead) window.SympyEditor.inkRead = inkRead;

  function reveal(el) {
    if (!el || !el.scrollIntoView) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    try { el.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" }); }
    catch (e) { el.scrollIntoView(false); }
  }

  // The add-on's page of the editor's help overlay, opened by the "?" in the
  // strip under the formula.
  function HELP(status) {
    return "<section><h3>Writing on the formula</h3><ul>"
      + "<li>" + toolIcon("pen", 16) + " <b>Write</b>, among the editor's tools, takes the pointer - its button pulses in one quiet colour for as long as it is on (and so does <i>the readings</i>, the button that takes you down to what was read), so writing mode is plain at a glance; with it off the editor is the editor it was - the formula is tapped, selected and edited in the usual way.</li>"
      + "<li>The formula itself makes room: the area grows, and it opens a space where what is written will go - after the selection, at the cursor, or by the piece the ink is written against - drawn as a box in light dashes, which widens as you write. A box that opens past the edge of the screen is scrolled towards the middle (wider, when it is at the end of the formula), and a stroke that ends by the edge opens more space ahead and scrolls the box back into sight. Nothing is sent while it is open: it closes when the ink goes.</li>"
      + "<li>A tap, with nothing written yet, still selects a piece or puts the cursor between two, the Pen on or off: choose where to write, then write there. (Once there is ink on the formula a tap is a dot.)</li>"
      + "<li>A moment after the pen lifts what is written is read, and the readings are offered under the formula, the best first. <b>Apply to the formula</b> puts the one picked in - nothing changes before that. Once one is in, picking another changes the formula to it instead (the one before is taken back, so they never pile up).</li>"
      + "<li>The choices come in the order one makes them: first the piece of the formula what is written goes with (writing freely, with nothing selected), then the reading, then the ways its LaTeX can be read - and then Apply.</li>"
      + "<li>Writing on a formula that fills the screen leaves the readings out of sight: a moment after the pen rests a button rises at the foot of the screen, and a press goes down to them. Writing again sends it away.</li>"
      + "<li>Written over a selected <b>operator</b> (the = of an equation, a +), the ink is read as the operator that takes its place \u2014 =, &lt;, &gt;, \u2264, \u2265, \u2260, +, \u2212, \u00b7, / \u2026 \u2014 with nothing to read it together with.</li>"
      + "<li>Where it goes is what the editor says: over the selected sub-expression (or the selected range) - which is hidden, its place kept, while you write over it, and comes back only if the writing is discarded (the ink cleared and the pen put away) -, at the cursor, and - with neither - against the piece of the formula it is written by. That piece is outlined, and it is read <i>together with</i> the ink: a bar under it with ink under the bar is a fraction over it, a small letter at its top-right corner its exponent, a letter beside it a product. <b>Read with</b> offers the other pieces it might be, and <i>alone</i>: the reading by itself, after the formula.</li>"
      + "<li>" + toolIcon("erase", 16) + " <b>Erase</b> takes away the strokes the pointer passes over (a pen turned round erases too); " + toolIcon("undo", 16) + " and " + toolIcon("redo", 16) + " take back the last stroke and write it again; " + toolIcon("clear", 16) + " <b>Clear ink</b> takes all of it. The editor's own Undo is for the formula, and takes back what a reading did.</li>"
      + "<li>Two fingers on the formula zoom it while writing, as they do at any other time, and the ink is zoomed with it; so do the \u2212/100%/+ buttons and <kbd>Ctrl</kbd>+wheel.</li>"
      + "<li>What the reading did is shown under the editor - the formula as it was and as it now is, what went marked red and what came marked green - to <b>Keep</b> (which takes you back up to the formula) or to <b>Undo the change</b>; the editor's own Undo takes it back too.</li>"
      + "<li>Under the readings: what SymPy gets of the one in the formula, with the ways to read each part of the LaTeX that can be read more than one way, typeset, to pick from and a switch for each constant name. <b>\u270e LaTeX</b> opens the reading's own LaTeX to correct where a glyph was read wrong: what is typed there is read and goes into the formula like any other reading, and stays among them to pick again.</li>"
      + "<li>The reading is done by math-ocr's stroke model. It reads one formula at a time, and mixes up look-alike glyphs most (<code>1</code> and <code>|</code>, <code>V</code> and <code>v</code>).</li>"
      + "<li>Where this device has a reader of its own - the app's (Apple's Vision) or the browser's - it is offered beside the model, in the menu at the top of the strip. It reads <i>text</i>, a line at a time: it knows nothing of fractions, exponents or roots, and what it reads is taken as typed. It is there for a device that carries no model, and for a line of ordinary algebra; the model is what reads mathematics.</li>"
      + "</ul></section>"
      + (status.notice ? '<section><h3>About the model</h3><p style="white-space: pre-wrap">' + plain(status.notice) + "</p></section>" : "");
  }

  return {
    // The editor's own tools: the Pen takes the formula (and gives it room),
    // the Eraser takes strokes away, Clear takes all the ink.
    tools: [
      { cmd: "pen", label: "Write",
        title: "Write on the formula by hand: over the selection, at the cursor, or beside a piece of it",
        run: function () { this.setPen(!this.writing()); } },
      { cmd: "erase", label: "Erase",
        title: "Take away the strokes the pointer passes over (press again to write)",
        run: function () { this.setErasing(!this.erasing()); } },
      { cmd: "undo", label: "Undo stroke", title: "Take back the last stroke written",
        run: function () { this.undoStroke(); } },
      { cmd: "redo", label: "Redo stroke", title: "Write the stroke taken back again",
        run: function () { this.redoStroke(); } },
      { cmd: "clear", label: "Clear ink", title: "Take all the ink away",
        run: function () { this.clearInk(); } }
    ],

    mount: function (api) {
      var h = api.h;
      var status = (api.options && api.options.status) ||
        { available: false, reason: "The add-on's Python said nothing about its model" };
      //: What can read strokes, as the add-on's Python lists them, and which
      //: of them is asked.  A "host" engine reads in the page (hostReader).
      var engines = (api.options && api.options.engines) || [];
      var engine = (api.options && api.options.engine) || (engines[0] && engines[0].name) || "math-ocr";
      var host = hostReader();
      var canRead = false;

      function engineNamed(name) {
        for (var i = 0; i < engines.length; i++) if (engines[i].name === name) return engines[i];
        return null;
      }
      /** Whether the engine named can read here, and what to say when it cannot. */
      function engineState(name) {
        var e = engineNamed(name);
        if (!e) return { ok: !!status.available, why: status.reason || "" };
        if (e.where === "host") {
          return host ? { ok: true, why: "" }
                      : { ok: false, why: "This device offers no reader of its own to the page" };
        }
        return { ok: !!(e.status && e.status.available),
                 why: (e.status && e.status.reason) || status.reason || "" };
      }
      /** The engines worth offering: the ones that can read, and the chosen
       *  one even when it cannot (so that it says why). */
      function usableEngines() {
        return engines.filter(function (e) { return e.name === engine || engineState(e.name).ok; });
      }

      var guide;                     // the add-on's own page of the help overlay, below

      /* ---- the strip under the editor: the readings and what came of them ---- */
      var note = h("div", { class: "hw-note", "aria-live": "polite" });
      var withRow = h("div", { class: "hw-with", role: "group", "aria-label": "What the ink is read together with" });
      var withDivide = h("hr", { class: "hw-divide", hidden: "" });
      var cands = h("div", { class: "hw-cands", role: "listbox", "aria-label": "Readings, the best first" });
      var readingOf = h("div", { class: "hw-reading-of" });
      var src = h("code", { class: "hw-src", title: "What SymPy gets of the reading" });
      // The reading's own LaTeX, to correct where the model read a glyph wrong.
      var latexField = h("input", { type: "text", class: "hw-latex", spellcheck: "false",
                                    autocapitalize: "off", autocomplete: "off",
                                    "aria-label": "The LaTeX of this reading", placeholder: "the reading's LaTeX" });
      var latexBtn = h("button", { type: "button", class: "hw-latex-read", title: "Put this LaTeX in the formula instead" }, ["Read"]);
      var latexRow = h("div", { class: "hw-latexrow", hidden: "" }, [latexField, latexBtn]);
      var editBtn = h("button", { type: "button", class: "hw-edit", title: "Correct this reading's LaTeX" },
        ["\u270e LaTeX"]);
      var ambig = h("div", { class: "hw-ambig" });
      var consts = h("div", { class: "hw-consts" });
      var parseBlock = h("div", { class: "hw-parse", hidden: "" }, [
        h("div", { class: "hw-parse-label" }, ["Where this LaTeX can be read more than one way:"]), ambig, consts]);
      var wasFormula = h("span", { class: "hw-formula hw-was" });
      var nowFormula = h("span", { class: "hw-formula hw-now" });
      var keepBtn = h("button", { type: "button", class: "hw-keep", title: "Leave the formula as it now is" }, ["Keep"]);
      var backBtn = h("button", { type: "button", class: "hw-back", title: "The formula as it was" }, ["Undo the change"]);
      var applyBtn = h("button", { type: "button", class: "hw-apply", disabled: "",
                                   title: "Put the reading picked into the formula" }, ["Apply to the formula"]);
      var actions = h("div", { class: "hw-actions", hidden: "" }, [applyBtn]);
      var appliedRow = h("div", { class: "hw-applied", hidden: "" }, [
        h("div", { class: "hw-applied-row" }, [h("span", { class: "hw-applied-label" }, ["from"]), wasFormula]),
        h("div", { class: "hw-applied-row" }, [h("span", { class: "hw-applied-label" }, ["to"]), nowFormula]),
        h("div", { class: "hw-applied-ask" }, [keepBtn, backBtn])]);
      var helpBtn = h("button", { type: "button", class: "hw-help", title: "How writing by hand works" }, ["?"]);
      //: Which reader is asked, when this page has more than one (renderEngines).
      var engineMenu = h("select", { class: "hw-engine", hidden: "", title: "What reads what you write" });
      engineMenu.addEventListener("change", function () { pickEngine(engineMenu.value); });
      var element = h("div", { class: "hw-panel", "data-strokes": "0", "data-pen": "off", "data-aim": "", hidden: "" },
        [h("div", { class: "hw-head" }, [note, engineMenu, helpBtn]), withRow, withDivide, cands, readingOf,
         h("div", { class: "hw-srcrow" }, [src, editBtn]), latexRow, parseBlock, actions, appliedRow]);
      helpBtn.addEventListener("click", function () { api.showHelp(guide, "Handwriting"); });
      // The keys of a menu or a button here are the panel's own, not the formula's.
      element.addEventListener("keydown", function (ev) { ev.stopPropagation(); });

      // The way down to the readings: it comes up a moment after the pen has
      // rested, and only while they are out of sight.
      var downBtn = h("button", { type: "button", class: "hw-down", hidden: "",
                                  title: "Go to what was read", "aria-label": "Go to what was read" });
      downBtn.innerHTML = downIcon() + '<span class="hw-down-word">the readings</span>';
      document.body.appendChild(downBtn);
      var downTimer = null;

      /* ---- the ink, on the editor's own formula ---- */
      var editor = api.editor, stage = editor && editor.stage, view = editor && editor.view;
      if (editor && editor.addonHost && editor.addonHost.parentNode) {
        editor.addonHost.parentNode.insertBefore(element, editor.addonHost);
      }
      var canvas = h("canvas", { class: "hw-ink", "aria-hidden": "true" });
      if (stage) stage.appendChild(canvas);
      var ctx = canvas.getContext("2d");

      var pen = false, erasing = false;
      var strokes = [], taken = [], current = null, currentId = null, t0 = 0, dpr = 1;
      var timer = null, seq = 0, katex = null;
      var touches = {}, blocked = false, pinch = null, down = null;
      var zoomWas = (editor && editor.zoom) || 1;
      var readings = [], chosen = -1, picks = { choices: {}, constants: {} };
      var aim = null;         // where a reading goes: {kind, path, children, caret, node, options}
      var held = null;        // the ink whose reading is in the formula: {strokes, guess} - the
                              // strokes go off the formula once read, but another piece to read
                              // them with, or another reading, is still asked of them
      var room = null;        // the space opened in the formula to write in: {el, side, edge, px}
      var picked = null;      // the piece picked by hand to read the ink with ("none": alone)
      var applied = null;     // what the reading did: {before: step, latex}
      var mine = 0;           // states of our own doing: they leave the strip standing
      var puts = 0;           // the putting in flight: an older one, answered late, is let go

      api.katex().then(function (k) { katex = k; }, function () {});

      /* ---- the ink layer ---- */
      function layout() {
        if (!stage) return;
        if (editor.zoom && editor.zoom !== zoomWas) {   // zoomed: the ink grows with the formula
          var k = editor.zoom / zoomWas;
          strokes.forEach(function (s) { s.forEach(function (p) { p[0] *= k; p[1] *= k; }); });
          if (current) current.forEach(function (p) { p[0] *= k; p[1] *= k; });
          zoomWas = editor.zoom;
          sizeRoom();                       // and the space it is written in grows too
        }
        var w = stage.clientWidth, ht = stage.clientHeight;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.style.width = w + "px";
        canvas.style.height = ht + "px";
        canvas.width = Math.max(1, Math.round(w * dpr));
        canvas.height = Math.max(1, Math.round(ht * dpr));
        redraw();
      }
      // The view scrolls under the ink: the strokes keep the formula's own pixels.
      function offset() { return { x: view ? view.scrollLeft : 0, y: view ? view.scrollTop : 0 }; }
      function point(ev) {
        var r = canvas.getBoundingClientRect(), o = offset();
        return [Math.round((ev.clientX - r.left + o.x) * 10) / 10,
                Math.round((ev.clientY - r.top + o.y) * 10) / 10,
                Math.round(ev.timeStamp - t0)];
      }
      function redraw() {
        syncCover();
        var o = offset();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(dpr, 0, 0, dpr, -o.x * dpr, -o.y * dpr);
        drawRoom();
        drawAim();
        ctx.lineWidth = 2.2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.strokeStyle = getComputedStyle(canvas).color || "#1f2328";
        var all = current ? strokes.concat([current]) : strokes;
        for (var s = 0; s < all.length; s++) {
          var pts = all[s];
          ctx.beginPath();
          ctx.moveTo(pts[0][0], pts[0][1]);
          if (pts.length === 1) ctx.lineTo(pts[0][0] + 0.1, pts[0][1]);
          for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
          ctx.stroke();
        }
      }
      function accent() {
        return (getComputedStyle(element).getPropertyValue("--se-accent") || "").trim() || "9, 105, 218";
      }
      // The space the formula has opened, drawn as a box to write in: where the
      // ink goes, and how much room there is for it.
      function roomRect() {
        if (!room || !room.el || !room.px) return null;
        var c = canvas.getBoundingClientRect(), o = offset(), q = room.el.getBoundingClientRect();
        var mid = q.top + q.height / 2 - c.top + o.y;
        var least = 2.4 * em(), h2 = Math.max(q.height + 0.6 * em(), least);
        var top = mid - h2 / 2, bottom = mid + h2 / 2;
        var all = current ? strokes.concat([current]) : strokes;
        if (all.length) {                       // written past it: the box holds the ink
          var b = boxOf(all), pad = 0.25 * em();
          top = Math.min(top, b.minY - pad);
          bottom = Math.max(bottom, b.maxY + pad);
        }
        return { x: roomEdge(), y: top, w: room.px, h: bottom - top };
      }
      function drawRoom() {
        var r = roomRect();
        if (!r) return;
        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = "rgba(" + accent() + ", 0.55)";
        ctx.fillStyle = "rgba(" + accent() + ", 0.06)";
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(r.x, r.y, r.w, r.h, 6); else ctx.rect(r.x, r.y, r.w, r.h);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }
      function drawAim() {          // the piece the ink is read together with, outlined
        if (!aim || !aim.node || !strokes.length) return;
        var r = aim.node.rect;
        ctx.save();
        ctx.setLineDash([4, 3]);
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = "rgba(" + accent() + ", 0.75)";
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(r.x - 3, r.y - 2, r.w + 6, r.h + 4, 4);
        else ctx.rect(r.x - 3, r.y - 2, r.w + 6, r.h + 4);
        ctx.stroke();
        ctx.restore();
      }

      /* ---- the pieces of the formula, where they are drawn ---- */
      // A node's box: the glyphs, rules and drawings under it (a wrapper's own
      // box is not where KaTeX puts what is in a script or a fraction).
      function unionRect(root) {
        var l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
        (function walk(el) {
          var tag = el.tagName.toLowerCase();
          if (tag === "svg" || !el.firstElementChild) {
            var cls = tag === "svg" ? "" : String(el.className || "");
            if (tag === "svg" || /\b(rule|frac-line|overline-line|underline-line|hline)\b/.test(cls) ||
                (el.textContent.replace(/[\s​]/g, "") && !/\bvlist-s\b/.test(cls))) {
              var q = el.getBoundingClientRect();
              if (q.width > 0 || q.height > 0) {
                l = Math.min(l, q.left); t = Math.min(t, q.top);
                r = Math.max(r, q.right); b = Math.max(b, q.bottom);
              }
            }
            return;
          }
          for (var k = el.firstElementChild; k; k = k.nextElementSibling) walk(k);
        })(root);
        return l === Infinity ? null : { left: l, top: t, width: r - l, height: b - t };
      }
      function nodes() {          // [{path, rect}], in the formula's own pixels
        if (!view) return [];
        var c = canvas.getBoundingClientRect(), o = offset(), out = [];
        var els = view.querySelectorAll("[data-path]");
        for (var i = 0; i < els.length; i++) {
          var q = unionRect(els[i]);
          if (!q) continue;
          out.push({ path: els[i].getAttribute("data-path"),
                     rect: { x: q.left - c.left + o.x, y: q.top - c.top + o.y, w: q.width, h: q.height } });
        }
        return out;
      }
      function boxOf(list) {
        var b = null;
        list.forEach(function (s) {
          s.forEach(function (p) {
            if (!b) b = { minX: p[0], minY: p[1], maxX: p[0], maxY: p[1] };
            else {
              b.minX = Math.min(b.minX, p[0]); b.minY = Math.min(b.minY, p[1]);
              b.maxX = Math.max(b.maxX, p[0]); b.maxY = Math.max(b.maxY, p[1]);
            }
          });
        });
        return b;
      }
      // Which piece of the formula free ink is written against - and the others
      // it might be: a bar drawn under (or over) a piece with ink beyond it is a
      // fraction over that piece; ink small at a piece's corner is a script;
      // ink beside a piece goes with that piece.
      function guess(list) {
        var all = nodes();
        if (!all.length || !list.length) return null;
        var I = boxOf(list), tol = 6;
        for (var i = 0; i < list.length; i++) {
          var S = boxOf([list[i]]), sw = S.maxX - S.minX, sh = S.maxY - S.minY;
          if (sw < 18 || sw < 3 * Math.max(sh, 1)) continue;         // not a bar
          var rest = list.filter(function (s, k) { return k !== i; });
          if (!rest.length) continue;
          var R = boxOf(rest), under = R.minY >= S.maxY - tol, over = R.maxY <= S.minY + tol;
          if (!under && !over) continue;
          var bars = all.filter(function (q) {
            var r = q.rect, shared = Math.min(r.x + r.w, S.maxX) - Math.max(r.x, S.minX);
            if (shared < 0.5 * r.w) return false;
            var gap = under ? S.minY - (r.y + r.h) : r.y - S.maxY;
            return gap >= -tol && gap <= Math.max(r.h, 24);
          }).sort(function (a, b) { return b.rect.w * b.rect.h - a.rect.w * a.rect.h; });
          if (bars.length) return { node: bars[0], options: bars, read: rest };
        }
        var beside = all.filter(function (q) {
          var r = q.rect;
          return r.x + r.w <= I.minX + Math.max(12, 0.25 * (I.maxX - I.minX)) &&
                 r.y - I.maxY < r.h && I.minY - (r.y + r.h) < r.h;
        });
        if (!beside.length) return null;
        var ih = I.maxY - I.minY;
        var scored = beside.map(function (q) {
          var r = q.rect, ax = r.x + r.w, ay = r.y + r.h / 2, by = (I.minY + I.maxY) / 2;
          var close = I.minX - (r.x + r.w) <= Math.max(28, r.w);
          if (close && ih <= 0.9 * r.h && I.maxY <= r.y + 0.55 * r.h) { ay = r.y + 0.25 * r.h; by = I.maxY; }
          else if (close && ih <= 0.9 * r.h && I.minY >= r.y + 0.45 * r.h) { ay = r.y + 0.75 * r.h; by = I.minY; }
          return { q: q, d: Math.sqrt(Math.pow(I.minX - ax, 2) + Math.pow(by - ay, 2)) / Math.max(r.h, 1) };
        }).sort(function (a, b) { return a.d - b.d; });
        var best = scored[0], options = scored.filter(function (o) { return o.d <= best.d + 1; })
                                              .map(function (o) { return o.q; });
        all.forEach(function (q) {    // and what holds it: a piece's path holds its children's
          if (best.q.path.indexOf(q.path) === 0 && q.path !== best.q.path && options.indexOf(q) < 0) options.push(q);
        });
        return { node: best.q, options: options };
      }
      // The piece the ink is read together with, as the box it is drawn in:
      // Python gives it to the model - itself, to a model trained with such
      // boxes, or drawn into the ink as a triangle (\Delta) - and puts the
      // piece's own LaTeX in its place.
      function contextBox(r) { return [r.x, r.y, r.x + r.w, r.y + r.h]; }
      // The piece's siblings - the other factors of its product, the other
      // terms of its sum - with it, left to right, as boxes: Python may give
      // them all to the model, which then says which the ink goes with.
      function siblingsOf(node) {
        var cut = node.path.lastIndexOf("/"), parent = cut < 0 ? "" : node.path.slice(0, cut);
        var out = nodes().filter(function (q) {
          var c = q.path.lastIndexOf("/");
          return (c < 0 ? "" : q.path.slice(0, c)) === parent && /^\d+$/.test(q.path.slice(c + 1));
        }).sort(function (a, b) { return a.rect.x - b.rect.x; });
        return out.length > 1 ? out.map(function (q) { return { path: q.path, box: contextBox(q.rect) }; }) : null;
      }

      /* ---- the way down to the readings ---- */
      function panelSeen() {
        if (element.hidden) return true;                  // nothing to go down to
        var r = element.getBoundingClientRect();
        var h2 = window.innerHeight || document.documentElement.clientHeight;
        return r.top < h2 - 40 && r.bottom > 0;
      }
      function wantDown() { return pen && !panelSeen() && (strokes.length > 0 || readings.length > 0); }
      function askDown(after) {
        clearTimeout(downTimer);
        downTimer = setTimeout(function () { if (wantDown()) showDown(); }, after === undefined ? 1200 : after);
      }
      function showDown() {
        if (!downBtn.hidden) return;
        downBtn.hidden = false;
        requestAnimationFrame(function () { downBtn.classList.add("hw-down-on"); });
      }
      function hideDown() {
        clearTimeout(downTimer);
        downBtn.classList.remove("hw-down-on");
        if (!downBtn.hidden) setTimeout(function () { if (!downBtn.classList.contains("hw-down-on")) downBtn.hidden = true; }, 220);
      }
      downBtn.addEventListener("click", function () {
        hideDown();
        try { element.scrollIntoView({ block: "center", behavior: "smooth" }); }
        catch (e) { element.scrollIntoView(false); }
      });
      window.addEventListener("scroll", onScroll, { passive: true });
      function onScroll() { if (!downBtn.hidden && panelSeen()) hideDown(); }

      /* ---- the room to write in ---------------------------------------------
       * The formula opens where what is written will go - after the selection,
       * at the cursor, or by the piece the ink is written against - so that the
       * ink is not written over the glyphs: the piece there is given a margin,
       * and everything after it moves along.  It is the rendering that moves,
       * not the formula: nothing is sent, and it closes when the ink goes. */
      function em() { return parseFloat(getComputedStyle(view).fontSize) || 16; }
      function elementFor(path) {
        if (!view || !path) return null;
        var els = view.querySelectorAll("[data-path]");
        for (var i = 0; i < els.length; i++) if (els[i].getAttribute("data-path") === path) return els[i];
        return null;
      }
      // What the room is opened beside, and on which side of it.
      function anchor() {
        var j = editor && editor.junction;
        if (j && j.el && j.el.isConnected) return { el: j.el, side: "right" };
        var c = api.caret && api.caret();
        if (c && c.leftEl) return { el: c.leftEl, side: "right" };
        if (c && c.rightEl) return { el: c.rightEl, side: "left" };
        var r = api.range && api.range();
        if (r) {
          var paths = editor._rangePaths ? editor._rangePaths() : [];
          var last = paths.length ? elementFor(paths[paths.length - 1]) : null;
          if (last) return { el: last, side: "right" };
        }
        var sel = api.selected && api.selected();
        if (sel) {
          var el = elementFor(sel);
          if (el) return { el: el, side: "right" };
        }
        var g = strokes.length ? guess(strokes) : null;
        if (g && g.node) {
          var q = elementFor(g.node.path);
          if (q) return { el: q, side: "right" };
        }
        return null;
      }
      /** What the room is opened by: the selection, the range, the caret. */
      var roomKey = null;
      function targetKey() {
        var r = api.range && api.range(), c = api.caret && api.caret(), sel = api.selected && api.selected();
        var at = function (el) { return el && el.getAttribute ? el.getAttribute("data-path") || "?" : ""; };
        var j = editor && editor.junction;
        return JSON.stringify([sel || null, r ? [r.parent, r.anchor, r.focus] : null,
                               j ? [j.path, j.left, j.right] : null,
                               c ? [at(c.leftEl), at(c.rightEl), c.path || null, c.index == null ? null : c.index] : null]);
      }
      function openRoom() {
        if (room || !view) return;
        roomKey = targetKey();
        var a = anchor();
        if (!a) return;
        room = { el: a.el, side: a.side, px: 0 };
        sizeRoom();
      }
      // Where the space begins, in the formula's own pixels, asked of the
      // piece itself every time: it moves with the formula as it is scrolled,
      // and grows with it as it is zoomed.  (A margin on the right does not
      // move the piece; one on the left moves it by its own width.)
      function roomEdge() {
        if (!room || !room.el) return 0;
        var c = canvas.getBoundingClientRect(), o = offset(), q = room.el.getBoundingClientRect();
        if (!q.width && !q.height) return room.edge || 0;         // rendered again: the old value
        var edge = (room.side === "right" ? q.right : q.left - room.px) - c.left + o.x;
        room.edge = edge;
        return edge;
      }
      // As wide as what is written needs, never less than a few letters' worth.
      function sizeRoom() {
        if (!room) return;
        var least = 3.5 * em(), want = least, edge = roomEdge();
        var all = current ? strokes.concat([current]) : strokes;
        if (all.length) {
          var b = boxOf(all);
          want = Math.max(least, b.maxX - edge + 0.6 * em());
        }
        want += room.lead || 0;             // space ahead, opened when the ink reached the screen's edge
        want = Math.round(Math.max(0, want));
        if (want === room.px) return;
        room.px = want;
        room.el.style[room.side === "right" ? "marginRight" : "marginLeft"] = want + "px";
        layout();
      }
      /* ---- what the writing replaces is off the screen ---- */
      // A selection written over is what the ink replaces: while the pen is
      // on - or ink, or readings of it, are still waiting - it is hidden, its
      // place kept (visibility, so the formula does not move under the pen),
      // and the editor's outline of it with it.  It comes back only when the
      // writing is discarded: the ink cleared with the pen down, or the pen
      // put down with nothing written.  Once a reading is applied it is gone
      // for good, replaced.
      var covered = [];
      function childPath(parent, i) { return (parent === "/" ? "" : parent) + "/" + i; }
      /** The operator glyph written over, when that is the target. */
      function coverOperator() {
        if (applied || mine) return null;
        if (!(pen || strokes.length || current || held || readings.length)) return null;
        if (aim && aim.kind === "operator") return aim.el && aim.el.isConnected ? aim.el : null;
        if (aim && aim.kind) return null;
        var j = editor && editor.junction;
        return j && j.el && j.el.isConnected ? j.el : null;
      }
      function coverPaths() {
        // applied, or going in (mine: the formula is being redrawn with the
        // reading, and the piece now at that place is the new one)
        if (applied || mine) return [];
        if (!(pen || strokes.length || current || held || readings.length)) return [];
        var a = aim && (aim.kind === "selection" || aim.kind === "range") ? aim : null;
        // Until a reading names where it goes (aim), what is selected now: the
        // pause between a stroke and its reading used to show it again.
        if (!a && !(aim && aim.kind)) {
          var r = api.range && api.range(), sel = api.selected && api.selected();
          if (r) a = { kind: "range", path: r.parent, children: editor._rangeIndices ? editor._rangeIndices() : [] };
          else if (sel) a = { kind: "selection", path: sel };
        }
        if (!a) return [];
        if (a.kind === "selection") return [a.path];
        return (a.children || []).map(function (i) { return childPath(a.path, i); });
      }
      function syncCover() {
        var want = coverPaths().map(elementFor).filter(function (el) { return !!el; });
        var op = coverOperator();
        if (op) want = [op];
        var same = want.length === covered.length && want.every(function (el, i) { return covered[i] === el; });
        if (same) return;
        covered.forEach(function (el) { el.classList.remove("hw-covered"); });
        want.forEach(function (el) { el.classList.add("hw-covered"); });
        covered = want;
        if (editor && editor.root) editor.root.classList.toggle("hw-covering", want.length > 0);
      }

      /** After a stroke that ends near an edge of what is on the screen: open
       *  space ahead of it (the room grows on that side) and scroll so the
       *  room - the blue box to write in - is in sight again, with room to
       *  go on.  Sideways the formula's view scrolls; up and down the view
       *  when it scrolls itself (full screen), the page otherwise.  The ink
       *  keeps the formula's own pixels, so it moves with the scroll. */
      /** The part of the formula's view that is on the screen, in client
       *  pixels (the page may have scrolled part of it away, a keyboard may
       *  cover the bottom). */
      function visibleBox() {
        var v = view.getBoundingClientRect(), vv = window.visualViewport;
        var box = { left: Math.max(v.left, 0), right: Math.min(v.right, window.innerWidth),
                    top: Math.max(v.top, vv ? vv.offsetTop : 0),
                    bottom: Math.min(v.bottom, vv ? vv.offsetTop + vv.height : window.innerHeight) };
        return box.right > box.left && box.bottom > box.top ? box : null;
      }
      function scrollHow() {
        return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
      }
      function scrollByPx(dx, dy) {
        if (dx > 0.5 || dx < -0.5) view.scrollBy({ left: dx, behavior: scrollHow() });
        if (dy > 0.5 || dy < -0.5) {
          if (view.scrollHeight > view.clientHeight + 1) view.scrollBy({ top: dy, behavior: scrollHow() });
          else window.scrollBy({ top: dy, behavior: scrollHow() });
        }
      }
      /** The room just opened - by the selection, at the cursor - may lie past
       *  the edge of what is on the screen: then it is brought towards the
       *  middle, so that the blue box to write in is there to write in.  A
       *  room already in sight stays where it is. */
      function centerRoom() {
        if (!room || !view) return;
        var r = roomRect(), box = visibleBox();
        if (!r || !box) return;
        var c = canvas.getBoundingClientRect(), o = offset();
        var x0 = r.x - o.x + c.left, x1 = x0 + r.w, y0 = r.y - o.y + c.top, y1 = y0 + r.h;
        var pad = 8, dx = 0, dy = 0;
        if (x1 > box.right - pad && room.side === "right" && !room.lead) {
          // Past the right edge - at the end of the formula there is nothing
          // after it to scroll to: the room opens wider, space to write in,
          // and can then be brought to the middle.
          room.lead = Math.round(0.35 * (box.right - box.left));
          sizeRoom();
          r = roomRect();
          x0 = r.x - o.x + c.left; x1 = x0 + r.w;
        }
        if (x0 < box.left + pad || x1 > box.right - pad) dx = (x0 + x1) / 2 - (box.left + box.right) / 2;
        if (y0 < box.top + pad || y1 > box.bottom - pad) dy = (y0 + y1) / 2 - (box.top + box.bottom) / 2;
        scrollByPx(dx, dy);
      }
      function revealRoom(stroke) {
        if (!stroke || !stroke.length || !view) return;
        var c = canvas.getBoundingClientRect(), o = offset(), b = boxOf([stroke]);
        var x0 = b.minX - o.x + c.left, x1 = b.maxX - o.x + c.left;
        var y0 = b.minY - o.y + c.top, y1 = b.maxY - o.y + c.top;
        var box = visibleBox();
        if (!box) return;
        var left = box.left, right = box.right, top = box.top, bottom = box.bottom;
        var mx = Math.max(32, 0.12 * (right - left)), my = Math.max(32, 0.12 * (bottom - top));
        var dx = 0, dy = 0;
        if (x1 > right - mx) {
          if (room && room.side === "right") {               // space to go on writing in
            room.lead = Math.round(0.35 * (right - left));
            sizeRoom();
          }
          var r = roomRect(), far = r ? r.x + r.w - o.x + c.left : x1 + mx;
          dx = far + 8 - right;
          dx = Math.min(dx, x0 - left - 8);                   // never past the stroke just written
        } else if (x0 < left + mx) {
          dx = Math.max(x0 - mx - left, -view.scrollLeft);
        }
        var rr = roomRect();
        var rTop = rr ? rr.y - o.y + c.top : y0, rBottom = rr ? rr.y + rr.h - o.y + c.top : y1;
        if (y1 > bottom - my) dy = Math.min(Math.max(rBottom, y1) + my / 2 - bottom, y0 - top - 8);
        else if (y0 < top + my) dy = Math.min(rTop, y0) - my / 2 - top;
        scrollByPx(dx, dy);
      }

      function closeRoom() {
        if (!room) return;
        try { room.el.style[room.side === "right" ? "marginRight" : "marginLeft"] = ""; }
        catch (e) { /* the formula was rendered again: the style went with it */ }
        room = null;
        layout();
      }

      /* ---- where a reading goes ---- */
      function aimNow() {
        // A selected operator (the = of an equation): what is written takes
        // its place, read as an operator - no piece to read it with.
        var j = editor && editor.junction;
        if (j) return { kind: "operator", path: j.path, left: j.left, right: j.right, el: j.el };
        var r = api.range && api.range(), sel = api.selected && api.selected();
        if (r) return { kind: "range", path: r.parent, children: editor._rangeIndices() };
        if (sel) return { kind: "selection", path: sel };
        var caret = api.insertion && api.insertion();
        if (caret) return { kind: "caret", caret: caret };
        var g = held ? held.guess : null;
        if (picked === "none" || !g) return { kind: "end", free: true, options: g ? g.options : [] };
        var node = g.node;
        if (picked) {
          var kept = g.options.filter(function (q) { return q.path === picked; })[0];
          if (kept) node = kept; else picked = null;
        }
        return { kind: "nest", free: true, path: node.path, node: node, options: g.options, read: g.read };
      }
      function aimWords(a) {
        if (!a) return "";
        if (a.kind === "range") return "What is written takes the selected range's place:";
        if (a.kind === "selection") return "What is written takes the selection's place:";
        if (a.kind === "operator") return "What is written takes the operator's place:";
        if (a.kind === "caret") return "What is written goes in at the cursor:";
        if (a.kind === "nest") return "What is written is read together with the piece outlined:";
        return "What is written goes after the formula:";
      }

      /* ---- reading ---- */
      function clearReadings() {
        clearTimeout(timer);
        cands.textContent = "";
        withRow.textContent = "";
        withDivide.hidden = true;
        readings = [];
        chosen = -1;
        picks = { choices: {}, constants: {} };
        syncCover();
        readingOf.textContent = "";
        src.textContent = "";
        latexRow.hidden = true;
        editBtn.hidden = true;
        ambig.textContent = "";
        consts.textContent = "";
        parseBlock.hidden = true;
        actions.hidden = true;
        applyBtn.disabled = true;
        say(idle(), !canRead);
      }
      // With nothing read yet, the panel says how to write - it is all it holds.
      /** The chooser: the readers this page has, the chosen one showing. */
      function renderEngines() {
        var offered = usableEngines();
        engineMenu.hidden = offered.length < 2;
        engineMenu.textContent = "";
        offered.forEach(function (e) {
          var label = e.label + (e.where === "host" && host ? " \u2014 " + host.label : "");
          var opt = h("option", { value: e.name, title: e.note || "" }, [label]);
          if (e.name === engine) opt.selected = true;
          engineMenu.appendChild(opt);
        });
        canRead = engineState(engine).ok;
        updateTools();
      }

      /** Ask another reader from now on. */
      function pickEngine(name) {
        if (!engineNamed(name) || name === engine) return;
        engine = name;
        renderEngines();
        var e = engineNamed(engine), state = engineState(engine);
        say(state.ok ? "Read from now on by " + (e.where === "host" && host ? host.label : e.label) +
                       (e.note ? " \u2014 " + e.note : "")
                     : state.why, !state.ok);
        api.call("engine", { name: name }, { quiet: true }).then(function () {}, function () {});
      }

      function idle() {
        if (!canRead) return engineState(engine).why || status.reason;
        return pen ? "Write on the formula \u2014 a tap still selects a piece, or puts the cursor between two."
                   : "Press Write in the tools, and write on the formula.";
      }
      function say(text, bad) {
        note.textContent = text || "";
        note.className = "hw-note" + (bad ? " error" : "");
        showPanel();
      }
      // Nothing written and nothing read: the editor is the editor it was, with
      // nothing of this add-on under it.
      function showPanel() {
        element.hidden = !(pen || strokes.length || readings.length || applied);
      }
      function typeset(el, tex, fallback) {
        el.textContent = "";
        if (katex && tex) {
          try {
            el.innerHTML = katex.renderToString(tex, { throwOnError: false, displayMode: false, output: "html" });
            return;
          } catch (e) { /* the text, then */ }
        }
        el.textContent = fallback || tex || "";
      }
      function read() {
        clearTimeout(timer);
        if (strokes.length) { held = { strokes: strokes.slice(), guess: guess(strokes) }; aim = null; }
        if (!held || !canRead) return;
        var my = ++seq;
        // Where it goes is asked once of the editor, and again only of a piece
        // picked by hand: what went into the formula must not move the answer.
        if (!aim || aim.free) aim = aimNow();
        element.setAttribute("data-aim", aim.kind);
        redraw();
        var chosen = engineNamed(engine), byHost = chosen && chosen.where === "host";
        var ink = held.strokes, box = null, sibs = null;
        // The piece is for a reader that reads mathematics: a box means nothing
        // at all to a reader of text, so a host reading is of the ink alone.
        if (!byHost && aim.kind === "nest" && aim.node) {
          ink = aim.read || held.strokes;
          box = contextBox(aim.node.rect);
          sibs = siblingsOf(aim.node);
        }
        var nest = !byHost && aim.kind === "nest" ? aim.path : null;
        say("Reading…");
        element.classList.add("hw-busy");
        // quiet: the editor's overlay would cover the formula while one writes on
        var asked = byHost
          ? hostRead(ink).then(function (found) {
              return api.call("write", { candidates: found.candidates, ms: found.ms, engine: engine,
                                         operator: aim.kind === "operator" }, { quiet: true });
            })
          : api.call("write", { strokes: ink, context: box, nest: nest, siblings: sibs, engine: engine,
                                operator: aim.kind === "operator" }, { quiet: true });
        asked
          .then(function (res) {
            if (my !== seq) return;
            element.classList.remove("hw-busy");
            readings = res.candidates || [];
            if (!readings.length) { say("Nothing could be read of this"); return; }
            say("Read in " + res.ms + " ms — " +
                (readings.length > 1 ? "the best first; pick the one that is right, then Apply" : "Apply to put it in the formula"));
            renderWith();
            renderReadings();
            pick(0);
            askDown(300);          // read: what it says is worth going down to
          }, function (e) {
            if (my !== seq) return;
            element.classList.remove("hw-busy");
            say(String((e && e.message) || e), true);
          });
      }
      /** The host's own reading of the ink, timed here: it is the page's work. */
      function hostRead(ink) {
        if (!host) return Promise.reject(new Error("This device offers no reader of its own"));
        var t0 = (window.performance && performance.now()) || Date.now();
        return host.read(ink).then(function (found) {
          var ms = Math.round(((window.performance && performance.now()) || Date.now()) - t0);
          var out = (found || []).filter(function (c) { return c && String(c.latex || "").trim(); })
            .map(function (c) { return { latex: String(c.latex).trim(), raw: String(c.raw || c.latex).trim() }; });
          return { candidates: out, ms: ms };
        });
      }

      function renderReadings() {
        cands.textContent = "";
        readings.forEach(function (c, i) {
          var b = h("button", { type: "button", class: "hw-cand" + (c.edited ? " hw-cand-edited" : ""),
                                role: "option", title: c.edited ? "Your own LaTeX: " + c.latex : c.latex });
          typeset(b, c.display || c.latex, c.latex);
          b.addEventListener("click", function () { pick(i, false); });
          cands.appendChild(b);
        });
      }
      function renderWith() {
        withRow.textContent = "";
        withDivide.hidden = true;
        if (!aim || !aim.options || !aim.options.length) return;
        withRow.appendChild(h("span", { class: "hw-with-label" }, ["What is written goes with:"]));
        // A few pieces, as they come (the one written by, then the others there,
        // then what holds them): more than that is a wall, not a choice.
        aim.options.slice(0, 4).forEach(function (q) {
          var node = api.node ? api.node(q.path) : null, text = (node && node.src) || q.path;
          text = text.replace(/\s+/g, " ").trim();
          var short = text.length > 16 ? text.slice(0, 15) + "\u2026" : text;
          var b = h("button", { type: "button", class: "hw-with-option", "data-path": q.path,
                                title: "Read the ink together with " + text }, [short]);
          var on = aim.kind === "nest" && q.path === aim.path;
          b.classList.toggle("hw-chosen", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
          b.addEventListener("click", function () { picked = q.path; read(); });
          withRow.appendChild(b);
        });
        var alone = h("button", { type: "button", class: "hw-with-option hw-alone",
                                  title: "Read the ink alone, after the formula" }, ["alone"]);
        alone.classList.toggle("hw-chosen", aim.kind === "end");
        alone.setAttribute("aria-pressed", aim.kind === "end" ? "true" : "false");
        alone.addEventListener("click", function () { picked = "none"; read(); });
        withRow.appendChild(alone);
        withDivide.hidden = false;
      }
      // A reading goes into the formula at once; picking another takes the one
      // before it back first, so that the two do not pile up.
      // Picking a reading says which one it is - nothing more: the formula is
      // the user's to change, with Apply.  Once one is in, though, picking
      // another changes it to that one (the one before is taken back), so the
      // formula never holds two of them.
      function pick(i) {
        var c = readings[i];
        if (!c) return;
        chosen = i;
        for (var k = 0; k < cands.children.length; k++) {
          cands.children[k].classList.toggle("hw-chosen", k === i);
          cands.children[k].setAttribute("aria-selected", k === i ? "true" : "false");
        }
        picks = { choices: {}, constants: {} };
        showReading(c);
        if (applied) putIn(c, false);
        else updateApply();
      }
      function updateApply() {
        var c = readings[chosen];
        applyBtn.disabled = !(c && c.reading && c.reading.ok) || !!applied;
        actions.hidden = !!applied || !readings.length;
      }
      applyBtn.addEventListener("click", function () {
        var c = readings[chosen];
        if (c) putIn(c, true);
      });
      // A reading of ink written by a piece takes that piece's place only when it
      // was read together with it (the stand-in is in it); a reading of the ink
      // alone goes after the formula instead, so that the piece is not lost.
      function nests(c) { return aim.kind === "nest" && c.nested !== false; }
      function payloadFor(c) {
        var p = { latex: c.latex, path: "/", choices: picks.choices, constants: picks.constants };
        // A nested reading holds a placeholder where the piece goes: Python
        // puts the piece itself there (by its path), not its LaTeX read back.
        if (nests(c) && c.nest != null) { p.nest = c.nest; p.display = c.display; }
        // read among its siblings: it replaces the run of them it names
        if (nests(c) && c.children) { p.path = c.nest; p.children = c.children; return p; }
        if (aim.kind === "range") { p.path = aim.path; p.children = aim.children; }
        else if (aim.kind === "selection" || nests(c)) p.path = aim.path;
        else if (aim.kind === "caret") p.caret = aim.caret;
        else p.end = true;
        return p;
      }
      function putIn(c, first) {
        if (!c.reading || !c.reading.ok) {
          say((c.reading && c.reading.error) || "This reading could not be read as SymPy", true);
          return;
        }
        var was = applied ? applied.before : step(), my = ++puts;
        mine++;
        var back = applied ? api.send({ action: "undo" }) : Promise.resolve();
        back.then(function () {
          if (aim.kind === "operator") {          // the operator changed, as the palette changes it
            return api.send({ action: "operator", path: aim.path, left: aim.left, right: aim.right,
                              op: c.reading.operator }).then(function (snap) {
              if (snap && snap.error) throw new Error(snap.error);
            });
          }
          return api.call("insert", payloadFor(c));
        }).then(function () {
          mine = Math.max(0, mine - 1);
          if (my !== puts) return;            // answered for since: Keep, Undo, or fresh ink
          applied = { before: was, latex: c.latex };
          showApplied(was, step());
          // What was written is in: the piece it replaced is not the
          // selection any more, so nothing is left to write over.
          if (aim && (aim.kind === "selection" || aim.kind === "range" || aim.kind === "operator")) {
            closeRoom();
            if (api.select) api.select(null);
          }
          strokes = [];
          taken = [];
          current = null;
          closeRoom();
          updateTools();
          redraw();
          updateApply();
          say(first ? "In the formula." : "Changed.");
        }, function (e) {
          mine = Math.max(0, mine - 1);
          if (my !== puts) return;
          say(String((e && e.message) || e), true);
        });
      }
      function showReading(c) {
        var reading = c && c.reading;
        readingOf.textContent = nests(c) || aim.kind !== "nest" ? aimWords(aim)
          : "What is written was read alone, and goes after the formula:";
        if (!reading) { src.textContent = ""; return; }
        src.textContent = reading.ok ? reading.src : (reading.error || "This could not be read");
        src.className = "hw-src" + (reading.ok ? "" : " error");
        editBtn.hidden = false;
        options(reading);
      }
      // The LaTeX parser's own options: a row of typeset readings per ambiguity, a switch per
      // constant name.  A pick reads the LaTeX again and puts that in instead.
      function options(reading) {
        ambig.textContent = "";
        consts.textContent = "";
        parseBlock.hidden = true;
        if (!reading || !reading.ok) return;
        picks.choices = Object.assign({}, reading.choices || {});
        (reading.ambiguities || []).forEach(function (a) {
          ambig.appendChild(h("div", { class: "hw-point" },
            [h("code", { class: "hw-fragment" }, [a.fragment]), " \u2192 ", choiceRow(a)]));
        });
        (reading.constants || []).forEach(function (c) {
          var box = h("input", { type: "checkbox" });
          box.checked = !!c.on;
          box.addEventListener("change", function () { picks.constants[c.name] = box.checked; again(); });
          consts.appendChild(h("label", { class: "hw-const", title: c.label },
            [box, " ", h("code", {}, [c.name]), " is " + c.value + " (" + c.label + ")"]));
        });
        parseBlock.hidden = !ambig.children.length && !consts.children.length;
      }
      /** The readings of one ambiguous part, as buttons showing each whole
       *  expression typeset; a pick reads the LaTeX again with it. */
      function choiceRow(a) {
        var row = h("div", { class: "hw-choice", role: "radiogroup", "aria-label": "How to read " + a.fragment });
        a.options.forEach(function (o, i) {
          var b = h("button", { type: "button", class: "hw-option", role: "radio", "data-index": String(i),
                                title: o.invalid ? "Not a reading" : o.src });
          if (o.invalid) { b.disabled = true; b.textContent = "(not a reading)"; }
          else typeset(b, o.latex, o.src);
          b.addEventListener("click", function () {
            if (picks.choices[a.key] === i) return;
            picks.choices[a.key] = i;
            mark();
            again();
          });
          row.appendChild(b);
        });
        function mark() {
          var at = a.key in picks.choices ? picks.choices[a.key] : a.choice;
          for (var k = 0; k < row.children.length; k++) {
            row.children[k].classList.toggle("hw-chosen", k === at);
            row.children[k].setAttribute("aria-checked", k === at ? "true" : "false");
          }
        }
        mark();
        return row;
      }
      function again() {          // the same reading, with the options picked
        var c = readings[chosen];
        if (!c) return;
        api.call("read", { latex: c.latex, choices: picks.choices, constants: picks.constants, nest: c.nest,
                           children: c.children }, { quiet: true })
          .then(function (res) {
            var reading = res.reading;
            src.textContent = reading && reading.ok ? reading.src : ((reading && reading.error) || "");
            src.className = "hw-src" + (reading && reading.ok ? "" : " error");
            readings[chosen] = { latex: c.latex, display: c.display, nested: c.nested, nest: c.nest,
                                 children: c.children, reading: reading, edited: c.edited };
            if (applied) putIn(readings[chosen], false);
            else updateApply();
          }, function (e) { say(String((e && e.message) || e), true); });
      }

      /* ---- what the reading did: the formula before and after ---- */
      function step() {
        var st = api.state && api.state();
        return { latex: st && st.latex, plain: (st && st.latex_plain) || "", nodes: st && st.nodes };
      }
      function showApplied(was, now) {
        var diff = null;
        try {
          diff = editor && editor._diffNodes && was.nodes && now.nodes ? editor._diffNodes(was.nodes, now.nodes) : null;
        } catch (e) { diff = null; }
        var marked = false;
        if (editor && editor._renderMarked && was.latex && now.latex) {
          try {
            wasFormula.innerHTML = editor._renderMarked(was.latex, diff && diff.oldKept, "rep-removed");
            nowFormula.innerHTML = editor._renderMarked(now.latex, diff && diff.newKept, "rep-added");
            marked = true;
          } catch (e) { marked = false; }
        }
        if (!marked) {
          typeset(wasFormula, was.plain, was.plain);
          typeset(nowFormula, now.plain, now.plain);
        }
        wasFormula.setAttribute("data-latex", was.plain);
        nowFormula.setAttribute("data-latex", now.plain);
        appliedRow.hidden = false;
        showPanel();
        reveal(element);
      }
      function openEdit() {
        var c = readings[chosen];
        if (!c) return;
        latexRow.hidden = false;
        latexField.value = c.latex;
        latexField.focus();
        latexField.select();
      }
      // The LaTeX as it was corrected: a reading of its own, read and put in the
      // formula like any other - and it stays among them, to pick again.
      function readEdited() {
        var tex = latexField.value.trim(), c = readings[chosen];
        if (!tex || !aim) return;
        var nested = c ? c.nested !== false : true, nest = c && nested ? c.nest : undefined;
        var children = c && nested ? c.children : undefined;
        say("Reading\u2026");
        api.call("read", { latex: tex, nest: nest, children: children }, { quiet: true }).then(function (res) {
          var edited = { latex: tex, display: tex, nested: nested, nest: nest, children: children,
                         reading: res.reading, edited: true };
          var at = readings.length;
          for (var i = 0; i < readings.length; i++) if (readings[i].edited) { at = i; break; }
          readings[at] = edited;
          renderReadings();
          pick(at);
        }, function (e) { say(String((e && e.message) || e), true); });
      }
      editBtn.addEventListener("click", function () { latexRow.hidden ? openEdit() : (latexRow.hidden = true); });
      latexBtn.addEventListener("click", readEdited);
      latexField.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); readEdited(); }
        if (ev.key === "Escape") { latexRow.hidden = true; }
      });

      function hideApplied() { appliedRow.hidden = true; applied = null; puts++; updateApply(); showPanel(); }
      /** Back to the formula: after Keep there is nothing more to read down
       *  here, and the formula is what one works on next. */
      function backToFormula() {
        var target = (editor && (editor.stage || editor.view)) || null;
        if (!target || !target.scrollIntoView) return;
        var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        try { target.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" }); }
        catch (e) { target.scrollIntoView(true); }
      }
      keepBtn.addEventListener("click", function () { hideApplied(); forget(); clearReadings(); backToFormula(); });
      backBtn.addEventListener("click", function () {
        mine++;
        var done = function () { mine = Math.max(0, mine - 1); };
        api.send({ action: "undo" }).then(done, done);
        hideApplied();
        forget();
        clearReadings();
      });

      /* ---- the tools, in the editor's own strip ---- */
      // Undo and Redo of the ink itself: the editor's own Undo is for the
      // formula, and takes back what a reading did (which is an edit of it).
      function undoStroke() {
        if (!strokes.length) return;
        taken.push(strokes.pop());
        afterInk();
      }
      function redoStroke() {
        if (!taken.length) return;
        strokes.push(taken.pop());
        afterInk();
      }
      function afterInk() {
        clearTimeout(timer);
        sizeRoom();
        updateTools();
        redraw();
        if (strokes.length) timer = setTimeout(read, 400);
        else { forget(); clearReadings(); }
      }
      function toolButton(cmd) {
        var root = editor && editor.root;
        return root ? root.querySelector('[data-cmd="addon:handwriting:' + cmd + '"]') : null;
      }
      function dressTools() {
        [["pen", "Write by hand"], ["erase", "Erase"], ["undo", "Undo stroke"],
         ["redo", "Redo stroke"], ["clear", "Clear ink"]].forEach(function (t) {
          var b = toolButton(t[0]);
          if (!b || b.getAttribute("data-hw") === "1") return;
          b.setAttribute("data-hw", "1");
          b.setAttribute("aria-label", t[1]);
          b.innerHTML = toolIcon(t[0]);
          b.classList.add("hw-tool");
        });
        updateTools();
      }
      /** A tool off, and kept off: the editor's toolbar sets every add-on's
       *  buttons as it updates itself (a tap that changes the selection does),
       *  and leaves alone the ones marked this way. */
      function toolOff(button, off) {
        if (!button) return;
        if (off) button.setAttribute("data-addon-off", "1");
        else button.removeAttribute("data-addon-off");
        button.disabled = !!off;
      }

      function updateTools() {
        var p = toolButton("pen"), e = toolButton("erase"), c = toolButton("clear");
        var u = toolButton("undo"), r = toolButton("redo");
        // Everything but the Pen is for writing: with the Pen off there is
        // nothing for them to do, whatever ink is still on the formula.
        toolOff(u, !pen || !strokes.length);
        toolOff(r, !pen || !taken.length);
        toolOff(c, !pen || !strokes.length);
        toolOff(e, !pen);
        toolOff(p, !canRead);
        if (p) {
          p.setAttribute("aria-pressed", pen ? "true" : "false");
          p.classList.toggle("hw-on", pen);
          p.classList.toggle("hw-pen-on", pen);        // the pulse: writing mode
        }
        if (e) {
          e.setAttribute("aria-pressed", erasing ? "true" : "false");
          e.classList.toggle("hw-on", erasing);
        }
        element.setAttribute("data-pen", pen ? "on" : "off");
        element.setAttribute("data-strokes", String(strokes.length));
      }
      function setPen(on) {
        pen = !!on && canRead;
        if (!pen) setErasing(false);
        if (editor && editor.root) editor.root.classList.toggle("se-inking", pen);
        canvas.style.pointerEvents = pen ? "auto" : "none";
        if (pen) { openRoom(); centerRoom(); } else closeRoom();
        if (!pen) hideDown();
        syncCover();
        if (!strokes.length) say(idle(), !canRead);
        showPanel();
        updateTools();
        setTimeout(layout, 0);
      }
      function setErasing(on) {
        erasing = !!on && pen;
        if (editor && editor.root) editor.root.classList.toggle("se-erasing", erasing);
        updateTools();
      }
      function clearInk() {
        if (!strokes.length && !current && !held) return;
        strokes = [];
        taken = [];
        current = null;
        closeRoom();
        if (pen) openRoom();
        clearTimeout(timer);
        hideDown();
        updateTools();
        forget();
        clearReadings();
        redraw();
      }
      function forget() { held = null; aim = null; picked = null; puts++; }

      /* ---- writing on the formula ---- */
      function eraseAt(p) {
        var r = 14, gone = false;
        for (var i = strokes.length - 1; i >= 0; i--) {
          var s = strokes[i];
          for (var k = 0; k < s.length; k++) {
            if (Math.abs(s[k][0] - p[0]) <= r && Math.abs(s[k][1] - p[1]) <= r) { strokes.splice(i, 1); gone = true; break; }
          }
        }
        if (gone) { updateTools(); redraw(); }
      }
      canvas.addEventListener("pointerdown", function (ev) {
        if (!pen || !canRead) return;
        if (ev.pointerType === "touch") {
          touches[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
          if (Object.keys(touches).length >= 2) {   // a second finger: a pinch, not a stroke
            current = null;
            blocked = true;
            pinch = { dist: spread(), zoom: editor.zoom || 1, cx: centre("x"), cy: centre("y") };
            redraw();
            return;
          }
          if (blocked) return;
        }
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        ev.preventDefault();
        clearTimeout(timer);
        hideDown();
        try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
        currentId = ev.pointerId;
        if (erasing || (ev.pointerType === "pen" && (ev.buttons & 32))) { current = null; eraseAt(point(ev)); return; }
        down = { x: ev.clientX, y: ev.clientY, t: ev.timeStamp, type: ev.pointerType };
        if (!strokes.length) {
          t0 = ev.timeStamp;
          // Fresh ink: what the last one did stands in the formula, answered for.
          if (held || applied) { forget(); hideApplied(); clearReadings(); }
        }
        current = [point(ev)];
        redraw();
      });
      function spread() {
        var ids = Object.keys(touches);
        if (ids.length < 2) return 1;
        var a = touches[ids[0]], b = touches[ids[1]];
        return Math.max(1, Math.sqrt(Math.pow(b.x - a.x, 2) + Math.pow(b.y - a.y, 2)));
      }
      function centre(axis) {
        var ids = Object.keys(touches), sum = 0;
        if (!ids.length) return 0;
        ids.forEach(function (id) { sum += touches[id][axis]; });
        return sum / ids.length;
      }
      canvas.addEventListener("pointermove", function (ev) {
        if (touches[ev.pointerId]) touches[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
        if (pinch) {
          if (Object.keys(touches).length < 2) return;
          // The fingers' centre drags the formula along, their spread zooms it.
          var cx = centre("x"), cy = centre("y");
          if (view) { view.scrollLeft -= cx - pinch.cx; view.scrollTop -= cy - pinch.cy; }
          pinch.cx = cx;
          pinch.cy = cy;
          editor.setZoom(pinch.zoom * spread() / pinch.dist, cx);
          ev.preventDefault();
          return;
        }
        if (!pen || ev.pointerId !== currentId) return;
        if (erasing || (ev.pointerType === "pen" && (ev.buttons & 32))) { eraseAt(point(ev)); return; }
        if (!current) return;
        ev.preventDefault();
        var evs = (ev.getCoalescedEvents && ev.getCoalescedEvents()) || [];
        if (!evs.length) evs = [ev];
        for (var i = 0; i < evs.length; i++) current.push(point(evs[i]));
        sizeRoom();
        redraw();
      });
      function lift(ev) {
        if (touches[ev.pointerId]) {
          delete touches[ev.pointerId];
          if (Object.keys(touches).length < 2) pinch = null;
          if (!Object.keys(touches).length) blocked = false;
        }
        if (ev.pointerId !== currentId) return;
        currentId = null;
        // A tap on the formula, with nothing written yet, is the editor's own:
        // it selects a piece, or puts the cursor between two, and the room to
        // write in opens there.  Once there is ink, a tap is a dot.
        if (current && !strokes.length && down && tapped(ev)) {
          current = null;
          redraw();
          tapThrough(ev);
          return;
        }
        if (current) {
          var done = current;
          strokes.push(current);
          taken = [];              // written on: there is no stroke to put back any more
          current = null;
          updateTools();
          redraw();
          revealRoom(done);
        }
        if (!strokes.length) return;
        clearTimeout(timer);
        timer = setTimeout(read, 700);       // a pause: what is written may be finished
        askDown();
      }
      function tapped(ev) {
        var slop = ev.pointerType === "touch" ? 9 : 4;
        return ev.timeStamp - down.t < 400 &&
               Math.abs(ev.clientX - down.x) <= slop && Math.abs(ev.clientY - down.y) <= slop;
      }
      // The press the canvas took, given to the formula under it.
      function tapThrough(ev) {
        canvas.style.pointerEvents = "none";
        var el = document.elementFromPoint(ev.clientX, ev.clientY);
        canvas.style.pointerEvents = pen ? "auto" : "none";
        if (!el || !view || !view.contains(el)) return;
        var opts = { bubbles: true, cancelable: true, composed: true, clientX: ev.clientX, clientY: ev.clientY,
                     button: 0, buttons: 1, pointerId: 1, pointerType: ev.pointerType || "mouse", isPrimary: true };
        try {
          el.dispatchEvent(new PointerEvent("pointerdown", opts));
          el.dispatchEvent(new PointerEvent("pointerup", Object.assign({}, opts, { buttons: 0 })));
        } catch (e) { /* an old browser: the click alone, then */ }
        el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, composed: true,
                                                   clientX: ev.clientX, clientY: ev.clientY, button: 0 }));
      }
      canvas.addEventListener("pointerup", lift);
      canvas.addEventListener("pointercancel", lift);
      if (view) view.addEventListener("scroll", redraw);
      var resizer = window.ResizeObserver ? new ResizeObserver(function () { layout(); }) : null;
      if (resizer && stage) resizer.observe(stage); else window.addEventListener("resize", layout);

      renderEngines();
      setPen(false);
      setTimeout(dressTools, 0);
      setTimeout(layout, 0);
      clearReadings();

      guide = HELP(status);
      return {
        writing: function () { return pen; },
        erasing: function () { return erasing; },
        setPen: setPen,
        setErasing: setErasing,
        clearInk: clearInk,
        undoStroke: undoStroke,
        redoStroke: redoStroke,
        help: guide,
        /** The system's Back (Android): the pen goes down - the ink stays,
         *  as when the Pen tool is pressed again. */
        onBack: function () {
          if (!pen) return false;
          setPen(false);
          return true;
        },
        onSelect: function () {         // the room follows what is selected
          // Only when that changed: the editor says so after every scroll
          // too, and closing and opening the room at each of them pulled the
          // view back (its space went for a moment) - a loop of scrolls.
          var key = targetKey();
          if (pen && !strokes.length && key !== roomKey) { closeRoom(); openRoom(); centerRoom(); }
          if (strokes.length) redraw();
          syncCover();
        },
        onZoom: function () { layout(); },
        onState: function () {
          dressTools();
          room = null;                       // rendered again: the margin went with the old nodes
          covered = [];                      // and what was hidden went with them
          if (editor && editor.root) editor.root.classList.remove("hw-covering");
          if (pen) openRoom();
          syncCover();
          if (!mine && applied) hideApplied();   // edited in the editor itself: what we did is answered for
          setTimeout(layout, 0);
        },
        destroy: function () {
          clearTimeout(timer);
          clearTimeout(downTimer);
          window.removeEventListener("scroll", onScroll);
          if (downBtn.parentNode) downBtn.parentNode.removeChild(downBtn);
          closeRoom();
          setPen(false);
          covered.forEach(function (el) { el.classList.remove("hw-covered"); });   // nothing stays hidden once the add-on goes
          covered = [];
          if (editor && editor.root) editor.root.classList.remove("hw-covering");
          if (resizer) resizer.disconnect(); else window.removeEventListener("resize", layout);
          if (view) view.removeEventListener("scroll", redraw);
          if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
          if (element.parentNode) element.parentNode.removeChild(element);
        }
      };
    }
  };
})());
