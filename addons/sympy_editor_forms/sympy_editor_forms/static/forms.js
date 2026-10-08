/*
 * sympy-editor add-on "forms": the selection rewritten every way SymPy
 * knows, one card per different form.
 *
 * Python runs the functions (method "run", one job per call, each under a
 * time box); this asks for them one after another - never two at a time,
 * and with a pause between them so that the user's own edits get in - and
 * draws the cards as the answers come.  A card applies its form ("apply":
 * an undoable step of the history, labelled with the function's name).
 */
SympyEditor.registerAddon("forms", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options || {};

    var exploreBtn = h("button", { type: "button", class: "fm-explore", title: "Run every function on the selection again" }, ["Explore"]);
    var stopBtn = h("button", { type: "button", class: "fm-stop", title: "Stop running the functions", disabled: "" }, ["Stop"]);
    var sortSel = h("select", { class: "fm-sort", title: "The order of the cards" }, [
      h("option", { value: "ops" }, ["fewest operations"]),
      h("option", { value: "length" }, ["shortest"]),
      h("option", { value: "order" }, ["as computed"])
    ]);
    var force = h("input", { type: "checkbox", class: "fm-force" });
    var boxSel = h("select", { class: "fm-timeout", title: "How long each function may run before it is given up" });
    [0.5, 1, 2, 5, 15].forEach(function (s) {
      var o = h("option", { value: String(s) }, [s + " s"]);
      if (Math.abs(s - (opts.timeout || 2)) < 1e-9) o.selected = true;
      boxSel.appendChild(o);
    });
    if (!boxSel.value) boxSel.value = "2";
    var follow = h("input", { type: "checkbox", class: "fm-follow", checked: "" });
    var bar = h("div", { class: "fm-bar" }, [
      exploreBtn, stopBtn,
      h("label", {}, ["sort ", sortSel]),
      h("label", {}, ["time box ", boxSel])
    ]);
    var switches = h("div", { class: "fm-switches" }, [
      h("label", { title: "Explore again whenever the selection or the formula changes" }, [follow, " follow the selection"]),
      h("label", { title: "logcombine, expand_log, powsimp and powdenest assume the symbols positive: log(x*y) = log(x) + log(y)" }, [force, " force (assume positive)"])
    ]);
    var current = h("div", { class: "fm-current" });
    var progress = h("div", { class: "fm-progress", role: "status" });
    var cards = h("div", { class: "fm-cards" });
    var rest = h("div", { class: "fm-rest" });
    var element = h("div", { class: "fm-panel" }, [bar, switches, current, progress, cards, rest]);

    var katex = null;
    api.katex().then(function (k) { katex = k; redraw(); }, function () { /* the sources are shown instead */ });

    var seq = 0;           // the exploration on show; a newer one makes the older stop
    var running = false;
    var gone = false;
    var timer = null;
    var box = null, stale = false;
    var askedKey = null;   // the target last explored (onSelect asks nothing for the same)
    var target = null;     // {path, children, force, timeout} of the exploration on show
    var now = null;        // the node as it is: {latex, src, ops, length, key}
    var forms = [];        // [{key, latex, src, ops, length, jobs: [id], labels: [label], order}]
    var timedOut = [], same = [], failed = [];
    var total = 0, done = 0;

    function pathTarget() {
      if (!follow.checked) return { path: "/", children: null };
      var r = api.range();
      if (r) return { path: r.parent, children: api.rangeIndices() };
      return { path: api.selected() || "/", children: null };
    }
    function keyOf(t) {
      var st = api.state();
      return (st && st.srepr || "") + "|" + t.path + (t.children ? ":" + t.children.join(",") : "") + "|" + (force.checked ? "f" : "") + "|" + boxSel.value;
    }

    /** Whether the panel is folded: nothing is explored for a box nobody
     *  sees, and what was skipped is explored when it is opened. */
    function folded() {
      if (!box && element.closest) {
        box = element.closest("details");
        if (box) box.addEventListener("toggle", unfold);
      }
      return !!box && !box.open;
    }
    function unfold() { if (box && box.open && stale) { stale = false; schedule(); } }

    function schedule() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      clearTimeout(timer);
      timer = setTimeout(explore, 250);
    }

    function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    function stop(text) {
      seq++;
      running = false;
      stopBtn.disabled = true;
      if (text !== undefined) progress.textContent = text;
    }

    function explore() {
      if (gone) return;
      var st = api.state();
      if (!st || st.preview) return;
      var t = pathTarget();
      askedKey = keyOf(t);
      var my = ++seq;
      target = { path: t.path, children: t.children, force: force.checked, timeout: parseFloat(boxSel.value) || 2 };
      now = null; forms = []; timedOut = []; same = []; failed = []; total = 0; done = 0;
      running = true;
      stopBtn.disabled = false;
      progress.textContent = "Looking at the selection…";
      progress.classList.remove("fm-error");
      redraw();
      var payload = { path: target.path };
      if (target.children) payload.children = target.children;
      api.call("plan", payload).then(function (res) {
        if (my !== seq) return;
        now = res.current;
        total = res.jobs.length;
        redraw();
        return runJobs(my, res.jobs);
      }).then(function () {
        if (my !== seq) return;
        running = false;
        stopBtn.disabled = true;
        progress.textContent = summary();
      }, function (e) {
        if (my !== seq) return;
        running = false;
        stopBtn.disabled = true;
        progress.textContent = String((e && e.message) || e);
        progress.classList.add("fm-error");
      });
    }

    /** The jobs one by one.  Each waits for the editor to be idle and then
     *  a moment more, so that an edit the user makes meanwhile goes first. */
    function runJobs(my, jobs) {
      var i = 0;
      var idle = function () {
        if (my !== seq || gone) return Promise.resolve(false);
        if (api.busy()) return wait(40).then(idle);
        return wait(30).then(function () { return my === seq && !gone && !api.busy() ? true : idle(); });
      };
      var next = function () {
        if (i >= jobs.length) return Promise.resolve();
        return idle().then(function (go) {
          if (!go) return;
          if (folded()) { stop(""); stale = true; askedKey = null; return; }
          var job = jobs[i];
          progress.textContent = "Running " + job.label + "\u2026 (" + (i + 1) + " of " + jobs.length + ")";
          var payload = { path: target.path, job: job.id, force: target.force, timeout: target.timeout };
          if (target.children) payload.children = target.children;
          return api.call("run", payload).then(null, function (e) {
            return { job: job.id, label: job.label, status: "error", error: String((e && e.message) || e) };
          }).then(function (res) {
            if (my !== seq || gone) return;
            done++;
            take(res, i);
            i++;
            redraw();
            return next();
          });
        });
      };
      return next();
    }

    function take(res, order) {
      if (res.status === "ok") {
        if (now && res.key === now.key) { same.push(res.label); return; }
        for (var i = 0; i < forms.length; i++) {
          if (forms[i].key === res.key) { forms[i].jobs.push(res.job); forms[i].labels.push(res.label); return; }
        }
        forms.push({ key: res.key, latex: res.latex, src: res.src, ops: res.ops, length: res.length,
                     jobs: [res.job], labels: [res.label], order: order });
      } else if (res.status === "timeout") {
        timedOut.push(res);
      } else if (res.status === "same") {
        same.push(res.label);
      } else {
        failed.push(res);
      }
    }

    function summary() {
      var n = forms.length;
      return n ? n + " other form" + (n === 1 ? "" : "s") + " from " + done + " functions"
               : "No other form: every function left it as it is, or gave up";
    }

    function sorted() {
      var by = sortSel.value;
      var num = function (v) { return v === null || v === undefined ? Infinity : v; };
      return forms.slice().sort(function (a, b) {
        if (by === "ops") return (num(a.ops) - num(b.ops)) || (a.length - b.length) || (a.order - b.order);
        if (by === "length") return (a.length - b.length) || (num(a.ops) - num(b.ops)) || (a.order - b.order);
        return a.order - b.order;
      });
    }

    function typeset(el, tex, src) {
      if (katex && tex) {
        try { katex.render(tex, el, { throwOnError: true, displayMode: false }); return; } catch (e) { /* the source instead */ }
      }
      el.textContent = src;
      el.classList.add("fm-src");
    }

    function meta(f, base) {
      var parts = [];
      if (f.ops !== null && f.ops !== undefined) {
        var d = base && base.ops !== null && base.ops !== undefined ? f.ops - base.ops : null;
        parts.push(h("span", { class: "fm-ops" + (d < 0 ? " fm-better" : d > 0 ? " fm-worse" : "") },
          [f.ops + " ops" + (d ? " (" + (d > 0 ? "+" : "−") + Math.abs(d) + ")" : "")]));
      }
      parts.push(h("span", { class: "fm-len" }, [f.length + " chars"]));
      return h("div", { class: "fm-meta" }, parts);
    }

    function redraw() {
      current.textContent = "";
      if (now) {
        var tex = h("span", { class: "fm-tex" });
        typeset(tex, now.latex, now.src);
        current.appendChild(h("span", { class: "fm-label" }, ["Now: "]));
        current.appendChild(tex);
        current.appendChild(meta(now, null));
      }
      cards.textContent = "";
      sorted().forEach(function (f) {
        var tex = h("div", { class: "fm-tex" });
        typeset(tex, f.latex, f.src);
        var card = h("button", { type: "button", class: "fm-card", "data-key": f.key, title: "Put this form in place of the selection (" + f.labels[0] + ")" }, [
          tex, meta(f, now), h("div", { class: "fm-fns" }, [f.labels.join(", ")])
        ]);
        card.addEventListener("click", function () { applyForm(f); });
        cards.appendChild(card);
      });
      timedOut.forEach(function (r) {
        cards.appendChild(h("div", { class: "fm-card fm-timeout-card", "data-job": r.job }, [
          h("div", { class: "fm-fns" }, [r.label]),
          h("div", { class: "fm-meta" }, ["timed out after " + r.seconds + " s"])
        ]));
      });
      rest.textContent = "";
      if (same.length) rest.appendChild(h("div", { class: "fm-same" }, ["Unchanged by " + same.join(", ")]));
      if (failed.length) {
        var det = h("details", { class: "fm-failed" }, [h("summary", {}, [failed.length + " not applicable"])]);
        failed.forEach(function (r) { det.appendChild(h("div", {}, [r.label + ": " + (r.error || "failed")])); });
        rest.appendChild(det);
      }
    }

    function applyForm(f) {
      if (!target || api.busy()) return;
      var payload = { path: target.path, job: f.jobs[0], force: target.force, timeout: target.timeout, key: f.key };
      if (target.children) payload.children = target.children;
      stop("Applying " + f.labels[0] + "…");
      api.call("apply", payload).then(function () {
        progress.textContent = "Applied " + f.labels[0];
      }, function (e) {
        progress.textContent = String((e && e.message) || e);
        progress.classList.add("fm-error");
      });
    }

    exploreBtn.addEventListener("click", function () { clearTimeout(timer); explore(); });
    stopBtn.addEventListener("click", function () { stop(done ? summary() + " (stopped)" : "Stopped"); });
    sortSel.addEventListener("change", redraw);
    force.addEventListener("change", schedule);
    boxSel.addEventListener("change", schedule);
    follow.addEventListener("change", schedule);

    var HELP = [
      "<section><h3>What it shows</h3><ul>",
      "<li>The selection — a range, or the whole formula when nothing is selected or <i>follow the selection</i> is off — rewritten by each of SymPy's simplification functions: <code>simplify</code>, <code>expand</code>, <code>factor</code>, <code>cancel</code>, <code>together</code>, <code>apart</code> and <code>collect</code> for each variable, <code>trigsimp</code>, <code>expand_trig</code>, <code>fu</code>, <code>powsimp</code>, <code>powdenest</code>, <code>radsimp</code>, <code>ratsimp</code>, <code>logcombine</code>, <code>expand_log</code>, <code>combsimp</code>, <code>gammasimp</code>, <code>nsimplify</code>, and <code>rewrite</code> in terms of <code>exp</code>, <code>sin</code>, <code>cos</code>, <code>sqrt</code>, <code>log</code>, <code>gamma</code>…</li>",
      "<li>One <b>card per different form</b>: when several functions give the same form, the card lists all of them. A function that leaves the selection as it is gets no card (they are named under the cards), nor one that does not apply to it.</li>",
      "<li>Each card shows the form, its number of <b>operations</b> (<code>count_ops</code>, with the difference from the selection as it is now: green fewer, red more) and its <b>length</b> in characters. <b>sort</b> orders them by the fewest operations, the shortest text, or as they were computed.</li>",
      "</ul></section>",
      "<section><h3>Using a form</h3><ul>",
      "<li><b>Tap a card</b> to put its form in place of the selection. It is a step of the history like any other edit, labelled with the function — <i>Forms: factor</i> — and Undo takes it back.</li>",
      "<li>With <i>follow the selection</i> on, the panel explores again whenever the selection or the formula changes; <b>Explore</b> runs everything again by hand, <b>Stop</b> stops.</li>",
      "<li><b>force</b>: <code>logcombine</code>, <code>expand_log</code>, <code>powsimp</code> and <code>powdenest</code> assume the symbols positive, so that <code>log(x y)</code> splits into <code>log(x) + log(y)</code> — true only for such symbols.</li>",
      "</ul></section>",
      "<section><h3>Slow functions</h3><ul>",
      "<li>The functions run one at a time, each for the <b>time box</b> at most: one that takes longer (<code>simplify</code> sometimes runs for minutes) is given up and shown as a <i>timed out</i> card, and the next one starts. A longer time box gives such a function a second chance.</li>",
      "<li>The editor stays yours meanwhile: an edit made while the functions run goes first, and the exploration starts again on the new formula. A folded panel runs nothing.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Forms",
      help: HELP,
      onState: function (snap) {
        if (snap.preview) return;
        if (keyOf(pathTarget()) === askedKey) return;      // the same formula drawn again
        // the formula changed: what is on show is about another one
        stop("");
        askedKey = null;
        schedule();
      },
      // Only for another target: a selection drawn again names the one on
      // show, and exploring again for it would ask Python for ever.
      onSelect: function () {
        if (!follow.checked) return;
        if (keyOf(pathTarget()) === askedKey) return;
        stop("");
        schedule();
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
