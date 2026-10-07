/*
 * sympy-editor add-on "steps": a step-by-step solution for the selection.
 *
 * Python works the steps out (method "steps", a query) and applies one
 * (method "apply", a change: a step of the history).  This lists them, each
 * rule in words and its result drawn by KaTeX, with an Apply button where
 * the step's result can take the selection's place.
 */
SympyEditor.registerAddon("steps", {
  mount: function (api) {
    var h = api.h;
    var TASKS = [["auto", "Explain the selection"], ["differentiate", "Differentiate"],
                 ["integrate", "Integrate"], ["solve", "Solve"]];
    var taskSel = h("select", { class: "st-task", title: "What to do with the selection: “explain” works out an integral or a derivative and solves an equation" });
    TASKS.forEach(function (t) { taskSel.appendChild(h("option", { value: t[0] }, [t[1]])); });
    var varSel = h("select", { class: "st-var", title: "The variable: to differentiate, integrate or solve for" });
    var varLabel = h("label", { class: "st-varlabel", hidden: "" }, ["for ", varSel]);
    var bar = h("div", { class: "st-bar" }, [taskSel, varLabel]);
    var start = h("div", { class: "st-start" });
    var list = h("ol", { class: "st-steps" });
    var note = h("div", { class: "st-note" });
    var offers = h("div", { class: "st-offers" });
    var element = h("div", { class: "st-panel" }, [bar, start, list, note, offers]);

    var katex = null, gone = false, seq = 0, timer = null, box = null, stale = false;
    var shown = null;       // the answer on show, with what it was asked for
    var askedKey = null;    // the target last asked for: a selection drawn again asks nothing
    var varPicked = null;   // the variable chosen in "for", until the selection moves elsewhere
    api.katex().then(function (k) { katex = k; if (shown) draw(shown.answer, shown.sent); }, function () { /* sources instead */ });

    function tex(el, src, display) {
      el.textContent = "";
      if (katex) {
        try { katex.render(src, el, { throwOnError: true, displayMode: !!display }); return; } catch (e) { /* the source instead */ }
      }
      el.appendChild(h("code", {}, [src]));
    }

    function target() {
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/" };
    }

    function keyOf(t) { return t.path + "|" + (t.children ? t.children.join(",") : ""); }

    function folded() {
      if (!box && element.closest) {
        box = element.closest("details");
        if (box) box.addEventListener("toggle", function () { if (box.open && stale) { stale = false; request(); } });
      }
      return !!box && !box.open;
    }

    function request() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      clearTimeout(timer);
      timer = setTimeout(ask, 120);
    }

    function payload() {
      var t = target();
      t.task = taskSel.value;
      if (varPicked) t.var = varPicked;
      return t;
    }

    function ask() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      if (api.busy()) { request(); return; }
      var sent = payload();
      askedKey = keyOf(sent);
      var my = ++seq;
      element.classList.add("st-working");
      api.call("steps", sent, { quiet: true }).then(function (answer) {
        if (my !== seq || gone) return;
        element.classList.remove("st-working");
        draw(answer, sent);
      }, function (e) {
        if (my !== seq || gone) return;
        element.classList.remove("st-working");
        shown = null;
        list.textContent = ""; start.textContent = ""; offers.textContent = "";
        note.className = "st-note st-error";
        note.textContent = String((e && e.message) || e);
      });
    }

    function fillVars(answer) {
      var vars = answer.vars || [];
      var keep = answer.var || varPicked;
      varSel.textContent = "";
      vars.forEach(function (v) { varSel.appendChild(h("option", { value: v }, [v])); });
      if (keep && vars.indexOf(keep) >= 0) varSel.value = keep;
      // a choice only where there is one to make
      varLabel.hidden = vars.length < 2;
    }

    function draw(answer, sent) {
      shown = { answer: answer, sent: sent };
      fillVars(answer);
      start.textContent = "";
      if (answer.start) {
        var math = h("span", { class: "st-math" });
        start.appendChild(h("span", { class: "st-head" }, [answer.title + ":"]));
        start.appendChild(math);
        tex(math, "\\displaystyle " + answer.start);
      }
      list.textContent = "";
      (answer.steps || []).forEach(function (st, i) {
        var words = h("div", { class: "st-text" }, [st.text]);
        var row = h("li", { class: "st-step" + (st.applicable ? "" : " st-remark"), "data-index": String(i) }, [words]);
        if (st.detail) {
          var detail = h("div", { class: "st-detail" });
          row.appendChild(detail);
          tex(detail, st.detail);
        }
        var line = h("div", { class: "st-line" });
        if (st.latex) {
          var math = h("span", { class: "st-math" });
          line.appendChild(math);
          tex(math, "\\displaystyle " + st.latex);
        }
        if (st.applicable) {
          var btn = h("button", { type: "button", class: "st-apply",
                                  title: "Put this result in the formula in place of the selection (Undo takes it back)" }, ["Apply"]);
          btn.addEventListener("click", function () { applyStep(i, st, sent); });
          line.appendChild(btn);
        }
        row.appendChild(line);
        list.appendChild(row);
      });
      note.className = "st-note";
      note.textContent = answer.message || "";
      offers.textContent = "";
      if (!(answer.steps || []).length && (answer.offers || []).length) {
        answer.offers.forEach(function (task) {
          var label = { differentiate: "Differentiate", integrate: "Integrate", solve: "Solve" }[task] || task;
          var b = h("button", { type: "button", class: "st-offer", "data-task": task, title: label + " the selection, step by step" }, [label]);
          b.addEventListener("click", function () { taskSel.value = task; request(); });
          offers.appendChild(b);
        });
      }
    }

    function applyStep(i, st, sent) {
      var msg = { path: sent.path, task: sent.task, index: i, src: shown ? shown.answer.src : null, label: st.text };
      if (sent.children) msg.children = sent.children;
      if (sent.var) msg.var = sent.var;
      api.call("apply", msg).then(function () { /* the new state asks again (onState) */ }, function (e) {
        note.className = "st-note st-error";
        note.textContent = String((e && e.message) || e);
      });
    }

    taskSel.addEventListener("change", request);
    varSel.addEventListener("change", function () { varPicked = varSel.value || null; request(); });

    var HELP = [
      "<section><h3>Steps</h3><ul>",
      "<li>Shows how a result is reached, one rule at a time, for the <b>selection</b> — or the whole formula when nothing is selected. A range of terms works too.</li>",
      "<li><b>Explain the selection</b> (the default) works out an <b>integral</b> (definite or not), a <b>derivative</b> (of any order) and <b>solves an equation</b> of degree one or two in one unknown. For any other expression it says so and offers <b>Differentiate</b>, <b>Integrate</b> or <b>Solve</b> (the expression = 0); the menu at the top asks for one of them directly.</li>",
      "<li><b>for</b> picks the variable when the selection has more than one symbol.</li>",
      "<li>Each step names its rule in words — sum, product, quotient, chain, power rule; substitution, integration by parts; the quadratic formula — with the rule's parameters (u = …, dv = …) and the whole expression the step leaves. Integrals still to do stand unevaluated until a later step does them.</li>",
      "<li><b>Apply</b> puts a step's result in the formula in place of the selection: one step of the history, labelled with the rule, which <b>Undo</b> takes back. Remarks (the coefficients, the discriminant, the antiderivative of a definite integral, the + C) have nothing to apply.</li>",
      "<li>Integration follows SymPy's <code>manualintegrate</code>: when it knows no rule, the panel says so, and gives SymPy's own result when there is one, without steps. A substitution's new variable is called u (v, w… when u is taken).</li>",
      "<li>What is not explained is said in words: an equation of degree three, an unknown in a denominator, an inequality, a function SymPy only differentiates as a whole.</li>",
      "<li>A folded panel asks for nothing; it catches up when opened.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Steps",
      help: HELP,
      onState: function (snap) { if (!snap.preview) request(); },
      onSelect: function () {
        if (keyOf(target()) === askedKey) return;      // the same target drawn again
        varPicked = null;                              // another target: its own variable
        request();
      },
      destroy: function () { gone = true; clearTimeout(timer); seq++; }
    };
  }
});
