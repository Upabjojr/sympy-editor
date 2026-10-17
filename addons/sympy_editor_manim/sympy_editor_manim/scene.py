"""The Manim scene of a history: pure functions, no Manim needed.

A history is a list of steps, each the annotated LaTeX of a formula with its
node table (what ``sympy_editor.document.render_step`` gives) and the label
the editor recorded for it.  :func:`script` writes a Python file for Manim
in which each formula turns into the next.

What moves and what fades is the editor's own notion of a change: the
sub-expressions two consecutive steps share (the same ``src`` in both node
tables, the top-most ones) are *kept*; in the scene they travel to their new
place while the rest fades out and the new parts fade in.  For Manim that
means putting each kept sub-expression in a ``{{ ... }}`` group of its own -
``MathTex`` splits a formula at double braces and ``TransformMatchingTex``
matches the pieces by their LaTeX - which :func:`isolate` does on the
annotated LaTeX, where every node's extent is known.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sympy_editor.printer import strip_annotations

__all__ = ["script", "isolate", "shared_paths", "Step", "STYLES", "THEMES", "DEFAULTS", "SCENE_NAME", "FILE_NAME"]

#: ``(annotated latex, node table, caption)`` of one step.
Step = Tuple[str, Dict[str, Dict[str, Any]], Optional[str]]

STYLES = ("matching", "shapes", "replace", "write")
THEMES = ("dark", "light")
SCENE_NAME = "FormulaSteps"
FILE_NAME = "formula_steps.py"

#: The scene's settings and their defaults: what the panel offers and what
#: the script's constants start as.
DEFAULTS: Dict[str, Any] = {
    "style": "matching", "captions": True, "highlight": True, "theme": "dark", "title": None,
    "hold": 1.0, "run_time": 1.2, "scale": 1.4,
}

COMMAND = r"\htmlData"


# -- the annotated LaTeX ----------------------------------------------------

def _match_brace(tex: str, i: int) -> int:
    """The index of the ``}`` closing the ``{`` at ``tex[i]``, or -1."""
    depth = 0
    n = len(tex)
    j = i
    while j < n:
        c = tex[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def isolate(tex: str, paths: Iterable[str]) -> str:
    """The plain LaTeX of the annotated ``tex`` with the nodes at ``paths``
    each inside a Manim group, `` {{ ... }} ``, and every other annotation
    removed.  The spaces matter: Manim opens a group only at a ``{{`` that
    starts the string or follows white space (a ``{{`` inside ordinary
    LaTeX, ``a^{{2}}``, is left alone), so each group is set off by one on
    either side; LaTeX ignores them in math mode."""
    wanted = set(paths)
    out: List[str] = []
    i = 0
    n = len(tex)
    while i < n:
        j = tex.find(COMMAND + "{", i)
        if j < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:j])
        k = _match_brace(tex, j + len(COMMAND))          # the end of the first argument
        if k < 0 or k + 1 >= n or tex[k + 1] != "{":
            out.append(tex[j:j + len(COMMAND)])
            i = j + len(COMMAND)
            continue
        end = _match_brace(tex, k + 1)                   # the end of the body
        if end < 0:
            out.append(tex[j:])
            break
        arg = tex[j + len(COMMAND) + 1:k]
        path = arg[len("path="):] if arg.startswith("path=") else None
        body = tex[k + 2:end]
        if path is not None and path in wanted:
            out.append(" {{ " + strip_annotations(body, COMMAND) + " }} ")
        else:
            out.append(isolate(body, wanted))
        i = end + 1
    return "".join(out)


# -- what two steps share -----------------------------------------------------

def _ancestors(path: str) -> List[str]:
    """``/1/0`` -> ``['/', '/1']``."""
    if path == "/":
        return []
    parts = path.strip("/").split("/")
    return ["/"] + ["/" + "/".join(parts[:k]) for k in range(1, len(parts))]


def shared_paths(nodes_a: Dict[str, Dict[str, Any]], nodes_b: Dict[str, Dict[str, Any]]) -> Tuple[Set[str], Set[str]]:
    """The paths, in each of two node tables, of the top-most nodes whose
    source is also in the other table: what a change keeps.  A kept node's
    children are kept with it, so they are not listed."""
    srcs_a = {p: n.get("src") for p, n in nodes_a.items()}
    srcs_b = {p: n.get("src") for p, n in nodes_b.items()}
    common = set(srcs_a.values()) & set(srcs_b.values())
    common.discard(None)

    def tops(srcs: Dict[str, Any]) -> Set[str]:
        out: Set[str] = set()
        for path in sorted(srcs, key=lambda p: (p.count("/"), p)):
            if srcs[path] in common and not any(a in out for a in _ancestors(path)):
                out.add(path)
        return out

    return tops(srcs_a), tops(srcs_b)


def changes(steps: Sequence[Step]) -> List[Tuple[str, str]]:
    """For each pair of consecutive steps, the two formulas with what they
    share in Manim groups."""
    out = []
    for (tex_a, nodes_a, _), (tex_b, nodes_b, _) in zip(steps, steps[1:]):
        kept_a, kept_b = shared_paths(nodes_a, nodes_b)
        out.append((isolate(tex_a, kept_a), isolate(tex_b, kept_b)))
    return out


# -- the script ---------------------------------------------------------------

def _literal(text: Optional[str]) -> str:
    """``text`` as a Python literal: a raw string where one can hold it (the
    LaTeX stays readable), ``repr`` otherwise."""
    if text is None:
        return "None"
    if '"' not in text and "\n" not in text and "\r" not in text and not text.endswith("\\"):
        return 'r"' + text + '"'
    return repr(text)


def settings(options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The scene's settings from ``options``, checked: unknown keys are
    ignored, a value of the wrong shape refused in words."""
    opts = dict(DEFAULTS)
    options = options or {}
    style = options.get("style", opts["style"])
    if style not in STYLES:
        raise ValueError(f"Unknown style {style!r}: one of {', '.join(STYLES)}")
    theme = options.get("theme", opts["theme"])
    if theme not in THEMES:
        raise ValueError(f"Unknown theme {theme!r}: one of {', '.join(THEMES)}")
    opts["style"], opts["theme"] = style, theme
    for key in ("captions", "highlight"):
        if key in options:
            opts[key] = bool(options[key])
    for key in ("hold", "run_time", "scale"):
        if options.get(key) is not None:
            try:
                value = float(options[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number, not {options[key]!r}") from None
            if not 0 < value <= 60:
                raise ValueError(f"{key} must be between 0 and 60 seconds" if key != "scale" else "scale must be between 0 and 60")
            opts[key] = value
    title = options.get("title")
    opts["title"] = str(title).strip() or None if title is not None else None
    return opts


TEMPLATE = '''"""Formula steps from SymPy Editor, animated with Manim.

    manim -ql {file} {scene}                 # a quick preview, 480p
    manim -qh {file} {scene}                 # 1080p
    manim -ql --format gif {file} {scene}    # an animated GIF

Needs Manim Community (pip install manim), a LaTeX installation and ffmpeg:
https://docs.manim.community/.  Written for Manim 0.22; with an older Manim
the formulas still render, but a formula that Manim cannot split into its
parts is shown whole (see `formula`).

The constants below are yours to change.
"""
from manim import *  # noqa: F401,F403

#: How one step becomes the next:
#:   "matching"  the sub-expressions the two steps share move to their new
#:               place, the rest fades out and the new parts fade in
#:   "shapes"    glyphs of the same shape move (TransformMatchingShapes)
#:   "replace"   the whole formula morphs (ReplacementTransform)
#:   "write"     the old formula fades out and the new one is written
STYLE = {style}
CAPTIONS = {captions}          # the editor's label of each step, under the formula
HIGHLIGHT = {highlight}         # tint what goes before a change, and what came after it
HOLD = {hold}               # seconds each step stays on screen
RUN_TIME = {run_time}           # seconds of each change
SCALE = {scale}              # the formula's size
THEME = {theme}           # "dark" or "light"
TITLE = {title}             # a title at the top, or None
REMOVED = "#e5484d"      # the tint of what goes
ADDED = "#30a46c"        # the tint of what came

#: The steps, oldest first: (LaTeX, caption).
STEPS = [
{steps}
]

#: Each change: the two formulas, with the sub-expressions they share in
#: {{{{ }}}} groups - Manim splits a formula there, and TransformMatchingTex
#: moves a part the next formula holds too, instead of fading it.
CHANGES = [
{changes}
]


def formula(tex, plain, ink):
    """The formula with its parts told apart - or whole, when Manim cannot
    split it (an older Manim compiles each part by itself)."""
    try:
        m = MathTex(tex, color=ink)
    except Exception:
        m = MathTex(plain, color=ink)
    return m.scale(SCALE).move_to(ORIGIN)


def keys(m):
    """The LaTeX of each part of a formula: what TransformMatchingTex matches."""
    return [getattr(part, "tex_string", "") for part in m.submobjects]


class {scene}(Scene):
    def construct(self):
        ink = WHITE
        if THEME == "light":
            self.camera.background_color = WHITE
            ink = BLACK
        if TITLE:
            self.add(Text(TITLE, font_size=28, color=ink).to_edge(UP))
        self.caption = None
        shown = formula(CHANGES[0][0] if CHANGES else STEPS[0][0], STEPS[0][0], ink)
        self.play(Write(shown), *self.captioned(STEPS[0][1], ink))
        self.wait(HOLD)
        for k, (src, dst) in enumerate(CHANGES):
            a = formula(src, STEPS[k][0], ink)
            b = formula(dst, STEPS[k + 1][0], ink)
            self.remove(shown)          # the same formula, split for this change
            self.add(a)
            kept = set(keys(a)) & set(keys(b))
            matching = STYLE == "matching" and 0 < len(kept)
            if HIGHLIGHT and matching:
                going = [p for p in a.submobjects if p.tex_string not in kept]
                if going and len(going) < len(a.submobjects):
                    self.play(*[p.animate.set_color(REMOVED) for p in going], run_time=0.4)
                    self.wait(0.3)
            anims = self.captioned(STEPS[k + 1][1], ink)
            if STYLE == "matching":
                self.play(TransformMatchingTex(a, b, run_time=RUN_TIME), *anims)
            elif STYLE == "shapes":
                self.play(TransformMatchingShapes(a, b, run_time=RUN_TIME), *anims)
            elif STYLE == "write":
                self.play(FadeOut(a, run_time=RUN_TIME / 2), *anims)
                self.play(Write(b, run_time=RUN_TIME))
            else:
                self.play(ReplacementTransform(a, b, run_time=RUN_TIME), *anims)
            if HIGHLIGHT and matching:
                came = [p for p in b.submobjects if p.tex_string not in kept]
                if came and len(came) < len(b.submobjects):
                    self.play(*[p.animate.set_color(ADDED) for p in came], run_time=0.3)
                    self.wait(0.4)
                    self.play(*[p.animate.set_color(ink) for p in came], run_time=0.4)
            shown = b
            self.wait(HOLD)
        self.wait(HOLD)

    def captioned(self, text, ink):
        """The animations that put `text` under the formula in place of the
        caption shown (none without captions)."""
        old, new = self.caption, None
        if CAPTIONS and text:
            new = Text(text, font_size=24, color=ink).to_edge(DOWN)
        self.caption = new
        anims = []
        if old is not None:
            anims.append(FadeOut(old))
        if new is not None:
            anims.append(FadeIn(new))
        return anims
'''


def script(steps: Sequence[Step], options: Optional[Dict[str, Any]] = None) -> str:
    """The Manim script of ``steps`` (``(annotated latex, nodes, caption)``
    each, oldest first; at least one), a Python file with one scene,
    :data:`SCENE_NAME`, and the settings of ``options`` (see
    :data:`DEFAULTS`) as constants at its top."""
    if not steps:
        raise ValueError("There is no step to animate")
    opts = settings(options)
    lines = []
    for tex, _, caption in steps:
        lines.append(f"    ({_literal(strip_annotations(tex, COMMAND))}, {_literal(caption)}),")
    change_lines = []
    for src, dst in changes(steps):
        change_lines.append(f"    ({_literal(src)},\n     {_literal(dst)}),")
    return TEMPLATE.format(
        file=FILE_NAME, scene=SCENE_NAME,
        style=repr(opts["style"]), captions=repr(opts["captions"]), highlight=repr(opts["highlight"]),
        hold=repr(opts["hold"]), run_time=repr(opts["run_time"]), scale=repr(opts["scale"]),
        theme=repr(opts["theme"]), title=repr(opts["title"]),
        steps="\n".join(lines), changes="\n".join(change_lines) if change_lines else "    # one step: nothing changes",
    )
