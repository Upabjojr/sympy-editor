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
    var hint = h("span", { class: "tree-hint" }, ["click: select \u00b7 double-click: edit \u00b7 right-click: menu \u00b7 drag onto a node: move \u00b7 Del: remove"]);
    var bar = h("div", { class: "tree-bar" }, [nodeBtn, headSel, argField, wrapField, hint]);
    var menu = h("div", { class: "tree-menu", hidden: "", role: "menu" });
    var element = h("div", { class: "tree-panel" }, [bar, scroller, note, editField, menu]);

    var tree = null;       // the last snapshot's tree
    var nodes = [];        // laid-out nodes: {data, x, y, w, el}
    var focused = null;    // the node the tree itself has focused (argument path as "0/1")
    var drag = null;

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

    function draw() {
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      nodes = [];
      if (!tree) return;
      if (tree.too_big) {
        note.textContent = "The tree has " + tree.too_big + " nodes or more; the graph stops at " + tree.max + ".";
        note.hidden = false;
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
      svg.setAttribute("width", String(tree._span + 2 * PAD));
      svg.setAttribute("height", String(PAD * 2 + (depth + 1) * NODE_H + depth * GAP_Y));
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
                                            function (e) { api.error(String(e && e.message || e)); });
    }

    svg.addEventListener("click", function (ev) {
      var n = nodeOf(ev.target);
      if (n) selectNode(n);
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
        if (n.data.path.length) call("delete", { path: n.data.path });
      } else if (ev.key === "Enter" || ev.key === "F2") {
        ev.preventDefault(); ev.stopPropagation();
        beginEdit(n);
      } else if (ev.key === " ") {
        ev.preventDefault(); ev.stopPropagation();
        selectNode(n);
      }
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
      drag.moved = true;
      drag.from.el.classList.add("tree-dragging");
      var under = document.elementFromPoint(ev.clientX, ev.clientY);
      var over = nodeOf(under);
      if (drag.over && drag.over !== over) drag.over.el.classList.remove("tree-drop");
      drag.over = over && over !== drag.from && !over.data.atom && key(over.data.path).indexOf(key(drag.from.data.path)) !== 0 ? over : null;
      if (drag.over) drag.over.el.classList.add("tree-drop");
    });
    function endDrag(ev) {
      if (!drag) return;
      var d = drag; drag = null;
      d.from.el.classList.remove("tree-dragging");
      if (d.over) d.over.el.classList.remove("tree-drop");
      if (d.moved && d.over) call("move", { from: d.from.data.path, to: d.over.data.path });
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

    return {
      element: element,
      title: "Expression tree",
      onState: function (snap) {
        if (snap.preview || !snap.tree) return;
        tree = snap.tree;
        endEdit(false);
        draw();
      },
      onSelect: function () { markSelection(); },
      destroy: function () { drag = null; hideMenu(); }
    };
  }
});
