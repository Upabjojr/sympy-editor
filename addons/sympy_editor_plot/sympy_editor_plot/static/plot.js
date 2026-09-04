/*
 * sympy-editor add-on "plot": the graph of the selection under the formula.
 *
 * Python samples (method "samples"); this draws.  Plotly.js is loaded from
 * the CDN in `api.options.plotlyJs` the first time a curve is drawn; when it
 * cannot be (offline, a bundle without it) the curve is an SVG polyline
 * drawn here, so a plot is never missing altogether.
 */
SympyEditor.registerAddon("plot", {
  mount: function (api) {
    var h = api.h;
    var opts = api.options;
    var area = h("div", { class: "plot-area" });
    var note = h("div", { class: "plot-note" });
    var varSel = h("select", { title: "The variable on the horizontal axis" });
    // Text fields, not number inputs: a number input offers no text selection
    // to speak of (no selectionStart, no double-click to select) in some
    // browsers; a text field with a decimal keyboard does.
    var numField = function (value, title) {
      return h("input", { type: "text", inputmode: "decimal", class: "plot-num", value: String(value), title: title, spellcheck: "false", autocomplete: "off" });
    };
    var from = numField(opts.span ? opts.span[0] : -6, "Left end of the axis (a zoom in the picture changes it too)");
    var to = numField(opts.span ? opts.span[1] : 6, "Right end of the axis (a zoom in the picture changes it too)");
    var shown = h("span", { class: "plot-shown", title: "The range on show, after a zoom or a pan in the picture" });
    var follow = h("input", { type: "checkbox", checked: "" });
    var sliders = h("div", { class: "plot-sliders" });
    var bar = h("div", { class: "plot-bar" }, [
      h("label", {}, ["variable ", varSel]),
      h("label", {}, ["from ", from]), h("label", {}, ["to ", to]), shown,
      h("label", { title: "Plot the selected piece of the formula; unticked, the whole expression" }, [follow, " follow the selection"])
    ]);
    var element = h("div", { class: "plot-panel" }, [bar, sliders, area, note]);

    var values = {};        // the values given to the other free symbols, by name (none until the user gives one)
    var seq = 0, timer = null, plotly = null, plotlyFailed = false;
    var lastVar = null;
    var sampled = null;     // [from, to] of the samples on show

    function fmt(v) { return Number(v).toPrecision(4).replace(/\.?0+$/, ""); }
    function showRange(a, b) { shown.textContent = a === null ? "" : "shown: " + fmt(a) + " \u2026 " + fmt(b); }

    function target() {
      if (!follow.checked) return { path: "/" };
      var r = api.range();
      if (r) {
        // The range's children, as the editor sends them with an edit.
        var ed = api.editor;
        return { path: r.parent, children: ed._rangeIndices() };
      }
      return { path: api.selected() || "/" };
    }

    function request() {
      clearTimeout(timer);
      timer = setTimeout(ask, 150);
    }

    function ask() {
      if (api.busy()) { request(); return; }     // after the edit in flight
      var my = ++seq;
      var t = target();
      var payload = { path: t.path, var: varSel.value || lastVar || null, values: values,
                      span: [parseFloat(from.value), parseFloat(to.value)], n: opts.samples || 400 };
      if (t.children) payload.children = t.children;
      if (!(payload.span[0] < payload.span[1])) payload.span = opts.span || [-6, 6];
      api.call("samples", payload).then(function (res) {
        if (my !== seq) return;
        fillVars(res);
        fillSliders(res);
        if (res.needs && res.needs.length) {
          // More than one free symbol and no value for the others: say so
          // and draw nothing, rather than guess.
          clearPlot();
          note.className = "plot-note error";
          note.textContent = res.src + " has " + res.free.length + " free symbols (" + res.free.join(", ") + "): "
            + res.var + " is on the axis; give a value to " + res.needs.join(", ") + " below, or pick another variable.";
          return;
        }
        draw(res);
      }, function (e) {
        if (my !== seq) return;
        note.textContent = String(e && e.message || e);
        note.className = "plot-note error";
      });
    }

    function fillVars(res) {
      var free = res.free || [];
      var current = res.var;
      lastVar = current;
      varSel.textContent = "";
      free.forEach(function (name) {
        var o = h("option", { value: name }, [name]);
        if (name === current) o.selected = true;
        varSel.appendChild(o);
      });
      varSel.disabled = free.length < 2;
    }

    function fillSliders(res) {
      var wanted = (res.free || []).filter(function (n) { return n !== res.var; });
      // A field and a slider per free symbol besides the axis: a value is
      // the user's to give (none is guessed); new symbols get an empty row,
      // vanished ones lose theirs, the rest keep their value.
      var seen = {};
      wanted.forEach(function (name) {
        seen[name] = true;
        var row = sliders.querySelector('[data-sym="' + name + '"]');
        if (row) return;
        var has = name in values;
        var num = h("input", { type: "text", inputmode: "decimal", class: "plot-num plot-value", placeholder: "value", title: "The value of " + name + " for the plot",
                               value: has ? String(values[name]) : "", spellcheck: "false", autocomplete: "off" });
        var range = h("input", { type: "range", min: "-3", max: "3", step: "0.05", value: has ? String(values[name]) : "0", title: "Slide to change " + name });
        var set = function (v) {
          if (!isFinite(v)) { delete values[name]; request(); return; }
          values[name] = v;
          request();
        };
        range.addEventListener("input", function () { num.value = range.value; set(parseFloat(range.value)); });
        num.addEventListener("input", function () { var v = parseFloat(num.value); if (isFinite(v)) range.value = String(Math.max(-3, Math.min(3, v))); set(v); });
        sliders.appendChild(h("label", { "data-sym": name, class: has ? "" : "plot-unset" }, [name + " = ", num, range]));
      });
      Array.prototype.slice.call(sliders.children).forEach(function (row) {
        var name = row.getAttribute("data-sym");
        if (!seen[name]) { sliders.removeChild(row); delete values[name]; }
      });
    }

    function clearPlot() {
      if (plotly && area.querySelector(".js-plotly-plot, .plot-container")) { try { plotly.purge(area); } catch (e) { /* ignore */ } }
      area.textContent = "";
      showRange(null);
    }

    function draw(res) {
      note.className = "plot-note";
      note.textContent = res.src + (res.curves.length > 1 ? "  (both sides)" : "");
      Array.prototype.slice.call(sliders.children).forEach(function (row) { row.classList.remove("plot-unset"); });
      var xs = res.x;
      if (!plotly && !plotlyFailed && opts.plotlyJs) {
        api.loadScript(opts.plotlyJs).then(function () {
          plotly = window.Plotly || null;
          if (!plotly) plotlyFailed = true;
          draw(res);
        }, function () { plotlyFailed = true; draw(res); });
        return;
      }
      if (plotly) {
        var traces = res.curves.map(function (c) {
          return { x: xs, y: c.y.map(function (v) { return v === null ? NaN : v; }), mode: "lines", name: c.label, connectgaps: false };
        });
        var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        plotly.react(area, traces, {
          margin: { l: 40, r: 10, t: 10, b: 30 }, showlegend: res.curves.length > 1,
          paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
          font: { color: dark ? "#e6e6e6" : "#1f2328", size: 11 },
          xaxis: { title: res.var, zeroline: true, gridcolor: dark ? "#333" : "#eee" },
          yaxis: { zeroline: true, gridcolor: dark ? "#333" : "#eee" }
        }, { responsive: true, displayModeBar: false }).then(listenZoom, function () { /* drawn or not, nothing to listen to */ });
        sampled = [xs[0], xs[xs.length - 1]];
        showRange(sampled[0], sampled[1]);
        return;
      }
      drawSvg(res);
      sampled = [xs[0], xs[xs.length - 1]];
      showRange(sampled[0], sampled[1]);
    }

    /** Once Plotly has drawn (its event API is on the element only then). */
    function listenZoom() {
      if (!area._seRelayout && typeof area.on === "function") {
          // A zoom or a pan in the picture (Plotly.react itself emits no
          // relayout): the fields take the range on show and the curve is
          // sampled again over it, so that zooming in brings detail rather
          // than stretching the same points.  A double-click resets to the
          // options' span.
          area._seRelayout = true;
          area.on("plotly_relayout", function (ev) {
            if (!ev) return;
            if (ev["xaxis.autorange"]) { from.value = String(opts.span ? opts.span[0] : -6); to.value = String(opts.span ? opts.span[1] : 6); request(); return; }
            var a = ev["xaxis.range[0]"], b = ev["xaxis.range[1]"];
            if (ev["xaxis.range"]) { a = ev["xaxis.range"][0]; b = ev["xaxis.range"][1]; }
            if (typeof a !== "number" || typeof b !== "number" || !(a < b)) return;
            if (sampled && Math.abs(a - sampled[0]) < 1e-12 && Math.abs(b - sampled[1]) < 1e-12) return;   // the range we drew
            from.value = fmt(a); to.value = fmt(b);
            showRange(a, b);
            request();
          });
      }
    }

    /** The fallback: axes and a polyline per curve, the vertical range from
     *  the bulk of the samples so that a pole does not flatten the rest. */
    function drawSvg(res) {
      var W = area.clientWidth || 500, H = area.clientHeight || 260;
      var xs = res.x, finite = [];
      res.curves.forEach(function (c) { c.y.forEach(function (v) { if (v !== null) finite.push(Math.abs(v)); }); });
      finite.sort(function (a, b) { return a - b; });
      var edge = finite.length ? (finite[Math.floor(finite.length * 0.98)] || 1) * 1.15 : 1;
      var x0 = xs[0], x1 = xs[xs.length - 1];
      var sx = function (v) { return (v - x0) / (x1 - x0) * (W - 20) + 10; };
      var sy = function (v) { return H / 2 - v / edge * (H / 2 - 10); };
      var ns = "http://www.w3.org/2000/svg";
      var svg = document.createElementNS(ns, "svg");
      svg.setAttribute("class", "plot-svg");
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      var axis = function (x1a, y1a, x2a, y2a) {
        var l = document.createElementNS(ns, "line");
        l.setAttribute("class", "axis"); l.setAttribute("x1", x1a); l.setAttribute("y1", y1a); l.setAttribute("x2", x2a); l.setAttribute("y2", y2a);
        svg.appendChild(l);
      };
      if (x0 <= 0 && x1 >= 0) axis(sx(0), 0, sx(0), H);
      axis(0, sy(0), W, sy(0));
      res.curves.forEach(function (c, i) {
        var d = "", pen = false;
        for (var k = 0; k < xs.length; k++) {
          var v = c.y[k];
          if (v === null || Math.abs(v) > edge * 4) { pen = false; continue; }
          d += (pen ? " L" : " M") + sx(xs[k]).toFixed(1) + " " + sy(v).toFixed(1);
          pen = true;
        }
        var p = document.createElementNS(ns, "path");
        p.setAttribute("class", "curve" + (i ? " second" : ""));
        p.setAttribute("d", d);
        svg.appendChild(p);
      });
      area.textContent = "";
      area.appendChild(svg);
    }

    varSel.addEventListener("change", request);
    from.addEventListener("change", request);
    to.addEventListener("change", request);
    follow.addEventListener("change", request);

    var HELP = [
      "<section><h3>What it draws</h3><ul>",
      "<li>The graph of the selected piece of the formula \u2014 the whole expression when nothing is selected, or when <i>follow the selection</i> is off.</li>",
      "<li>Python samples the function (<code>lambdify</code>; a value that is not a real number leaves a gap), and the curve is drawn by Plotly.js \u2014 by a plain SVG line when its CDN cannot be reached.</li>",
      "<li>An equation gives two curves, one per side.</li>",
      "</ul></section>",
      "<section><h3>Controls</h3><ul>",
      "<li><b>variable</b>: the symbol on the horizontal axis (the first free symbol to begin with); <b>from</b>/<b>to</b>: the span.</li>",
      "<li>With more than one free symbol nothing is drawn until the others have a value: each gets a field and a slider, and the value is substituted on the way to the plot \u2014 the formula stays symbolic. No value is ever guessed.</li>",
      "<li>Zoom or pan in the picture (drag a box, double-click to reset): the <b>from</b>/<b>to</b> fields take the range on show, <i>shown</i> reads it out, and the curve is sampled again over it \u2014 zooming in brings detail.</li>",
      "<li>The picture follows every committed change \u2014 an edit, a transformation, an undo \u2014 and the selection.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Plot",
      help: HELP,
      onState: function (snap) { if (!snap.preview) request(); },
      onSelect: function () { if (follow.checked) request(); },
      destroy: function () { clearTimeout(timer); seq++; }
    };
  }
});
