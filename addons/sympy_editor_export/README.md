# sympy-editor-export

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
selection - a node, a range of terms, or the whole formula when nothing is
selected - written out in another language, to copy or to save.

| tab | written by | options |
|---|---|---|
| LaTeX | `LatexPrinter` (with the document's printer settings) | mode: plain, `$…$`, `equation*`, `equation` |
| MathML | `MathMLPresentationPrinter` / `MathMLContentPrinter`, indented | presentation or content; inside `<math>` or bare |
| Python | `PythonCodePrinter`, `NumPyPrinter`, `MpmathPrinter`, or `sympy.printing.python` (SymPy source with its symbols declared) | the functions' module; the imports are written at the top |
| C | `C89CodePrinter` / `C99CodePrinter` / `C11CodePrinter` | standard; assign to a variable |
| Fortran | `FCodePrinter` | standard 77 … 2008; free or fixed form; assign to |
| JavaScript, Octave/MATLAB, Julia, Rust | SymPy's code printer for each | assign to |
| Function | `sympy.utilities.codegen.codegen` | the function's name; C99, C89, Fortran 95, Octave, Julia, Rust; with or without the header file |

Each file comes in a read-only box with **Copy** and **Save**.  Copy goes
where the editor's own Copy goes - the host app's clipboard
(`SympyEditorApp.copyText`) when there is one, else the browser's - and Save
through `api.saveFile`, as the editor saves its own files: the host app's
save sheet, the kernel, the share sheet or a download.

Details worth knowing:

- **What a language lacks** (a Bessel function in C, an unevaluated
  integral anywhere) is printed with `strict=False`: SymPy writes it as it
  writes it and lists it in its own *Not supported* comment at the top of the
  code; the panel repeats the names in a note.  For **Function**, whose body
  codegen prints strictly off, the C printer is asked the same question and
  its comment is put at the top of the `.c` file.
- **A printer that refuses** the whole expression (SymPy's Rust printer and a
  matrix) is reported in words (`Rust cannot write this: …`), with no file.
  So is an option that cannot be used: a variable or function name that is
  not an identifier.
- **Matrices** are written element by element into an array in C,
  JavaScript and Fortran (`M` unless *Assign to* names it); codegen returns
  one through the array argument `out`.
- **Function** takes the free symbols as its arguments, in alphabetical
  order; an equation `x = …` makes `x` an output argument (codegen's rule).
- The tab and the options are kept through the editor's keeper
  (`api.keep`, name `export-settings`).
- The panel asks Python only while its box is open, and only for what it has
  not asked already (formula, selection, format and options).
- Nothing changes the formula; the one method, `export`, is a query.

A format whose printer the running SymPy lacks is not offered.  No
dependency beyond SymPy; nothing uses the network.

## Python

```python
from sympy_editor_export import export
export(sin(x)/y, "c", {"standard": "C89", "assign": "r"})
# {'format': 'c', 'files': [{'name': 'formula.c', 'code': 'r = sin(x)/y;', 'mime': 'text/x-c'}],
#  'notes': [], 'error': None, ...}
```

The message is `{"action": "addon", "addon": "export", "method": "export",
"path", "children", "format", "options"}`.

## Tests

```sh
PYTHONPATH=src pytest addons/sympy_editor_export      # unit tests, and the panel in Chromium on the local server
```
