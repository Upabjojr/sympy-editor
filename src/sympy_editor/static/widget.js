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
  // Each message carries a request id and its answer brings it back, so the
  // two are paired by id - not by order.  Answers used to be matched to the
  // oldest waiting message: a snapshot the kernel pushed on its own (a
  // `w.expr = ...` in a cell) or a message that never answered put every
  // later pairing off by one, and one send() then never settled.
  const pending = {};
  let seq = 0;
  const backend = {
    send: (msg) => new Promise((resolve) => {
      const id = ++seq;
      pending[id] = resolve;
      model.send(Object.assign({ _req: id }, msg));
    }),
    interrupt: () => { model.send({ action: "interrupt" }); return true; },
  };
  const editor = new SympyEditor.Editor(el, backend, model.get("options") || {});
  const apply = () => {
    const raw = model.get("snapshot");
    if (!raw) return;
    const snap = JSON.parse(raw);
    const done = pending[snap._req];
    delete pending[snap._req];
    editor.setState(snap).then(() => { if (done) done(null); });
  };
  model.on("change:snapshot", apply);
  apply();
  return () => {
    model.off("change:snapshot", apply);
    editor.destroy();
  };
}

export default { render };
