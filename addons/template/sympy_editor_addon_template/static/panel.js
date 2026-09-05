/*
 * The browser part of the template add-on.  A plain script, run once per
 * page with `SympyEditor` in scope; it registers under the add-on's name.
 * `mount(api)` is called by every editor whose document has the add-on.
 */
SympyEditor.registerAddon("template", {
  // Toolbar buttons: the editor makes a block of them and disables them
  // while it is busy.  `run(api)` may call the add-on's Python.
  tools: [
    { cmd: "double", label: "Double", title: "Twice the expression (the add-on's Python does it)",
      run: function (api) { return api.call("double", {}); } }
  ],
  mount: function (api) {
    var h = api.h;                                     // the editor's element helper
    var info = h("span", { class: "tpl-info" }, [api.options.greeting]);
    var count = h("button", { type: "button" }, ["Count the selection"]);
    var out = h("code", { class: "tpl-out" });
    count.addEventListener("click", function () {
      // A query: the promise resolves with the dict Python returned.
      api.call("count", { path: api.selected() || "/" }).then(function (res) {
        out.textContent = res.src + " has " + res.args + " argument(s)";
      }, function (e) { api.error(String(e.message || e)); });
    });
    var element = h("div", { class: "tpl-panel" }, [info, count, out]);
    return {
      element: element,                                // shown in a box under the formula
      title: "Template",                               // the box's heading
      help: "<section><h3>What this add-on does</h3><ul><li>The guide behind the panel's \"?\": HTML, shown like the editor's own.</li>"
          + "<li>Say what each control does and what the add-on changes.</li></ul></section>",
      onState: function (snap) {                       // every snapshot (previews too)
        if (!snap.preview && snap.template) info.textContent = api.options.greeting + " - " + snap.template.args + " argument(s), " + snap.template.atoms + " atom(s)";
      },
      onSelect: function (path) {                      // the selection changed
        count.textContent = path ? "Count " + path : "Count the whole expression";
      },
      destroy: function () {}                          // the editor is removed
    };
  }
});
