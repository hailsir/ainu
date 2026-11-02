"""
Ainu Chise (traditional Ainu thatched house) - procedural, game-ready Blender model
==================================================================================
Target: Blender 5.0 (tested on 5.0.1). Pure bpy + mathutils, no add-ons.

Run inside Blender:  Scripting workspace > Open > ainu_chise.py > Run Script
Run headless:
  blender --background --factory-startup --python ainu_chise.py -- \
      --render out/exterior.png --render-interior out/interior.png \
      --export out/ainu_chise.glb --save out/ainu_chise.blend

Everything is generated from CONFIG below. See AINU_CHISE_TEMPLATE.md for the layout,
conventions and how to adapt this script to another building.

Axes: Z up, metres, origin = floor centre. Long axis = X. Door on the -X gable end
(west), sacred window (rorun-puyar) on +X (east), side windows on -Y (south) and +Y (north).
"""

import math
import os
import sys

import bpy
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

# ----------------------------------------------------------------------------- config
CONFIG = {
    "name": "AinuChise",
    "seed": 11,
    "max_tris": 30000,            # budget for LOD0 (exterior + frame + props)

    # Footprint measured at the OUTER faces of the walls: 5.0 x 4.0 = 20 m2
    "length": 5.0,                # X
    "width": 4.0,                 # Y
    "wall_height": 2.3,           # inner-face height where the roof underside rests
    "wall_thickness": 0.22,       # reed / bamboo-grass bundle walls

    # Roof (hip roof with a small smoke gable under each end of the ridge)
    "pitch_side": 52.0,           # degrees, long slopes (+/-Y)
    "pitch_end": 54.0,            # degrees, hipped ends (+/-X)
    "eave_overhang": 0.40,        # past the long walls (end overhang is derived so the eave is level)
    "thatch_thickness": 0.35,     # vertical thickness of the thatch shell
    "gable_height": 1.10,         # height of the smoke gable (vent triangle)
    "gable_overhang": 0.30,       # upper roof projects past the smoke gable
    "corner_radius": 0.80,        # rounded hip corners at the eave (tapers toward the top)
    "inner_corner_factor": 0.35,  # underside corners are tighter so they meet the wall corners
    "tier_lip": 0.06,             # how far each thatch course steps out
    "door_clearance": 2.0,        # eave is notched over the door to at least this height

    # Openings. wall: S/N (long, +u = +X / -X), E/W (gable ends, +u = +Y / -Y).
    # offset = shift of the opening centre along +u from the wall centre.
    "openings": [
        {"wall": "W", "kind": "door",   "offset": 0.0, "width": 0.90, "height": 1.75, "sill": 0.0},
        {"wall": "E", "kind": "window", "offset": 0.0, "width": 0.70, "height": 0.60, "sill": 1.00},
        {"wall": "S", "kind": "window", "offset": 1.2, "width": 0.80, "height": 0.65, "sill": 0.95},
        {"wall": "N", "kind": "window", "offset": 1.0, "width": 0.80, "height": 0.65, "sill": 0.95},
    ],

    "hearth": {"center_x": 0.10, "half_x": 0.75, "half_y": 0.40, "depth": 0.10},
    "preview_scene": True,        # camera, sun, ground in a separate "Preview" collection
}

# Exterior detail per LOD. LOD0 is the hero mesh; LOD1/LOD2 are exterior-only.
LOD_QUALITY = [
    dict(tiers=6, nx=14, ny=10, nc=4, tiers_u=2, nx_u=8, poles=True,  sticks=True,  frames=True,  wall_cut=0.6, jitter=1.0),
    dict(tiers=3, nx=7,  ny=5,  nc=2, tiers_u=1, nx_u=3, poles=False, sticks=True,  frames=True,  wall_cut=0.0, jitter=1.0),
    dict(tiers=1, nx=3,  ny=2,  nc=1, tiers_u=1, nx_u=1, poles=False, sticks=False, frames=False, wall_cut=0.0, jitter=0.0),
]

# ----------------------------------------------------------------------------- materials
# pattern: streak (fibres along V), blotch (isotropic), weave, diamond, lattice.
# UVs are world-scale (1 UV unit = 1 m), V runs along fibres / up the wall / along poles.
MATERIALS = {
    "Thatch_Roof": dict(pattern="streak", su=22, sv=1.4, ns=3.0, bs=0.7, bw=0.35, rough=0.95, bump=0.5,
                        colors=["#4c443b", "#6c6256", "#8b8071", "#a69a86"]),
    "Thatch_Wall": dict(pattern="streak", su=34, sv=0.9, ns=3.0, bs=0.9, bw=0.30, rough=0.95, bump=0.5,
                        colors=["#5a4a38", "#7c6850", "#998466", "#b59f7c"]),
    "Wood_Pole":   dict(pattern="streak", su=10, sv=0.5, ns=4.0, bs=1.5, bw=0.40, rough=0.85, bump=0.3,
                        colors=["#3a3129", "#584c40", "#736657", "#8a7d6c"]),
    "Wood_Smoked": dict(pattern="streak", su=10, sv=0.5, ns=4.0, bs=1.5, bw=0.40, rough=0.60, bump=0.3,
                        colors=["#1b140f", "#2c2018", "#41301f", "#58432f"]),
    "Wood_Light":  dict(pattern="streak", su=10, sv=0.6, ns=4.0, bs=1.2, bw=0.30, rough=0.70, bump=0.2,
                        colors=["#9b8461", "#b59d76", "#cdb68d", "#e0cca3"]),
    "Gable_Vent":  dict(pattern="lattice", ku=7.0, kv=3.0, rough=0.95, bump=0.6,
                        stops=[(0.0, "#5b4c3c"), (0.09, "#0d0a08")]),
    "Earth_Floor": dict(pattern="blotch", ns=2.5, rough=0.95, bump=0.2,
                        colors=["#2c241d", "#3d3228", "#4e4132", "#5d4f3d"]),
    "Mat_Woven":   dict(pattern="weave", k=45.0, rough=0.90, bump=0.25,
                        colors=["#7d6c4a", "#9c8a63", "#b5a379", "#cbb98e"]),
    "Mat_Pattern": dict(pattern="diamond", k=3.0, rough=0.85, bump=0.15,
                        stops=[(0.0, "#2a1a12"), (0.22, "#c2ad82"), (0.34, "#2a1a12"), (0.46, "#c2ad82"),
                               (0.62, "#7b2a1b"), (0.74, "#c2ad82"), (0.88, "#2a1a12")]),
    "Ash":         dict(pattern="blotch", ns=5.0, rough=1.0, bump=0.2,
                        colors=["#4a4642", "#6b6661", "#8a857e", "#a6a199"]),
    "Embers":      dict(pattern="blotch", ns=7.0, rough=0.9, bump=0.1, emission=6.0,
                        colors=["#1a0602", "#5c1404", "#c2410c", "#ff8a2a"]),
    "Iron":        dict(pattern="blotch", ns=4.0, rough=0.55, bump=0.1, metallic=0.85,
                        colors=["#141312", "#1f1d1b", "#2b2826", "#3a3530"]),
    "Lacquer":     dict(pattern="blotch", ns=1.5, rough=0.25, bump=0.05,
                        colors=["#0b0706", "#160c09", "#4a100b", "#6b1a10"]),
}

# Preview cameras: name -> (location, target, lens_mm, exposure). CLI: --render-<name> path.png
PREVIEW_VIEWS = {
    "exterior": ((-10.5, -8.5, 3.4), (0.0, 0.0, 2.4), 32, 0.0),   # door (W) + south side
    "rear":     ((10.0, 8.0, 3.0), (0.0, 0.0, 2.3), 32, 0.0),     # sacred window (E) + north side
    "interior": ((-1.95, -1.3, 1.6), (1.2, 0.5, 1.7), 14, 0.6),   # from the doorway toward the hearth
}

Z = Vector((0.0, 0.0, 1.0))
_SEED = [0]


# ----------------------------------------------------------------------------- helpers
def hn(*args):
    """Deterministic hash noise in [-1, 1]."""
    s = _SEED[0] * 0.618
    for i, x in enumerate(args):
        s += x * (12.9898 + 7.233 * i)
    return (math.sin(s) * 43758.5453) % 1.0 * 2.0 - 1.0


def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def srgb(hexstr):
    h = hexstr.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)


def newell(ps):
    nx = ny = nz = 0.0
    for i in range(len(ps)):
        x1, y1, z1 = ps[i]
        x2, y2, z2 = ps[(i + 1) % len(ps)]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return Vector((nx, ny, nz))


def box_uv(ps):
    n = newell(ps)
    ax = max(range(3), key=lambda i: abs(n[i]))
    if ax == 2:
        return [(p[0], p[1]) for p in ps]
    if ax == 0:
        return [(p[1], p[2]) for p in ps]
    return [(p[0], p[2]) for p in ps]


class MeshBuilder:
    """Accumulates verts/faces/material/smooth/UV; turned into one object at the end."""

    def __init__(self):
        self.v, self.f, self.fm, self.fs, self.fuv, self.mats = [], [], [], [], [], []

    def mat(self, name):
        if name is None:
            return 0
        if name not in self.mats:
            self.mats.append(name)
        return self.mats.index(name)

    def vert(self, p):
        self.v.append((p[0], p[1], p[2]))
        return len(self.v) - 1

    def verts(self, ps):
        return [self.vert(p) for p in ps]

    def face(self, idx, mat, smooth=False, uv=None, facing=None):
        idx = list(idx)
        nrm = newell([self.v[i] for i in idx])
        if facing is not None and nrm.dot(Vector(facing)) < 0:
            idx.reverse()
            uv = uv[::-1] if uv else None
            nrm = -nrm
        if len(idx) > 4:  # n-gons -> tris (handles concave caps; engines need tris/quads for tangents)
            for tri in tessellate_polygon([[Vector(self.v[i]) for i in idx]]):
                t = list(tri)
                if newell([self.v[idx[k]] for k in t]).dot(nrm) < 0:
                    t.reverse()
                self.face([idx[k] for k in t], mat, smooth, [uv[k] for k in t] if uv else None)
            return
        self.f.append(idx)
        self.fm.append(self.mat(mat))
        self.fs.append(bool(smooth))
        self.fuv.append(uv)

    def poly(self, ps, mat, smooth=False, uv=None, facing=None):
        self.face(self.verts(ps), mat, smooth, uv, facing)

    def tris(self):
        return sum(len(f) - 2 for f in self.f)


def loft(b, rows, mat, closed=True, flip=False, smooth=True, uvf=None):
    """Quads between consecutive rows of vertex indices. Default UV: (arc length, z)."""
    uvf = uvf or (lambda p, a: (a, p[2]))
    n = len(rows[0])
    segs = n if closed else n - 1
    arcs = []
    for row in rows:
        acc = [0.0]
        for j in range(1, segs + 1):
            acc.append(acc[-1] + (Vector(b.v[row[j % n]]) - Vector(b.v[row[j - 1]])).length)
        arcs.append(acc)
    for r in range(len(rows) - 1):
        A, B = rows[r], rows[r + 1]
        for j in range(segs):
            j1 = (j + 1) % n
            idx = [A[j], A[j1], B[j1], B[j]]
            uv = [uvf(b.v[A[j]], arcs[r][j]), uvf(b.v[A[j1]], arcs[r][j + 1]),
                  uvf(b.v[B[j1]], arcs[r + 1][j + 1]), uvf(b.v[B[j]], arcs[r + 1][j])]
            if flip:
                idx.reverse()
                uv.reverse()
            sm = smooth[j] if isinstance(smooth, (list, tuple)) else smooth
            b.face(idx, mat, sm, uv)


def tube(b, pts, r, mat, sides=6, closed=False, caps=True, smooth=True):
    """Pole along a polyline. r may be (r_up, r_side) for an elliptical section."""
    P = [Vector(p) for p in pts]
    n = len(P)
    rn, rb = r if isinstance(r, tuple) else (r, r)
    rings, tans = [], []
    for i in range(n):
        if closed:
            t = P[(i + 1) % n] - P[i - 1]
        else:
            t = P[min(i + 1, n - 1)] - P[max(i - 1, 0)]
        t.normalize()
        tans.append(t)
        ref = Z if abs(t.z) < 0.95 else Vector((1.0, 0.0, 0.0))
        nv = (ref - t * ref.dot(t)).normalized()
        bv = t.cross(nv)
        rings.append(b.verts([P[i] + nv * (math.cos(a) * rn) + bv * (math.sin(a) * rb)
                              for a in (2 * math.pi * k / sides for k in range(sides))]))
    vs = [0.0]
    for i in range(1, n):
        vs.append(vs[-1] + (P[i] - P[i - 1]).length)
    if closed:
        vs.append(vs[-1] + (P[0] - P[-1]).length)
    circ = math.pi * (rn + rb)
    for i in range(n if closed else n - 1):
        A, B = rings[i], rings[(i + 1) % n]
        for k in range(sides):
            k1 = (k + 1) % sides
            u0, u1 = circ * k / sides, circ * (k + 1) / sides
            b.face([A[k], A[k1], B[k1], B[k]], mat, smooth,
                   [(u0, vs[i]), (u1, vs[i]), (u1, vs[i + 1]), (u0, vs[i + 1])])
    if caps and not closed:
        b.poly([b.v[i] for i in rings[0]], mat, facing=-tans[0])
        b.poly([b.v[i] for i in rings[-1]], mat, facing=tans[-1])


def obox(b, c, ax, ay, az, mat, smooth=False):
    """Oriented box from centre and three half-extent vectors."""
    c, ax, ay, az = Vector(c), Vector(ax), Vector(ay), Vector(az)
    for d, u, v in ((ax, ay, az), (ay, az, ax), (az, ax, ay)):
        for s in (1, -1):
            m = c + d * s
            b.poly([m - u - v, m + u - v, m + u + v, m - u + v], mat, smooth, facing=d * s)


def box(b, c, half, mat):
    obox(b, c, (half[0], 0, 0), (0, half[1], 0), (0, 0, half[2]), mat)


def lathe(b, prof, mat, center, segs=12, flip=False, smooth=True):
    """Revolve (radius, z) profile around Z. Radius 0 collapses to a pole vertex."""
    cx, cy, cz = center
    rings = []
    for r, z in prof:
        if r < 1e-6:
            rings.append([b.vert((cx, cy, cz + z))])
        else:
            rings.append(b.verts([(cx + r * math.cos(2 * math.pi * k / segs),
                                   cy + r * math.sin(2 * math.pi * k / segs), cz + z) for k in range(segs)]))
    for i in range(len(rings) - 1):
        A, B = rings[i], rings[i + 1]
        if len(A) == 1 and len(B) == 1:
            continue
        for k in range(segs):
            k1 = (k + 1) % segs
            if len(A) == 1:
                idx = [A[0], B[k1], B[k]]
            elif len(B) == 1:
                idx = [A[k], A[k1], B[0]]
            else:
                idx = [A[k], A[k1], B[k1], B[k]]
            if flip:
                idx.reverse()
            b.face(idx, mat, smooth)


def ring(X, Y, r, nx, ny, nc):
    """Rounded rectangle, CCW from the +X side. Returns (x, y, outward_normal)."""
    r = max(0.05, min(r, X - 0.02, Y - 0.02))
    pts = []

    def side(x0, y0, x1, y1, n, nrm):
        for i in range(n):
            t = i / n
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, nrm))

    def corner(cx, cy, a0):
        for i in range(nc):
            a = a0 + 0.5 * math.pi * i / nc
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a), (math.cos(a), math.sin(a))))

    side(X, -(Y - r), X, Y - r, ny, (1, 0))
    corner(X - r, Y - r, 0.0)
    side(X - r, Y, -(X - r), Y, nx, (0, 1))
    corner(-(X - r), Y - r, 0.5 * math.pi)
    side(-X, Y - r, -X, -(Y - r), ny, (-1, 0))
    corner(-(X - r), -(Y - r), math.pi)
    side(-(X - r), -Y, X - r, -Y, nx, (0, -1))
    corner(X - r, -(Y - r), 1.5 * math.pi)
    return pts


# ----------------------------------------------------------------------------- derived geometry
class Geo:
    """All derived dimensions. Roof planes: z_outer = zE + (L - |coord|) * tan(pitch)."""

    def __init__(s, c):
        s.c = c
        s.wt, s.zW = c["wall_thickness"], c["wall_height"]
        s.Wx, s.Wy = c["length"] / 2, c["width"] / 2
        s.Wxi, s.Wyi = s.Wx - s.wt, s.Wy - s.wt
        s.tanP = math.tan(math.radians(c["pitch_side"]))
        s.tanQ = math.tan(math.radians(c["pitch_end"]))
        s.t, s.lip = c["thatch_thickness"], c["tier_lip"]
        ov = c["eave_overhang"]
        # roof underside passes exactly through the top of the inner wall faces
        s.Ly = s.Wy + ov
        s.Lx = s.Wx + (ov + s.wt) * s.tanP / s.tanQ - s.wt
        s.zE = s.zW - (ov + s.wt) * s.tanP + s.t
        s.zR = s.zE + s.Ly * s.tanP
        s.zG = s.zR - c["gable_height"]
        s.yg = (s.zR - s.zG) / s.tanP
        s.Gx = s.Lx - (s.zG - s.zE) / s.tanQ
        s.xe = s.Gx + c["gable_overhang"]
        s.rc0 = c["corner_radius"]
        s.rc1 = min(0.25, s.rc0)
        s.inner_r = c["inner_corner_factor"]
        s.hout_side = s.zW - s.wt * s.tanP   # wall tops are bevelled parallel to the roof underside
        s.hout_end = s.zW - s.wt * s.tanQ
        if s.Gx < 0.4 or s.zG <= s.zE + 0.3:
            raise ValueError("Roof too steep/short for the footprint: lower gable_height or pitch_end")

        s.walls = {  # origin, +u dir, length, outward normal, outer top height, is_long
            "S": (Vector((-s.Wx, -s.Wy, 0)), Vector((1, 0, 0)), 2 * s.Wx, Vector((0, -1, 0)), s.hout_side, True),
            "N": (Vector((s.Wx, s.Wy, 0)), Vector((-1, 0, 0)), 2 * s.Wx, Vector((0, 1, 0)), s.hout_side, True),
            "E": (Vector((s.Wx, -s.Wyi, 0)), Vector((0, 1, 0)), 2 * s.Wyi, Vector((1, 0, 0)), s.hout_end, False),
            "W": (Vector((-s.Wx, s.Wyi, 0)), Vector((0, -1, 0)), 2 * s.Wyi, Vector((-1, 0, 0)), s.hout_end, False),
        }
        s.openings = []
        for spec in c["openings"]:
            o, ud, L, nout, hout, _ = s.walls[spec["wall"]]
            uc = L / 2 + spec.get("offset", 0.0)
            w, h, sill = spec["width"], spec["height"], spec.get("sill", 0.0)
            op = dict(spec, u0=uc - w / 2, u1=uc + w / 2, v0=sill, v1=sill + h, center=o + ud * uc, ud=ud, nout=nout)
            if op["u0"] < 0.25 or op["u1"] > L - 0.25:
                raise ValueError(f"Opening {spec} is too close to the wall end")
            if op["v1"] > hout - 0.12:
                raise ValueError(f"Opening {spec} too tall: wall outer top is {hout:.2f} m")
            s.openings.append(op)
        s.door = next((o for o in s.openings if o["kind"] == "door"), None)
        s.lift = 0.0
        if s.door:
            s.lift = max(0.0, c["door_clearance"] - (s.zE - s.t))
            s.lift_tan = s.tanQ if s.door["wall"] in "EW" else s.tanP

    def X(s, h):
        return s.Lx - (h - s.zE) / s.tanQ

    def Y(s, h):
        return s.Ly - (h - s.zE) / s.tanP

    def rc(s, h):
        f = max(0.0, min(1.0, (h - s.zE) / (s.zG - s.zE)))
        return s.rc0 + (s.rc1 - s.rc0) * f

    def roof_z(s, x, y):
        """Height of the outer (unstepped) hip-roof surface above (x, y); bisection on the ring SDF."""
        def sdf(h):
            X, Y = s.X(h), s.Y(h)
            r = max(0.05, min(s.rc(h), X - 0.02, Y - 0.02))
            qx, qy = abs(x) - (X - r), abs(y) - (Y - r)
            return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
        lo, hi = s.zE, s.zG
        if sdf(lo) > 0:
            return lo
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            lo, hi = (lo, mid) if sdf(mid) > 0 else (mid, hi)
        return lo

    def z_in_side(s, y):
        return s.zE + (s.Ly - abs(y)) * s.tanP - s.t

    def z_in_end(s, x):
        return s.zE + (s.Lx - abs(x)) * s.tanQ - s.t

    def door_lift(s, p, h, nrm):
        """Slide eave points up the roof plane over the door (eyebrow notch)."""
        if s.lift <= 0.0:
            return p
        d = s.door
        if Vector((nrm[0], nrm[1], 0)).dot(d["nout"]) < 0.7:
            return p
        along = abs((p - d["center"]).dot(d["ud"]))
        a = d["width"] / 2 + 0.25
        f = 1.0 if along < a else smoothstep(1.0 - (along - a) / 0.7)
        g = max(0.0, 1.0 - (h - s.zE) / (3.0 * s.lift))
        k = s.lift * f * g
        return p - d["nout"] * (k / s.lift_tan) + Z * k


# ----------------------------------------------------------------------------- exterior
def roof_ring(G, q, h, lip=0.0, dz=0.0, jit=0.0, jz=0.0, rid=0, inner=False, outward=False):
    """outward=True keeps jitter >= 0 so a course's lip never sinks into the course below."""
    r = G.rc(h) * (G.inner_r if inner else 1.0)
    out = []
    for j, (x, y, n) in enumerate(ring(G.X(h), G.Y(h), r, q["nx"], q["ny"], q["nc"])):
        k = hn(j, rid)
        o = lip + jit * q["jitter"] * (0.5 * (k + 1.0) if outward else k)
        p = Vector((x + n[0] * o, y + n[1] * o, h + dz + jz * q["jitter"] * hn(j, rid + 50) - (G.t if inner else 0.0)))
        out.append(G.door_lift(p, h, n))
    return out


def build_lower_roof(b, G, q):
    T, M = q["tiers"], "Thatch_Roof"
    H = [G.zE + (G.zG - G.zE) * i / T for i in range(T + 1)]
    Bs, Ts = [], []
    for i in range(T):
        top = i == T - 1
        B = b.verts(roof_ring(G, q, H[i], lip=G.lip if i else 0.0, dz=-0.04 if i else 0.0,
                              jit=0.03, jz=0.03 if i == 0 else 0.0, rid=i * 3, outward=True))
        Mid = b.verts(roof_ring(G, q, 0.5 * (H[i] + H[i + 1]), lip=G.lip * 0.55, jit=0.02, rid=i * 3 + 1))
        Tt = b.verts(roof_ring(G, q, H[i + 1], jit=0.0 if top else 0.01, rid=i * 3 + 2))
        loft(b, [B, Mid, Tt], M, smooth=True)
        if i:
            loft(b, [Ts[-1], B], M, smooth=False)       # underside of the stepped course
        Bs.append(B)
        Ts.append(Tt)
    I = [b.verts(roof_ring(G, q, h, inner=True)) for h in H]
    loft(b, I, M, flip=True, smooth=True)               # underside seen from inside
    loft(b, [I[0], Bs[0]], M, smooth=False)             # thick eave edge
    loft(b, [I[-1], Ts[-1]], M, flip=True, smooth=False)


def build_upper_roof(b, G, q):
    """Gabled top: closed thatch shell along X, stepped courses, capped ends."""
    Tu, M = q["tiers_u"], "Thatch_Roof"
    Hs = [G.zG + (G.zR - G.zG) * k / Tu for k in range(Tu + 1)]
    up, upf, upj = [], [], []   # +Y side, bottom -> apex; flag = smooth segment to next point
    for k in range(Tu):
        h0, h1 = Hs[k], Hs[k + 1]
        L = G.lip * (1.8 if k == 0 else 1.0)
        hm = 0.5 * (h0 + h1)
        up += [(G.Y(h0) + L, h0 - 0.04), (G.Y(hm) + L * 0.55, hm)]
        upf += [True, True]
        upj += [False, True]
        if k < Tu - 1:
            up.append((G.Y(h1), h1))
            upf.append(False)
            upj.append(True)
    P = up + [(0.0, G.zR)] + [(-y, z) for y, z in reversed(up)]
    J = upj + [False] + upj[::-1]
    S = upf + upf[::-1]
    inner = [(-G.yg, G.zG - G.t), (0.0, G.zR - G.t), (G.yg, G.zG - G.t)]
    sec = P + inner
    flags = S + [False, True, True, False]
    jits = J + [False, False, False]
    rows, slices = [], []
    n = q["nx_u"]
    for i in range(n + 1):
        x = -G.xe + 2 * G.xe * i / n
        pts = []
        for k, (y, z) in enumerate(sec):
            dy = 0.03 * q["jitter"] * hn(i, k, 9) if jits[k] else 0.0
            pts.append(Vector((x, y + math.copysign(dy, y), z)))
        slices.append(pts)
        rows.append(b.verts(pts))
    loft(b, rows, M, closed=True, smooth=flags, uvf=lambda p, a: (p[0], p[2]))
    b.poly(slices[0], M, facing=(-1, 0, 0))
    b.poly(slices[-1], M, facing=(1, 0, 0))
    # smoke-gable panels (two-sided so they read from inside too)
    yb = G.yg - G.t / G.tanP
    for s in (1, -1):
        for off, face in ((0.04, s), (0.07, -s)):
            x = s * (G.Gx - off)
            b.poly([(x, -yb, G.zG), (x, yb, G.zG), (x, 0.0, G.zR - G.t)], "Gable_Vent", facing=(face, 0, 0))


def build_ridge(b, G, q):
    xe = G.xe + 0.12
    tube(b, [(-xe + 2 * xe * i / 4, 0, G.zR - 0.03) for i in range(5)], (0.17, 0.24), "Thatch_Roof", sides=8)
    if not q["sticks"]:
        return
    zc = G.zR + 0.20
    n = max(2, int(2 * G.xe / 0.55))
    for i in range(n + 1):
        x = -G.xe + 0.1 + (2 * G.xe - 0.2) * i / n
        for s in (1, -1):
            p0 = Vector((x + 0.02 * s, s * 0.55, G.zR - 0.55 * G.tanP + 0.08))
            pc = Vector((x + 0.02 * s, 0.0, zc))
            tube(b, [p0, pc + (pc - p0).normalized() * 0.3], 0.025, "Wood_Pole", sides=4)
    tube(b, [(-G.xe - 0.05, 0, zc + 0.05), (G.xe + 0.05, 0, zc + 0.05)], 0.035, "Wood_Pole", sides=5)


def build_wall(b, G, key, q):
    o, ud, L, nout, hout, is_long = G.walls[key]
    hin, wt, M = G.zW, G.wt, "Thatch_Wall"
    wid = "SNEW".index(key)
    ops = [(p["u0"], p["u1"], p["v0"], p["v1"], p) for p in G.openings if p["wall"] == key]
    J = 0.025 * q["jitter"]

    def P(u, v, w):
        return o + ud * u + Z * v - nout * w

    def outer(u, v):
        j = 0.0 if (u < 1e-4 or u > L - 1e-4) else J * 0.5 * (hn(round(u * 97), round(v * 89), wid) + 1.0)
        return P(u, v, -j)

    edges = sorted({e for op in ops for e in op[:2]})
    us = {0.0, L} | set(edges)
    if q["wall_cut"]:
        n = max(1, int(L / q["wall_cut"]))
        us |= {L * i / n for i in range(1, n) if all(abs(L * i / n - e) > 0.08 for e in edges)}
    us = sorted(us)
    vedges = sorted({e for op in ops for e in op[2:4]})
    vs = {0.0} | set(vedges) | {hout * k / 3 for k in (1, 2) if all(abs(hout * k / 3 - e) > 0.08 for e in vedges)}
    vs = sorted(v for v in vs if v < hout - 1e-4) + [hout]
    top = len(vs) - 1

    def vtop(u, w):  # bevelled top, pulled down where the rounded hip corners dip
        p = P(u, 0.0, w)
        return min(hin if w > 0 else hout, G.roof_z(p.x, p.y) - 0.05)

    gO, gI = {}, {}

    def go(i, j):
        if (i, j) not in gO:
            gO[(i, j)] = b.vert(outer(us[i], vtop(us[i], 0.0) if j == top else vs[j]))
        return gO[(i, j)]

    def gi(i, j):
        if (i, j) not in gI:
            gI[(i, j)] = b.vert(P(us[i], vtop(us[i], wt) if j == top else vs[j], wt))
        return gI[(i, j)]

    for i in range(len(us) - 1):
        uc = 0.5 * (us[i] + us[i + 1])
        for j in range(top):
            vc = 0.5 * (vs[j] + vs[j + 1])
            if any(u0 < uc < u1 and v0 < vc < v1 for u0, u1, v0, v1, _ in ops):
                continue
            b.face([go(i, j), go(i + 1, j), go(i + 1, j + 1), go(i, j + 1)], M, True, facing=nout)
            b.face([gi(i, j), gi(i + 1, j), gi(i + 1, j + 1), gi(i, j + 1)], M, False, facing=-nout)
        b.face([go(i, top), go(i + 1, top), gi(i + 1, top), gi(i, top)], M, facing=Z)
    if is_long:  # end caps; the short walls tuck in between the long walls
        for u, f in ((0.0, -ud), (L, ud)):
            b.poly([outer(u, 0), outer(u, vtop(u, 0.0)), P(u, vtop(u, wt), wt), P(u, 0, wt)], M, facing=f)

    for u0, u1, v0, v1, op in ops:  # reveals
        b.poly([outer(u0, v1), outer(u1, v1), P(u1, v1, wt), P(u0, v1, wt)], M, facing=-Z)
        if v0 > 0:
            b.poly([outer(u0, v0), outer(u1, v0), P(u1, v0, wt), P(u0, v0, wt)], M, facing=Z)
        b.poly([outer(u0, v0), outer(u0, v1), P(u0, v1, wt), P(u0, v0, wt)], M, facing=ud)
        b.poly([outer(u1, v0), outer(u1, v1), P(u1, v1, wt), P(u1, v0, wt)], M, facing=-ud)
        if q["frames"]:  # pole frame + rolled reed blind above
            w = wt * 0.5
            vb = max(0.0, v0 - 0.05)
            tube(b, [P(u0 + 0.02, vb, w), P(u0 + 0.02, v1 + 0.05, w)], 0.045, "Wood_Pole", 6)
            tube(b, [P(u1 - 0.02, vb, w), P(u1 - 0.02, v1 + 0.05, w)], 0.045, "Wood_Pole", 6)
            tube(b, [P(u0 - 0.12, v1 - 0.02, w), P(u1 + 0.12, v1 - 0.02, w)], 0.045, "Wood_Pole", 6)
            if v0 > 0:
                tube(b, [P(u0 - 0.12, v0 + 0.02, w), P(u1 + 0.12, v0 + 0.02, w)], 0.045, "Wood_Pole", 6)
            tube(b, [P(u0 - 0.05, v1 + 0.10, -0.085), P(u1 + 0.05, v1 + 0.10, -0.085)], 0.05, "Mat_Woven", 8)

    if q["poles"]:  # horizontal binding poles on the outside of the reed walls
        for fi, frac in enumerate((0.12, 0.47, 0.82)):
            v = hout * frac
            blocks = sorted((u0 - 0.07, u1 + 0.07) for u0, u1, v0, v1, _ in ops if v0 - 0.07 < v < v1 + 0.07)
            cur, end, segs = (-0.03, L + 0.03, []) if is_long else (0.0, L, [])
            for a, c in blocks:
                if a > cur + 0.1:
                    segs.append((cur, a))
                cur = max(cur, c)
            if end > cur + 0.1:
                segs.append((cur, end))
            for si, (a, c) in enumerate(segs):
                n = max(1, math.ceil((c - a) / 0.9))
                pts = [P(a + (c - a) * k / n, v + 0.03 * hn(k, wid, fi, si), -0.05) for k in range(n + 1)]
                tube(b, pts, 0.022, "Wood_Pole", sides=5, caps=False)


def build_exterior(G, q):
    b = MeshBuilder()
    build_lower_roof(b, G, q)
    build_upper_roof(b, G, q)
    build_ridge(b, G, q)
    for key in "SNEW":
        build_wall(b, G, key, q)
    return b


# ----------------------------------------------------------------------------- interior frame
def build_frame(G):
    b, M = MeshBuilder(), "Wood_Smoked"
    rp, zp = 0.075, G.zW - 0.12        # wall plates
    # posts along the inner wall faces (skipping openings)
    spans = {k: [] for k in "SNEW"}
    for op in G.openings:
        c, half = op["center"], op["width"] / 2 + 0.15
        axis = 0 if op["wall"] in "SN" else 1
        spans[op["wall"]].append((c[axis] - half, c[axis] + half))
    posts = set()
    for sx in (1, -1):
        for sy in (1, -1):
            posts.add((sx * (G.Wxi - 0.045), sy * (G.Wyi - 0.045)))
    nx, ny = max(1, round(2 * G.Wxi / 1.3)), max(1, round(2 * G.Wyi / 1.3))
    for k in range(1, nx):
        x = -G.Wxi + 2 * G.Wxi * k / nx
        for sy, key in ((-1, "S"), (1, "N")):
            if not any(a < x < c for a, c in spans[key]):
                posts.add((x, sy * (G.Wyi - 0.045)))
    for k in range(1, ny):
        y = -G.Wyi + 2 * G.Wyi * k / ny
        for sx, key in ((1, "E"), (-1, "W")):
            if not any(a < y < c for a, c in spans[key]):
                posts.add((sx * (G.Wxi - 0.045), y))
    for x, y in sorted(posts):
        tube(b, [(x, y, 0.0), (x, y, zp - rp)], 0.07, M, sides=6)
    # wall plates
    for sy in (1, -1):
        tube(b, [(-G.Wxi, sy * (G.Wyi - 0.07), zp), (G.Wxi, sy * (G.Wyi - 0.07), zp)], rp, M, 6)
    for sx in (1, -1):
        tube(b, [(sx * (G.Wxi - 0.07), -G.Wyi, zp), (sx * (G.Wxi - 0.07), G.Wyi, zp)], rp, M, 6)
    # tie beams across the width; the outer two carry the A-frames
    tie_x = min(G.Wxi * 0.62, G.Gx - 0.05)
    for x in (-tie_x, 0.0, tie_x):
        tube(b, [(x, -G.Wyi, zp + 0.02), (x, G.Wyi, zp + 0.02)], 0.09, M, 8)
    # ridge pole on two A-frames
    z_rp = G.zR - G.t - 0.20
    tube(b, [(-(G.Gx - 0.05), 0, z_rp), (G.Gx - 0.05, 0, z_rp)], 0.09, M, 8)
    ax = tie_x - 0.03
    for sx in (1, -1):
        apex = Vector((sx * ax, 0.0, z_rp - 0.04))
        for sy in (1, -1):
            foot = Vector((sx * ax, sy * (G.Wyi - 0.15), zp + 0.1))
            tube(b, [foot, apex + (apex - foot).normalized() * 0.2], 0.065, M, 6)
    # rafters
    off = 0.14
    xs = [-(G.Gx - 0.3) + 2 * (G.Gx - 0.3) * k / 5 for k in range(6)]
    for x in xs:
        for s in (1, -1):
            y0, y1 = G.Wyi - 0.04, 0.06
            tube(b, [(x, s * y0, G.z_in_side(y0) - off), (x, s * y1, G.z_in_side(y1) - off)], 0.05, M, 6)
    for sx in (1, -1):
        for y in (-(G.yg - 0.35), 0.0, G.yg - 0.35):
            x0, x1 = G.Wxi - 0.04, G.Gx
            tube(b, [(sx * x0, y, G.z_in_end(x0) - off), (sx * x1, y, G.z_in_end(x1) - off)], 0.05, M, 6)
        tube(b, [(sx * (G.Gx - 0.1), -G.yg, G.zG - G.t - off), (sx * (G.Gx - 0.1), G.yg, G.zG - G.t - off)], 0.06, M, 6)
    # hip rafters follow the rounded underside corners
    c45 = math.cos(math.pi / 4)
    for sx in (1, -1):
        for sy in (1, -1):
            pts = []
            for k in range(25):
                h = G.zE + (G.zG - G.zE) * k / 24
                r = G.rc(h) * G.inner_r
                x, y = G.X(h) - r + r * c45, G.Y(h) - r + r * c45
                if x < G.Wxi - 0.05 and y < G.Wyi - 0.05:
                    pts.append((sx * x, sy * y, h - G.t - off))
            pts = pts[::3] if len(pts) > 6 else pts
            if len(pts) >= 2:
                tube(b, pts, 0.055, M, 6)
    # purlins: rings under the hip thatch, battens under the upper slopes
    rpu = 0.035
    for h in (G.zW + G.t + 0.35, 0.5 * (G.zW + G.t + 0.35 + G.zG - 0.15)):
        r = G.rc(h) * G.inner_r
        pts = [(x, y, h - G.t - rpu - 0.005) for x, y, _ in ring(G.X(h), G.Y(h), r, 2, 2, 2)]
        tube(b, pts, rpu, M, 5, closed=True)
    for s in (1, -1):
        y = s * G.yg * 0.45
        tube(b, [(-(G.Gx - 0.05), y, G.z_in_side(y) - rpu), (G.Gx - 0.05, y, G.z_in_side(y) - rpu)], rpu, M, 5)
    return b


# ----------------------------------------------------------------------------- props
def build_props(G):
    b = MeshBuilder()
    h = G.c["hearth"]
    hc, hx, hy, hd = h["center_x"], h["half_x"], h["half_y"], h["depth"]
    # earthen floor with the hearth pit cut out
    o = [(-G.Wxi, -G.Wyi), (G.Wxi, -G.Wyi), (G.Wxi, G.Wyi), (-G.Wxi, G.Wyi)]
    i = [(hc - hx, -hy), (hc + hx, -hy), (hc + hx, hy), (hc - hx, hy)]
    for k in range(4):
        k1 = (k + 1) % 4
        b.poly([(*o[k], 0), (*o[k1], 0), (*i[k1], 0), (*i[k], 0)], "Earth_Floor", facing=Z)
    for op in G.openings:  # threshold under the door
        if op["kind"] == "door":
            ow, ud, L, nout, *_ = G.walls[op["wall"]]
            P = lambda u, w: ow + ud * u - nout * w
            b.poly([P(op["u0"], 0), P(op["u1"], 0), P(op["u1"], G.wt), P(op["u0"], G.wt)], "Earth_Floor", facing=Z)
    # hearth pit, ash, embers, frame, firewood
    for k in range(4):
        k1 = (k + 1) % 4
        cx = Vector((hc, 0, 0))
        mid = Vector(((i[k][0] + i[k1][0]) / 2, (i[k][1] + i[k1][1]) / 2, 0))
        b.poly([(*i[k], 0), (*i[k1], 0), (*i[k1], -hd), (*i[k], -hd)], "Earth_Floor", facing=cx - mid)
    b.poly([(*p, -hd) for p in i], "Ash", facing=Z)
    b.poly([(hc + 0.22 * math.cos(a), 0.18 * math.sin(a), -hd + 0.012)
            for a in (2 * math.pi * k / 8 for k in range(8))], "Embers", facing=Z)
    box(b, (hc, hy + 0.06, 0.05), (hx + 0.12, 0.06, 0.05), "Wood_Smoked")
    box(b, (hc, -hy - 0.06, 0.05), (hx + 0.12, 0.06, 0.05), "Wood_Smoked")
    box(b, (hc + hx + 0.06, 0, 0.05), (0.06, hy, 0.05), "Wood_Smoked")
    box(b, (hc - hx - 0.06, 0, 0.05), (0.06, hy, 0.05), "Wood_Smoked")
    for ang, ln in ((0, 0.55), (165, 0.55), (195, 0.5), (85, 0.3)):
        a = math.radians(ang)
        d = Vector((math.cos(a), math.sin(a), 0))
        c = Vector((hc, 0, 0))
        tube(b, [c + d * 0.07 + Vector((0, 0, -hd + 0.07)), c + d * ln + Vector((0, 0, -hd + 0.035))],
             0.035, "Wood_Smoked", 6)
    # floor mats (kina) and patterned wall hangings
    t = 0.015
    yn0, yn1 = hy + 0.25, G.Wyi - 0.12
    for s in (1, -1):
        box(b, (-0.15, s * (yn0 + yn1) / 2, t / 2), (1.35, (yn1 - yn0) / 2, t / 2), "Mat_Woven")
    ex0, ex1 = hc + hx + 0.3, G.Wxi - 0.1
    box(b, ((ex0 + ex1) / 2, 0, t / 2), ((ex1 - ex0) / 2, 0.6, t / 2), "Mat_Woven")
    box(b, (0.25, G.Wyi - 0.012, 1.15), (0.7, 0.008, 0.55), "Mat_Pattern")
    box(b, (-0.6, -G.Wyi + 0.012, 1.15), (0.9, 0.008, 0.55), "Mat_Pattern")
    # drying rack (tuna) over the hearth, hung from the roof
    zr, rr = 1.85, 0.025
    for s in (1, -1):
        tube(b, [(hc - 0.95, s * 0.5, zr), (hc + 0.95, s * 0.5, zr)], rr, "Wood_Smoked", 5)
        tube(b, [(hc + s * 0.85, -0.6, zr + 0.05), (hc + s * 0.85, 0.6, zr + 0.05)], rr, "Wood_Smoked", 5)
        for sy in (1, -1):
            tube(b, [(hc + s * 0.85, sy * 0.5, zr), (hc + s * 0.85, sy * 0.5, G.z_in_side(0.5) - 0.05)],
                 0.008, "Mat_Woven", 3, caps=False)
    for dx in (-0.4, 0.0, 0.4):
        tube(b, [(hc + dx, -0.6, zr + 0.05), (hc + dx, 0.6, zr + 0.05)], rr, "Wood_Smoked", 5)
    # pot hanger with fish-shaped adjuster, iron pot
    tube(b, [(hc, 0, zr + 0.05), (hc, 0, 1.12)], 0.018, "Wood_Smoked", 5)
    fish = [(-0.22, 0), (-0.12, 0.07), (0.05, 0.08), (0.18, 0.03), (0.24, 0.08),
            (0.24, -0.08), (0.18, -0.03), (0.05, -0.08), (-0.12, -0.07)]
    fz, fw = 1.08, 0.018
    front = [(hc + x, fw, fz + z) for x, z in fish]
    back = [(hc + x, -fw, fz + z) for x, z in fish]
    b.poly(front, "Wood_Smoked", facing=(0, 1, 0))
    b.poly(back, "Wood_Smoked", facing=(0, -1, 0))
    for k in range(len(fish)):
        k1 = (k + 1) % len(fish)
        ex, ez = fish[k1][0] - fish[k][0], fish[k1][1] - fish[k][1]
        mx, mz = (fish[k][0] + fish[k1][0]) / 2, (fish[k][1] + fish[k1][1]) / 2
        nrm = Vector((ez, 0, -ex))
        if nrm.dot(Vector((mx - 0.0, 0, mz))) < 0:
            nrm = -nrm
        b.poly([front[k], front[k1], back[k1], back[k]], "Wood_Smoked", facing=nrm)
    pz = 0.30
    tube(b, [(hc, 0, fz - 0.08), (hc, 0, pz + 0.38)], 0.012, "Iron", 4)
    lathe(b, [(0, 0), (0.14, 0), (0.2, 0.07), (0.215, 0.17), (0.205, 0.25), (0.19, 0.25),
              (0.2, 0.17), (0.185, 0.08), (0.13, 0.03), (0, 0.03)], "Iron", (hc, 0, pz), 12)
    tube(b, [(hc - 0.205 * math.cos(math.pi * k / 6), 0, pz + 0.25 + 0.13 * math.sin(math.pi * k / 6))
             for k in range(7)], 0.008, "Iron", 4)
    # treasure shelf with lacquerware, house guardian inau (chisekorkamuy) in the NE corner
    sy = G.Wyi - 0.25
    box(b, (1.675, sy, 0.33), (0.375, 0.2, 0.02), "Wood_Smoked")
    for x in (1.35, 2.0):
        box(b, (x, sy, 0.155), (0.03, 0.18, 0.155), "Wood_Smoked")
    lathe(b, [(0, 0), (0.12, 0), (0.14, 0.04), (0.14, 0.2), (0.12, 0.24), (0, 0.24)], "Lacquer", (1.5, sy, 0.35), 12)
    for k in range(2):
        lathe(b, [(0, 0), (0.04, 0), (0.09, 0.035), (0.1, 0.06), (0.09, 0.06), (0.075, 0.035), (0, 0.025)],
              "Lacquer", (1.85, sy, 0.35 + 0.045 * k), 12)
    ix, iy = G.Wxi - 0.15, G.Wyi - 0.15
    tube(b, [(ix, iy, 0), (ix, iy, 1.1)], 0.018, "Wood_Light", 5)
    lathe(b, [(0, 1.1), (0.03, 1.08), (0.075, 0.8), (0, 0.82)], "Wood_Light", (ix, iy, 0), 6, flip=True)
    return b


# ----------------------------------------------------------------------------- collision
def build_collision(G):
    """Convex pieces, Unreal UCX_ naming. Returns list of builders."""
    out = []

    def add_box(c, ax, ay, az):
        b = MeshBuilder()
        obox(b, c, ax, ay, az, None)
        out.append(b)

    for key in "SNEW":
        o, ud, L, nout, hout, _ = G.walls[key]
        doors = sorted((p["u0"], p["u1"], p["v1"]) for p in G.openings if p["wall"] == key and p["kind"] == "door")
        cuts, cur = [], 0.0
        for u0, u1, v1 in doors:
            cuts.append((cur, u0))
            cur = u1
        cuts.append((cur, L))
        for a, c in cuts:
            mid = o + ud * ((a + c) / 2) - nout * (G.wt / 2) + Z * (G.zW / 2)
            add_box(mid, ud * ((c - a) / 2), nout * (G.wt / 2), Z * (G.zW / 2))
        for u0, u1, v1 in doors:
            mid = o + ud * ((u0 + u1) / 2) - nout * (G.wt / 2) + Z * ((v1 + G.zW) / 2)
            add_box(mid, ud * ((u1 - u0) / 2), nout * (G.wt / 2), Z * ((G.zW - v1) / 2))
    add_box((0, 0, -0.05), (G.Wx, 0, 0), (0, G.Wy, 0), (0, 0, 0.05))
    # roof hull above head height
    zc = G.c["door_clearance"]
    X, Y, zt = G.X(zc), G.Y(zc), G.zR + 0.1
    b = MeshBuilder()
    c = [(-X, -Y, zc), (X, -Y, zc), (X, Y, zc), (-X, Y, zc)]
    r0, r1 = (-G.xe, 0, zt), (G.xe, 0, zt)
    b.poly(c, None, facing=-Z)
    b.poly([c[0], c[1], r1, r0], None, facing=(0, -1, 0))
    b.poly([c[2], c[3], r0, r1], None, facing=(0, 1, 0))
    b.poly([c[1], c[2], r1], None, facing=(1, 0, 0))
    b.poly([c[3], c[0], r0], None, facing=(-1, 0, 0))
    out.append(b)
    return out


# ----------------------------------------------------------------------------- blender data
def make_material(name, spec):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:  # Blender < 5.0; use_nodes is deprecated in 5.x
        mat.use_nodes = True
    nt = mat.node_tree
    N, Lk = nt.nodes, nt.links
    N.clear()
    loc = [-1400]

    def node(t, **inputs):
        n = N.new(t)
        n.location = (loc[0], 0)
        loc[0] += 180
        for k, v in inputs.items():
            n.inputs[k].default_value = v
        return n

    def op(o, a, bb=None):
        n = N.new("ShaderNodeMath")
        n.operation = o
        n.location = (loc[0], -200)
        loc[0] += 120
        for idx, x in enumerate((a, bb)):
            if x is None:
                continue
            if isinstance(x, (int, float)):
                n.inputs[idx].default_value = x
            else:
                Lk.new(x, n.inputs[idx])
        return n.outputs[0]

    uv = node("ShaderNodeTexCoord").outputs["UV"]
    pat = spec["pattern"]
    if pat == "streak":
        mp = node("ShaderNodeMapping", Scale=(spec["su"], spec["sv"], 1.0))
        Lk.new(uv, mp.inputs["Vector"])
        n1 = node("ShaderNodeTexNoise", Scale=spec["ns"], Detail=8.0)
        Lk.new(mp.outputs["Vector"], n1.inputs["Vector"])
        n2 = node("ShaderNodeTexNoise", Scale=spec["bs"], Detail=3.0)
        Lk.new(uv, n2.inputs["Vector"])
        fac = op("ADD", op("MULTIPLY", op("SUBTRACT", n2.outputs["Fac"], 0.5), spec["bw"]), n1.outputs["Fac"])
    elif pat == "blotch":
        n1 = node("ShaderNodeTexNoise", Scale=spec["ns"], Detail=6.0)
        Lk.new(uv, n1.inputs["Vector"])
        fac = n1.outputs["Fac"]
    else:
        sep = node("ShaderNodeSeparateXYZ")
        Lk.new(uv, sep.inputs["Vector"])
        u, v = sep.outputs["X"], sep.outputs["Y"]
        n1 = node("ShaderNodeTexNoise", Scale=6.0, Detail=4.0)
        Lk.new(uv, n1.inputs["Vector"])
        if pat == "weave":
            fac = op("ADD", op("ADD", op("PINGPONG", op("MULTIPLY", v, spec["k"]), 0.5),
                               op("MULTIPLY", op("PINGPONG", op("MULTIPLY", u, spec["k"] / 6), 0.5), 0.3)),
                     op("MULTIPLY", n1.outputs["Fac"], 0.5))
        elif pat == "diamond":
            fac = op("ADD", op("PINGPONG", op("MULTIPLY", u, spec["k"]), 0.5),
                     op("PINGPONG", op("MULTIPLY", v, spec["k"]), 0.5))
        else:  # lattice
            fac = op("ADD", op("MINIMUM", op("PINGPONG", op("MULTIPLY", u, spec["ku"]), 0.5),
                               op("PINGPONG", op("MULTIPLY", v, spec["kv"]), 0.5)),
                     op("MULTIPLY", n1.outputs["Fac"], 0.05))
    ramp = node("ShaderNodeValToRGB")
    Lk.new(fac, ramp.inputs["Fac"])
    cr = ramp.color_ramp
    stops = spec.get("stops")
    if stops:
        cr.interpolation = "CONSTANT"
    else:
        stops = list(zip((0.25, 0.42, 0.58, 0.75), spec["colors"]))
    while len(cr.elements) < len(stops):
        cr.elements.new(0.5)
    for el, (pos, col) in zip(cr.elements, stops):
        el.position = pos
        el.color = srgb(col)
    bsdf = node("ShaderNodeBsdfPrincipled", Roughness=spec["rough"])
    if "metallic" in spec:
        bsdf.inputs["Metallic"].default_value = spec["metallic"]
    Lk.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    if "emission" in spec:
        Lk.new(ramp.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = spec["emission"]
    bump = node("ShaderNodeBump", Strength=spec["bump"], Distance=0.02)
    Lk.new(fac, bump.inputs["Height"])
    Lk.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    out = node("ShaderNodeOutputMaterial")
    Lk.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    cols = [srgb(c) for _, c in stops]
    mat.diffuse_color = tuple(sum(c[i] for c in cols) / len(cols) for i in range(3)) + (1.0,)
    mat.roughness = spec["rough"]
    return mat


def to_object(b, name, coll):
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    if bpy.data.meshes.get(name):
        bpy.data.meshes.remove(bpy.data.meshes[name])
    me = bpy.data.meshes.new(name)
    me.from_pydata(b.v, [], b.f)
    for m in b.mats:
        me.materials.append(bpy.data.materials.get(m) or make_material(m, MATERIALS[m]))
    me.polygons.foreach_set("material_index", b.fm)
    try:
        me.polygons.foreach_set("use_smooth", b.fs)
    except Exception:
        attr = me.attributes.get("sharp_face") or me.attributes.new("sharp_face", "BOOLEAN", "FACE")
        attr.data.foreach_set("value", [not s for s in b.fs])
    uvl = me.uv_layers.new(name="UVMap")
    flat = []
    for f, uv in zip(b.f, b.fuv):
        for u in (uv or box_uv([b.v[i] for i in f])):
            flat += (u[0], u[1])
    uvl.data.foreach_set("uv", flat)
    me.validate(clean_customdata=False)
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def tri_count(ob):
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


def get_collection(name, parent):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
    if c.name not in parent.children:
        parent.children.link(c)
    return c


def reset(name):
    for c in list(bpy.data.collections):
        if c.name == name or c.name.startswith(name + "_"):
            for ob in list(c.objects):
                bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.collections.remove(c)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


def build_preview(G, coll):
    """Camera, sun, ground, interior fill light. Not part of the game asset."""
    scn = bpy.context.scene
    ground = MeshBuilder()  # ring around the footprint so it doesn't cover the sunken hearth
    o = [(-30, -30), (30, -30), (30, 30), (-30, 30)]
    i = [(-G.Wx, -G.Wy), (G.Wx, -G.Wy), (G.Wx, G.Wy), (-G.Wx, G.Wy)]
    for k in range(4):
        k1 = (k + 1) % 4
        ground.poly([(*o[k], -0.005), (*o[k1], -0.005), (*i[k1], -0.005), (*i[k], -0.005)], "Preview_Ground", facing=Z)
    if not bpy.data.materials.get("Preview_Ground"):
        make_material("Preview_Ground", dict(pattern="blotch", ns=0.8, rough=1.0, bump=0.1,
                                             colors=["#3d4a24", "#4c5a2c", "#5d6a34", "#6e7a40"]))
    to_object(ground, "Preview_Ground", coll)

    sun_data = bpy.data.lights.new("Preview_Sun", "SUN")
    sun_data.energy = 3.5
    sun_data.angle = math.radians(3)
    sun = bpy.data.objects.new("Preview_Sun", sun_data)
    sun.rotation_euler = (math.radians(48), 0, math.radians(-125))
    coll.objects.link(sun)
    fill_data = bpy.data.lights.new("Preview_InteriorFill", "POINT")
    fill_data.energy = 350
    fill_data.color = (1.0, 0.72, 0.45)
    fill_data.shadow_soft_size = 0.4
    fill = bpy.data.objects.new("Preview_InteriorFill", fill_data)
    fill.location = (-1.2, 0.0, 1.5)
    coll.objects.link(fill)

    cam_data = bpy.data.cameras.new("Preview_Camera")
    cam = bpy.data.objects.new("Preview_Camera", cam_data)
    coll.objects.link(cam)
    scn.camera = cam
    aim_camera(cam, *PREVIEW_VIEWS["exterior"][:3])

    world = scn.world or bpy.data.worlds.new("World")
    scn.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = srgb("#b9c8d6")
        bg.inputs["Strength"].default_value = 1.0


def aim_camera(cam, loc, target, lens):
    cam.location = loc
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = lens


# ----------------------------------------------------------------------------- main
def build(cfg=CONFIG):
    _SEED[0] = cfg["seed"]
    G = Geo(cfg)
    name = cfg["name"]
    reset(name)
    scn = bpy.context.scene
    root = get_collection(name, scn.collection)
    c0 = get_collection(name + "_LOD0", root)
    cl = get_collection(name + "_LODs", root)
    cc = get_collection(name + "_Collision", root)

    for m, spec in MATERIALS.items():
        make_material(m, spec)

    ext_name = f"SM_{name}_Exterior"
    objs = [to_object(build_exterior(G, LOD_QUALITY[0]), ext_name, c0),
            to_object(build_frame(G), f"SM_{name}_Frame", c0),
            to_object(build_props(G), f"SM_{name}_Props", c0)]
    lods = [to_object(build_exterior(G, LOD_QUALITY[i]), f"{ext_name}_LOD{i}", cl) for i in (1, 2)]
    cols = [to_object(b, f"UCX_{ext_name}_{i:02d}", cc) for i, b in enumerate(build_collision(G))]
    for ob in cols:
        ob.display_type = "WIRE"
        ob.hide_render = True
    cl.hide_viewport = cl.hide_render = True
    cc.hide_render = True

    if cfg.get("preview_scene"):
        cp = get_collection(name + "_Preview", root)
        build_preview(G, cp)

    total = sum(tri_count(o) for o in objs)
    print(f"\n{name}: footprint {cfg['length']:.2f} x {cfg['width']:.2f} m = {cfg['length'] * cfg['width']:.1f} m2,"
          f" eave {G.zE - G.t:.2f}-{G.zE:.2f} m, ridge {G.zR:.2f} m, ridge top {G.zR + 0.28:.2f} m")
    for o in objs + lods + cols:
        print(f"  {o.name:<34} {tri_count(o):>7,} tris  mats={len(o.data.materials)}")
    print(f"  LOD0 total {total:,} / {cfg['max_tris']:,} tris")
    if total > cfg["max_tris"]:
        print("  WARNING: over triangle budget - lower LOD_QUALITY[0] tiers/nx/ny/nc")
    return dict(G=G, objects=objs, lods=lods, collision=cols, total=total)


def _cli_args():
    if "--" not in sys.argv:
        return {}
    a, out, i = sys.argv[sys.argv.index("--") + 1:], {}, 0
    while i < len(a):
        if a[i].startswith("--") and i + 1 < len(a):
            out[a[i][2:]] = a[i + 1]
            i += 2
        else:
            i += 1
    return out


def _render(path, samples, view):
    scn = bpy.context.scene
    loc, target, lens, exposure = PREVIEW_VIEWS[view]
    aim_camera(scn.camera, loc, target, lens)
    scn.view_settings.exposure = exposure
    scn.render.engine = "CYCLES"
    scn.cycles.samples = samples
    scn.cycles.use_denoising = True
    scn.render.resolution_x, scn.render.resolution_y = 1280, 720
    scn.render.filepath = os.path.abspath(path)
    os.makedirs(os.path.dirname(scn.render.filepath), exist_ok=True)
    bpy.ops.render.render(write_still=True)


def _export(path, res):
    vl = bpy.context.view_layer
    vl.update()  # object list is stale after objects were removed in this session
    for o in bpy.context.scene.objects:
        o.select_set(False)
    sel = list(res["objects"])
    if path.lower().endswith(".fbx"):
        sel += res["collision"]
    for o in sel:
        o.hide_set(False)
        o.select_set(True)
    vl.objects.active = sel[0]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if path.lower().endswith(".fbx"):
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, apply_scale_options="FBX_SCALE_UNITS",
                                 mesh_smooth_type="FACE", use_tspace=True)
    else:
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)


if __name__ == "__main__":
    args = _cli_args()
    if bpy.app.background:  # headless: start from an empty scene
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
    res = build()
    samples = int(args.get("samples", 32))
    for key, path in args.items():
        if key == "render" or key.startswith("render-"):
            _render(path, samples, key[7:] or "exterior")
    if "export" in args:
        _export(args["export"], res)
    if "save" in args:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args["save"]))
