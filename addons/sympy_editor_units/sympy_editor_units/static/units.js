/*
 * The units add-on's panel: the selection's dimension, what does not add up
 * under it, and conversions.  A plain script (no imports), run once per page
 * with `SympyEditor` in scope.
 *
 * Python does the physics (sympy.physics.units): every snapshot carries
 * `snap.units` - {short, has_units, problems: [{path, message}]} for the whole
 * formula, which is what the formula's outlines come from - and the panel
 * asks `inspect` for the selection (its dimension, the problems under it, its
 * SI form).  Convert / SI base units / Simplify units are changes: each a step
 * of the history, undone with Undo.
 */
SympyEditor.registerAddon("units", {
  mount: function (api) {
    var h = api.h;
    var katex = null;
    var opts = api.options || {};

    function tex(el, src) {
      if (katex) {
        try { el.innerHTML = katex.renderToString(src, { throwOnError: false, output: "html" }); return; }
        catch (e) { /* fall through to text */ }
      }
      el.textContent = src;
    }

    function button(label, title, cls) {
      return h("button", { type: "button", class: "su-btn" + (cls ? " " + cls : ""), title: title }, [label]);
    }

    // ---- the dimension of the selection -------------------------------------
    var what = h("span", { class: "su-what" }, ["The formula"]);
    var dimName = h("span", { class: "su-dim-name" });
    var dimTex = h("span", { class: "su-dim-tex" });
    var dimRow = h("div", { class: "su-row su-dim" }, [what, h("span", { class: "su-is" }, ["is"]), dimName, dimTex]);
    var siTex = h("span", { class: "su-si-tex" });
    var siRow = h("div", { class: "su-row su-si", hidden: "" }, [h("span", { class: "su-label" }, ["In SI base units"]), siTex]);

    // ---- the check ------------------------------------------------------------
    var verdict = h("div", { class: "su-verdict" });
    var problemList = h("ul", { class: "su-problems" });

    // ---- conversions -------------------------------------------------------
    var listId = "su-targets-" + Math.random().toString(36).slice(2);
    var datalist = h("datalist", { id: listId });
    (opts.targets || []).forEach(function (t) { datalist.appendChild(h("option", { value: t })); });
    var target = h("input", { type: "text", class: "su-target", list: listId, placeholder: "km/hour",
      "aria-label": "Units to convert to", title: "Units to convert the selection to: km/hour, joule, kg*meter**2/second**2 - or several, comma-separated (kg, meter, second)" });
    var convert = button("Convert", "Rewrite the selection in these units (a step of the history)", "su-primary");
    var si = button("SI base units", "Rewrite the selection in metres, kilograms, seconds, amperes, kelvins, moles, candelas");
    var simplify = button("Simplify units", "Merge the units of the selection: newton*meter is joule, kilometer/meter is 1000");
    var convertRow = h("div", { class: "su-row su-convert" }, [target, convert, si, simplify, datalist]);

    // ---- the switch ---------------------------------------------------------
    var shortBox = h("input", { type: "checkbox", class: "su-short-box" });
    var shortNames = (opts.short || []).slice(0, 12).join(", ");
    var shortLabel = h("label", { class: "su-short", title: "With this on, " + shortNames + "... are units, not variables (names the formula already uses stay what they are)" },
      [shortBox, " Short unit names (m, s, g, N, J…)"]);

    var note = h("div", { class: "su-note" });
    var element = h("div", { class: "su-panel" }, [dimRow, siRow, verdict, problemList, convertRow, shortLabel, note]);

    api.katex().then(function (k) { katex = k; refreshTex(); }, function () {});

    var last = null;        // the last inspect answer
    var askedKey = null;    // what it was for: selection and formula
    var formulaProblems = [];

    function target_() {
      var r = api.range && api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }

    function refreshTex() {
      if (!last) return;
      var d = last.dimension || {};
      tex(dimTex, d.latex || "?");
      if (last.si) tex(siTex, last.si);
    }

    function setNote(text, isError) {
      note.textContent = text || "";
      note.className = "su-note" + (isError ? " su-error" : "");
    }

    function showInspect(res) {
      last = res;
      var d = res.dimension || {};
      var sel = target_();
      what.textContent = sel.children ? "The selected terms" : (sel.path === "/" ? "The formula" : "The selection");
      dimName.textContent = d.known ? (d.name || "of dimension") : "of a dimension that cannot be told";
      dimTex.hidden = !d.known;
      refreshTex();
      siRow.hidden = !res.si;
      problemList.textContent = "";
      var problems = res.problems || [];
      if (!res.has_units) {
        verdict.className = "su-verdict su-none";
        verdict.textContent = "No units here.";
      } else if (!problems.length) {
        verdict.className = "su-verdict su-ok";
        verdict.textContent = "✓ The dimensions agree.";
      } else {
        verdict.className = "su-verdict su-bad";
        verdict.textContent = problems.length === 1 ? "1 thing does not add up:" : problems.length + " things do not add up:";
        problems.forEach(function (p) {
          var src = h("code", { class: "su-src" });
          src.textContent = p.src;
          var item = h("li", {}, [h("button", { type: "button", class: "su-problem", title: "Select it in the formula" },
            [src, h("span", { class: "su-msg" }, [" — " + p.message])])]);
          item.firstChild.addEventListener("click", function () { api.select(p.path); });
          problemList.appendChild(item);
        });
      }
    }

    function showNoUnits() {
      last = null;
      what.textContent = "The formula";
      dimName.textContent = "has no units";
      dimTex.textContent = "";
      siRow.hidden = true;
      problemList.textContent = "";
      verdict.className = "su-verdict su-none";
      verdict.textContent = "Type a unit's name into the formula: 5*meter/second, 3*km/hour, speed_of_light.";
    }

    function inspect(force) {
      var st = api.state();
      if (!st || st.preview) return;
      var info = st.units || {};
      if (!info.has_units) { askedKey = null; showNoUnits(); return; }
      var sel = target_();
      var key = (st.src || "") + "|" + sel.path + "|" + (sel.children ? sel.children.join(",") : "");
      if (!force && key === askedKey) return;
      askedKey = key;
      api.call("inspect", sel, { quiet: true }).then(function (res) {
        if (askedKey === key) showInspect(res);
      }, function (e) { if (askedKey === key) setNote(String(e && e.message || e), true); });
    }

    // Outline what does not add up, in the formula itself.  The rendering is
    // made again with every state, so the marks go on after each.
    function markFormula() {
      var view = api.editor && api.editor.view;
      if (!view) return;
      var old = view.querySelectorAll(".su-bad-node");
      for (var i = 0; i < old.length; i++) old[i].classList.remove("su-bad-node");
      formulaProblems.forEach(function (p) {
        var el = view.querySelector('[data-path="' + p.path + '"]');
        if (el) { el.classList.add("su-bad-node"); el.setAttribute("title", p.message); }
      });
    }

    function change(method, extra) {
      var sel = target_();
      var payload = { path: sel.path };
      if (sel.children) payload.children = sel.children;
      for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
      setNote("");
      return api.call(method, payload).then(function () { setNote(""); },
        function (e) { setNote(String(e && e.message || e).replace(/^ValueError: /, ""), true); });
    }

    convert.addEventListener("click", function () {
      var text = target.value.trim();
      if (!text) { setNote("Type the units to convert to first (km/hour, joule...).", true); target.focus(); return; }
      change("convert", { target: text });
    });
    target.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); convert.click(); }
      ev.stopPropagation();          // the editor's keys are not this field's
    });
    si.addEventListener("click", function () { change("si", {}); });
    simplify.addEventListener("click", function () { change("simplify", {}); });
    shortBox.addEventListener("change", function () {
      var on = shortBox.checked;
      api.call("short", { on: on }, { quiet: true }).then(function () {
        setNote(on ? "m, s, g, N... typed from now on are units; names the formula already uses stay variables."
                   : "m, s, g, N... typed from now on are variables again.");
      }, function (e) { shortBox.checked = !on; setNote(String(e && e.message || e), true); });
    });

    return {
      element: element,
      title: "Units",
      help: "<section><h3>Units</h3><ul>"
        + "<li><b>Typing units.</b> With the add-on on, SymPy's units and constants are names in the formula: "
        + "<code>5*meter/second</code>, <code>3 km/hour</code>, <code>kg</code>, <code>newton</code>, <code>joule</code>, "
        + "<code>speed_of_light</code>, <code>gravitational_constant</code>, <code>planck</code>... "
        + "They are drawn as their symbols (m, km, N, c, G).</li>"
        + "<li><b>Short unit names.</b> One-letter abbreviations (<code>m</code>, <code>s</code>, <code>g</code>, <code>N</code>, <code>c</code>...) "
        + "are variables unless <i>Short unit names</i> is ticked; a name the formula already uses stays what it is. The switch is kept with the session.</li>"
        + "<li><b>Dimension.</b> The panel shows the dimension of the selection (of the whole formula when nothing is selected) "
        + "in base dimensions - M mass, L length, T time, I current, &Theta; temperature, N amount, J luminous intensity - "
        + "with its name when it has one (velocity, force, energy), and its form in SI base units.</li>"
        + "<li><b>The check.</b> Every sum, equation, exponent and function argument is checked: a term whose dimension differs from "
        + "the rest of its sum, an equation whose sides differ, a dimensioned exponent or argument of sin, exp, log... is listed "
        + "and outlined in the formula; a click on it selects it. A plain variable counts as dimensionless, as SymPy has it.</li>"
        + "<li><b>Convert.</b> Type the units (<code>km/hour</code>, <code>joule</code>, <code>kg*meter**2/second**2</code>, or several "
        + "comma-separated: <code>kg, meter, second</code>) and press <i>Convert</i> or Enter: the selection is rewritten in them. "
        + "<i>SI base units</i> rewrites it in metres, kilograms, seconds...; <i>Simplify units</i> merges them (newton*meter is joule). "
        + "Each is a step of the history: Undo takes it back. Both are also in the Transform menu.</li>"
        + "</ul></section>",
      onState: function (snap) {
        if (!snap || snap.preview) return;
        var info = snap.units || {};
        shortBox.checked = !!info.short;
        formulaProblems = info.problems || [];
        setTimeout(markFormula, 0);
      },
      onSelect: function () {
        markFormula();
        inspect(false);
      },
      destroy: function () {
        var view = api.editor && api.editor.view;
        if (!view) return;
        var old = view.querySelectorAll(".su-bad-node");
        for (var i = 0; i < old.length; i++) old[i].classList.remove("su-bad-node");
      }
    };
  }
});
