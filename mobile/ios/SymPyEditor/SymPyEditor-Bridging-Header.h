// What Swift sees of the Objective-C side: the embedded interpreter, which
// needs Python's C API and so cannot be written in Swift (the framework
// ships headers, not a Swift module).
#import "PythonRuntime.h"
