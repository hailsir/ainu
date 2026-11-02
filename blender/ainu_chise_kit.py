"""
ainu_chise_kit.py - split the Ainu chise into a modular game kit (Blender 5.0)
==============================================================================
Cuts the three merged LOD0 meshes made by ainu_chise.py (SM_AinuChise_Exterior,
_Frame and _Props) into separate mesh objects, each with its own pivot:

  walls     straight segments, window and door walls, the four outer corners
  roof      lower hip skirt: side straights, the four hip corners, the two hip ends
            (W with the door eave notch); upper smoke-gable roof: straights and
            gable ends; ridge: straights and ends
  synthetic inner wall corner and roof valley.  The house has no concave corners, so
            these are mitred from the straights. They let you build L-shaped houses.
  interior  tie beam and every prop (floor, hearth, mats, hangings, rack, pot hanger,
            pot, shelf, lacquerware, inau)

The interior timber goes with the shell piece it sits in. Posts and wall plates go
with the walls. Rafters, hip rafters and purlin rings go with the roof skirt.
A-frames, upper purlins and the ridge pole go with the upper roof. Every piece looks
right from inside too, and a longer house carries its frame with it.

Run headless (from this folder):

    blender --background ainu_chise.blend --python ainu_chise_kit.py -- \
        --export out/ainu_chise_kit.glb --export-dir out/kit --manifest out/kit_manifest.json \
        --render-parts out/parts.png --render-kit out/kit.png --render-demo out/demo.png \
        --save out/ainu_chise_kit.blend

If the scene does not contain the house yet (for example with --factory-startup), the
script runs ainu_chise.py from the same folder to build it first. Interactive use:
open ainu_chise.blend, open this file in the Scripting workspace and click Run Script.
It only replaces its own collections (AinuChise_Parts, _Kit, _KitPreview), so you can
re-run it as often as you like. The source meshes are hidden, not changed.

Outputs
  AinuChise_Parts       every piece of the house in place, so the house is re-assembled from parts.
  AinuChise_Kit         one object per unique piece type, laid out in labelled rows.
                        These share mesh data with the Parts, and this is what gets exported.
  AinuChise_KitPreview  labels, plus a demo house with 2 extra modules built from kit pieces.
                        Never exported.

Conventions follow AINU_CHISE_TEMPLATE.md: Z up, metres, long axis = X, door on -X (W).
Pieces keep their world orientation and their names carry the facing (S/N/E/W,
SE/NE/...). Every pivot is on z = 0, so the pieces share one floor grid:
  wall and roof-skirt runs  outer wall line, at the start of the piece (min x or min y)
  corners                   outer wall corner
  hip ends                  outer wall line, middle of the end wall
  upper roof / ridge        ridge centreline, at the start of the piece (ends: at the join)
  synthetic corners         the concave outer-wall corner
  props / beams             bottom centre (hanging props: top centre)
A 1 m wall straight and a 1 m roof straight with the same pivot stack exactly. To make
a longer house, insert 1 m straights at x = 0 and move the east half along (see the demo).
"""

import bpy
import bmesh
import json
import math
import os
import runpy
import sys
from collections import defaultdict
from mathutils import Vector, kdtree

# --------------------------------------------------------------------------------------
# CONFIG (reference build: 5.0 x 4.0 m, door W, windows S / N / E)
# --------------------------------------------------------------------------------------
CONFIG = {
    "name": "AinuChise",
    "prefix": "SM_AinuChise",
    "sources": ("SM_AinuChise_Exterior", "SM_AinuChise_Frame", "SM_AinuChise_Props"),
    "generator": "ainu_chise.py",     # used for exact dimensions + to build a missing house
    # Wall corner pieces: leg length on each wall, measured from the outer faces.
    "wall_corner": 0.5,
    # Cut positions along each wall between the corner pieces (S/N: x, E/W: y).
    # Every opening (with its pole frame) must fit inside one segment; this is validated.
    "wall_cuts": {
        "S": (-2.0, -1.0, 0.0, 0.5, 2.0),     # 1 | 1 | 0.5 | window 1.5
        "N": (-2.0, -0.5, 0.0, 1.0, 2.0),     # window 1.5 | 0.5 | 1 | 1
        "E": (-1.5, -0.75, 0.75, 1.5),        # 0.75 | window 1.5 | 0.75
        "W": (-1.5, -0.75, 0.75, 1.5),        # 0.75 | door 1.5 | 0.75
    },
    "opening_margin": 0.12,           # the pole frame sticks out this far past an opening
    # Roof straight modules (lower skirt, upper roof, ridge): cut positions along x.
    # Everything beyond the outer two cuts belongs to the hip / gable ends.
    "roof_cuts": (-1.0, 0.0, 1.0),
    # At the hip ends, |y| < roof_end_half is the end slope; beyond it is the hip corner.
    "roof_end_half": 0.6,
    # Cutting rules where a piece boundary crosses an island:
    #   exterior: thatch and single binding/ridge poles are cut and capped.  Assembled
    #             details (window and door frames, crossed ridge sticks), blinds and
    #             gable vents are kept whole.
    #   frame:    members at least frame_cut_extent long are cut (purlin rings, wall
    #             plates, ridge pole, upper purlins).  Posts, rafters and A-frames are kept whole.
    # A whole island goes to the piece that holds the centre of its bounding box.
    "frame_cut_extent": 2.5,
    "group_extent": 1.5,      # small same-material parts whose boxes touch form one unit
    "graze": 0.03,            # crossings shallower than this are ignored
    # Seam conforming: the cut ends of run pieces are snapped to one reference profile, so
    # any straight tiles with any other straight, corner or end (the source has ~2 cm of
    # outward jitter).
    "seam_ref": {"walls_x": -0.25, "walls_y": -1.1, "roof_x": 0.0},
    "seam_match": 0.05,       # max distance for snapping an end vertex onto the profile
    "seam_falloff": 0.20,     # vertices this close to a seam follow the snap smoothly
    # Ridge straights: the crossed stick pairs are re-spaced to these offsets in every 1 m
    # module, so a row of modules keeps an even 0.5 m rhythm.
    "ridge_stick_slots": (0.25, 0.75),
    # Synthetic concave pieces: how far each arm reaches past the corner.
    "synth_arm": 1.0,
    # Kit layout / demo / renders
    "layout_origin": (7.0, -8.0),
    "layout_gap": 0.6,
    "layout_row_width": 20.0,
    "demo_extra": 2,          # 1 m modules inserted at x = 0 for the demo house
    "demo_offset": (0.0, 11.0),
    "samples": 16,
}

# Interior items: the first matching selector wins.  Each island (welded mesh part) is
# tested on its material and the centre of its bounding box (x0, x1, y0, y1, z0, z1).
# Anything nothing matches goes to Prop_Misc and is reported, so no geometry is lost.
INF = 1e9
PROPS = [
    # name               materials                         centre box                           pivot
    ("Floor",            {"Earth_Floor", "Ash"},            None,                                "bottom"),
    ("Hearth",           {"Wood_Smoked", "Embers"},         (-1.0, 1.1, -0.6, 0.6, -0.3, 0.15),  "bottom"),
    ("Mat_Floor_N",      {"Mat_Woven"},                     (-INF, INF, 0.62, INF, -0.1, 0.05),  "bottom"),
    ("Mat_Floor_S",      {"Mat_Woven"},                     (-INF, INF, -INF, -0.62, -0.1, 0.05), "bottom"),
    ("Mat_Floor_E",      {"Mat_Woven"},                     (1.1, INF, -INF, INF, -0.1, 0.05),   "bottom"),
    ("Hanging_N",        {"Mat_Pattern"},                   (-INF, INF, 0.0, INF, -INF, INF),    "bottom"),
    ("Hanging_S",        {"Mat_Pattern"},                   (-INF, INF, -INF, 0.0, -INF, INF),   "bottom"),
    ("Drying_Rack",      {"Wood_Smoked", "Mat_Woven"},      (-INF, INF, -INF, INF, 1.8, INF),    "top"),
    ("Pot_Hanger",       {"Wood_Smoked", "Iron"},           (-0.3, 0.5, -0.1, 0.1, 0.66, 1.8),   "top"),
    ("Pot",              {"Iron"},                          None,                                "bottom"),
    ("Treasure_Shelf",   {"Wood_Smoked"},                   (1.2, INF, 1.2, INF, -INF, 0.5),     "bottom"),
    ("Lacquer_Tub",      {"Lacquer"},                       (-INF, 1.7, -INF, INF, -INF, INF),   "bottom"),
    ("Lacquer_Bowl_A",   {"Lacquer"},                       (-INF, INF, -INF, INF, -INF, 0.405), "bottom"),
    ("Lacquer_Bowl_B",   {"Lacquer"},                       None,                                "bottom"),
    ("Inau",             {"Wood_Light"},                    None,                                "bottom"),
]
SINGLE_ISLAND_PROPS = {"Lacquer_Tub", "Lacquer_Bowl_A", "Lacquer_Bowl_B"}

# Layer ids
WALL, LOWER, UPPER, RIDGE, BEAM, PROP, MISC = range(7)
LAYER_NAMES = ["Wall", "Roof", "RoofTop", "Ridge", "Frame", "Prop", "Misc"]
CUT_MATS = {"Thatch_Roof", "Thatch_Wall", "Wood_Pole"}


def log(*a):
    print("[chise-kit]", *a)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
class Plane:
    """Axis-aligned plane. The kept side is (p[axis] - value) * sign >= 0."""

    def __init__(self, axis, value, sign):
        self.axis, self.value, self.sign = axis, value, sign
        self.no = Vector((0.0, 0.0, 0.0))
        self.no[axis] = float(sign)
        self.co = Vector((0.0, 0.0, 0.0))
        self.co[axis] = value

    def d(self, p):
        return (p[self.axis] - self.value) * self.sign


def box_planes(x0=None, x1=None, y0=None, y1=None):
    pl = []
    if x0 is not None:
        pl.append(Plane(0, x0, +1))
    if x1 is not None:
        pl.append(Plane(0, x1, -1))
    if y0 is not None:
        pl.append(Plane(1, y0, +1))
    if y1 is not None:
        pl.append(Plane(1, y1, -1))
    return pl


def fmt_len(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") + "m"


def new_bm():
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    for name in ("isl", "cap", "cut"):
        bm.faces.layers.int.new(name)
    bm.verts.layers.float_vector.new("seam_n")   # custom normal for seam vertices (0 = auto)
    return bm


def append_faces(dst, src, faces, offset=None, mirror_diag=False, only_cut=False):
    """Copy faces (UVs, material, smooth, tags) from bmesh `src` into `dst`.
    mirror_diag reflects across the vertical plane x = -y (and fixes the winding)."""
    uv_s, uv_d = src.loops.layers.uv["UVMap"], dst.loops.layers.uv["UVMap"]
    ls = [src.faces.layers.int[n] for n in ("isl", "cap", "cut")]
    ld = [dst.faces.layers.int[n] for n in ("isl", "cap", "cut")]
    sn_s, sn_d = src.verts.layers.float_vector["seam_n"], dst.verts.layers.float_vector["seam_n"]
    vmap = {}
    for f in faces:
        if only_cut and not f[ls[2]]:
            continue
        vs = []
        for v in f.verts:
            nv = vmap.get(v)
            if nv is None:
                co = Vector((-v.co.y, -v.co.x, v.co.z)) if mirror_diag else v.co.copy()
                if offset is not None:
                    co += offset
                nv = vmap[v] = dst.verts.new(co)
                n = v[sn_s]
                nv[sn_d] = Vector((-n.y, -n.x, n.z)) if mirror_diag else n
            vs.append(nv)
        loops = list(f.loops)
        if mirror_diag:
            vs.reverse()
            loops.reverse()
        try:
            nf = dst.faces.new(vs)
        except ValueError:
            continue
        nf.material_index = f.material_index
        nf.smooth = f.smooth
        for a, b in zip(ls, ld):
            nf[b] = f[a]
        for nl, ol in zip(nf.loops, loops):
            nl[uv_d].uv = ol[uv_s].uv
    return dst


def island_geom(bm, isl_ids):
    lay = bm.faces.layers.int["isl"]
    faces = [f for f in bm.faces if f[lay] in isl_ids]
    edges = {e for f in faces for e in f.edges}
    verts = {v for f in faces for v in f.verts}
    return list(verts) + list(edges) + faces


def bisect_keep(bm, plane, isl_ids):
    """Cut the given islands with a plane and delete the part outside the kept side."""
    geom = island_geom(bm, isl_ids)
    if geom:
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=plane.co, plane_no=-plane.no,
                               clear_outer=True, clear_inner=False)


def bisect_all(bm, co, no):
    """Cut everything and delete the side that `no` points to."""
    geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
    bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=co, plane_no=no, clear_outer=True)


def fill_caps(bm, co, no, outward, eps=1e-4):
    """Close the open cross-sections that lie on a plane.  Only closed loops are filled, and
    each island is filled separately (so the poles and the thatch don't merge).  Caps are
    flat-shaded and get a world-scale planar UV."""
    isl = bm.faces.layers.int["isl"]
    cap = bm.faces.layers.int["cap"]
    cut = bm.faces.layers.int["cut"]
    uv = bm.loops.layers.uv["UVMap"]

    def on(v):
        return abs((v.co - co).dot(no)) < eps

    by_isl = defaultdict(list)
    for e in bm.edges:
        if e.is_boundary and on(e.verts[0]) and on(e.verts[1]):
            by_isl[e.link_faces[0][isl]].append(e)
    t1 = no.cross(Vector((0, 0, 1)))
    if t1.length < 1e-6:
        t1 = Vector((1, 0, 0))
    t1.normalize()
    t2 = no.cross(t1)
    made = 0
    for isl_id, es in by_isl.items():
        deg = defaultdict(int)
        for e in es:
            for v in e.verts:
                deg[v] += 1
        es = [e for e in es if deg[e.verts[0]] == 2 and deg[e.verts[1]] == 2]
        if len(es) < 3:
            continue
        mat = es[0].link_faces[0].material_index
        res = bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=es, normal=outward)
        for f in (g for g in res["geom"] if isinstance(g, bmesh.types.BMFace)):
            if f.normal.dot(outward) < 0:
                f.normal_flip()
            f.material_index = mat
            f.smooth = False
            f[isl], f[cap], f[cut] = isl_id, 1, 1
            for lp in f.loops:
                lp[uv].uv = (lp.vert.co.dot(t1), lp.vert.co.dot(t2))
            made += 1
    return made


def collection(name, parent=None):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(col)
    return col


def purge_collection(name):
    col = bpy.data.collections.get(name)
    if not col:
        return
    for ch in list(col.children):
        purge_collection(ch.name)
    for ob in list(col.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
    bpy.data.collections.remove(col)


# --------------------------------------------------------------------------------------
# Generator (ainu_chise.py): exact dimensions, and builds the house if it is missing
# --------------------------------------------------------------------------------------
def _script_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return bpy.path.abspath("//") or os.getcwd()


def load_generator():
    for d in (_script_dir(), bpy.path.abspath("//"), os.getcwd()):
        p = os.path.join(d, CONFIG["generator"]) if d else None
        if p and os.path.isfile(p):
            return runpy.run_path(p, run_name="ainu_chise_generator")
    return None


def ensure_sources(gen):
    if all(bpy.data.objects.get(n) for n in CONFIG["sources"]):
        return
    if gen is None:
        raise RuntimeError("source meshes %s not found and %s is not next to this script"
                           % (", ".join(CONFIG["sources"]), CONFIG["generator"]))
    log("house not in the scene - building it with", CONFIG["generator"])
    if bpy.app.background:
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
    gen["build"]()


# --------------------------------------------------------------------------------------
# Source: all three meshes in one world-space bmesh, faces tagged by welded island
# --------------------------------------------------------------------------------------
class Source:
    def __init__(self):
        self.mats = []
        self.bm = new_bm()
        self.src_of = []           # per island: source index
        self.load()
        self.find_islands()

    def load(self):
        bm = self.bm
        uv = bm.loops.layers.uv["UVMap"]
        self.face_src = {}
        for si, name in enumerate(CONFIG["sources"]):
            ob = bpy.data.objects[name]
            me = ob.data
            remap = []
            for m in (list(me.materials) or [None]):
                if m not in self.mats:
                    self.mats.append(m)
                remap.append(self.mats.index(m))
            vmap = [bm.verts.new(ob.matrix_world @ v.co) for v in me.vertices]
            uvd = me.uv_layers.active.data if me.uv_layers.active else None
            for p in me.polygons:
                try:
                    f = bm.faces.new([vmap[i] for i in p.vertices])
                except ValueError:
                    continue
                f.material_index = remap[min(p.material_index, len(remap) - 1)]
                f.smooth = p.use_smooth
                self.face_src[f] = si
                if uvd:
                    for loop, li in zip(f.loops, p.loop_indices):
                        loop[uv].uv = uvd[li].uv
        log("loaded %d faces / %d materials from %s" % (len(bm.faces), len(self.mats), ", ".join(CONFIG["sources"])))

    def mat_name(self, idx):
        m = self.mats[idx]
        return m.name if m else ""

    def find_islands(self, small=16):
        """Mesh parts.  Start from topological islands (shared vertices), then weld small
        ones (caps, box faces, single quads: flat-shaded parts store split vertices) onto
        whatever they touch by vertex *position*, within the same source and material.
        Two large surfaces that merely touch (upper roof resting on the hip skirt) stay
        separate."""
        faces = list(self.bm.faces)
        fidx = {f: i for i, f in enumerate(faces)}
        parent = list(range(len(faces)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for v in self.bm.verts:                       # topology
            lf = v.link_faces
            for f in lf[1:]:
                union(fidx[lf[0]], fidx[f])
        topo = defaultdict(list)
        for i in range(len(faces)):
            topo[find(i)].append(i)
        size = {r: len(m) for r, m in topo.items()}
        at = defaultdict(set)                         # position key -> topological islands
        for r, members in topo.items():
            for i in members:
                f = faces[i]
                for v in f.verts:
                    c = v.co
                    at[(self.face_src[f], f.material_index, round(c.x, 4), round(c.y, 4), round(c.z, 4))].add(r)
        for rs in at.values():
            if len(rs) < 2:
                continue
            rs = sorted(rs, key=lambda r: -size[r])
            big = [r for r in rs if size[r] > small]
            anchor = big[0] if big else rs[0]
            for r in rs:
                if r != anchor and size[r] <= small:
                    union(anchor, r)
        roots = {}
        self.isl_faces = []
        lay = self.bm.faces.layers.int["isl"]
        for i, f in enumerate(faces):
            r = find(i)
            if r not in roots:
                roots[r] = len(self.isl_faces)
                self.isl_faces.append([])
            f[lay] = roots[r]
            self.isl_faces[roots[r]].append(f)
        self.info = []
        for fs in self.isl_faces:
            vs = {v for f in fs for v in f.verts}
            lo = Vector([min(v.co[k] for v in vs) for k in range(3)])
            hi = Vector([max(v.co[k] for v in vs) for k in range(3)])
            self.info.append({"lo": lo, "hi": hi, "c": (lo + hi) * 0.5, "src": self.face_src[fs[0]],
                              "mat": self.mat_name(fs[0].material_index),
                              "area": sum(f.calc_area() for f in fs)})
        log("%d welded islands" % len(self.isl_faces))


# --------------------------------------------------------------------------------------
# Classification: island -> layer, small parts -> groups, groups -> cuttable or whole
# --------------------------------------------------------------------------------------
def measure(S, G):
    if G is not None:
        dims = {"X0": G.Wx, "Y0": G.Wy, "z_wall": G.zW, "lower_top": G.zG, "G": G}
    else:  # fall back to measuring the mesh (outer faces carry ~2 cm of outward jitter)
        th = [n for n in S.info if n["src"] == 0 and n["mat"] == "Thatch_Wall"]
        snap = lambda v: math.floor(v / 0.05 + 0.25) * 0.05
        z_wall = max(n["hi"].z for n in th)
        dims = {"X0": snap(max(max(n["hi"].x, -n["lo"].x) for n in th)),
                "Y0": snap(max(max(n["hi"].y, -n["lo"].y) for n in th)),
                "z_wall": z_wall,
                "lower_top": max(n["hi"].z for n in S.info
                                 if n["src"] == 0 and n["mat"] == "Thatch_Roof" and n["lo"].z < z_wall),
                "G": None}
    log("outer walls x=+-%.2f y=+-%.2f, wall top %.2f, hip skirt top %.2f"
        % (dims["X0"], dims["Y0"], dims["z_wall"], dims["lower_top"]))
    return dims


def classify(S, dims):
    X0, Y0, z_wall, lower_top = dims["X0"], dims["Y0"], dims["z_wall"], dims["lower_top"]
    layer = [MISC] * len(S.info)
    for i, n in enumerate(S.info):
        lo, hi, c, mat = n["lo"], n["hi"], n["c"], n["mat"]
        if n["src"] == 0:                                    # exterior shell
            if mat == "Thatch_Roof" and lo.z < z_wall:
                layer[i] = LOWER                             # hip skirt
            elif mat != "Gable_Vent" and lo.z > lower_top + 0.3 and (hi.y - lo.y) < 0.5 * Y0:
                layer[i] = RIDGE                             # bundle, crossed sticks, top pole
            elif mat in ("Thatch_Roof", "Gable_Vent") and lo.z > z_wall:
                layer[i] = UPPER                             # smoke-gable roof + vents
            elif hi.z <= z_wall + 0.1:
                layer[i] = WALL                              # thatch, binding poles, frames, blinds
        elif n["src"] == 1:                                  # interior timber
            near_wall = min(X0 - abs(c.x), Y0 - abs(c.y)) < 0.45
            crosses_ridge = lo.y < -0.1 and hi.y > 0.1 and (hi.x - lo.x) < 0.5
            if lo.z < 0.05:
                layer[i] = WALL                              # posts
            elif hi.z <= z_wall + 0.05 and lo.z > 1.5:
                layer[i] = WALL if near_wall else BEAM       # wall plates / tie beams
            elif lo.z > lower_top or crosses_ridge:
                layer[i] = UPPER                             # A-frames, collars, purlins, ridge pole
            else:
                layer[i] = LOWER                             # rafters, hip rafters, purlin rings
        else:
            layer[i] = PROP

    # small same-material parts whose boxes touch become one unit (crossed sticks,
    # A-frame legs, window-frame poles ...)
    ge = CONFIG["group_extent"]
    group = list(range(len(S.info)))

    def gfind(a):
        while group[a] != a:
            group[a] = group[group[a]]
            a = group[a]
        return a

    small = [i for i, n in enumerate(S.info) if layer[i] not in (PROP, MISC)
             and max(n["hi"].x - n["lo"].x, n["hi"].y - n["lo"].y) < ge]
    for k, a in enumerate(small):
        A = S.info[a]
        for b in small[k + 1:]:
            B = S.info[b]
            if layer[a] != layer[b] or A["mat"] != B["mat"] or A["src"] != B["src"]:
                continue
            if all(A["lo"][j] <= B["hi"][j] + 1e-4 and B["lo"][j] <= A["hi"][j] + 1e-4 for j in range(3)):
                ra, rb = gfind(a), gfind(b)
                if ra != rb:
                    group[rb] = ra
    group = [gfind(i) for i in range(len(S.info))]

    members = defaultdict(list)
    for i, g in enumerate(group):
        members[g].append(i)
    cuttable = {}
    for g, ids in members.items():
        n = S.info[ids[0]]
        if n["src"] == 0:
            cuttable[g] = len(ids) == 1 and n["mat"] in CUT_MATS
        elif n["src"] == 1:
            cuttable[g] = max(n["hi"].x - n["lo"].x, n["hi"].y - n["lo"].y) >= CONFIG["frame_cut_extent"]
        else:
            cuttable[g] = False
    lay_cut = S.bm.faces.layers.int["cut"]
    for i, fs in enumerate(S.isl_faces):
        for f in fs:
            f[lay_cut] = int(cuttable[group[i]])
    return layer, group, cuttable


# --------------------------------------------------------------------------------------
# Piece regions
# --------------------------------------------------------------------------------------
class PieceDef:
    def __init__(self, layer, base, planes, pivot, kind, seam_axes=(), meta=None):
        self.layer, self.base, self.planes = layer, base, planes
        self.pivot = Vector(pivot)
        self.kind = kind            # run | corner | end | item | synth
        self.seam_axes = seam_axes  # axes whose cut planes are conformed to the profile
        self.meta = meta or {}
        self.name = None
        self.kit = False


def opening_spans(G):
    """World-space interval along the wall for every opening, plus its frame."""
    out = defaultdict(list)
    if G is None:
        return out
    m = CONFIG["opening_margin"]
    for op in G.openings:
        o, ud = G.walls[op["wall"]][0], op["ud"]
        axis = 0 if op["wall"] in "SN" else 1
        a, b = sorted(((o + ud * op["u0"])[axis], (o + ud * op["u1"])[axis]))
        out[op["wall"]].append((a - m, b + m, op["kind"]))
    return out


def build_defs(dims):
    X0, Y0 = dims["X0"], dims["Y0"]
    c = CONFIG["wall_corner"]
    xc, yc = X0 - c, Y0 - c
    defs = []
    spans = opening_spans(dims["G"])

    def check(side, cuts, half):
        if abs(cuts[0] + half) > 1e-6 or abs(cuts[-1] - half) > 1e-6:
            raise ValueError("wall_cuts[%s] must run from %.2f to %.2f" % (side, -half, half))
        g = CONFIG["graze"]     # a frame poking out less than this stays whole in its piece
        for a, b, kind in spans.get(side, ()):
            if not any(p <= a + g and b - g <= q for p, q in zip(cuts[:-1], cuts[1:])):
                raise ValueError("wall_cuts[%s] %s cuts through the %s at %.2f..%.2f"
                                 % (side, cuts, kind, a, b))

    # walls ----------------------------------------------------------------------------
    for sx, sy, tag in ((1, -1, "SE"), (-1, -1, "SW"), (1, 1, "NE"), (-1, 1, "NW")):
        pl = box_planes(x0=xc if sx > 0 else None, x1=-xc if sx < 0 else None,
                        y0=yc if sy > 0 else None, y1=-yc if sy < 0 else None)
        defs.append(PieceDef(WALL, "Wall_Corner_" + tag, pl, (sx * X0, sy * Y0, 0), "corner", (0, 1)))
    cuts = CONFIG["wall_cuts"]
    for side, sy in (("S", -1), ("N", 1)):
        check(side, cuts[side], xc)
        for a, b in zip(cuts[side][:-1], cuts[side][1:]):
            pl = box_planes(x0=a, x1=b, y0=yc if sy > 0 else None, y1=-yc if sy < 0 else None)
            defs.append(PieceDef(WALL, "Wall_%s_%s" % (side, fmt_len(b - a)), pl, (a, sy * Y0, 0), "run",
                                 (0, 1), {"span": (a, b), "axis": 0, "side": side}))
    for side, sx in (("E", 1), ("W", -1)):
        check(side, cuts[side], yc)
        for a, b in zip(cuts[side][:-1], cuts[side][1:]):
            pl = box_planes(x0=xc if sx > 0 else None, x1=-xc if sx < 0 else None, y0=a, y1=b)
            defs.append(PieceDef(WALL, "Wall_%s_%s" % (side, fmt_len(b - a)), pl, (sx * X0, a, 0), "run",
                                 (0, 1), {"span": (a, b), "axis": 1, "side": side}))

    # hip skirt ----------------------------------------------------------------------------
    rc = CONFIG["roof_cuts"]
    r0, r1 = rc[0], rc[-1]
    ye = CONFIG["roof_end_half"]
    for sx, sy, tag in ((1, -1, "SE"), (-1, -1, "SW"), (1, 1, "NE"), (-1, 1, "NW")):
        pl = box_planes(x0=r1 if sx > 0 else None, x1=r0 if sx < 0 else None,
                        y0=ye if sy > 0 else None, y1=-ye if sy < 0 else None)
        defs.append(PieceDef(LOWER, "Roof_Corner_" + tag, pl, (sx * X0, sy * Y0, 0), "corner", (0,)))
    for sx, tag in ((1, "E"), (-1, "W")):
        pl = box_planes(x0=r1 if sx > 0 else None, x1=r0 if sx < 0 else None, y0=-ye, y1=ye)
        defs.append(PieceDef(LOWER, "Roof_End_" + tag, pl, (sx * X0, 0, 0), "end", (0,)))
    for side, sy in (("S", -1), ("N", 1)):
        for a, b in zip(rc[:-1], rc[1:]):
            pl = box_planes(x0=a, x1=b, y0=0 if sy > 0 else None, y1=0 if sy < 0 else None)
            defs.append(PieceDef(LOWER, "Roof_%s_%s" % (side, fmt_len(b - a)), pl, (a, sy * Y0, 0), "run",
                                 (0,), {"span": (a, b), "axis": 0, "side": side}))

    # upper roof + ridge -------------------------------------------------------------------
    for layer, base in ((UPPER, "RoofTop"), (RIDGE, "Ridge")):
        for sx, tag in ((1, "E"), (-1, "W")):
            pl = box_planes(x0=r1 if sx > 0 else None, x1=r0 if sx < 0 else None)
            defs.append(PieceDef(layer, "%s_End_%s" % (base, tag), pl, (r1 if sx > 0 else r0, 0, 0), "end", (0,)))
        for a, b in zip(rc[:-1], rc[1:]):
            defs.append(PieceDef(layer, "%s_%s" % (base, fmt_len(b - a)), box_planes(x0=a, x1=b), (a, 0, 0),
                                 "run", (0,), {"span": (a, b), "axis": 0, "side": "C"}))
    return defs


# --------------------------------------------------------------------------------------
# Seam profiles
# --------------------------------------------------------------------------------------
class Profile:
    """Cross-section of a layer at a reference plane, split into closed loops (one per
    thatch shell, pole, plate ...).  Run-piece ends are snapped onto it loop by loop."""

    def __init__(self, bm_layer, axis, value):
        bm = bm_layer.copy()
        co = Vector((0, 0, 0))
        co[axis] = value
        no = Vector((0, 0, 0))
        no[axis] = 1
        lay = bm.faces.layers.int["cut"]
        faces = [f for f in bm.faces if f[lay]]
        geom = list({v for f in faces for v in f.verts}) + list({e for f in faces for e in f.edges}) + faces
        res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=co, plane_no=no)
        bm.normal_update()
        self.axis = axis
        cutv = [v for v in res["geom_cut"] if isinstance(v, bmesh.types.BMVert)]
        cute = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
        self.pts = [self.flat(v.co) for v in cutv]
        self.nrm = [self.smooth_normal(v) for v in cutv]
        index = {v: i for i, v in enumerate(cutv)}
        segs = [(index[e.verts[0]], index[e.verts[1]]) for e in cute if e.verts[0] in index and e.verts[1] in index]
        self.loops = self.components(len(self.pts), segs, self.pts)
        bm.free()

    def flat(self, p):
        q = Vector(p)
        q[self.axis] = 0.0
        return q

    @staticmethod
    def components(n, segs, pts):
        """Connected vertex sets -> list of dicts(verts, segs, centre, size)."""
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for a, b in segs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        comp = defaultdict(lambda: {"verts": [], "segs": []})
        for i in range(n):
            comp[find(i)]["verts"].append(i)
        for a, b in segs:
            comp[find(a)]["segs"].append((a, b))
        out = []
        for c in comp.values():
            ps = [pts[i] for i in c["verts"]]
            c["centre"] = sum(ps, Vector()) / len(ps)
            c["size"] = max((p - c["centre"]).length for p in ps)
            out.append(c)
        return out

    @staticmethod
    def smooth_normal(v):
        """Normal of the continuous surface at a cut vertex: area-weighted average of the
        smooth faces around it (zero if only flat faces touch it; they keep face normals)."""
        n = Vector()
        for f in v.link_faces:
            if f.smooth:
                n += f.normal * f.calc_area()
        return n.normalized() if n.length > 1e-12 else Vector()

    def snap_in_loop(self, loop, q):
        """Closest profile vertex, or else closest point on the loop outline, with normal."""
        i = min(loop["verts"], key=lambda k: (self.pts[k] - q).length)
        if (self.pts[i] - q).length < 1e-3 or not loop["segs"]:
            return self.pts[i], self.nrm[i]
        best, bn, bd = None, None, 1e9
        for ia, ib in loop["segs"]:
            a, b = self.pts[ia], self.pts[ib]
            ab = b - a
            t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (q - a).dot(ab) / ab.length_squared))
            p = a + ab * t
            d = (p - q).length
            if d < bd:
                na, nb = self.nrm[ia], self.nrm[ib]
                n = na.lerp(nb, t) if na.length and nb.length else (na if t < 0.5 else nb)
                best, bn, bd = p, (n.normalized() if n.length else Vector()), d
        if (self.pts[i] - q).length <= bd + 1e-6:
            return self.pts[i], self.nrm[i]
        return best, bn

    def assign_normals(self, bm, value):
        """Seam normals for vertices that already sit exactly on this cross-section."""
        sn = bm.verts.layers.float_vector["seam_n"]
        kd = kdtree.KDTree(max(1, len(self.pts)))
        for i, p in enumerate(self.pts):
            kd.insert(p, i)
        kd.balance()
        for v in bm.verts:
            if abs(v.co[self.axis] - value) < 1e-5:
                p, i, d = kd.find(self.flat(v.co))
                if p is not None and d < 1e-4:
                    v[sn] = self.nrm[i]

    def conform(self, bm, value, keep_sign):
        """Snap every cut loop on plane axis=value onto its matching profile loop (moved as a
        unit first, then vertex by vertex), and drag nearby vertices of the same island along
        with a smooth falloff.  Returns (snapped verts, unmatched verts)."""
        ax, tol, fall = self.axis, CONFIG["seam_match"], CONFIG["seam_falloff"]
        isl = bm.faces.layers.int["isl"]
        sn = bm.verts.layers.float_vector["seam_n"]
        ends = [v for v in bm.verts if abs(v.co[ax] - value) < 1e-5]
        if not ends or not self.loops:
            return 0, len(ends)
        bm.normal_update()
        index = {v: i for i, v in enumerate(ends)}
        segs = [(index[e.verts[0]], index[e.verts[1]]) for e in bm.edges
                if e.verts[0] in index and e.verts[1] in index]
        mine = self.components(len(ends), segs, [self.flat(v.co) for v in ends])
        delta = {}
        for comp in mine:
            # matching profile loop: nearest centre of a similar size
            cand = [L for L in self.loops if abs(L["size"] - comp["size"]) < max(tol, 0.25 * L["size"])]
            if not cand:
                continue
            L = min(cand, key=lambda L: (L["centre"] - comp["centre"]).length)
            shift = L["centre"] - comp["centre"]
            if shift.length > tol:
                continue
            # 1) every profile vertex claims its closest end vertex (one-to-one, closest
            #    first), so the end keeps all the profile's corners;  2) the remaining end
            #    vertices are projected onto the profile outline.
            pairs = sorted(((self.pts[j] - (self.flat(ends[k].co) + shift)).length, k, j)
                           for k in comp["verts"] for j in L["verts"])
            fixed, used = {}, set()
            for d, k, j in pairs:
                if d > tol:
                    break
                if k not in fixed and j not in used:
                    fixed[k] = j
                    used.add(j)
            for k in comp["verts"]:
                v = ends[k]
                q = self.flat(v.co) + shift
                if k in fixed:
                    p, n = self.pts[fixed[k]], self.nrm[fixed[k]]
                else:
                    p, n = self.snap_in_loop(L, q)
                if (p - q).length > tol:
                    continue
                delta[v] = p - self.flat(v.co)
                own = self.smooth_normal(v)
                if n.length and own.length and own.angle(n) < math.radians(35):
                    v[sn] = n            # (a tube rotated differently keeps its own shading)
        if not delta:
            return 0, len(ends)
        # falloff: vertices near the seam follow the closest snapped end of their own island
        per_isl = defaultdict(list)
        for v, d in delta.items():
            if v.link_faces:
                per_isl[v.link_faces[0][isl]].append((v, d))
        trees = {}
        for k, items in per_isl.items():
            kd = kdtree.KDTree(len(items))
            for i, (v, _) in enumerate(items):
                kd.insert(self.flat(v.co), i)
            kd.balance()
            trees[k] = (kd, items)
        for v in bm.verts:
            off = (v.co[ax] - value) * keep_sign
            if off < 1e-5 or off > fall or not v.link_faces:
                continue
            t = trees.get(v.link_faces[0][isl])
            if t is None:
                continue
            p, i, d = t[0].find(self.flat(v.co))
            if p is None or d > 0.15:
                continue
            w = 1.0 - off / fall
            v.co += t[1][i][1] * (w * w * (3 - 2 * w))
        for v, d in delta.items():
            v.co += d
            v.co[ax] = value
        return len(delta), len(ends) - len(delta)


# --------------------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------------------
class KitBuilder:
    def __init__(self, G=None):
        self.S = Source()
        self.dims = measure(self.S, G)
        self.layer, self.group, self.cuttable = classify(self.S, self.dims)
        self.defs = build_defs(self.dims)
        self.parts = []           # (PieceDef, bmesh in world space)
        self.uncapped = {}        # base -> uncapped world-space bmesh of the first run piece
        self.synth_parts = []
        self.report = defaultdict(list)

    def groups_in_layer(self, L):
        g = defaultdict(list)
        for i, lay in enumerate(self.layer):
            if lay == L:
                g[self.group[i]].append(i)
        return g

    def box(self, ids):
        info = self.S.info
        lo = Vector([min(info[i]["lo"][k] for i in ids) for k in range(3)])
        hi = Vector([max(info[i]["hi"][k] for i in ids) for k in range(3)])
        return lo, hi

    def faces_of(self, ids):
        return [f for i in ids for f in self.S.isl_faces[i]]

    def assign(self, pdef, groups):
        """Groups that belong to the region, and the planes that must cut each of them."""
        graze = CONFIG["graze"]
        take, cuts = [], defaultdict(set)
        nudge = Vector((1e-5, 2e-5, 3e-5))      # tie-break for parts centred on a cut
        for gid, ids in groups.items():
            lo, hi = self.box(ids)
            cen = (lo + hi) * 0.5 + nudge
            ok, mine = True, []
            for pl in pdef.planes:
                dlo, dhi = sorted((pl.d(lo), pl.d(hi)))
                if dlo >= -graze:
                    continue
                if dhi <= graze:
                    ok = False
                    break
                if self.cuttable[gid]:
                    mine.append(pl)
                elif pl.d(cen) < 0:
                    ok = False
                    break
            if ok:
                take.append(gid)
                for pl in mine:
                    cuts[id(pl)].update(ids)
        return take, cuts

    def build(self):
        S = self.S
        ref = CONFIG["seam_ref"]
        for L in (WALL, LOWER, UPPER, RIDGE):
            groups = self.groups_in_layer(L)
            bm = append_faces(new_bm(), S.bm, self.faces_of([i for ids in groups.values() for i in ids]))
            if L == WALL:
                profiles = {0: Profile(bm, 0, ref["walls_x"]), 1: Profile(bm, 1, ref["walls_y"])}
            else:
                profiles = {0: Profile(bm, 0, ref["roof_x"])}
            self.layer_bm = bm                 # for exact seam normals on non-modular cuts
            self.sections = {}
            self.build_layer(L, groups, profiles)
            bm.free()
        self.build_items()
        self.name_parts()
        self.synth()

    def build_layer(self, L, groups, profiles):
        S = self.S
        layer_area = sum(S.info[i]["area"] for ids in groups.values() for i in ids)
        got_area = 0.0
        claimed = defaultdict(int)
        for pdef in (d for d in self.defs if d.layer == L):
            take, cuts = self.assign(pdef, groups)
            if not take:
                self.report["empty"].append(pdef.base)
                continue
            for g in take:
                claimed[g] += 1
            bm = append_faces(new_bm(), S.bm, self.faces_of([i for g in take for i in groups[g]]))
            for pl in pdef.planes:
                if cuts.get(id(pl)):
                    bisect_keep(bm, pl, cuts[id(pl)])
            if L == RIDGE and pdef.kind == "run":
                self.respace_ridge_sticks(bm, pdef, groups)
            for pl in pdef.planes:
                if not cuts.get(id(pl)):
                    continue
                if pl.axis in pdef.seam_axes:
                    _, miss = profiles[pl.axis].conform(bm, pl.value, pl.sign)
                    if miss:
                        self.report["seam_unmatched"].append(
                            "%s at %s=%.2f: %d verts" % (pdef.base, "xy"[pl.axis], pl.value, miss))
                else:
                    key = (pl.axis, round(pl.value, 6))
                    if key not in self.sections:
                        self.sections[key] = Profile(self.layer_bm, pl.axis, pl.value)
                    self.sections[key].assign_normals(bm, pl.value)
            if pdef.kind == "run" and pdef.base not in self.uncapped:
                self.uncapped[pdef.base] = (pdef, bm.copy())
            for pl in pdef.planes:
                if cuts.get(id(pl)):
                    fill_caps(bm, pl.co, pl.no, -pl.no)
            cap = bm.faces.layers.int["cap"]
            got_area += sum(f.calc_area() for f in bm.faces if not f[cap])
            self.parts.append((pdef, bm))
        lost = [g for g in groups if claimed[g] == 0]
        if lost:
            self.report["unassigned"].append("%s: %d groups -> %s_Leftover" % (LAYER_NAMES[L], len(lost), LAYER_NAMES[L]))
            pdef = PieceDef(L, LAYER_NAMES[L] + "_Leftover", [], (0, 0, 0), "item")
            self.parts.append((pdef, append_faces(new_bm(), S.bm, self.faces_of([i for g in lost for i in groups[g]]))))
        dup = [g for g in groups if claimed[g] > 1 and not self.cuttable[g]]
        if dup:
            self.report["duplicated"].append("%s: %d whole groups in >1 piece" % (LAYER_NAMES[L], len(dup)))
        self.report["coverage"].append("%-8s source %7.3f m2 -> pieces %7.3f m2 (%+.2f%%)"
                                       % (LAYER_NAMES[L], layer_area, got_area,
                                          100 * (got_area - layer_area) / max(layer_area, 1e-9)))

    def respace_ridge_sticks(self, bm, pdef, groups):
        """Replace the crossed stick pairs of a ridge straight with evenly spaced copies."""
        S = self.S
        sticks = defaultdict(list)
        for g, ids in groups.items():
            n = S.info[ids[0]]
            lo, hi = self.box(ids)
            if n["mat"] == "Wood_Pole" and (hi.x - lo.x) < 0.3:
                sticks[g] = ids
        if not sticks:
            return
        tmpl = min(sticks.values(), key=lambda ids: abs(sum(self.box(ids)[k].x for k in (0, 1))))
        lo, hi = self.box(tmpl)
        cx = (lo.x + hi.x) * 0.5
        all_ids = {i for ids in sticks.values() for i in ids}
        isl = bm.faces.layers.int["isl"]
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f[isl] in all_ids], context="FACES")
        a = pdef.meta["span"][0]
        for s in CONFIG["ridge_stick_slots"]:
            append_faces(bm, S.bm, self.faces_of(tmpl), offset=Vector((a + s - cx, 0, 0)))

    # ---- beams + props ------------------------------------------------------------------
    def build_items(self):
        S = self.S
        for gid, ids in sorted(self.groups_in_layer(BEAM).items(), key=lambda kv: self.box(kv[1])[0].x):
            lo, hi = self.box(ids)
            piv = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
            self.parts.append((PieceDef(BEAM, "Frame_TieBeam", [], piv, "item"),
                               append_faces(new_bm(), S.bm, self.faces_of(ids))))
        buckets = defaultdict(list)
        for i, lay in enumerate(self.layer):
            if lay == MISC:
                buckets["Misc"].append(i)
                self.report["misc"].append("%s island @ %s" % (S.info[i]["mat"], tuple(round(v, 2) for v in S.info[i]["c"])))
            if lay != PROP:
                continue
            n = S.info[i]
            c = n["c"]
            for name, mats, bx, _ in PROPS:
                if n["mat"] not in mats:
                    continue
                if bx and not (bx[0] <= c.x <= bx[1] and bx[2] <= c.y <= bx[3] and bx[4] <= c.z <= bx[5]):
                    continue
                if name in SINGLE_ISLAND_PROPS and buckets[name]:
                    continue
                buckets[name].append(i)
                break
            else:
                buckets["Misc"].append(i)
                self.report["misc"].append("prop %s island @ %s" % (n["mat"], tuple(round(v, 2) for v in c)))
        pivots = {p[0]: p[3] for p in PROPS}
        for name in [p[0] for p in PROPS] + ["Misc"]:
            ids = buckets.get(name)
            if not ids:
                if name != "Misc":
                    self.report["empty"].append("Prop_" + name)
                continue
            lo, hi = self.box(ids)
            top = pivots.get(name) == "top"
            piv = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z if top else lo.z))
            self.parts.append((PieceDef(PROP, "Prop_" + name, [], piv, "item"),
                               append_faces(new_bm(), S.bm, self.faces_of(ids))))

    # ---- names --------------------------------------------------------------------------
    def name_parts(self):
        """Name the openings and number the repeats."""
        woven = {i for i, m in enumerate(self.S.mats) if m and m.name == "Mat_Woven"}
        for pdef, bm in self.parts:
            if pdef.layer == WALL and pdef.kind == "run" and any(f.material_index in woven for f in bm.faces):
                side = pdef.meta["side"]
                a, b = pdef.meta["span"]
                pdef.base = "Wall_%s_%s_%s" % (side, "Door" if side == "W" else "Window", fmt_len(b - a))
            elif pdef.base == "Roof_End_W":
                pdef.base = "Roof_End_W_Door"
            elif pdef.base.startswith("RoofTop_End_"):
                pdef.base = pdef.base.replace("RoofTop_End_", "RoofTop_Gable_")
        seen = defaultdict(int)
        for pdef, _ in self.parts:
            seen[pdef.base] += 1
            pdef.name = "PT_%s_%02d" % (pdef.base, seen[pdef.base])
            pdef.kit = seen[pdef.base] == 1

    # ---- synthetic concave pieces -------------------------------------------------------
    def synth(self):
        """Inner wall corner and roof valley: lay copies of a straight run end to end, mitre
        them at 45 deg through the outer wall corner, and mirror across the mitre."""
        Y0 = self.dims["Y0"]
        arm = CONFIG["synth_arm"]
        for src_base, new_base, layer in (("Wall_S_1m", "Wall_Corner_Inner", WALL),
                                          ("Roof_S_1m", "Roof_Valley", LOWER)):
            if src_base not in self.uncapped:
                self.report["synth_skipped"].append(new_base)
                continue
            pdef_src, src = self.uncapped[src_base]
            a, b = pdef_src.meta["span"]
            L = b - a
            loc = src.copy()                       # local: outer wall line y = 0, start x = 0
            bmesh.ops.translate(loc, verts=loc.verts, vec=Vector((-a, Y0, 0)))
            depth = max(v.co.y for v in loc.verts)
            k = max(1, math.ceil(depth / L))
            run = new_bm()
            for i in range(-k, 1):                 # extra copies: only the continuous parts
                append_faces(run, loc, list(loc.faces), offset=Vector((i * L, 0, 0)), only_cut=i < 0)
            seam = [v for v in run.verts if abs(v.co.x / L - round(v.co.x / L)) * L < 1e-4
                    and -k * L + 1e-4 < v.co.x < L - 1e-4]
            bmesh.ops.remove_doubles(run, verts=seam, dist=2e-3)
            diag = Vector((1, 1, 0)).normalized()
            bisect_all(run, Vector((0, 0, 0)), -diag)          # keep x + y >= 0
            bisect_all(run, Vector((arm, 0, 0)), Vector((1, 0, 0)))
            out = new_bm()
            append_faces(out, run, list(run.faces))
            append_faces(out, run, list(run.faces), mirror_diag=True)
            bmesh.ops.remove_doubles(out, verts=[v for v in out.verts if abs(v.co.x + v.co.y) < 1e-4], dist=2e-3)
            fill_caps(out, Vector((arm, 0, 0)), Vector((1, 0, 0)), Vector((1, 0, 0)))
            fill_caps(out, Vector((0, -arm, 0)), Vector((0, 1, 0)), Vector((0, -1, 0)))
            run.free()
            loc.free()
            pdef = PieceDef(layer, new_base, [], (0, 0, 0), "synth")
            pdef.name = CONFIG["prefix"] + "_" + new_base
            pdef.kit = True
            self.synth_parts.append((pdef, out))


# --------------------------------------------------------------------------------------
# Objects, kit layout, demo
# --------------------------------------------------------------------------------------
CATEGORY_ROWS = [
    ("Walls", lambda b: b.startswith("Wall_") and "Corner" not in b),
    ("Corners", lambda b: b.startswith("Wall_Corner")),
    ("Roof", lambda b: b.startswith("Roof_")),
    ("RoofTop + Ridge", lambda b: b.startswith("RoofTop") or b.startswith("Ridge")),
    ("Frame", lambda b: b.startswith("Frame")),
    ("Props", lambda b: b.startswith("Prop")),
]


def to_mesh(name, bm, pivot, mats):
    bm = bm.copy()
    bmesh.ops.translate(bm, verts=bm.verts, vec=-pivot)
    # quads and tris only (cutting a quad across a corner leaves a pentagon; FBX cannot
    # build tangents for n-gons)
    ngons = [f for f in bm.faces if len(f.verts) > 4]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
    used = sorted({f.material_index for f in bm.faces})
    remap = {m: i for i, m in enumerate(used)}
    for f in bm.faces:
        f.material_index = remap[f.material_index]
    sn = bm.verts.layers.float_vector["seam_n"]
    seam = {v.index: Vector(v[sn]) for v in bm.verts if Vector(v[sn]).length > 0.5}
    for lay in ("isl", "cap", "cut"):
        bm.faces.layers.int.remove(bm.faces.layers.int[lay])
    bm.verts.layers.float_vector.remove(sn)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in used:
        me.materials.append(mats[m])
    if seam:
        # seam vertices take the normal of the continuous surface so neighbouring pieces
        # shade as one; zero vectors keep Blender's own normal everywhere else
        loops = [(0.0, 0.0, 0.0)] * len(me.loops)
        for p in me.polygons:
            if not p.use_smooth:
                continue
            for li in p.loop_indices:
                n = seam.get(me.loops[li].vertex_index)
                if n is not None:
                    loops[li] = n[:]
        me.normals_split_custom_set(loops)
    return me


def tri_count(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def tag(ob, pdef, kit_name):
    ob["kit_type"] = kit_name
    ob["kit_layer"] = LAYER_NAMES[pdef.layer]
    ob["kit_kind"] = pdef.kind


def build():
    name = CONFIG["name"]
    gen = load_generator()
    ensure_sources(gen)
    G = None
    if gen is not None:
        try:
            G = gen["Geo"](gen["CONFIG"])
        except Exception as ex:  # a generator config that no longer matches the scene
            log("generator dimensions unavailable (%s) - measuring the mesh" % ex)
    for cn in (name + "_Parts", name + "_Kit", name + "_KitPreview"):
        purge_collection(cn)
    kb = KitBuilder(G)
    kb.build()
    mats = kb.S.mats

    root = bpy.data.collections.get(name) or bpy.context.scene.collection
    col_parts = collection(name + "_Parts", root)
    col_kit = collection(name + "_Kit", root)
    col_prev = collection(name + "_KitPreview", root)

    parts, kit = [], []
    for pdef, bm in kb.parts:
        kit_name = CONFIG["prefix"] + "_" + pdef.base
        me = to_mesh(kit_name if pdef.kit else pdef.name, bm, pdef.pivot, mats)
        ob = bpy.data.objects.new(pdef.name, me)
        ob.location = pdef.pivot
        tag(ob, pdef, kit_name)
        col_parts.objects.link(ob)
        parts.append((pdef, ob))
        if pdef.kit:
            kob = bpy.data.objects.new(kit_name, me)
            tag(kob, pdef, kit_name)
            col_kit.objects.link(kob)
            kit.append((pdef, kob))
        bm.free()
    for pdef, bm in kb.synth_parts:
        me = to_mesh(pdef.name, bm, Vector((0, 0, 0)), mats)
        kob = bpy.data.objects.new(pdef.name, me)
        tag(kob, pdef, pdef.name)
        col_kit.objects.link(kob)
        kit.append((pdef, kob))
        bm.free()
    for _, (_, bm) in kb.uncapped.items():
        bm.free()

    layout_kit(kit, col_prev)
    build_demo(parts, kit, col_prev)

    # hide the merged source meshes (and their LODs / collision) so the parts are visible
    for cn in (name + "_LOD0", name + "_LODs", name + "_Collision"):
        col = bpy.data.collections.get(cn)
        for ob in (col.objects if col else ()):
            ob.hide_render = True
            try:
                ob.hide_set(True)
            except RuntimeError:
                pass
    print_report(kb, parts, kit)
    return kb, parts, kit


def layout_kit(kit, col_prev):
    """Lay the kit out in labelled rows (one category per row, wrapping), each piece resting
    on the ground.  Only the object location changes; the mesh pivot stays on z = 0."""
    ox, oy = CONFIG["layout_origin"]
    gap, wrap = CONFIG["layout_gap"], CONFIG["layout_row_width"]
    placed, rows = set(), []
    for title, pred in CATEGORY_ROWS:
        items = [(p, o) for p, o in kit if pred(p.base) and o.name not in placed]
        placed.update(o.name for _, o in items)
        rows.append(items)
    rows.append([(p, o) for p, o in kit if o.name not in placed])
    mat = bpy.data.materials.get("KitLabel") or bpy.data.materials.new("KitLabel")
    mat.diffuse_color = (0.02, 0.02, 0.02, 1)
    if mat.node_tree and mat.node_tree.nodes.get("Principled BSDF"):
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1)
    row_y = oy
    for items in rows:
        if not items:
            continue
        x, depth = ox, 0.0
        for pdef, ob in items:
            vs = [v.co for v in ob.data.vertices]
            lo = Vector([min(v[k] for v in vs) for k in range(3)])
            hi = Vector([max(v[k] for v in vs) for k in range(3)])
            w = max(hi.x - lo.x, 0.075 * len(pdef.base))
            if x > ox and x + w > ox + wrap:            # wrap to a new line
                row_y += depth + gap + 0.5
                x, depth = ox, 0.0
            ob.location = Vector((x - lo.x, row_y - lo.y, -lo.z))
            depth = max(depth, hi.y - lo.y)
            cu = bpy.data.curves.new("LBL_" + pdef.base, "FONT")
            cu.body = pdef.base.replace("_", " ")
            cu.size = 0.13
            cu.materials.append(mat)
            lb = bpy.data.objects.new("LBL_" + pdef.base, cu)
            lb.location = (x, row_y - 0.3, 0.005)
            col_prev.objects.link(lb)
            x += w + gap
        row_y += depth + gap + 0.9


def build_demo(parts, kit, col_prev):
    """A longer house: the east half moved along by n metres and n x 1 m straights
    inserted at x = 0, all as linked duplicates of the kit, to show the pieces snap."""
    n = CONFIG["demo_extra"]
    if n <= 0:
        return
    dx, dy = CONFIG["demo_offset"]
    col = collection(CONFIG["name"] + "_KitDemo", col_prev)
    kit_by_base = {p.base: o for p, o in kit}

    def inst(data, loc, nm):
        ob = bpy.data.objects.new("DEMO_" + nm, data)
        ob.location = loc
        col.objects.link(ob)

    for pdef, ob in parts:
        if pdef.layer == PROP:
            continue
        span = pdef.meta.get("span")
        if span is not None and pdef.meta.get("axis") == 0:
            east = span[0] >= -1e-6
        else:
            east = pdef.pivot.x > 1e-3
        inst(ob.data, pdef.pivot + Vector((n if east else 0, 0, 0)) + Vector((dx, dy, 0)), pdef.name)
    for base in ("Wall_S_1m", "Wall_N_1m", "Roof_S_1m", "Roof_N_1m", "RoofTop_1m", "Ridge_1m"):
        src = next((p for p, _ in parts if p.base == base), None)
        if src is None:
            continue
        for i in range(n):
            inst(kit_by_base[base].data, Vector((i + dx, src.pivot.y + dy, 0)), "%s_add%d" % (base, i))


def seam_gap(me, axis, length):
    """Tiling check for a run piece in local space: how far (m) the vertices on one end
    lie from the cross-section outline of the other end, worst case over both ends."""
    def outline(val):
        ids = {i for i, v in enumerate(me.vertices) if abs(v.co[axis] - val) < 1e-4}
        segs = [tuple(e.vertices) for e in me.edges if e.vertices[0] in ids and e.vertices[1] in ids]
        flat = {i: Vector([0.0 if k == axis else me.vertices[i].co[k] for k in range(3)]) for i in ids}
        return flat, segs

    def dist(q, flat, segs):
        best = min(((flat[i] - q).length for i in flat), default=1e9)
        for a, b in segs:
            pa, ab = flat[a], flat[b] - flat[a]
            if ab.length_squared > 1e-12:
                t = max(0.0, min(1.0, (q - pa).dot(ab) / ab.length_squared))
                best = min(best, (pa + ab * t - q).length)
        return best

    A, B = outline(0.0), outline(length)
    if not A[0] or not B[0]:
        return None
    return max(max(dist(q, *B) for q in A[0].values()), max(dist(q, *A) for q in B[0].values()))


def print_report(kb, parts, kit):
    log("-" * 78)
    log("%-40s %6s %4s  %s" % ("kit piece", "tris", "mats", "size x * y * z (m)"))
    total = 0
    for pdef, ob in kit:
        vs = [v.co for v in ob.data.vertices]
        size = [max(v[k] for v in vs) - min(v[k] for v in vs) for k in range(3)]
        t = tri_count(ob.data)
        total += t
        log("%-40s %6d %4d  %.2f x %.2f x %.2f" % (ob.name, t, len(ob.data.materials), *size))
    log("-" * 78)
    log("%d parts make up the house, %d unique kit pieces, kit total %d tris" % (len(parts), len(kit), total))
    gaps = []
    for pdef, ob in kit:
        if pdef.kind == "run":
            a, b = pdef.meta["span"]
            g = seam_gap(ob.data, pdef.meta["axis"], b - a)
            if g is not None:
                gaps.append((g, pdef.base))
    if gaps:
        g, worst = max(gaps)
        log("tiling check   %d straights: worst end-to-end mismatch %.1f mm (%s)" % (len(gaps), g * 1000, worst))
    for key in ("coverage", "unassigned", "duplicated", "empty", "seam_unmatched", "misc", "synth_skipped"):
        for line in kb.report.get(key, []):
            log("%-14s %s" % (key, line))


# --------------------------------------------------------------------------------------
# Export, manifest, preview renders
# --------------------------------------------------------------------------------------
def kit_objects():
    col = bpy.data.collections.get(CONFIG["name"] + "_Kit")
    return sorted(col.objects, key=lambda o: o.name) if col else []


def select_only(obs):
    vl = bpy.context.view_layer
    vl.update()
    for o in bpy.context.scene.objects:
        try:
            o.select_set(False)
        except RuntimeError:
            pass
    for o in obs:
        o.hide_set(False)
        o.select_set(True)
    if obs:
        vl.objects.active = obs[0]


def export_file(path, obs):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    select_only(obs)
    if path.lower().endswith(".fbx"):
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, apply_scale_options="FBX_SCALE_UNITS",
                                 mesh_smooth_type="FACE", use_tspace=True, use_custom_props=True,
                                 object_types={"MESH"})
    else:
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_extras=True)
    return path


def export_dir(dirpath, fmt="glb"):
    """One file per kit piece, each with its pivot at the file origin."""
    n = 0
    for ob in kit_objects():
        old = ob.location.copy()
        ob.location = (0, 0, 0)
        export_file(os.path.join(dirpath, ob.name + "." + fmt), [ob])
        ob.location = old
        n += 1
    log("exported %d pieces to %s" % (n, dirpath))


def write_manifest(path):
    rows = []
    for ob in kit_objects():
        vs = [v.co for v in ob.data.vertices]
        rows.append({"name": ob.name, "layer": ob.get("kit_layer"), "kind": ob.get("kit_kind"),
                     "bounds_min": [round(min(v[k] for v in vs), 3) for k in range(3)],
                     "bounds_max": [round(max(v[k] for v in vs), 3) for k in range(3)],
                     "tris": tri_count(ob.data), "materials": [m.name for m in ob.data.materials if m]})
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"kit": CONFIG["name"], "units": "metres, Z up",
                   "pivots": "all on z=0; see ainu_chise_kit.py docstring", "pieces": rows}, fh, indent=1)
    log("manifest", path)


def _camera():
    cam = bpy.data.objects.get("KitPreview_Camera")
    if cam is None:
        cam = bpy.data.objects.new("KitPreview_Camera", bpy.data.cameras.new("KitPreview_Camera"))
        collection(CONFIG["name"] + "_KitPreview").objects.link(cam)
    bpy.context.scene.camera = cam
    return cam


def _fit(cam, obs, direction, ortho, lens=35, margin=1.06):
    sc = bpy.context.scene
    bpy.context.view_layer.update()       # matrix_world is stale right after moving objects
    pts = [o.matrix_world @ Vector(c) for o in obs if o.type == "MESH" for c in o.bound_box]
    ctr = sum(pts, Vector()) / len(pts)
    d = Vector(direction).normalized()
    rot = (-d).to_track_quat("-Z", "Y")
    cam.rotation_euler = rot.to_euler()
    right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    xs = [(p - ctr).dot(right) for p in pts]
    ys = [(p - ctr).dot(up) for p in pts]
    ctr += right * (max(xs) + min(xs)) / 2 + up * (max(ys) + min(ys)) / 2
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    span = max(w, h * sc.render.resolution_x / sc.render.resolution_y) * margin
    cam.data.clip_end = 300
    if ortho:
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = span
        cam.location = ctr + d * 60
    else:
        cam.data.type = "PERSP"
        cam.data.lens = lens
        depth = max((p - ctr).dot(d) for p in pts)
        cam.location = ctr + d * (depth + span / 2 / math.tan(math.atan(18 / lens)))


def render(view, path, samples=None):
    sc = bpy.context.scene
    name = CONFIG["name"]
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples or CONFIG["samples"]
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = 1600, 900
    parts = bpy.data.collections[name + "_Parts"]
    kit = bpy.data.collections[name + "_Kit"]
    prev = bpy.data.collections[name + "_KitPreview"]
    demo = bpy.data.collections.get(name + "_KitDemo")
    state = {c: c.hide_render for c in (parts, kit, prev, demo) if c}
    parts.hide_render = view != "parts"
    kit.hide_render = view != "kit"
    prev.hide_render = view == "parts"
    if demo:
        demo.hide_render = view != "demo"
    for o in prev.objects:
        o.hide_render = view != "kit"
    cam = _camera()
    moved = []
    if view == "parts":
        # exploded view: walls pushed out, roof layers lifted
        lift = {WALL: 0.0, LOWER: 1.0, UPPER: 2.1, RIDGE: 3.0}
        push = {WALL: 0.7, LOWER: 1.1, UPPER: 0.6, RIDGE: 0.6}
        for o in parts.objects:
            L = LAYER_NAMES.index(o["kit_layer"])
            if L not in lift:
                continue
            vs = o.data.vertices
            c = o.matrix_world @ (sum((v.co for v in vs), Vector()) / max(1, len(vs)))
            dv = Vector((c.x / 2.5, c.y / 2.0, 0))
            if dv.length > 1e-3:
                dv.normalize()
            moved.append((o, o.location.copy()))
            o.location += dv * push[L] + Vector((0, 0, lift[L]))
        _fit(cam, parts.objects, (-1.0, -1.3, 0.75), ortho=False)
    elif view == "kit":
        _fit(cam, kit.objects, (-0.15, -0.55, 1.0), ortho=True, margin=1.03)
    else:
        _fit(cam, demo.objects, (-1.0, -1.15, 0.5), ortho=False)
    sc.render.filepath = os.path.abspath(path)
    os.makedirs(os.path.dirname(sc.render.filepath), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    for o, loc in moved:
        o.location = loc
    for c, v in state.items():
        c.hide_render = v
    for o in prev.objects:
        o.hide_render = False
    log("rendered", view, "->", sc.render.filepath)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
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


def main():
    opts = _cli_args()
    if "samples" in opts:
        CONFIG["samples"] = int(opts["samples"])
    build()
    for view in ("parts", "kit", "demo"):
        if "render-" + view in opts:
            render(view, opts["render-" + view])
    if "export" in opts:
        log("exported", export_file(opts["export"], kit_objects()))
    if "export-dir" in opts:
        export_dir(opts["export-dir"], opts.get("format", "glb"))
    if "manifest" in opts:
        write_manifest(opts["manifest"])
    if "save" in opts:
        p = os.path.abspath(opts["save"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=p, copy=True)
        log("saved", p)


if __name__ == "__main__":
    main()
