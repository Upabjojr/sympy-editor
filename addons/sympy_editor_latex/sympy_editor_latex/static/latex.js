/*
 * sympy-editor add-on "latex": LaTeX in, under the formula.
 *
 * A box for LaTeX; Python reads it (method "read") and answers with a first
 * reading, the ambiguities of the text - each a menu of the readings it
 * allows, the whole expression under each - and the constant names it uses,
 * each a switch (\pi the constant, or a symbol called pi).  Every change
 * asks again; "insert" puts the reading into the document, over the
 * selection or as the whole expression.
 */
SympyEditor.registerAddon("latex", {
  tools: [
    { cmd: "focus", label: "LaTeX", title: "Type or paste LaTeX in the box under the formula",
      run: function (api) {
        var box = document.querySelector(".se-addon-latex .ltx-input");
        if (box) { var d = box.closest("details"); if (d) d.open = true; box.focus(); }
      } }
  ],
  mount: function (api) {
    var h = api.h;
    var input = h("textarea", { class: "ltx-input", rows: "2", spellcheck: "false", autocomplete: "off", autocapitalize: "off",
      placeholder: "\\frac{x^2}{2} + \\sin x \\cos y … (Ctrl+Enter inserts)",
      title: "LaTeX to read: type or paste it; the reading appears below, with its ambiguities" });
    var readBtn = h("button", { type: "button", class: "ltx-read", title: "Read the LaTeX again" }, ["Read"]);
    var preview = h("div", { class: "ltx-preview", "aria-live": "polite" });
    var src = h("code", { class: "ltx-src", title: "The reading as SymPy source" });
    var note = h("div", { class: "ltx-note" });
    var ambig = h("div", { class: "ltx-ambig" });
    var consts = h("div", { class: "ltx-consts" });
    var insertSel = h("button", { type: "button", class: "ltx-insert", disabled: "" }, ["Replace the selection"]);
    var insertAll = h("button", { type: "button", class: "ltx-insert-all", disabled: "" }, ["Replace the whole expression"]);
    var element = h("div", { class: "ltx-panel" }, [
      h("div", { class: "ltx-row" }, [input, readBtn]),
      note, preview, src, ambig, consts,
      h("div", { class: "ltx-actions" }, [insertSel, insertAll])
    ]);

    var choices = {};         // ambiguity key -> alternative index (the user's picks and the reader's own decisions)
    var constants = {};       // constant name -> on/off (the user's switches; the defaults otherwise)
    var last = null;          // the last reading
    var seq = 0, timer = null, katex = null;

    api.katex().then(function (k) { katex = k; if (last && last.ok) render(last); }, function () {});

    function typeset(el, tex, fallback) {
      el.textContent = "";
      if (katex && tex) {
        try { el.innerHTML = katex.renderToString(tex, { throwOnError: false, displayMode: false }); return; }
        catch (e) { /* fall through to the text */ }
      }
      el.textContent = fallback || tex || "";
    }

    function schedule() { clearTimeout(timer); timer = setTimeout(read, 450); }

    function read() {
      clearTimeout(timer);
      var text = input.value.trim();
      if (!text) { clear(); return; }
      var my = ++seq;
      element.classList.add("ltx-busy");
      api.call("read", { latex: text, choices: choices, constants: constants }).then(function (res) {
        if (my !== seq) return;
        element.classList.remove("ltx-busy");
        last = res;
        if (res.ok && res.choices) choices = res.choices;   // every decision, so the next pick changes only itself
        render(res);
      }, function (e) {
        if (my !== seq) return;
        element.classList.remove("ltx-busy");
        last = null;
        clear();
        note.textContent = String(e && e.message || e);
        note.className = "ltx-note error";
      });
    }

    function clear() {
      preview.textContent = ""; src.textContent = ""; ambig.textContent = ""; consts.textContent = "";
      note.textContent = ""; note.className = "ltx-note";
      insertSel.disabled = insertAll.disabled = true;
    }

    function render(res) {
      if (!res.ok) {
        clear();
        note.textContent = res.error || "This LaTeX could not be read";
        note.className = "ltx-note error";
        return;
      }
      note.className = "ltx-note";
      note.textContent = res.ambiguities.length
        ? (res.ambiguities.length === 1 ? "One part of this can be read two ways: pick below." : res.ambiguities.length + " parts of this can be read several ways: pick below.")
        : "";
      typeset(preview, res.latex, res.src);
      src.textContent = res.src;
      // the ambiguities: a menu per point, the whole expression under each alternative
      ambig.textContent = "";
      res.ambiguities.forEach(function (a) {
        var sel = h("select", { class: "ltx-choice", title: "How to read " + a.fragment });
        a.options.forEach(function (o, i) {
          var opt = h("option", { value: String(i) }, [o.invalid ? "(not a reading)" : o.src]);
          if (o.invalid) opt.disabled = true;
          if (i === a.choice) opt.selected = true;
          sel.appendChild(opt);
        });
        sel.addEventListener("change", function () { choices[a.key] = parseInt(sel.value, 10); read(); });
        var shown = h("span", { class: "ltx-shown" });
        var current = a.options[a.choice];
        typeset(shown, current && current.latex, current && current.src);
        ambig.appendChild(h("label", { class: "ltx-point" }, [h("code", { class: "ltx-fragment" }, [a.fragment]), " → ", sel, shown]));
      });
      // the constants: a switch per name that occurs
      consts.textContent = "";
      res.constants.forEach(function (c) {
        var box = h("input", { type: "checkbox" });
        box.checked = !!c.on;
        box.addEventListener("change", function () { constants[c.name] = box.checked; read(); });
        consts.appendChild(h("label", { class: "ltx-const", title: c.label }, [box, " ", h("code", {}, [c.name]), " is " + c.value + " (" + c.label + ")"]));
      });
      updateInsert();
    }

    function updateInsert() {
      var ok = !!(last && last.ok);
      insertAll.disabled = !ok;
      var sel = api.selected(), r = api.range();
      insertSel.disabled = !ok || (!sel && !r) || sel === "/";
      insertSel.textContent = r ? "Replace the selected range" : "Replace the selection";
    }

    function insert(path) {
      if (!last || !last.ok) return;
      var text = input.value.trim();
      var payload = { latex: text, choices: choices, constants: constants, path: path };
      var r = api.range();
      if (path !== "/" && r) { payload.path = r.parent; payload.children = api.editor._rangeIndices(); }
      api.call("insert", payload).then(function () {
        note.textContent = "Inserted.";
        note.className = "ltx-note";
      }, function (e) {
        note.textContent = String(e && e.message || e);
        note.className = "ltx-note error";
      });
    }

    input.addEventListener("input", function () { choices = {}; schedule(); });   // new text: the old picks no longer apply
    input.addEventListener("keydown", function (ev) {
      ev.stopPropagation();                        // the editor's keys are not for the box
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); insert(api.selected() && api.selected() !== "/" ? api.selected() : "/"); }
    });
    readBtn.addEventListener("click", read);
    insertSel.addEventListener("click", function () { insert(api.range() ? api.range().parent : api.selected()); });
    insertAll.addEventListener("click", function () { insert("/"); });

    return {
      element: element,
      title: "LaTeX",
      help: "<section><h3>LaTeX in</h3><ul>"
        + "<li>Type or paste LaTeX in the box; the reading appears under it, rendered and as SymPy source.</li>"
        + "<li>Where the text can be read in several ways — <code>f(x)</code> applied or multiplied, how far <code>\\sin x \\cos y</code> reaches — a menu shows every reading of that part; the first is the usual convention, pick another and the whole follows.</li>"
        + "<li>Names that usually mean a constant — <code>\\pi</code>, <code>e</code>, <code>i</code>, <code>\\gamma</code> — are switches: the constant, or a plain symbol of that name.</li>"
        + "<li><b>Replace the selection</b> puts the reading over what is selected; <b>Replace the whole expression</b> (or Ctrl+Enter) makes it the formula.</li>"
        + "</ul></section>",
      onSelect: function () { updateInsert(); },
      destroy: function () { clearTimeout(timer); }
    };
  }
});
