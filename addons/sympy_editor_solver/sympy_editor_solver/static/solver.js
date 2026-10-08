/*
 * sympy-editor add-on "solver": solve the selected equation, inequality or
 * system, check a solution by substituting it back, insert it.
 *
 * Python reads and solves (methods "problem", "solve", "check", "insert");
 * this shows.  The panel follows the selection - a range of an And's
 * arguments included - and asks only how it reads (cheap); solving waits for
 * the Solve button, since SymPy may take its time.
 */
SympyEditor.registerAddon("solver", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options || {};
    var katex = null;

    var problemEl = h("div", { class: "sv-problem" });
    var unknownsEl = h("div", { class: "sv-unknowns", role: "group", "aria-label": "Unknowns" });
    var domainSel = h("select", { class: "sv-domain", title: "Where the solutions are looked for" });
    (opts.domains || []).forEach(function (d) {
      domainSel.appendChild(h("option", { value: d.key }, [d.sign + "  " + d.words]));
    });
    var solveBtn = h("button", { type: "button", class: "sv-btn sv-solve", title: "Solve for the ticked unknowns" }, ["Solve"]);
    var wholeBtn = h("button", { type: "button", class: "sv-btn sv-insert-all", hidden: "",
                                 title: "Put the whole solution set in place of what was solved (Undo takes it back)" }, ["Insert the set"]);
    var status = h("div", { class: "sv-status", role: "status", "aria-live": "polite" });
    var summary = h("div", { class: "sv-summary" });
    var list = h("ol", { class: "sv-list" });
    var element = h("div", { class: "sv-panel" }, [
      problemEl,
      h("div", { class: "sv-bar" }, [h("span", { class: "sv-label" }, ["Unknowns"]), unknownsEl]),
      h("div", { class: "sv-bar" }, [h("label", { class: "sv-label" }, ["Domain ", domainSel]), solveBtn, wholeBtn]),
      status, summary, list
    ]);

    var gone = false, timer = null, seq = 0;
    var problem = null;          // the last "problem" answer
    var ticked = null;           // the unknowns ticked, by name, for the problem on show
    var solved = null;           // the last "solve" answer: its token names it to Python
    var askedKey = null;         // what the problem on show was asked for

    function tex(el, src, plain) {
      el.textContent = "";
      if (katex) {
        try { katex.render(src, el, { throwOnError: true, displayMode: false }); return; } catch (e) { /* the text instead */ }
      }
      el.textContent = plain != null ? plain : src;
    }

    function say(text, kind) {
      status.textContent = text || "";
      status.className = "sv-status" + (kind ? " sv-" + kind : "");
    }

    /** What the panel works on: the range when several arguments are
     *  selected, else the selection, else the whole formula. */
    function target() {
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }
    function keyOf(t) {
      var st = api.state();
      return JSON.stringify([t.path, t.children || null, st ? st.seq : null]);
    }

    function clearSolution() {
      solved = null;
      summary.textContent = "";
      list.textContent = "";
      wholeBtn.hidden = true;
    }

    function ask() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (gone) return;
        if (api.busy()) { ask(); return; }
        var t = target();
        askedKey = keyOf(t);
        var my = ++seq;
        api.call("problem", t, { quiet: true }).then(function (res) {
          if (gone || my !== seq) return;
          showProblem(res);
        }, function () { /* a selection gone stale: the next one asks again */ });
      }, 120);
    }

    function showProblem(res) {
      var sameProblem = problem && res.ok && problem.ok && problem.src === res.src;
      problem = res;
      if (!sameProblem) clearSolution();
      problemEl.textContent = "";
      unknownsEl.textContent = "";
      if (!res.ok) {
        problemEl.appendChild(h("span", { class: "sv-none" }, [res.reason]));
        solveBtn.disabled = true;
        ticked = null;
        return;
      }
      var formula = h("span", { class: "sv-formula" });
      tex(formula, res.latex, res.src);
      problemEl.appendChild(h("span", { class: "sv-words" }, ["Solving " + res.words + ": "]));
      problemEl.appendChild(formula);
      var names = res.symbols.map(function (s) { return s.name; });
      if (!sameProblem || !ticked) ticked = res.defaults.slice();
      ticked = ticked.filter(function (n) { return names.indexOf(n) >= 0; });
      res.symbols.forEach(function (s) {
        var box = h("input", { type: "checkbox", value: s.name, "data-name": s.name });
        box.checked = ticked.indexOf(s.name) >= 0;
        box.addEventListener("change", function () {
          ticked = Array.prototype.filter.call(unknownsEl.querySelectorAll("input"), function (b) { return b.checked; })
            .map(function (b) { return b.value; });
          solveBtn.disabled = !ticked.length;
          clearSolution();
          say("");
        });
        var name = h("span", { class: "sv-sym" });
        tex(name, s.latex, s.name);
        unknownsEl.appendChild(h("label", { class: "sv-unknown", title: "Solve for " + s.name }, [box, name]));
      });
      solveBtn.disabled = !ticked.length;
    }

    function solve() {
      if (!problem || !problem.ok || !ticked || !ticked.length) return;
      var t = target();
      var payload = { path: t.path, unknowns: ticked.slice(), domain: domainSel.value };
      if (t.children) payload.children = t.children;
      clearSolution();
      solveBtn.disabled = true;
      say("Solving… SymPy is given " + (opts.timeout || 8) + " s.", "busy");
      api.call("solve", payload).then(function (res) {
        solveBtn.disabled = false;
        if (gone) return;
        if (res.timed_out) { say(res.message, "error"); return; }
        if (res.failed) { say(res.message, "error"); return; }
        say("");
        showSolution(res);
      }, function (e) {
        solveBtn.disabled = false;
        say(String(e && e.message || e), "error");
      });
    }

    function showSolution(res) {
      solved = res;
      summary.textContent = "";
      var head = h("div", { class: "sv-head" }, [
        h("strong", {}, [res.summary]), " ",
        h("span", { class: "sv-method", title: "The SymPy function that solved it, and the domain" },
          ["(" + res.method + " over " + res.domain + ")"])
      ]);
      summary.appendChild(head);
      if (res.items.length) {
        var set = h("div", { class: "sv-set", title: res.set_src });
        tex(set, res.set_latex, res.set_src);
        summary.appendChild(set);
      }
      res.notes.forEach(function (n) { summary.appendChild(h("div", { class: "sv-note" }, [n])); });
      list.textContent = "";
      res.items.forEach(function (item) {
        var formula = h("span", { class: "sv-formula", title: item.src });
        tex(formula, item.latex, item.src);
        var checkOut = h("div", { class: "sv-check", hidden: "" });
        var buttons = [];
        if (item.check) {
          var check = h("button", { type: "button", class: "sv-btn sv-check-btn",
                                    title: "Substitute this solution back into the equations and simplify" }, ["Substitute back"]);
          check.addEventListener("click", function () { runCheck(item, checkOut, check); });
          buttons.push(check);
        }
        var ins = h("button", { type: "button", class: "sv-btn sv-insert",
                                title: "Put this solution in place of what was solved (Undo takes it back)" }, ["Insert"]);
        ins.addEventListener("click", function () { insert({ index: item.index, text: item.src }); });
        buttons.push(ins);
        var row = h("li", { class: "sv-item sv-" + item.kind, "data-index": String(item.index) }, [
          h("div", { class: "sv-line" }, [formula, h("span", { class: "sv-actions" }, buttons)]),
          item.words ? h("div", { class: "sv-item-words" }, [item.words]) : null,
          checkOut
        ].filter(Boolean));
        list.appendChild(row);
      });
      wholeBtn.hidden = !res.items.length;
    }

    function runCheck(item, out, btn) {
      if (!solved) return;
      btn.disabled = true;
      out.hidden = false;
      out.textContent = "Checking…";
      api.call("check", { token: solved.token, index: item.index }).then(function (res) {
        btn.disabled = false;
        out.textContent = "";
        if (res.timed_out) { out.appendChild(h("div", { class: "sv-open" }, [res.message])); return; }
        res.rows.forEach(function (r) {
          var before = h("span", { class: "sv-before" }), after = h("span", { class: "sv-after" });
          tex(before, r.before);
          tex(after, r.after);
          out.appendChild(h("div", { class: "sv-row sv-" + r.verdict }, [before, h("span", { class: "sv-arrow" }, [" ⟶ "]), after]));
        });
        var verdicts = res.rows.map(function (r) { return r.verdict; });
        var cls = verdicts.indexOf("fails") >= 0 ? "sv-fails" : (verdicts.every(function (v) { return v === "holds"; }) ? "sv-holds" : "sv-open");
        out.appendChild(h("div", { class: "sv-verdict " + cls }, [res.words]));
      }, function (e) {
        btn.disabled = false;
        out.textContent = String(e && e.message || e);
        out.className = "sv-check sv-fails";
      });
    }

    function insert(what) {
      if (!solved) return;
      var payload = { token: solved.token };
      for (var k in what) payload[k] = what[k];
      api.call("insert", payload).then(function () {
        clearSolution();
        say("Inserted: Undo takes it back.");
      }, function (e) { say(String(e && e.message || e), "error"); });
    }

    solveBtn.addEventListener("click", solve);
    wholeBtn.addEventListener("click", function () { if (solved) insert({ whole: true, text: solved.set_src }); });
    domainSel.addEventListener("change", function () { clearSolution(); say(""); });

    api.katex().then(function (k) {
      katex = k;
      if (problem) showProblem(problem);
      if (solved) showSolution(solved);
    }, function () { /* the sources are shown instead */ });

    var HELP = [
      "<section><h3>Solving the selection</h3><ul>",
      "<li>The panel reads what is selected (the whole formula when nothing is): an <b>equation</b> <code>a = b</code>, an <b>inequality</b> (<code>&lt; &le; &gt; &ge; &ne;</code>), an <b>expression</b> alone, solved as <i>expression = 0</i>, or a <b>system</b>: equations joined by <i>and</i> (<code>Eq(x + y, 3) &amp; Eq(x - y, 1)</code>), a set or a tuple of them, or several of an <i>and</i>'s equations selected together (a drag over them; with a finger a long press, then the drag).</li>",
      "<li><b>Unknowns</b>: tick the symbols to solve for. To begin with, as many as there are equations are ticked - x, y, z first. The other symbols are parameters.</li>",
      "<li><b>Domain</b>: the complex numbers, the reals, the positive reals or the integers. An inequality is always solved over the reals.</li>",
      "<li><b>Solve</b> picks SymPy's solver by itself: <code>solveset</code> for one unknown (every equation of a system solved and the sets intersected), <code>linsolve</code> for a linear system in several unknowns, <code>nonlinsolve</code> otherwise, and <code>solve</code> when that gives up. With several unknowns the domain filters the solutions found. Inequalities in several unknowns are not solved.</li>",
      "<li>SymPy can take very long, or for ever: after " + (opts.timeout || 8) + " seconds the solver gives up and says so. The editor's Interrupt button stops it sooner.</li>",
      "</ul></section>",
      "<section><h3>The solutions</h3><ul>",
      "<li>Each solution has a line of its own. What is not one value is shown as SymPy has it, with words: an <b>interval</b> (every value in it), a <b>family</b> such as <code>{2&pi;n + &pi;/2 | n &isin; &#8484;}</code> (one solution for every integer n), a <b>condition set</b> (SymPy could not solve it: the values for which the condition holds), a free unknown in a system (any value).</li>",
      "<li><b>Substitute back</b> puts the solution into every equation - shown as substituted - and simplifies: <i>True</i> when it holds, <i>False</i> when not, and what is left when SymPy cannot decide. A family is checked for its general member (for every integer n).</li>",
      "<li><b>Insert</b> puts that solution in place of what was solved: <code>x = 2</code>, or <code>x = 2 &and; y = 1</code> for a system, or the set itself for a family or an interval. <b>Insert the set</b> puts the whole solution set there. Either is a step of the history: Undo brings the equation back.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Solve",
      help: HELP,
      onState: function (snap) {
        if (snap.preview) return;
        if (keyOf(target()) !== askedKey) ask();
      },
      onSelect: function () { if (keyOf(target()) !== askedKey) ask(); },
      destroy: function () { gone = true; clearTimeout(timer); seq++; }
    };
  }
});
