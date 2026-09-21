/*
 * anywidget entry point.  widget.py concatenates editor.js and this file into
 * one ES module, so `SympyEditor` is in scope here.
 *
 * Messages go to the kernel with model.send(), each with a request id; the
 * kernel answers each one either by updating the "snapshot" trait (JSON
 * string) - a committed state, which every display of the widget draws - or
 * with a custom message of its own - an answer for the view that asked and
 * nobody else: a preview, a query, the session to keep, an error that changed
 * nothing, what the store kept, a file written.  Either way the answer
 * settles the promise of the message it answers, with the snapshot itself -
 * the same contract as the HTTP and Pyodide backends, whose send() resolves
 * to the answer and applies nothing: whoever sent the message applies what it
 * needs of it.  A snapshot in the trait that answers nothing of this view's
 * (the first, one the kernel pushes on its own after `w.expr = ...` in a
 * cell, or an edit made in another display) is applied here.  "interrupt"
 * gets no answer of its own: the interrupted message answers with the error.
 */
function render({ model, el }) {
  // Each message carries a request id and its answer brings it back, so the
  // two are paired by id - not by order.  The ids are this view's own (a
  // random prefix): two displays of one widget share the kernel and see each
  // other's answers, and both counting from 1 paired one's answer with the
  // other's message.
  const prefix = "v" + Math.random().toString(36).slice(2, 10) + ":";
  let seq = 0;
  const pending = {};
  let closed = false;
  const ask = (msg) => new Promise((resolve, reject) => {
    if (closed) { reject(new Error("This view of the widget is closed")); return; }
    const id = prefix + (++seq);
    pending[id] = { resolve, reject };
    model.send(Object.assign({ _req: id }, msg));
  });
  const settle = (answer) => {
    const p = answer && pending[answer._req];
    if (!p) return false;
    delete pending[answer._req];
    p.resolve(answer);
    return true;
  };
  const answered = (msg) => { settle(msg); };
  model.on("msg:custom", answered);
  const backend = {
    send: (msg) => ask(msg),
    interrupt: () => { model.send({ action: "interrupt" }); return true; },
    /** A file the editor saves (a formula, the history), written by the
     *  kernel next to the notebook: a download from inside a notebook is
     *  refused by more front ends than accept it (VS Code, among others). */
    saveFile: (name, mime, text) => ask({ action: "writefile", name: name, mime: mime, text: text })
      .then((answer) => { if (answer.error) throw new Error(answer.error); return answer.saved; }),
    // The widget holds one document, which a session replaces ("load"); it
    // was handed in by the notebook, so the sessions keep it rather than
    // opening the last one over it.
    openDocument: SympyEditor.loadThrough((msg) => ask(msg)),
    givenDocument: true,
  };
  // What the page keeps - sessions, add-on switches, the zoom, an add-on's
  // library - is kept by the kernel (the widget's Store), not in the
  // browser storage of whatever page shows the notebook.  The editor keeps
  // through its own backend (each view its own).
  backend.keep = SympyEditor.keepThrough(ask);
  const editor = new SympyEditor.Editor(el, backend, model.get("options") || {});
  let started = null;
  const apply = () => {
    const raw = model.get("snapshot");
    if (!raw) return;
    const snap = JSON.parse(raw);
    // To the sender, which applies it: Editor.send and the preview call
    // setState themselves, and the rest read their answer off it.
    if (settle(snap)) return;
    const shown = editor.setState(snap);
    // The first state shown starts the sessions (as mount does for a page),
    // so that a notebook keeps them in the kernel's store too.
    if (!started) started = shown.then(() => editor._initSessions()).catch(() => {}).then(() => editor._restoreAddons());
    else shown.then(() => editor._restoreAddons());
  };
  model.on("change:snapshot", apply);
  apply();
  return () => {
    model.off("change:snapshot", apply);
    model.off("msg:custom", answered);
    editor.destroy();
    closed = true;
    // What this view still waits for will never come: said, not left hanging.
    for (const id in pending) {
      const p = pending[id];
      delete pending[id];
      p.reject(new Error("This view of the widget is closed"));
    }
  };
}

export default { render };
