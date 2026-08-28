/*
 * anywidget entry point.  widget.py concatenates editor.js and this file into
 * one ES module, so `SympyEditor` is in scope here.
 *
 * Messages go to the kernel with model.send(); the kernel answers by updating
 * the "snapshot" trait (JSON string), which we apply to the editor.
 */
function render({ model, el }) {
  const backend = {
    send: async (msg) => {
      model.send(msg);
      return null; // the snapshot arrives through the trait below
    },
  };
  const editor = new SympyEditor.Editor(el, backend, model.get("options") || {});
  const apply = () => {
    const raw = model.get("snapshot");
    if (raw) editor.setState(JSON.parse(raw));
  };
  model.on("change:snapshot", apply);
  apply();
  return () => {
    model.off("change:snapshot", apply);
    editor.destroy();
  };
}

export default { render };
