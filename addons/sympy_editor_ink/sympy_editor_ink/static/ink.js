/*
 * sympy-editor add-on "ink": write a formula by hand.
 *
 * A writing area under the formula.  Each stroke is kept as points
 * [x, y, t] (CSS pixels, milliseconds from the first stroke); a pause after
 * the pen lifts sends them to Python (method "recognize"), where math-ocr's
 * stroke model reads them.  The readings come back as LaTeX, best first, each
 * with what SymPy makes of it; the chosen one can be corrected in its box and
 * goes in over the selection or as the whole expression.
 */
SympyEditor.registerAddon("ink", {
  mount: function (api) {
    var h = api.h;
    var status = (api.options && api.options.status) || { available: false, reason: "The add-on's Python said nothing about its model" };
    var canvas = h("canvas", { class: "ink-canvas", "aria-label": "Writing area: write a formula with a pen, a finger or the mouse" });
    var undoBtn = h("button", { type: "button", title: "Take back the last stroke" }, ["Undo stroke"]);
    var clearBtn = h("button", { type: "button", title: "Start again" }, ["Clear"]);
    var readBtn = h("button", { type: "button", title: "Read what is written, now" }, ["Read"]);
    var note = h("div", { class: "ink-note", "aria-live": "polite" });
    var cands = h("div", { class: "ink-cands", role: "listbox", "aria-label": "Readings, best first" });
    var field = h("input", { class: "ink-latex", type: "text", spellcheck: "false", autocomplete: "off", autocapitalize: "off",
      placeholder: "The reading as LaTeX - correct it here", "aria-label": "The reading as LaTeX" });
    var src = h("code", { class: "ink-src", title: "What SymPy gets" });
    var insertSel = h("button", { type: "button", class: "ink-insert", disabled: "" }, ["Replace the selection"]);
    var insertAll = h("button", { type: "button", class: "ink-insert-all", disabled: "" }, ["Replace the whole expression"]);
    var toLatex = h("button", { type: "button", class: "ink-to-latex", hidden: "",
      title: "Hand the reading to the LaTeX panel, which offers every way it can be read" }, ["Open in the LaTeX panel"]);
    var element = h("div", { class: "ink-panel" }, [
      canvas,
      h("div", { class: "ink-row" }, [undoBtn, clearBtn, readBtn]),
      note, cands, field, src,
      h("div", { class: "ink-actions" }, [insertSel, insertAll, toLatex])
    ]);

    var strokes = [];          // [[[x, y, t], ...], ...]
    var current = null;        // the stroke being drawn, and its pointer
    var currentId = null;
    var t0 = 0;
    var timer = null, readTimer = null, seq = 0;
    var katex = null;
    var last = null;           // the reading shown: {ok, src, latex, error, readings}

    api.katex().then(function (k) { katex = k; }, function () {});

    // ---- the writing area ---------------------------------------------------
    var ctx = canvas.getContext("2d");
    function size() {
      var r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      var w = Math.max(1, Math.round(r.width * dpr)), hh = Math.max(1, Math.round(r.height * dpr));
      if (canvas.width !== w || canvas.height !== hh) { canvas.width = w; canvas.height = hh; }
      redraw();
    }
    function redraw() {
      var dpr = window.devicePixelRatio || 1;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
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
    }
    function point(ev) {
      var r = canvas.getBoundingClientRect();
      return [Math.round((ev.clientX - r.left) * 10) / 10, Math.round((ev.clientY - r.top) * 10) / 10, Math.round(ev.timeStamp - t0)];
    }
    var resizer = window.ResizeObserver ? new ResizeObserver(size) : null;
    if (resizer) resizer.observe(canvas); else window.addEventListener("resize", size);

    canvas.addEventListener("pointerdown", function (ev) {
      if (!status.available || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      ev.preventDefault();
      clearTimeout(timer);
      if (!strokes.length && !current) t0 = ev.timeStamp;
      try { canvas.setPointerCapture(ev.pointerId); } catch (e) { /* not capturable */ }
      current = [point(ev)];
      currentId = ev.pointerId;
      redraw();
    });
    canvas.addEventListener("pointermove", function (ev) {
      if (!current || ev.pointerId !== currentId) return;
      var evs = (ev.getCoalescedEvents && ev.getCoalescedEvents()) || [];
      if (!evs.length) evs = [ev];
      for (var i = 0; i < evs.length; i++) current.push(point(evs[i]));
      redraw();
    });
    function endStroke(ev) {
      if (!current || ev.pointerId !== currentId) return;
      strokes.push(current);
      current = null;
      currentId = null;
      redraw();
      clearTimeout(timer);
      timer = setTimeout(recognize, 700);       // a pause: the formula may be finished
    }
    canvas.addEventListener("pointerup", endStroke);
    canvas.addEventListener("pointercancel", endStroke);

    undoBtn.addEventListener("click", function () {
      strokes.pop();
      redraw();
      if (strokes.length) { clearTimeout(timer); timer = setTimeout(recognize, 300); } else reset();
    });
    clearBtn.addEventListener("click", function () { strokes = []; redraw(); reset(); });
    readBtn.addEventListener("click", recognize);

    function reset() {
      clearTimeout(timer);
      seq++;
      cands.textContent = "";
      field.value = "";
      note.textContent = status.available ? "" : status.reason;
      note.className = "ink-note" + (status.available ? "" : " error");
      show(null);
    }

    // ---- reading ---------------------------------------------------------------
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
      if (!strokes.length || !status.available) return;
      var my = ++seq;
      element.classList.add("ink-busy");
      note.textContent = "Reading…";
      note.className = "ink-note";
      // quiet: the editor's overlay would cover the area while one writes on
      api.call("recognize", { strokes: strokes }, { quiet: true }).then(function (res) {
        if (my !== seq) return;
        element.classList.remove("ink-busy");
        cands.textContent = "";
        if (!res.candidates.length) { note.textContent = "Nothing could be read"; show(null); return; }
        note.textContent = "Read in " + res.ms + " ms" + (res.candidates.length > 1 ? " — the best reading first, pick another if it is the one" : "");
        res.candidates.forEach(function (c, i) {
          var b = h("button", { type: "button", class: "ink-cand", role: "option", title: c.latex });
          typeset(b, c.latex, c.latex);
          b.addEventListener("click", function () { choose(c, b); });
          cands.appendChild(b);
          if (i === 0) choose(c, b);
        });
      }, function (e) {
        if (my !== seq) return;
        element.classList.remove("ink-busy");
        note.textContent = String((e && e.message) || e);
        note.className = "ink-note error";
      });
    }

    function choose(c, button) {
      for (var i = 0; i < cands.children.length; i++) {
        var b = cands.children[i];
        b.classList.toggle("ink-chosen", b === button);
        b.setAttribute("aria-selected", b === button ? "true" : "false");
      }
      field.value = c.latex;
      show(c.reading);
    }

    field.addEventListener("input", function () {
      for (var i = 0; i < cands.children.length; i++) cands.children[i].classList.remove("ink-chosen");
      clearTimeout(readTimer);
      readTimer = setTimeout(function () {
        var my = ++seq;
        if (!field.value.trim()) { show(null); return; }
        api.call("read", { latex: field.value }, { quiet: true }).then(function (res) { if (my === seq) show(res.reading); }, function () {});
      }, 400);
    });
    field.addEventListener("keydown", function (ev) {
      ev.stopPropagation();                        // the editor's keys are not for the box
      if (ev.key === "Enter") { ev.preventDefault(); insert(api.range() || (api.selected() && api.selected() !== "/") ? "selection" : "whole"); }
    });

    function show(reading) {
      last = reading || null;
      if (!reading) {
        src.textContent = "";
      } else if (reading.ok) {
        src.textContent = reading.src + (reading.readings > 1 ? "   — " + reading.readings + " ways to read it" : "");
      } else {
        src.textContent = reading.error || "This could not be read";
      }
      src.className = "ink-src" + (reading && !reading.ok ? " error" : "");
      updateInsert();
    }

    function latexBox() {
      return api.editor && api.editor.root ? api.editor.root.querySelector(".se-addon-latex .ltx-input") : null;
    }

    function updateInsert() {
      var ok = !!(last && last.ok);
      var sel = api.selected(), r = api.range();
      insertAll.disabled = !ok;
      insertSel.disabled = !ok || (!sel && !r) || sel === "/";
      insertSel.textContent = r ? "Replace the selected range" : "Replace the selection";
      toLatex.hidden = !latexBox() || !field.value.trim();
    }

    function insert(which) {
      if (!last || !last.ok) return;
      var payload = { latex: field.value, path: "/" };
      var r = api.range(), sel = api.selected();
      if (which === "selection" && r) { payload.path = r.parent; payload.children = api.editor._rangeIndices(); }
      else if (which === "selection" && sel && sel !== "/") payload.path = sel;
      api.call("insert", payload).then(function () {
        strokes = [];
        redraw();
        reset();
        note.textContent = "Inserted.";
      }, function (e) {
        note.textContent = String((e && e.message) || e);
        note.className = "ink-note error";
      });
    }
    insertSel.addEventListener("click", function () { insert("selection"); });
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

    if (!status.available) canvas.classList.add("ink-off");
    reset();
    setTimeout(size, 0);

    return {
      element: element,
      title: "Handwriting",
      help: "<section><h3>Writing a formula by hand</h3><ul>"
        + "<li>Write in the area with a pen, a finger or the mouse. A moment after the pen lifts, what is written is read; <b>Read</b> reads it at once, <b>Undo stroke</b> takes back the last stroke, <b>Clear</b> starts again.</li>"
        + "<li>The best reading comes first and the others after it: pick the one you wrote. Its LaTeX is in the box, to correct; the line under it is what SymPy gets.</li>"
        + "<li><b>Replace the selection</b> puts it over what is selected (a node or a range); <b>Replace the whole expression</b> makes it the formula. Enter in the box does the first when something is selected, the second otherwise.</li>"
        + "<li>Where it can be read in more than one way, <b>Open in the LaTeX panel</b> hands it to that panel, which offers every reading.</li>"
        + "<li>The reading is done by math-ocr's stroke model, in this Python. It reads one formula at a time, and mixes up look-alike glyphs most (<code>1</code> and <code>|</code>, <code>V</code> and <code>v</code>).</li>"
        + "</ul></section>",
      onSelect: function () { updateInsert(); },
      destroy: function () {
        clearTimeout(timer);
        clearTimeout(readTimer);
        if (resizer) resizer.disconnect(); else window.removeEventListener("resize", size);
      }
    };
  }
});
