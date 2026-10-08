"""Handwriting for the store screenshots (mobile/screenshots.py --set handwriting).

Nobody's hand writes in a headless browser, so the ink is made here: each
glyph is a few control points in a unit box, smoothed (Catmull-Rom), slanted
and shaken a little, and a :class:`Writer` lays glyphs out like a formula.
The strokes are drawn on the page with the pointer, and math-ocr's model
reads them as it reads anyone's.
"""
import math, random

G = {  # unit box: x right, y down, height 1; (width, strokes)
 "x": (0.6, [[(0.0,0.05),(0.15,0.1),(0.3,0.5),(0.45,0.9),(0.6,0.95)], [(0.6,0.05),(0.45,0.2),(0.3,0.5),(0.12,0.85),(0.0,0.95)]]),
 "y": (0.6, [[(0.0,0.0),(0.12,0.3),(0.3,0.55)], [(0.6,0.0),(0.42,0.4),(0.25,0.8),(0.1,1.25),(-0.05,1.45)]]),
 "a": (0.6, [[(0.5,0.2),(0.3,0.05),(0.08,0.3),(0.05,0.7),(0.25,0.95),(0.45,0.7),(0.52,0.2),(0.52,0.6),(0.58,0.9),(0.68,0.97)]]),
 "b": (0.55, [[(0.05,-0.6),(0.05,0.2),(0.06,0.95),(0.2,0.6),(0.4,0.45),(0.52,0.65),(0.4,0.92),(0.2,0.97),(0.06,0.85)]]),
 "n": (0.6, [[(0.02,0.05),(0.05,0.5),(0.05,0.97),(0.1,0.5),(0.3,0.1),(0.48,0.25),(0.5,0.6),(0.52,0.97)]]),
 "1": (0.3, [[(0.0,0.3),(0.2,0.0),(0.2,0.5),(0.2,1.0)]]),
 "2": (0.6, [[(0.05,0.25),(0.2,0.03),(0.45,0.08),(0.5,0.35),(0.3,0.65),(0.03,0.98),(0.3,0.96),(0.6,0.97)]]),
 "3": (0.55, [[(0.05,0.1),(0.3,0.0),(0.5,0.2),(0.25,0.47),(0.5,0.72),(0.3,0.98),(0.02,0.88)]]),
 "0": (0.55, [[(0.3,0.02),(0.08,0.25),(0.05,0.7),(0.28,0.98),(0.5,0.72),(0.5,0.28),(0.3,0.02)]]),
 "+": (0.7, [[(0.0,0.5),(0.35,0.5),(0.7,0.5)], [(0.35,0.15),(0.35,0.5),(0.35,0.85)]]),
 "-": (0.6, [[(0.0,0.5),(0.3,0.5),(0.6,0.5)]]),
 "=": (0.7, [[(0.0,0.35),(0.35,0.35),(0.7,0.35)], [(0.0,0.65),(0.35,0.65),(0.7,0.65)]]),
 "(": (0.3, [[(0.3,-0.15),(0.08,0.2),(0.03,0.5),(0.08,0.8),(0.3,1.15)]]),
 ")": (0.3, [[(0.0,-0.15),(0.22,0.2),(0.27,0.5),(0.22,0.8),(0.0,1.15)]]),
 "int": (0.5, [[(0.5,-0.35),(0.42,-0.45),(0.32,-0.3),(0.27,0.2),(0.23,0.8),(0.18,1.3),(0.08,1.45),(0.0,1.35)]]),
 "d": (0.6, [[(0.5,0.3),(0.3,0.08),(0.08,0.35),(0.06,0.7),(0.25,0.95),(0.45,0.7),(0.52,0.2),(0.52,-0.6),(0.52,0.5),(0.55,0.97)]]),
 "s": (0.5, [[(0.45,0.15),(0.25,0.03),(0.08,0.22),(0.25,0.48),(0.42,0.72),(0.25,0.97),(0.03,0.85)]]),
 "i": (0.25, [[(0.12,0.1),(0.12,0.5),(0.12,0.97)], [(0.12,-0.3),(0.13,-0.25)]]),
 "c": (0.5, [[(0.48,0.2),(0.3,0.03),(0.08,0.3),(0.08,0.7),(0.28,0.97),(0.5,0.82)]]),
 "o": (0.5, [[(0.28,0.03),(0.06,0.3),(0.06,0.7),(0.27,0.97),(0.47,0.7),(0.47,0.3),(0.28,0.03)]]),
}

def smooth(pts, per=7):
    if len(pts) < 3:
        a, b = pts[0], pts[-1]
        return [(a[0] + (b[0]-a[0])*i/per, a[1] + (b[1]-a[1])*i/per) for i in range(per+1)]
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(p) - 2):
        for k in range(per):
            t = k / per
            out.append(tuple(0.5 * ((2*p[i][j]) + (-p[i-1][j] + p[i+1][j])*t + (2*p[i-1][j] - 5*p[i][j] + 4*p[i+1][j] - p[i+2][j])*t*t
                                    + (-p[i-1][j] + 3*p[i][j] - 3*p[i+1][j] + p[i+2][j])*t**3) for j in (0, 1)))
    out.append(pts[-1])
    return out

class Writer:
    def __init__(self, seed=3, shake=0.006, slant=0.12):
        self.r, self.shake, self.slant, self.strokes = random.Random(seed), shake, slant, []
    def stroke(self, pts, x, y, size, slant=True, shake=None):
        """One stroke of a glyph in the unit box at (x, y), ``size`` high - or,
        with ``slant`` off and a ``shake`` in pixels, one given where it lies."""
        dx, dy = self.r.uniform(-.03, .03) * size, self.r.uniform(-.03, .03) * size
        shake = self.shake if shake is None else shake / size
        out = []
        for px, py in smooth(pts):
            if slant:
                px += (0.5 - py) * self.slant
            out.append((x + px*size + dx + self.r.gauss(0, shake)*size, y + py*size + dy + self.r.gauss(0, shake)*size))
        self.strokes.append(out)
    def put(self, ch, x, y, size):
        w, strokes = G[ch]
        for s in strokes:
            self.stroke(s, x, y, size)
        return x + w * size
    def text(self, chars, x, y, size, gap=0.22):
        for ch in chars:
            if ch == " ":
                x += 0.3 * size
                continue
            x = self.put(ch, x, y, size) + gap * size
        return x
    def line(self, x0, y0, x1, y1):
        n = 6
        self.stroke([(x0 + (x1-x0)*i/n, y0 + (y1-y0)*i/n) for i in range(n+1)], 0, 0, 1, slant=False, shake=0.35)
    def root(self, x, y, size, width):
        """A radical over [x, x+width], body height `size` at y."""
        self.stroke([(x - 0.55*size, y + 0.55*size), (x - 0.42*size, y + 0.5*size), (x - 0.25*size, y + 1.05*size),
                     (x - 0.05*size, y - 0.3*size), (x + width*0.5, y - 0.32*size), (x + width, y - 0.3*size)],
                    0, 0, 1, slant=False, shake=0.35)
    def timed(self):
        t, out = 0.0, []
        for s in self.strokes:
            st = []
            for i, (px, py) in enumerate(s):
                st.append([round(px, 2), round(py, 2), round(t)])
                t += 9
            out.append(st)
            t += 220
        return out
