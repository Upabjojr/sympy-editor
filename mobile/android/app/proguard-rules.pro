# R8 for the release build: it shrinks and renames the app's own code and its
# libraries, and the bundle carries the mapping file that turns the names back
# in Play's crash reports.  What it must not rename or remove is what is
# reached by name rather than by a call R8 can see:

# The page calls the two bridges by method name (WebView.addJavascriptInterface:
# SympyEditorApp, SympyEditorPy).
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keepattributes JavascriptInterface

# ONNX Runtime: the handwriting add-on's Python looks its classes and methods up
# by name (java.jclass("ai.onnxruntime.OrtEnvironment")...), and its native
# library calls back into them through JNI.
-keep class ai.onnxruntime.** { *; }

# Chaquopy: Python reaches Java by reflection (its library brings rules of its
# own; these keep the whole of it, to be sure).
-keep class com.chaquo.python.** { *; }

# Line numbers in the stack traces of crash reports; the file names are
# renamed with everything else, and the mapping gives them back.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
