/*
 * sympy-editor add-on "transforms": integral transforms of the selection.
 *
 * The panel picks a transform, the variable of the selection (one of its
 * free symbols) and the name of the new one.  "Compute" asks Python (a
 * query: nothing changes) and shows the result with its conditions in
 * words; "Apply" replaces the selection - a step of the history, unevaluated
 * (LaplaceTransform(f, t, s)) while the editor's "unevaluated" toggle is on.
 * Nothing is asked of Python on its own: a selection only refills the
 * variable list, from the snapshot's node table.
 */
SympyEditor.registerAddon("transforms", {
  mount: function (api) {
    var h = api.h;
    var table = (api.options && api.options.transforms) || [];
    var byKey = {};
    table.forEach(function (t) { byKey[t.key] = t; });

    var kind = h("select", { class: "tf-kind", title: "Which transform" });
    table.forEach(function (t) { kind.appendChild(h("option", { value: t.key }, [t.label])); });
    var varSel = h("select", { class: "tf-var", title: "The variable of the selection, which the transform integrates (or sums) over" });
    var newVar = h("input", { type: "text", class: "tf-new", spellcheck: "false", autocomplete: "off",
                              autocapitalize: "off", autocorrect: "off", title: "The name of the variable of the result" });
    var extraName = h("span", { class: "tf-extra-name" });
    var extra = h("input", { type: "text", class: "tf-extra", spellcheck: "false", autocomplete: "off",
                             autocapitalize: "off", autocorrect: "off" });
    var extraBox = h("label", { class: "tf-field tf-extra-box", hidden: "" }, [extraName, extra]);
    var compute = h("button", { type: "button", class: "tf-compute", title: "Show the transform and where it holds; the formula is left as it is" }, ["Compute"]);
    var apply = h("button", { type: "button", class: "tf-apply", title: "Replace the selection by its transform (a step Undo takes back); unevaluated while the editor's \"unevaluated\" toggle is on" }, ["Apply"]);
    var result = h("div", { class: "tf-result", "aria-live": "polite" });
    var conds = h("ul", { class: "tf-conds" });
    var convention = h("div", { class: "tf-convention" });
    var note = h("div", { class: "tf-note" });
    var element = h("div", { class: "tf-panel" }, [
      h("div", { class: "tf-bar" }, [
        h("label", { class: "tf-field" }, [h("span", {}, ["transform"]), kind]),
        h("label", { class: "tf-field" }, [h("span", {}, ["of"]), varSel]),
        h("label", { class: "tf-field" }, [h("span", {}, ["new variable"]), newVar]),
        extraBox
      ]),
      h("div", { class: "tf-buttons" }, [compute, apply]),
      note, result, conds, convention
    ]);

    var typedNew = false;     // the user named the new variable: a change of transform leaves it alone
    var typedExtra = false;
    var shownKey = null;      // the target whose variables are listed (onSelect does nothing for the same one)
    var shownSrc = null;      // ... and its source, so a change of the formula refreshes the list
    var resultFor = null;     // what the result on show was computed for
    var seq = 0;

    function target() {
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }
    function keyOf(t) { return t.path + (t.children ? ":" + t.children.join(",") : ""); }

    function spec() { return byKey[kind.value] || table[0]; }

    /** The variable list: the target's free symbols, the transform's usual
     *  name for its variable first chosen. */
    function fillVars() {
      var t = target();
      var node = api.node(t.path) || {};
      var free = (node.free || []).slice();
      var before = varSel.value;
      varSel.textContent = "";
      free.forEach(function (name) { varSel.appendChild(h("option", { value: name }, [name])); });
      varSel.disabled = !free.length;
      if (!free.length) varSel.appendChild(h("option", { value: "" }, ["(no variable)"]));
      shownKey = keyOf(t);
      shownSrc = node.src || null;
      pickVar(before);
    }

    function pickVar(keep) {
      var names = Array.prototype.map.call(varSel.options, function (o) { return o.value; });
      if (keep && names.indexOf(keep) >= 0 && !guessable(keep)) { varSel.value = keep; return; }
      var usual = (spec() && spec().vars) || [];
      for (var i = 0; i < usual.length; i++) {
        if (names.indexOf(usual[i]) >= 0) { varSel.value = usual[i]; return; }
      }
      if (keep && names.indexOf(keep) >= 0) varSel.value = keep;
    }
    // a variable the user did not choose themselves is chosen again for the
    // next transform (t for Laplace, s for its inverse)
    var chosenVar = null;
    function guessable(name) { return name !== chosenVar; }

    function fillDefaults() {
      var s = spec();
      if (!s) return;
      if (!typedNew) newVar.value = s.new;
      newVar.placeholder = s.new;
      if (s.extra) {
        extraBox.hidden = false;
        extraName.textContent = s.extra.name;
        extra.title = s.extra.title || "";
        extra.placeholder = s.extra["default"];
        if (!typedExtra) extra.value = s.extra["default"];
      } else {
        extraBox.hidden = true;
      }
      pickVar(varSel.value);
    }

    function clearResult(text) {
      result.textContent = "";
      conds.textContent = "";
      convention.textContent = "";
      note.textContent = text || "";
      note.className = "tf-note";
      resultFor = null;
    }

    function payload() {
      var t = target();
      var p = { path: t.path, transform: kind.value, var: varSel.value || null, new: newVar.value.trim() || spec().new };
      if (t.children) p.children = t.children;
      if (spec().extra) p.extra = extra.value.trim();
      return p;
    }

    function showError(e) {
      result.textContent = "";
      conds.textContent = "";
      convention.textContent = "";
      note.className = "tf-note tf-error";
      note.textContent = String(e && e.message || e).replace(/^\w*Error: /, "");
    }

    function show(res) {
      note.className = "tf-note";
      note.textContent = res.label + (/transform$/.test(res.label) ? "" : " transform") + ", " + res["var"] + " → " + res["new"] + ":";
      result.textContent = res.src;
      result.setAttribute("data-src", res.src);
      api.katex().then(function (katex) {
        if (result.getAttribute("data-src") !== res.src) return;
        try { katex.render(res.latex, result, { throwOnError: false, displayMode: true }); } catch (e) { result.textContent = res.src; }
      }, function () {});
      conds.textContent = "";
      res.conditions.forEach(function (c) { conds.appendChild(h("li", {}, [c])); });
      convention.textContent = res.convention || "";
    }

    compute.addEventListener("click", function () {
      if (api.busy()) return;
      var p = payload();
      var my = ++seq;
      clearResult("Computing…");
      resultFor = keyOf(target()) + "|" + shownSrc;
      api.call("compute", p).then(function (res) {
        if (my !== seq) return;
        show(res);
      }, function (e) { if (my === seq) showError(e); });
    });

    apply.addEventListener("click", function () {
      if (api.busy()) return;
      var p = payload();
      if (api.editor && api.editor.lazy && api.editor.lazy()) p.lazy = true;
      var my = ++seq;
      clearResult("Applying…");
      api.call("apply", p).then(function (snap) {
        if (my !== seq) return;
        note.textContent = snap && snap.note ? snap.note : "Applied.";
      }, function (e) { if (my === seq) showError(e); });
    });

    kind.addEventListener("change", function () {
      fillDefaults();
      clearResult("");
    });
    varSel.addEventListener("change", function () { chosenVar = varSel.value; clearResult(""); });
    newVar.addEventListener("input", function () { typedNew = !!newVar.value.trim(); });
    extra.addEventListener("input", function () { typedExtra = !!extra.value.trim(); });
    [newVar, extra].forEach(function (field) {
      field.addEventListener("keydown", function (ev) {
        ev.stopPropagation();                       // the editor's keys are not this field's
        if (ev.key === "Enter") { ev.preventDefault(); compute.click(); }
      });
    });

    var HELP = "<section><h3>Integral transforms</h3><ul>"
      + "<li>Select a piece of the formula (nothing selected: the whole of it), pick the <b>transform</b>, the variable it is <b>of</b> - "
      + "one of the selection's symbols, the usual one picked for you (t for Laplace, s for its inverse, n for the z-transform) - "
      + "and the name of the <b>new variable</b>.</li>"
      + "<li><b>Compute</b> shows the result and where it holds, in words: <i>converges for Re(s) &gt; −a</i>, <i>|z| &gt; 1/2</i>, "
      + "the conditions SymPy attaches.  The formula is not changed.</li>"
      + "<li><b>Apply</b> replaces the selection by its transform - one step of the history, which Undo takes back.  "
      + "With the editor's <b>unevaluated</b> toggle on it puts the transform itself in, not computed: "
      + "<code>LaplaceTransform(f, t, s)</code>, and for the z-transform the sum <code>Sum(f*z**(-n), (n, 0, oo))</code>; "
      + "<i>Evaluate (doit)</i> computes it later.</li>"
      + "<li>Laplace (one-sided), Fourier, sine, cosine, Mellin and Hankel transforms and their inverses are SymPy's, with SymPy's "
      + "conventions (Fourier: e<sup>−2πixk</sup>; the convention is said under each result).  Hankel asks for the order ν, the inverse "
      + "Mellin transform for its strip (a, b).</li>"
      + "<li>The <b>z-transform</b> is one-sided: Σ<sub>n≥0</sub> f(n) z<sup>−n</sup>, found term by term for powers, exponentials, "
      + "sines and cosines of n times powers of n, and Kronecker deltas, otherwise by SymPy's summation.  Its inverse takes a rational "
      + "function of z, by partial fractions, and gives the sequence for n ≥ 0.</li>"
      + "<li>The same transforms are in the <b>Transform</b> menu - Laplace transform…, Inverse Laplace transform…, Fourier transform…, "
      + "Inverse Fourier transform…, z-transform…, Inverse z-transform… - which ask for the two variables and leave the conditions in "
      + "the status line.</li>"
      + "<li>When SymPy cannot find a transform, the panel says so and nothing changes.</li>"
      + "</ul></section>";

    fillVars();
    fillDefaults();

    return {
      element: element,
      title: "Transforms",
      help: HELP,
      onState: function (snap) {
        if (snap.preview) return;
        var t = target();
        var node = api.node(t.path) || {};
        if (keyOf(t) !== shownKey || (node.src || null) !== shownSrc) {
          fillVars();
          if (resultFor) clearResult("");
        }
      },
      onSelect: function () {
        // local work only (the node table): nothing is asked of Python, and
        // nothing at all for the target already shown
        var t = target();
        if (keyOf(t) === shownKey && ((api.node(t.path) || {}).src || null) === shownSrc) return;
        fillVars();
        clearResult("");
      },
      destroy: function () { seq++; }
    };
  }
});
