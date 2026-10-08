/*
 * sympy-editor add-on "series": the expansion of the selection under the
 * formula.
 *
 * Python expands (method "expand", a query; "insert", a change); this asks
 * and shows.  The panel follows the selection - the whole formula when
 * nothing is selected - and asks again only when what it would ask changes:
 * another target, another variable, point, order, direction or kind.
 */
SympyEditor.registerAddon("series", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options || {};
    var maxOrder = opts.maxOrder || 20;

    function field(value, title, cls) {
      return h("input", { type: "text", class: "ser-field " + (cls || ""), value: value, title: title,
                          autocapitalize: "off", autocorrect: "off", autocomplete: "off", spellcheck: "false" });
    }
    function button(label, title, cls) {
      return h("button", { type: "button", class: "ser-btn " + (cls || ""), title: title }, [label]);
    }
    function option(value, label) { return h("option", { value: value }, [label]); }

    var kindSel = h("select", { class: "ser-kind", title: "What to compute" }, [
      option("series", "Series (Taylor, Laurent, Puiseux)"),
      option("asymptotic", "Asymptotic (x → ±∞)"),
      option("leading", "Leading term")
    ]);
    var varSel = h("select", { class: "ser-var", title: "The variable to expand in (a free symbol of the selection)" });
    var point = field("0", "The point: any expression - 0, 1, pi/2, a, oo, -oo", "ser-point");
    var dirSel = h("select", { class: "ser-dir", title: "The side the point is approached from" }, [
      option("+", "from above (+)"), option("-", "from below (−)"), option("both", "both sides")
    ]);
    var dirLabel = h("label", { class: "ser-dir-label", hidden: "" }, ["direction ", dirSel]);
    var order = h("input", { type: "range", class: "ser-order", min: "1", max: String(maxOrder), step: "1",
                             value: String(opts.order || 6), title: "The order: the power of the O term" });
    var orderOut = h("output", { class: "ser-order-out" }, [String(opts.order || 6)]);
    var minus = button("−", "One order less", "ser-step ser-less");
    var plus = button("+", "One order more", "ser-step ser-more");
    var orderRow = h("div", { class: "ser-row ser-order-row" }, [h("span", {}, ["order "]), minus, order, plus, orderOut]);
    var follow = h("input", { type: "checkbox", checked: "", class: "ser-follow" });
    var bare = h("input", { type: "checkbox", class: "ser-bare" });
    var at = field("", "A point near the expansion point at which to compare the function with the truncated expansion (empty: chosen for you)", "ser-at");
    var atVar = h("span", { class: "ser-at-var" }, ["x"]);

    var heading = h("div", { class: "ser-heading" });
    var result = h("div", { class: "ser-result", "aria-live": "polite" });
    var otherSide = h("div", { class: "ser-other", hidden: "" });
    var note = h("div", { class: "ser-note" });
    var check = h("div", { class: "ser-check" });
    var insert = button("Insert", "Put the expansion, with its O term, in place of the selection (one step: Undo takes it back)", "ser-insert");
    var insertBare = button("Insert without O", "Put the expansion without its O term in place of the selection", "ser-insert-bare");
    var showTerms = button("Show terms", "List the coefficients aₖ of the expansion", "ser-show-terms");
    var terms = h("div", { class: "ser-terms", hidden: "" });

    var element = h("div", { class: "ser-panel" }, [
      h("div", { class: "ser-row" }, [kindSel, h("label", {}, ["variable ", varSel]),
                                      h("label", { class: "ser-point-label" }, [h("span", { class: "ser-arrow" }, ["→ "]), point]),
                                      dirLabel]),
      orderRow,
      h("div", { class: "ser-row ser-checks" }, [
        h("label", { title: "Expand the selected piece; unticked, the whole formula" }, [follow, " follow the selection"]),
        h("label", { title: "Show the expansion without its O term" }, [bare, " without O"])
      ]),
      heading, result, otherSide, note,
      h("div", { class: "ser-row ser-check-row" }, [h("label", {}, ["error at ", atVar, " = ", at])]),
      check,
      h("div", { class: "ser-row ser-actions" }, [insert, insertBare, showTerms]),
      terms
    ]);

    var gone = false, timer = null, seq = 0, inFlight = false, wanted = false;
    var last = null;        // the answer on show
    var askedKey = null;    // what the answer on show (or the one on its way) was asked for
    var box = null, stale = false;
    var termsOpen = false;

    function targetOf() {
      if (!follow.checked) return { path: "/" };
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }
    function payloadOf() {
      var t = targetOf();
      var p = { path: t.path, var: varSel.value || null, point: point.value, n: parseInt(order.value, 10) || 6,
                dir: dirSel.value, kind: kindSel.value, at: at.value };
      if (t.children) p.children = t.children;
      return p;
    }
    function keyOf(p) { return JSON.stringify(p); }

    function folded() {
      if (!box && element.closest) {
        box = element.closest("details");
        if (box) box.addEventListener("toggle", unfold);
      }
      return !!box && !box.open;
    }
    function unfold() { if (box && box.open && stale) { stale = false; request(true); } }

    /** Ask again after a short pause.  `force`: even for the question already
     *  answered (the formula changed under it). */
    function request(force) {
      if (gone) return;
      if (force) askedKey = null;
      if (folded()) { stale = true; return; }
      clearTimeout(timer);
      timer = setTimeout(ask, 180);
    }

    function message(e) { return String(e && e.message || e).replace(/^[A-Za-z]*Error: /, ""); }

    function ask() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      if (api.busy()) { request(); return; }
      var p = payloadOf();
      var key = keyOf(p);
      if (key === askedKey) return;              // the answer on show is this one's
      if (inFlight) { wanted = true; return; }
      inFlight = true;
      askedKey = key;
      var my = ++seq;
      note.className = "ser-note ser-working";
      note.textContent = "Expanding…" + (opts.timeLimit ? " (SymPy is given " + opts.timeLimit + " s)" : "");
      api.call("expand", p).then(function (res) {
        done();
        if (gone || my !== seq) return;
        show(res);
        // the variable Python chose is now the picker's: the question on
        // show is the one the panel would ask now, not another one
        if (askedKey === key) askedKey = keyOf(payloadOf());
      }, function (e) {
        done();
        if (gone || my !== seq) return;
        fail(message(e));
      });
    }
    function done() {
      inFlight = false;
      if (wanted) { wanted = false; request(); }
    }

    function math(el, tex, text) {
      el.textContent = text;
      api.katex().then(function (k) {
        try { el.textContent = ""; k.render(tex, el, { throwOnError: false, displayMode: false }); }
        catch (e) { el.textContent = text; }
      }, function () { el.textContent = text; });
    }

    function fillVars(res) {
      var names = res.free || [];
      var same = varSel.options.length === names.length;
      for (var i = 0; same && i < names.length; i++) same = varSel.options[i].value === names[i];
      if (!same) {
        varSel.textContent = "";
        names.forEach(function (n) { varSel.appendChild(option(n, n)); });
      }
      varSel.value = res.var;
      atVar.textContent = res.var;
    }

    function fail(text) {
      last = null;
      heading.textContent = "";
      result.textContent = "";
      otherSide.hidden = true;
      check.textContent = "";
      terms.textContent = "";
      terms.hidden = true;
      note.className = "ser-note ser-error";
      note.textContent = text;
      update();
    }

    var LABELS = { "Taylor": "Taylor series", "Laurent": "Laurent series", "Puiseux": "Puiseux series",
                   "with logarithms": "Series with logarithms", "generalized": "Generalized series",
                   "asymptotic": "Asymptotic expansion", "leading term": "Leading term", "no expansion": "No expansion",
                   "incomplete": "Partial expansion" };

    function show(res) {
      last = res;
      fillVars(res);
      var where = res.var + (res.infinite ? " → " : " = ") + res.point;
      heading.textContent = (LABELS[res.label] || res.label) + " of " + res.src + " at " + where
        + (res.kind === "leading" ? "" : ", order " + res.n);
      var withO = !bare.checked || !res.has_o;
      math(result, withO ? res.latex : res.latex_plain, withO ? res.text : res.text_plain);
      if (res.other) {
        otherSide.hidden = false;
        otherSide.textContent = "";
        var span = h("span", { class: "ser-other-math" });
        otherSide.appendChild(h("span", {}, ["from below: "]));
        otherSide.appendChild(span);
        math(span, res.other.latex, res.other.text);
      } else {
        otherSide.hidden = true;
      }
      var notes = [];
      if (res.note) notes.push(res.note);
      if (res.kind === "leading") notes.push("Coefficient " + res.coeff + ", exponent " + res.exponent + ".");
      if (res.dir === "both" && res.dir_matters && !res.other) notes.push("The two sides differ.");
      if (res.dir === "both" && !res.dir_matters && !res.infinite) notes.push("Both sides give the same expansion.");
      note.className = "ser-note" + (res.label === "no expansion" || res.label === "incomplete" ? " ser-error" : "");
      note.textContent = notes.join(" ");
      dirLabel.hidden = !!res.infinite || !(res.dir_matters || dirSel.value !== "+");
      check.textContent = "";
      check.className = "ser-check";
      if (res.check) {
        var c = res.check;
        check.textContent = "At " + res.var + " = " + c.at + ": the function is " + c.exact + ", the expansion "
          + c.approx + " - error " + c.error_text + (c.relative_text ? " (relative " + c.relative_text + ")" : "")
          + (c.o_size ? "; the O term's size there is " + c.o_size : "") + ".";
      } else if (res.check_error) {
        check.className = "ser-check ser-error";
        check.textContent = res.check_error;
      } else if (!res.infinite) {
        check.textContent = "Give a sample point to measure the truncation error.";
      }
      drawTerms();
      update();
    }

    function drawTerms() {
      terms.textContent = "";
      terms.hidden = !termsOpen || !last;
      showTerms.textContent = termsOpen ? "Hide terms" : "Show terms";
      if (!termsOpen || !last) return;
      if (last.label === "no expansion" || !last.terms.length) {
        terms.appendChild(h("div", { class: "ser-note" }, ["No coefficients to list."]));
        return;
      }
      var head = h("div", { class: "ser-terms-head" });
      math(head, "a_k \\text{ of } " + (last.basis_latex === last.var || last.basis_latex.length < 2 ? last.basis_latex : "\\left(" + last.basis_latex + "\\right)") + "^k",
           "a_k of (" + last.basis_latex + ")^k");
      terms.appendChild(head);
      var table = h("table", { class: "ser-table" });
      table.appendChild(h("tr", {}, [h("th", {}, ["k"]), h("th", {}, ["aₖ"])]));
      last.terms.forEach(function (t) {
        var k = h("td", { class: "ser-k" }), c = h("td", { class: "ser-a" });
        math(k, t.k_latex, t.k);
        math(c, t.coeff_latex, t.coeff);
        table.appendChild(h("tr", {}, [k, c]));
      });
      terms.appendChild(table);
      if (last.logs) terms.appendChild(h("div", { class: "ser-note" }, ["A coefficient that still holds " + last.var + " is a logarithm, or what SymPy left unexpanded."]));
      if (last.more_terms) terms.appendChild(h("div", { class: "ser-note" }, ["… and " + last.more_terms + " more."]));
    }

    function update() {
      var ok = !!last && last.label !== "no expansion";
      insert.disabled = !ok;
      insertBare.hidden = !ok || !last.has_o;
      insertBare.disabled = !ok;
      insert.textContent = last && !last.has_o ? "Insert" : "Insert with O";
      showTerms.disabled = !last;
      orderRow.hidden = kindSel.value === "leading";
      orderOut.textContent = order.value;
      minus.disabled = parseInt(order.value, 10) <= 1;
      plus.disabled = parseInt(order.value, 10) >= maxOrder;
    }

    function doInsert(withO) {
      if (!last) return;
      var p = payloadOf();
      p.with_o = withO;
      api.call("insert", p).then(function () {}, function (e) { fail(message(e)); });
    }

    function stepOrder(d) {
      var v = Math.max(1, Math.min(maxOrder, (parseInt(order.value, 10) || 6) + d));
      order.value = String(v);
      update();
      request();
    }

    kindSel.addEventListener("change", function () {
      var v = point.value.replace(/\s+/g, "");
      if (kindSel.value === "asymptotic" && v !== "oo" && v !== "-oo" && v !== "∞" && v !== "-∞") point.value = "oo";
      update();
      request();
    });
    varSel.addEventListener("change", function () { atVar.textContent = varSel.value; request(); });
    point.addEventListener("change", function () { request(); });
    point.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); request(); } });
    at.addEventListener("change", function () { request(); });
    at.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); request(); } });
    dirSel.addEventListener("change", function () { request(); });
    order.addEventListener("input", function () { update(); request(); });
    minus.addEventListener("click", function () { stepOrder(-1); });
    plus.addEventListener("click", function () { stepOrder(1); });
    follow.addEventListener("change", function () { request(); });
    bare.addEventListener("change", function () { if (last) show(last); });
    insert.addEventListener("click", function () { doInsert(true); });
    insertBare.addEventListener("click", function () { doInsert(false); });
    showTerms.addEventListener("click", function () { termsOpen = !termsOpen; drawTerms(); });
    update();

    var HELP = [
      "<section><h3>What it computes</h3><ul>",
      "<li>The expansion of the selected piece of the formula — the whole formula when nothing is selected, or when <i>follow the selection</i> is off — in a <b>variable</b> (one of its free symbols) about a <b>point</b>: any expression, <code>0</code>, <code>pi/2</code>, <code>a</code>, or <code>oo</code> / <code>-oo</code> for infinity.</li>",
      "<li><b>Series</b>: what SymPy's <code>series</code> gives, named after its powers — <i>Taylor</i> (whole powers from 0), <i>Laurent</i> (negative powers too, at a pole), <i>Puiseux</i> (fractional powers, at a branch point such as <code>sqrt(x)</code> at 0), or with logarithms.</li>",
      "<li><b>Asymptotic</b>: the expansion at <code>oo</code> or <code>-oo</code>, in falling powers of the variable.</li>",
      "<li><b>Leading term</b>: the first term only (<code>as_leading_term</code>), with its coefficient and exponent.</li>",
      "</ul></section>",
      "<section><h3>Controls</h3><ul>",
      "<li><b>order</b>: the slider, or − and +, from 1 to " + maxOrder + " — the power of the O term.</li>",
      "<li><b>direction</b> appears only where it matters: when the expansion from above and from below differ (<code>sqrt(x**2)</code>, <code>log(x)</code> at 0). <i>both sides</i> shows the two.</li>",
      "<li><b>without O</b> shows the truncated polynomial; <b>error at</b> compares the function with it at a point near the expansion point (a tenth away, or 10 at infinity, unless you type one) and gives the difference beside the size of the O term there.</li>",
      "<li><b>Insert with O</b> / <b>Insert without O</b> put the expansion in place of the selection — one step of the history, which Undo takes back.</li>",
      "<li><b>Show terms</b> lists the coefficients a<sub>k</sub> of (x − a)<sup>k</sup> (of x<sup>k</sup> at infinity).</li>",
      "</ul></section>",
      "<section><h3>When there is no answer</h3><ul>",
      "<li>A series can take for ever: SymPy is given " + (opts.timeLimit || 8) + " seconds, and past that the panel says it gave up — try a lower order or another point.</li>",
      "<li>At a singularity SymPy cannot expand (an essential singularity, such as <code>exp(1/x)</code> at 0, or <code>exp(x)</code> at infinity) the panel says so in words instead of showing a result.</li>",
      "<li>A folded panel asks for nothing; it catches up when it is opened.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Series",
      help: HELP,
      onState: function (snap) { if (!snap.preview) request(true); },
      // Only for another target: the same selection drawn again (a relayout,
      // the overlay going away) must not ask Python again.
      onSelect: function () { if (follow.checked && keyOf(payloadOf()) !== askedKey) request(); },
      destroy: function () {
        gone = true;
        clearTimeout(timer);
        seq++;
        if (box) box.removeEventListener("toggle", unfold);
      }
    };
  }
});
