/*
 * sympy-editor add-on "numeric": the Values panel.
 *
 * The selection - the whole formula when nothing is selected - evaluated
 * as a number.  Python does the arithmetic (methods "evaluate" and
 * "table", both queries: nothing here changes the formula); this keeps a
 * field per free symbol, the precision, the table's range, and shows what
 * comes back.  Plain script, no library: KaTeX (the editor's own) draws an
 * exact form when it is there.
 */
SympyEditor.registerAddon("numeric", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options || {};
    var DELAY = 250;            // ms of quiet typing before Python is asked

    function button(label, title, cls) {
      return h("button", { type: "button", class: "num-btn" + (cls ? " " + cls : ""), title: title }, [label]);
    }
    function field(value, title, cls) {
      return h("input", { type: "text", class: "num-in" + (cls ? " " + cls : ""), value: value, title: title });
    }

    var valueBtn = button("Value", "One value: every symbol given a number", "num-mode-value");
    var tableBtn = button("Table", "A table: one symbol over a range, the others fixed", "num-mode-table");
    var digitsSel = h("select", { class: "num-digits", title: "Significant digits of the result (evalf)" });
    (opts.digits || [15, 30, 50, 100]).forEach(function (d) { digitsSel.appendChild(h("option", { value: String(d) }, [String(d)])); });
    var guess = h("input", { type: "checkbox", class: "num-guess" });
    var follow = h("input", { type: "checkbox", class: "num-followbox", checked: "" });
    var bar = h("div", { class: "num-bar" }, [
      h("span", { class: "num-modes", role: "group", "aria-label": "Mode" }, [valueBtn, tableBtn]),
      h("label", {}, ["digits ", digitsSel]),
      h("label", { title: "When SymPy has no exact form of its own, ask nsimplify to recognise the number (pi, e, roots, fractions)" }, [guess, " guess an exact form"])
    ]);
    var followRow = h("div", { class: "num-follow" }, [
      h("label", { title: "Evaluate the selected piece of the formula; unticked, the whole expression" }, [follow, " follow the selection"])
    ]);
    var targetLine = h("div", { class: "num-target" });
    var fields = h("div", { class: "num-fields" });

    // The table's range: from/to/step, or a list of values.
    var varSel = h("select", { class: "num-var", title: "The symbol that varies down the table" });
    var start = field("0", "The first value", "num-start");
    var stop = field("1", "The last value (reached when the steps land on it)", "num-stop");
    var step = field("0.1", "The step: negative to count down", "num-step");
    var list = field("", "Or the values themselves, separated by commas (0, pi/6, pi/4): the range is then left aside", "num-list");
    list.setAttribute("placeholder", "or a list: 0, pi/6, pi/4");
    var tableBar = h("div", { class: "num-tablebar", hidden: "" }, [
      h("label", {}, ["vary ", varSel]),
      h("label", {}, ["from ", start]), h("label", {}, ["to ", stop]), h("label", {}, ["step ", step]),
      list
    ]);

    var valueText = h("span", { class: "num-value" });
    var exactText = h("span", { class: "num-exact" });
    var copyValue = button("Copy", "Copy the value", "num-copy-value");
    var result = h("div", { class: "num-result", hidden: "" }, [valueText, exactText, copyValue]);

    var table = h("table", { class: "num-table" });
    var copyCsv = button("Copy CSV", "Copy the table, comma-separated", "num-copy-csv");
    var copyTsv = button("Copy TSV", "Copy the table, tab-separated (pastes into a spreadsheet's cells)", "num-copy-tsv");
    var saveCsv = button("Save CSV", "Save the table as a .csv file", "num-save-csv");
    var tableBox = h("div", { class: "num-tablebox", hidden: "" }, [
      h("div", { class: "num-scroll" }, [table]),
      h("div", { class: "num-tools" }, [copyCsv, copyTsv, saveCsv])
    ]);
    var note = h("div", { class: "num-note" });
    var element = h("div", { class: "num-panel" }, [bar, followRow, targetLine, fields, tableBar, result, tableBox, note]);

    // The values typed for the symbols, by name, as typed: they outlive a
    // selection that lacks the symbol, so going back finds them again.  No
    // prototype: a name is any text.
    var values = Object.create(null);
    var mode = "value";
    var lastRows = null;        // the table on show: {var, rows}
    var lastValue = "";         // the value on show, for Copy
    var seq = 0, timer = null, gone = false, box = null, stale = false;
    var inFlight = false, wanted = false;
    var askedKey = null;        // the target last asked about (onSelect's guard)

    function targetKey(t) { return t.path + (t.children ? ":" + t.children.join(",") : ""); }

    function target() {
      if (!follow.checked) return { path: "/" };
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }

    /** Folded panels ask nothing; what was skipped is asked for on opening. */
    function folded() {
      if (!box && element.closest) {
        box = element.closest("details");
        if (box) box.addEventListener("toggle", unfold);
      }
      return !!box && !box.open;
    }
    function unfold() {
      if (!box || !box.open || !stale) return;
      stale = false;
      request();
    }

    function request(delay) {
      if (gone) return;
      if (folded()) { stale = true; return; }
      clearTimeout(timer);
      timer = setTimeout(ask, delay === undefined ? DELAY : delay);
    }

    function ask() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      if (api.busy()) { request(); return; }          // after the edit in flight
      if (inFlight) { wanted = true; return; }         // one at a time; the latest is what is wanted
      inFlight = true;
      var my = ++seq;
      var t = target();
      askedKey = targetKey(t);
      var sent = Object.create(null);
      for (var name in values) sent[name] = values[name];
      var payload = { path: t.path, values: sent, digits: parseInt(digitsSel.value, 10) || 15, guess: guess.checked };
      if (t.children) payload.children = t.children;
      var method = mode === "table" ? "table" : "evaluate";
      if (method === "table") {
        payload["var"] = varSel.value || null;
        payload.start = start.value; payload.stop = stop.value; payload.step = step.value;
        if (list.value.trim()) payload.list = list.value;
      }
      api.call(method, payload).then(function (res) {
        settle();
        if (gone || my !== seq) return;
        if (folded()) { stale = true; return; }
        show(method, res);
      }, function (e) {
        settle();
        if (gone || my !== seq) return;
        if (folded()) { stale = true; return; }
        clearResult();
        note.className = "num-note num-error";
        note.textContent = String(e && e.message || e);
      });
    }

    function settle() {
      inFlight = false;
      if (wanted) { wanted = false; request(0); }
    }

    function clearResult() {
      result.hidden = true;
      tableBox.hidden = true;
      lastRows = null;
      lastValue = "";
    }

    /** The row of a symbol, by attribute: a name is any text, and no
     *  selector made of it could be trusted (\alpha, a quote). */
    function rowOf(name) {
      for (var i = 0; i < fields.children.length; i++) {
        if (fields.children[i].getAttribute("data-sym") === name) return fields.children[i];
      }
      return null;
    }

    /** A field per free symbol (but the one the table varies): new ones
     *  empty unless a value was typed before, gone ones removed. */
    function fillFields(res, skip) {
      var wantedNames = (res.free || []).filter(function (n) { return n !== skip; });
      wantedNames.forEach(function (name) {
        var row = rowOf(name);
        if (!row) {
          var input = field(name in values ? values[name] : "", "The value of " + name + ": a number, or anything that is one (pi/3, 1e-3, sqrt(2), 2 + I)", "num-value-in");
          input.setAttribute("placeholder", "value");
          input.addEventListener("input", function () {
            var text = input.value.trim();
            if (text) values[name] = input.value; else delete values[name];
            request();
          });
          row = h("label", { "data-sym": name }, [name + " = ", input, h("span", { class: "num-read" })]);
          fields.appendChild(row);
        }
        var unset = (res.needs || []).indexOf(name) >= 0;
        row.classList.toggle("num-unset", unset);
        // what a value that had to be read was read as: pi/3 = 1.0472...
        var read = res.values && res.values[name];
        var typed = (values[name] || "").trim();
        row.querySelector(".num-read").textContent = read && typed && read !== typed && isNaN(Number(typed)) ? "= " + read : "";
      });
      Array.prototype.slice.call(fields.children).forEach(function (row) {
        if (wantedNames.indexOf(row.getAttribute("data-sym")) < 0) fields.removeChild(row);
      });
    }

    function fillVars(res) {
      varSel.textContent = "";
      (res.free || []).forEach(function (name) {
        var o = h("option", { value: name }, [name]);
        if (name === res["var"]) o.selected = true;
        varSel.appendChild(o);
      });
      varSel.disabled = (res.free || []).length < 2;
    }

    var GOOD = { real: true, complex: true, boolean: true, relation: true, matrix: true };

    function show(method, res) {
      targetLine.textContent = "";
      targetLine.appendChild(document.createTextNode(method === "table" ? "Table of " : "Value of "));
      targetLine.appendChild(h("code", {}, [res.src]));
      note.className = "num-note";
      note.textContent = "";
      if (method === "table") fillVars(res);
      fillFields(res, method === "table" ? res["var"] : null);
      if (res.needs && res.needs.length) {
        clearResult();
        note.className = "num-note num-error";
        note.textContent = "Give a value to " + res.needs.join(", ") + (method === "table" ? " (" + res["var"] + " varies down the table)" : "") + ".";
        return;
      }
      if (method === "table") return showTable(res);
      tableBox.hidden = true;
      var r = res.result || {};
      result.hidden = false;
      lastValue = r.text || "";
      valueText.textContent = r.text || "";
      if (r.truth !== undefined) valueText.textContent += "   (" + (r.truth ? "True" : "False") + ")";
      valueText.className = "num-value" + (GOOD[r.kind] && !(r.reason && r.kind !== "complex") ? "" : " num-bad");
      valueText.setAttribute("data-kind", r.kind || "");
      showExact(res.exact);
      if (r.reason) note.textContent = (r.kind === "complex" ? "Not real: " : r.kind === "relation" || r.kind === "matrix" ? "" : "No value: ") + r.reason;
    }

    function showExact(exact) {
      exactText.textContent = "";
      if (!exact) return;
      exactText.title = exact.guessed ? "Recognised by nsimplify: a guess, checked to the digits shown" : "SymPy's exact value";
      exactText.appendChild(document.createTextNode(exact.guessed ? "≈ " : "= "));
      var math = h("span", { class: "num-exact-math" }, [exact.src]);
      exactText.appendChild(math);
      if (exact.guessed) exactText.appendChild(document.createTextNode(" (guessed)"));
      Promise.resolve(api.katex ? api.katex() : null).then(function (katex) {
        if (!katex || !math.parentNode) return;
        try { katex.render(exact.latex, math, { throwOnError: true }); }
        catch (e) { math.textContent = exact.src; }
      }, function () { /* the text stays */ });
    }

    function showTable(res) {
      result.hidden = true;
      tableBox.hidden = false;
      var rows = res.rows || [];
      lastRows = { "var": res["var"], rows: rows };
      var withNotes = rows.some(function (r) { return !!r.note; });
      table.textContent = "";
      var head = h("tr", {}, [h("th", {}, [res["var"]]), h("th", {}, ["value"])]);
      if (withNotes) head.appendChild(h("th", {}, ["why"]));
      table.appendChild(h("thead", {}, [head]));
      var body = h("tbody");
      rows.forEach(function (r) {
        var x = h("td", { class: "num-x" }, [r.x]);
        if (r.exact) x.title = r.exact;
        var v = h("td", { class: "num-y" + (r.kind === "real" || r.kind === "complex" || r.kind === "relation" || r.kind === "boolean" ? "" : " num-bad") }, [r.value]);
        var tr = h("tr", {}, [x, v]);
        if (withNotes) tr.appendChild(h("td", { class: "num-why" }, [r.note || ""]));
        body.appendChild(tr);
      });
      table.appendChild(body);
      var said = [];
      if (res.capped) said.push("Only the first " + rows.length + " of " + res.asked + " rows: a table stops at " + res.maxRows + ".");
      if (res.stopped === "time") said.push("Stopped after " + rows.length + " rows: evaluating took too long. Narrow the range or lower the digits.");
      note.textContent = said.join(" ");
    }

    /* ---- copying, through the clipboard the editor uses ----
     * The app's own when the page runs in one (a WebView's may refuse, iOS
     * asks each time), the browser's otherwise, a hidden text area last. */
    function copyText(text, what) {
      var done = function () { api.status("Copied " + what); };
      var app = window.SympyEditorApp;
      if (app && typeof app.copyText === "function") {
        try { app.copyText(text); done(); return; } catch (e) { /* the browser's then */ }
      }
      var fallback = function () {
        if (api.editor && typeof api.editor._fallbackCopy === "function") api.editor._fallbackCopy(text);
        else {
          var ta = h("textarea", { style: "position:fixed;opacity:0" });
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand("copy"); } catch (e) { /* nothing more to try */ }
          document.body.removeChild(ta);
        }
        done();
      };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
      else fallback();
    }

    /** The table as text: a header, a row per value, the reasons in a third
     *  column when there are any.  CSV quotes what needs it. */
    function tableText(sep) {
      if (!lastRows) return "";
      var withNotes = lastRows.rows.some(function (r) { return !!r.note; });
      var cell = function (s) {
        s = String(s === undefined || s === null ? "" : s);
        if (sep === "\t") return s.replace(/[\t\r\n]+/g, " ");
        return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      };
      var lines = [[lastRows["var"], "value"].concat(withNotes ? ["note"] : []).map(cell).join(sep)];
      lastRows.rows.forEach(function (r) {
        lines.push([r.x, r.value].concat(withNotes ? [r.note || ""] : []).map(cell).join(sep));
      });
      return lines.join("\n") + "\n";
    }

    copyCsv.addEventListener("click", function () { copyText(tableText(","), "the table as CSV (" + lastRows.rows.length + " rows)"); });
    copyTsv.addEventListener("click", function () { copyText(tableText("\t"), "the table as TSV (" + lastRows.rows.length + " rows)"); });
    saveCsv.addEventListener("click", function () {
      var name = "table-" + String(lastRows["var"]).replace(/[^A-Za-z0-9_]+/g, "_") + ".csv";
      Promise.resolve(api.saveFile(name, "text/csv", tableText(","))).then(null, function (e) { api.error(String(e && e.message || e)); });
    });
    copyValue.addEventListener("click", function () { if (lastValue) copyText(lastValue, lastValue); });

    function setMode(m) {
      mode = m;
      valueBtn.setAttribute("aria-pressed", String(m === "value"));
      tableBtn.setAttribute("aria-pressed", String(m === "table"));
      tableBar.hidden = m !== "table";
      clearResult();
      request(0);
    }
    valueBtn.addEventListener("click", function () { if (mode !== "value") setMode("value"); });
    tableBtn.addEventListener("click", function () { if (mode !== "table") setMode("table"); });
    valueBtn.setAttribute("aria-pressed", "true");
    tableBtn.setAttribute("aria-pressed", "false");

    [digitsSel, guess, follow, varSel].forEach(function (el) { el.addEventListener("change", function () { request(0); }); });
    [start, stop, step, list].forEach(function (el) { el.addEventListener("input", function () { request(); }); });

    var HELP = [
      "<section><h3>What it shows</h3><ul>",
      "<li>The value of the selected piece of the formula — the whole expression when nothing is selected, or when <i>follow the selection</i> is off. Nothing here changes the formula.</li>",
      "<li>Every free symbol gets a field. A value is a number or anything that is one, read the way the formula's own text is: <code>pi/3</code>, <code>1e-3</code>, <code>sqrt(2)</code>, <code>2 + I</code>. No value is guessed: until each symbol has one, the panel says which are missing. Typed values are kept by name, so they are there again when another piece uses the same symbol.</li>",
      "<li><b>digits</b>: the significant digits of the result (15, 30, 50 or 100), computed by SymPy's <code>evalf</code> to that precision, not rounded from a float.</li>",
      "<li>Beside the number, its <b>exact form</b> when SymPy has a short one (<code>sin(pi/3)</code> gives <code>sqrt(3)/2</code>). With <b>guess an exact form</b> ticked, <code>nsimplify</code> is asked to recognise the number when SymPy has none; a guess is marked ≈ and checked to the digits shown, but it is a guess.</li>",
      "<li>A complex value reads <code>a + b i</code>. When the inputs are real and the value is not, the panel says which piece left the real numbers (a square root of a negative number, <code>asin(2)</code>).</li>",
      "<li>A value that is not a number says why: a <b>division by zero</b> (<code>1/x</code> at 0), an argument <b>outside a function's domain</b> (<code>log(0)</code>, <code>tan(pi/2)</code>), an <b>indeterminate form</b> such as 0/0. The piece named is the innermost one that goes wrong.</li>",
      "<li>An equation or inequality shows both sides and whether it holds; a matrix, each entry.</li>",
      "</ul></section>",
      "<section><h3>Table</h3><ul>",
      "<li><b>Table</b> lists the value as one symbol (<b>vary</b>) goes <b>from</b> … <b>to</b> … by <b>step</b> — or over the values typed in the list field (<code>0, pi/6, pi/4</code>), which then takes the range's place. The other symbols keep the values in their fields.</li>",
      "<li>A table has at most " + (opts.maxRows || 500) + " rows, and stops after a few seconds of evaluating: the rows done by then are shown, and the panel says it stopped.</li>",
      "<li><b>Copy CSV</b> and <b>Copy TSV</b> put the table on the clipboard (TSV pastes into a spreadsheet's cells), <b>Save CSV</b> saves it as a file. A third column holds the reasons when some rows have no value.</li>",
      "</ul></section>",
      "<section><h3>Good to know</h3><ul>",
      "<li>The panel follows every committed change and the selection, and a folded panel asks for nothing until it is opened.</li>",
      "<li>A single value is not cut short: something huge (<code>factorial(10**7)</code>) takes as long as it takes — the editor's <b>Interrupt</b> button stops it.</li>",
      "<li>Two different symbols of the same name (an <code>x</code>, and an <code>x</code> declared real) cannot be told apart by name: give one of them another name.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Values",
      help: HELP,
      onState: function (snap) { if (!snap.preview) request(); },
      // Only for another target: the same selection drawn again (a relayout,
      // the overlay going away) asks nothing - asking on every redraw made
      // each answer bring the next question.
      onSelect: function () { if (follow.checked && targetKey(target()) !== askedKey) request(); },
      destroy: function () {
        gone = true;
        clearTimeout(timer);
        seq++;
        wanted = false; stale = false;
        if (box) box.removeEventListener("toggle", unfold);
      }
    };
  }
});
