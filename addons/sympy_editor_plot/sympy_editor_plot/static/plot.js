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
    // browsers.  And the ordinary keyboard, not a numeric one: on iOS and
    // Android the decimal and numeric pads have no minus sign, and the
    // numbers typed here are often negative ("from" is, to begin with).
    // Nothing asks a phone for digits and a minus alone; the ordinary
    // keyboard has both one tap away (123 / ?123).
    var numField = function (value, title) {
      return h("input", { type: "text", autocapitalize: "off", autocorrect: "off", class: "plot-num", value: String(value), title: title, spellcheck: "false", autocomplete: "off" });
    };
    var from = numField(opts.span ? opts.span[0] : -6, "Left end of the axis (a zoom in the picture changes it too)");
    var to = numField(opts.span ? opts.span[1] : 6, "Right end of the axis (a zoom in the picture changes it too)");
    var follow = h("input", { type: "checkbox", checked: "" });
    var sliders = h("div", { class: "plot-sliders" });
    var bar = h("div", { class: "plot-bar" }, [
      h("label", {}, ["variable ", varSel]),
      h("label", {}, ["from ", from]), h("label", {}, ["to ", to])
    ]);
    // A line of its own.  Beside the fields it wrapped onto the next line and
    // back whenever the bar's width changed - and the width changed on every
    // zoom while a readout of the visible range sat in the bar - so the
    // picture underneath jumped up and down under the fingers.  The readout
    // is gone too: the from/to fields already follow a zoom or a pan.
    var followRow = h("div", { class: "plot-follow" }, [
      h("label", { title: "Plot the selected piece of the formula; unticked, the whole expression" }, [follow, " follow the selection"])
    ]);
    var element = h("div", { class: "plot-panel" }, [bar, followRow, sliders, area, note]);

    var values = {};        // the values given to the other free symbols, by name (none until the user gives one)
    var seq = 0, timer = null, plotly = null, plotlyFailed = false;
    var lastVar = null;
    var sampled = null;     // [from, to] of the samples on show
    var yRange = null;      // [low, high] once a gesture has set one: y is otherwise
                            // read off the curve, and would spring back on every draw

    /* ---- keeping the picture from eating the machine ----
     *
     * Sampling a function is Python's work and can be slow - an integral, a
     * big expression, a phone - while a gesture asks for a new range many
     * times a second.  Three things keep that in hand: only one sampling is
     * ever in flight, the redraws a gesture asks for are collected into one
     * a frame, and how long the last sampling took decides how many points
     * the next one gets.  When even a small sampling stays slow the picture
     * stops following by itself and says so, rather than locking up. */
    var inFlight = false;   // a sampling is out; the next one waits for it
    var wanted = false;     // ... and one is waiting
    var samples = 0;        // points asked for last time (0: the option's own number)
    var slowRuns = 0;       // samplings in a row that took longer than the budget
    var paused = false;     // the picture has stopped following, until asked again
    var frame = null;       // the relayout a gesture asked for, waiting for a frame
    var SLOW = 900;         // ms: over budget, so fewer points next time
    var QUICK = 250;        // ms: room to spare, so more of them again
    var STALL = 3500;       // ms: too slow to keep doing by itself
    var FEWEST = 60;        // points: below this the curve is not worth drawing

    function fmt(v) { return Number(v).toPrecision(4).replace(/\.?0+$/, ""); }

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
      if (paused) return;                        // stopped following: only an explicit ask draws now
      if (inFlight) { wanted = true; return; }   // one at a time, and only the latest is wanted
      inFlight = true;
      var began = Date.now();
      var my = ++seq;
      var t = target();
      var payload = { path: t.path, var: varSel.value || lastVar || null, values: values,
                      span: [parseFloat(from.value), parseFloat(to.value)], n: samples || opts.samples || 400 };
      if (t.children) payload.children = t.children;
      if (!(payload.span[0] < payload.span[1])) payload.span = opts.span || [-6, 6];
      api.call("samples", payload).then(function (res) {
        var giveUp = settle(began);
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
        if (giveUp) pause();          // after the draw: the note it writes is the last word
      }, function (e) {
        settle(began);
        if (my !== seq) return;
        note.textContent = String(e && e.message || e);
        note.className = "plot-note error";
      });
    }

    /** A sampling has come back: let the next one go, and let how long this
     *  one took decide how big it is - fewer points while it is slow, more
     *  again once there is room.  Two slow ones in a row at the fewest points
     *  we would draw, and the picture stops following on its own. */
    function settle(began) {
      var took = Date.now() - began;
      var was = samples || opts.samples || 400;
      inFlight = false;
      if (took > SLOW) {
        samples = Math.max(FEWEST, Math.round(was / 2));
        if (took > STALL) slowRuns += 1;
        if (slowRuns >= 2 && samples <= FEWEST) return true;   // the caller pauses, after it has drawn
      } else if (took < QUICK) {
        slowRuns = 0;
        samples = Math.min(opts.samples || 400, Math.round(was * 1.5) || (opts.samples || 400));
      }
      if (wanted) { wanted = false; request(); }
      return false;
    }

    /** Stop following by itself, and say so where the reason belongs. */
    function pause() {
      paused = true;
      wanted = false;
      note.className = "plot-note plot-paused";
      note.textContent = "";
      note.appendChild(document.createTextNode("The picture has stopped following: sampling this took too long. "));
      var again = h("button", { type: "button", class: "plot-again" }, ["Draw it again"]);
      again.addEventListener("click", function () { resume(); });
      note.appendChild(again);
    }

    function resume() {
      paused = false;
      slowRuns = 0;
      samples = FEWEST;                          // start small; settle() grows it back
      note.className = "plot-note";
      note.textContent = "";
      request();
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
        var num = h("input", { type: "text", autocapitalize: "off", autocorrect: "off", class: "plot-num plot-value", placeholder: "value", title: "The value of " + name + " for the plot",
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
      // purge takes Plotly's event API off the element, our zoom listener
      // with it: the next draw registers it again (listenZoom).
      if (plotly && area.querySelector(".js-plotly-plot, .plot-container")) { try { plotly.purge(area); } catch (e) { /* ignore */ } }
      area._seRelayout = false;
      area.textContent = "";
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
          // A drag moves the picture, the way a finger does: zooming is the
          // wheel, a trackpad pinch, two fingers, or the from/to fields, and
          // a double-click comes back to the whole thing.
          dragmode: "pan",
          margin: { l: 40, r: 10, t: 10, b: 30 }, showlegend: res.curves.length > 1,
          paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
          font: { color: dark ? "#e6e6e6" : "#1f2328", size: 11 },
          xaxis: { title: res.var, zeroline: true, gridcolor: dark ? "#333" : "#eee" },
          yaxis: yRange
            ? { zeroline: true, gridcolor: dark ? "#333" : "#eee", range: [yRange[0], yRange[1]], autorange: false }
            : { zeroline: true, gridcolor: dark ? "#333" : "#eee" }
        }, { responsive: true, displayModeBar: false, scrollZoom: true }).then(listenZoom, function () { /* drawn or not, nothing to listen to */ });
        sampled = [xs[0], xs[xs.length - 1]];
        return;
      }
      drawSvg(res);
      sampled = [xs[0], xs[xs.length - 1]];
    }


    /* ---- fingers on the picture: pinch to zoom, drag to scroll ----
     *
     * Plotly's own touch handling reads a drag as the box zoom it uses for a
     * mouse: two fingers landed the range wherever they finished rather than
     * around what they were holding, and one finger drew a zoom box where a
     * finger on a picture is expected to push it along.  Both gestures are
     * taken here instead, before Plotly sees them.
     *
     * A pinch scales the span by how far the fingers move apart and keeps
     * what is under the middle of them where it is.  A drag sideways moves
     * the span along under the finger, so the curve follows it exactly.
     *
     * The span alone is changed, never the vertical axis: y is read off the
     * curve every time it is sampled, so anything set for it would be gone
     * by the next draw.  That is also why a drag up or down is left to the
     * browser - it scrolls the page, as it does everywhere else.
     */
    var pinch = null;
    var drag = null;
    var DRAG_SLOP = 8;      // px of movement before a drag is one
    var SEPARATION = 24;    // px: below this the fingers say nothing about that axis

    function separation(a, b) {
      return { x: Math.abs(a.clientX - b.clientX), y: Math.abs(a.clientY - b.clientY) };
    }

    /** How much an axis is scaled by fingers that started `was` apart along
     *  it and are now `now` apart.  A pinch along the picture says nothing
     *  about the other axis - the fingers barely separate across it, and the
     *  ratio of two small numbers is noise - so a separation under a couple
     *  of dozen pixels leaves that axis alone.  This is what makes a
     *  sideways pinch zoom the span, an upright one the height, and a
     *  diagonal one both, each by its own share. */
    function axisScale(was, now) {
      if (was < SEPARATION || now < SEPARATION) return 1;
      return was / now;
    }

    /** Where a point on the screen falls along the axis: 0 at its left end,
     *  1 at its right.  Plotly puts the axis's own offset and length on the
     *  layout; without them (an SVG fallback) the box is close enough. */
    function axisFraction(clientX) {
      var box = area.getBoundingClientRect();
      var ax = area._fullLayout && area._fullLayout.xaxis;
      var left = box.left + (ax && typeof ax._offset === "number" ? ax._offset : 0);
      var width = ax && ax._length ? ax._length : box.width;
      if (!width) return 0.5;
      return Math.min(1, Math.max(0, (clientX - left) / width));
    }

    /** Move the axes, at most once a frame.  A finger sends moves faster than
     *  the picture can be redrawn, and asking Plotly for each of them is what
     *  makes a gesture stutter; the last one before the frame is the only one
     *  that matters anyway. */
    function moveAxes(change) {
      if (!change || (!change["xaxis.range"] && !change["yaxis.range"])) return;
      frame = frame || {};
      for (var k in change) frame[k] = change[k];
      if (frame.__queued) return;
      frame.__queued = true;
      requestAnimationFrame(function () {
        var pending = frame;
        frame = null;
        if (!plotly || !pending) return;
        delete pending.__queued;
        plotly.relayout(area, pending);
      });
    }

    /** Room to move, no room to break the axis. */
    function clampSpan(want, was) {
      var limit = Math.abs(was) || 1;
      return Math.min(Math.max(want, limit * 1e-4), limit * 1e4);
    }

    /** The axis's width in pixels (its own, not the panel's). */
    function axisLength() {
      var ax = area._fullLayout && area._fullLayout.xaxis;
      if (ax && ax._length) return ax._length;
      var box = area.getBoundingClientRect();
      return box.width || 0;
    }

    function currentRange() {
      var ax = area._fullLayout && area._fullLayout.xaxis;
      if (ax && ax.range && ax.range.length === 2 && ax.range[0] < ax.range[1]) return [ax.range[0], ax.range[1]];
      var a = parseFloat(from.value), b = parseFloat(to.value);
      return (a < b) ? [a, b] : (opts.span || [-6, 6]);
    }

    /** The height on show, as the axis has it. */
    function currentHeight() {
      var ay = area._fullLayout && area._fullLayout.yaxis;
      if (ay && ay.range && ay.range.length === 2 && ay.range[0] < ay.range[1]) return [ay.range[0], ay.range[1]];
      return yRange;
    }

    function axisHeight() {
      var ay = area._fullLayout && area._fullLayout.yaxis;
      if (ay && ay._length) return ay._length;
      var box = area.getBoundingClientRect();
      return box.height || 0;
    }

    /** Where a point on the screen falls up the axis: 0 at the bottom, 1 at
     *  the top (the screen counts downwards, the axis upwards). */
    function heightFraction(clientY) {
      var box = area.getBoundingClientRect();
      var ay = area._fullLayout && area._fullLayout.yaxis;
      var top = box.top + (ay && typeof ay._offset === "number" ? ay._offset : 0);
      var length = ay && ay._length ? ay._length : box.height;
      if (!length) return 0.5;
      return 1 - Math.min(1, Math.max(0, (clientY - top) / length));
    }

    // Caught on the way down, and stopped there: Plotly reads a two-finger
    // drag as the box zoom it uses for a mouse, and would undo this on the
    // same gesture.  One finger is left alone, so its own pan still works.
    area.addEventListener("touchstart", function (ev) {
      if (plotly && ev.touches.length === 1) {
        // not a drag yet: which way the finger goes decides, so that a
        // scroll down the page over the picture still scrolls the page.
        // Stopped here all the same, without preventing the default: Plotly
        // would otherwise read the drag as its mouse zoom box and pull the
        // range about on a gesture meant for the page.  Not preventing the
        // default is what leaves the page free to scroll.
        drag = { x: ev.touches[0].clientX, y: ev.touches[0].clientY,
                 range: currentRange(), height: currentHeight(), moving: false };
        ev.stopPropagation();
      }
      if (!plotly || ev.touches.length !== 2) { pinch = null; return; }
      drag = null;
      var apart = separation(ev.touches[0], ev.touches[1]);
      pinch = {
        apart: apart,
        range: currentRange(),
        height: currentHeight(),
        fraction: axisFraction((ev.touches[0].clientX + ev.touches[1].clientX) / 2),
        up: heightFraction((ev.touches[0].clientY + ev.touches[1].clientY) / 2)
      };
      ev.preventDefault();
      ev.stopPropagation();
    }, true);

    area.addEventListener("touchmove", function (ev) {
      if (drag && !pinch && ev.touches.length === 1) {
        var dx = ev.touches[0].clientX - drag.x, dy = ev.touches[0].clientY - drag.y;
        if (!drag.moving) {
          if (Math.abs(dx) < DRAG_SLOP && Math.abs(dy) < DRAG_SLOP) return;   // too early to say
          drag.moving = true;
        }
        ev.preventDefault();
        ev.stopPropagation();
        var moved = {};
        var length = axisLength();
        if (length) {
          var wide = drag.range[1] - drag.range[0];
          var by = -(dx / length) * wide;                   // the picture goes with the finger
          moved["xaxis.range"] = [drag.range[0] + by, drag.range[1] + by];
        }
        var tall = axisHeight();
        var height = drag.height || currentHeight();
        if (tall && height) {
          var reach = height[1] - height[0];
          var up = (dy / tall) * reach;                     // the screen counts down, the axis up
          yRange = [height[0] + up, height[1] + up];
          moved["yaxis.range"] = yRange.slice();
          moved["yaxis.autorange"] = false;
        }
        moveAxes(moved);
        return;
      }
      if (!pinch || ev.touches.length !== 2) return;
      var now = separation(ev.touches[0], ev.touches[1]);
      ev.preventDefault();
      ev.stopPropagation();
      var change = {};
      // Each axis takes the share the fingers moved along it: sideways for
      // the span, up and down for the height, both for a pinch across the
      // corner.  What is under the middle of the pinch stays where it is.
      var sx = axisScale(pinch.apart.x, now.x);
      if (sx !== 1) {
        var wide = pinch.range[1] - pinch.range[0];
        var span = clampSpan(wide * sx, wide);
        var heldX = pinch.range[0] + pinch.fraction * wide;
        var lo = heldX - pinch.fraction * span;
        if (isFinite(lo) && isFinite(lo + span)) change["xaxis.range"] = [lo, lo + span];
      }
      var height = pinch.height || currentHeight();
      var sy = axisScale(pinch.apart.y, now.y);
      if (sy !== 1 && height) {
        var tall = height[1] - height[0];
        var reach = clampSpan(tall * sy, tall);
        var heldY = height[0] + pinch.up * tall;
        var bottom = heldY - pinch.up * reach;
        if (isFinite(bottom) && isFinite(bottom + reach)) {
          yRange = [bottom, bottom + reach];        // kept, so the next draw does not undo it
          change["yaxis.range"] = yRange.slice();
          change["yaxis.autorange"] = false;
        }
      }
      // the relayout tells the panel, which writes the fields and asks for
      // samples over the new span (that request is debounced, so a pinch
      // makes one of them, not one per frame)
      moveAxes(change);
    }, true);

    /* A pinch on a laptop's trackpad reaches the page as a wheel event with
     * ctrlKey set - that is how the browser reports it, and how it would zoom
     * the page if nobody took it.  Plotly's own wheel zoom ignores it (it
     * wants a plain wheel), so it is taken here and zooms both axes about the
     * pointer, the way two fingers on a screen do. */
    area.addEventListener("wheel", function (ev) {
      if (!plotly || !(ev.ctrlKey || ev.metaKey)) return;      // a plain wheel is Plotly's own zoom
      ev.preventDefault();
      ev.stopPropagation();
      var unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 100 : 1;
      var scale = Math.exp(ev.deltaY * unit * 0.002);          // away from you: a wider view
      var change = {};
      var r = currentRange(), wide = r[1] - r[0];
      var fx = axisFraction(ev.clientX);
      var span = clampSpan(wide * scale, wide);
      var lo = (r[0] + fx * wide) - fx * span;
      if (isFinite(lo) && isFinite(lo + span)) change["xaxis.range"] = [lo, lo + span];
      var height = currentHeight();
      if (height) {
        var tall = height[1] - height[0], fy = heightFraction(ev.clientY);
        var reach = clampSpan(tall * scale, tall);
        var bottom = (height[0] + fy * tall) - fy * reach;
        if (isFinite(bottom) && isFinite(bottom + reach)) {
          yRange = [bottom, bottom + reach];
          change["yaxis.range"] = yRange.slice();
          change["yaxis.autorange"] = false;
        }
      }
      moveAxes(change);
    }, { passive: false });

    var endPinch = function (ev) {
      if (!ev.touches || ev.touches.length < 2) pinch = null;
      if (!ev.touches || !ev.touches.length) drag = null;
    };
    area.addEventListener("touchend", endPinch, true);
    area.addEventListener("touchcancel", endPinch, true);

    /** A zoom or a pan in the picture (Plotly.react itself emits no
     *  relayout): the fields take the visible range and the curve is
     *  sampled again over it, so that zooming in brings detail rather than
     *  stretching the same points.  A double-click resets to the options'
     *  span. */
    function onRelayout(ev) {
      if (!ev) return;
      if (ev["yaxis.autorange"]) yRange = null;                       // back to the curve's own height
      if (ev["xaxis.autorange"]) { yRange = null; from.value = String(opts.span ? opts.span[0] : -6); to.value = String(opts.span ? opts.span[1] : 6); request(); return; }
      var ylo = ev["yaxis.range[0]"], yhi = ev["yaxis.range[1]"];
      if (ev["yaxis.range"]) { ylo = ev["yaxis.range"][0]; yhi = ev["yaxis.range"][1]; }
      if (typeof ylo === "number" && typeof yhi === "number" && ylo < yhi) yRange = [ylo, yhi];
      var a = ev["xaxis.range[0]"], b = ev["xaxis.range[1]"];
      if (ev["xaxis.range"]) { a = ev["xaxis.range"][0]; b = ev["xaxis.range"][1]; }
      if (typeof a !== "number" || typeof b !== "number" || !(a < b)) return;
      if (sampled && Math.abs(a - sampled[0]) < 1e-12 && Math.abs(b - sampled[1]) < 1e-12) return;   // the range we drew
      from.value = fmt(a); to.value = fmt(b);
      request();
    }

    /** After every draw (Plotly's event API is on the element only once it
     *  has drawn, and a purge takes it off again): listen for zooms, once. */
    function listenZoom() {
      if (typeof area.on !== "function") return;
      if (typeof area.removeListener === "function") area.removeListener("plotly_relayout", onRelayout);
      area.on("plotly_relayout", onRelayout);
      area._seRelayout = true;
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
      "<li><b>Drag</b> in the picture to move it \u2014 with the mouse or a finger, in either direction \u2014 and <b>turn the wheel</b> over it to zoom; double-click to come back to the whole thing. The <b>from</b>/<b>to</b> fields take the visible range, and the curve is sampled again over it \u2014 zooming in brings detail.</li>",
      "<li>On a touch screen, <b>pinch with two fingers</b> to zoom: apart for a closer look, together to come back out. Each axis takes the share the fingers moved along it \u2014 sideways for the span, up and down for the height, both for a pinch across the corner \u2014 and what is under the middle of the pinch stays where it is.</li>",
      "<li><b>Drag with one finger</b> to move the picture, in either direction: the span sideways, the height up and down.</li>",
      "<li>On a laptop, a <b>pinch on the trackpad</b> zooms both axes about the pointer. Double-click to come back to the whole picture.</li>",
      "<li>Sampling a function is Python's work, and some are slow. Only one sampling is ever out at a time, a gesture is drawn once a frame however fast the finger moves, and a slow function is given fewer points until it keeps up. If it stays too slow the picture stops following and offers to draw again \u2014 the axes still move, the curve is simply not sampled afresh until you ask.</li>",
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
