/*
 * anywidget entry point.  widget.py concatenates editor.js and this file into
 * one ES module, so `SympyEditor` is in scope here.
 *
 * Messages go to the kernel with model.send(); the kernel answers each one by
 * updating the "snapshot" trait (JSON string), which we apply to the editor
 * and use to settle the promise of the message it answers.  "interrupt" gets
 * no answer of its own: the interrupted message answers with the error.
 */
function render({ model, el }) {
  const waiting = [];   // resolvers of the messages awaiting a snapshot, oldest first
  const backend = {
    send: (msg) => new Promise((resolve) => { waiting.push(resolve); model.send(msg); }),
    interrupt: () => { model.send({ action: "interrupt" }); return true; },
  };
  const editor = new SympyEditor.Editor(el, backend, model.get("options") || {});
  const apply = () => {
    const raw = model.get("snapshot");
    if (!raw) return;
    const done = waiting.shift();
    editor.setState(JSON.parse(raw)).then(() => { if (done) done(null); });
  };
  model.on("change:snapshot", apply);
  apply();
  return () => {
    model.off("change:snapshot", apply);
    editor.destroy();
  };
}

export default { render };
