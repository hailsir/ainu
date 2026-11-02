"""
ainu_flora.py - grass and Rishiri flora for the Ainu village, painted and modelled in code (Blender 5.0)
====================================================================================================
The texture atlas has no grass or plant swatches, so this script paints a 2048 x 2048 foliage
atlas with numpy (BaseColor with alpha, Normal, ORM), models low-poly plants on it, and scatters
them over the village terrain. ainu_village_scene.py calls it while it builds the village. It can
also run on its own to preview and export the plants:

    blender --background --factory-startup --python ainu_flora.py -- \
        --out out/flora --render out/flora.png --save out/flora.blend

Species (all common on Rishiri-to and the Hokkaido coast):

| prefab | plant | where it grows here |
|---|---|---|
| Grass_Meadow | O-nogariyasu (Calamagrostis langsdorffii) meadow grass | grassy ground, thinning onto the earth |
| Grass_Flowers | meadow grass with buttercups (Ranunculus) | patches inside the meadow |
| Leymus_Mollis | Hama-ninniku, beach wild rye | upper beach and the dune line |
| Sasa_Kurilensis | Chishima-zasa, dwarf bamboo | thickets at the village edge and on the hills |
| Heracleum_Lanatum | O-hanaudo, cow parsnip (white umbels) | meadow edges, along the fence |
| Hemerocallis_Esculenta | Ezo-kanzo, day lily | coastal meadow patches |
| Rosa_Rugosa | Hamanasu, beach rose | dune and meadow edge |
| Geranium_Erianthum | Chishima-furo, wild geranium | meadow patches |
| Picea_Jezoensis | Ezo-matsu, Ezo spruce | forest patches on the lower hills |
| Betula_Ermanii | Dake-kanba, Erman's birch | stands higher up the slopes |

Game-readiness:
  * One atlas and two materials (M_FoliageGrass, M_Foliage). Both use alpha clip (glTF MASK) and
    are double-sided. Grass cards carry up-facing normals so they light like the ground.
  * Plants are merged into 16 m chunks per group (Flora_Grass_*, Flora_Plants_*), and trees into
    64 m chunks (Flora_Trees_*): one surface per chunk, so it culls well and lets Godot draw
    distances apply per chunk. Tree chunks carry a *_COL-colonly child with a box per trunk.
  * COLOR_0 = (sway 0..1 from root to tip, random phase, 0, 1). The Godot shader
    foliage.gdshader uses it for wind. Blender ignores it.
  * Grass and plants have no collision.
"""

import bpy
import math
import os
import runpy
import sys
import numpy as np
from mathutils import Vector, Matrix

ATLAS = 2048
# Atlas cells (x0, y0, x1, y1) in pixels, y down. Cards keep the cell's aspect ratio.
CELLS = {
    "grass":     (0, 0, 1024, 512),
    "leymus":    (1024, 0, 2048, 512),
    "sasa_leaf": (0, 512, 256, 1024),
    "strap":     (256, 512, 768, 1024),
    "hera_leaf": (768, 512, 1280, 1024),
    "umbel":     (1280, 512, 1664, 896),
    "lily":      (1664, 512, 2048, 896),
    "rose":      (1280, 896, 1536, 1152),
    "geranium":  (1536, 896, 1792, 1152),
    "rose_leaf": (0, 1024, 512, 1536),
    "ger_leaf":  (512, 1024, 1024, 1536),
    "stem":      (1024, 1152, 1152, 1664),
    "grass_b":   (0, 1536, 1024, 2048),
    "spruce_tier": (1152, 1152, 1664, 1536),
    "birch_leaves": (1664, 1152, 2048, 1536),
    "birch_bark": (1152, 1536, 1408, 2048),
    "spruce_bark": (1408, 1536, 1536, 2048),
}

FLORA = {
    "chunk": 16.0,
    "region": (-34.0, 85.0, -55.0, 75.0),     # x0, x1, y0, y1 (m)
    "variants": 3,
    "seed": 41,
    "alpha_cut": 0.45,
}
# species -> (group, grid spacing m, scale range, rules key)
SPECIES = {
    "Grass_Meadow":           ("grass",  1.35, (1.0, 1.8)),
    "Grass_Flowers":          ("grass",  2.2,  (0.9, 1.4)),
    "Leymus_Mollis":          ("grass",  0.95, (0.8, 1.3)),
    "Sasa_Kurilensis":        ("plants", 2.1,  (0.9, 1.35)),
    "Heracleum_Lanatum":      ("plants", 2.6,  (0.85, 1.15)),
    "Hemerocallis_Esculenta": ("plants", 1.6,  (0.8, 1.2)),
    "Rosa_Rugosa":            ("plants", 2.3,  (0.8, 1.3)),
    "Geranium_Erianthum":     ("plants", 1.5,  (0.8, 1.2)),
    "Picea_Jezoensis":        ("trees",  13.0, (0.75, 1.15)),
    "Betula_Ermanii":         ("trees",  16.0, (0.8, 1.2)),
}
GROUPS = {"grass": dict(mat="M_FoliageGrass", vis=75.0, up=3.0, chunk=16.0, region=FLORA["region"]),
          "plants": dict(mat="M_Foliage", vis=170.0, up=0.45, chunk=16.0, region=FLORA["region"]),
          "trees": dict(mat="M_Foliage", vis=650.0, up=0.5, chunk=64.0, region=(-90.0, 420.0, -110.0, 650.0))}


def log(*a):
    print("[flora]", *a)


# --------------------------------------------------------------------------------------
# Atlas painter (numpy). Colours are sRGB values, premultiplied while painting.
# --------------------------------------------------------------------------------------
def sh_lance(u):
    return np.where(u < 0.3, np.sqrt(np.clip(u / 0.3, 0, 1)), np.clip((1 - u) / 0.7, 0, 1) ** 0.85)


def sh_oval(u):
    return np.sqrt(np.clip(1 - (2 * u - 1) ** 2, 0, 1))


def _sinp(x):
    # sin(pi) is slightly negative in float32; a negative base to a fractional power is NaN
    return np.clip(np.sin(np.pi * np.clip(x, 0, 1)), 0, 1)


def sh_petal(u):
    return _sinp(u) ** 0.6 * (0.55 + 0.45 * u)


def sh_narrow(u):
    return _sinp(u) ** 0.9


def sh_lobe(u):
    return _sinp(np.clip(u, 0, 1) ** 0.75) ** 0.7


def smooth01(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


class Painter:
    def __init__(self, res, seed):
        self.R = res
        self.rgb = np.zeros((res, res, 3), np.float32)
        self.a = np.zeros((res, res), np.float32)
        self.h = np.zeros((res, res), np.float32)
        self.ao = np.ones((res, res), np.float32)
        self.ro = np.full((res, res), 0.7, np.float32)
        self.rng = np.random.default_rng(seed)

    def grid(self, x0, y0, x1, y1):
        x0, y0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
        x1, y1 = min(self.R, int(math.ceil(x1)) + 1), min(self.R, int(math.ceil(y1)) + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        return (slice(y0, y1), slice(x0, x1)), xx + 0.5, yy + 0.5

    def comp(self, sl, cov, rgb, h, ao, ro):
        c = np.clip(cov, 0, 1)
        c3 = c[..., None]
        self.rgb[sl] = np.asarray(rgb, np.float32) * c3 + self.rgb[sl] * (1 - c3)
        self.a[sl] = c + self.a[sl] * (1 - c)
        self.h[sl] = h * c + self.h[sl] * (1 - c)
        self.ao[sl] = ao * c + self.ao[sl] * (1 - c)
        self.ro[sl] = ro * c + self.ro[sl] * (1 - c)

    def blade(self, p0, p1, p2, w0, c0, c1, ao0=0.45, ro=0.75, taper=0.7, clip=None):
        """Tapered blade along a quadratic Bezier (base p0, control p1, tip p2), w0 = base width."""
        t = np.linspace(0, 1, 14, dtype=np.float32)[:, None]
        P = (1 - t) ** 2 * np.array(p0) + 2 * (1 - t) * t * np.array(p1) + t * t * np.array(p2)
        r = w0 + 2
        g = self.grid(P[:, 0].min() - r, P[:, 1].min() - r, P[:, 0].max() + r, P[:, 1].max() + r)
        if g is None:
            return
        sl, xx, yy = g
        A, AB = P[:-1], P[1:] - P[:-1]
        L2 = (AB ** 2).sum(1) + 1e-6
        px, py = xx[..., None], yy[..., None]
        tt = np.clip(((px - A[:, 0]) * AB[:, 0] + (py - A[:, 1]) * AB[:, 1]) / L2, 0, 1)
        dx, dy = px - (A[:, 0] + tt * AB[:, 0]), py - (A[:, 1] + tt * AB[:, 1])
        d2 = dx * dx + dy * dy
        k = d2.argmin(-1)
        d = np.sqrt(np.take_along_axis(d2, k[..., None], -1)[..., 0])
        ts = (k + np.take_along_axis(tt, k[..., None], -1)[..., 0]) / (len(P) - 1)
        w = 0.5 * w0 * np.maximum(1 - ts, 0) ** taper + 0.35
        cov = np.clip(w - d + 0.5, 0, 1)
        if clip is not None:                       # keep inside the cell
            x0, y0, x1, y1 = clip
            cov *= (xx > x0 + 1) & (xx < x1 - 1) & (yy > y0 + 1) & (yy < y1 - 1)
        c0, c1 = np.array(c0, np.float32), np.array(c1, np.float32)
        rgb = c0 + (c1 - c0) * ts[..., None]
        rgb = rgb * (0.94 + 0.06 * np.sin(ts * 37.0 + p0[0] * 0.13))[..., None]
        h = 1 - d / np.maximum(w, 0.5)
        self.comp(sl, cov, rgb, h, ao0 + (1 - ao0) * ts, ro)

    def leaf(self, base, ang, L, W, shape, c_mid, c_edge=None, edge=0.0, c_base=None, serr=0.0, teeth=20,
             lobing=0.0, lobes=3, veins=0.0, nveins=8, stripes=0.0, ro=0.6, ao0=0.65, clip=None):
        """Leaf / petal: base point, angle (radians, image space: -pi/2 = up), length L, width W (px)."""
        ca, sa = math.cos(ang), math.sin(ang)
        R = L + W
        g = self.grid(base[0] - R, base[1] - R, base[0] + R, base[1] + R)
        if g is None:
            return
        sl, xx, yy = g
        dx, dy = xx - base[0], yy - base[1]
        u = (dx * ca + dy * sa) / L
        v = -dx * sa + dy * ca
        uc = np.clip(u, 0, 1)
        hw = 0.5 * W * shape(uc)
        if lobing:
            hw *= 1 - lobing * np.abs(np.sin(uc * math.pi * lobes)) * uc
        if serr:
            hw *= 1 - serr * np.abs(np.sin(uc * math.pi * teeth))
        av = np.abs(v)
        cov = np.clip(hw - av + 0.5, 0, 1) * np.clip(u * L + 0.5, 0, 1) * np.clip((1 - u) * L + 0.5, 0, 1)
        if clip is not None:                       # keep inside the cell
            x0, y0, x1, y1 = clip
            cov *= (xx > x0 + 1) & (xx < x1 - 1) & (yy > y0 + 1) & (yy < y1 - 1)
        t = av / np.maximum(hw, 1e-3)
        rgb = np.broadcast_to(np.array(c_mid, np.float32), t.shape + (3,)).copy()
        if c_base is not None:
            rgb = rgb + (np.array(c_base, np.float32) - rgb) * (1 - smooth01(uc / 0.35))[..., None]
        rgb *= (1 - 0.16 * t)[..., None]
        if c_edge is not None and edge:
            rgb += (np.array(c_edge, np.float32) - rgb) * smooth01((t - (1 - edge)) / max(edge, 1e-3))[..., None]
        h = 0.7 * (1 - t * t)
        mid = np.exp(-(av / 1.8) ** 2)
        h -= 0.25 * mid
        rgb *= (1 + 0.12 * mid)[..., None]
        if veins:
            ph = uc * nveins - t * 0.7
            vl = np.exp(-((ph - np.round(ph)) * 9) ** 2)
            h -= 0.15 * veins * vl
            rgb *= (1 - 0.12 * veins * vl)[..., None]
        if stripes:
            sp = np.cos(t * math.pi * stripes) ** 8
            rgb *= (1 - 0.1 * sp)[..., None]
            h -= 0.08 * sp
        self.comp(sl, cov, rgb, h, ao0 + (1 - ao0) * uc, ro)

    def disc(self, c, r, rgb, h=1.0, ro=0.6, ao=1.0):
        g = self.grid(c[0] - r - 2, c[1] - r - 2, c[0] + r + 2, c[1] + r + 2)
        if g is None:
            return
        sl, xx, yy = g
        d = np.hypot(xx - c[0], yy - c[1])
        cov = np.clip(r - d + 0.5, 0, 1)
        self.comp(sl, cov, rgb, h * np.sqrt(np.clip(1 - (d / r) ** 2, 0, 1)), ao, ro)

    # ------------------------------------------------------------------ cells
    def grass(self, cell, n, hr, wr, greens, dry=0.12, arch=0.0, ao0=0.35, flowers=0):
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        rng = self.rng
        for i in range(n):
            depth = i / n                                  # later blades are in front: lighter
            bx = x0 + W * (0.05 + 0.9 * rng.random())
            by = y1 - 1
            hgt = H * (hr[0] + (hr[1] - hr[0]) * rng.random())
            lean = (rng.random() - 0.5) * hgt * 0.8
            tip = [min(max(bx + lean, x0 + 6), x1 - 6), max(by - hgt * (1 - arch), y0 + 6)]
            ctrl = [bx + lean * 0.15, by - hgt * (0.6 + 0.5 * arch)]
            c = greens[rng.integers(len(greens))]
            if rng.random() < dry:
                c = ((0.55, 0.5, 0.28), (0.72, 0.66, 0.4))
            shade = 0.75 + 0.25 * depth
            c0 = tuple(v * shade * 0.8 for v in c[0])
            c1 = tuple(v * shade for v in c[1])
            self.blade((bx, by), ctrl, tip, wr[0] + (wr[1] - wr[0]) * rng.random(), c0, c1,
                       ao0=ao0 + 0.3 * depth, clip=cell)
        for i in range(flowers):                          # buttercup-like heads on thin stems
            bx = x0 + W * (0.08 + 0.84 * rng.random())
            hgt = H * (0.45 + 0.45 * rng.random())
            tip = (bx + (rng.random() - 0.5) * 40, y1 - hgt)
            self.blade((bx, y1 - 1), (bx, y1 - hgt * 0.5), tip, 3, (0.2, 0.32, 0.1), (0.3, 0.45, 0.15), clip=cell)
            col = (0.98, 0.85, 0.15) if rng.random() < 0.7 else (0.95, 0.95, 0.9)
            for k in range(5):
                a = k * 2 * math.pi / 5
                self.disc((tip[0] + 7 * math.cos(a), tip[1] + 7 * math.sin(a)), 6.5, col, ro=0.4)
            self.disc(tip, 4, (0.85, 0.65, 0.1), ro=0.5)

    def leymus(self, cell):
        greens = [((0.3, 0.4, 0.34), (0.55, 0.64, 0.56)), ((0.34, 0.44, 0.36), (0.6, 0.68, 0.58)),
                  ((0.28, 0.36, 0.3), (0.5, 0.58, 0.5))]
        self.grass(cell, 46, (0.45, 0.95), (11, 17), greens, dry=0.08, arch=0.25, ao0=0.4)
        x0, y0, x1, y1 = cell
        rng = self.rng
        for i in range(6):                                 # seed spikes
            bx = x0 + (x1 - x0) * (0.15 + 0.7 * rng.random())
            top = y0 + 20 + 60 * rng.random()
            lean = (rng.random() - 0.5) * 60
            self.blade((bx, y1 - 1), (bx + lean * 0.3, (top + y1) / 2), (bx + lean, top + 70), 4,
                       (0.45, 0.48, 0.36), (0.62, 0.6, 0.45), clip=cell)
            self.leaf((bx + lean, top + 72), -math.pi / 2 + lean / 400, 78, 15, sh_oval,
                      (0.72, 0.66, 0.46), stripes=6, ro=0.8)

    def sasa_leaf(self, cell):
        x0, y0, x1, y1 = cell
        cx = (x0 + x1) / 2
        self.blade((cx, y1 - 1), (cx, y1 - 10), (cx, y1 - 24), 6, (0.3, 0.36, 0.14), (0.25, 0.38, 0.12))
        self.leaf((cx, y1 - 20), -math.pi / 2, (y1 - y0) - 28, (x1 - x0) * 0.44, sh_lance, (0.2, 0.38, 0.1),
                  c_edge=(0.8, 0.76, 0.52), edge=0.18, stripes=7, ro=0.5, ao0=0.7)

    def strap(self, cell):
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        cx = (x0 + x1) / 2
        rng = self.rng
        for i in range(16):
            side = -1 if i % 2 else 1
            bx = cx + (rng.random() - 0.5) * 50
            tipx = cx + side * W * (0.12 + 0.36 * rng.random())
            tipy = y1 - H * (0.3 + 0.62 * rng.random())
            ctrl = (bx + (tipx - bx) * 0.25, y1 - H * (0.75 + 0.25 * rng.random()))
            shade = 0.8 + 0.2 * i / 16
            self.blade((bx, y1 - 1), ctrl, (tipx, tipy), 18 + 8 * rng.random(),
                       (0.14 * shade, 0.28 * shade, 0.08 * shade), (0.25 * shade, 0.45 * shade, 0.14 * shade),
                       ao0=0.4, ro=0.55, taper=0.5, clip=cell)

    def hera_leaf(self, cell):
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        cx = (x0 + x1) / 2
        jy = y1 - H * 0.24
        self.blade((cx, y1 - 1), (cx, (y1 + jy) / 2), (cx, jy), 12, (0.3, 0.4, 0.16), (0.24, 0.4, 0.13))
        green = (0.19, 0.37, 0.11)
        for ang, L in ((-math.pi / 2 - 1.08, 0.48), (-math.pi / 2 + 1.08, 0.48), (-math.pi / 2, 0.72)):
            self.leaf((cx, jy), ang, H * L, H * L * 0.82, sh_oval, green, serr=0.05, teeth=26,
                      lobing=0.22, lobes=3, veins=0.8, nveins=6, ro=0.6, ao0=0.55)

    def umbel(self, cell):
        x0, y0, x1, y1 = cell
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        R = 0.46 * min(x1 - x0, y1 - y0)
        n = 21
        centres = []
        for i in range(n):
            r = R * 0.8 * math.sqrt((i + 0.5) / n)
            a = i * 2.39996
            centres.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a), r / (R * 0.8)))
        for px, py, _ in centres:                     # rays
            self.blade(c, ((c[0] + px) / 2, (c[1] + py) / 2), (px, py), 5, (0.3, 0.42, 0.18), (0.4, 0.5, 0.25))
        rng = self.rng
        for px, py, rr in centres:
            ru = R * 0.17
            for j in range(13):
                r2 = ru * math.sqrt((j + 0.5) / 13)
                a2 = j * 2.39996 + rng.random()
                big = 1.0 + 0.7 * rr * max(0.0, math.cos(a2 - math.atan2(py - c[1], px - c[0])))
                col = (0.93, 0.93, 0.86) if j % 4 else (0.84, 0.88, 0.72)
                self.disc((px + r2 * math.cos(a2), py + r2 * math.sin(a2)), R * 0.032 * big, col, ro=0.55)

    def flower(self, cell, n, L, W, shape, col, c_base, centre, centre_r, inner=None, veins=0.0, stripes=0.0):
        x0, y0, x1, y1 = cell
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        S = min(x1 - x0, y1 - y0)
        rot = self.rng.random()
        for k in range(n):
            a = rot + k * 2 * math.pi / n
            self.leaf(c, a, S * L, S * W, shape, col, c_base=c_base, veins=veins, nveins=5, stripes=stripes,
                      ro=0.45, ao0=0.75)
        if inner:
            for k in range(inner[0]):
                a = rot + (k + 0.5) * 2 * math.pi / inner[0]
                self.leaf(c, a, S * inner[1], S * inner[2], shape, col, c_base=c_base, ro=0.45, ao0=0.8)
        self.disc(c, S * centre_r, centre, ro=0.5)

    def lily(self, cell):
        self.flower(cell, 3, 0.47, 0.22, sh_narrow, (0.99, 0.62, 0.12), (0.88, 0.42, 0.06), (0.85, 0.42, 0.06), 0.06,
                    inner=(3, 0.45, 0.32))
        x0, y0, x1, y1 = cell
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        S = x1 - x0
        for k in range(6):                                 # stamens
            a = k * math.pi / 3 + 0.3
            tip = (c[0] + S * 0.26 * math.cos(a), c[1] + S * 0.26 * math.sin(a))
            self.blade(c, ((c[0] + tip[0]) / 2, (c[1] + tip[1]) / 2), tip, 3, (0.95, 0.7, 0.3), (0.95, 0.75, 0.35))
            self.disc(tip, 5, (0.45, 0.25, 0.1))

    def rose(self, cell):
        self.flower(cell, 5, 0.44, 0.5, sh_petal, (0.86, 0.2, 0.5), (0.95, 0.72, 0.84), (0.98, 0.82, 0.3), 0.12,
                    veins=0.4)
        x0, y0, x1, y1 = cell
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        for k in range(14):
            a = k * 2.39996
            r = (x1 - x0) * 0.1 * math.sqrt((k + 0.5) / 14)
            self.disc((c[0] + r * math.cos(a), c[1] + r * math.sin(a)), 3.5, (0.9, 0.7, 0.2))

    def geranium(self, cell):
        self.flower(cell, 5, 0.45, 0.46, sh_petal, (0.55, 0.45, 0.88), (0.85, 0.82, 0.95), (0.95, 0.95, 0.92), 0.06,
                    stripes=5)

    def rose_leaf(self, cell):
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        cx = (x0 + x1) / 2
        dark = (0.13, 0.3, 0.08)
        self.blade((cx, y1 - 1), (cx, y1 - H * 0.5), (cx, y0 + H * 0.12), 7, (0.3, 0.32, 0.12), (0.2, 0.32, 0.1))
        for u, L in ((0.3, 0.25), (0.52, 0.24), (0.72, 0.21)):
            y = y1 - H * u
            for s in (-1, 1):
                self.leaf((cx, y), -math.pi / 2 + s * 1.0, H * L, H * L * 0.56, sh_oval, dark, serr=0.06, teeth=16,
                          veins=0.7, nveins=7, ro=0.42, ao0=0.6)
        self.leaf((cx, y0 + H * 0.36), -math.pi / 2, H * 0.27, H * 0.27 * 0.56, sh_oval, dark, serr=0.06, teeth=16,
                  veins=0.7, nveins=7, ro=0.42, ao0=0.6)

    def ger_leaf(self, cell):
        x0, y0, x1, y1 = cell
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        S = min(x1 - x0, y1 - y0)
        for k in range(7):
            a = math.radians(-240 + k * 50)
            self.leaf(c, a, S * 0.46, S * 0.46 * 0.42, sh_lobe, (0.18, 0.36, 0.1), serr=0.18, teeth=7,
                      veins=0.5, nveins=5, ro=0.6, ao0=0.55)

    def stem(self, cell):
        x0, y0, x1, y1 = cell
        sl = (slice(y0, y1), slice(x0, x1))
        xx = np.arange(x1 - x0, dtype=np.float32)[None, :] / (x1 - x0)
        yy = np.arange(y1 - y0, dtype=np.float32)[:, None] / (y1 - y0)
        base = np.array([0.36, 0.42, 0.16], np.float32)
        top = np.array([0.26, 0.42, 0.15], np.float32)
        rgb = base + (top - base) * (1 - yy)[..., None]
        rib = np.cos(xx * math.pi * 10) ** 6
        rgb = rgb * (1 - 0.15 * rib)[..., None] * np.ones_like(yy)[..., None]
        self.comp(sl, np.ones((y1 - y0, x1 - x0), np.float32), rgb, 0.5 - 0.3 * rib + 0 * yy, 0.85, 0.6)

    def spruce_tier(self, cell):
        """One tier of a spruce seen from the side: dense needles with drooping branch tips along
        the bottom edge (the cone skirts map t = 0 to that edge)."""
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        rng = self.rng
        xs = np.arange(x0, x1, dtype=np.float32) + 0.5
        base = y0 + H * 0.55
        edge = np.full(W, base, np.float32)
        for k in range(10):
            xi = x0 + W * (k + 0.5) / 10 + rng.normal() * W * 0.015
            yi = y0 + H * (0.84 + 0.13 * rng.random())
            wi = W * (0.035 + 0.025 * rng.random())
            edge = np.maximum(edge, base + (yi - base) * np.exp(-((xs - xi) / wi) ** 2))
        edge = np.minimum(edge, y1 - 2)
        sl = (slice(y0, y1), slice(x0, x1))
        yy = np.arange(y0, y1, dtype=np.float32)[:, None] + 0.5
        cov = np.clip(edge[None, :] - yy + 0.5, 0, 1)
        f = ((yy - y0) / H) * np.ones((1, W), np.float32)
        rgb = np.stack([0.05 + 0.04 * f, 0.11 + 0.08 * f, 0.07 + 0.04 * f], -1)
        self.comp(sl, cov, rgb, 0.3 * np.ones_like(f), 0.45 + 0.4 * f, 0.8)
        for i in range(1100):
            px = x0 + 2 + (W - 4) * rng.random()
            e = float(np.interp(px, xs, edge))
            py = y0 + (e - y0) * rng.random() ** 0.6
            L = 12 + 20 * rng.random()
            a = math.pi / 2 + (rng.random() - 0.5) * 1.3
            tip = (px + math.cos(a) * L, py + math.sin(a) * L)
            t = (py - y0) / H
            lift = 0.75 + 0.5 * t
            c0 = (0.05 * lift, 0.12 * lift, 0.08 * lift)
            c1 = (0.13 * lift, 0.25 * lift, 0.16 * lift) if rng.random() > 0.15 else (0.2, 0.33, 0.19)
            self.blade((px, py), ((px + tip[0]) / 2, (py + tip[1]) / 2 - 2), tip, 3.0, c0, c1,
                       ao0=0.45 + 0.4 * t, ro=0.8, clip=cell)

    def birch_leaves(self, cell):
        x0, y0, x1, y1 = cell
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        S = min(x1 - x0, y1 - y0)
        rng = self.rng
        for k in range(10):                               # twigs
            a = rng.random() * 2 * math.pi
            tip = (c[0] + S * 0.42 * math.cos(a), c[1] + S * 0.42 * math.sin(a))
            self.blade(c, ((c[0] + tip[0]) / 2 + 8, (c[1] + tip[1]) / 2), tip, 4, (0.3, 0.24, 0.18),
                       (0.36, 0.3, 0.22), clip=cell)
        greens = [(0.3, 0.48, 0.13), (0.38, 0.56, 0.17), (0.25, 0.4, 0.11), (0.45, 0.58, 0.2)]
        shape = lambda u: _sinp(np.clip(u, 0, 1) ** 0.8) ** 0.8
        n = 140
        for i in range(n):
            rr = S * 0.42 * math.sqrt(rng.random())
            a = rng.random() * 2 * math.pi
            base = (c[0] + rr * math.cos(a), c[1] + rr * math.sin(a))
            L = S * (0.09 + 0.05 * rng.random())
            col = greens[rng.integers(len(greens))]
            depth = 0.7 + 0.3 * i / n
            self.leaf(base, rng.random() * 2 * math.pi, L, L * 0.62, shape, tuple(v * depth for v in col),
                      serr=0.07, teeth=12, veins=0.5, nveins=6, ro=0.55, ao0=0.55 + 0.3 * i / n, clip=cell)

    def birch_bark(self, cell):
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        rng = self.rng
        sl = (slice(y0, y1), slice(x0, x1))
        yy = np.arange(H, dtype=np.float32)[:, None]
        xx = np.arange(W, dtype=np.float32)[None, :]
        tone = 0.92 + 0.05 * np.sin(yy * 0.05 + xx * 0.03) + 0.03 * np.sin(xx * 0.21)
        rgb = np.stack([0.84 * tone, 0.79 * tone, 0.73 * tone], -1)
        self.comp(sl, np.ones((H, W), np.float32), rgb, 0.5 * np.ones((H, W), np.float32), 0.85, 0.6)
        for i in range(18):                               # peeling, pinkish-orange patches
            self.leaf((x0 + W * rng.random(), y0 + H * rng.random()), (rng.random() - 0.5) * 0.3,
                      60 + 70 * rng.random(), 18 + 22 * rng.random(), sh_oval, (0.82, 0.62, 0.52), ro=0.6, ao0=0.8,
                      clip=cell)
        for i in range(140):                              # dark horizontal lenticels
            L = 14 + 50 * rng.random()
            self.leaf((x0 + W * rng.random() - L / 2, y0 + H * rng.random()), (rng.random() - 0.5) * 0.1,
                      L, 2.5 + 3.5 * rng.random(), sh_oval, (0.24, 0.2, 0.18), ro=0.75, ao0=0.6, clip=cell)

    def spruce_bark(self, cell):
        x0, y0, x1, y1 = cell
        W, H = x1 - x0, y1 - y0
        rng = self.rng
        sl = (slice(y0, y1), slice(x0, x1))
        self.comp(sl, np.ones((H, W), np.float32), (0.25, 0.21, 0.17), np.zeros((H, W), np.float32), 0.7, 0.85)
        for i in range(160):                              # scaly plates
            L = 14 + 14 * rng.random()
            col = (0.36, 0.3, 0.24) if rng.random() < 0.6 else (0.2, 0.16, 0.13)
            self.leaf((x0 + W * rng.random(), y0 + H * rng.random()), -math.pi / 2 + (rng.random() - 0.5) * 0.8,
                      L, L * 0.7, sh_oval, col, ro=0.85, ao0=0.55, clip=cell)

    def paint_all(self):
        greens = [((0.16, 0.3, 0.08), (0.36, 0.52, 0.16)), ((0.2, 0.34, 0.1), (0.42, 0.56, 0.2)),
                  ((0.14, 0.26, 0.07), (0.3, 0.46, 0.13)), ((0.22, 0.32, 0.1), (0.5, 0.58, 0.24))]
        self.grass(CELLS["grass"], 95, (0.45, 0.97), (6, 10), greens)
        self.grass(CELLS["grass_b"], 80, (0.3, 0.75), (6, 9), greens, dry=0.18, flowers=12)
        self.leymus(CELLS["leymus"])
        self.sasa_leaf(CELLS["sasa_leaf"])
        self.strap(CELLS["strap"])
        self.hera_leaf(CELLS["hera_leaf"])
        self.umbel(CELLS["umbel"])
        self.lily(CELLS["lily"])
        self.rose(CELLS["rose"])
        self.geranium(CELLS["geranium"])
        self.rose_leaf(CELLS["rose_leaf"])
        self.ger_leaf(CELLS["ger_leaf"])
        self.stem(CELLS["stem"])
        self.spruce_tier(CELLS["spruce_tier"])
        self.birch_leaves(CELLS["birch_leaves"])
        self.birch_bark(CELLS["birch_bark"])
        self.spruce_bark(CELLS["spruce_bark"])

    # ------------------------------------------------------------------ outputs
    def fill_colour(self):
        """Straight (un-premultiplied) colour with the transparent area filled from the nearest
        painted colour (push-pull), so alpha-clipped mipmaps get no dark fringes."""
        a = self.a
        pc, pa = self.rgb.copy(), a.copy()
        levels = []
        while pa.shape[0] > 8:
            levels.append((pc, pa))
            pc = 0.25 * (pc[0::2, 0::2] + pc[1::2, 0::2] + pc[0::2, 1::2] + pc[1::2, 1::2])
            pa = 0.25 * (pa[0::2, 0::2] + pa[1::2, 0::2] + pa[0::2, 1::2] + pa[1::2, 1::2])
        col = pc / np.maximum(pa, 1e-6)[..., None]
        col[pa < 1e-6] = (0.25, 0.35, 0.12)
        for lpc, lpa in reversed(levels):
            up = np.repeat(np.repeat(col, 2, 0), 2, 1)
            own = lpc / np.maximum(lpa, 1e-6)[..., None]
            w = np.clip(lpa * 4, 0, 1)[..., None]
            col = own * w + up * (1 - w)
        return np.clip(col, 0, 1)

    def normal(self, strength=2.5):
        h = self.h
        hb = (h + np.roll(h, 1, 0) + np.roll(h, -1, 0) + np.roll(h, 1, 1) + np.roll(h, -1, 1)) / 5
        gx = (np.roll(hb, -1, 1) - np.roll(hb, 1, 1)) * 0.5
        gr = (np.roll(hb, -1, 0) - np.roll(hb, 1, 0)) * 0.5      # d/d(row): rows run down
        n = np.stack([-gx * strength, gr * strength, np.ones_like(h)], -1)   # OpenGL +Y (v up = -row)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        return n * 0.5 + 0.5


def _save(arr, path, alpha=None):
    """arr: (H, W, 3) top-down image rows; saved raw (no colour transform)."""
    h, w = arr.shape[:2]
    px = np.ones((h, w, 4), np.float32)
    px[..., :3] = arr
    if alpha is not None:
        px[..., 3] = alpha
    im = bpy.data.images.new("_fl_save", w, h, alpha=alpha is not None, float_buffer=False)
    im.colorspace_settings.name = "Non-Color"
    im.pixels.foreach_set(np.flipud(px).ravel())
    im.filepath_raw = path
    im.file_format = "PNG"
    im.save()
    bpy.data.images.remove(im)


def _resize(arr, res):
    h, w, c = arr.shape
    if h == res:
        return arr
    px = np.ones((h, w, 4), np.float32)
    px[..., :c] = arr
    im = bpy.data.images.new("_fl_rs", w, h, alpha=True, float_buffer=True)
    im.pixels.foreach_set(px.ravel())
    im.scale(res, res)
    out = np.empty(res * res * 4, np.float32)
    im.pixels.foreach_get(out)
    bpy.data.images.remove(im)
    return out.reshape(res, res, 4)[..., :c]


def make_textures(out_dir, res=2048, force=False):
    """T_Foliage_BaseColor.png (RGBA), T_Foliage_Normal.png, T_Foliage_ORM.png."""
    os.makedirs(out_dir, exist_ok=True)
    paths = {k: os.path.join(out_dir, "T_Foliage_%s.png" % s)
             for k, s in (("albedo", "BaseColor"), ("normal", "Normal"), ("orm", "ORM"))}
    if not force and all(os.path.isfile(p) for p in paths.values()) and \
            os.path.getmtime(paths["albedo"]) > os.path.getmtime(os.path.abspath(__file__) if "__file__" in globals() else paths["albedo"]):
        log("foliage atlas up to date")
        return paths
    p = Painter(ATLAS, FLORA["seed"])
    p.paint_all()
    bad = int(np.isnan(p.rgb).any(-1).sum() + np.isnan(p.h).sum())
    if bad:
        raise RuntimeError("foliage painter produced %d NaN pixels" % bad)
    col, nrm = p.fill_colour(), p.normal()
    orm = np.stack([p.ao, p.ro, np.zeros_like(p.ao)], -1)
    alpha = p.a
    if res != ATLAS:
        col, nrm, orm = _resize(col, res), _resize(nrm, res), _resize(orm, res)
        alpha = _resize(np.repeat(alpha[..., None], 3, -1), res)[..., 0]
        n = nrm * 2 - 1
        nrm = n / np.linalg.norm(n, axis=-1, keepdims=True) * 0.5 + 0.5
    _save(col, paths["albedo"], alpha)
    _save(nrm, paths["normal"])
    _save(orm, paths["orm"])
    log("painted foliage atlas %d px -> %s (coverage %.0f%%)" % (res, out_dir, 100 * float((alpha > 0.5).mean())))
    return paths


# --------------------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------------------
def _srgb(c):
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)


def make_materials(tex):
    """Alpha clip (exported as glTF MASK), double-sided, ORM through the glTF output group."""
    group = bpy.data.node_groups.get("glTF Material Output")
    if group is None:
        group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
        group.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
        group.interface.new_socket("Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
    mats = {}
    for gname, g in GROUPS.items():
        name = g["mat"]
        m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        if m.node_tree is None:
            m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        L = nt.links.new
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (600, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (300, 0)
        L(bsdf.outputs["BSDF"], out.inputs["Surface"])

        def img(path, cs, y):
            n = nt.nodes.new("ShaderNodeTexImage")
            n.image = bpy.data.images.load(path, check_existing=True)
            n.image.colorspace_settings.name = cs
            n.image.alpha_mode = "STRAIGHT"
            n.interpolation = "Linear"
            n.location = (-700, y)
            return n

        a = img(tex["albedo"], "sRGB", 300)
        L(a.outputs["Color"], bsdf.inputs["Base Color"])
        lt = nt.nodes.new("ShaderNodeMath")              # 1 - (alpha < cut): the exporter's alpha-clip pattern
        lt.operation = "LESS_THAN"
        lt.inputs[1].default_value = FLORA["alpha_cut"]
        lt.location = (-350, 450)
        inv = nt.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        inv.location = (-150, 450)
        L(a.outputs["Alpha"], lt.inputs[0])
        L(lt.outputs[0], inv.inputs[1])
        L(inv.outputs[0], bsdf.inputs["Alpha"])
        n = img(tex["normal"], "Non-Color", -300)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (-350, -300)
        L(n.outputs["Color"], nm.inputs["Color"])
        L(nm.outputs["Normal"], bsdf.inputs["Normal"])
        o = img(tex["orm"], "Non-Color", 0)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (-350, 0)
        L(o.outputs["Color"], sep.inputs["Color"])
        L(sep.outputs["Green"], bsdf.inputs["Roughness"])
        L(sep.outputs["Blue"], bsdf.inputs["Metallic"])
        gn = nt.nodes.new("ShaderNodeGroup")
        gn.node_tree = group
        gn.location = (300, -450)
        L(sep.outputs["Red"], gn.inputs["Occlusion"])
        for sock, val in (("Subsurface Weight", 0.0), ("Specular IOR Level", 0.35)):
            if sock in bsdf.inputs:
                bsdf.inputs[sock].default_value = val
        m.use_backface_culling = False
        for attr, val in (("surface_render_method", "DITHERED"), ("blend_method", "CLIP")):
            try:
                setattr(m, attr, val)
            except (AttributeError, TypeError):
                pass
        m["tile_m"] = 1.0
        m.diffuse_color = _srgb((0.3, 0.45, 0.15))
        mats[gname] = m
    return mats


# --------------------------------------------------------------------------------------
# Plant records: numpy geometry with per-corner UVs and normals (one per variant)
# --------------------------------------------------------------------------------------
Z = Vector((0, 0, 1))


def cuv(cell, s, t):
    """UV inside a cell: s across (0 left .. 1 right), t up (0 bottom .. 1 top)."""
    x0, y0, x1, y1 = CELLS[cell]
    e = 0.5
    x = x0 + e + (x1 - x0 - 2 * e) * s
    y = y1 - e - (y1 - y0 - 2 * e) * t
    return (x / ATLAS, 1.0 - y / ATLAS)


class Rec:
    """Accumulates quads/tris with UVs; becomes a numpy record."""

    def __init__(self):
        self.v, self.f, self.uv = [], [], []

    def poly(self, pts, uvs):
        i = len(self.v)
        self.v += [tuple(p) for p in pts]
        self.f.append(list(range(i, i + len(pts))))
        self.uv.append(list(uvs))

    def card(self, cell, base, ang, width, height, bend=0.15, segs=2, s0=0.0, s1=1.0):
        d = Vector((math.cos(ang), math.sin(ang), 0))
        n = Vector((-d.y, d.x, 0))
        rows = []
        for k in range(segs + 1):
            t = k / segs
            c = Vector(base) + n * (bend * height * t * t) + Z * (height * t * (1 - 0.12 * bend * t))
            rows.append((c - d * width / 2, c + d * width / 2, t))
        for k in range(segs):
            (a0, a1, t0), (b0, b1, t1) = rows[k], rows[k + 1]
            self.poly([a0, a1, b1, b0], [cuv(cell, s0, t0), cuv(cell, s1, t0), cuv(cell, s1, t1), cuv(cell, s0, t1)])

    def leaf(self, cell, base, direction, L, W, droop=0.0, segs=1, fold=0.0):
        d = Vector(direction).normalized()
        side = Z.cross(d)
        side = side.normalized() if side.length > 1e-4 else Vector((1, 0, 0))
        rows = []
        for k in range(segs + 1):
            t = k / segs
            c = Vector(base) + d * (L * t) - Z * (droop * L * t * t)
            if fold:
                rows.append(([c - side * W / 2 + Z * fold * W, c, c + side * W / 2 + Z * fold * W], (0, 0.5, 1), t))
            else:
                rows.append(([c - side * W / 2, c + side * W / 2], (0, 1), t))
        for k in range(segs):
            (A, S, t0), (B, _, t1) = rows[k], rows[k + 1]
            for j in range(len(A) - 1):
                self.poly([A[j], A[j + 1], B[j + 1], B[j]],
                          [cuv(cell, S[j], t0), cuv(cell, S[j + 1], t0), cuv(cell, S[j + 1], t1), cuv(cell, S[j], t1)])

    def quad(self, cell, c, normal, up, size):
        """Square card centred on c facing `normal` (flowers, top-view leaves)."""
        nrm = Vector(normal).normalized()
        u = Vector(up)
        u = (u - nrm * u.dot(nrm)).normalized()
        r = u.cross(nrm).normalized()
        h = size / 2
        c = Vector(c)
        self.poly([c - r * h - u * h, c + r * h - u * h, c + r * h + u * h, c - r * h + u * h],
                  [cuv(cell, 0, 0), cuv(cell, 1, 0), cuv(cell, 1, 1), cuv(cell, 0, 1)])

    def umbel(self, c, R, dome=0.3, n=8):
        c = Vector(c)
        top = c + Z * (dome * R)
        ring = [c + Vector((R * math.cos(2 * math.pi * k / n), R * math.sin(2 * math.pi * k / n), 0)) for k in range(n)]
        uvr = [cuv("umbel", 0.5 + 0.5 * math.cos(2 * math.pi * k / n), 0.5 + 0.5 * math.sin(2 * math.pi * k / n))
               for k in range(n)]
        for k in range(n):
            k1 = (k + 1) % n
            self.poly([top, ring[k], ring[k1]], [cuv("umbel", 0.5, 0.5), uvr[k], uvr[k1]])

    def stem(self, pts, r, sides=3, r1=None, cell="stem", seg_len=None):
        """Tube along a polyline, tapering from r to r1. With seg_len, the polyline is split into
        pieces no longer than seg_len and the cell's V restarts on each (bark that repeats)."""
        P = [Vector(p) for p in pts]
        if seg_len:
            Q = [P[0]]
            for a, b in zip(P, P[1:]):
                n = max(1, int(math.ceil((b - a).length / seg_len)))
                Q += [a + (b - a) * (k / n) for k in range(1, n + 1)]
            P = Q
        r1 = r if r1 is None else r1
        total = sum((P[i + 1] - P[i]).length for i in range(len(P) - 1)) or 1.0
        acc = 0.0
        for i in range(len(P) - 1):
            t = (P[i + 1] - P[i]).normalized()
            ref = Z if abs(t.z) < 0.95 else Vector((1, 0, 0))
            a = (ref - t * ref.dot(t)).normalized()
            bb = t.cross(a)
            seg = (P[i + 1] - P[i]).length
            ra, rb = r + (r1 - r) * acc / total, r + (r1 - r) * (acc + seg) / total
            v0, v1 = (0.03, 0.97) if seg_len else (acc / total, (acc + seg) / total)
            for k in range(sides):
                a0, a1 = 2 * math.pi * k / sides, 2 * math.pi * (k + 1) / sides
                d0 = a * math.cos(a0) + bb * math.sin(a0)
                d1 = a * math.cos(a1) + bb * math.sin(a1)
                s0, s1 = 0.06 + 0.88 * k / sides, 0.06 + 0.88 * (k + 1) / sides    # inset: no bleed from neighbours
                self.poly([P[i] + d0 * ra, P[i] + d1 * ra, P[i + 1] + d1 * rb, P[i + 1] + d0 * rb],
                          [cuv(cell, s0, v0), cuv(cell, s1, v0), cuv(cell, s1, v1), cuv(cell, s0, v1)])
            acc += seg

    def skirt(self, cell, zb, rb, zt, rt, sides=8, rot=0.0, jitter=None, centre=(0.0, 0.0)):
        """Open cone ring from a wide bottom (cell t = 0, the ragged edge) to a narrow top."""
        cx, cy = centre
        ring_b, ring_t = [], []
        for k in range(sides):
            a = rot + 2 * math.pi * k / sides
            j = 1.0 + (jitter[k] if jitter else 0.0)
            ring_b.append(Vector((cx + rb * j * math.cos(a), cy + rb * j * math.sin(a), zb - 0.12 * rb * (j - 1))))
            ring_t.append(Vector((cx + rt * math.cos(a), cy + rt * math.sin(a), zt)))
        top = 0.9                                     # keep clear of the cell above: mipmaps would bleed it in
        for k in range(sides):
            k1 = (k + 1) % sides
            self.poly([ring_b[k], ring_b[k1], ring_t[k1], ring_t[k]],
                      [cuv(cell, 0.02, 0), cuv(cell, 0.98, 0), cuv(cell, 0.98, top), cuv(cell, 0.02, top)])

    def finish(self, up_k):
        """numpy record: verts, faces (loop start/total), corner verts, UVs, normals, sway."""
        V = np.array(self.v, np.float32)
        lv, luv, ln, st, tot = [], [], [], [], []
        for f, uv in zip(self.f, self.uv):
            P = V[f]
            fn = np.cross(P[1] - P[0], P[2] - P[0])
            if len(f) == 4:
                fn = fn + np.cross(P[2] - P[0], P[3] - P[0])
            ln_ = np.linalg.norm(fn)
            fn = fn / ln_ if ln_ > 1e-9 else np.array([0, 0, 1.0])
            if fn[2] < 0:                                   # face up (normal and winding together)
                f, uv, fn = f[::-1], uv[::-1], -fn
            n = fn + np.array([0, 0, up_k])
            n /= np.linalg.norm(n)
            st.append(len(lv))
            tot.append(len(f))
            lv += f
            luv += uv
            ln += [n] * len(f)
        hgt = max(float(V[:, 2].max()), 0.05)
        return dict(v=V, lv=np.array(lv, np.int32), uv=np.array(luv, np.float32), n=np.array(ln, np.float32),
                    st=np.array(st, np.int32), tot=np.array(tot, np.int32),
                    sway=np.clip(V[:, 2] / hgt, 0, 1).astype(np.float32), tris=int(sum(t - 2 for t in tot)),
                    height=hgt)


def _r(rng, a, b):
    return a + (b - a) * rng.random()


def sp_grass(rng, cell="grass", h=0.4, ncards=4):
    r = Rec()
    a0 = rng.random() * math.pi
    for k in range(ncards):
        s0 = rng.choice([0.0, 0.25, 0.5])
        hh = h * _r(rng, 0.8, 1.15)
        r.card(cell, (_r(rng, -0.08, 0.08), _r(rng, -0.08, 0.08), -0.02), a0 + k * math.pi / ncards + _r(rng, -0.25, 0.25),
               width=2 * hh * 0.5, height=hh, bend=_r(rng, 0.05, 0.3), s0=s0, s1=s0 + 0.5)
    return r


def sp_leymus(rng):
    r = Rec()
    a0 = rng.random() * math.pi
    for k in range(3):
        hh = _r(rng, 0.5, 0.65)
        s0 = rng.choice([0.0, 0.2, 0.4])
        r.card("leymus", (_r(rng, -0.1, 0.1), _r(rng, -0.1, 0.1), -0.02), a0 + k * math.pi / 3 + _r(rng, -0.2, 0.2),
               width=2 * hh * 0.6, height=hh, bend=_r(rng, 0.1, 0.35), s0=s0, s1=s0 + 0.6)
    return r


def sp_sasa(rng):
    r = Rec()
    for c in range(6):
        a = rng.random() * 2 * math.pi
        rad = 0.5 * math.sqrt(rng.random())
        base = Vector((rad * math.cos(a), rad * math.sin(a), -0.03))
        top = base + Vector((_r(rng, -0.12, 0.12), _r(rng, -0.12, 0.12), _r(rng, 0.55, 1.05)))
        r.stem([base, top], 0.006, sides=3)
        a1 = rng.random() * 2 * math.pi
        for k in range(6):
            th = a1 + k * 2.39996 + _r(rng, -0.3, 0.3)
            el = _r(rng, 0.0, 0.6)
            d = (math.cos(th) * math.cos(el), math.sin(th) * math.cos(el), math.sin(el))
            L = _r(rng, 0.23, 0.3)
            at = base + (top - base) * _r(rng, 0.55, 1.0)
            r.leaf("sasa_leaf", at, d, L, L * 0.5, droop=0.0, segs=1)
    return r


def sp_heracleum(rng):
    r = Rec()
    H = _r(rng, 1.45, 2.0)
    lean = Vector((_r(rng, -0.12, 0.12), _r(rng, -0.12, 0.12), 0))
    pts = [Vector((0, 0, -0.05)), lean * 0.2 + Z * H * 0.35, lean * 0.6 + Z * H * 0.7, lean + Z * H]
    r.stem(pts, 0.02, sides=5)
    r.umbel(pts[-1], _r(rng, 0.22, 0.28), dome=0.3)
    for s in (-1, 1):                                  # side branches with smaller umbels
        a = rng.random() * 2 * math.pi
        b0 = pts[2]
        b1 = b0 + Vector((0.28 * math.cos(a), 0.28 * math.sin(a), 0.2))
        r.stem([b0, b1], 0.01, sides=3)
        r.umbel(b1, _r(rng, 0.11, 0.15), dome=0.3)
    a0 = rng.random() * 2 * math.pi
    for k in range(4):                                 # big basal leaves
        th = a0 + k * math.pi / 2 + _r(rng, -0.3, 0.3)
        el = _r(rng, 0.35, 0.7)
        L = _r(rng, 0.55, 0.72)
        r.leaf("hera_leaf", (0.05 * math.cos(th), 0.05 * math.sin(th), _r(rng, 0.02, 0.2)),
               (math.cos(th) * math.cos(el), math.sin(th) * math.cos(el), math.sin(el)), L, L,
               droop=_r(rng, 0.45, 0.7), segs=2, fold=0.08)
    th = rng.random() * 2 * math.pi
    r.leaf("hera_leaf", pts[1], (math.cos(th), math.sin(th), 0.5), 0.38, 0.38, droop=0.4, segs=2)
    return r


def sp_hemerocallis(rng):
    r = Rec()
    a0 = rng.random() * math.pi
    for k in range(2):
        hh = _r(rng, 0.5, 0.62)
        r.card("strap", (0, 0, -0.02), a0 + k * math.pi / 2, width=hh, height=hh, bend=0.1)
    for k in range(3):
        th = rng.random() * 2 * math.pi
        top = Vector((0.12 * math.cos(th), 0.12 * math.sin(th), _r(rng, 0.7, 0.92)))
        r.stem([(0, 0, 0), top], 0.006, sides=3)
        for j in range(rng.integers(2, 4)):
            ph = th + _r(rng, -1.2, 1.2)
            out = Vector((math.cos(ph), math.sin(ph), 0))
            c = top + out * 0.05 + Z * _r(rng, -0.08, 0.03)
            r.quad("lily", c, out * 0.8 + Z * 0.6, Z, _r(rng, 0.1, 0.13))
    return r


def sp_rosa(rng):
    r = Rec()
    rx, hz = _r(rng, 0.5, 0.7), _r(rng, 0.6, 0.85)
    for k in range(4):
        th = rng.random() * 2 * math.pi
        r.stem([(0, 0, 0), (rx * 0.5 * math.cos(th), rx * 0.5 * math.sin(th), hz * 0.85)], 0.008, sides=3)
    for k in range(44):
        th = rng.random() * 2 * math.pi
        ph = _r(rng, 0.25, 1.45)
        rad = Vector((math.cos(th), math.sin(th), 0))
        p = Vector((rx * math.sin(ph) * math.cos(th), rx * math.sin(ph) * math.sin(th), hz * math.cos(ph)))
        p *= _r(rng, 0.75, 1.0)
        d = (rad * 0.55 + Z * 0.75 + Vector((_r(rng, -0.3, 0.3), _r(rng, -0.3, 0.3), 0))).normalized()
        L = _r(rng, 0.22, 0.3)
        r.leaf("rose_leaf", p - d * L * 0.4, d, L, L, droop=0.0)
    for k in range(6):
        th = rng.random() * 2 * math.pi
        ph = _r(rng, 0.35, 1.2)
        rad = Vector((math.sin(ph) * math.cos(th), math.sin(ph) * math.sin(th), math.cos(ph)))
        p = Vector((rx * rad.x, rx * rad.y, hz * rad.z)) * 1.02
        r.quad("rose", p, rad * 0.6 + Z * 0.5, Z, _r(rng, 0.09, 0.12))
    return r


def sp_geranium(rng):
    r = Rec()
    for k in range(7):
        th = rng.random() * 2 * math.pi
        rad = _r(rng, 0.04, 0.18)
        c = Vector((rad * math.cos(th), rad * math.sin(th), _r(rng, 0.12, 0.32)))
        nrm = Vector((math.cos(th) * 0.3, math.sin(th) * 0.3, 1))
        r.quad("ger_leaf", c, nrm, Vector((math.cos(th), math.sin(th), 0)), _r(rng, 0.13, 0.17))
    for k in range(3):
        th = rng.random() * 2 * math.pi
        top = Vector((0.15 * math.cos(th), 0.15 * math.sin(th), _r(rng, 0.38, 0.55)))
        r.stem([(0, 0, 0.05), top], 0.004, sides=3)
        for j in range(2):
            ph = th + _r(rng, -1.0, 1.0)
            out = Vector((math.cos(ph), math.sin(ph), 0))
            r.quad("geranium", top + out * 0.03 + Z * 0.01 * j, out * 0.5 + Z * 0.8, out, _r(rng, 0.055, 0.07))
    return r


def sp_spruce(rng):
    """Ezo spruce: a tapered trunk inside stacked cone tiers, ragged needle edges from the atlas."""
    r = Rec()
    H = _r(rng, 14.0, 19.0)
    r.stem([(0, 0, -0.4), (0, 0, H * 0.96)], 0.28, sides=4, r1=0.04, cell="spruce_bark", seg_len=4.5)
    rot = rng.random() * 2 * math.pi
    tiers = 7
    for i in range(tiers):
        f = i / tiers
        zb = H * (0.1 + 0.78 * f)
        R0 = 0.24 * H * (1 - f) ** 0.85 + 0.45
        ht = H * 0.2 * (1 - 0.45 * f) + 0.8
        sides = 8 if i < 4 else 6
        jit = [_r(rng, -0.12, 0.12) for _ in range(sides)]
        r.skirt("spruce_tier", zb, R0, zb + ht, R0 * 0.12, sides=sides, rot=rot + i * 0.7, jitter=jit)
    r.skirt("spruce_tier", H * 0.86, 0.55, H * 1.03, 0.02, sides=5, rot=rot)
    r.trunk = 0.3
    return r


def sp_birch(rng):
    """Erman's birch: a leaning, twisting pale trunk, three limbs, leaf-cluster cards."""
    r = Rec()
    H = _r(rng, 8.0, 12.0)
    th = rng.random() * 2 * math.pi
    lean = Vector((math.cos(th), math.sin(th), 0)) * _r(rng, 0.4, 1.2)
    side = Vector((-lean.y, lean.x, 0)) * 0.4
    pts = [Vector((0, 0, -0.3)), lean * 0.15 + Z * (H * 0.25), lean * 0.5 + side + Z * (H * 0.5),
           lean * 0.8 - side * 0.5 + Z * (H * 0.74)]
    r.stem(pts, 0.17, sides=6, r1=0.07, cell="birch_bark", seg_len=2.6)
    ends = [pts[-1] + Z * (H * 0.08)]
    for k in range(3):
        a = th + k * 2.1 + _r(rng, -0.4, 0.4)
        out = Vector((math.cos(a), math.sin(a), 0))
        s0 = pts[2] + (pts[3] - pts[2]) * _r(rng, 0.0, 0.6)
        mid = s0 + out * (H * 0.14) + Z * (H * 0.08)
        end = mid + out * (H * 0.14) + Z * (H * 0.1)
        r.stem([s0, mid, end], 0.07, sides=4, r1=0.025, cell="birch_bark", seg_len=2.6)
        ends.append(end)
    centre = lean * 0.7 + Z * (H * 0.72)
    cl = list(ends)
    for k in range(11):
        a = rng.random() * 2 * math.pi
        rr = math.sqrt(rng.random())
        cl.append(centre + Vector((0.28 * H * rr * math.cos(a), 0.28 * H * rr * math.sin(a),
                                   0.2 * H * _r(rng, -0.8, 1.0))))
    for c in cl:
        sz = _r(rng, 2.4, 3.3)
        a = rng.random() * math.pi
        n1 = Vector((math.cos(a), math.sin(a), 0.25))
        n2 = Vector((-math.sin(a), math.cos(a), 0.25))
        r.quad("birch_leaves", c, n1, Z, sz)
        r.quad("birch_leaves", c, n2, Z, sz)
        r.quad("birch_leaves", c + Z * 0.2, Vector((0.15, 0.1, 1.0)), Vector((math.cos(a), math.sin(a), 0)), sz * 0.9)
    r.trunk = 0.2
    return r


BUILDERS = {
    "Grass_Meadow": lambda rng: sp_grass(rng, "grass", 0.42, 3),
    "Grass_Flowers": lambda rng: sp_grass(rng, "grass_b", 0.36, 3),
    "Leymus_Mollis": sp_leymus,
    "Sasa_Kurilensis": sp_sasa,
    "Heracleum_Lanatum": sp_heracleum,
    "Hemerocallis_Esculenta": sp_hemerocallis,
    "Rosa_Rugosa": sp_rosa,
    "Geranium_Erianthum": sp_geranium,
    "Picea_Jezoensis": sp_spruce,
    "Betula_Ermanii": sp_birch,
}


def build_records():
    recs = {}
    for i, (key, (group, _, _)) in enumerate(SPECIES.items()):
        recs[key] = []
        for v in range(FLORA["variants"]):
            b = BUILDERS[key](np.random.default_rng(FLORA["seed"] * 100 + i * 10 + v))
            rec = b.finish(GROUPS[group]["up"])
            rec["trunk"] = getattr(b, "trunk", 0.0)
            recs[key].append(rec)
    return recs


# --------------------------------------------------------------------------------------
# Records -> Blender mesh (custom normals, UVs, "Wind" colour attribute)
# --------------------------------------------------------------------------------------
def mesh_from_instances(name, inst, mat):
    """inst: [(record, 4x4 numpy matrix, phase)] -> one mesh."""
    V, LV, UV, N, ST, TOT, COL = [], [], [], [], [], [], []
    nv = nl = 0
    for rec, M, phase in inst:
        R3 = M[:3, :3]
        s = np.cbrt(abs(np.linalg.det(R3))) or 1.0
        V.append(rec["v"] @ R3.T + M[:3, 3])
        LV.append(rec["lv"] + nv)
        UV.append(rec["uv"])
        N.append(rec["n"] @ (R3 / s).T)
        ST.append(rec["st"] + nl)
        TOT.append(rec["tot"])
        c = np.zeros((len(rec["v"]), 4), np.float32)
        c[:, 0] = rec["sway"]
        c[:, 1] = phase
        c[:, 3] = 1.0
        COL.append(c)
        nv += len(rec["v"])
        nl += len(rec["lv"])
    V, LV, UV, N = np.concatenate(V), np.concatenate(LV), np.concatenate(UV), np.concatenate(N)
    ST, TOT, COL = np.concatenate(ST), np.concatenate(TOT), np.concatenate(COL)
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(V))
    me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(len(LV))
    me.loops.foreach_set("vertex_index", LV)
    me.polygons.add(len(ST))
    me.polygons.foreach_set("loop_start", ST)
    me.polygons.foreach_set("loop_total", TOT)
    me.update(calc_edges=True)
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", UV.ravel())
    ca = me.color_attributes.new("Wind", "FLOAT_COLOR", "POINT")
    ca.data.foreach_set("color", COL.ravel())
    me.color_attributes.active_color = ca
    me.materials.append(mat)
    me.polygons.foreach_set("use_smooth", np.ones(len(ST), bool))
    me.normals_split_custom_set(N.astype(np.float32).reshape(-1, 3).tolist())
    me.validate(clean_customdata=False)
    me["tiled"] = True
    return me


def _mtx(x, y, z, rot, s):
    c, sn = math.cos(rot), math.sin(rot)
    return np.array([[c * s, -sn * s, 0, x], [sn * s, c * s, 0, y], [0, 0, s, z], [0, 0, 0, 1]], np.float32)


# --------------------------------------------------------------------------------------
# Scatter rules
# --------------------------------------------------------------------------------------
class Blockers:
    """Footprints of the placed village objects (oriented boxes from their local bounds)."""

    def __init__(self, placed):
        self.items, self.cell, self.grid = [], 8.0, {}
        cache = {}
        for ob in placed:
            if ob.type != "MESH" or ob.name.startswith("Fence"):
                continue
            me = ob.data
            if me.name not in cache:
                co = np.empty(len(me.vertices) * 3, np.float32)
                me.vertices.foreach_get("co", co)
                co = co.reshape(-1, 3)
                cache[me.name] = (co.min(0), co.max(0))
            lo, hi = cache[me.name]
            a, s = ob.rotation_euler.z, ob.scale.x
            item = (ob.location.x, ob.location.y, math.cos(a), math.sin(a), s, lo, hi)
            idx = len(self.items)
            self.items.append(item)
            r = s * float(np.max(np.abs(np.concatenate([lo[:2], hi[:2]])))) + 2.0
            for gx in range(int((ob.location.x - r) // self.cell), int((ob.location.x + r) // self.cell) + 1):
                for gy in range(int((ob.location.y - r) // self.cell), int((ob.location.y + r) // self.cell) + 1):
                    self.grid.setdefault((gx, gy), []).append(idx)

    def blocked(self, x, y, margin):
        for i in self.grid.get((int(x // self.cell), int(y // self.cell)), ()):
            ox, oy, c, s, sc, lo, hi = self.items[i]
            dx, dy = x - ox, y - oy
            lx, ly = (dx * c + dy * s) / sc, (-dx * s + dy * c) / sc
            m = margin / sc
            if lo[0] - m <= lx <= hi[0] + m and lo[1] - m <= ly <= hi[1] + m:
                return True
        return False


def _seg_dist(x, y, pts):
    best = 1e9
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - x0 - dx * t, y - y0 - dy * t))
    return best


def suitability(key, land, x, y, z, w, fence_d, village_d):
    """Probability (0..1) that `key` grows at a candidate point. w = splat (sand, earth, grass, rock)."""
    n = land.n
    sand, earth, grass, rock = w
    d = x - land.shore_x(y)
    if z < 0.25 or rock > 0.5:
        return 0.0
    patch = lambda sx, sy, k: smooth(n.fbm(x / sx + k * 13.1, y / sy - k * 7.7, 3) * 2.2)
    far = 1.0 - smooth((village_d - 50.0) / 25.0)
    if key == "Grass_Meadow":
        return (0.85 * grass * (0.45 + 0.55 * patch(5, 5, 9)) + 0.3 * earth * patch(6, 6, 1) * (1 - sand)) * far
    if key == "Grass_Flowers":
        return 0.7 * grass * patch(9, 9, 2) * far
    if key == "Leymus_Mollis":
        dune = smooth((d - 5.0) / 3.0) * (1 - smooth((d - 19.0) / 4.0))
        return 0.85 * dune * (0.35 + 0.65 * patch(7, 5, 3)) * (sand + 0.5 * grass)
    if key == "Sasa_Kurilensis":
        edge = smooth((village_d - 30.0) / 14.0)
        return 0.9 * grass * edge * smooth((n.fbm(x / 16 + 4, y / 16 - 3, 3) - 0.22) * 4)
    if key == "Heracleum_Lanatum":
        rim = max(0.0, 1.0 - abs(earth - 0.35) / 0.3)
        near_fence = math.exp(-(fence_d / 2.2) ** 2)
        return (0.1 + 0.45 * rim) * grass * far + 0.9 * near_fence * (grass + 0.6 * sand + 0.3)
    if key == "Hemerocallis_Esculenta":
        coast = smooth((d - 13.0) / 4.0) * (1 - smooth((d - 70.0) / 20.0))
        return 0.75 * grass * coast * smooth((n.fbm(x / 8 + 40, y / 8, 3) - 0.3) * 4)
    if key == "Rosa_Rugosa":
        edge = smooth((d - 9.0) / 3.0) * (1 - smooth((d - 24.0) / 4.0))
        return 0.7 * edge * (grass + 0.6 * sand) * smooth((n.fbm(x / 10 - 20, y / 10, 3) + 0.05) * 4)
    if key == "Picea_Jezoensis":
        if village_d < 55 or d < 25 or rock > 0.35:
            return 0.0
        forest = smooth((n.fbm(x / 70 + 11, y / 70 - 4, 3) + 0.05) * 3)
        return 0.6 * (grass + 0.4 * earth) * forest * (1 - smooth((z - 160) / 80)) * smooth((village_d - 55) / 45)
    if key == "Betula_Ermanii":
        if village_d < 50 or d < 30 or rock > 0.6:
            return 0.0
        stand = smooth((n.fbm(x / 55 - 30, y / 55 + 8, 3) + 0.1) * 3)
        return 0.55 * (grass + 0.5 * rock) * stand * smooth((z - 4) / 30) * smooth((village_d - 50) / 30)
    if key == "Geranium_Erianthum":
        return 0.6 * grass * far * smooth((n.fbm(x / 7 + 70, y / 7 - 9, 3) - 0.3) * 4)
    return 0.0


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def populate(recs, mats, land, hn, placed, parent, fence=(), camera=(0.0, -30.0), village_c=(4.0, -4.0)):
    """Scatter every species and merge the instances into 16 m chunks per group."""
    blk = Blockers(placed)
    chunks = {}
    counts = {k: 0 for k in SPECIES}
    for si, (key, (group, spacing, (s_lo, s_hi))) in enumerate(SPECIES.items()):
        x0, x1, y0, y1 = GROUPS[group]["region"]
        csize = GROUPS[group]["chunk"]
        margin = {"grass": 0.05, "plants": 0.45, "trees": 2.0}[group]
        nx, ny = int((x1 - x0) / spacing), int((y1 - y0) / spacing)
        for j in range(ny):
            for i in range(nx):
                hx, hy = hn(i, j, si, 1), hn(i, j, si, 2)
                x = x0 + (i + 0.5 + 0.45 * hx) * spacing
                y = y0 + (j + 0.5 + 0.45 * hy) * spacing
                if (0.5 + 0.5 * hn(i, j, si, 3)) > 0.999:
                    continue
                cam_d = math.hypot(x - camera[0], y - camera[1])
                if cam_d < {"grass": 1.5, "plants": 3.5, "trees": 8.0}[group]:
                    continue
                z = land.height(x, y)
                gx = (land.height(x + 0.5, y) - land.height(x - 0.5, y))
                gy = (land.height(x, y + 0.5) - land.height(x, y - 0.5))
                nz = 1.0 / math.sqrt(1.0 + gx * gx + gy * gy)
                w = land.splat(x, y, z, nz)
                p = suitability(key, land, x, y, z, w, _seg_dist(x, y, fence) if fence else 99.0,
                                math.hypot(x - village_c[0], y - village_c[1]))
                if p <= 0.0 or (0.5 + 0.5 * hn(i, j, si, 4)) > p:
                    continue
                if blk.blocked(x, y, margin):
                    continue
                if group == "plants" and fence and _seg_dist(x, y, fence) < 0.35:
                    continue
                var = int((0.5 + 0.5 * hn(i, j, si, 5)) * FLORA["variants"]) % FLORA["variants"]
                s = s_lo + (s_hi - s_lo) * (0.5 + 0.5 * hn(i, j, si, 6))
                rot = math.pi * hn(i, j, si, 7)
                cx, cy = int(math.floor(x / csize)), int(math.floor(y / csize))
                chunks.setdefault((group, cx, cy), []).append(
                    (recs[key][var], (x, y, z - 0.02, rot, s), 0.5 + 0.5 * hn(i, j, si, 8)))
                counts[key] += 1
    col = bpy.data.collections.get(parent.name + "_Flora") or bpy.data.collections.new(parent.name + "_Flora")
    if col.name not in parent.children:
        parent.children.link(col)
    objs = []
    for (group, cx, cy), lst in sorted(chunks.items()):
        csize = GROUPS[group]["chunk"]
        ox, oy = (cx + 0.5) * csize, (cy + 0.5) * csize
        oz = land.height(ox, oy)
        inst = [(rec, _mtx(x - ox, y - oy, z - oz, rot, s), ph) for rec, (x, y, z, rot, s), ph in lst]
        name = "Flora_%s_%02d_%02d" % (group.capitalize(), cx + 50, cy + 50)
        me = mesh_from_instances("SM_AV_" + name, inst, mats[group])
        ob = bpy.data.objects.new(name, me)
        ob.location = (ox, oy, oz)
        ob["godot_vis_range"] = GROUPS[group]["vis"]
        ob["flora_group"] = group
        col.objects.link(ob)
        objs.append(ob)
        trunks = [(x - ox, y - oy, z - oz, rec["trunk"] * s) for rec, (x, y, z, rot, s), ph in lst if rec["trunk"]]
        if trunks:                                    # one box per trunk, trimesh body in Godot
            V, F = [], []
            for tx, ty, tz, tr in trunks:
                i0 = len(V)
                V += [(tx + sx * tr, ty + sy * tr, tz + sz) for sz in (-0.4, 3.5) for sx, sy in
                      ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                F += [(i0, i0 + 3, i0 + 2, i0 + 1), (i0 + 4, i0 + 5, i0 + 6, i0 + 7)]
                F += [(i0 + k, i0 + (k + 1) % 4, i0 + 4 + (k + 1) % 4, i0 + 4 + k) for k in range(4)]
            cme = bpy.data.meshes.new("SM_AV_" + name + "_COL")
            cme.from_pydata(V, [], F)
            cob = bpy.data.objects.new(name + "_COL-colonly", cme)
            cob.parent = ob
            cob.display_type = "WIRE"
            cob.hide_render = True
            col.objects.link(cob)
            objs.append(cob)
    tri = {k: recs[k][0]["tris"] for k in SPECIES}
    print("\n%-24s %6s %8s %10s" % ("flora species", "tris", "count", "tris total"))
    total = 0
    for k in SPECIES:
        total += tri[k] * counts[k]
        print("%-24s %6d %8d %10d" % (k, tri[k], counts[k], tri[k] * counts[k]))
    print("flora: %d plants and trees, ~%d tris in %d chunks (Godot draws grass to %.0f m, plants to %.0f m, "
          "trees to %.0f m)\n" % (sum(counts.values()), total, len([o for o in objs if not o.name.endswith("-colonly")]),
                                 GROUPS["grass"]["vis"], GROUPS["plants"]["vis"], GROUPS["trees"]["vis"]))
    return objs, counts, total


def species_meshes(recs, mats):
    """One mesh per species (variant 0), pivot at the root: the prefabs."""
    out = {}
    for key, (group, _, _) in SPECIES.items():
        name = "SM_AV_Flora_" + key
        old = bpy.data.meshes.get(name)
        if old and old.users == 0:
            bpy.data.meshes.remove(old)
        out[key] = mesh_from_instances(name, [(recs[key][0], np.eye(4, dtype=np.float32), 0.5)], mats[group])
    return out


def build(gen, land, placed, parent, tex_dir, res=2048, force=False, camera=(0.0, -30.0), fence=()):
    """Called by ainu_village_scene.py. Returns textures, materials, prefab meshes, chunk objects."""
    for c in list(bpy.data.collections):
        if c.name == parent.name + "_Flora":
            for ob in list(c.objects):
                bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        if me.users == 0 and (me.name.startswith("SM_AV_Flora") or me.name.startswith("SM_AV_Flora_Trees")):
            bpy.data.meshes.remove(me)
    tex = make_textures(tex_dir, res, force)
    mats = make_materials(tex)
    recs = build_records()
    species = species_meshes(recs, mats)
    objs, counts, total = populate(recs, mats, land, gen["hn"], placed, parent, fence=fence, camera=camera)
    return dict(textures=tex, materials=mats, species=species, chunks=objs, counts=counts, tris=total)


# --------------------------------------------------------------------------------------
# Godot shader (written by ainu_village_scene.py next to the other shaders)
# --------------------------------------------------------------------------------------
GD_FOLIAGE = r"""// Foliage for ainu_village.gltf (generated by ainu_flora.py): alpha clip, wind, backlight.
// COLOR.r = sway (0 at the root, 1 at the tip), COLOR.g = random phase.
shader_type spatial;
render_mode cull_disabled, depth_draw_opaque, diffuse_lambert_wrap, specular_schlick_ggx;

uniform sampler2D albedo_tex : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D normal_tex : hint_normal, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D orm_tex : hint_default_white, filter_linear_mipmap_anisotropic, repeat_disable;
uniform float alpha_cut : hint_range(0.0, 1.0) = 0.45;
uniform bool keep_up_normals = false;          // grass cards: light both sides like the ground
uniform float wind_strength = 0.08;
uniform float wind_speed = 1.3;
uniform vec2 wind_dir = vec2(1.0, 0.35);
uniform float tint_variation = 0.12;
uniform vec4 backlight : source_color = vec4(0.32, 0.38, 0.16, 1.0);
uniform float normal_strength = 1.0;

varying float v_tint;

void vertex() {
	float sway = COLOR.r * COLOR.r;
	float ph = COLOR.g * 6.2831;
	vec3 wp = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	float t = TIME * wind_speed + ph + dot(wp.xz, vec2(0.13, 0.07));
	float gust = sin(t) * 0.55 + sin(t * 2.3 + 1.7) * 0.25 + sin(t * 0.31 + wp.x * 0.02) * 0.5;
	vec3 offs = vec3(normalize(wind_dir).x, 0.0, normalize(wind_dir).y) * gust * wind_strength * sway;
	offs.y = -abs(gust) * wind_strength * 0.25 * sway;
	VERTEX += transpose(mat3(MODEL_MATRIX)) * offs;
	v_tint = mix(1.0 - tint_variation, 1.0 + tint_variation * 0.5, COLOR.g);
}

void fragment() {
	vec4 a = texture(albedo_tex, UV);
	ALBEDO = a.rgb * v_tint;
	ALPHA = a.a;
	ALPHA_SCISSOR_THRESHOLD = alpha_cut;
	vec3 orm = texture(orm_tex, UV).rgb;
	AO = orm.r;
	AO_LIGHT_AFFECT = 0.4;
	ROUGHNESS = orm.g;
	if (keep_up_normals && !FRONT_FACING) {
		NORMAL = -NORMAL;          // undo Godot's back-face flip: cards keep their up-facing normal
	}
	NORMAL_MAP = texture(normal_tex, UV).rgb;
	NORMAL_MAP_DEPTH = normal_strength;
	BACKLIGHT = backlight.rgb;
}
"""


def godot_material_tres(keep_up, wind):
    return "\n".join([
        '[gd_resource type="ShaderMaterial" load_steps=5 format=3]', "",
        '[ext_resource type="Shader" path="../shaders/foliage.gdshader" id="1_shader"]',
        '[ext_resource type="Texture2D" path="../textures/T_Foliage_BaseColor.png" id="2_tex"]',
        '[ext_resource type="Texture2D" path="../textures/T_Foliage_Normal.png" id="3_tex"]',
        '[ext_resource type="Texture2D" path="../textures/T_Foliage_ORM.png" id="4_tex"]', "",
        "[resource]", 'shader = ExtResource("1_shader")',
        'shader_parameter/albedo_tex = ExtResource("2_tex")',
        'shader_parameter/normal_tex = ExtResource("3_tex")',
        'shader_parameter/orm_tex = ExtResource("4_tex")',
        "shader_parameter/alpha_cut = %.2f" % FLORA["alpha_cut"],
        "shader_parameter/keep_up_normals = %s" % ("true" if keep_up else "false"),
        "shader_parameter/wind_strength = %.3f" % wind, ""])


# --------------------------------------------------------------------------------------
# Standalone: paint, model, line the species up, render / export
# --------------------------------------------------------------------------------------
class _FlatLand:
    """Stand-in land for the standalone preview: flat meadow."""

    class _N:
        def fbm(self, *a, **k):
            return 0.5

    n = _N()

    def height(self, x, y):
        return 0.0

    def splat(self, *a):
        return (0.0, 0.0, 1.0, 0.0)

    @staticmethod
    def shore_x(y):
        return -1000.0


def _standalone(opts):
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.abspath(opts.get("out") or os.path.join(here, "out", "flora"))
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    tex = make_textures(os.path.join(out, "textures"), int(opts.get("tex-res", 2048)), "force-textures" in opts)
    mats = make_materials(tex)
    recs = build_records()
    species = species_meshes(recs, mats)
    col = bpy.data.collections.new("Flora_Preview")
    bpy.context.scene.collection.children.link(col)
    obs = []
    small = [k for k in species if SPECIES[k][0] != "trees"]
    for i, (key, me) in enumerate(species.items()):
        ob = bpy.data.objects.new(key, me)
        if key in small:
            ob.location = (small.index(key) * 1.3 - 4.55, 0, 0)
        else:
            ob.location = ((len(obs) - len(small)) * 12.0 - 6.0, 40.0, 0)
        col.objects.link(ob)
        obs.append(ob)
        print("  %-24s %5d tris" % (key, recs[key][0]["tris"]))
    gme = bpy.data.meshes.new("Preview_Ground")
    gme.from_pydata([(-40, -4, 0), (40, -4, 0), (40, 60, 0), (-40, 60, 0)], [], [(0, 1, 2, 3)])
    gm = bpy.data.materials.new("Preview_Ground")
    gm.diffuse_color = (0.08, 0.07, 0.05, 1)
    if gm.node_tree:
        gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.09, 0.075, 0.05, 1)
    gme.materials.append(gm)
    col.objects.link(bpy.data.objects.new("Preview_Ground", gme))
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.5
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    col.objects.link(sun)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.location = (0, -9.5, 2.6)
    cam.rotation_euler = (math.radians(91), 0, 0)
    cam.data.lens = 26
    col.objects.link(cam)
    sc = bpy.context.scene
    sc.camera = cam
    w = bpy.data.worlds.new("W")
    w.color = (0.5, 0.62, 0.8)
    if w.node_tree:
        w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.68, 0.85, 1)
        w.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    sc.world = w
    if True:                                      # always export the species set
        os.makedirs(out, exist_ok=True)
        for o in sc.objects:
            o.select_set(o in obs)
        bpy.ops.export_scene.gltf(filepath=os.path.join(out, "flora_species.gltf"), export_format="GLTF_SEPARATE",
                                  export_texture_dir="textures", use_selection=True, export_vertex_color="ACTIVE",
                                  export_all_vertex_colors=False, export_image_format="AUTO")
        log("exported", os.path.join(out, "flora_species.gltf"))
    if "render" in opts:
        sc.render.engine = "CYCLES"
        sc.cycles.samples = int(opts.get("samples", 32))
        sc.cycles.use_denoising = True
        sc.render.resolution_x, sc.render.resolution_y = 1600, 700
        sc.render.filepath = os.path.abspath(opts["render"])
        bpy.ops.render.render(write_still=True)
        log("rendered", sc.render.filepath)
    if "save" in opts:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(opts["save"]), copy=True)


def _cli_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            if argv[i].startswith("--"):
                opts[argv[i][2:]] = True
            i += 1
    return opts


if __name__ == "__main__":
    _standalone(_cli_args())
