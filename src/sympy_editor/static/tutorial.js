/*
 * sympy-editor tutorial player: plays a script of timed steps on an editor -
 * captions, an arrow and a pulsing ring on what is about to be pressed, the
 * press itself - so that a page can be watched, or recorded, as a tutorial.
 *
 * Nothing in the editor knows about it and nothing in its interface starts
 * it: a page plays a script only when it includes this file and calls
 *
 *     SympyEditorTutorial.run(editorOrItsElement, script)      // -> a Player
 *
 * sympy_editor.tutorial builds such pages (and fragments to embed) from a
 * JSON script, and documents every kind of step.  In short: {"steps": [{"at":
 * 0, "caption": "..."}, {"after": 2, "click": {"path": "/1"}, "say": "..."}]}.
 *
 * The player drives the editor the way a person would - it presses the real
 * buttons and types into the real fields - so what the video shows is what a
 * reader will find.  The clock starts once the formula is drawn and Python
 * is ready, and a step never starts before its time, nor while Python is
 * still working on the one before: the editor would drop it.  When the script
 * is over, everything of the player goes and the editor is left as it is.
 */
(function () {
  "use strict";
  if (window.SympyEditorTutorial) return;

  var LEAD = 1.2;      // seconds of arrow and ring before a press
  var GAP = 1.0;       // seconds after the previous step, when a step says neither "at" nor "after"
  var PER_CHAR = 0.07; // seconds per character typed
  var FIND = 6;        // seconds to wait for a target to appear (a menu opening, a panel drawn)
  var LAST = 2.5;      // seconds the last caption stays, when it does not say how long
  var MARGIN = 12;     // px kept between a caption and the edges, the target, the arrow

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, Math.max(0, ms)); }); }
  function make(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }
  function editorOf(thing) {
    if (!thing) thing = document.querySelector(".sympy-editor");
    if (thing && thing.root && thing.send) return thing;                 // an Editor already
    if (thing && thing.__sympyEditor) return thing.__sympyEditor;
    var root = thing && thing.querySelector && thing.querySelector(".sympy-editor");
    return root ? root.__sympyEditor : null;
  }
  function visible(node) {
    if (!node || !node.getBoundingClientRect) return false;
    var r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(node).visibility !== "hidden";
  }
  /** Where to aim at `node`: the element - or its content, when that is much
   *  narrower (a row of a menu, a checkbox and its label), so that the ring
   *  goes round what is read rather than the empty end of a wide row. */
  function aim(node) {
    var box = node.getBoundingClientRect();
    if (box.width < 120 || !node.textContent || !node.textContent.trim()) return box;
    try {
      var range = document.createRange();
      range.selectNodeContents(node);
      var inner = range.getBoundingClientRect();
      if (inner.width > 0 && inner.height > 0 && inner.width < box.width * 0.6) return inner;
    } catch (e) { /* no ranges over this kind of node */ }
    return box;
  }

  /** `rectOf`, holding on to the last box it gave with anything in it.  An
   *  element the editor replaces - the rows of the add-ons' list are made
   *  again when one is switched on - answers an empty box at 0, 0 once it
   *  is off the page, and what was placed by it jumped to the corner. */
  function steady(rectOf) {
    var last = null;
    return function () {
      var r = rectOf();
      if (r && (r.width || r.height)) last = r;
      return last || r;
    };
  }

  /** The subject with the room its ring (round it) and its arrow (over it,
   *  or under it near the top) take - what a caption keeps clear of.  The
   *  same sums as Overlay.layout draws them with. */
  function keepOut(s) {
    var size = Math.max(34, Math.min(160, Math.max(s.width, s.height) + 22));
    var cx = s.left + s.width / 2, cy = s.top + s.height / 2;
    var k = { left: Math.min(s.left, cx - size / 2), right: Math.max(s.right, cx + size / 2),
              top: Math.min(s.top, cy - size / 2), bottom: Math.max(s.bottom, cy + size / 2) };
    if (s.top < 76) k.bottom = Math.max(k.bottom, s.bottom + 62); else k.top = Math.min(k.top, s.top - 62);
    return k;
  }

  /* ---- the overlay: a layer over the page that never takes a pointer ---- */

  function Overlay(editor) {
    this.editor = editor;
    this.layer = make("div", "se-tour-layer", document.body);
    this.caption = make("div", "se-tour-caption", this.layer);
    this.caption.setAttribute("role", "status");
    this.caption.setAttribute("aria-live", "polite");
    this.caption.hidden = true;
    this.arrow = make("div", "se-tour-arrow", this.layer);
    this.arrow.innerHTML = '<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M32 5 V55 M15 38 L32 55 L49 38"/></svg>';
    this.arrow.hidden = true;
    this.ring = make("div", "se-tour-ring", this.layer);
    this.ring.hidden = true;
    this.target = null;       // {node, rect()} the arrow and the ring are on
    this.subject = null;      // what the caption is about, when it is placed beside something
    this.place = "near";
    this.until = 0;           // when the caption on show goes (0: when the next one comes)
    this.captionTimer = null;
    var self = this;
    this.frame = requestAnimationFrame(function loop() { self.layout(); self.frame = requestAnimationFrame(loop); });
  }

  /** The part of the editor on the screen: where captions go when they are
   *  not beside something - the editor's, not the window's, so that one
   *  embedded in a longer page keeps its captions to itself. */
  Overlay.prototype.stage = function () {
    // The History (or the guide) covers the window while it is open: then it
    // is what the captions go at the top, middle or bottom of.
    var open = this.editor.root.querySelector(".se-history-view");
    var r = (open || this.editor.root).getBoundingClientRect();
    var top = Math.max(r.top, 0), bottom = Math.min(r.bottom, innerHeight);
    if (bottom - top < 120) { top = 0; bottom = innerHeight; }                  // hardly on the screen: the window
    var left = Math.max(r.left, 0), right = Math.min(r.right, innerWidth);
    return { left: left, right: right, top: top, bottom: bottom, width: right - left, height: bottom - top };
  };

  Overlay.prototype.layout = function () {
    var t = this.target;
    if (t) {
      var r = t.rect();
      var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      var size = Math.max(34, Math.min(160, Math.max(r.width, r.height) + 22));
      this.ring.style.width = this.ring.style.height = size + "px";
      this.ring.style.left = (cx - size / 2) + "px";
      this.ring.style.top = (cy - size / 2) + "px";
      // Above the target, pointing down - below it, pointing up, when there
      // is no room above.
      var below = r.top < 76;
      this.arrow.classList.toggle("below", below);
      this.arrow.style.left = (cx - 28) + "px";
      this.arrow.style.top = (below ? r.bottom + 6 : r.top - 62) + "px";
    }
    var box = this.caption;
    if (box.hidden) return;
    var w = box.offsetWidth, h = box.offsetHeight, x, y;
    var s = this.subject ? this.subject() : null;
    var place = this.place;
    if (s && place !== "top" && place !== "center" && place !== "bottom") {
      // Beside what it is about: above it, or below when there is no room;
      // never over it - nor over the ring and the arrow it gets.  Their room
      // is kept from the start, worked out from the subject as `layout`
      // draws them: a caption placed round the subject alone was overlapped
      // by the ring of a wide, short box, and jumped when the ring came.
      var k = keepOut(s);
      var above = k.top - MARGIN - h, underneath = k.bottom + MARGIN;
      var cx = s.left + s.width / 2, cy = s.top + s.height / 2;
      var onLeft = k.left - MARGIN - w, onRight = k.right + MARGIN;
      if (place === "above" || (place !== "below" && above >= MARGIN)) {
        y = above; x = cx - w / 2;
      } else if (place === "near" && cx > innerWidth / 2 && onLeft >= MARGIN) {
        // No room above a target on the right (a row of the drawer at the top
        // of the screen): beside it, on the left - below, it would cover the
        // rows that come next.
        x = onLeft; y = cy - h / 2;
      } else if (place === "near" && cx <= innerWidth / 2 && onRight + w <= innerWidth - MARGIN) {
        x = onRight; y = cy - h / 2;
      } else {
        y = underneath; x = cx - w / 2;
      }
    } else {
      var st = this.stage();
      x = st.left + st.width / 2 - w / 2;
      y = place === "top" ? st.top + 18 : place === "bottom" ? st.bottom - h - 18 : st.top + st.height / 2 - h / 2;
    }
    x = Math.max(MARGIN, Math.min(innerWidth - w - MARGIN, x));
    y = Math.max(MARGIN, Math.min(innerHeight - h - MARGIN, y));
    box.style.left = Math.round(x) + "px";
    box.style.top = Math.round(y) + "px";
  };

  /** A caption; null or "" takes it away.  `opts`: place ("near" - beside
   *  `subject` -, "above", "below", "top", "center", "bottom"), size
   *  ("large"), seconds (gone after that long; else until the next). */
  Overlay.prototype.say = function (text, opts, subject) {
    var box = this.caption, self = this;
    opts = opts || {};
    clearTimeout(this.captionTimer);
    if (!text) {
      box.classList.remove("shown");
      this.until = 0;
      this.captionTimer = setTimeout(function () { box.hidden = true; }, 250);
      return;
    }
    this.subject = subject || null;
    this.place = opts.place || (subject ? "near" : "center");
    box.textContent = String(text);
    box.className = "se-tour-caption" + (opts.size === "large" ? " large" : "");
    box.hidden = false;
    this.layout();
    void box.offsetWidth;                          // start the fade from where it is
    box.classList.add("shown");
    this.until = opts.seconds ? Date.now() + opts.seconds * 1000 : 0;
    if (opts.seconds) this.captionTimer = setTimeout(function () { self.say(null); }, opts.seconds * 1000);
  };

  /** Arrow and ring on `node`, following it while it moves, until `clear`. */
  Overlay.prototype.point = function (node, rectOf) {
    this.target = { node: node, rect: steady(rectOf || function () { return aim(node); }) };
    this.arrow.hidden = false;
    this.ring.hidden = false;
    this.ring.classList.remove("pressing");
    this.layout();
  };

  /** The press: the ring closes on the target. */
  Overlay.prototype.pressing = function () { this.ring.classList.add("pressing"); };

  Overlay.prototype.clear = function () {
    this.target = null;
    this.arrow.hidden = true;
    this.ring.hidden = true;
    this.ring.classList.remove("pressing");
  };

  Overlay.prototype.remove = function () {
    this.clear();
    cancelAnimationFrame(this.frame);
    clearTimeout(this.captionTimer);
    if (this.layer.parentNode) this.layer.parentNode.removeChild(this.layer);
  };

  /* ---- what a person does, done to the real elements ---- */

  /** A press at the middle of `node`: pointer and mouse down and up, then the
   *  click - every handler the editor has on anything sees what a finger or
   *  a mouse would give it. */
  function press(node) {
    var r = node.getBoundingClientRect();
    var at = { bubbles: true, cancelable: true, composed: true, view: window, button: 0,
               clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    var pointer = function (type, buttons) {
      if (window.PointerEvent) node.dispatchEvent(new PointerEvent(type, Object.assign({ pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: buttons }, at)));
    };
    pointer("pointerdown", 1);
    node.dispatchEvent(new MouseEvent("mousedown", Object.assign({ buttons: 1 }, at)));
    if (node.matches && node.matches("input, textarea, select, button, [contenteditable], [tabindex]")) node.focus({ preventScroll: true });
    pointer("pointerup", 0);
    node.dispatchEvent(new MouseEvent("mouseup", at));
    node.dispatchEvent(new MouseEvent("click", at));
  }

  function key(node, spec) {
    var k = typeof spec === "string" ? { key: spec } : spec;
    var init = { key: k.key, code: k.code || "", bubbles: true, cancelable: true, composed: true,
                 ctrlKey: !!k.ctrl, shiftKey: !!k.shift, altKey: !!k.alt, metaKey: !!k.meta };
    node.dispatchEvent(new KeyboardEvent("keydown", init));
    node.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  function changed(node) {
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /* ---- the player ---- */

  function Player(editor, script, opts) {
    this.editor = editor;
    this.script = script || {};
    this.opts = opts || {};
    this.steps = (this.script.steps || []).slice();
    this.speed = +(this.opts.speed || this.script.speed || 1) || 1;
    this.errors = [];
    this.stopped = false;
    this.index = -1;
    this.overlay = new Overlay(editor);
    var self = this;
    this.done = new Promise(function (resolve) { self._resolve = resolve; });
  }

  Player.prototype.seconds = function (s) { return (s || 0) * 1000 / this.speed; };

  /** Until the editor has its first state, Python has started, and nothing
   *  is on its way: a step sent to a busy editor is dropped. */
  Player.prototype.idle = async function (limit) {
    var ed = this.editor, end = Date.now() + (limit || 120000);
    while (!this.stopped && Date.now() < end && (!ed.state || ed.loading || ed.busy)) await sleep(40);
  };

  /** Until the page is ready to be shown: the formula drawn, and Python
   *  started where the page runs its own (Pyodide).  Waiting on `loading`
   *  alone started the tour at once - the editor's first state is set a
   *  moment before its warm-up begins - and the first steps played over
   *  the loading screen.  The backend's warm-up is the promise the page is
   *  waiting on already; asking for it again starts nothing twice. */
  Player.prototype.ready = async function () {
    var ed = this.editor, end = Date.now() + 180000;
    while (!this.stopped && Date.now() < end && !ed.root.getAttribute("data-seq")) await sleep(40);
    if (ed.backend && typeof ed.backend.warmup === "function") {
      try {
        await ed.backend.warmup(function () {});
      } catch (e) {
        this.errors.push("start: Python did not start: " + ((e && e.message) || e));
      }
    }
    await this.idle();
  };

  Player.prototype.find = async function (target) {
    if (target === "focused") return document.activeElement ? { node: document.activeElement } : null;
    var t = typeof target === "string" ? { selector: target } : (target || {});
    var ed = this.editor, end = Date.now() + (t.timeout || FIND) * 1000;
    while (!this.stopped) {
      var found = null, rect = null;
      if (t.path !== undefined) {
        var parts = Array.prototype.slice.call(ed.root.querySelectorAll('.se-view [data-path="' + String(t.path).replace(/"/g, '\\"') + '"]')).filter(visible);
        if (parts.length) {
          found = parts[0];
          rect = function () {       // a node broken into pieces ("-sin(x)"): the box round all of them
            var l = Infinity, tp = Infinity, rt = -Infinity, bt = -Infinity;
            parts.forEach(function (p) { var q = p.getBoundingClientRect(); l = Math.min(l, q.left); tp = Math.min(tp, q.top); rt = Math.max(rt, q.right); bt = Math.max(bt, q.bottom); });
            return { left: l, top: tp, right: rt, bottom: bt, width: rt - l, height: bt - tp };
          };
        }
      } else if (t.selector) {
        var all = Array.prototype.slice.call(document.querySelectorAll(t.selector)).filter(visible);
        if (t.text) all = all.filter(function (n) { return n.textContent.indexOf(t.text) >= 0; });
        found = all[0] || null;
      }
      if (found) return { node: found, rect: steady(rect || function () { return aim(found); }), path: t.path };
      if (Date.now() > end) return null;
      await sleep(80);
    }
    return null;
  };

  Player.prototype.show = async function (hit, seconds) {
    try { hit.node.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" }); } catch (e) { /* old browsers */ }
    await sleep(this.seconds(0.35));
    this.overlay.point(hit.node, hit.rect);
    await sleep(this.seconds(seconds));
  };

  Player.prototype.miss = function (i, what) {
    this.errors.push("step " + i + ": " + what);
    if (window.console) console.warn("sympy-editor tutorial: step " + i + ": " + what);
  };

  /** What a step's caption is about: its own target, when it has one. */
  Player.prototype.subjectOf = async function (step) {
    var target = step.click || step.point || step.choose || (step.type && (step.type.target || step.type.selector));
    if (step.choose && typeof step.choose === "object" && !step.choose.path && !step.choose.selector) target = step.choose.target;
    if (step.near) target = step.near;
    if (target === "focused") {
      // Typing where the focus is - the field Edit opened in the formula:
      // the caption goes beside that field, not over the middle of the
      // editor, which is where the field is.
      var f = document.activeElement;
      return f && f !== document.body && this.editor.root.contains(f) ? steady(function () { return aim(f); }) : null;
    }
    if (!target) return null;
    var hit = await this.find(target);
    return hit ? hit.rect : null;
  };

  Player.prototype.perform = async function (i, step) {
    var ed = this.editor, hit;
    var sayOpts = { place: step.position, size: step.size };
    if (step.say !== undefined) {
      sayOpts.seconds = step.sayFor;
      this.overlay.say(step.say, sayOpts, await this.subjectOf(step));
    }
    if ("caption" in step) {
      sayOpts.seconds = step.duration;
      this.overlay.say(step.caption, sayOpts, step.near ? await this.subjectOf(step) : null);
    } else if (step.point) {
      if (!(hit = await this.find(step.point))) return this.miss(i, "nothing to point at: " + JSON.stringify(step.point));
      await this.show(hit, step.hold !== undefined ? step.hold : LEAD);
      this.overlay.clear();
    } else if (step.click) {
      if (!(hit = await this.find(step.click))) return this.miss(i, "nothing to click: " + JSON.stringify(step.click));
      await this.show(hit, step.lead !== undefined ? step.lead : LEAD);
      this.overlay.pressing();
      await sleep(this.seconds(0.18));
      if (hit.path !== undefined) ed.select(hit.path);        // a piece of the formula: selected as a click would
      else press(hit.node);
      await sleep(this.seconds(0.25));
      this.overlay.clear();
    } else if (step.choose) {
      var c = step.choose;
      if (!(hit = await this.find(c.target || c.selector))) return this.miss(i, "nothing to choose in: " + JSON.stringify(c));
      var sel = hit.node, want = String(c.value);
      var opt = Array.prototype.slice.call(sel.options || []).filter(function (o) { return o.value === want || o.textContent.trim() === want; })[0];
      if (!opt) return this.miss(i, "no option " + JSON.stringify(want) + " in " + JSON.stringify(c.target || c.selector));
      await this.show(hit, step.lead !== undefined ? step.lead : 0.8);
      this.overlay.pressing();
      await sleep(this.seconds(0.18));
      sel.focus({ preventScroll: true });
      sel.value = opt.value;
      changed(sel);
      await sleep(this.seconds(0.25));
      this.overlay.clear();
    } else if (step.type) {
      var t = step.type;
      if (!(hit = await this.find(t.target || t.selector || "focused"))) return this.miss(i, "nowhere to type: " + JSON.stringify(t));
      var node = hit.node, editable = node.isContentEditable;
      if ((t.target || t.selector || "focused") !== "focused") {
        await this.show(hit, t.lead !== undefined ? t.lead : 0.6);
        this.overlay.clear();
        press(node);
      }
      node.focus({ preventScroll: true });
      if (t.replace !== false) { if (editable) node.textContent = ""; else node.value = ""; }
      var text = String(t.text || "");
      for (var ch = 0; ch < text.length && !this.stopped; ch++) {
        if (editable) node.textContent += text[ch]; else node.value += text[ch];
        node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text[ch] }));
        await sleep(this.seconds(t.perChar !== undefined ? t.perChar : PER_CHAR));
      }
      // what leaving the field, or Enter, would tell its listeners
      if (!editable) node.dispatchEvent(new Event("change", { bubbles: true }));
      if (t.enter) {
        await sleep(this.seconds(0.3));
        key(node, "Enter");
        // The editor's in-place field (Edit, or a double-click, on a piece
        // of the formula) takes a person's Enter, not one sent from a script;
        // leaving the field applies it just the same, and that is what is
        // done when the Enter did not.
        await sleep(this.seconds(0.15));
        if (node === ed.input && document.activeElement === node) node.blur();
      }
    } else if (step.key) {
      var active = document.activeElement;
      var into = active && ed.root.contains(active) ? active : ed.view;
      if (into === ed.view) ed.view.focus({ preventScroll: true });
      (Array.isArray(step.key) ? step.key : [step.key]).forEach(function (k) { key(into, k); });
    } else if ("set" in step) {
      await ed.send({ action: "set", src: String(step.set) });
    } else if (step.apply) {
      var a = typeof step.apply === "string" ? { op: step.apply } : step.apply;
      var msg = { action: "apply", op: a.op, path: a.path || (ed.range ? ed.range.parent : ed.selected) || "/" };
      if (a.args) msg.args = a.args;
      await ed.send(msg);
    } else if (step.undo) {
      await ed.send({ action: "undo" });
    } else if (step.redo) {
      await ed.send({ action: "redo" });
    } else if ("zoom" in step) {
      ed.setZoom(+step.zoom || 1);
    } else if (step.addons) {
      await ed.send({ action: "addons", enable: step.addons.enable || [], disable: step.addons.disable || [] });
    }
    // "wait": nothing to do - the timing was the point
    await this.idle();
  };

  /** The script is over: the last caption has its time, then everything of
   *  the player goes, and what is left is the editor, as a reader finds it -
   *  no overlay, the History, the drawer and the menus shut, the page at its
   *  top. */
  Player.prototype.finish = async function () {
    var ov = this.overlay;
    ov.clear();
    if (!ov.caption.hidden) {
      var left = ov.until ? ov.until - Date.now() : this.seconds(LAST);
      await sleep(left);
      ov.say(null);
      await sleep(300);
    }
    ov.remove();
    var ed = this.editor;
    if (ed.root.querySelector(".se-history-view") && typeof ed.closeHistory === "function") ed.closeHistory();
    if (typeof ed.closeDrawer === "function") ed.closeDrawer();
    if (document.activeElement && document.activeElement !== document.body && ed.root.contains(document.activeElement)) document.activeElement.blur();
    try {
      if (this.opts.fullPage) window.scrollTo({ top: 0, behavior: "smooth" });
      else ed.root.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (e) { /* old browsers */ }
  };

  Player.prototype.play = async function () {
    document.documentElement.classList.add("se-tour-running");
    await this.ready();
    do {
      var start = performance.now(), last = start;
      for (var i = 0; i < this.steps.length && !this.stopped; i++) {
        var step = this.steps[i] || {};
        var due = step.at !== undefined ? start + this.seconds(step.at)
                : last + this.seconds(step.after !== undefined ? step.after : (i ? GAP : 0));
        await sleep(due - performance.now());
        await this.idle();
        if (this.stopped) break;
        this.index = i;
        window.dispatchEvent(new CustomEvent("sympy-editor-tutorial-step", { detail: { index: i, step: step } }));
        try { await this.perform(i, step); } catch (e) { this.miss(i, String((e && e.message) || e)); }
        last = performance.now();
      }
      if (this.script.loop && !this.stopped) await sleep(this.seconds(this.script.loopDelay !== undefined ? this.script.loopDelay : 3));
    } while (this.script.loop && !this.stopped);
    if (!this.stopped) await this.finish();
    document.documentElement.classList.remove("se-tour-running");
    window.dispatchEvent(new CustomEvent("sympy-editor-tutorial-end", { detail: { errors: this.errors.slice() } }));
    this._resolve({ errors: this.errors.slice(), stopped: this.stopped });
  };

  Player.prototype.stop = function () {
    this.stopped = true;
    this.overlay.remove();
    document.documentElement.classList.remove("se-tour-running");
  };

  window.SympyEditorTutorial = {
    version: 2,
    Player: Player,
    /** Play `script` on an editor (the Editor, its element, or the element
     *  it was mounted in; the page's first editor by default).  `opts`:
     *  fullPage (the page is the editor's: back to its top at the end),
     *  speed. */
    run: function (target, script, opts) {
      var ed = editorOf(target);
      if (!ed) throw new Error("SympyEditorTutorial.run: no editor to play on");
      var player = new Player(ed, script, opts);
      window.SympyEditorTutorial.current = player;
      player.play();
      return player;
    }
  };
})();
