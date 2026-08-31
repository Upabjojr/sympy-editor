plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    // CPython and SymPy inside the app (see app/build.gradle.kts): the editor
    // runs the same Python as the desktop, with nothing to download.
    id("com.chaquo.python") version "16.1.0" apply false
}
