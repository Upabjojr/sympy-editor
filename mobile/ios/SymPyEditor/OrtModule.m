#import "OrtModule.h"

#import <onnxruntime/onnxruntime_c_api.h>

// `_sympy_ort`: ONNX Runtime for the app's Python.
//
// There is no onnxruntime wheel for iOS, so the handwriting add-on's model
// (math-ocr's, staged by mobile/build.py) cannot run the way it does on a
// desktop.  ONNX Runtime itself is built for iOS - a static library with a C
// API (onnxruntime.xcframework, linked into the app) - and this is the little
// of it the add-on needs, as a module of the interpreter: a session made from
// a model's bytes, and `run`.  It knows nothing of numpy: tensors go in and
// come out as (type code, shape, bytes), and the add-on's Python
// (recognizer._NativeSession) turns them into arrays.  Android does the same
// job through Chaquopy's Java bridge (recognizer._JavaSession).
//
//     session = _sympy_ort.Session(model_bytes, threads)
//     session.output_names                      -> ["memory", "mask"]
//     session.run({"src": ("f", (1, 40, 7), buffer), ...})
//                                               -> [("f", (1, 40, 256), bytes), ...]
//
// Type codes are the struct module's: "f" float32, "q" int64, "?" bool.

static const OrtApi *ort = NULL;
static OrtEnv *environment = NULL;

/// Turn a failed status into a Python exception.  True when it had failed.
static int failed(OrtStatus *status) {
    if (status == NULL) return 0;
    PyErr_Format(PyExc_RuntimeError, "ONNX Runtime: %s", ort->GetErrorMessage(status));
    ort->ReleaseStatus(status);
    return 1;
}

/// The one environment of the process, made at the first session (under the GIL).
static int ensureEnvironment(void) {
    if (environment != NULL) return 1;
    if (ort == NULL) ort = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (ort == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "ONNX Runtime: this build does not have the API the app was compiled for");
        return 0;
    }
    if (failed(ort->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "sympy-editor", &environment))) return 0;
    // Nothing leaves the device (the app has no network either).
    OrtStatus *quiet = ort->DisableTelemetryEvents(environment);
    if (quiet != NULL) ort->ReleaseStatus(quiet);
    return 1;
}

typedef struct {
    PyObject_HEAD
    OrtSession *session;
    PyObject *outputs;          // list of str: the names of the model's outputs, in order
} Session;

static int Session_init(Session *self, PyObject *args, PyObject *kwargs) {
    Py_buffer model;
    int threads = 1;
    if (!PyArg_ParseTuple(args, "y*|i:Session", &model, &threads)) return -1;
    OrtSessionOptions *options = NULL;
    OrtSession *session = NULL;
    PyObject *outputs = NULL;
    int ok = 0;

    if (!ensureEnvironment()) goto done;
    if (failed(ort->CreateSessionOptions(&options))) goto done;
    if (failed(ort->SetIntraOpNumThreads(options, threads > 0 ? threads : 1))) goto done;
    if (failed(ort->SetInterOpNumThreads(options, 1))) goto done;

    OrtStatus *status;
    Py_BEGIN_ALLOW_THREADS
    status = ort->CreateSessionFromArray(environment, model.buf, (size_t)model.len, options, &session);
    Py_END_ALLOW_THREADS
    if (failed(status)) goto done;

    size_t count = 0;
    OrtAllocator *allocator = NULL;
    if (failed(ort->SessionGetOutputCount(session, &count))) goto done;
    if (failed(ort->GetAllocatorWithDefaultOptions(&allocator))) goto done;
    outputs = PyList_New(0);
    if (outputs == NULL) goto done;
    for (size_t i = 0; i < count; i++) {
        char *name = NULL;
        if (failed(ort->SessionGetOutputName(session, i, allocator, &name))) goto done;
        PyObject *text = PyUnicode_FromString(name);
        OrtStatus *freed = ort->AllocatorFree(allocator, name);
        if (freed != NULL) ort->ReleaseStatus(freed);
        if (text == NULL || PyList_Append(outputs, text) < 0) { Py_XDECREF(text); goto done; }
        Py_DECREF(text);
    }
    ok = 1;

done:
    if (options != NULL) ort->ReleaseSessionOptions(options);
    PyBuffer_Release(&model);
    if (!ok) {
        if (session != NULL) ort->ReleaseSession(session);
        Py_XDECREF(outputs);
        return -1;
    }
    if (self->session != NULL) ort->ReleaseSession(self->session);     // __init__ called again
    Py_XSETREF(self->outputs, outputs);
    self->session = session;
    return 0;
}

static void Session_dealloc(Session *self) {
    if (self->session != NULL) ort->ReleaseSession(self->session);
    Py_XDECREF(self->outputs);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

#define MAX_TENSORS 8
#define MAX_RANK 8

/// The ONNX element type of a type code, and the size of one element; 0 for a code not handled.
static size_t elementOf(const char *code, ONNXTensorElementDataType *type) {
    if (code[0] != '\0' && code[1] == '\0') {
        switch (code[0]) {
            case 'f': *type = ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT; return sizeof(float);
            case 'q': *type = ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64; return sizeof(int64_t);
            case '?': *type = ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL; return sizeof(bool);
        }
    }
    return 0;
}

/// One output of the model as (type code, shape, bytes).
static PyObject *tensorOut(OrtValue *value) {
    OrtTensorTypeAndShapeInfo *info = NULL;
    if (failed(ort->GetTensorTypeAndShape(value, &info))) return NULL;
    ONNXTensorElementDataType type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
    size_t rank = 0, elements = 0;
    int64_t dims[MAX_RANK];
    int bad = failed(ort->GetTensorElementType(info, &type)) || failed(ort->GetDimensionsCount(info, &rank));
    if (!bad && rank > MAX_RANK) {
        PyErr_SetString(PyExc_ValueError, "The model answered with a tensor of too many dimensions");
        bad = 1;
    }
    bad = bad || failed(ort->GetDimensions(info, dims, rank)) || failed(ort->GetTensorShapeElementCount(info, &elements));
    ort->ReleaseTensorTypeAndShapeInfo(info);
    if (bad) return NULL;

    const char *code;
    size_t size;
    switch (type) {
        case ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT: code = "f"; size = sizeof(float); break;
        case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64: code = "q"; size = sizeof(int64_t); break;
        case ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL: code = "?"; size = sizeof(bool); break;
        default:
            PyErr_Format(PyExc_TypeError, "The model answered with a tensor of type %d", (int)type);
            return NULL;
    }
    void *data = NULL;
    if (failed(ort->GetTensorMutableData(value, &data))) return NULL;
    PyObject *shape = PyTuple_New((Py_ssize_t)rank);
    if (shape == NULL) return NULL;
    for (size_t i = 0; i < rank; i++) {
        PyObject *n = PyLong_FromLongLong(dims[i]);
        if (n == NULL) { Py_DECREF(shape); return NULL; }
        PyTuple_SET_ITEM(shape, (Py_ssize_t)i, n);
    }
    PyObject *bytes = PyBytes_FromStringAndSize(data, (Py_ssize_t)(elements * size));
    if (bytes == NULL) { Py_DECREF(shape); return NULL; }
    return Py_BuildValue("(sNN)", code, shape, bytes);
}

static PyObject *Session_run(Session *self, PyObject *feeds) {
    if (self->session == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "The session has no model");
        return NULL;
    }
    if (!PyDict_Check(feeds)) {
        PyErr_SetString(PyExc_TypeError, "run() takes a dict: name -> (type code, shape, buffer)");
        return NULL;
    }
    Py_ssize_t given = PyDict_Size(feeds), wanted = PyList_GET_SIZE(self->outputs);
    if (given > MAX_TENSORS || wanted > MAX_TENSORS) {
        PyErr_SetString(PyExc_ValueError, "Too many tensors");
        return NULL;
    }
    const char *inputNames[MAX_TENSORS], *outputNames[MAX_TENSORS];
    OrtValue *inputs[MAX_TENSORS] = {NULL}, *outputs[MAX_TENSORS] = {NULL};
    Py_buffer buffers[MAX_TENSORS];
    Py_ssize_t held = 0;                    // how many of `buffers` are held
    OrtMemoryInfo *memory = NULL;
    PyObject *result = NULL;

    if (failed(ort->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &memory))) goto done;

    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(feeds, &position, &key, &value)) {
        const char *code;
        PyObject *shape, *data;
        if ((inputNames[held] = PyUnicode_AsUTF8(key)) == NULL) goto done;
        if (!PyArg_ParseTuple(value, "sO!O", &code, &PyTuple_Type, &shape, &data)) goto done;
        ONNXTensorElementDataType type;
        size_t size = elementOf(code, &type);
        Py_ssize_t rank = PyTuple_GET_SIZE(shape);
        if (size == 0 || rank > MAX_RANK) {
            PyErr_Format(PyExc_TypeError, "A tensor of type %s and %zd dimensions cannot be given to the model", code, rank);
            goto done;
        }
        int64_t dims[MAX_RANK];
        size_t elements = 1;
        for (Py_ssize_t i = 0; i < rank; i++) {
            dims[i] = PyLong_AsLongLong(PyTuple_GET_ITEM(shape, i));
            if (dims[i] < 0) {
                if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, "A negative dimension");
                goto done;
            }
            elements *= (size_t)dims[i];
        }
        if (PyObject_GetBuffer(data, &buffers[held], PyBUF_SIMPLE) < 0) goto done;
        held++;
        if ((size_t)buffers[held - 1].len != elements * size) {
            PyErr_SetString(PyExc_ValueError, "A tensor's data does not have the size of its shape");
            goto done;
        }
        // The tensor is the buffer itself, not a copy: it is held until the run is over.
        if (failed(ort->CreateTensorWithDataAsOrtValue(memory, buffers[held - 1].buf, (size_t)buffers[held - 1].len,
                                                       dims, (size_t)rank, type, &inputs[held - 1]))) goto done;
    }
    for (Py_ssize_t i = 0; i < wanted; i++) {
        if ((outputNames[i] = PyUnicode_AsUTF8(PyList_GET_ITEM(self->outputs, i))) == NULL) goto done;
    }

    OrtStatus *status;
    Py_BEGIN_ALLOW_THREADS
    status = ort->Run(self->session, NULL, inputNames, (const OrtValue *const *)inputs, (size_t)held,
                      outputNames, (size_t)wanted, outputs);
    Py_END_ALLOW_THREADS
    if (failed(status)) goto done;

    result = PyList_New(wanted);
    for (Py_ssize_t i = 0; result != NULL && i < wanted; i++) {
        PyObject *item = tensorOut(outputs[i]);
        if (item == NULL) { Py_CLEAR(result); break; }
        PyList_SET_ITEM(result, i, item);
    }

done:
    for (Py_ssize_t i = 0; i < MAX_TENSORS; i++) {
        if (inputs[i] != NULL) ort->ReleaseValue(inputs[i]);
        if (outputs[i] != NULL) ort->ReleaseValue(outputs[i]);
    }
    for (Py_ssize_t i = 0; i < held; i++) PyBuffer_Release(&buffers[i]);
    if (memory != NULL) ort->ReleaseMemoryInfo(memory);
    return result;
}

static PyObject *Session_outputNames(Session *self, void *closure) {
    if (self->outputs == NULL) return PyList_New(0);
    return PyList_GetSlice(self->outputs, 0, PyList_GET_SIZE(self->outputs));
}

static PyMethodDef Session_methods[] = {
    {"run", (PyCFunction)Session_run, METH_O,
     "run(feeds) -> the model's outputs, in order, each (type code, shape, bytes).\n"
     "feeds: name -> (type code, shape, buffer); codes: f float32, q int64, ? bool."},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef Session_getset[] = {
    {"output_names", (getter)Session_outputNames, NULL, "The names of the model's outputs, in order.", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyTypeObject SessionType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_sympy_ort.Session",
    .tp_basicsize = sizeof(Session),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "Session(model: bytes, threads: int = 1): an ONNX model, loaded in ONNX Runtime.",
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Session_init,
    .tp_dealloc = (destructor)Session_dealloc,
    .tp_methods = Session_methods,
    .tp_getset = Session_getset,
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_sympy_ort",
    .m_doc = "ONNX Runtime for the app's Python: what the handwriting add-on's model runs on.",
    .m_size = -1,
};

PyMODINIT_FUNC PyInit__sympy_ort(void) {
    if (PyType_Ready(&SessionType) < 0) return NULL;
    PyObject *made = PyModule_Create(&module);
    if (made == NULL) return NULL;
    Py_INCREF(&SessionType);
    if (PyModule_AddObject(made, "Session", (PyObject *)&SessionType) < 0) {
        Py_DECREF(&SessionType);
        Py_DECREF(made);
        return NULL;
    }
    const OrtApiBase *base = OrtGetApiBase();
    PyModule_AddStringConstant(made, "version", base ? base->GetVersionString() : "");
    return made;
}
