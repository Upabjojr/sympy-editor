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
/// Every method here must be called from one and the same thread; the
/// interpreter is entered under the GIL for the duration of a call.  Swift
/// keeps that promise with a serial queue - see PythonBridge in EditorView.swift.
/// The domain of the errors these methods report.
extern NSErrorDomain const SymPyEditorPythonErrorDomain;

@interface PythonRuntime : NSObject

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
