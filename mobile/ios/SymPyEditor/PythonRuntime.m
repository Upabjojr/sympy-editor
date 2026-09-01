#import "PythonRuntime.h"

#import <Python/Python.h>

NSErrorDomain const SymPyEditorPythonErrorDomain = @"org.sympy.editor.python";

/// Wrap a message as an error to hand back to Swift.
static NSError *pythonError(NSString *message) {
    return [NSError errorWithDomain:SymPyEditorPythonErrorDomain code:1
                           userInfo:@{NSLocalizedDescriptionKey: message}];
}

/// The text of an object, or nil if it is not a string.
static NSString *stringFrom(PyObject *object) {
    if (object == NULL || !PyUnicode_Check(object)) return nil;
    const char *utf8 = PyUnicode_AsUTF8(object);
    return utf8 ? [NSString stringWithUTF8String:utf8] : nil;
}

/// Take the pending exception and describe it the way the interpreter would
/// ("ValueError: ..."), leaving no error set behind.
static NSString *drainError(void) {
    if (!PyErr_Occurred()) return @"unknown Python error";
    PyObject *type = NULL, *value = NULL, *traceback = NULL;
    PyErr_Fetch(&type, &value, &traceback);
    PyErr_NormalizeException(&type, &value, &traceback);

    NSString *text = nil;
    PyObject *module = PyImport_ImportModule("traceback");
    if (module != NULL && type != NULL) {
        PyObject *lines = PyObject_CallMethod(module, "format_exception_only", "OO",
                                              type, value ? value : Py_None);
        if (lines != NULL) {
            PyObject *separator = PyUnicode_FromString("");
            PyObject *joined = separator ? PyObject_CallMethod(separator, "join", "O", lines) : NULL;
            text = stringFrom(joined);
            Py_XDECREF(joined);
            Py_XDECREF(separator);
            Py_DECREF(lines);
        }
    }
    if (text == nil && value != NULL) {       // traceback itself failed: say what we can
        PyObject *fallback = PyObject_Str(value);
        text = stringFrom(fallback);
        Py_XDECREF(fallback);
    }
    PyErr_Clear();                            // format_exception_only may have raised in turn
    Py_XDECREF(module);
    Py_XDECREF(type);
    Py_XDECREF(value);
    Py_XDECREF(traceback);

    text = [(text ?: @"unknown Python error") stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    return text.length ? text : @"unknown Python error";
}

@implementation PythonRuntime {
    PyObject *_app;        // the sympy_editor_app module
}

- (void)dealloc {
    // The interpreter lives as long as the app does; nothing to finalize.
}

/// `site.addsitedir(packages)` (so any .pth file there is honoured) and
/// `sys.path.insert(0, app)`, the two directories mobile/build.py stages.
- (BOOL)addPackages:(NSString *)packages app:(NSString *)app error:(NSError **)error {
    PyObject *site = PyImport_ImportModule("site");
    PyObject *added = site ? PyObject_CallMethod(site, "addsitedir", "s", packages.UTF8String) : NULL;
    Py_XDECREF(site);
    if (added == NULL) { if (error) *error = pythonError(drainError()); return NO; }
    Py_DECREF(added);

    PyObject *sys = PyImport_ImportModule("sys");
    PyObject *path = sys ? PyObject_GetAttrString(sys, "path") : NULL;
    PyObject *inserted = path ? PyObject_CallMethod(path, "insert", "is", 0, app.UTF8String) : NULL;
    Py_XDECREF(path);
    Py_XDECREF(sys);
    if (inserted == NULL) { if (error) *error = pythonError(drainError()); return NO; }
    Py_DECREF(inserted);
    return YES;
}

- (BOOL)startAndReturnError:(NSError **)error {
    if (_app != NULL) return YES;

    NSString *resources = [NSBundle mainBundle].resourcePath;
    PyStatus status;

    // An isolated interpreter: it must read nothing of the environment, and
    // it cannot write .pyc files next to a bundle that is already signed.
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        if (error) *error = pythonError([NSString stringWithFormat:@"cannot pre-initialize Python: %s", status.err_msg]);
        return NO;
    }

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.write_bytecode = 0;
    config.install_signal_handlers = 0;   // the app owns its signals, not Python

    // PYTHONHOME: `python/lib/python3.x` in the bundle, put there by the
    // "Process Python libraries" build phase (Python.xcframework/build/utils.sh).
    wchar_t *home = Py_DecodeLocale([[resources stringByAppendingPathComponent:@"python"] UTF8String], NULL);
    status = PyConfig_SetString(&config, &config.home, home);
    PyMem_RawFree(home);
    if (!PyStatus_Exception(status)) status = PyConfig_Read(&config);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        if (error) *error = pythonError([NSString stringWithFormat:@"cannot start Python: %s", status.err_msg]);
        return NO;
    }

    // Py_InitializeFromConfig left the GIL held by this thread; whether the
    // rest works or not, hand it back before returning, so that every call
    // from here on takes it the same way (-call:arguments:error:).
    BOOL ready = [self addPackages:[resources stringByAppendingPathComponent:@"app_packages"]
                               app:[resources stringByAppendingPathComponent:@"app"]
                             error:error];
    if (ready) {
        _app = PyImport_ImportModule("sympy_editor_app");
        if (_app == NULL) {
            if (error) *error = pythonError(drainError());
            ready = NO;
        }
    }
    PyEval_SaveThread();
    return ready;
}

- (NSString *)call:(NSString *)function
         arguments:(NSArray<NSString *> *)arguments
             error:(NSError **)error {
    PyGILState_STATE gil = PyGILState_Ensure();
    NSString *answer = nil;

    PyObject *callable = PyObject_GetAttrString(_app, function.UTF8String);
    PyObject *argv = callable ? PyTuple_New((Py_ssize_t)arguments.count) : NULL;
    if (argv != NULL) {
        for (NSUInteger i = 0; i < arguments.count; i++) {
            PyObject *item = PyUnicode_FromString(arguments[i].UTF8String);
            if (item == NULL) { Py_CLEAR(argv); break; }
            PyTuple_SET_ITEM(argv, (Py_ssize_t)i, item);   // steals the reference
        }
    }
    PyObject *result = argv ? PyObject_CallObject(callable, argv) : NULL;
    if (result == NULL) {
        if (error) *error = pythonError(drainError());
    } else {
        // Everything the module returns is a string but `close`, which returns None.
        answer = (result == Py_None) ? @"" : stringFrom(result);
        if (answer == nil && error) *error = pythonError([NSString stringWithFormat:@"%@ did not return a string", function]);
    }
    Py_XDECREF(result);
    Py_XDECREF(argv);
    Py_XDECREF(callable);

    PyGILState_Release(gil);
    return answer;
}

@end
