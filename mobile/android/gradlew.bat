@echo off
set DIR=%~dp0
set JAR=%DIR%gradle\wrapper\gradle-wrapper.jar
if not exist "%JAR%" curl -fsSL -o "%JAR%" https://raw.githubusercontent.com/gradle/gradle/v8.9.0/gradle/wrapper/gradle-wrapper.jar
java -classpath "%JAR%" org.gradle.wrapper.GradleWrapperMain %*
