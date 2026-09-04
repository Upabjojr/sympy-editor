# sympy-editor-addon-template

The smallest complete add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor),
to copy when writing one of your own.  It shows every kind of hook once:

- an **op** in the Transform menu (`twice`);
- a **method** the panel calls (`count`, a query) and one that changes the
  expression (`double`);
- **data** in every snapshot (`snap["template"]`);
- a **panel** under the formula and a **toolbar button**, in `static/`.

## Make it yours

1. Copy this folder anywhere - it does not have to live in the editor's
   repository - and rename the package: the folder
   `sympy_editor_addon_template`, the `name`/`include`/`package-data` fields in
   `pyproject.toml`, the entry point (`template = ...` → `yours = ...`), and
   `name = "template"` in `__init__.py`.
2. `pip install -e .` in that folder.  The editor now finds it:

   ```python
   from sympy_editor import edit, installed_addons
   installed_addons()                       # {'yours': 'your_package:ADDON', ...}
   w = edit(expr, addons=["yours"])
   ```

   Without installing, `addons=["your_package"]` (a module name) or
   `addons=[ADDON]` (the object) work too.
3. Fill in `__init__.py` (the Python) and `static/panel.js` (the browser).
   `pytest` runs the tests.

The contract is documented in `sympy_editor/addons.py`; the design in
`addons/README.md` of the editor's repository.
