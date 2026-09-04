/*
 * sympy-editor add-on "matching": the rule set, what matches the selection,
 * and the buttons that apply a rule.  All the matching is Python's
 * (sympy-matching); this only asks and shows.
 */
SympyEditor.registerAddon("matching", {
  mount: function (api) {
    var h = api.h;
    var list = h("ol", { class: "mt-rules" });
    var empty = h("div", { class: "mt-empty" }, ["No rule yet: type one below, e.g.  sin(a_)**2 -> 1 - cos(a_)**2"]);
    var field = h("input", { type: "text", class: "mt-field", placeholder: "pattern -> replacement  [if condition]",
      title: "A rule in SymPy syntax: a name ending in _ is a wildcard (a_), one in underscores an optional one (_a_); Enter adds it",
      spellcheck: "false", autocomplete: "off" });
    var add = h("button", { type: "button", title: "Add the rule to the set" }, ["Add rule"]);
    var use = h("button", { type: "button", title: "The selected Rule(...) node joins the set", disabled: "" }, ["Use selection as rule"]);
    var once = h("button", { type: "button", title: "One pass over the selection: every piece a rule matches is replaced, outermost first; what a rule produced is not rewritten again" }, ["Rewrite"]);
    var all = h("button", { type: "button", title: "Pass after pass until no rule matches any more (a rule that matches its own result never settles: after 50 passes this is refused and nothing changes)" }, ["Rewrite all"]);
    var hits = h("div", { class: "mt-hits" });
    var element = h("div", { class: "mt-panel" }, [
      h("div", { class: "mt-head" }, [h("strong", {}, ["Rules"]), use]),
      empty, list,
      h("div", { class: "mt-row" }, [field, add]),
      h("div", { class: "mt-head" }, [h("strong", {}, ["Matching the selection"]), once, all]),
      hits
    ]);

    var rules = [], seq = 0, timer = null, katex = null;
    var editing = null;      // the index of the rule opened in the formula editor, until it is saved or dropped
    api.katex().then(function (k) { katex = k; renderRules(); }, function () { /* the sources are shown instead */ });

    function tex(el, latexSrc, plain) {
      if (katex) {
        try { katex.render(latexSrc, el, { throwOnError: true, displayMode: false }); return; } catch (e) { /* fall back */ }
      }
      el.textContent = plain;
    }

    function renderRules() {
      list.textContent = "";
      empty.hidden = rules.length > 0;
      rules.forEach(function (r) {
        var formula = h("span", { class: "mt-formula", title: r.src + "  (double-click to edit as text)" });
        tex(formula, r.latex, r.src);
        formula.addEventListener("dblclick", function () { editRule(r, row); });
        var edit = h("button", { type: "button", class: "mt-edit", title: "Edit this rule as text: pattern -> replacement [if condition]", "aria-label": "Edit rule " + (r.index + 1) }, ["\u270e"]);
        edit.addEventListener("click", function () { editRule(r, row); });
        var open = h("button", { type: "button", class: "mt-open", title: "Open this rule in the formula editor, to edit its sides there; Save puts it back", "aria-label": "Open rule " + (r.index + 1) + " in the editor" }, ["\u2197"]);
        open.addEventListener("click", function () {
          api.call("open_rule", { index: r.index }).then(function () { editing = r.index; updateSaveButton(); }, fail);
        });
        var del = h("button", { type: "button", class: "mt-del", title: "Remove this rule", "aria-label": "Remove rule " + (r.index + 1) }, ["\u00d7"]);
        del.addEventListener("click", function () { if (editing === r.index) editing = null; query("remove_rule", { index: r.index }); });
        var row = h("li", { "data-index": String(r.index), class: editing === r.index ? "mt-editing" : "" }, [formula, edit, open, del]);
        list.appendChild(row);
      });
      updateSaveButton();
    }

    /** The row becomes a field with the rule's text; Enter saves, Escape leaves it. */
    function editRule(r, row) {
      var field = h("input", { type: "text", class: "mt-field mt-inline", value: r.text || r.src, spellcheck: "false", autocomplete: "off",
        title: "pattern -> replacement  [if condition]; Enter saves, Esc cancels" });
      var done = function (save) {
        if (!field.parentNode) return;
        var text = field.value.trim();
        if (save && text && text !== r.text) query("update_rule", { index: r.index, src: text });
        else renderRules();
      };
      field.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key === "Enter") { ev.preventDefault(); done(true); }
        else if (ev.key === "Escape") { ev.preventDefault(); done(false); }
      });
      field.addEventListener("blur", function () { setTimeout(function () { done(true); }, 0); });
      row.textContent = "";
      row.appendChild(field);
      field.focus();
      field.select();
    }

    /** "Use selection as rule" adds a rule; while a rule is open in the
     *  editor it reads "Save as rule N" and puts the selection back over it. */
    function updateSaveButton() {
      var node = api.node(api.selected() || "/");
      var isRule = !!(node && node.type === "RewriteRule");
      if (editing !== null && editing < rules.length) {
        use.textContent = "Save as rule " + (editing + 1);
        use.title = "Put the selected Rule(...) back over rule " + (editing + 1) + " (it was opened from there)";
        use.disabled = !isRule;
      } else {
        editing = null;
        use.textContent = "Use selection as rule";
        use.title = "The selected Rule(...) node joins the set";
        use.disabled = !isRule;
      }
    }

    function fail(e) { api.error(String(e && e.message || e)); }

    function query(method, payload) {
      return api.call(method, payload || {}).then(function (res) {
        if (res && res.rules) { rules = res.rules; renderRules(); askMatches(); }
        return res;
      }, fail);
    }

    function target() { return api.selected() || "/"; }

    function askMatches() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (api.busy()) { askMatches(); return; }
        var my = ++seq, path = target();
        updateSaveButton();
        if (!rules.length) { hits.textContent = ""; return; }
        api.call("matches", { path: path }).then(function (res) {
          if (my !== seq) return;
          showHits(res);
        }, function () { /* a stale selection: nothing to show */ });
      }, 120);
    }

    function showHits(res) {
      hits.textContent = "";
      if (!res.matches.length) {
        hits.appendChild(h("div", { class: "mt-empty" }, ["No rule matches " + res.src + " at its root; Rewrite looks inside it too."]));
        return;
      }
      res.matches.forEach(function (m) {
        var rule = rules[m.index];
        var bound = Object.keys(m.bindings).map(function (k) { return k + " = " + m.bindings[k]; }).join(",  ");
        var apply = h("button", { type: "button", class: "mt-apply", title: "Apply this rule here" }, ["Apply"]);
        apply.addEventListener("click", function () {
          api.call("rewrite", { path: res.path, index: m.index }).then(null, fail);
        });
        var formula = h("span", { class: "mt-formula" });
        if (rule) tex(formula, rule.latex, rule.src);
        hits.appendChild(h("div", { class: "mt-hit" }, [
          h("span", { class: "mt-num" }, ["rule " + (m.index + 1)]), formula,
          h("code", { class: "mt-bind" }, [bound || "no wildcard"]),
          h("code", { class: "mt-result" }, ["→ " + m.result]), apply
        ]));
      });
    }

    function addRule() {
      var src = field.value.trim();
      if (!src) return;
      query("add_rule", { src: src }).then(function (res) { if (res) field.value = ""; });
    }
    add.addEventListener("click", addRule);
    field.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.key === "Enter") { ev.preventDefault(); addRule(); }
    });
    use.addEventListener("click", function () {
      if (editing !== null) {
        var index = editing;
        query("update_rule", { index: index, path: target() }).then(function (res) { if (res) { editing = null; renderRules(); } });
      } else {
        query("use_selection", { path: target() });
      }
    });
    once.addEventListener("click", function () { api.call("rewrite", { path: target() }).then(null, fail); });
    all.addEventListener("click", function () { api.call("rewrite", { path: target(), all: true }).then(null, fail); });

    query("rules");

    var HELP = [
      "<section><h3>Rules and wildcards</h3><ul>",
      "<li>A rule is <code>pattern -&gt; replacement</code>, optionally <code>if condition</code>, in SymPy syntax: <code>sin(a_)**2 -&gt; 1 - cos(a_)**2</code>, <code>x**m_ -&gt; x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)</code>.</li>",
      "<li>A name ending in <code>_</code> is a <b>wildcard</b>: <code>a_</code> matches anything and binds it; the same name binds the same thing everywhere in the rule. A name in underscores, <code>_a_</code>, is an <b>optional</b> wildcard: absent, it takes the identity of its slot (0 in a sum, 1 in a product or an exponent). Any other name, <code>x</code>, matches only itself.</li>",
      "<li>Wildcards are drawn underlined in the formula; an optional one in brackets.</li>",
      "<li>The condition is a SymPy Boolean over the wildcards, checked after the structure matches.</li>",
      "</ul></section>",
      "<section><h3>The set</h3><ul>",
      "<li>Type a rule in the field and press <kbd>Enter</kbd> or <b>Add rule</b>. The pencil (or a double-click on a rule) edits it as text; <b>\u2197</b> opens it in the formula editor as a <code>Rule(…)</code> node \u2014 edit its sides there, then <b>Save as rule N</b> puts it back. <b>\u00d7</b> removes it.</li>",
      "<li>A <code>Rule(pattern, replacement[, condition])</code> typed in the editor is a node like any other: <b>Use selection as rule</b> adds the selected one to the set; its type menu can swap its sides.</li>",
      "<li>All the rules are compiled into one many-to-one matcher (sympy-matching, OmniMatch) when the set changes: a query walks it once whatever the number of rules.</li>",
      "</ul></section>",
      "<section><h3>Matching and rewriting</h3><ul>",
      "<li><b>Matching the selection</b> lists the rules whose pattern matches the selected piece at its root, with what each wildcard bound and the result; <b>Apply</b> rewrites that piece with that rule.</li>",
      "<li><b>Rewrite</b> makes one pass over the selection (the whole expression when nothing is selected), outermost first: every piece a rule matches is replaced, and what a rule produced is left alone in that pass \u2014 <code>x -&gt; x**2</code> on <code>x + sin(x)</code> gives <code>x**2 + sin(x**2)</code>, once.</li>",
      "<li><b>Rewrite all</b> repeats the pass until no rule matches. A rule that matches its own result never settles: after 50 passes it is refused, with a message, and the expression stays as it was.</li>",
      "<li>The same two are in the <b>Transform \u25be</b> menu. Every rewrite is a step of the history: <kbd>Ctrl</kbd>+<kbd>Z</kbd> takes it back.</li>",
      "</ul></section>"
    ].join("");

    return {
      element: element,
      title: "Rewrite rules",
      help: HELP,
      onState: function (snap) { if (!snap.preview) askMatches(); },
      onSelect: function () { askMatches(); },
      destroy: function () { clearTimeout(timer); seq++; }
    };
  }
});
