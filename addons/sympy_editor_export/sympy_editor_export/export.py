"""The formula written out in other languages, with SymPy's own printers.

:func:`export` takes a SymPy object, a format key (:data:`FORMATS`) and the
format's options, and answers ``{"format", "files": [{"name", "code",
"mime"}], "notes": [...], "error"}``: a code printer that meets something
it has no counterpart for writes its own "Not supported" comment at the top
of the code (``strict=False``), and the names it listed come back as a note
too; a printer that refuses the whole expression answers ``error``, in
words, and no files.  Nothing here raises for a bad expression or option.

Every format is optional: one whose printer this SymPy does not have is
left out of :data:`FORMATS`, so the panel never offers it.
"""

from __future__ import annotations

import keyword
import re
from typing import Any, Callable, Dict, List, Optional

from sympy import Basic, Eq, MatrixSymbol, Symbol, sympify

__all__ = ["FORMATS", "export", "formats_for_client"]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExportError(ValueError):
    """An option the user gave cannot be used (a name that is not a name)."""


def _name(text: Any, what: str, default: str = "") -> str:
    name = str(text if text is not None else "").strip() or default
    if name and (not _IDENT.match(name) or keyword.iskeyword(name)):
        raise ExportError(f"{what} must be a plain name (letters, digits and _, not starting with a digit): {name!r}")
    return name


def _is_matrix(node: Basic) -> bool:
    return bool(getattr(node, "is_Matrix", False)) and hasattr(node, "shape")


def _assign_to(node: Basic, options: Dict[str, Any], matrix_default: str = ""):
    """The target of an assignment, from the "assign" option: a
    MatrixSymbol of the node's shape for a matrix (``matrix_default`` when
    the option is empty - C, JavaScript and Fortran cannot write a matrix
    otherwise), a Symbol for anything else, None for no assignment."""
    if _is_matrix(node):
        name = _name(options.get("assign"), "The variable to assign to", matrix_default)
        return MatrixSymbol(name, *node.shape) if name else None
    name = _name(options.get("assign"), "The variable to assign to")
    return Symbol(name) if name else None


class _Noted(set):
    """The printer's set of what it could not translate, telling ``log``
    too: ``doprint`` empties the printer's own set before it returns."""

    def __init__(self, log):
        super().__init__()
        self.log = log

    def add(self, item):
        self.log.add(type(item).__name__)
        super().add(item)


def _printer(cls, settings):
    """An instance of code printer ``cls`` that remembers, in ``._noted``,
    the names it wrote in its "Not supported" comment."""
    log: set = set()

    def get(self):
        return self.__dict__.get("_se_not_supported", set())

    def put(self, value):
        self.__dict__["_se_not_supported"] = _Noted(log)

    sub = type(cls.__name__, (cls,), {"_not_supported": property(get, put)})
    printer = sub(settings)
    printer._noted = log
    return printer


def _unsupported(printer) -> List[str]:
    return sorted(getattr(printer, "_noted", ()) or ())


def _code(printer, node, assign=None) -> Dict[str, Any]:
    code = printer.doprint(node, assign) if assign is not None else printer.doprint(node)
    return {"code": code, "unsupported": _unsupported(printer)}


# ---------------------------------------------------------------------------
# One function per format: (node, options, context) -> {"code", "unsupported"}
# or {"files": [...]} for several files.

def _latex(node, options, ctx):
    from sympy.printing.latex import LatexPrinter
    settings = {k: v for k, v in (ctx.get("latex_settings") or {}).items() if k in LatexPrinter._default_settings}
    mode = options.get("mode") or "plain"
    if mode not in ("plain", "inline", "equation", "equation*"):
        raise ExportError(f"No LaTeX mode {mode!r}")
    settings["mode"] = mode
    return {"code": LatexPrinter(settings).doprint(node)}


def _mathml(node, options, ctx):
    from sympy.printing.mathml import MathMLContentPrinter, MathMLPresentationPrinter
    kind = options.get("printer") or "presentation"
    if kind not in ("presentation", "content"):
        raise ExportError(f"No MathML printer {kind!r}")
    printer = (MathMLPresentationPrinter if kind == "presentation" else MathMLContentPrinter)({})
    xml = printer._print(sympify(node))
    body = xml.toprettyxml(indent="  ").strip()
    if options.get("wrap", True) not in (False, "false", "0", 0):
        display = ' display="block"' if kind == "presentation" else ""
        body = ('<math xmlns="http://www.w3.org/1998/Math/MathML"%s>\n' % display
                + "\n".join("  " + line for line in body.splitlines()) + "\n</math>")
    return {"code": body}


def _python(node, options, ctx):
    flavour = options.get("module") or "math"
    if flavour == "sympy":
        from sympy.printing.python import python
        return {"code": python(node)}
    from sympy.printing.pycode import MpmathPrinter, PythonCodePrinter
    try:
        from sympy.printing.numpy import NumPyPrinter
    except ImportError:              # pragma: no cover - SymPy >= 1.14 has it
        NumPyPrinter = None
    classes = {"math": PythonCodePrinter, "mpmath": MpmathPrinter, "numpy": NumPyPrinter}
    cls = classes.get(flavour)
    if cls is None:
        raise ExportError(f"No Python flavour {flavour!r}")
    printer = _printer(cls, {"strict": False})
    out = _code(printer, node)
    imports = getattr(printer, "module_imports", None) or {}
    lines = []
    for module in sorted(imports):
        names = sorted(imports[module])
        lines.append(f"import {module}" if module in ("math", "mpmath", "numpy") else
                     f"from {module} import {', '.join(names)}")
    if lines:
        out["code"] = "\n".join(lines) + "\n\n" + out["code"]
    return out


def _c(node, options, ctx):
    from sympy.printing.c import c_code_printers
    std = str(options.get("standard") or "C99").lower()
    if std not in c_code_printers:
        raise ExportError(f"No C standard {options.get('standard')!r}")
    return _code(_printer(c_code_printers[std], {"strict": False}), node, _assign_to(node, options, "M"))


def _fortran(node, options, ctx):
    from sympy.printing.fortran import FCodePrinter
    try:
        std = int(options.get("standard") or 95)
    except (TypeError, ValueError):
        raise ExportError(f"No Fortran standard {options.get('standard')!r}")
    form = options.get("source_format") or "free"
    if form not in ("free", "fixed"):
        raise ExportError(f"No Fortran source form {form!r}")
    printer = _printer(FCodePrinter, {"strict": False, "standard": std, "source_format": form})
    return _code(printer, node, _assign_to(node, options, "M"))


def _simple(module: str, cls: str, matrix_default: str = "") -> Callable:
    def run(node, options, ctx):
        mod = __import__(module, fromlist=[cls])
        return _code(_printer(getattr(mod, cls), {"strict": False}), node, _assign_to(node, options, matrix_default))
    return run


CODEGEN_LANGUAGES = [("C99", "C99"), ("C89", "C89"), ("F95", "Fortran 95"), ("Octave", "Octave"),
                     ("Julia", "Julia"), ("Rust", "Rust")]


def _function(node, options, ctx):
    from sympy.utilities.codegen import codegen
    language = options.get("language") or "C99"
    if language not in dict(CODEGEN_LANGUAGES):
        raise ExportError(f"codegen has no language {language!r}")
    name = _name(options.get("name"), "The function's name", "f")
    args = sorted(node.free_symbols, key=lambda s: str(s))
    header = options.get("header", True) not in (False, "false", "0", 0)

    # A matrix comes out through an array argument: name it, or codegen
    # makes up out_3250835285176398066.
    routine = Eq(MatrixSymbol("out", *node.shape), node, evaluate=False) if _is_matrix(node) else node

    def run(argument_sequence):
        return codegen((name, routine), language, name, header=False, empty=False,
                       argument_sequence=argument_sequence)
    try:
        files = run(args)
    except Exception:                 # an equation's left side is an output, not an argument
        files = run(None)
    # The function body is written by the language's code printer, which
    # codegen runs strictly off: ask a C printer what it would have refused.
    unsupported: List[str] = []
    if language in ("C89", "C99"):
        from sympy.printing.c import c_code_printers
        probe = _printer(c_code_printers[language.lower()], {"strict": False})
        try:
            probe.doprint(node, _assign_to(node, {}, "M") if _is_matrix(node) else None)
            unsupported = _unsupported(probe)
        except Exception:
            pass
    out = []
    for fname, code in files:
        if fname.endswith(".h") and not header:
            continue
        if unsupported and fname.endswith(".c"):
            code = "/* Not supported in C: */\n" + "".join(f"/* {u} */\n" for u in unsupported) + code
        out.append({"name": fname, "code": code})
    return {"files": out, "unsupported": unsupported, "lang": "C"}


# ---------------------------------------------------------------------------

def _choice(name, label, choices, default):
    return {"name": name, "label": label, "kind": "choice", "choices": [list(c) for c in choices], "default": default}


def _text(name, label, default="", placeholder=""):
    return {"name": name, "label": label, "kind": "text", "default": default, "placeholder": placeholder}


def _bool(name, label, default):
    return {"name": name, "label": label, "kind": "bool", "default": default}


_ASSIGN = _text("assign", "Assign to", "", "variable")

_CANDIDATES = [
    # key, label, extension, mime, options, function, module that must import
    ("latex", "LaTeX", "tex", "application/x-tex",
     [_choice("mode", "Mode", [("plain", "plain"), ("inline", "$…$"), ("equation*", "equation*"), ("equation", "equation")], "plain")],
     _latex, "sympy.printing.latex"),
    ("mathml", "MathML", "mml", "application/mathml+xml",
     [_choice("printer", "Markup", [("presentation", "presentation"), ("content", "content")], "presentation"),
      _bool("wrap", "<math> element", True)],
     _mathml, "sympy.printing.mathml"),
    ("python", "Python", "py", "text/x-python",
     [_choice("module", "Functions from", [("math", "math"), ("numpy", "NumPy"), ("mpmath", "mpmath"), ("sympy", "SymPy")], "math")],
     _python, "sympy.printing.pycode"),
    ("c", "C", "c", "text/x-c",
     [_choice("standard", "Standard", [("C89", "C89"), ("C99", "C99"), ("C11", "C11")], "C99"), _ASSIGN],
     _c, "sympy.printing.c"),
    ("fortran", "Fortran", "f90", "text/x-fortran",
     [_choice("standard", "Standard", [("77", "77"), ("90", "90"), ("95", "95"), ("2003", "2003"), ("2008", "2008")], "95"),
      _choice("source_format", "Source form", [("free", "free"), ("fixed", "fixed")], "free"), _ASSIGN],
     _fortran, "sympy.printing.fortran"),
    ("javascript", "JavaScript", "js", "text/javascript", [_ASSIGN],
     _simple("sympy.printing.jscode", "JavascriptCodePrinter", "M"), "sympy.printing.jscode"),
    ("octave", "Octave/MATLAB", "m", "text/x-octave", [_ASSIGN],
     _simple("sympy.printing.octave", "OctaveCodePrinter"), "sympy.printing.octave"),
    ("julia", "Julia", "jl", "text/x-julia", [_ASSIGN],
     _simple("sympy.printing.julia", "JuliaCodePrinter"), "sympy.printing.julia"),
    ("rust", "Rust", "rs", "text/x-rust", [_ASSIGN],
     _simple("sympy.printing.rust", "RustCodePrinter"), "sympy.printing.rust"),
    ("function", "Function", "c", "text/x-c",
     [_text("name", "Name", "f", "f"), _choice("language", "Language", CODEGEN_LANGUAGES, "C99"),
      _bool("header", "Header file", True)],
     _function, "sympy.utilities.codegen"),
]


def _available():
    import importlib
    out = []
    for key, label, ext, mime, opts, fn, module in _CANDIDATES:
        try:
            importlib.import_module(module)
        except Exception:             # this SymPy has no such printer: not offered
            continue
        out.append({"key": key, "label": label, "ext": ext, "mime": mime, "options": opts, "fn": fn})
    return out


#: The formats this SymPy can write, in the panel's order.
FORMATS: List[Dict[str, Any]] = _available()
_BY_KEY = {f["key"]: f for f in FORMATS}

_MIME_BY_EXT = {"c": "text/x-c", "h": "text/x-c", "f90": "text/x-fortran", "f": "text/x-fortran",
                "m": "text/x-octave", "jl": "text/x-julia", "rs": "text/x-rust"}


def formats_for_client() -> List[Dict[str, Any]]:
    """The formats and their options, for the panel (no functions)."""
    return [{k: v for k, v in f.items() if k != "fn"} for f in FORMATS]


def _defaults(fmt: Dict[str, Any], options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = {o["name"]: o["default"] for o in fmt["options"]}
    for k, v in (options or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def _say(exc: BaseException, label: str) -> str:
    text = str(exc).strip() or type(exc).__name__
    text = text.splitlines()[0] if "\n" in text else text
    return f"{label} cannot write this: {text}"


def export(node: Basic, fmt: str, options: Optional[Dict[str, Any]] = None,
           latex_settings: Optional[Dict[str, Any]] = None, basename: str = "formula") -> Dict[str, Any]:
    """``node`` written in format ``fmt`` (a key of :data:`FORMATS`)."""
    spec = _BY_KEY.get(fmt)
    if spec is None:
        return {"format": fmt, "files": [], "notes": [], "error": f"No export format {fmt!r} here"}
    opts = _defaults(spec, options)
    answer: Dict[str, Any] = {"format": fmt, "label": spec["label"], "files": [], "notes": [], "error": None,
                              "options": opts}
    try:
        res = spec["fn"](node, opts, {"latex_settings": latex_settings or {}})
    except ExportError as exc:
        answer["error"] = str(exc)
        return answer
    except RecursionError:
        answer["error"] = f"{spec['label']} cannot write this: the expression is nested too deeply"
        return answer
    except Exception as exc:          # the printer refused the expression: say so, in words
        answer["error"] = _say(exc, spec["label"])
        return answer
    if "files" in res:
        for f in res["files"]:
            ext = f["name"].rsplit(".", 1)[-1]
            answer["files"].append({"name": f["name"], "code": f["code"], "mime": _MIME_BY_EXT.get(ext, "text/plain")})
    else:
        answer["files"].append({"name": f"{basename}.{spec['ext']}", "code": res["code"], "mime": spec["mime"]})
    unsupported = res.get("unsupported") or []
    if unsupported:
        answer["notes"].append(f"Not supported in {res.get('lang') or spec['label']}: {', '.join(unsupported)} - "
                               "written as SymPy writes it, and listed in a comment at the top")
    return answer
