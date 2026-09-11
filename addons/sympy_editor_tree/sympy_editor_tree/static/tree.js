/*
 * sympy-editor add-on "tree": the expression's argument tree as a graph.
 *
 * A plain script: the editor runs it once per page with `SympyEditor` in
 * scope (SympyEditor.loadAddons), and it registers itself.  Each editor
 * whose document has the add-on calls mount(api) and shows the returned
 * element under the formula; onState draws the tree the snapshot carries
 * (snap.tree, from TreeAddon.contribute), onSelect follows the selection.
 *
 * No library: the layout is a plain tidy tree (each subtree as wide as its
 * children side by side, the parent centred over them) drawn in SVG.
 */
SympyEditor.registerAddon("tree", {
  mount: function (api) {
    var h = api.h;
    var NODE_H = 26, GAP_X = 10, GAP_Y = 34, PAD = 12, CHAR = 7.2;

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "tree-svg");
    var scroller = h("div", { class: "tree-scroll" }, [svg]);
    var note = h("div", { class: "tree-note", hidden: "" });
    var editField = h("input", { class: "tree-edit", type: "text", hidden: "", spellcheck: "false", autocomplete: "off" });

    // The panel's own tools: add an argument to the selected node, wrap it,
    // change its head.
    var heads = api.options.heads || [];
    var headSel = h("select", { class: "tree-head", title: "Change the head of the selected node" },
      [h("option", { value: "", disabled: "", selected: "" }, ["Head ▾"])].concat(
        heads.map(function (name) { return h("option", { value: name }, [name]); })));
    var argField = h("input", { type: "text", class: "tree-field", placeholder: "new argument…",
      title: "Add this as a last argument of the selected node (Enter)", spellcheck: "false", autocomplete: "off" });
    var wrapField = h("input", { type: "text", class: "tree-field", placeholder: "wrap in…",
      title: "Put the selected node inside this function (Enter)", spellcheck: "false", autocomplete: "off" });
    var nodeBtn = h("button", { type: "button", class: "tree-node-btn", title: "What can be done with the selected node: edit, delete, wrap, transform, its methods (also a right-click on a node)", disabled: "" }, ["Node \u25be"]);
    var hint = h("span", { class: "tree-hint" }, ["click: select \u00b7 double-click: edit \u00b7 right-click: menu \u00b7 drag onto a node: move \u00b7 Del: remove \u00b7 pinch or ctrl+wheel: zoom"]);
    var bar = h("div", { class: "tree-bar" }, [nodeBtn, headSel, argField, wrapField, hint]);
    var menu = h("div", { class: "tree-menu", hidden: "", role: "menu" });
    // The quick actions: a small bar under the clicked node with the few
    // things one does most; the "\u22ef" opens the full menu.
    var quick = h("div", { class: "tree-quick", hidden: "", role: "toolbar", "aria-label": "Node actions" });
    var element = h("div", { class: "tree-panel" }, [bar, scroller, note, editField, menu, quick]);

    /** A refused transformation: the error in the editor's line, and half
     *  a second of red flicker over the whole panel, so that it is not
     *  missed. */
    function flashError(text) {
      api.error(text);
      element.classList.remove("tree-flash");
      void element.offsetWidth;                          // restart the animation
      element.classList.add("tree-flash");
      setTimeout(function () { element.classList.remove("tree-flash"); }, 600);
    }

    var tree = null;       // the last snapshot's tree
    var nodes = [];        // laid-out nodes: {data, x, y, w, el}
    var focused = null;    // the node the tree itself has focused (argument path as "0/1")
    var drag = null;
    var zoom = 1;          // how much the drawing is magnified (see applyZoom)
    var natural = null;    // its size at zoom 1, in the units it is laid out in

    function key(path) { return path.join("/"); }
    function byKey(k) { for (var i = 0; i < nodes.length; i++) if (key(nodes[i].data.path) === k) return nodes[i]; return null; }
    function byView(view) {
      if (view === null || view === undefined) return null;
      for (var i = 0; i < nodes.length; i++) if (nodes[i].data.view === view) return nodes[i];
      return null;
    }
    function selectedNode() {
      var v = api.selected();
      // The formula's selection is a view path; a node under a fraction has
      // none in the argument tree - fall back to the focused node.
      return byView(v) || (focused ? byKey(focused) : null) || byKey("");
    }

    /* ---- layout ---- */

    function measure(d) {
      d._w = Math.max(30, d.label.length * CHAR + 16);
      var kids = d.children || [];
      var total = 0;
      for (var i = 0; i < kids.length; i++) { measure(kids[i]); total += kids[i]._span + (i ? GAP_X : 0); }
      d._span = Math.max(d._w, total);
    }
    function place(d, left, depth) {
      var kids = d.children || [];
      var total = 0;
      for (var i = 0; i < kids.length; i++) total += kids[i]._span + (i ? GAP_X : 0);
      var x = left + (d._span - total) / 2;
      for (var j = 0; j < kids.length; j++) { place(kids[j], x, depth + 1); x += kids[j]._span + GAP_X; }
      d._x = kids.length ? (kids[0]._cx + kids[kids.length - 1]._cx) / 2 - d._w / 2 : left + (d._span - d._w) / 2;
      d._cx = d._x + d._w / 2;
      d._y = PAD + depth * (NODE_H + GAP_Y);
    }

    function el(tag, attrs) {
      var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      return e;
    }

    /** A tree drawn small and still, for a step of the history: the nodes
     *  the previous step did not have (by source) are tinted as new. */
    function treeSvg(t, prev) {
      var had = {};
      (function collect(d) { had[d.src] = true; (d.children || []).forEach(collect); })(prev || { src: null, children: [] });
      var out = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      out.setAttribute("class", "tree-svg tree-small");
      if (!t || t.too_big) return out;
      var H = 20, GX = 6, GY = 22, P = 6, CH = 6.4;
      (function measure(d) {
        d._w = Math.max(22, d.label.length * CH + 12);
        var kids = d.children || [], total = 0;
        for (var i = 0; i < kids.length; i++) { measure(kids[i]); total += kids[i]._span + (i ? GX : 0); }
        d._span = Math.max(d._w, total);
      })(t);
      var depth = 0;
      (function place(d, left, level) {
        depth = Math.max(depth, level);
        var kids = d.children || [], total = 0;
        for (var i = 0; i < kids.length; i++) total += kids[i]._span + (i ? GX : 0);
        var x = left + (d._span - total) / 2;
        for (var j = 0; j < kids.length; j++) { place(kids[j], x, level + 1); x += kids[j]._span + GX; }
        d._x = kids.length ? (kids[0]._cx + kids[kids.length - 1]._cx) / 2 - d._w / 2 : left + (d._span - d._w) / 2;
        d._cx = d._x + d._w / 2;
        d._y = P + level * (H + GY);
      })(t, P, 0);
      var edges = el("g", { class: "tree-edges" }), boxes = el("g", { class: "tree-nodes" });
      out.appendChild(edges); out.appendChild(boxes);
      (function walk(d) {
        (d.children || []).forEach(function (k) {
          edges.appendChild(el("line", { x1: d._cx, y1: d._y + H, x2: k._cx, y2: k._y }));
          walk(k);
        });
        var g = el("g", { class: "tree-node " + (d.atom ? "tree-atom" : "tree-head-node") + (prev && !had[d.src] ? " tree-added" : ""),
                          transform: "translate(" + d._x + "," + d._y + ")" });
        g.appendChild(el("rect", { width: d._w, height: H, rx: 5, ry: 5 }));
        var tx = el("text", { x: d._w / 2, y: H / 2 + 3.5, "text-anchor": "middle" });
        tx.textContent = d.label;
        g.appendChild(tx);
        boxes.appendChild(g);
      })(t);
      out.setAttribute("width", String(t._span + 2 * P));
      out.setAttribute("height", String(P * 2 + (depth + 1) * H + depth * GY));
      out.setAttribute("viewBox", "0 0 " + (t._span + 2 * P) + " " + (P * 2 + (depth + 1) * H + depth * GY));
      return out;
    }

    /** The history's step: a collapsible box with the step's tree.
     *
     *  Shut to start with.  A history is read as a list of steps, and a tree
     *  opened beside every one of them buries that list - the trees are worth
     *  looking at one at a time, or all at once from the tools above, and
     *  either way it is the reader who asks. */
    function historyBox(step, prev) {
      if (!step || !step.tree) return null;
      var d = h("details", { class: "tree-history" }, [h("summary", {}, ["Expression tree"]),
        h("div", { class: "tree-history-scroll" }, [treeSvg(step.tree, prev && prev.tree)])]);
      if (step.tree.too_big) d.querySelector(".tree-history-scroll").textContent = "(too many nodes to draw)";
      return d;
    }

    // The report is a self-contained page without the add-on's stylesheet:
    // the tree's few rules go with the markup (kept plain: no CSS variables).
    var HISTORY_CSS = [
      ".tree-history { margin: 0.2rem 0; }",
      ".tree-history > summary { cursor: pointer; font-size: 0.8rem; color: #656d76; font-weight: 600; }",
      ".tree-history-scroll { overflow-x: auto; }",
      ".tree-history .tree-svg { display: block; font: 11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }",
      ".tree-history .tree-edges line { stroke: #656d76; stroke-width: 1; }",
      ".tree-history .tree-node rect { fill: #ffffff; stroke: #d0d7de; stroke-width: 1; }",
      ".tree-history .tree-node text { fill: #1f2328; }",
      ".tree-history .tree-head-node rect { fill: #e8effc; stroke: #9ab8f3; }",
      ".tree-history .tree-node.tree-added rect { fill: #e8f2eb; stroke: #1a7f37; stroke-width: 1.5; }",
      ".tree-history .tree-node.tree-added text { fill: #1a7f37; font-weight: bold; }",
      "@media (prefers-color-scheme: dark) { .tree-history .tree-node rect { fill: #1e1e1e; stroke: #444; } .tree-history .tree-node text { fill: #e6e6e6; }",
      "  .tree-history .tree-head-node rect { fill: #253044; stroke: #3b5a8a; } .tree-history .tree-node.tree-added rect { fill: #233726; stroke: #3fb950; }",
      "  .tree-history .tree-node.tree-added text { fill: #3fb950; } .tree-history .tree-edges line { stroke: #a0a0a0; } }"
    ].join("\n");

    function draw() {
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      nodes = [];
      if (!tree) return;
      if (tree.too_big) {
        note.textContent = "The tree has " + tree.too_big + " nodes or more; the graph stops at " + tree.max + ".";
        note.hidden = false;
        natural = null;
        scroller.style.maxHeight = "";
        svg.removeAttribute("viewBox");
        svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
        return;
      }
      note.hidden = true;
      measure(tree);
      place(tree, PAD, 0);
      var depth = 0;
      var edges = el("g", { class: "tree-edges" });
      var boxes = el("g", { class: "tree-nodes" });
      svg.appendChild(edges); svg.appendChild(boxes);
      (function walk(d, level) {
        depth = Math.max(depth, level);
        var kids = d.children || [];
        for (var i = 0; i < kids.length; i++) {
          edges.appendChild(el("line", { x1: d._cx, y1: d._y + NODE_H, x2: kids[i]._cx, y2: kids[i]._y }));
          walk(kids[i], level + 1);
        }
        var g = el("g", { class: "tree-node " + (d.atom ? "tree-atom" : "tree-head-node"), transform: "translate(" + d._x + "," + d._y + ")",
                          tabindex: "0", "data-key": key(d.path) });
        g.appendChild(el("rect", { width: d._w, height: NODE_H, rx: 6, ry: 6 }));
        var t = el("text", { x: d._w / 2, y: NODE_H / 2 + 4, "text-anchor": "middle" });
        t.textContent = d.label;
        g.appendChild(t);
        var title = el("title", {});
        title.textContent = d.src;
        g.appendChild(title);
        boxes.appendChild(g);
        nodes.push({ data: d, x: d._x, y: d._y, w: d._w, el: g });
      })(tree, 0);
      natural = { w: tree._span + 2 * PAD, h: PAD * 2 + (depth + 1) * NODE_H + depth * GAP_Y };
      applyZoom();
      markSelection();
    }

    function markSelection() {
      var sel = selectedNode();
      for (var i = 0; i < nodes.length; i++) {
        var on = sel && nodes[i] === sel;
        nodes[i].el.classList.toggle("tree-selected", !!on);
      }
      var n = sel ? sel.data : null;
      headSel.disabled = !n || n.atom;
      headSel.selectedIndex = 0;
      wrapField.disabled = !n;
      argField.disabled = !n;
      nodeBtn.disabled = !n;
    }

    /* ---- the node menu: everything that can be done with one node ---- */

    /** The view path the editor knows the node by - its own, or the
     *  nearest ancestor's (a node hidden inside a fraction). */
    function viewPathOf(n) {
      var k = n.data.path.slice();
      while (true) {
        var cand = byKey(k.join("/"));
        if (cand && cand.data.view) return cand.data.view;
        if (!k.length) return "/";
        k.pop();
      }
    }

    function hideMenu() { menu.hidden = true; menu.textContent = ""; }

    function showMenu(n, x, y) {
      hideMenu();
      hideQuick();
      selectNode(n);
      var d = n.data, view = viewPathOf(n), own = view === d.view;
      var item = function (label, fn, title) {
        var b = h("button", { type: "button", class: "tree-item", title: title || "" }, [label]);
        b.addEventListener("click", function () { hideMenu(); fn(); });
        menu.appendChild(b);
        return b;
      };
      var head = function (text) { menu.appendChild(h("div", { class: "tree-menu-head" }, [text])); };
      head((d.atom ? "Leaf " : d.head + " ") + d.src.slice(0, 40));
      item(d.atom ? "Edit value\u2026" : "Change head\u2026", function () { beginEdit(n); }, "Type over it (double-click does the same)");
      if (d.path.length) item("Delete", function () { call("delete", { path: d.path }); }, "Remove this node from its parent (Del)");
      item("Wrap in\u2026", function () { wrapField.focus(); }, "Put it inside a function: type the name in the field");
      if (!d.atom) item("Add argument\u2026", function () { argField.focus(); }, "Type a new argument in the field");
      // The editor's own tools for the same node: its transformations (by
      // kind) and the methods of its class, applied through the editor so
      // that parameters are asked for as usual.
      var state = api.state() || {};
      var node = own ? api.node(view) : null;
      if (node) {
        var kinds = node.kinds || [node.kind];
        var ops = (state.ops || []).filter(function (op) {
          return !op.kinds || op.kinds.some(function (k) { return kinds.indexOf(k) >= 0; });
        });
        if (ops.length) head("Transform");
        ops.forEach(function (op) {
          item(op.label || op.name, function () {
            if (op.params && op.params.length) api.editor._askOpParams(op, view, n.el);
            else api.send({ action: "apply", path: view, op: op.name, lazy: api.editor.lazy() });
          }, op.doc || "");
        });
        var methods = (api.editor._methodsCache || {})[node.type] || [];
        if (methods.length) head("Methods of " + node.type);
        methods.slice(0, 40).forEach(function (m) {
          item(m.label || (m.property ? "." + m.name : "." + m.name + "()"), function () { api.editor._pickFn("." + m.name); }, m.doc || "");
        });
      } else if (!own) {
        head("Shown as part of " + view + " in the formula: the tools apply there");
      }
      var host = element.getBoundingClientRect();
      menu.style.left = Math.max(0, x - host.left) + "px";
      menu.style.top = Math.max(0, y - host.top) + "px";
      menu.hidden = false;
      var first = menu.querySelector("button");
      if (first) first.focus({ preventScroll: true });
    }

    menu.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      var items = menu.querySelectorAll("button");
      var at = Array.prototype.indexOf.call(items, document.activeElement);
      if (ev.key === "Escape") { ev.preventDefault(); hideMenu(); }
      else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        var next = (at + (ev.key === "ArrowDown" ? 1 : items.length - 1) + items.length) % items.length;
        if (items[next]) items[next].focus({ preventScroll: true });
      }
    });
    document.addEventListener("pointerdown", function (ev) {
      if (!menu.hidden && !menu.contains(ev.target) && ev.target !== nodeBtn) hideMenu();
      if (!quick.hidden && !quick.contains(ev.target) && !svg.contains(ev.target)) hideQuick();
    });
    nodeBtn.addEventListener("click", function () {
      var n = selectedNode();
      if (!n) return;
      if (!menu.hidden) { hideMenu(); return; }
      var r = nodeBtn.getBoundingClientRect();
      showMenu(n, r.left, r.bottom + 4);
    });
    svg.addEventListener("contextmenu", function (ev) {
      var n = nodeOf(ev.target);
      if (!n) return;
      ev.preventDefault();
      showMenu(n, ev.clientX, ev.clientY);
    });

    /* ---- editing ---- */

    function nodeOf(target) {
      var g = target && target.closest ? target.closest(".tree-node") : null;
      return g ? byKey(g.getAttribute("data-key")) : null;
    }

    function selectNode(n) {
      focused = key(n.data.path);
      // Select the same piece in the formula when it has one; else the
      // nearest ancestor that does (the fraction the node is part of).
      var d = n.data, k = d.path.slice();
      while (true) {
        var cand = byKey(k.join("/"));
        if (cand && cand.data.view) { api.select(cand.data.view); break; }
        if (!k.length) break;
        k.pop();
      }
      markSelection();
      n.el.focus({ preventScroll: true });
    }

    function beginEdit(n) {
      var d = n.data;
      var box = n.el.getBoundingClientRect(), host = element.getBoundingClientRect();
      editField.value = d.atom ? d.src : d.head;
      editField.style.left = (box.left - host.left) + "px";
      editField.style.top = (box.top - host.top) + "px";
      editField.style.width = Math.max(box.width, 80) + "px";
      editField.hidden = false;
      editField.setAttribute("data-key", key(d.path));
      editField.focus();
      editField.select();
    }
    function endEdit(commit) {
      if (editField.hidden) return;
      var k = editField.getAttribute("data-key"), text = editField.value.trim();
      editField.hidden = true;
      var n = byKey(k);
      if (!commit || !n || !text) return;
      if (n.data.atom) call("replace", { path: n.data.path, src: text });
      else call("set_head", { path: n.data.path, head: text });
    }
    editField.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); endEdit(true); }
      else if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); endEdit(false); }
      else ev.stopPropagation();   // the editor's own keys must not see this typing
    });
    editField.addEventListener("blur", function () { setTimeout(function () { endEdit(true); }, 0); });

    function call(method, payload) {
      return api.call(method, payload).then(function () { api.status("Tree: " + method + " done"); },
                                            function (e) { flashError(String(e && e.message || e)); });
    }

    /* ---- the quick actions under a clicked node ---- */

    function hideQuick() { quick.hidden = true; quick.textContent = ""; }

    function showQuick(n) {
      hideQuick();
      var d = n.data;
      var btn = function (label, title, fn, disabled) {
        var b = h("button", { type: "button", title: title }, [label]);
        if (disabled) b.disabled = true;
        b.addEventListener("click", function (ev) { ev.stopPropagation(); fn(); });
        quick.appendChild(b);
        return b;
      };
      btn(d.atom ? "Edit" : "Head", d.atom ? "Type a new value over it" : "Type another head over it", function () { beginEdit(n); });
      btn("Delete", d.path.length ? (d.removable ? "Take this node out of its parent (Del)" : d.head + " cannot leave " + parentSrc(n) + ": it is needed there")
                                  : "The root cannot be deleted: type a new expression instead",
          function () { call("delete", { path: d.path }); }, !d.path.length || !d.removable);
      btn("Wrap", "Put it inside a function: type the name in the field", function () { wrapField.focus(); });
      if (!d.atom) btn("+ arg", "Add an argument: type it in the field", function () { argField.focus(); });
      btn("\u22ef", "Everything that can be done with this node: transformations, methods", function () {
        var r = n.el.getBoundingClientRect();
        showMenu(n, r.left, r.bottom + 4);
      });
      var box = n.el.getBoundingClientRect(), host = element.getBoundingClientRect();
      quick.hidden = false;
      quick.style.left = Math.max(0, Math.min(box.left - host.left, host.width - quick.offsetWidth - 4)) + "px";
      quick.style.top = (box.bottom - host.top + 3) + "px";
    }

    function parentSrc(n) {
      var p = byKey(n.data.path.slice(0, -1).join("/"));
      return p ? p.data.src.slice(0, 30) : "its parent";
    }

    /** Whether `from` may be dropped onto `over`, and why not. */
    function dropVerdict(from, over) {
      if (!over || over === from) return null;
      if (!from.data.path.length) return "The root cannot be moved";
      var fk = key(from.data.path), ok = key(over.data.path);
      if (ok === fk || ok.indexOf(fk + "/") === 0) return "A node cannot be moved into itself";
      if (!from.data.removable) return from.data.src.slice(0, 30) + " cannot be taken out of " + parentSrc(from) + ": it is needed there";
      if (over.data.atom) return over.data.src.slice(0, 30) + " is a leaf and takes no argument; drop onto an inner node";
      return "";                                        // allowed
    }

    svg.addEventListener("click", function (ev) {
      var n = nodeOf(ev.target);
      if (n) { selectNode(n); showQuick(n); } else hideQuick();
    });
    svg.addEventListener("dblclick", function (ev) {
      var n = nodeOf(ev.target);
      if (n) { ev.preventDefault(); beginEdit(n); }
    });
    svg.addEventListener("keydown", function (ev) {
      var n = nodeOf(document.activeElement);
      if (!n) return;
      if (ev.key === "Delete" || ev.key === "Backspace") {
        ev.preventDefault(); ev.stopPropagation();
        if (!n.data.path.length) flashError("The root cannot be deleted: type a new expression instead");
        else if (!n.data.removable) flashError(n.data.src.slice(0, 30) + " cannot be taken out of " + parentSrc(n) + ": it is needed there");
        else call("delete", { path: n.data.path });
      } else if (ev.key === "Enter" || ev.key === "F2") {
        ev.preventDefault(); ev.stopPropagation();
        beginEdit(n);
      } else if (ev.key === " ") {
        ev.preventDefault(); ev.stopPropagation();
        selectNode(n);
      }
    });

    /* ---- fingers, trackpad and mouse on the tree: pinch to zoom, drag to
     *      scroll ----
     *
     * The same gestures as the plot's picture, and taken the same way; what
     * differs is what they mean.  The plot has two axes of its own and scales
     * each by its own share of a pinch, so that a sideways pinch stretches the
     * span alone.  A tree is a drawing, not a pair of axes: stretching it
     * along one side would only distort it, so a pinch scales it evenly, by
     * how far the fingers move apart in any direction.
     *
     * Zooming is a viewBox and a size: the drawing keeps its own coordinates
     * (everything laid out and every position read off the screen goes on
     * working unchanged) and is drawn larger or smaller than them.  Scrolling
     * is then the scroll box's own, so a zoomed-in tree pans with one finger
     * as any overflowing box does - see touch-action in the CSS, which leaves
     * one finger to the browser and brings two here.
     *
     * The magnification stays across redraws: an edit should not throw away
     * the reader's place in a big tree.
     */
    var ZOOM_MIN = 0.25, ZOOM_MAX = 4;
    var SEPARATION = 24;   // px: fingers closer than this say nothing about scale
    var pinch = null;
    var panning = null;
    var frame = null;    // what the last move asked for, until the frame draws it
    var queued = false;

    /** Draw the tree at the current magnification.  The layout is untouched:
     *  the viewBox is its natural size and the element is that size times the
     *  zoom, so the browser does the scaling and every coordinate in this file
     *  stays in the units the layout produced. */
    function applyZoom() {
      if (!natural) return;
      svg.setAttribute("viewBox", "0 0 " + natural.w + " " + natural.h);
      svg.setAttribute("width", String(Math.round(natural.w * zoom)));
      svg.setAttribute("height", String(Math.round(natural.h * zoom)));
      // Magnified, the drawing is kept within the room it had at life size:
      // it then pans up and down inside the panel, rather than growing the
      // panel until the formula above it is off the screen.  At life size
      // the box is left alone, so an unzoomed tree shows as it always has.
      scroller.style.maxHeight = zoom > 1 ? Math.round(natural.h) + "px" : "";
    }

    function clampZoom(want) {
      if (!isFinite(want)) return zoom;
      return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, want));
    }

    /** Magnify to `want`, and scroll so that the point of the drawing at
     *  (`px`, `py`) - in the layout's own units - stays under (`clientX`,
     *  `clientY`) on the screen.  Absolute rather than step by step: a gesture
     *  works out where it started from and asks for that every time it moves,
     *  so nothing drifts however many moves it takes.
     *
     *  At most once a frame.  A finger sends moves faster than the tree can be
     *  laid out again, and only the last one before the frame is drawn. */
    function showAt(want, px, py, clientX, clientY) {
      frame = { zoom: clampZoom(want), px: px, py: py, x: clientX, y: clientY };
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () {
        var at = frame;
        frame = null;
        queued = false;
        if (!at || !natural) return;
        zoom = at.zoom;
        applyZoom();
        var box = scroller.getBoundingClientRect();
        scroller.scrollLeft = at.px * zoom - (at.x - box.left);
        scroller.scrollTop = at.py * zoom - (at.y - box.top);
      });
    }

    /** Where the point under (clientX, clientY) is in the drawing's own
     *  units - what has to be held still while the magnification changes. */
    function pointAt(clientX, clientY) {
      var box = scroller.getBoundingClientRect();
      return { x: (clientX - box.left + scroller.scrollLeft) / zoom,
               y: (clientY - box.top + scroller.scrollTop) / zoom };
    }

    /** A node being dragged onto another, given up: a second finger, or a
     *  redraw, means the drag is no longer what is happening.  Not endDrag -
     *  that one lets go of the subtree where it is. */
    function cancelDrag() {
      if (!drag) return;
      drag.from.el.classList.remove("tree-dragging");
      if (drag.over) drag.over.el.classList.remove("tree-drop", "tree-drop-no");
      drag = null;
    }

    scroller.addEventListener("touchstart", function (ev) {
      if (ev.touches.length !== 2) { pinch = null; return; }
      cancelDrag();                       // two fingers are a gesture, not a drag
      hideQuick(); hideMenu();
      var a = ev.touches[0], b = ev.touches[1];
      var mid = pointAt((a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2);
      pinch = { apart: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
                zoom: zoom, px: mid.x, py: mid.y };
      ev.preventDefault();
    }, { passive: false });

    scroller.addEventListener("touchmove", function (ev) {
      if (!pinch || ev.touches.length !== 2) return;
      ev.preventDefault();
      var a = ev.touches[0], b = ev.touches[1];
      var now = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      // Fingers barely apart tell us nothing about scale - the ratio of two
      // small numbers is noise - so they only push the tree along.
      var scale = (pinch.apart < SEPARATION || now < SEPARATION) ? 1 : now / pinch.apart;
      // The middle of the fingers carries the point it started on: this is
      // the pinch and the two-finger drag at once, in one sum.
      showAt(pinch.zoom * scale, pinch.px, pinch.py,
             (a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2);
    }, { passive: false });

    var endPinch = function (ev) {
      if (!ev.touches || ev.touches.length < 2) pinch = null;
    };
    scroller.addEventListener("touchend", endPinch, true);
    scroller.addEventListener("touchcancel", endPinch, true);

    /* A pinch on a laptop's trackpad reaches the page as a wheel event with
     * ctrlKey set - that is how the browser reports it, and how it would zoom
     * the whole page if nobody took it.  A plain wheel is left alone: it
     * scrolls the box, which is what a wheel over a tall drawing should do. */
    scroller.addEventListener("wheel", function (ev) {
      if (!natural || !(ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault();
      ev.stopPropagation();
      var unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 100 : 1;
      var at = pointAt(ev.clientX, ev.clientY);
      // From what the frame is already going to draw, not from what is on the
      // screen: a trackpad sends several of these between two frames, and
      // reading the drawn zoom each time would throw all but one of them away.
      showAt((frame ? frame.zoom : zoom) * Math.exp(-ev.deltaY * unit * 0.002),
             at.x, at.y, ev.clientX, ev.clientY);
    }, { passive: false });

    /* With a mouse there is no pinch and nothing to drag on empty space, so
     * that is where the tree is pushed along from - the scrollbars alone are
     * a poor way about a drawing wider than the panel.  A press on a node is
     * left to the drag that moves it. */
    scroller.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType === "touch") return;      // fingers: the gestures above
      if (ev.button !== 0 || nodeOf(ev.target)) return;
      panning = { x: ev.clientX, y: ev.clientY, id: ev.pointerId,
                  left: scroller.scrollLeft, top: scroller.scrollTop };
      try { scroller.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
      scroller.classList.add("tree-panning");
    });
    scroller.addEventListener("pointermove", function (ev) {
      if (!panning || ev.pointerId !== panning.id) return;
      scroller.scrollLeft = panning.left - (ev.clientX - panning.x);
      scroller.scrollTop = panning.top - (ev.clientY - panning.y);
    });
    var endPan = function () {
      if (!panning) return;
      try { scroller.releasePointerCapture(panning.id); } catch (e) { /* ignore */ }
      panning = null;
      scroller.classList.remove("tree-panning");
    };
    scroller.addEventListener("pointerup", endPan);
    scroller.addEventListener("pointercancel", endPan);

    /* Nothing else gets back to life size, so a double-click on empty space
     * does - the plot's double-click resets its span the same way. */
    scroller.addEventListener("dblclick", function (ev) {
      if (nodeOf(ev.target) || zoom === 1) return;   // on a node it opens the editor
      var at = pointAt(ev.clientX, ev.clientY);
      showAt(1, at.x, at.y, ev.clientX, ev.clientY);
    });

    // Drag a subtree onto another node: it becomes that node's last argument.
    svg.addEventListener("pointerdown", function (ev) {
      var n = nodeOf(ev.target);
      if (!n || !n.data.path.length || ev.button !== 0) return;
      drag = { from: n, x: ev.clientX, y: ev.clientY, moved: false, over: null, pointer: ev.pointerId };
    });
    svg.addEventListener("pointermove", function (ev) {
      if (!drag) return;
      if (!drag.moved && Math.abs(ev.clientX - drag.x) + Math.abs(ev.clientY - drag.y) < 5) return;
      if (!drag.moved) {
        // Capture only once it is a drag: captured from the start, the
        // click and double-click that follow a plain press would be
        // retargeted to the SVG and miss the node.
        try { svg.setPointerCapture(drag.pointer); } catch (e) { /* ignore */ }
      }
      if (!drag.moved) hideQuick();
      drag.moved = true;
      drag.from.el.classList.add("tree-dragging");
      var under = document.elementFromPoint(ev.clientX, ev.clientY);
      var over = nodeOf(under);
      if (drag.over && drag.over !== over) drag.over.el.classList.remove("tree-drop", "tree-drop-no");
      drag.over = over && over !== drag.from ? over : null;
      if (drag.over) {
        // Said before the drop: green where it may land, red where not.
        var why = dropVerdict(drag.from, drag.over);
        drag.over.el.classList.add(why ? "tree-drop-no" : "tree-drop");
      }
    });
    function endDrag(ev) {
      if (!drag) return;
      var d = drag; drag = null;
      d.from.el.classList.remove("tree-dragging");
      if (d.over) d.over.el.classList.remove("tree-drop", "tree-drop-no");
      if (!d.moved || !d.over) return;
      var why = dropVerdict(d.from, d.over);
      if (why) { flashError(why); return; }
      call("move", { from: d.from.data.path, to: d.over.data.path });
    }
    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", endDrag);

    headSel.addEventListener("change", function () {
      var n = selectedNode();
      if (n && headSel.value) call("set_head", { path: n.data.path, head: headSel.value });
      headSel.selectedIndex = 0;
    });
    function onEnter(field, method, name) {
      field.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        var n = selectedNode(), text = field.value.trim();
        if (!n || !text) return;
        var payload = { path: n.data.path };
        payload[name] = text;
        call(method, payload).then(function () { field.value = ""; });
      });
    }
    onEnter(argField, "insert", "src");
    onEnter(wrapField, "wrap", "head");

    var HELP = [
      "<section><h3>What it shows</h3><ul>",
      "<li>The expression as SymPy holds it: <code>x + y*z</code> is <b>Add</b> over <code>x</code> and <b>Mul</b>, <b>Mul</b> over <code>y</code> and <code>z</code>. Inner nodes carry the class (the head), leaves their value.</li>",
      "<li>The formula shows the printer's view: a fraction hides a <code>Pow(…, -1)</code>, a minus a <code>Mul(-1, …)</code>. Those nodes are here, but have no piece of their own in the formula: selecting one selects the nearest piece that is there.</li>",
      "</ul></section>",
      "<section><h3>Selecting</h3><ul>",
      "<li>Click a node to select the same piece in the formula (and the node lights up here when you select in the formula); a bar of quick actions appears under it: edit, delete, wrap, add an argument, and \u22ef for everything else.</li>",
      "<li><kbd>Space</kbd> selects the focused node, <kbd>Tab</kbd> moves between nodes.</li>",
      "</ul></section>",
      "<section><h3>In the history</h3><ul>",
      "<li>While this add-on is on, every step of the history \u2014 the drawer's list and the History view \u2014 carries the tree of its expression in a collapsible box \u2014 shut until you open it, so the list of steps stays readable \u2014 with the nodes the previous step did not have in green: how the tree evolved, step by step. The saved web page keeps them.</li>",
      "<li>A click on a box's heading folds or unfolds that tree (the step opens on a click elsewhere); <b>Expand trees</b> and <b>Collapse trees</b>, in the History view's strip and the drawer, do all of them at once.</li>",
      "</ul></section>",
      "<section><h3>Zoom and scroll</h3><ul>",
      "<li>Pinch with two fingers, or <kbd>Ctrl</kbd>+wheel (a pinch on a trackpad), to zoom the drawing; what is under the fingers or the pointer stays where it is. Only the tree zooms, never the page.</li>",
      "<li>Zoomed in, the drawing scrolls in its box: with one finger, the wheel, or a mouse drag on empty space.</li>",
      "<li>A double-click on empty space brings it back to life size.</li>",
      "</ul></section>",
      "<section><h3>Editing</h3><ul>",
      "<li>Double-click a node (or <kbd>Enter</kbd> on it, or <b>Edit</b>/<b>Head</b> in the bar under it) to type over it: a new value for a leaf, a new head for an inner node \u2014 <b>Mul</b> over the arguments of an <b>Add</b> turns the sum into a product.</li>",
      "<li>Right-click a node, or press <b>Node \u25be</b> for the selected one (or <b>\u22ef</b> in the bar under it, which is how a finger gets there): edit, delete, wrap, add an argument, then the editor's <b>Transform</b> entries for that kind of node and the <b>Methods</b> of its class.</li>",
      "<li>With a mouse, drag a subtree onto another node: it becomes that node's last argument (a finger on the tree scrolls it instead). While you drag, a node lights up green where the drop may land and red where it may not \u2014 a node that its parent needs (the x of sin(x), the base of a power) cannot be taken out, a leaf takes no argument, nothing goes into itself. <kbd>Del</kbd> removes the focused node.</li>",
      "<li>A transformation that is not allowed is refused: the error shows in the editor's line and the panel flickers red for half a second.</li>",
      "<li>The fields add an argument to the selected node or wrap it in a function; <b>Head \u25be</b> changes its head.</li>",
      "<li>Every change is a step of the editor's history: <kbd>Ctrl</kbd>+<kbd>Z</kbd> takes it back. SymPy evaluates as it does for any edit, so moving <code>y</code> under an <b>Add</b> of <code>x</code> gives <code>x + y</code>.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Expression tree",
      help: HELP,
      historyStep: function (step, i, prev) { return historyBox(step, prev); },
      historyStepHtml: function (step, i, prev) {
        var box = historyBox(step, prev);
        return box ? box.outerHTML : "";
      },
      historyCss: HISTORY_CSS,
      historyTools: function (target) {
        // Expand or collapse every tree of the history at once (the view's
        // strip, or the drawer's list).
        var all = function (open) {
          var root = target.getDoc() || target.root;
          if (!root) return;
          var boxes = root.querySelectorAll("details.tree-history");
          for (var i = 0; i < boxes.length; i++) boxes[i].open = open;
        };
        var expand = h("button", { type: "button", class: "se-addon-tool tree-expand-all", title: "Show the expression tree of every step" }, ["Expand trees"]);
        var collapse = h("button", { type: "button", class: "se-addon-tool tree-collapse-all", title: "Hide the expression trees" }, ["Collapse trees"]);
        expand.addEventListener("click", function () { all(true); });
        collapse.addEventListener("click", function () { all(false); });
        return [expand, collapse];
      },
      onState: function (snap) {
        if (snap.preview || !snap.tree) return;
        tree = snap.tree;
        endEdit(false);
        hideQuick();
        draw();
      },
      onSelect: function () { markSelection(); },
      destroy: function () { drag = null; hideMenu(); }
    };
  }
});
