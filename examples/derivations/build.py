#!/usr/bin/env python3
"""Build the shelf: one page per derivation, and an index over them.

    python examples/derivations/build.py            # writes the HTML here
    python examples/derivations/build.py --open     # ...and opens the index

Each page is the history viewer over a derivation from `derivations.py`:
every step with what changed in green and red, a **Play** button that runs it
as a slideshow, and **Save** to keep it as one self-contained file.  Nothing
on these pages runs Python - they are the viewer, not the editor.
"""

from __future__ import annotations

import html
import importlib.util
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from sympy_editor import save_history_html          # noqa: E402  (after sys.path)

spec = importlib.util.spec_from_file_location("derivations", HERE / "derivations.py")
derivations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(derivations)

INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Derivations</title>
<style>
  body {{ margin: 0; padding: 2.5rem 1.5rem 4rem; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         color: #1f2328; background: #ffffff; }}
  main {{ max-width: 46rem; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 0.3rem; }}
  p.lead {{ color: #656d76; margin: 0 0 2rem; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ margin: 0 0 0.6rem; }}
  a {{ display: block; padding: 0.8rem 1rem; border: 1px solid #d0d7de; border-radius: 0.6rem;
       text-decoration: none; color: inherit; }}
  a:hover {{ border-color: #3b82f6; background: #f6f8fa; }}
  .name {{ font-weight: 600; }}
  .steps {{ float: right; color: #656d76; font-size: 0.85rem; }}
  .why {{ color: #656d76; font-size: 0.9rem; }}
  footer {{ margin-top: 2.5rem; color: #656d76; font-size: 0.85rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1e1e1e; color: #e6e6e6; }}
    a {{ border-color: #444; }} a:hover {{ background: #262626; }}
    p.lead, .steps, .why, footer {{ color: #a0a0a0; }}
  }}
</style>
</head>
<body>
<main>
<h1>Derivations</h1>
<p class="lead">Results nobody believes in one step, written out as the steps
somebody would take.  Each is a history: open one and press <b>Play</b>.</p>
<ul>
{items}
</ul>
<footer>Built by <code>examples/derivations/build.py</code> with
<code>sympy_editor.History</code>.  Every page carries its own rendering and
plays offline.</footer>
</main>
</body>
</html>
"""

ITEM = """<li><a href="{slug}.html"><span class="steps">{steps} steps</span>
<span class="name">{title}</span><br><span class="why">{why}</span></a></li>"""


def main() -> int:
    items = []
    for slug, make in derivations.DERIVATIONS:
        history = make()
        save_history_html(history, HERE / f"{slug}.html", title=history.title)
        why = (make.__doc__ or "").strip().splitlines()[0]
        items.append(ITEM.format(slug=slug, steps=len(history),
                                 title=html.escape(history.title), why=html.escape(why)))
        print(f"  {slug + '.html':40s} {len(history):2d} steps  {history.title}")
    index = HERE / "index.html"
    index.write_text(INDEX.format(items="\n".join(items)), encoding="utf-8")
    print("Wrote", index)
    if "--open" in sys.argv:
        webbrowser.open(index.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
