/*
 * The browser part of the assumptions add-on: what SymPy knows about the
 * selection (a query, "facts"), the symbols' own assumptions as switches
 * (a change, "assume", through the document's retype - undoable), and the
 * simplification a switch made possible (a change, "simplify").
 * A plain script, run once per page with `SympyEditor` in scope.
 */
SympyEditor.registerAddon("assumptions", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options || {};
    var LABELS = { "true": "true", "false": "false", "null": "unknown" };

    var subject = h("div", { class: "as-subject" });
    var grid = h("div", { class: "as-grid", role: "list" });
    var why = h("p", { class: "as-why", "aria-live": "polite" });
    var symbols = h("div", { class: "as-symbols" });
    var hint = h("div", { class: "as-hint", role: "status" });
    var note = h("p", { class: "as-note" });
    hint.hidden = true;
    why.hidden = true;
    var element = h("div", { class: "as-panel" }, [
      hint,
      subject, grid, why,
      h("div", { class: "as-heading" }, ["Symbols"]),
      symbols, note
    ]);

    var gone = false, box = null, stale = false, timer = null, inFlight = false, wanted = false;
    var askedKey = null, seq = 0, openWhy = null, katexLib = null;
    api.katex().then(function (k) { katexLib = k; }, function () {});

    function tex(latex, fallback) {
      var span = h("span", { class: "as-tex" });
      if (katexLib && latex) {
        try { span.innerHTML = katexLib.renderToString(latex, { throwOnError: false }); return span; } catch (e) {}
      }
      span.textContent = fallback;
      return span;
    }

    // The node the table speaks of: the range, the selection, or the whole formula.
    function target() {
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }
    function keyOf(t) { return t.path + (t.children ? ":" + t.children.join(",") : ""); }

    /** A folded panel asks nothing; what was skipped is asked when it opens. */
    function folded() {
      if (!box && element.closest) {
        box = element.closest("details");
        if (box) box.addEventListener("toggle", unfold);
      }
      return !!box && !box.open;
    }
    function unfold() { if (box && box.open && stale) { stale = false; request(true); } }

    function request(force) {
      if (gone) return;
      if (force) askedKey = null;
      if (folded()) { stale = true; return; }
      clearTimeout(timer);
      timer = setTimeout(ask, 120);
    }

    function ask() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      if (api.busy()) { request(); return; }
      var t = target();
      var key = keyOf(t) + "#" + ((api.state() || {}).seq);
      if (key === askedKey) return;
      if (inFlight) { wanted = true; return; }
      inFlight = true;
      askedKey = key;
      var my = ++seq;
      var payload = { path: t.path };
      if (t.children) payload.children = t.children;
      api.call("facts", payload).then(function (res) {
        if (!gone && my === seq) drawFacts(res);
      }, function (e) {
        if (!gone && my === seq) {
          grid.innerHTML = "";
          note.textContent = String(e && e.message || e);
          note.className = "as-note as-error";
        }
      }).then(function () {
        inFlight = false;
        if (wanted) { wanted = false; request(); }
      });
    }

    function drawFacts(res) {
      subject.innerHTML = "";
      subject.appendChild(h("span", { class: "as-label" }, [api.selected() || api.range() ? "Selection: " : "Whole formula: "]));
      subject.appendChild(tex(res.latex, res.src));
      grid.innerHTML = "";
      why.hidden = true;
      if (!res.applicable) {
        grid.appendChild(h("p", { class: "as-na" }, [res.reason || "Not a number."]));
        return;
      }
      res.rows.forEach(function (row) {
        var v = String(row.value);
        var cell = h("button", {
          type: "button", class: "as-fact as-" + v, role: "listitem", "data-pred": row.name,
          title: row.value === null ? "unknown: tap for why" :
                 row.name + ": " + v + (row.source === "ask" ? " (by ask(Q." + row.name + "(...)))" : " (by the assumptions, .is_" + row.name + ")")
        }, [
          h("span", { class: "as-pred" }, [row.name]),
          h("span", { class: "as-val" }, [LABELS[v]]),
        ]);
        if (row.source === "ask") cell.appendChild(h("span", { class: "as-src" }, ["ask"]));
        cell.addEventListener("click", function () { explain(row, cell); });
        grid.appendChild(cell);
        if (openWhy === row.name && row.value === null) explain(row, cell, true);
      });
    }

    function explain(row, cell, keep) {
      var cells = grid.querySelectorAll(".as-fact");
      for (var i = 0; i < cells.length; i++) cells[i].classList.remove("as-open");
      if (!keep && openWhy === row.name && !why.hidden) { why.hidden = true; openWhy = null; return; }
      openWhy = row.name;
      cell.classList.add("as-open");
      why.innerHTML = "";
      if (row.value === null) {
        why.appendChild(document.createTextNode(row.why || ("SymPy cannot tell whether this is " + row.name + ".")));
        (row.would || []).forEach(function (w) {
          if (w.names.length !== 1 || opts.editable.indexOf(w.assumption) < 0) return;
          var b = h("button", { type: "button", class: "as-btn as-suggest", "data-name": w.names[0], "data-pred": w.assumption,
                                title: "Assume " + w.names[0] + " " + w.assumption + " everywhere (one step of the history)" },
                    ["Assume " + w.names[0] + " " + w.assumption]);
          b.addEventListener("click", function () { assume(w.names[0], w.assumption, true); });
          why.appendChild(document.createTextNode(" "));
          why.appendChild(b);
        });
      } else {
        why.textContent = row.name + " is " + row.value + ": " + (row.source === "ask"
          ? "the old assumptions (.is_" + row.name + ") cannot tell, but ask(Q." + row.name + "(...)) can."
          : "SymPy's assumptions (.is_" + row.name + ") say so.");
      }
      why.hidden = false;
    }

    function assume(name, pred, value) {
      note.textContent = "";
      note.className = "as-note";
      api.call("assume", { name: name, assumption: pred, value: value }).then(function () {}, function (e) {
        note.textContent = String(e && e.message || e).replace(/^ValueError: /, "");
        note.className = "as-note as-error";
      });
    }

    // given true -> given false -> nothing -> given true
    function next(given) {
      if (given === true) return false;
      if (given === false) return null;
      return true;
    }

    function drawSymbols(rows) {
      symbols.innerHTML = "";
      if (!rows || !rows.length) {
        symbols.appendChild(h("p", { class: "as-na" }, ["No symbols in the formula."]));
        return;
      }
      rows.forEach(function (row) {
        var line = h("div", { class: "as-sym", "data-name": row.name }, [h("code", { class: "as-name" }, [row.name])]);
        if (row.clash) {
          line.appendChild(h("span", { class: "as-na" }, [" two different symbols are named " + row.name + " (with different assumptions): give one of them another name to switch its assumptions here."]));
          symbols.appendChild(line);
          return;
        }
        var chips = h("span", { class: "as-chips" });
        opts.editable.forEach(function (pred) {
          var g = row.given.hasOwnProperty(pred) ? row.given[pred] : null;
          var k = row.known.hasOwnProperty(pred) ? row.known[pred] : null;
          var cls = "as-chip" + (g === true ? " as-given-true" : g === false ? " as-given-false"
                    : k === true ? " as-implied-true" : k === false ? " as-implied-false" : "");
          var label = (g === false ? "not " : "") + pred;
          var state = g !== null ? "assumed " + (g ? "" : "not ") + pred
                    : k !== null ? (k ? "" : "not ") + pred + ", as follows from what is assumed" : "nothing assumed";
          var b = h("button", {
            type: "button", class: cls, "data-pred": pred, "aria-pressed": g === null ? "false" : "true",
            title: row.name + ": " + state + ". Tap: " + ({ "true": "assume " + pred, "false": "assume not " + pred, "null": "forget it" })[String(next(g))]
          }, [label]);
          b.addEventListener("click", function () { assume(row.name, pred, next(g)); });
          chips.appendChild(b);
        });
        line.appendChild(chips);
        symbols.appendChild(line);
      });
    }

    function drawHint(data) {
      hint.innerHTML = "";
      if (!data) { hint.hidden = true; return; }
      var what = data.name + (data.value === null ? " no longer " + data.assumption
                 : data.value ? " " + data.assumption : " not " + data.assumption);
      if (data.dropped && data.dropped.length) {
        hint.appendChild(h("p", {}, ["Assuming " + what + " replaced what contradicted it: " + data.dropped.join(", ") + " dropped."]));
      }
      if (data.rewrote) {
        var p = h("p", { class: "as-rewrote" }, ["With " + what + ", SymPy rewrote the formula on its own: "]);
        p.appendChild(tex(data.before_latex, data.before));
        p.appendChild(document.createTextNode(" became what is shown now."));
        hint.appendChild(p);
      }
      if (data.simplified) {
        var q = h("p", { class: "as-simpl" }, ["With " + what + ", simplify now gives "]);
        q.appendChild(tex(data.simplified_latex, data.simplified));
        q.appendChild(document.createTextNode(" (before, it gave " + data.was + ")."));
        var go = h("button", { type: "button", class: "as-btn as-apply", title: "Replace the formula by its simplified form (one step of the history)" }, ["Apply"]);
        go.addEventListener("click", function () {
          api.call("simplify", {}).then(function () {}, function (e) { note.textContent = String(e && e.message || e); note.className = "as-note as-error"; });
        });
        q.appendChild(document.createTextNode(" "));
        q.appendChild(go);
        hint.appendChild(q);
      }
      hint.hidden = !hint.firstChild;
    }

    var HELP = [
      "<section><h3>Assumptions</h3><ul>",
      "<li>The table says what SymPy knows about the <b>selection</b> (a node or a range) - or the whole formula when nothing is selected: real, complex, positive, integer, prime, finite, ... each <b>true</b>, <b>false</b> or <b>unknown</b>.</li>",
      "<li>SymPy's assumptions (<code>expr.is_positive</code>) answer first. Where they cannot tell, <code>ask(Q.positive(expr))</code> is tried as well, on an expression small enough for it; such an answer is marked <b>ask</b>.</li>",
      "<li><b>Tap a cell</b> for the reason. An unknown says what it depends on, and, when assuming something of one symbol would decide it, offers to assume it.</li>",
      "<li><b>Symbols</b>: every free symbol of the formula, with its assumptions as switches. A tap cycles: assumed (solid) - assumed not (struck through) - nothing. Faint ones follow from what is assumed (a positive symbol is real).</li>",
      "<li>A switch changes the symbol <b>everywhere</b> in the formula - one step of the history, so Undo gives the old symbol back. An assumption that contradicts the new one is dropped (a negative x made positive is no longer negative).</li>",
      "<li>After a switch the panel compares <code>simplify</code> before and after: when the formula changed by itself (<code>sqrt(x**2)</code> is <code>x</code> once x is positive) or a simplification became possible (<code>log(x) + log(y)</code> is <code>log(x*y)</code> for positive x and y), it says so, and <b>Apply</b> puts the simplified form in.</li>",
      "<li>A folded panel asks Python nothing.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Assumptions",
      help: HELP,
      onState: function (snap) {
        if (snap.preview) return;
        var data = snap.assumptions || {};
        drawSymbols(data.symbols);
        drawHint(data.hint);
        request();
      },
      onSelect: function () {
        var t = target();
        var key = keyOf(t) + "#" + ((api.state() || {}).seq);
        if (key !== askedKey) request();
      },
      destroy: function () {
        gone = true;
        clearTimeout(timer);
        seq++;
        if (box) box.removeEventListener("toggle", unfold);
      }
    };
  }
});
