#import <Python/Python.h>

/// `_sympy_ort`: ONNX Runtime for the app's Python, as a module built into
/// the app (see OrtModule.m).  PythonRuntime puts it in the table of built-in
/// modules before the interpreter starts.
PyMODINIT_FUNC PyInit__sympy_ort(void);
