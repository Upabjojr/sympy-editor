/*
 * sympy-editor add-on "latex": LaTeX typed into the formula itself.
 *
 * There is no box under the editor to type in: the LaTeX goes where it will
 * land.  The tool (or the L key's button) opens a field *in the formula* -
 * over the selection, at the cursor, or after the whole expression - and the
 * formula opens a space for it, as it does for handwriting.  What is typed is
 * read as it is typed (Python's "read"), shown beside the field as it will
 * look and under the editor as SymPy would get it; nothing changes in the
 * document until "Apply to the formula" says so, and then the strip shows the
 * formula before and after, to keep or to take back.
 *
 * The ambiguities of the text - each a menu of the readings it allows - and
 * the constant names it uses - each a switch - live in that strip, as the
 * handwriting add-on's do: one shape for both, since both end in the same
 * question ("which reading did you mean, and shall it go in?").
 */
SympyEditor.registerAddon("latex", (function () {
  return {
    tools: [
      { cmd: "type", label: "LaTeX", title: "Type LaTeX into the formula: over the selection, at the cursor, or at its end",
        run: function () { this.setTyping(!this.typing()); } }
    ],

    mount: function (api) {
      var h = api.h;
      var editor = api.editor, view = editor && editor.view;

      /* ---- the strip under the editor: what the LaTeX reads as ---- */
      var note = h("div", { class: "ltx-note", "aria-live": "polite" });
      var helpBtn = h("button", { type: "button", class: "ltx-help", title: "How typing LaTeX works" }, ["?"]);
      var readingOf = h("div", { class: "ltx-reading-of" });
      var src = h("code", { class: "ltx-src", title: "What SymPy gets of it" });
      var ambig = h("div", { class: "ltx-ambig" });
      var consts = h("div", { class: "ltx-consts" });
      var parseBlock = h("div", { class: "ltx-parse", hidden: "" }, [ambig, consts]);
      var applyBtn = h("button", { type: "button", class: "ltx-apply", disabled: "",
                                   title: "Put this reading into the formula" }, ["Apply to the formula"]);
      var actions = h("div", { class: "ltx-actions", hidden: "" }, [applyBtn]);
      var wasFormula = h("span", { class: "ltx-formula ltx-was" });
      var nowFormula = h("span", { class: "ltx-formula ltx-now" });
      var keepBtn = h("button", { type: "button", class: "ltx-keep", title: "Leave the formula as it now is" }, ["Keep"]);
      var backBtn = h("button", { type: "button", class: "ltx-back", title: "The formula as it was" }, ["Undo the change"]);
      var appliedRow = h("div", { class: "ltx-applied", hidden: "" }, [
        h("div", { class: "ltx-applied-row" }, [h("span", { class: "ltx-applied-label" }, ["from"]), wasFormula]),
        h("div", { class: "ltx-applied-row" }, [h("span", { class: "ltx-applied-label" }, ["to"]), nowFormula]),
        h("div", { class: "ltx-applied-ask" }, [keepBtn, backBtn])]);
      var element = h("div", { class: "ltx-panel", hidden: "" },
        [h("div", { class: "ltx-head" }, [note, helpBtn]), readingOf, src, parseBlock, actions, appliedRow]);
      if (editor && editor.addonHost && editor.addonHost.parentNode) {
        editor.addonHost.parentNode.insertBefore(element, editor.addonHost);
      }
      element.addEventListener("keydown", function (ev) { ev.stopPropagation(); });

      /* ---- the field, in the formula ---- */
      var field = h("input", { type: "text", class: "ltx-field", spellcheck: "false", autocomplete: "off",
                               autocapitalize: "off", "aria-label": "LaTeX to put into the formula",
                               placeholder: "\\frac{x^2}{2}" });
      //: What it will look like, beside the field: the reading typeset.
      var ghost = h("span", { class: "ltx-ghost", "aria-hidden": "true" });
      var typing = false, room = null, aim = null, anchored = null;
      var choices = {}, constants = {}, last = null;
      var seq = 0, timer = null, katex = null;
      var applied = null, mine = 0, puts = 0, guide;

      api.katex().then(function (k) { katex = k; if (last && last.ok) drawGhost(last.latex); }, function () {});
      // The parsers are built as the add-on is switched on, not at the first
      // reading: in a thread of its own where Python has threads, and where it
      // has none (Pyodide) by this request, which goes before what comes next.
      api.call("warm", { background: true }, { quiet: true }).then(null, function () {});

      /* ---- where the LaTeX will go ---- */
      function aimNow() {
        var r = api.range && api.range(), sel = api.selected && api.selected();
        if (r) return { kind: "range", path: r.parent, children: editor._rangeIndices() };
        if (sel) return { kind: "selection", path: sel };
        var caret = api.insertion && api.insertion();
        if (caret) return { kind: "caret", caret: caret };
        return { kind: "end" };
      }
      function aimWords(a) {
        if (!a) return "";
        if (a.kind === "range") return "This takes the selected range's place:";
        if (a.kind === "selection") return "This takes the selection's place:";
        if (a.kind === "caret") return "This goes in at the cursor:";
        return "This goes after the formula:";
      }
      function elementFor(path) {
        if (!view || !path) return null;
        var els = view.querySelectorAll("[data-path]");
        for (var i = 0; i < els.length; i++) if (els[i].getAttribute("data-path") === path) return els[i];
        return null;
      }
      /** The piece the field is put beside, and on which side of it. */
      function anchor() {
        var c = api.caret && api.caret();
        if (c && c.leftEl) return { el: c.leftEl, side: "after" };
        if (c && c.rightEl) return { el: c.rightEl, side: "before" };
        var r = api.range && api.range();
        if (r && editor._rangePaths) {
          var paths = editor._rangePaths();
          var last1 = paths.length ? elementFor(paths[paths.length - 1]) : null;
          if (last1) return { el: last1, side: "after" };
        }
        var sel = api.selected && api.selected();
        var el = sel ? elementFor(sel) : null;
        if (el) return { el: el, side: "after" };
        var all = view ? view.querySelectorAll("[data-path]") : [];
        return all.length ? { el: all[0], side: "end" } : null;
      }

      /* ---- opening and closing the place to type ---- */
      /** Open the field where the LaTeX will land.  `again` re-places a field
       *  that was already open (the formula was drawn afresh, or the selection
       *  moved): where it aims is asked of the editor only when it is opened
       *  anew, since putting the field in the formula is itself a change the
       *  editor answers - and the answer must not move the target. */
      function openField(again) {
        if (!view) return;
        var held = again && aim ? aim : null;
        var wanted = again && anchored && anchored.el && anchored.el.isConnected ? anchored : null;
        closeField(true);
        aim = held || aimNow();
        var a = wanted || anchor();
        anchored = a;
        var holder = h("span", { class: "ltx-slot" }, [field, ghost]);
        if (!a) view.appendChild(holder);
        else if (a.side === "before" && a.el.parentNode) a.el.parentNode.insertBefore(holder, a.el);
        else if (a.el.parentNode) a.el.parentNode.insertBefore(holder, a.el.nextSibling);
        else view.appendChild(holder);
        room = holder;
        if (editor && editor.root) editor.root.classList.add("se-typing-latex");
        element.setAttribute("data-aim", aim.kind);
        readingOf.textContent = aimWords(aim);
        showPanel();
        focusField();
      }

      /** The field takes the focus - and, on a phone, the keyboard with it.
       *  A WebView raises the keyboard when a field is focused in answer to a
       *  tap; the host is asked as well, since a field put there by script is
       *  not always taken for one (Android's MainActivity.showKeyboard). */
      function focusField() {
        field.focus();
        field.select();
        var app = window.SympyEditorApp;
        if (app && app.showKeyboard) {
          try { app.showKeyboard(); } catch (e) { /* the focus alone, then */ }
        }
      }
      function closeField(quiet) {
        // The field goes, and the keyboard with it: a phone keeps the keyboard
        // up for as long as something is focused, so the formula takes the
        // focus back (which is where the editor's own keys belong anyway).
        var had = document.activeElement === field;
        if (room && room.parentNode) room.parentNode.removeChild(room);
        room = null;
        if (had) {
          try { field.blur(); } catch (e) { /* gone already */ }
          if (view && view.focus) view.focus({ preventScroll: true });
        }
        if (editor && editor.root) editor.root.classList.remove("se-typing-latex");
        if (!quiet) showPanel();
      }
      function setTyping(on) {
        typing = !!on;
        var button = editor && editor.root ? editor.root.querySelector('[data-cmd="addon:latex:type"]') : null;
        if (button) {
          button.setAttribute("aria-pressed", typing ? "true" : "false");
          button.classList.toggle("ltx-on", typing);
        }
        if (typing) {
          openField();
          say(field.value ? "" : "Type LaTeX here — it goes where the field is.");
        } else {
          closeField();
          if (!applied) clearReading();
        }
      }
      function showPanel() {
        element.hidden = !(typing || (last && last.ok) || applied);
      }
      function say(text, bad) {
        note.textContent = text || "";
        note.className = "ltx-note" + (bad ? " error" : "");
        showPanel();
      }

      /* ---- reading what is typed ---- */
      function schedule() { clearTimeout(timer); timer = setTimeout(read, 400); }
      function read() {
        clearTimeout(timer);
        var text = field.value.trim();
        if (!text) { last = null; clearReading(); return; }
        var my = ++seq;
        element.classList.add("ltx-busy");
        // quiet: the editor's overlay would cover the formula and take the
        // focus out of the field while one types
        api.call("read", { latex: text, choices: choices, constants: constants }, { quiet: true })
          .then(function (res) {
            if (my !== seq) return;
            element.classList.remove("ltx-busy");
            last = res;
            if (res.ok && res.choices) choices = res.choices;   // every decision: the next pick changes only itself
            render(res);
          }, function (e) {
            if (my !== seq) return;
            element.classList.remove("ltx-busy");
            last = null;
            clearReading();
            say(String((e && e.message) || e), true);
          });
      }
      function clearReading() {
        element.classList.remove("ltx-stale");
        src.textContent = "";
        ambig.textContent = "";
        consts.textContent = "";
        parseBlock.hidden = true;
        actions.hidden = true;
        applyBtn.disabled = true;
        drawGhost("");
        showPanel();
      }
      function drawGhost(tex) {
        ghost.textContent = "";
        if (!tex) { ghost.hidden = true; return; }
        ghost.hidden = false;
        if (katex) {
          try {
            ghost.innerHTML = katex.renderToString(tex, { throwOnError: false, displayMode: false, output: "html" });
            return;
          } catch (e) { /* the source in the strip stands for it */ }
        }
        ghost.textContent = tex;
      }
      function render(res) {
        if (!res.ok && res.incomplete) {
          // The text stops mid-expression: it is being typed, not wrong.  The
          // last reading stays, dimmed - it is not this text's - and cannot go in.
          say(res.error);
          note.className = "ltx-note pending";
          if (src.textContent) element.classList.add("ltx-stale");
          actions.hidden = true;
          applyBtn.disabled = true;
          return;
        }
        element.classList.remove("ltx-stale");
        if (!res.ok) {
          clearReading();
          say(res.error || "This LaTeX could not be read", true);
          return;
        }
        say(res.ambiguities.length
            ? (res.ambiguities.length === 1 ? "One part of this can be read two ways: pick below."
                                            : res.ambiguities.length + " parts of this can be read several ways: pick below.")
            : "");
        readingOf.textContent = aimWords(aim);
        drawGhost(res.latex);
        src.textContent = res.src;
        ambig.textContent = "";
        res.ambiguities.forEach(function (a) {
          var sel = h("select", { class: "ltx-choice", title: "How to read " + a.fragment });
          a.options.forEach(function (o, i) {
            var opt = h("option", { value: String(i) }, [o.invalid ? "(not a reading)" : o.src]);
            if (o.invalid) opt.disabled = true;
            if (i === a.choice) opt.selected = true;
            sel.appendChild(opt);
          });
          sel.addEventListener("change", function () { choices[a.key] = parseInt(sel.value, 10); read(); });
          ambig.appendChild(h("label", { class: "ltx-point" },
            [h("code", { class: "ltx-fragment" }, [a.fragment]), " → ", sel]));
        });
        consts.textContent = "";
        res.constants.forEach(function (c) {
          var box = h("input", { type: "checkbox" });
          box.checked = !!c.on;
          box.addEventListener("change", function () { constants[c.name] = box.checked; read(); });
          consts.appendChild(h("label", { class: "ltx-const", title: c.label },
            [box, " ", h("code", {}, [c.name]), " is " + c.value + " (" + c.label + ")"]));
        });
        parseBlock.hidden = !ambig.children.length && !consts.children.length;
        actions.hidden = !!applied;
        applyBtn.disabled = !!applied;
        showPanel();
      }

      /* ---- putting it in ---- */
      function payload() {
        var p = { latex: field.value.trim(), choices: choices, constants: constants, path: "/" };
        if (aim.kind === "range") { p.path = aim.path; p.children = aim.children; }
        else if (aim.kind === "selection") p.path = aim.path;
        else if (aim.kind === "caret") p.caret = aim.caret;
        else p.end = true;
        return p;
      }
      function apply() {
        if (!last || !last.ok || !aim) return;
        var was = applied ? applied.before : step(), my = ++puts;
        setTyping(false);          // first: onState and onSelect follow the change, and would open it again
        mine++;
        var back = applied ? api.send({ action: "undo" }) : Promise.resolve();
        back.then(function () { return api.call("insert", payload()); }).then(function () {
          mine = Math.max(0, mine - 1);
          if (my !== puts) return;
          applied = { before: was };
          showApplied(was, step());
          say("In the formula.");
        }, function (e) {
          mine = Math.max(0, mine - 1);
          if (my !== puts) return;
          say(String((e && e.message) || e), true);
        });
      }
      function step() {
        var st = api.state && api.state();
        return { latex: st && st.latex, plain: (st && st.latex_plain) || "", nodes: st && st.nodes };
      }
      function showApplied(was, now) {
        var diff = null;
        try {
          diff = editor && editor._diffNodes && was.nodes && now.nodes ? editor._diffNodes(was.nodes, now.nodes) : null;
        } catch (e) { diff = null; }
        var marked = false;
        if (editor && editor._renderMarked && was.latex && now.latex) {
          try {
            wasFormula.innerHTML = editor._renderMarked(was.latex, diff && diff.oldKept, "rep-removed");
            nowFormula.innerHTML = editor._renderMarked(now.latex, diff && diff.newKept, "rep-added");
            marked = true;
          } catch (e) { marked = false; }
        }
        if (!marked) {
          wasFormula.textContent = was.plain;
          nowFormula.textContent = now.plain;
        }
        wasFormula.setAttribute("data-latex", was.plain);
        nowFormula.setAttribute("data-latex", now.plain);
        appliedRow.hidden = false;
        actions.hidden = true;
        showPanel();
      }
      function hideApplied() {
        appliedRow.hidden = true;
        applied = null;
        puts++;
        showPanel();
      }
      keepBtn.addEventListener("click", function () { hideApplied(); last = null; clearReading(); });
      backBtn.addEventListener("click", function () {
        mine++;
        var done = function () { mine = Math.max(0, mine - 1); };
        api.send({ action: "undo" }).then(done, done);
        hideApplied();
        last = null;
        clearReading();
      });
      applyBtn.addEventListener("click", apply);
      helpBtn.addEventListener("click", function () { api.showHelp(guide, "LaTeX"); });

      field.addEventListener("input", function () { choices = {}; schedule(); });   // new text: the old picks are not its
      field.addEventListener("keydown", function (ev) {
        ev.stopPropagation();                        // the editor's keys are not for the field
        if (ev.key === "Escape") { ev.preventDefault(); setTyping(false); return; }
        if (ev.key === "Enter") { ev.preventDefault(); if (!applyBtn.disabled) apply(); }
      });

      guide = "<section><h3>LaTeX into the formula</h3><ul>"
        + "<li><b>LaTeX</b>, among the editor's tools, opens a field <i>in the formula</i>: over the selection, at the cursor, or after the whole expression - wherever what you type will go. The formula makes room for it, and beside it you see the LaTeX as it will look.</li>"
        + "<li>What is typed is read as you type. Under the editor: what SymPy gets of it, the parts that can be read more than one way - <code>f(x)</code> applied or multiplied, how far <code>\\sin x \\cos y</code> reaches - each a menu, and a switch for each name that usually means a constant (<code>\\pi</code>, <code>e</code>, <code>i</code>, <code>\\gamma</code>).</li>"
        + "<li>A text that stops in the middle of an expression (<code>\\frac{x</code>, <code>x +</code>) or of a command (<code>\\fr</code>) is <i>not finished yet</i>, not wrong: the last reading stays, dimmed, until it reads again.</li>"
        + "<li><b>Apply to the formula</b> (or <kbd>Enter</kbd>) puts it in - nothing changes before that - and then the formula before and after is shown, what went in red and what came in green, to <b>Keep</b> or to <b>Undo the change</b>. <kbd>Esc</kbd> closes the field and leaves the formula alone.</li>"
        + "</ul></section>";

      return {
        title: "LaTeX",
        help: guide,
        typing: function () { return typing; },
        setTyping: setTyping,
        onSelect: function () {
          // The field belongs where the selection is: while it is open, a new
          // selection moves it (and the reading follows the new target).
          if (!typing) return;
          var moved = aimNow();
          var same = aim && moved.kind === aim.kind && moved.path === aim.path;
          var text = field.value;
          openField(same);                       // the same target: only put the field back
          field.value = text;
          if (text.trim()) read();
        },
        onState: function () {
          if (!mine && applied) hideApplied();     // edited in the editor itself: what we did is answered for
          if (typing) {                            // the formula was rendered again: the field went with it
            var text = field.value;
            openField(true);
            field.value = text;
          }
        },
        destroy: function () {
          clearTimeout(timer);
          closeField(true);
          if (element.parentNode) element.parentNode.removeChild(element);
        }
      };
    }
  };
})());
