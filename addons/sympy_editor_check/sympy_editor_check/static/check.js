/*
 * sympy-editor add-on "check": is every step of the history the same
 * mathematics as the one before?  A plain script, run once per page with
 * `SympyEditor` in scope.
 *
 * The panel asks Python (`check`) after each committed change, quietly: one
 * request compares at most about a second's worth of new steps (the editor
 * answers one message at a time, and a check must not hold it), and while
 * steps are left (`pending`) the panel asks again once the editor is idle.
 * Python keeps every verdict, so a step is compared once.  Clicking a step
 * goes there (the editor's `goto`).
 */
(function () {
  var NAMES = {
    equal: "equal", different: "not equal", unknown: "could not decide", transformation: "transformation",
    incomplete: "being built", start: "start", pending: "not checked yet"
  };

  var HELP = "<section><h3>Check my work</h3>"
    + "<p>Compares every step of the history with the step before it and says whether the two are the same mathematics.</p><ul>"
    + "<li><b>✓</b> equal: proven by SymPy (<code>simplify(a − b) = 0</code>, <code>a.equals(b)</code>, the same solutions), "
    + "or - said so in the line - found equal at several random points when SymPy could not prove it.</li>"
    + "<li><b>✗</b> not equal: a point where the two steps differ, with the value of each there.  The first ✗ is marked: "
    + "that is where the maths went wrong.</li>"
    + "<li><b>?</b> could not decide, and why (SymPy ran out of time, the steps are of different kinds...).</li>"
    + "<li><b>→</b> a transformation that is not meant to keep the value - Differentiate, Integrate, Substitute, Solve, "
    + "a SymPy function such as <code>diff</code> or <code>subs</code>, Unwrap, Isolate - named by the history's label.</li>"
    + "<li><b>…</b> a step with empty slots (a template being filled in) is not compared.</li></ul>"
    + "<h3>Equations and inequalities</h3><ul>"
    + "<li>Two equations are equal when the difference of their sides is the same up to a factor that is not 0, "
    + "or when they have the same solutions (in one unknown: the solution sets; in several: solved for a shared unknown "
    + "at random values of the others).  <code>x² = 4</code> then <code>x = 2</code> is ✗: <code>x = −2</code> was lost.</li>"
    + "<li>Inequalities are compared over the reals, so multiplying by a negative number without turning the sign round is ✗.</li></ul>"
    + "<h3>Using it</h3><ul>"
    + "<li>Click a step (or Enter on it) to go to it in the formula - the editor's history, as Undo and Redo walk it.</li>"
    + "<li><b>Check</b> checks again; <b>Auto</b> checks after every change.  Each comparison is time-limited, "
    + "so a step SymPy cannot settle quickly is a ?, never a frozen editor.</li>"
    + "<li>The history view shows the same marks beside each step that has been checked.</li></ul></section>";

  var HISTORY_CSS = ".chk-history { display: inline-block; margin: 0.15rem 0; font: 12px/1.3 system-ui, sans-serif; color: #555; }"
    + ".chk-history b { display: inline-block; min-width: 1.2em; text-align: center; }"
    + ".chk-history.chk-equal b { color: #1a7f37; } .chk-history.chk-different b, .chk-history.chk-different { color: #b42318; }"
    + ".chk-history.chk-transformation b { color: #2f6fdb; }"
    + "@media (prefers-color-scheme: dark) { .chk-history { color: #aaa; } .chk-history.chk-equal b { color: #4ac26b; }"
    + " .chk-history.chk-different b, .chk-history.chk-different { color: #ff7b72; } .chk-history.chk-transformation b { color: #79a8ff; } }";

  function historyBadge(h, step) {
    var c = step && step.check;
    if (!c || c.status === "start") return null;
    return h("div", { class: "chk-history chk-" + c.status, title: c.text || "" },
      [h("b", {}, [c.symbol || "?"]), " " + (c.text || NAMES[c.status] || "")]);
  }

  SympyEditor.registerAddon("check", {
    tools: [],
    mount: function (api) {
      var h = api.h;
      var summary = h("span", { class: "chk-summary" }, ["Not checked yet."]);
      var again = h("button", { type: "button", class: "chk-again", title: "Check every step again, from scratch" }, ["Check"]);
      var autoBox = h("input", { type: "checkbox", class: "chk-auto-box" });
      autoBox.checked = true;
      var auto = h("label", { class: "chk-auto", title: "Check after every change" }, [autoBox, "Auto"]);
      var list = h("ol", { class: "chk-list", role: "list" });
      var element = h("div", { class: "chk-panel" }, [h("div", { class: "chk-bar" }, [summary, again, auto]), list]);

      var asking = false, again_ = false, lastKey = null, timer = null, destroyed = false, last = null;

      function keyOf(snap) {
        var c = snap && snap.check;
        return c ? c.n + ":" + c.index + ":" + (snap.src || "") : null;
      }

      function goTo(index) {
        if (last && index === last.index) return;
        api.send({ action: "goto", index: index });
      }

      function render(res) {
        last = res;
        list.textContent = "";
        var rows = res.steps || [];
        for (var i = 0; i < rows.length; i++) {
          var r = rows[i];
          var cls = "chk-step chk-" + r.status + (r.index === res.index ? " chk-current" : "")
            + (r.index === res.first_error ? " chk-first-error" : "");
          var parts = [
            h("span", { class: "chk-mark", "aria-label": NAMES[r.status] || r.status }, [r.symbol || "?"]),
            h("span", { class: "chk-n" }, [String(r.index + 1)]),
            h("span", {}, [h("code", { class: "chk-src" }, [r.src]), " ", h("span", { class: "chk-label" }, ["(" + r.label + ")"])])
          ];
          if (r.index > 0) parts.push(h("span", { class: "chk-text" }, [r.text || ""]));
          var li = h("li", { class: cls, tabindex: "0", "data-index": String(r.index),
                             title: (NAMES[r.status] || r.status) + " - click to go to this step" }, parts);
          (function (index) {
            li.addEventListener("click", function () { goTo(index); });
            li.addEventListener("keydown", function (ev) {
              if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.stopPropagation(); goTo(index); }
            });
          })(r.index);
          list.appendChild(li);
        }
        var c = res.counts || {};
        summary.className = "chk-summary";
        if (res.first_error != null) {
          summary.textContent = "The maths went wrong at step " + (res.first_error + 1) + ".";
          summary.className += " chk-bad";
        } else if (rows.length < 2) {
          summary.textContent = "One step: nothing to compare yet.";
        } else if (res.pending) {
          summary.textContent = "Checking…";
        } else if (c.unknown) {
          summary.textContent = "No error found; " + c.unknown + " step" + (c.unknown > 1 ? "s" : "") + " could not be decided.";
        } else {
          summary.textContent = "Every step checks out.";
          summary.className += " chk-good";
        }
        var cur = list.querySelector(".chk-current");
        if (cur && cur.scrollIntoView && list.scrollHeight > list.clientHeight) {
          var top = cur.offsetTop, bottom = top + cur.offsetHeight;
          if (top < list.scrollTop || bottom > list.scrollTop + list.clientHeight) list.scrollTop = Math.max(0, top - 8);
        }
      }

      function ask(method) {
        if (destroyed) return;
        if (asking) { again_ = true; return; }
        asking = true;
        api.call(method || "check", {}, { quiet: true }).then(function (res) {
          asking = false;
          if (destroyed) return;
          render(res);
          if (again_) { again_ = false; ask(); }
          else if (res.pending) later(120);
        }, function (e) {
          asking = false;
          if (destroyed) return;
          summary.textContent = "The check failed: " + String(e && e.message || e);
          summary.className = "chk-summary chk-bad";
        });
      }

      function later(ms) {
        clearTimeout(timer);
        timer = setTimeout(function wait() {
          if (destroyed) return;
          if (api.busy()) { timer = setTimeout(wait, 150); return; }   // the user's own edits first
          ask();
        }, ms);
      }

      again.addEventListener("click", function () { ask("forget"); });
      autoBox.addEventListener("change", function () { if (autoBox.checked) later(0); });

      return {
        element: element,
        title: "Check my work",
        help: HELP,
        onState: function (snap) {
          if (!snap || snap.preview || snap.error) return;
          var key = keyOf(snap);
          if (key === lastKey) return;
          lastKey = key;
          if (autoBox.checked) later(250);
          else if (last && snap.check) {           // the current step moved: show it without asking
            last.index = snap.check.index;
            render(last);
          }
        },
        historyStep: function (step) { return historyBadge(h, step); },
        historyStepHtml: function (step) {
          var el = historyBadge(h, step);
          return el ? el.outerHTML : "";
        },
        historyCss: HISTORY_CSS,
        destroy: function () { destroyed = true; clearTimeout(timer); }
      };
    }
  });
})();
