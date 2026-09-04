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
    var from = h("input", { type: "number", value: String(opts.span ? opts.span[0] : -6), step: "any", title: "Left end of the axis" });
    var to = h("input", { type: "number", value: String(opts.span ? opts.span[1] : 6), step: "any", title: "Right end of the axis" });
    var follow = h("input", { type: "checkbox", checked: "" });
    var sliders = h("div", { class: "plot-sliders" });
    var bar = h("div", { class: "plot-bar" }, [
      h("label", {}, ["variable ", varSel]),
      h("label", {}, ["from ", from]), h("label", {}, ["to ", to]),
      h("label", { title: "Plot the selected piece of the formula; unticked, the whole expression" }, [follow, " follow the selection"])
    ]);
    var element = h("div", { class: "plot-panel" }, [bar, sliders, area, note]);

    var values = {};        // slider values by symbol name
    var seq = 0, timer = null, plotly = null, plotlyFailed = false;
    var lastVar = null;

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
        if (res.needs && res.needs.length) { ask(); return; }    // the sliders now have values: again
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
      // One slider per free symbol besides the axis: new ones appear with a
      // value of 1, vanished ones go, the rest keep their value.
      var seen = {};
      wanted.forEach(function (name) {
        seen[name] = true;
        if (!(name in values)) values[name] = 1;
        var row = sliders.querySelector('[data-sym="' + name + '"]');
        if (row) return;
        var out = h("output", {}, [String(values[name])]);
        var range = h("input", { type: "range", min: "-3", max: "3", step: "0.05", value: String(values[name]) });
        range.addEventListener("input", function () {
          values[name] = parseFloat(range.value);
          out.textContent = String(values[name]);
          request();
        });
        sliders.appendChild(h("label", { "data-sym": name }, [name + " ", range, out]));
      });
      Array.prototype.slice.call(sliders.children).forEach(function (row) {
        var name = row.getAttribute("data-sym");
        if (!seen[name]) { sliders.removeChild(row); delete values[name]; }
      });
    }

    function draw(res) {
      note.className = "plot-note";
      note.textContent = res.src + (res.curves.length > 1 ? "  (both sides)" : "");
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
        }, { responsive: true, displayModeBar: false });
        return;
      }
      drawSvg(res);
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

    return {
      element: element,
      title: "Plot",
      onState: function (snap) { if (!snap.preview) request(); },
      onSelect: function () { if (follow.checked) request(); },
      destroy: function () { clearTimeout(timer); seq++; }
    };
  }
});
