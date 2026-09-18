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
 * the strokes as a stand-in (a triangle, read as \Delta) so that the model
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
    clear: "M2.9 4.5h10.2M6.2 4.5V3.1h3.6v1.4M4.3 4.5l.7 8.7a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.7-8.7M6.9 6.9v4.7M9.1 6.9v4.7"
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
      + "<li>" + toolIcon("pen", 16) + " <b>Write</b>, among the editor's tools, takes the pointer and gives the formula room to write in; with it off the editor is the editor it was - the formula is tapped, selected and edited in the usual way.</li>"
      + "<li>A moment after the pen lifts what is written is read, and the best reading goes into the formula at once. The others are offered under it: picking one puts that in instead (the one before it is taken back first, so they never pile up).</li>"
      + "<li>Where it goes is what the editor says: over the selected sub-expression (or the selected range), at the cursor, and - with neither - against the piece of the formula it is written by. That piece is outlined, and it is read <i>together with</i> the ink: a bar under it with ink under the bar is a fraction over it, a small letter at its top-right corner its exponent, a letter beside it a product. <b>Read with</b> offers the other pieces it might be, and <i>alone</i>: the reading by itself, after the formula.</li>"
      + "<li>" + toolIcon("erase", 16) + " <b>Erase</b> takes away the strokes the pointer passes over (a pen turned round erases too), and " + toolIcon("clear", 16) + " <b>Clear ink</b> takes all of it.</li>"
      + "<li>What the reading did is shown under the editor - the formula as it was and as it now is, what went marked red and what came marked green - to <b>Keep</b> or to <b>Undo the change</b>; the editor's own Undo takes it back too.</li>"
      + "<li>Under the readings: what SymPy gets of the one in the formula, with a menu for each part of the LaTeX that can be read more than one way and a switch for each constant name.</li>"
      + "<li>The reading is done by math-ocr's stroke model. It reads one formula at a time, and mixes up look-alike glyphs most (<code>1</code> and <code>|</code>, <code>V</code> and <code>v</code>).</li>"
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
      { cmd: "clear", label: "Clear ink", title: "Take all the ink away",
        run: function () { this.clearInk(); } }
    ],

    mount: function (api) {
      var h = api.h;
      var status = (api.options && api.options.status) ||
        { available: false, reason: "The add-on's Python said nothing about its model" };
      var canRead = !!status.available;

      var guide;                     // the add-on's own page of the help overlay, below

      /* ---- the strip under the editor: the readings and what came of them ---- */
      var note = h("div", { class: "hw-note", "aria-live": "polite" });
      var withRow = h("div", { class: "hw-with", role: "group", "aria-label": "What the ink is read together with" });
      var withDivide = h("hr", { class: "hw-divide", hidden: "" });
      var cands = h("div", { class: "hw-cands", role: "listbox", "aria-label": "Readings, the best first" });
      var readingOf = h("div", { class: "hw-reading-of" });
      var src = h("code", { class: "hw-src", title: "What SymPy gets of the reading" });
      var ambig = h("div", { class: "hw-ambig" });
      var consts = h("div", { class: "hw-consts" });
      var parseBlock = h("div", { class: "hw-parse", hidden: "" }, [
        h("div", { class: "hw-parse-label" }, ["Where this LaTeX can be read more than one way:"]), ambig, consts]);
      var wasFormula = h("span", { class: "hw-formula hw-was" });
      var nowFormula = h("span", { class: "hw-formula hw-now" });
      var keepBtn = h("button", { type: "button", class: "hw-keep", title: "Leave the formula as it now is" }, ["Keep"]);
      var backBtn = h("button", { type: "button", class: "hw-back", title: "The formula as it was" }, ["Undo the change"]);
      var appliedRow = h("div", { class: "hw-applied", hidden: "" }, [
        h("div", { class: "hw-applied-row" }, [h("span", { class: "hw-applied-label" }, ["from"]), wasFormula]),
        h("div", { class: "hw-applied-row" }, [h("span", { class: "hw-applied-label" }, ["to"]), nowFormula]),
        h("div", { class: "hw-applied-ask" }, [keepBtn, backBtn])]);
      var helpBtn = h("button", { type: "button", class: "hw-help", title: "How writing by hand works" }, ["?"]);
      var element = h("div", { class: "hw-panel", "data-strokes": "0", "data-pen": "off", "data-aim": "", hidden: "" },
        [h("div", { class: "hw-head" }, [note, helpBtn]), cands, withDivide, withRow, readingOf, src, parseBlock, appliedRow]);
      helpBtn.addEventListener("click", function () { api.showHelp(guide, "Handwriting"); });
      // The keys of a menu or a button here are the panel's own, not the formula's.
      element.addEventListener("keydown", function (ev) { ev.stopPropagation(); });

      /* ---- the ink, on the editor's own formula ---- */
      var editor = api.editor, stage = editor && editor.stage, view = editor && editor.view;
      if (editor && editor.addonHost && editor.addonHost.parentNode) {
        editor.addonHost.parentNode.insertBefore(element, editor.addonHost);
      }
      var canvas = h("canvas", { class: "hw-ink", "aria-hidden": "true" });
      if (stage) stage.appendChild(canvas);
      var ctx = canvas.getContext("2d");

      var pen = false, erasing = false;
      var strokes = [], current = null, currentId = null, t0 = 0, dpr = 1;
      var timer = null, seq = 0, katex = null;
      var touches = {}, blocked = false;
      var zoomWas = (editor && editor.zoom) || 1;
      var readings = [], chosen = -1, picks = { choices: {}, constants: {} };
      var aim = null;         // where a reading goes: {kind, path, children, caret, node, options}
      var held = null;        // the ink whose reading is in the formula: {strokes, guess} - the
                              // strokes go off the formula once read, but another piece to read
                              // them with, or another reading, is still asked of them
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
          zoomWas = editor.zoom;
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
        var o = offset();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(dpr, 0, 0, dpr, -o.x * dpr, -o.y * dpr);
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
      // The piece drawn into the ink: a triangle, no wider than tall, in its
      // place and before the ink - the model reads it as \Delta, and Python
      // puts the piece's own LaTeX there.
      function standIn(r, list) {
        var ht = r.h * 0.8, w = Math.min(r.w, ht * 1.2);
        var x0 = r.x + (r.w - w) / 2, y0 = r.y + (r.h - ht) / 2, pts = [], t = 0;
        var line = function (ax, ay, bx, by) {
          var n = Math.max(2, Math.round(Math.sqrt(Math.pow(bx - ax, 2) + Math.pow(by - ay, 2)) / 2));
          for (var i = pts.length ? 1 : 0; i <= n; i++) {
            pts.push([ax + (bx - ax) * i / n, ay + (by - ay) * i / n, t]);
            t += 8;
          }
        };
        line(x0, y0 + ht, x0 + w / 2, y0);
        line(x0 + w / 2, y0, x0 + w, y0 + ht);
        line(x0 + w, y0 + ht, x0, y0 + ht);
        var shift = t + 250;
        return [pts].concat(list.map(function (s) {
          return s.map(function (p) { return [p[0], p[1], p[2] + shift]; });
        }));
      }

      /* ---- where a reading goes ---- */
      function aimNow() {
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
        readingOf.textContent = "";
        src.textContent = "";
        ambig.textContent = "";
        consts.textContent = "";
        parseBlock.hidden = true;
        say(idle(), !canRead);
      }
      // With nothing read yet, the panel says how to write - it is all it holds.
      function idle() {
        if (!canRead) return status.reason;
        return pen ? "Write on the formula." : "Press Write in the tools, and write on the formula.";
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
        var ink = held.strokes;
        if (aim.kind === "nest" && aim.node) ink = standIn(aim.node.rect, aim.read || held.strokes);
        say("Reading…");
        element.classList.add("hw-busy");
        // quiet: the editor's overlay would cover the formula while one writes on
        api.call("write", { strokes: ink, nest: aim.kind === "nest" ? aim.path : null }, { quiet: true })
          .then(function (res) {
            if (my !== seq) return;
            element.classList.remove("hw-busy");
            readings = res.candidates || [];
            if (!readings.length) { say("Nothing could be read of this"); return; }
            say("Read in " + res.ms + " ms" +
                (readings.length > 1 ? " — the best is in the formula; pick another if it is the one" : ""));
            renderWith();
            renderReadings();
            pick(0, true);
          }, function (e) {
            if (my !== seq) return;
            element.classList.remove("hw-busy");
            say(String((e && e.message) || e), true);
          });
      }
      function renderReadings() {
        cands.textContent = "";
        readings.forEach(function (c, i) {
          var b = h("button", { type: "button", class: "hw-cand", role: "option", title: c.latex });
          typeset(b, c.display || c.latex, c.latex);
          b.addEventListener("click", function () { pick(i, false); });
          cands.appendChild(b);
        });
      }
      function renderWith() {
        withRow.textContent = "";
        withDivide.hidden = true;
        if (!aim || !aim.options || !aim.options.length) return;
        withRow.appendChild(h("span", { class: "hw-with-label" }, ["Read with:"]));
        aim.options.forEach(function (q) {
          var node = api.node ? api.node(q.path) : null, text = (node && node.src) || q.path;
          var b = h("button", { type: "button", class: "hw-with-option", "data-path": q.path,
                                title: "Read the ink together with " + text }, [text]);
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
      function pick(i, first) {
        var c = readings[i];
        if (!c) return;
        chosen = i;
        for (var k = 0; k < cands.children.length; k++) {
          cands.children[k].classList.toggle("hw-chosen", k === i);
          cands.children[k].setAttribute("aria-selected", k === i ? "true" : "false");
        }
        picks = { choices: {}, constants: {} };
        showReading(c);
        putIn(c, first);
      }
      // A reading of ink written by a piece takes that piece's place only when it
      // was read together with it (the stand-in is in it); a reading of the ink
      // alone goes after the formula instead, so that the piece is not lost.
      function nests(c) { return aim.kind === "nest" && c.nested !== false; }
      function payloadFor(c) {
        var p = { latex: c.latex, path: "/", choices: picks.choices, constants: picks.constants };
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
        back.then(function () { return api.call("insert", payloadFor(c)); }).then(function () {
          mine = Math.max(0, mine - 1);
          if (my !== puts) return;            // answered for since: Keep, Undo, or fresh ink
          applied = { before: was, latex: c.latex };
          showApplied(was, step());
          strokes = [];
          current = null;
          updateTools();
          redraw();
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
        options(reading);
      }
      // The LaTeX parser's own options: a menu per ambiguity, a switch per
      // constant name.  A pick reads the LaTeX again and puts that in instead.
      function options(reading) {
        ambig.textContent = "";
        consts.textContent = "";
        parseBlock.hidden = true;
        if (!reading || !reading.ok) return;
        picks.choices = Object.assign({}, reading.choices || {});
        (reading.ambiguities || []).forEach(function (a) {
          var sel = h("select", { class: "hw-choice", title: "How to read " + a.fragment });
          a.options.forEach(function (o, i) {
            var opt = h("option", { value: String(i) }, [o.invalid ? "(not a reading)" : o.src]);
            if (o.invalid) opt.disabled = true;
            if (i === a.choice) opt.selected = true;
            sel.appendChild(opt);
          });
          sel.addEventListener("change", function () { picks.choices[a.key] = parseInt(sel.value, 10); again(); });
          ambig.appendChild(h("label", { class: "hw-point" },
            [h("code", { class: "hw-fragment" }, [a.fragment]), " → ", sel]));
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
      function again() {          // the same reading, with the options picked
        var c = readings[chosen];
        if (!c) return;
        api.call("read", { latex: c.latex, choices: picks.choices, constants: picks.constants }, { quiet: true })
          .then(function (res) {
            var reading = res.reading;
            src.textContent = reading && reading.ok ? reading.src : ((reading && reading.error) || "");
            src.className = "hw-src" + (reading && reading.ok ? "" : " error");
            putIn({ latex: c.latex, reading: reading }, false);
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
      function hideApplied() { appliedRow.hidden = true; applied = null; puts++; showPanel(); }
      keepBtn.addEventListener("click", function () { hideApplied(); forget(); clearReadings(); });
      backBtn.addEventListener("click", function () {
        mine++;
        var done = function () { mine = Math.max(0, mine - 1); };
        api.send({ action: "undo" }).then(done, done);
        hideApplied();
        forget();
        clearReadings();
      });

      /* ---- the tools, in the editor's own strip ---- */
      function toolButton(cmd) {
        var root = editor && editor.root;
        return root ? root.querySelector('[data-cmd="addon:handwriting:' + cmd + '"]') : null;
      }
      function dressTools() {
        [["pen", "Write by hand"], ["erase", "Erase"], ["clear", "Clear ink"]].forEach(function (t) {
          var b = toolButton(t[0]);
          if (!b || b.getAttribute("data-hw") === "1") return;
          b.setAttribute("data-hw", "1");
          b.setAttribute("aria-label", t[1]);
          b.innerHTML = toolIcon(t[0]);
          b.classList.add("hw-tool");
        });
        updateTools();
      }
      function updateTools() {
        var p = toolButton("pen"), e = toolButton("erase"), c = toolButton("clear");
        if (p) {
          p.setAttribute("aria-pressed", pen ? "true" : "false");
          p.classList.toggle("hw-on", pen);
          p.disabled = !canRead;
        }
        if (e) {
          e.setAttribute("aria-pressed", erasing ? "true" : "false");
          e.classList.toggle("hw-on", erasing);
          e.disabled = !pen;
        }
        if (c) c.disabled = !strokes.length;
        element.setAttribute("data-pen", pen ? "on" : "off");
        element.setAttribute("data-strokes", String(strokes.length));
      }
      function setPen(on) {
        pen = !!on && canRead;
        if (!pen) setErasing(false);
        if (editor && editor.root) editor.root.classList.toggle("se-inking", pen);
        canvas.style.pointerEvents = pen ? "auto" : "none";
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
        current = null;
        clearTimeout(timer);
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
          touches[ev.pointerId] = true;
          if (Object.keys(touches).length >= 2) { current = null; blocked = true; redraw(); return; }
          if (blocked) return;
        }
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        ev.preventDefault();
        clearTimeout(timer);
        try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
        currentId = ev.pointerId;
        if (erasing || (ev.pointerType === "pen" && (ev.buttons & 32))) { current = null; eraseAt(point(ev)); return; }
        if (!strokes.length) {
          t0 = ev.timeStamp;
          // Fresh ink: what the last one did stands in the formula, answered for.
          if (held || applied) { forget(); hideApplied(); clearReadings(); }
        }
        current = [point(ev)];
        redraw();
      });
      canvas.addEventListener("pointermove", function (ev) {
        if (!pen || ev.pointerId !== currentId) return;
        if (erasing || (ev.pointerType === "pen" && (ev.buttons & 32))) { eraseAt(point(ev)); return; }
        if (!current) return;
        ev.preventDefault();
        var evs = (ev.getCoalescedEvents && ev.getCoalescedEvents()) || [];
        if (!evs.length) evs = [ev];
        for (var i = 0; i < evs.length; i++) current.push(point(evs[i]));
        redraw();
      });
      function lift(ev) {
        if (touches[ev.pointerId]) {
          delete touches[ev.pointerId];
          if (!Object.keys(touches).length) blocked = false;
        }
        if (ev.pointerId !== currentId) return;
        currentId = null;
        if (current) {
          strokes.push(current);
          current = null;
          updateTools();
          redraw();
        }
        if (!strokes.length) return;
        clearTimeout(timer);
        timer = setTimeout(read, 700);       // a pause: what is written may be finished
      }
      canvas.addEventListener("pointerup", lift);
      canvas.addEventListener("pointercancel", lift);
      if (view) view.addEventListener("scroll", redraw);
      var resizer = window.ResizeObserver ? new ResizeObserver(function () { layout(); }) : null;
      if (resizer && stage) resizer.observe(stage); else window.addEventListener("resize", layout);

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
        help: guide,
        onSelect: function () { if (strokes.length) redraw(); },
        onState: function () {
          dressTools();
          if (!mine && applied) hideApplied();   // edited in the editor itself: what we did is answered for
          setTimeout(layout, 0);
        },
        destroy: function () {
          clearTimeout(timer);
          setPen(false);
          if (resizer) resizer.disconnect(); else window.removeEventListener("resize", layout);
          if (view) view.removeEventListener("scroll", redraw);
          if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
        }
      };
    }
  };
})());
