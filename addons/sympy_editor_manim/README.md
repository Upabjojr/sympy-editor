# sympy-editor-manim

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
session's history as a [Manim](https://www.manim.community/) animation.  Each
formula turns into the next, the sub-expressions a change keeps travel to
their new place while the rest fades out and the new parts fade in, and the
editor's label of each step ("Transform: Expand", "Edit: 1 → x**3") is the
caption.

The panel under the formula writes the scene as a plain Manim file -
**Script**, then **Copy** or **Save…** - and, where Manim is installed beside
the editor's Python, **Render** runs it and plays the video in the panel,
with a link to save it.

```python
from sympy_editor import edit
edit(expr, addons=["manim"])
```

The add-on has no dependency: the script is written without Manim, by the
editor's Python wherever it runs.  Rendering needs Manim itself, with a
LaTeX installation and ffmpeg beside it
([docs.manim.community](https://docs.manim.community/)):

```sh
pip install manim                       # or: pip install -e "addons/sympy_editor_manim[render]"
manim -ql formula_steps.py FormulaSteps # the saved script, by hand: a quick preview
manim -qh formula_steps.py FormulaSteps # 1080p
```

The Render button finds Manim in this order: `SYMPY_EDITOR_MANIM` (the path
of a `manim` executable - a Manim in a virtualenv of its own), the `manim`
module of the editor's interpreter (`python -m manim`), a `manim` on the
PATH.  Nothing of it runs in a browser page (Pyodide) or in the apps, which
is why the add-on is not bundled there (`"bundle": false`): the script is
what those give.

## The scene

`sympy_editor_manim.scene.script(steps, options)` writes the file from the
history's steps as the editor renders them (`render_step`: annotated LaTeX
and the node table), and nothing else - so the file is self-contained.  At
its top are the settings as constants, to change by hand:

| constant | panel | what |
|---|---|---|
| `STYLE` | Style | `matching` (TransformMatchingTex: the shared parts move), `shapes` (TransformMatchingShapes: glyphs of the same shape move), `replace` (ReplacementTransform), `write` (fade out, Write) |
| `CAPTIONS` | Captions | the history's labels under the formula |
| `HIGHLIGHT` | | what goes is tinted `REMOVED` before the change, what came `ADDED` after it (matching style) |
| `HOLD`, `RUN_TIME` | | seconds a step stays, seconds a change takes |
| `SCALE` | | the formula's size |
| `THEME` | Theme | `dark` or `light` |
| `TITLE` | Title | a line at the top, or `None` |

Then `STEPS`, `(LaTeX, caption)` per step, and `CHANGES`: for each pair of
consecutive steps the two formulas with **what they share in `{{ }}`
groups**.  Manim's `MathTex` splits a formula at double braces (one set off
by spaces, which is how `isolate` writes them: a `{{` inside ordinary LaTeX,
`a^{{2}}`, is left alone) and `TransformMatchingTex` moves a part the other
formula holds too instead of fading it.  What two steps share is the editor's
own notion of a change - the same rule as the history view's diff: the
top-most nodes whose source (`str`) is in both node tables
(`shared_paths`); a kept node's children travel with it.  A formula is
split twice - once for the change that leads to it and once for the one it
leads to - and swapped on screen between the two, invisibly.

Manim 0.22 compiles a formula once, with every part marked, so any
sub-expression can be a part (inside a fraction, an exponent, a matrix).  An
older Manim compiled each part by itself, and a part such as `\frac{` did
not compile: the script's `formula()` falls back to the whole formula, which
then fades rather than moves.

## Python

```python
from sympy_editor import Document
from sympy_editor_manim import ADDON

doc = Document(expr, addons=[ADDON])
doc.handle({"action": "addon", "addon": "manim", "method": "script", "style": "matching"})["query"]["result"]
# {"code": "...", "name": "formula_steps.py", "scene": "FormulaSteps", "steps": n, "from": 0, "to": n - 1}
doc.handle({"action": "addon", "addon": "manim", "method": "render", "quality": "l", "format": "mp4"})["query"]["result"]
# {"name": "formula_steps.mp4", "mime": "video/mp4", "data": base64, "size", "seconds", "command", "log", ...}
doc.handle({"action": "addon", "addon": "manim", "method": "status"})["query"]["result"]
# {"available": bool, "command", "version", "latex", "ffmpeg", "hint"}
```

`script` and `render` take `from`/`to` (0-based step indices, the whole
history by default) and the settings above by name (`style`, `captions`,
`highlight`, `theme`, `title`, `hold`, `run_time`, `scale`); `render` also
`quality` (`l` 480p15, `m` 720p30, `h` 1080p60, `p`, `k`), `format` (`mp4`,
`gif`, `webm`, `mov`) and `timeout` (seconds, 600).  Every snapshot carries
`snap["manim"] = {"n", "index"}`, so the panel knows when the history moved.

Rendering holds the editor until it is done (one message at a time); Manim
runs as a child process, and the editor's Interrupt - or any exception
raised into the thread - kills it.  A failed render comes back in words with
the end of Manim's log.

## Tests

`pytest addons/sympy_editor_manim` - the isolation of the kept parts, the
script, the span of steps, the settings, a stand-in Manim command and the
real one where it is installed (`tests/test_manim.py`), and the panel in
Chromium (`tests/test_manim_browser.py`, Playwright; the render through the
panel only with Manim and `SYMPY_EDITOR_SLOW_TESTS=1`).
