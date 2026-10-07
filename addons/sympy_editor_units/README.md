# sympy-editor-units

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor):
physical units, with SymPy's own `sympy.physics.units`.  No dependency
beyond SymPy.

## What it does

- **Units are names in the formula.**  With the add-on on, `5*meter/second`,
  `3 km/hour`, `kg`, `newton`, `joule`, `kPa`, `electronvolt`,
  `speed_of_light`, `gravitational_constant`, `planck`... are SymPy's
  quantities, drawn as their symbols (m, km, N, J, c, G).  The names are the
  add-on's `namespace()`, so a name the formula already uses wins (an `x` stays
  `x`), and they are what a saved session is read back with.
- **Short names are a switch.**  `m`, `s`, `g`, `h`, `c`, `N`, `J`, `ms`,
  `deg`... are ordinary variables far too often to be taken by default.  The
  panel's *Short unit names* box adds them for the document (an instance of
  the add-on with `short=True` takes the document's place - the namespace is
  asked without a document); the session keeps it (`export_state`).
- **The dimension of the selection** (of the whole formula with nothing
  selected) in base dimensions - M, L, T, I, Θ, N, J - with its name when it
  has one (velocity, force, energy, pressure...), and its form in SI base
  units.
- **The check**, the point of it all: every sum, relation, exponent and
  function argument under the selection is checked.  A term whose dimension
  differs from the rest of its sum (the commonest dimension among the terms
  that carry units wins; a tie goes to the term drawn first), an equation
  whose sides differ, a dimensioned exponent or argument of `sin`, `exp`,
  `log`... is listed in the panel and outlined in the formula; a click on the
  item selects it.  A plain symbol counts as dimensionless, as in SymPy.
- **Convert** (`convert_to`) the selection - or a range of terms - to the
  units typed (`km/hour`, `joule`, `kg*meter**2/second**2`, or several:
  `kg, meter, second`); a conversion between dimensions that differ is refused
  with both named.  **SI base units** and **Simplify units**
  (`quantity_simplify` across dimensions: `newton*meter` is `joule`) are
  buttons and also ops in the Transform menu.  Each is a step of the history.

## Methods

| method | payload | answer |
|---|---|---|
| `inspect` | `path` (`children` for a range) | query: `{dimension: {latex, name, known}, problems: [{path, src, kind, message, dim, expected}], si, has_units}` |
| `convert` | `path`, `target` (`children`) | change: "Units: convert to …" |
| `si`, `simplify` | `path` (`children`) | change |
| `short` | `on` | query: `{short}` |

Every snapshot carries `snap["units"] = {short, has_units, problems: [{path,
message}]}` for the whole formula (at most 20), from which the panel outlines
the formula without asking.

## Limitations

- Prefixes (`kilo*meter`) are not typed names: use the prefixed units SymPy
  has (`km`, `kilometer`, `kPa`, `mg`...).
- `degree` is the unit while the add-on is on (SymPy's `degree()` of a
  polynomial is still reachable from the function box, which calls SymPy's
  own); with short names on, so are `deg`, `rad`, `N` and `S`.
- The dimension of a unit to a symbolic power, of a `Piecewise`, an undefined
  function or a matrix is not told ("cannot be told"), and nothing under it
  is flagged for that reason.
- Temperatures convert as differences (SymPy's `convert_to` has no offsets:
  no °C ↔ K).

## Tests

```sh
PYTHONPATH=src python -m pytest addons/sympy_editor_units -q
```

`tests/test_units.py` (names, the switch, the check and its paths, conversions,
a session saved and opened again) and `tests/test_units_browser.py`
(Playwright: the outlines, Convert, Undo, the switch, the guide).
