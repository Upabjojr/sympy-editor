/*
 * The console add-on's panel: a Python console (In / Out, as IPython) and a
 * script editor, both running in the document's Python - the app's own, the
 * server's, Pyodide's - with `editor` there to read and change the formula.
 *
 * What the user types outlives the page through the editor's keeper
 * (api.keep): the script and its name, the inputs for ↑/↓, the tab last used.
 */
(function () {
  var HELP = [
    "<section><h3>Two ways to run Python</h3><ul>",
    "<li><b>Console</b> - as IPython: type at <code>In [n]:</code>, <kbd>Enter</kbd> runs it, and the value of the last line is shown as <code>Out[n]</code>, typeset when it is mathematics. ",
    "An unfinished block (<code>for i in range(3):</code>) asks for the next line instead, and runs at an empty one; <kbd>Shift</kbd>+<kbd>Enter</kbd> is a new line, <kbd>Ctrl</kbd>+<kbd>Enter</kbd> runs whatever is there.</li>",
    "<li><b>Script</b> - a whole file, typed here or opened with <b>Open…</b>, run with <b>Run script</b> as <code>python script.py</code> runs it. ",
    "What it defines is then in the console, as after IPython's <code>%run</code>. The script is kept between visits; <b>Save .py</b> writes it to a file.</li>",
    "</ul></section>",
    "<section><h3>The formula, from Python</h3><ul>",
    "<li>SymPy is imported, and the formula's symbols are there under their names, assumptions and all.</li>",
    "<li><code>editor.expr</code> is the formula; <code>editor.expr = simplify(editor.expr)</code> changes it.</li>",
    "<li><code>editor.selection</code> is what is selected when the code runs (the whole formula when nothing is); assign to it to replace it.</li>",
    "<li><code>editor[\"/1\"]</code> is the piece at a path, <code>editor.paths()</code> lists them, <code>editor.find(cos(x))</code> finds one, <code>editor.select(\"/1\")</code> selects it.</li>",
    "<li><code>editor.apply(\"factor\", \"/0\")</code> runs one of the editor's transformations (<code>editor.ops</code>), <code>editor.undo()</code> / <code>editor.redo()</code> walk the history.</li>",
    "<li>Every change is a step of the formula's history: the editor's Undo takes it back.</li>",
      "<li>Run leaves you at the prompt; the transcript above it scrolls in its own box once it is long.</li>",
    "<li><b>Use</b> beside an <code>Out[n]</code> puts that value in the formula - over the selection, or as the whole formula.</li>",
    "</ul></section>",
    "<section><h3>What IPython adds</h3><ul>",
    "<li><code>_</code>, <code>__</code>, <code>___</code>, <code>_3</code>, <code>Out[3]</code>, <code>In[3]</code>.</li>",
    "<li><code>factor?</code> describes <code>factor</code>, <code>factor??</code> shows its source.</li>",
    "<li><code>%who</code> and <code>%whos</code> list your variables, <code>%time</code> times a statement, <code>%reset</code> (or <b>Reset</b>) starts afresh.</li>",
    "<li>Completion: after a <code>.</code> a menu lists what the object in memory has (methods, properties), and while a name is typed it opens when only a few names begin that way - yours first. <kbd>↑</kbd> / <kbd>↓</kbd> choose, <kbd>Enter</kbd>, <kbd>Tab</kbd> or a tap take one, <kbd>Esc</kbd> closes it; <kbd>Tab</kbd> also opens it with every name that fits.</li>",
    "<li><kbd>↑</kbd> / <kbd>↓</kbd> bring back earlier inputs (kept between visits), a tap on an earlier input puts it back and a tap on an output copies it into the input at the cursor, <kbd>Ctrl</kbd>+<kbd>L</kbd> or <b>Clear</b> clears the screen.</li>",
    "<li><code>display(obj)</code> shows a value typeset in the middle of the output.</li>",
    "</ul></section>",
    "<section><h3>Good to know</h3><ul>",
    "<li>There is no keyboard to wait on: <code>input()</code> says so, and <code>!commands</code> have no shell to run in.</li>",
    "<li>A long computation can be stopped with the editor's Interrupt button. In a standalone page that restarts Python, and the variables go with it.</li>",
    "<li>Each session of the editor has a namespace of its own.</li>",
    "</ul></section>"].join("");

  SympyEditor.registerAddon("console", {
    mount: function (api) {
      var h = api.h;
      var HISTORY_MAX = 200;

      /** A field for code: no capitals, no corrections, no spelling marks - what
       *  a phone keyboard would do to Python otherwise. */
      function codeField(cls, rows, placeholder) {
        var ta = h("textarea", { class: "pc-code-field " + cls, rows: String(rows), spellcheck: "false",
                                 autocapitalize: "off", autocomplete: "off", autocorrect: "off",
                                 placeholder: placeholder || "", "aria-label": placeholder || "Python" });
        ta.setAttribute("wrap", "off");
        return ta;
      }
      function button(label, title, cls) {
        return h("button", { type: "button", class: "pc-btn" + (cls ? " " + cls : ""), title: title || label }, [label]);
      }

      /* ---- the two tabs ---- */
      var tabConsole = h("button", { type: "button", class: "pc-tab", role: "tab", "data-mode": "console" }, ["Console"]);
      var tabScript = h("button", { type: "button", class: "pc-tab", role: "tab", "data-mode": "script" }, ["Script"]);
      var clearBtn = button("Clear", "Clear the transcript (Ctrl+L); the variables stay");
      var resetBtn = button("Reset", "A fresh namespace: every variable goes, SymPy and editor come back");
      var tabs = h("div", { class: "pc-tabs", role: "tablist" }, [tabConsole, tabScript, h("span", { class: "pc-fill" }), clearBtn, resetBtn]);

      /* ---- the console ---- */
      var log = h("div", { class: "pc-log", "aria-live": "polite" });
      var prompt = h("span", { class: "pc-prompt pc-prompt-in" }, ["In [1]:"]);
      var input = codeField("pc-input", 1, "Python - Enter runs, Shift+Enter a new line");
      var runBtn = button("Run", "Run the input (Enter; Ctrl+Enter runs an unfinished block too)", "pc-run");
      var hint = h("div", { class: "pc-hint" });
      var inputRow = h("div", { class: "pc-input-row" }, [prompt, input, runBtn]);   // and the completion menu
      var consolePane = h("div", { class: "pc-pane pc-console" }, [log, inputRow, hint]);

      /* ---- the script ---- */
      var nameField = h("input", { type: "text", class: "pc-name", value: "script.py", spellcheck: "false",
                                   autocapitalize: "off", autocomplete: "off", autocorrect: "off", "aria-label": "Script name" });
      var openBtn = button("Open…", "Open a Python file");
      var saveBtn = button("Save .py", "Save the script as a file");
      var runScriptBtn = button("Run script", "Run the whole script (Ctrl+Enter), as python runs a file", "pc-run");
      var script = codeField("pc-script", 12, "# A whole Python file: SymPy is imported, editor is the formula");
      var scriptOut = h("div", { class: "pc-log pc-script-out" });
      var scriptPane = h("div", { class: "pc-pane pc-scripting" }, [
        h("div", { class: "pc-script-bar" }, [nameField, openBtn, saveBtn, h("span", { class: "pc-fill" }), runScriptBtn]),
        script, scriptOut]);

      var element = h("div", { class: "pc-panel" }, [tabs, consolePane, scriptPane]);

      var state = { mode: "console", history: [], pos: 0, draft: "", token: null, next: 1, running: false };

      var EXAMPLE = [
        "# A script runs as a file does; what it defines stays in the console.",
        "# editor is the formula: editor.expr, editor.selection, editor[\"/1\"] ...",
        "",
        "f = editor.expr",
        "print(\"The formula:\", f)",
        "print(\"Its free symbols:\", f.free_symbols)",
        "",
        "# Uncomment to change the formula (Undo takes it back):",
        "# editor.expr = simplify(f)",
        ""].join("\n");

      /* ---- what is kept ---- */
      function keepRead(name) {
        return Promise.resolve(api.keep ? api.keep.read(name) : null).catch(function () { return null; });
      }
      function keepWrite(name, text) {
        if (api.keep) Promise.resolve(api.keep.write(name, text)).catch(function () {});
      }
      var scriptTimer = null;
      function keepScript() {
        clearTimeout(scriptTimer);
        scriptTimer = setTimeout(function () {
          keepWrite("console-script", JSON.stringify({ name: nameField.value, text: script.value }));
        }, 400);
      }
      keepRead("console-script").then(function (text) {
        try {
          var kept = text ? JSON.parse(text) : null;
          if (kept && typeof kept.text === "string" && !script.value) { script.value = kept.text; nameField.value = kept.name || "script.py"; }
        } catch (e) { /* not ours */ }
        if (!script.value) script.value = EXAMPLE;
      });
      keepRead("console-history").then(function (text) {
        try { var list = text ? JSON.parse(text) : null; if (Array.isArray(list)) state.history = list.concat(state.history).slice(-HISTORY_MAX); }
        catch (e) { /* not ours */ }
        state.pos = state.history.length;
      });
      keepRead("console-mode").then(function (mode) { if (mode === "script" || mode === "console") setMode(mode, true); });

      function setMode(mode, quiet) {
        state.mode = mode;
        tabConsole.classList.toggle("pc-on", mode === "console");
        tabScript.classList.toggle("pc-on", mode === "script");
        tabConsole.setAttribute("aria-selected", mode === "console" ? "true" : "false");
        tabScript.setAttribute("aria-selected", mode === "script" ? "true" : "false");
        consolePane.hidden = mode !== "console";
        scriptPane.hidden = mode !== "script";
        if (!quiet) keepWrite("console-mode", mode);
      }
      setMode("console", true);
      tabConsole.addEventListener("click", function () { setMode("console"); input.focus(); });
      tabScript.addEventListener("click", function () { setMode("script"); script.focus(); });

      /* ---- drawing what came back ---- */
      function typeset(el, latex, text) {
        el.textContent = text;
        if (!latex) return;
        Promise.resolve(api.katex()).then(function (katex) {
          if (!katex) return;
          try { el.textContent = ""; katex.render(latex, el, { throwOnError: false, displayMode: false }); el.title = text; }
          catch (e) { el.textContent = text; }
        }, function () {});
      }
      function drawItems(box, items) {
        (items || []).forEach(function (item) {
          if (item.kind === "display") {
            var math = h("div", { class: "pc-display", title: "Tap to copy it into the input" });
            typeset(math, item.latex, item.text);
            reusable(math, item.text);
            box.appendChild(math);
          } else {
            box.appendChild(h("pre", { class: "pc-stream pc-" + (item.kind === "error" ? "error" : item.kind) }, [item.text]));
          }
        });
      }
      function scrollDown(box) { box.scrollTop = box.scrollHeight; }

      /** A value shown in the transcript, tapped: its text goes into the
       *  input where the cursor was, to be used in the next line. */
      function reusable(el, text) {
        el.classList.add("pc-reusable");
        el.addEventListener("click", function () {
          if (window.getSelection && String(window.getSelection()).length) return;   // selecting text to copy it
          setMode("console");
          insertAtCaret(input, text);
          input.focus({ preventScroll: true });
        });
      }

      /** Keep the prompt where it is on the screen while `work` adds to the
       *  transcript above it: the transcript grows until its own scroll bar
       *  takes over, and the page would carry the prompt off the bottom -
       *  away from the keyboard, the field and the Run button just pressed. */
      function holdPrompt(work) {
        var before = inputRow.getBoundingClientRect().top;
        var adjust = function () {
          if (consolePane.hidden || !inputRow.isConnected) return;
          var d = inputRow.getBoundingClientRect().top - before;
          if (Math.abs(d) < 1) return;
          var box = inputRow.parentNode;
          while (box && box !== document.body && box.nodeType === 1) {
            var oy = getComputedStyle(box).overflowY;
            if ((oy === "auto" || oy === "scroll") && box.scrollHeight > box.clientHeight) break;
            box = box.parentNode;
          }
          if (box && box !== document.body && box.nodeType === 1) box.scrollTop += d;
          else window.scrollBy(0, d);
        };
        return Promise.resolve(work()).then(function (v) {
          adjust();
          requestAnimationFrame(adjust);            // the typeset output, a moment later
          setTimeout(adjust, 250);
          return v;
        });
      }
      function note(box, text, cls) {
        box.appendChild(h("div", { class: "pc-note" + (cls ? " " + cls : "") }, [text]));
        scrollDown(box);
      }

      function useButton(n) {
        var b = button("Use", "Put Out[" + n + "] in the formula: over the selection, or as the whole formula when nothing is selected", "pc-use");
        b.addEventListener("click", function () {
          var msg = where();
          msg.n = n;
          api.call("use", msg).catch(function (e) { api.error(String(e.message || e)); });
        });
        return b;
      }

      function drawCell(code, res) {
        var n = res && res.n != null ? res.n : "?";
        var entry = h("div", { class: "pc-entry" });
        var codeEl = h("pre", { class: "pc-code", title: "Tap to put it back in the input" }, [code]);
        codeEl.addEventListener("click", function () { input.value = code; autosize(); input.focus(); });
        entry.appendChild(h("div", { class: "pc-in" }, [h("span", { class: "pc-prompt pc-prompt-in" }, ["In [" + n + "]:"]), codeEl]));
        if (res) {
          drawItems(entry, res.items);
          if (res.out) {
            var math = h("div", { class: "pc-math", title: "Tap to copy it into the input" });
            typeset(math, res.out.latex, res.out.text);
            reusable(math, res.out.text);
            entry.appendChild(h("div", { class: "pc-out" }, [h("span", { class: "pc-prompt pc-prompt-out" }, ["Out[" + n + "]:"]), math, useButton(n)]));
          }
        }
        log.appendChild(entry);
        scrollDown(log);
      }

      /* ---- where the code's `editor.selection` points ---- */
      function where() {
        var msg = {};
        var range = api.range && api.range();
        if (range && range.parent != null && api.editor && api.editor._rangeIndices) {
          try { msg.path = range.parent; msg.children = api.editor._rangeIndices(); } catch (e) { msg = {}; }
        }
        if (!msg.path && api.selected()) msg.path = api.selected();
        return msg;
      }

      /* ---- after a run: the formula, if the code changed it ---- */
      function followUp(res) {
        if (!res) return Promise.resolve();
        if (res.token && state.token && res.token !== state.token) note(log, "A new namespace: the variables above are gone.", "pc-note-new");
        if (res.token) state.token = res.token;
        if (res.next) setNext(res.next);
        var p = res.changed ? Promise.resolve(api.send({ action: "snapshot" })) : Promise.resolve();
        return p.then(function () {
          if (res.select) { try { api.select(res.select); } catch (e) { /* gone */ } }
        });
      }
      function setNext(n) { state.next = n; prompt.textContent = "In [" + n + "]:"; }

      function failed(box, e) {
        var text = String((e && e.message) || e);
        if (/interrupt|terminated|restart/i.test(text)) text += "\n(The variables may be gone with it: a stopped computation takes its Python along in a page.)";
        box.appendChild(h("pre", { class: "pc-stream pc-error" }, [text]));
        scrollDown(box);
      }

      /* ---- the console's input ---- */
      function autosize() {
        input.rows = Math.min(12, Math.max(1, input.value.split("\n").length));
      }
      function remember(code) {
        if (!code.trim()) return;
        if (state.history[state.history.length - 1] !== code) state.history.push(code);
        if (state.history.length > HISTORY_MAX) state.history = state.history.slice(-HISTORY_MAX);
        state.pos = state.history.length;
        state.draft = "";
        keepWrite("console-history", JSON.stringify(state.history));
      }
      function insertAtCaret(field, text) {
        var s = field.selectionStart, e = field.selectionEnd;
        field.value = field.value.slice(0, s) + text + field.value.slice(e);
        field.selectionStart = field.selectionEnd = s + text.length;
        if (field === input) autosize();
      }
      function newline(field) {
        var before = field.value.slice(0, field.selectionStart);
        var line = before.slice(before.lastIndexOf("\n") + 1);
        var indent = (line.match(/^[ \t]*/) || [""])[0];
        if (/:\s*(#.*)?$/.test(line)) indent += "    ";
        insertAtCaret(field, "\n" + indent);
      }

      function run(force) {
        if (state.running) return;
        closeMenu();
        var code = input.value;
        if (!code.trim()) { input.value = ""; autosize(); return; }
        var msg = where();
        msg.code = code;
        msg.interactive = !force;
        state.running = true;
        hint.textContent = "";
        holdPrompt(function () {
          return api.call("run", msg).then(function (res) {
            state.running = false;
            if (res && res.incomplete) { newline(input); return; }
            remember(code);
            input.value = "";
            autosize();
            drawCell(code, res);
            return followUp(res);
          }, function (e) {
            state.running = false;
            drawCell(code, null);
            failed(log, e);
          });
        }).then(function () { input.focus({ preventScroll: true }); });
      }

      function recall(step) {
        if (!state.history.length) return false;
        closeMenu();
        if (state.pos === state.history.length) state.draft = input.value;
        var pos = Math.max(0, Math.min(state.history.length, state.pos + step));
        if (pos === state.pos) return false;
        state.pos = pos;
        input.value = pos === state.history.length ? state.draft : state.history[pos];
        autosize();
        input.selectionStart = input.selectionEnd = input.value.length;
        return true;
      }

      /* ---- completion: a menu at the caret ----
       * It opens by itself after a "." (the attributes of what is already in
       * memory: nothing is called to find them) and while a name is typed
       * that few names begin with (AUTO_MAX); Tab opens it with everything
       * that fits.  ↑/↓ choose, Enter or Tab take one, Esc closes it, and
       * typing on narrows it. */
      var AUTO_MAX = 12;
      var menu = h("div", { class: "pc-complete", role: "listbox", "aria-label": "Completions" });
      menu.hidden = true;
      inputRow.appendChild(menu);
      var comp = { items: [], shown: [], index: 0, start: 0, word: "", total: 0, auto: false, seq: 0, timer: null };

      function wordBeforeCaret() {
        var pos = input.selectionStart;
        if (input.selectionEnd !== pos) return null;
        var m = /[A-Za-z_][\w.]*$/.exec(input.value.slice(0, pos));
        return m ? { start: m.index, word: m[0], pos: pos } : null;
      }
      function lastPart(name) { return name.slice(name.lastIndexOf(".") + 1); }
      function closeMenu() {
        clearTimeout(comp.timer);
        comp.seq++;
        if (menu.hidden) return;
        menu.hidden = true;
        menu.textContent = "";
        comp.shown = [];
        input.removeAttribute("aria-activedescendant");
      }
      function menuOpen() { return !menu.hidden && comp.shown.length > 0; }

      /** Show the matches of `w` (the word at the caret) among what came back. */
      function showMatches(w) {
        var shown = comp.items.filter(function (it) { return it.name.lastIndexOf(w.word, 0) === 0; });
        var dotted = w.word.indexOf(".") >= 0;
        // Typing a plain name opens it only for a handful; a finished word closes it.
        if (!shown.length || (shown.length === 1 && shown[0].name === w.word)
            || (comp.auto && !dotted && (comp.total > comp.items.length ? comp.total : shown.length) > AUTO_MAX)) {
          closeMenu();
          return;
        }
        comp.shown = shown;
        comp.start = w.start;
        drawMenu(w);
        // The name as typed, when it is one, is the one Enter takes: Enter then runs.
        for (var i = 0; i < shown.length; i++) if (shown[i].name === w.word) highlight(i);
      }
      function drawMenu(w) {
        menu.textContent = "";
        var typed = lastPart(w.word);
        comp.shown.forEach(function (it, i) {
          var part = lastPart(it.name);
          var row = h("div", { class: "pc-comp-item", role: "option", id: "pc-comp-" + i, "data-name": it.name }, [
            h("span", { class: "pc-comp-name" }, [h("b", {}, [part.slice(0, typed.length)]), part.slice(typed.length)]),
            h("span", { class: "pc-comp-kind" }, [it.kind || ""])]);
          // A tap takes it and leaves the keyboard where it is.
          row.addEventListener("pointerdown", function (ev) { ev.preventDefault(); });
          row.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
          row.addEventListener("click", function () { comp.index = i; accept(); });
          menu.appendChild(row);
        });
        var more = comp.total - comp.items.length;
        if (more > 0 && comp.shown.length === comp.items.length) {
          menu.appendChild(h("div", { class: "pc-comp-more" }, ["… " + more + " more: keep typing"]));
        }
        menu.hidden = false;
        highlight(0);
        placeMenu();
      }
      function highlight(i) {
        var rows = menu.querySelectorAll(".pc-comp-item");
        if (!rows.length) return;
        comp.index = (i + rows.length) % rows.length;
        for (var r = 0; r < rows.length; r++) rows[r].classList.toggle("pc-comp-on", r === comp.index);
        var on = rows[comp.index];
        input.setAttribute("aria-activedescendant", on.id);
        if (on.offsetTop < menu.scrollTop) menu.scrollTop = on.offsetTop;
        else if (on.offsetTop + on.offsetHeight > menu.scrollTop + menu.clientHeight) menu.scrollTop = on.offsetTop + on.offsetHeight - menu.clientHeight;
      }
      /** Take the highlighted completion; false when it was already typed. */
      function accept() {
        var it = comp.shown[comp.index];
        var pos = input.selectionStart;
        closeMenu();
        if (!it) return false;
        var typed = input.value.slice(comp.start, pos);
        input.value = input.value.slice(0, comp.start) + it.name + input.value.slice(pos);
        input.selectionStart = input.selectionEnd = comp.start + it.name.length;
        autosize();
        input.focus({ preventScroll: true });
        return typed !== it.name;
      }

      /** The caret's place in the row: the field is monospaced, so a column
       *  and a line say where it is drawn. */
      var charWidth = 0;
      function placeMenu() {
        var cs = getComputedStyle(input);
        if (!charWidth) {
          var probe = h("span", { style: "position:absolute;visibility:hidden;white-space:pre;font:" + cs.font }, ["0000000000"]);
          inputRow.appendChild(probe);
          charWidth = probe.getBoundingClientRect().width / 10 || 8;
          inputRow.removeChild(probe);
        }
        var before = input.value.slice(0, comp.start);
        var line = before.split("\n").length - 1;
        var col = before.length - before.lastIndexOf("\n") - 1;
        var lh = parseFloat(cs.lineHeight) || 18;
        var rowBox = inputRow.getBoundingClientRect();
        var box = input.getBoundingClientRect();
        var x = box.left - rowBox.left + (parseFloat(cs.paddingLeft) || 0) + col * charWidth - input.scrollLeft;
        var lineTop = box.top - rowBox.top + (parseFloat(cs.paddingTop) || 0) + line * lh - input.scrollTop;
        menu.style.left = Math.max(0, Math.min(x, rowBox.width - menu.offsetWidth)) + "px";
        // Under the line when there is room - above it next to a phone's keyboard.
        var vv = window.visualViewport;
        var bottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
        var below = bottom - (rowBox.top + lineTop + lh);
        if (below >= Math.min(menu.offsetHeight, 160) || below > rowBox.top + lineTop) {
          menu.style.top = (lineTop + lh + 2) + "px";
          menu.style.bottom = "";
        } else {
          menu.style.top = "";
          menu.style.bottom = (rowBox.height - lineTop + 2) + "px";
        }
      }

      /** Ask Python for what completes the word at the caret. `auto`: the
       *  menu opened by itself, so a long list of plain names stays shut. */
      function askCompletions(auto) {
        var w = wordBeforeCaret();
        if (!w) { closeMenu(); return; }
        var seq = ++comp.seq;
        api.call("complete", { code: input.value, pos: w.pos }, { quiet: true }).then(function (res) {
          if (seq !== comp.seq || !res) return;
          var now = wordBeforeCaret();
          if (!now || now.start !== res.start || now.word.lastIndexOf(res.word, 0) !== 0) return;
          comp.items = (res.matches || []).map(function (name, i) { return { name: name, kind: (res.kinds || [])[i] || "" }; });
          comp.word = res.word;
          comp.total = res.total != null ? res.total : comp.items.length;
          comp.auto = auto;
          if (!auto) {
            if (!comp.items.length) { hint.textContent = "No completion."; return; }
            comp.start = res.start;
            if (comp.items.length === 1) { comp.shown = comp.items; comp.index = 0; accept(); return; }
            var prefix = comp.items.map(function (it) { return it.name; }).filter(function (name) {
              return name.lastIndexOf(now.word, 0) === 0;
            }).reduce(function (a, b) { var i = 0; while (a != null && i < a.length && a[i] === b[i]) i++; return a == null ? b : a.slice(0, i); }, null);
            if (prefix && prefix.length > now.word.length) {
              input.value = input.value.slice(0, now.start) + prefix + input.value.slice(now.pos);
              input.selectionStart = input.selectionEnd = now.start + prefix.length;
              autosize();
              now = wordBeforeCaret();
            }
          }
          showMatches(now);
        }, function () {});
      }
      /** After each change to the input: open, narrow or close the menu. */
      function completeAsTyped() {
        var pos = input.selectionStart;
        var w = wordBeforeCaret();
        if (state.running || !w || !/[\w.]/.test(input.value.charAt(pos - 1))) { closeMenu(); return; }
        var narrowing = !menu.hidden && w.start === comp.start && w.word.lastIndexOf(comp.word, 0) === 0
          && w.word.lastIndexOf(".") === comp.word.lastIndexOf(".") && comp.items.length === comp.total;
        if (narrowing) { showMatches(w); return; }
        closeMenu();
        clearTimeout(comp.timer);
        var dot = w.word.charAt(w.word.length - 1) === ".";
        comp.timer = setTimeout(function () { askCompletions(true); }, dot ? 0 : 150);
      }
      input.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== input) closeMenu(); }, 150); });
      input.addEventListener("scroll", function () { if (!menu.hidden) placeMenu(); });

      /** Tab: complete as far as every match agrees, then show the menu. */
      function complete(field) {
        var pos = field.selectionStart;
        if (field.selectionEnd !== pos || !/[A-Za-z0-9_.]$/.test(field.value.slice(0, pos))) return false;
        askCompletions(false);
        return true;
      }

      input.addEventListener("input", function () { autosize(); hint.textContent = ""; completeAsTyped(); });
      input.addEventListener("keydown", function (ev) {
        var k = ev.key;
        var mod = ev.ctrlKey || ev.metaKey;
        if (menuOpen() && !mod && !ev.altKey) {
          if (k === "ArrowDown" || k === "ArrowUp") { ev.preventDefault(); highlight(comp.index + (k === "ArrowDown" ? 1 : -1)); return; }
          if (k === "Tab" && !ev.shiftKey) { ev.preventDefault(); accept(); return; }
          if (k === "Enter" && !ev.shiftKey) {
            // A completion taken, or - when it was typed already - Enter as ever.
            if (accept()) { ev.preventDefault(); return; }
          }
          if (k === "Escape") { ev.preventDefault(); ev.stopPropagation(); closeMenu(); return; }
          if (k === "ArrowLeft" || k === "ArrowRight" || k === "Home" || k === "End") closeMenu();
        }
        if (k === "Enter" && mod) { ev.preventDefault(); run(true); return; }
        if (k === "Enter" && ev.shiftKey) { ev.preventDefault(); newline(input); return; }
        if (k === "Enter") {
          ev.preventDefault();
          // Inside a block being written, Enter is a new line; at its end it
          // runs - or, for an unfinished block, asks for the next line.
          if (input.selectionEnd < input.value.replace(/\s+$/, "").length) newline(input); else run(false);
          return;
        }
        if (k === "Tab" && !ev.shiftKey) {
          ev.preventDefault();
          if (!complete(input)) insertAtCaret(input, "    ");
          return;
        }
        if (k === "ArrowUp" && input.value.lastIndexOf("\n", input.selectionStart - 1) < 0) {
          if (recall(-1)) ev.preventDefault();
          return;
        }
        if (k === "ArrowDown" && input.value.indexOf("\n", input.selectionEnd) < 0) {
          if (recall(1)) ev.preventDefault();
          return;
        }
        if (mod && (k === "l" || k === "L")) { ev.preventDefault(); clear(); }
      });
      runBtn.addEventListener("click", function () { run(false); });
      // Pressing Run leaves the focus - and a phone's keyboard - in the field.
      runBtn.addEventListener("mousedown", function (ev) { ev.preventDefault(); });

      function clear() {
        if (state.mode === "script") scriptOut.textContent = "";
        else log.textContent = "";
        hint.textContent = "";
      }
      clearBtn.addEventListener("click", clear);
      resetBtn.addEventListener("click", function () {
        api.call("reset", {}).then(function (res) {
          if (res && res.token) state.token = res.token;
          if (res && res.next) setNext(res.next);
          note(state.mode === "script" ? scriptOut : log, "A fresh namespace: SymPy, editor and the formula's names.", "pc-note-new");
        }, function (e) { api.error(String(e.message || e)); });
      });

      /* ---- the script ---- */
      script.addEventListener("keydown", function (ev) {
        var mod = ev.ctrlKey || ev.metaKey;
        if (ev.key === "Enter" && mod) { ev.preventDefault(); runScript(); return; }
        if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); newline(script); keepScript(); return; }
        if (ev.key === "Tab" && !ev.shiftKey) { ev.preventDefault(); insertAtCaret(script, "    "); keepScript(); }
      });
      script.addEventListener("input", keepScript);
      nameField.addEventListener("input", keepScript);

      function runScript() {
        if (state.running) return;
        var msg = where();
        msg.code = script.value;
        msg.name = nameField.value || "script.py";
        state.running = true;
        scriptOut.textContent = "";
        api.call("script", msg).then(function (res) {
          state.running = false;
          drawItems(scriptOut, res && res.items);
          var failedRun = res && (res.items || []).some(function (i) { return i.kind === "error"; });
          note(scriptOut, (failedRun ? "Stopped" : "Done") + " in " + seconds(res && res.seconds)
               + (res && res.changed ? " - the formula changed (Undo takes it back)" : "")
               + "; what it defined is in the console.", failedRun ? "pc-note-bad" : "");
          return followUp(res);
        }, function (e) {
          state.running = false;
          failed(scriptOut, e);
        });
      }
      function seconds(s) {
        s = Number(s) || 0;
        return s < 1 ? (s * 1000).toPrecision(3) + " ms" : s.toPrecision(3) + " s";
      }
      runScriptBtn.addEventListener("click", runScript);

      openBtn.addEventListener("click", function () {
        if (!api.openFile) { api.error("This editor cannot open files"); return; }
        api.openFile(".py,text/x-python,text/plain").then(function (file) {
          if (!file) return;
          script.value = String(file.text || "");
          nameField.value = file.name || "script.py";
          keepScript();
          setMode("script");
        }, function (e) { api.error(String(e.message || e)); });
      });
      saveBtn.addEventListener("click", function () {
        if (!api.saveFile) { api.error("This editor cannot save files"); return; }
        var name = (nameField.value || "script.py").replace(/[\\/:*?"<>|]+/g, "_");
        if (!/\.py$/i.test(name)) name += ".py";
        api.saveFile(name, "text/x-python", script.value);
      });

      // The namespace behind the panel, and its next input number.
      api.call("hello", {}, { quiet: true }).then(function (res) {
        if (res && res.token) state.token = res.token;
        if (res && res.next) setNext(res.next);
      }, function () {});

      return {
        element: element,
        title: "Python console",
        help: HELP,
        onState: function (snap) {
          var c = snap && !snap.preview && snap.console;
          if (!c) return;
          if (state.token && c.token !== state.token) {
            note(log, "A new namespace (another session, or Python restarted): the variables above are gone.", "pc-note-new");
            state.token = c.token;
          }
          if (c.next) setNext(c.next);
        },
        destroy: function () { clearTimeout(scriptTimer); }
      };
    }
  });
})();
