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
 * switch - and go in over the selection or as the whole expression.  Strokes,
 * clearing and inserting can be undone and redone.  Two fingers never write:
 * they pinch the area to zoom it and drag it to scroll, and the strokes keep
 * the canvas's own coordinates whatever the zoom.  In full screen the panel
 * covers the page: the tools on top, the writing area, and the readings in a
 * sheet at the bottom that folds away.
 */
SympyEditor.registerAddon("handwriting", (function () {
  // After a reading goes in, the formula it went into is brought back into
  // sight: the panel sits below the editor, often scrolled past it.
  function showFormula(api) {
    var root = api.editor && api.editor.root;
    if (!root || !root.scrollIntoView) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    try { root.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" }); }
    catch (e) { root.scrollIntoView(true); }       // no options object
  }
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
    read: "M3 2.8h10a1.2 1.2 0 0 1 1.2 1.2v8a1.2 1.2 0 0 1-1.2 1.2H3A1.2 1.2 0 0 1 1.8 12V4A1.2 1.2 0 0 1 3 2.8ZM5.2 5.5h5.6M8 5.5v5.2"
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
      var pad = h("div", { class: "ink-pad" }, [canvas]);
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
      var undoBtn = toolButton(h, "undo", "Undo: take back the last stroke, erasing or clearing", { disabled: "" });
      var redoBtn = toolButton(h, "redo", "Redo: put back what Undo took", { disabled: "" });
      var eraseBtn = toolButton(h, "erase", "Erase: what the pen, the finger or the mouse passes over goes (press again to write)", { "aria-pressed": "false" });
      var clearBtn = toolButton(h, "clear", "Clear: take all the ink away (Undo brings it back)", { disabled: "" });
      var readBtn = toolButton(h, "read", "Read: read what is written now");
      var bar = h("div", { class: "ink-bar" }, [undoBtn, redoBtn, eraseBtn, clearBtn, readBtn]);

      var note = h("div", { class: "ink-note", "aria-live": "polite" });
      var cands = h("div", { class: "ink-cands", role: "listbox", "aria-label": "Readings, best first" });
      var field = h("input", { class: "ink-latex", type: "text", spellcheck: "false", autocomplete: "off", autocapitalize: "off",
        placeholder: "The reading as LaTeX - correct it here", "aria-label": "The reading as LaTeX" });
      var src = h("code", { class: "ink-src", title: "What SymPy gets" });
      var ambig = h("div", { class: "ink-ambig" });
      var consts = h("div", { class: "ink-consts" });
      var insertSel = h("button", { type: "button", class: "ink-insert", disabled: "" }, ["Replace the selection"]);
      var insertAll = h("button", { type: "button", class: "ink-insert-all", disabled: "" }, ["Replace the whole expression"]);
      var toLatex = h("button", { type: "button", class: "ink-to-latex", hidden: "",
        title: "Hand the reading to the LaTeX panel, which offers every way it can be read" }, ["Open in the LaTeX panel"]);
      var sheetChevron = h("span", { class: "ink-sheet-chevron", "aria-hidden": "true" });
      var sheetSummary = h("span", { class: "ink-sheet-summary" });
      var sheetHead = h("button", { type: "button", class: "ink-sheet-head", "aria-expanded": "true",
        title: "Fold the readings away, or bring them back" }, [sheetChevron, sheetSummary]);
      var actions = h("div", { class: "ink-actions" }, [insertSel, insertAll, toLatex]);
      var sheetBody = h("div", { class: "ink-sheet-body" }, [note, cands, field, src, ambig, consts, actions]);
      var sheet = h("div", { class: "ink-sheet" }, [sheetHead, sheetBody]);
      var element = h("div", { class: "ink-panel", "data-strokes": "0", "data-zoom": "1.00" }, [bar, stage, sheet]);

      // ---- state -------------------------------------------------------------------
      var strokes = [];                 // [[[x, y, t], ...], ...]
      var done = [], undone = [];       // {kind: "stroke", stroke} or {kind: "clear", strokes}
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
      var erasing = false;              // the Erase mode: pointers take strokes away instead of writing
      var erase = null;                 // an erasing drag: {id, at, removed: [{index, stroke}]}

      api.katex().then(function (k) { katex = k; }, function () {});

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
        redraw();
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
        done.push({ kind: "erase", removed: e.removed });
        undone = [];
        refit();
        changed(300);                     // what is left is read again
      }
      function setErasing(on) {
        erasing = !!on;
        eraseBtn.setAttribute("aria-pressed", erasing ? "true" : "false");
        eraseBtn.classList.toggle("ink-on", erasing);
        canvas.classList.toggle("ink-erasing", erasing);
      }
      eraseBtn.addEventListener("click", function () { setErasing(!erasing); });

      // ---- zooming and scrolling: two fingers, or a pinch on a trackpad ------------
      function zoomTo(z) {                // the new zoom, the canvas still covering the box
        z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
        if (Math.abs(z - zoom) < 1e-3) return false;
        zoom = z;
        width = Math.max(width, pad.clientWidth / zoom);
        height = Math.max(height, pad.clientHeight / zoom);
        applySize();
        element.setAttribute("data-zoom", zoom.toFixed(2));
        return true;
      }
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
        if (current) { current = null; currentId = null; refit(); redraw(); }   // what a first finger began is not a stroke - nor the room it made
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
        if (current || erase) return;                                      // a palm beside a pen
        if (!canRead || (ev.pointerType === "mouse" && ev.button !== 0)) return;
        if (erasing || (ev.pointerType === "pen" && (ev.buttons & 32))) {  // the Erase mode, or a pen turned round
          ev.preventDefault();
          clearTimeout(timer);
          try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
          erase = { id: ev.pointerId, at: null, removed: [] };
          eraseTo(point(ev));
          return;
        }
        ev.preventDefault();
        clearTimeout(timer);
        if (!strokes.length && !current) t0 = ev.timeStamp;
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
        strokes.push(current);
        done.push({ kind: "stroke", stroke: current });
        undone = [];
        current = null;
        currentId = null;
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
        if (erase && ev.pointerId === erase.id) { finishErase(); return; }
        endStroke(ev);
      }
      canvas.addEventListener("pointerup", lift);
      canvas.addEventListener("pointercancel", lift);

      // ---- undo, redo, clear -----------------------------------------------------------
      function changed(delay) {
        element.setAttribute("data-strokes", String(strokes.length));
        dirty = strokes.length > 0;
        undoBtn.disabled = !done.length;
        redoBtn.disabled = !undone.length;
        clearBtn.disabled = !strokes.length;
        redraw();
        clearTimeout(timer);
        if (strokes.length) timer = setTimeout(recognize, delay); else reset();
      }
      function clearInk() {
        if (!strokes.length) return false;
        done.push({ kind: "clear", strokes: strokes.slice() });
        undone = [];
        strokes = [];
        shrink();
        changed(0);
        return true;
      }
      undoBtn.addEventListener("click", function () {
        var a = done.pop();
        if (!a) return;
        if (a.kind === "stroke") strokes = strokes.slice(0, -1);
        else if (a.kind === "clear") strokes = a.strokes.slice();
        else {                            // erased: each stroke back in its place, the last taken first
          strokes = strokes.slice();
          for (var i = a.removed.length - 1; i >= 0; i--) strokes.splice(a.removed[i].index, 0, a.removed[i].stroke);
        }
        refit();                          // the room the ink taken back had made goes with it
        undone.push(a);
        changed(300);
      });
      redoBtn.addEventListener("click", function () {
        var a = undone.pop();
        if (!a) return;
        if (a.kind === "stroke") { strokes.push(a.stroke); growToFit(); }
        else if (a.kind === "clear") { strokes = []; shrink(); }
        else {                            // erased again, in the order it went
          strokes = strokes.slice();
          a.removed.forEach(function (r) { strokes.splice(r.index, 1); });
          refit();
        }
        done.push(a);
        changed(300);
      });
      clearBtn.addEventListener("click", clearInk);
      readBtn.addEventListener("click", recognize);

      // ---- reading -----------------------------------------------------------------------
      function reset() {
        clearTimeout(timer);
        seq++;
        cands.textContent = "";
        field.value = "";
        picks = { choices: {}, constants: {} };
        note.textContent = canRead ? "" : status.reason;
        note.className = "ink-note" + (canRead ? "" : " error");
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
      function recognize() {
        clearTimeout(timer);
        if (!strokes.length || !canRead) return;
        dirty = false;
        var my = ++seq;
        element.classList.add("ink-busy");
        note.textContent = "Reading…";
        note.className = "ink-note";
        updateSummary();
        // quiet: the editor's overlay would cover the area while one writes on
        api.call("recognize", { strokes: strokes }, { quiet: true }).then(function (res) {
          if (my !== seq) return;
          element.classList.remove("ink-busy");
          cands.textContent = "";
          if (!res.candidates.length) { note.textContent = "Nothing could be read"; show(null); return; }
          note.textContent = "Read in " + res.ms + " ms" + (res.candidates.length > 1 ? " — the best reading first, pick another if it is the one" : "");
          res.candidates.forEach(function (c, i) {
            var b = h("button", { type: "button", class: "ink-cand", role: "option", title: c.latex });
            typeset(b, c.display || c.latex, c.latex);      // the delimiters sized (\left, \right): easier on the eye
            b.addEventListener("click", function () {
              choose(c, b);
              // picked by hand: on to its LaTeX, its options and the buttons that put it in
              // (the first reading, chosen for the writer, leaves the page where it is)
              requestAnimationFrame(function () { reveal(actions, "end"); });
            });
            cands.appendChild(b);
            if (i === 0) choose(c, b);
          });
        }, function (e) {
          if (my !== seq) return;
          element.classList.remove("ink-busy");
          note.textContent = String((e && e.message) || e);
          note.className = "ink-note error";
          updateSummary();
        });
      }
      function choose(c, button) {
        for (var i = 0; i < cands.children.length; i++) {
          var b = cands.children[i];
          b.classList.toggle("ink-chosen", b === button);
          b.setAttribute("aria-selected", b === button ? "true" : "false");
        }
        field.value = c.latex;
        picks = { choices: {}, constants: {} };
        show(c.reading);
      }
      function reread() {
        var my = ++seq;
        if (!field.value.trim()) { show(null); return; }
        api.call("read", { latex: field.value, choices: picks.choices, constants: picks.constants }, { quiet: true })
          .then(function (res) { if (my === seq) show(res.reading); }, function () {});
      }
      field.addEventListener("input", function () {
        for (var i = 0; i < cands.children.length; i++) cands.children[i].classList.remove("ink-chosen");
        picks = { choices: {}, constants: {} };     // new text: the old picks do not apply to it
        clearTimeout(readTimer);
        readTimer = setTimeout(reread, 400);
      });
      field.addEventListener("keydown", function (ev) {
        ev.stopPropagation();                        // the editor's keys are not for the box
        if (ev.key === "Enter") { ev.preventDefault(); insert(target()); }       // what the first button says
      });

      function show(reading) {
        last = reading || null;
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
          sel.addEventListener("change", function () { picks.choices[a.key] = parseInt(sel.value, 10); reread(); });
          ambig.appendChild(h("label", { class: "ink-point" }, [h("code", { class: "ink-fragment" }, [a.fragment]), " → ", sel]));
        });
        (reading.constants || []).forEach(function (c) {
          var box = h("input", { type: "checkbox" });
          box.checked = !!c.on;
          box.addEventListener("change", function () { picks.constants[c.name] = box.checked; reread(); });
          consts.appendChild(h("label", { class: "ink-const", title: c.label }, [box, " ", h("code", {}, [c.name]), " is " + c.value + " (" + c.label + ")"]));
        });
      }

      function latexBox() {
        return api.editor && api.editor.root ? api.editor.root.querySelector(".se-addon-latex .ltx-input") : null;
      }
      // Where the first button puts the reading: over the selection, at the
      // caret, or after the whole formula when there is neither.
      function target() {
        if (api.range() || api.selected()) return "selection";
        return api.insertion && api.insertion() ? "caret" : "end";
      }
      function updateInsert() {
        var ok = !!(last && last.ok), where = target();
        insertAll.disabled = !ok;
        insertSel.disabled = !ok;
        insertSel.textContent = where === "caret" ? "Add to cursor" : where === "end" ? "Add to end"
                              : api.range() ? "Replace the selected range" : "Replace the selection";
        toLatex.hidden = full || !latexBox() || !field.value.trim();
      }
      function updateSummary() {
        sheetSummary.textContent = last && last.ok ? last.src : (note.textContent || "Readings");
        sheetChevron.innerHTML = chevronIcon(folded ? "up" : "down");
        sheetHead.setAttribute("aria-expanded", folded ? "false" : "true");
        sheet.classList.toggle("ink-folded", folded);
      }
      sheetHead.addEventListener("click", function () { folded = !folded; updateSummary(); });

      function insert(which) {
        if (!last || !last.ok) return;
        var payload = { latex: field.value, path: "/", choices: picks.choices, constants: picks.constants };
        var r = api.range(), sel = api.selected();
        if (which === "selection" && r) { payload.path = r.parent; payload.children = api.editor._rangeIndices(); }
        else if (which === "selection" && sel) payload.path = sel;
        else if (which === "caret") payload.caret = api.insertion();
        else if (which === "end") payload.end = true;
        api.call("insert", payload).then(function () {
          setFull(false);                          // the formula it went into, in sight
          if (!clearInk()) reset();                // the ink goes too - Undo brings it back
          note.textContent = "Inserted.";
          updateSummary();
          showFormula(api);                        // and the page back up to it
        }, function (e) {
          note.textContent = String((e && e.message) || e);
          note.className = "ink-note error";
          updateSummary();
        });
      }
      insertSel.addEventListener("click", function () { insert(target()); });
      insertAll.addEventListener("click", function () { insert("whole"); });
      toLatex.addEventListener("click", function () {
        var box = latexBox();
        if (!box) return;
        var d = box.closest("details");
        if (d) d.open = true;
        box.value = field.value;
        box.dispatchEvent(new Event("input", { bubbles: true }));
        box.focus();
      });

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

      if (!canRead) canvas.classList.add("ink-off");
      reset();
      setTimeout(fitPad, 0);

      return {
        element: element,
        title: "Handwriting",
        help: "<section><h3>The tools</h3><ul>"
          + "<li>" + toolIcon("undo", 16) + " <b>Undo</b> takes back the last stroke - or the erasing, the clearing, the ink an insertion took.</li>"
          + "<li>" + toolIcon("redo", 16) + " <b>Redo</b> puts back what Undo took.</li>"
          + "<li>" + toolIcon("erase", 16) + " <b>Erase</b> is a switch: while it is on, the pen, the finger or the mouse takes away every stroke it passes over instead of writing. Press it again to write. A pen turned round erases too.</li>"
          + "<li>" + toolIcon("clear", 16) + " <b>Clear</b> takes all the ink away; Undo brings it back.</li>"
          + "<li>" + toolIcon("read", 16) + " <b>Read</b> reads what is written now, without waiting for the pause.</li>"
          + "<li>" + toolIcon("full", 16) + " <b>Full screen</b>, in the area's corner: the writing area as large as the screen, the tools on top and the readings in a sheet at the bottom that folds away. Esc or the same button comes back, and so does inserting.</li>"
          + "</ul></section>"
          + "<section><h3>Writing a formula by hand</h3><ul>"
          + "<li>Write in the area with a pen, a finger or the mouse. A moment after the pen lifts, what is written is read.</li>"
          + "<li>Nearing the right or the bottom edge, the area makes room beyond it; a small button in the middle of an edge scrolls it that way, and so does the wheel. Only that button scrolls: the rest of the edge writes.</li>"
          + "<li>Two fingers never write: pinch to zoom the area in or out, drag with two fingers to move it about (a pinch on a trackpad zooms too).</li>"
          + "<li>The best reading comes first and the others after it: pick the one you wrote. Its LaTeX is in the box, to correct; the line under it is what SymPy gets, with a menu for each part that can be read more than one way and a switch for each constant name.</li>"
          + "<li><b>Replace the selection</b> puts it over what is selected (a node or a range); with a cursor in the formula instead the button is <b>Add to cursor</b>, and with neither <b>Add to end</b>: the reading goes in as if typed there - multiplied, or added when it begins with + or -. <b>Replace the whole expression</b> makes it the formula. Enter in the box does what the first button says. Either way the page goes back up to the formula.</li>"
          + "<li>The reading is done by math-ocr's stroke model. It reads one formula at a time, and mixes up look-alike glyphs most (<code>1</code> and <code>|</code>, <code>V</code> and <code>v</code>).</li>"
          + "</ul></section>"
          + (status.notice ? '<section><h3>About the model</h3><p style="white-space: pre-wrap">' + noticeHtml(status.notice) + "</p></section>" : ""),
        onSelect: function () { updateInsert(); },
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
