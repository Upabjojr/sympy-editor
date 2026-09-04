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
    var once = h("button", { type: "button", title: "Apply the first rule that matches the selection, or a piece inside it" }, ["Rewrite"]);
    var all = h("button", { type: "button", title: "Apply the rules again and again until none matches" }, ["Rewrite all"]);
    var hits = h("div", { class: "mt-hits" });
    var element = h("div", { class: "mt-panel" }, [
      h("div", { class: "mt-head" }, [h("strong", {}, ["Rules"]), use]),
      empty, list,
      h("div", { class: "mt-row" }, [field, add]),
      h("div", { class: "mt-head" }, [h("strong", {}, ["Matching the selection"]), once, all]),
      hits
    ]);

    var rules = [], seq = 0, timer = null, katex = null;
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
        var formula = h("span", { class: "mt-formula", title: r.src });
        tex(formula, r.latex, r.src);
        var del = h("button", { type: "button", class: "mt-del", title: "Remove this rule", "aria-label": "Remove rule " + (r.index + 1) }, ["×"]);
        del.addEventListener("click", function () { query("remove_rule", { index: r.index }); });
        list.appendChild(h("li", { "data-index": String(r.index) }, [formula, del]));
      });
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
        var node = api.node(path);
        use.disabled = !(node && node.type === "RewriteRule");
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
    use.addEventListener("click", function () { query("use_selection", { path: target() }); });
    once.addEventListener("click", function () { api.call("rewrite", { path: target() }).then(null, fail); });
    all.addEventListener("click", function () { api.call("rewrite", { path: target(), all: true }).then(null, fail); });

    query("rules");

    return {
      element: element,
      title: "Rewrite rules",
      onState: function (snap) { if (!snap.preview) askMatches(); },
      onSelect: function () { askMatches(); },
      destroy: function () { clearTimeout(timer); seq++; }
    };
  }
});
