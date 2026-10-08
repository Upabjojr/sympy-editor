#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// The app's own CPython, and the one module the page talks to.
///
/// The iOS app ships the interpreter and SymPy (Python.xcframework and the
/// `python`, `app` and `app_packages` folders of the bundle, assembled by
/// mobile/build.py) instead of running them in the browser: the same choice
/// the Android app makes with Chaquopy, and for the same reasons - the edit
/// is immediate, nothing is downloaded, and it is the very
/// `sympy_editor.document.Document` the server and the Jupyter widget use.
///
/// There is one interpreter per process, so there is one runtime: `shared`.
/// CPython cannot be initialized twice - a second window of the Mac app that
/// made a runtime of its own failed in Py_InitializeFromConfig ("failed to
/// read thread state") and never had a working Python.  Every window's
/// bridge uses this one, on one serial queue (PythonHost in EditorView.swift);
/// the interpreter is entered under the GIL for the duration of a call, so
/// `interrupt` may come from another thread.
/// The domain of the errors these methods report.
extern NSErrorDomain const SymPyEditorPythonErrorDomain;

@interface PythonRuntime : NSObject

/// The process's one runtime.  Do not make another: there is one interpreter.
@property (class, nonatomic, readonly) PythonRuntime *shared;

/// Start the interpreter and import `sympy_editor_app`.  Calling it again
/// after it has succeeded is harmless.
- (BOOL)startAndReturnError:(NSError **)error;

/// Call `function` in `sympy_editor_app` with string arguments, and return
/// what it returned as a string (the module answers in JSON).  A Python
/// exception comes back as an error whose description is the message the
/// interpreter would print.
- (nullable NSString *)call:(NSString *)function
                  arguments:(NSArray<NSString *> *)arguments
                      error:(NSError **)error;

@end

NS_ASSUME_NONNULL_END
