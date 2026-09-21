/*
 * anywidget entry point.  widget.py concatenates editor.js and this file into
 * one ES module, so `SympyEditor` is in scope here.
 *
 * Messages go to the kernel with model.send(); the kernel answers each one by
 * updating the "snapshot" trait (JSON string).  An answer settles the promise
 * of the message it answers, with the snapshot itself - the same contract as
 * the HTTP and Pyodide backends, whose send() resolves to the answer and
 * applies nothing: whoever sent the message applies what it needs of it.  A
 * snapshot that answers nothing (the first, or one the kernel pushes on its
 * own after `w.expr = ...` in a cell) is applied here.  "interrupt" gets no
 * answer of its own: the interrupted message answers with the error.
 */
function render({ model, el }) {
  // Each message carries a request id and its answer brings it back, so the
  // two are paired by id - not by order.  Answers used to be matched to the
  // oldest waiting message: a snapshot the kernel pushed on its own (a
  // `w.expr = ...` in a cell) or a message that never answered put every
  // later pairing off by one, and one send() then never settled.
  const pending = {};
  let seq = 0;
  // Questions that are not edits (keep, writefile) are answered by a
  // message of their own, not the snapshot trait - which must only ever
  // hold a snapshot, being what a second display of the widget draws.
  const asked = {};
  const ask = (msg) => new Promise((resolve) => {
    const id = ++seq;
    asked[id] = resolve;
    model.send(Object.assign({ _req: id }, msg));
  });
  const answered = (msg) => {
    const done = msg && asked[msg._req];
    if (!done) return;
    delete asked[msg._req];
    done(msg);
  };
  model.on("msg:custom", answered);
  const backend = {
    send: (msg) => new Promise((resolve) => {
      const id = ++seq;
      pending[id] = resolve;
      model.send(Object.assign({ _req: id }, msg));
    }),
    interrupt: () => { model.send({ action: "interrupt" }); return true; },
    /** A file the editor saves (a formula, the history), written by the
     *  kernel next to the notebook: a download from inside a notebook is
     *  refused by more front ends than accept it (VS Code, among others). */
    saveFile: (name, mime, text) => ask({ action: "writefile", name: name, mime: mime, text: text })
      .then((answer) => { if (answer.error) throw new Error(answer.error); return answer.saved; }),
  };
  // What the page keeps - sessions, add-on switches, the zoom, an add-on's
  // library - is kept by the kernel (the widget's Store), not in the
  // browser storage of whatever page shows the notebook.
  backend.keep = SympyEditor.keepThrough(ask);
  SympyEditor.setKeeper(backend);
  const editor = new SympyEditor.Editor(el, backend, model.get("options") || {});
  const apply = () => {
    const raw = model.get("snapshot");
    if (!raw) return;
    const snap = JSON.parse(raw);
    const done = pending[snap._req];
    if (done) {
      // To the sender, which applies it: Editor.send and the preview call
      // setState themselves, and the rest read their answer off it - an
      // add-on's method its result, the function picker its list, a save its
      // session.  This used to settle them with null after applying the
      // snapshot here, so every caller that reads its answer got nothing: the
      // plot said "No answer" and drew nothing, the LaTeX reader never
      // parsed, and a session could not be saved from a notebook.
      delete pending[snap._req];
      done(snap);
      return;
    }
    editor.setState(snap).then(() => editor._restoreAddons());
  };
  model.on("change:snapshot", apply);
  apply();
  return () => {
    model.off("change:snapshot", apply);
    model.off("msg:custom", answered);
    editor.destroy();
  };
}

export default { render };
