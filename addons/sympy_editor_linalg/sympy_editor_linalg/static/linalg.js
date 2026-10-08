/*
 * sympy-editor add-on "linalg": a workbench for the selected matrix.
 *
 * Python computes (methods analyse, spectrum, decompose, rref - queries
 * all), this shows: every result typeset, each with an Insert button that
 * puts it in place of the matrix as one step of the history (method
 * insert).  The panel asks only for a matrix it has not asked about: the
 * target is the explicit matrix around the selection, read from the node
 * table, so clicking from one entry to another of the same matrix asks
 * nothing; and nothing is asked while the panel is folded.
 */
SympyEditor.registerAddon("linalg", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options || {};
    var limits = opts.limits || { basic: 4, spectrum: 8, decompose: 8, max: 120 };
    var katex = null;
    api.katex().then(function (k) { katex = k; redraw(); }, function () { /* the texts are shown instead */ });

    var head = h("div", { class: "la-head" });
    var props = h("div", { class: "la-props" });
    var spectrum = h("div", { class: "la-spectrum" });
    var decomp = h("div", { class: "la-decomp" });
    var problems = h("div", { class: "la-problems" });
    function tool(kind, label, title) {
      var b = h("button", { type: "button", class: "la-tool", "data-kind": kind, title: title }, [label]);
      b.addEventListener("click", function () { decompose(kind); });
      return b;
    }
    var tools = h("div", { class: "la-tools" }, [
      tool("rref", "Row reduce (steps)", "Gauss-Jordan elimination, one elementary row operation at a time"),
      tool("lu", "LU", "M = L U (with a permutation when rows must be exchanged)"),
      tool("qr", "QR", "M = Q R, Q with orthonormal columns"),
      tool("cholesky", "Cholesky", "M = L Lᴴ, for a Hermitian positive definite matrix")
    ]);
    var element = h("div", { class: "la-panel" }, [head, problems, props, spectrum, tools, decomp]);

    var seqs = { a: 0, s: 0, d: 0 }, timer = null, gone = false, box = null, stale = false;
    var askedKey = null;     // the target last asked about: a selection that names it again asks nothing
    var shown = null;        // the last answers, for a redraw when KaTeX arrives
    var target = "/";

    /** The explicit matrix around the selection, by the node table: the
     *  nearest ancestor (or the node itself) marked `matrix`; the selection
     *  itself when there is none (Python then looks further). */
    function targetPath() {
      var r = api.range();
      var p = r ? r.parent : (api.selected() || "/");
      var snap = api.state();
      var nodes = snap && snap.nodes || {};
      var q = p;
      for (;;) {
        if (nodes[q] && nodes[q].matrix) return q;
        if (q === "/" || !q) break;
        q = q.replace(/\/[^\/]*$/, "") || "/";
      }
      return p;
    }

    /** What was asked about: the matrix's path in this formula.  A new state
     *  of the same formula (a re-render) or another entry of the same matrix
     *  is the same question. */
    function askKey(path) {
      var snap = api.state();
      return path + "|" + (snap && snap.src || "");
    }

    function folded() {
      if (!box && element.closest) {
        box = element.closest("details");
        if (box) box.addEventListener("toggle", unfold);
      }
      return !!box && !box.open;
    }
    function unfold() { if (box && box.open && stale) { stale = false; request(); } }

    function request() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      clearTimeout(timer);
      timer = setTimeout(ask, 120);
    }

    function math(tex, src) {
      var span = h("span", { class: "la-math" });
      if (katex) {
        try { span.innerHTML = katex.renderToString(tex, { throwOnError: false }); return span; }
        catch (e) { /* the text instead */ }
      }
      span.textContent = src || tex;
      return span;
    }

    function insertButton(item, what) {
      var b = h("button", { type: "button", class: "la-insert", title: "Put this in place of the matrix (Undo takes it back)" }, ["Insert"]);
      b.addEventListener("click", function () {
        api.call("insert", { id: item.id, what: what || item.label }).then(null, function (e) { api.error(String(e && e.message || e)); });
      });
      return b;
    }

    function row(label, item, what) {
      return h("div", { class: "la-row" }, [
        h("span", { class: "la-label" }, [label]),
        math(item.latex, item.src),
        insertButton(item, what)
      ]);
    }

    function showProblems(list, method, extra) {
      (list || []).forEach(function (p) {
        var line = h("div", { class: "la-problem" }, [p.text]);
        if (p.timeout && p.timeout < limits.max) {
          var again = h("button", { type: "button", class: "la-again" }, ["Try for " + Math.min(limits.max, p.timeout * 4) + " s"]);
          again.addEventListener("click", function () {
            var payload = Object.assign({ path: target, limit: Math.min(limits.max, p.timeout * 4) }, extra || {});
            run(method, payload);
          });
          line.appendChild(again);
        }
        problems.appendChild(line);
      });
    }

    function ask() {
      if (gone) return;
      if (folded()) { stale = true; return; }
      if (api.busy()) { request(); return; }
      var path = targetPath();
      var key = askKey(path);
      if (key === askedKey) return;
      askedKey = key;
      target = path;
      seqs.a++; seqs.s++; seqs.d++;
      shown = { analyse: null, spectrum: null, decomp: null };
      decomp.textContent = "";
      run("analyse", { path: path });
    }

    /** One method, its answer drawn where it belongs; analyse brings the
     *  spectrum after it for a square matrix. */
    function run(method, payload) {
      var slot = method === "analyse" ? "a" : method === "spectrum" ? "s" : "d";
      var my = ++seqs[slot];
      var old = function () { return gone || my !== seqs[slot]; };
      if (method === "analyse") { head.textContent = "Looking at the matrix…"; head.className = "la-head"; }
      if (method === "spectrum") spectrum.textContent = "Eigenvalues: computing…";
      if (method === "decompose" || method === "rref") decomp.textContent = "Computing…";
      return api.call(method, payload).then(function (res) {
        if (old()) return;
        if (method === "analyse") {
          shown.analyse = res; shown.spectrum = null;
          problems.textContent = "";
          drawAnalyse(res);
          tools.hidden = false;
          tools.querySelector('[data-kind="cholesky"]').disabled = !res.square;
          if (res.square) run("spectrum", { path: res.path });
          else spectrum.textContent = "";
        } else if (method === "spectrum") {
          shown.spectrum = res;
          drawSpectrum(res);
        } else {
          shown.decomp = { method: method, res: res };
          drawDecomp(method, res);
        }
      }, function (e) {
        if (old()) return;
        if (method === "analyse") {
          shown = { analyse: null, spectrum: null, decomp: null };
          head.className = "la-head la-none";
          head.textContent = String(e && e.message || e).replace(/^ValueError: /, "");
          props.textContent = ""; spectrum.textContent = ""; problems.textContent = ""; decomp.textContent = "";
          tools.hidden = true;
        } else {
          var where = method === "spectrum" ? spectrum : decomp;
          where.textContent = String(e && e.message || e).replace(/^ValueError: /, "");
          where.className = where.className.replace(/ la-error/, "") + " la-error";
        }
      });
    }

    function drawAnalyse(res) {
      head.className = "la-head";
      head.textContent = res.rows + " × " + res.cols + (res.square ? " square" : "") + " matrix"
        + (res.path === "/" ? " (the whole formula)" : " at " + res.path)
        + (res.explicit ? "" : ", written out entry by entry")
        + (res.limited ? "" : " - no time limit here: the Interrupt button stops a long computation");
      props.textContent = "";
      res.items.forEach(function (item) { props.appendChild(row(item.label, item)); });
      if (!res.square) props.appendChild(h("div", { class: "la-note" }, ["Determinant, trace, characteristic polynomial and eigenvalues need a square matrix."]));
      showProblems(res.problems, "analyse");
    }

    function drawSpectrum(res) {
      spectrum.textContent = "";
      spectrum.className = "la-spectrum";
      if (res.eigen && res.eigen.length) {
        var table = h("table", { class: "la-eigen" }, [h("tr", {}, [
          h("th", {}, ["eigenvalue"]), h("th", { title: "algebraic multiplicity: how often it is a root of the characteristic polynomial" }, ["alg."]),
          h("th", { title: "geometric multiplicity: the dimension of its eigenspace" }, ["geo."]), h("th", {}, ["eigenvectors"])])]);
        res.eigen.forEach(function (e) {
          var vecs = h("td", { class: "la-vecs" });
          e.vectors.forEach(function (v) { vecs.appendChild(h("span", { class: "la-vec" }, [math(v.latex, v.src), insertButton(v, "eigenvector")])); });
          if (e.geo === null) vecs.appendChild(h("span", { class: "la-note" }, ["not computed"]));
          table.appendChild(h("tr", {}, [
            h("td", {}, [math(e.value.latex, e.value.src), insertButton(e.value, "eigenvalue")]),
            h("td", { class: "la-num" }, [String(e.alg)]),
            h("td", { class: "la-num" + (e.geo !== null && e.geo < e.alg ? " la-short" : "") }, [e.geo === null ? "?" : String(e.geo)]),
            vecs]));
        });
        spectrum.appendChild(h("div", { class: "la-sub" }, ["Eigenvalues"]));
        spectrum.appendChild(table);
      }
      var d = res.diagonalizable;
      spectrum.appendChild(h("div", { class: "la-verdict" + (d === false ? " la-no" : "") }, [
        d === true ? "Diagonalizable: every eigenvalue has as many independent eigenvectors as its multiplicity."
          : d === false ? "Not diagonalizable: an eigenvalue has fewer independent eigenvectors than its multiplicity (marked); the Jordan form has a block larger than 1 × 1."
          : "Diagonalizable: could not be decided in time."]));
      if (res.jordan) {
        spectrum.appendChild(h("div", { class: "la-sub" }, ["Jordan form"]));
        spectrum.appendChild(row("J", res.jordan.J, "Jordan form"));
        spectrum.appendChild(row("P", res.jordan.P, "change of basis"));
        spectrum.appendChild(row("M = P J P⁻¹", res.jordan.product, "P J P⁻¹"));
      }
      showProblems(res.problems, "spectrum");
    }

    function drawDecomp(method, res) {
      decomp.textContent = "";
      decomp.className = "la-decomp";
      if (res.problems && res.problems.length) {
        res.problems.forEach(function (p) {
          var line = h("div", { class: "la-problem" }, [p.text]);
          if (p.timeout && p.timeout < limits.max) {
            var again = h("button", { type: "button", class: "la-again" }, ["Try for " + Math.min(limits.max, p.timeout * 4) + " s"]);
            again.addEventListener("click", function () {
              var payload = { path: target, limit: Math.min(limits.max, p.timeout * 4) };
              if (method === "decompose") payload.kind = res.kind;
              run(method, payload);
            });
            line.appendChild(again);
          }
          decomp.appendChild(line);
        });
        return;
      }
      if (method === "rref") {
        decomp.appendChild(h("div", { class: "la-sub" }, ["Row reduction, step by step"]));
        if (!res.steps.length) decomp.appendChild(h("div", { class: "la-note" }, ["Already in reduced row echelon form: no operation needed."]));
        var list = h("ol", { class: "la-steps" });
        res.steps.forEach(function (s) {
          var li = h("li", { class: "la-step", "data-kind": s.kind }, [
            h("div", { class: "la-op" }, [math(s.oplatex, s.op), h("span", { class: "la-optext" }, [s.op])]),
            h("div", { class: "la-row" }, [math(s.latex, s.src), insertButton(s, "after " + s.op)])
          ]);
          if (s.assumes) li.appendChild(h("div", { class: "la-note" }, [math(s.assumes + " \\neq 0"), " is assumed"]));
          list.appendChild(li);
        });
        decomp.appendChild(list);
        decomp.appendChild(row("RREF", res.result, "reduced row echelon form"));
        decomp.appendChild(h("div", { class: "la-note" }, [
          "Pivot columns: " + (res.pivots.join(", ") || "none") + ". "
          + (res.check ? "Agrees with SymPy's Matrix.rref()." : "Differs from SymPy's Matrix.rref() - an entry could not be decided zero or not.")
          + (res.assumed.length ? " Symbolic pivots were taken to be non-zero." : "")]));
        return;
      }
      decomp.appendChild(h("div", { class: "la-sub" }, [{ lu: "LU", qr: "QR", cholesky: "Cholesky" }[res.kind] + " decomposition"]));
      res.factors.forEach(function (f) { decomp.appendChild(row(f.label, f)); });
      decomp.appendChild(row("M = " + res.relation, res.product, res.relation));
      if (res.note) decomp.appendChild(h("div", { class: "la-note" }, [res.note]));
    }

    function decompose(kind) {
      if (!shown || !shown.analyse) return;
      if (kind === "rref") run("rref", { path: target });
      else run("decompose", { path: target, kind: kind });
    }

    function redraw() {
      if (!shown) return;
      if (shown.analyse) { problems.textContent = ""; drawAnalyse(shown.analyse); }
      if (shown.spectrum) drawSpectrum(shown.spectrum);
      if (shown.decomp) drawDecomp(shown.decomp.method, shown.decomp.res);
    }

    tools.hidden = true;

    var HELP = [
      "<section><h3>What it shows</h3><ul>",
      "<li>The <b>explicit matrix</b> around the selection: the selected matrix, the matrix an entry belongs to, or the whole formula when nothing is selected. A matrix expression that can be written out (the transpose of a matrix, a matrix symbol of a numeric size) is written out entry by entry.</li>",
      "<li>Its <b>shape</b>, <b>rank</b>, and for a square matrix the <b>determinant</b>, the <b>trace</b> and the <b>characteristic polynomial</b> in λ (factored too, when that says more).</li>",
      "<li>The <b>eigenvalues</b>, each with its <i>algebraic</i> multiplicity (how often it is a root of the characteristic polynomial), its <i>geometric</i> multiplicity (how many independent eigenvectors it has) and those <b>eigenvectors</b>. A geometric multiplicity smaller than the algebraic one is marked: the matrix is then <b>not diagonalizable</b>.</li>",
      "<li>The <b>Jordan form</b> J with the change of basis P, so that M = P J P⁻¹.</li>",
      "</ul></section>",
      "<section><h3>On demand</h3><ul>",
      "<li><b>Row reduce (steps)</b>: Gauss-Jordan elimination one elementary row operation at a time - a swap (R1 ↔ R2), a row divided by its pivot (R1 ← ½·R1), a multiple of the pivot row taken from another (R2 ← R2 − 3·R1) - each with the matrix after it. The result is checked against SymPy's own <code>Matrix.rref()</code>. A symbolic pivot is taken to be non-zero, as SymPy does, and the step says so.</li>",
      "<li><b>LU</b>: M = L U (Pᵀ L U when rows had to be exchanged). <b>QR</b>: M = Q R. <b>Cholesky</b>: M = L Lᴴ, for a Hermitian positive definite matrix - the panel says when it does not apply.</li>",
      "</ul></section>",
      "<section><h3>Insert</h3><ul>",
      "<li>Every result has an <b>Insert</b> button: it takes the place of the matrix in the formula, as one step of the history, so <b>Undo</b> takes it back. A factorisation goes in as the product of its factors, left unevaluated - it is equal to the matrix.</li>",
      "<li>If the matrix has changed since the result was computed, Insert refuses rather than put a stale result in.</li>",
      "</ul></section>",
      "<section><h3>Time limits</h3><ul>",
      "<li>Symbolic linear algebra can take very long - the eigenvectors of a symbolic 4 × 4 matrix may never finish. Each computation is stopped after a few seconds and the panel says which one, with a button to <b>try for longer</b>.</li>",
      "<li>In a standalone page (Python running in the browser) there are no threads to stop a computation with: there the editor's <b>Interrupt</b> button is the limit.</li>",
      "<li>The panel asks only when the matrix changes - a click from one entry to another of the same matrix asks nothing - and a folded panel asks for nothing at all.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Linear algebra",
      help: HELP,
      onState: function (snap) { if (!snap.preview) request(); },
      // Only for another matrix: the same one selected again (another entry
      // of it, a redraw) asks nothing.
      onSelect: function () { if (askKey(targetPath()) !== askedKey) request(); },
      destroy: function () {
        gone = true;
        clearTimeout(timer);
        seqs.a++; seqs.s++; seqs.d++;
        if (box) box.removeEventListener("toggle", unfold);
      }
    };
  }
});
