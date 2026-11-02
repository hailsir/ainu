"""
ainu_village_scene.py - coastal Ainu kotan (village) for Godot 4, built from the chise kit (Blender 5.0)
======================================================================================================
Builds the concept scene: a beach village under a green coastal mountain range. There are chise
houses of two lengths (assembled from the modular kit in ainu_chise_kit.py), raised pu
storehouses, fish drying racks, a fire pit with a hanging pot, firewood, baskets, leaning pole
bundles, a foreground fence, dugout boats, rocks, a terrain with a splat map, and the sea. There
are no characters.

Run headless (from this folder):

    blender --background --factory-startup --python ainu_village_scene.py -- \
        --godot-dir out/godot/ainu_village --render out/village.png --save out/ainu_village.blend

Interactive: open this file in the Scripting workspace and click Run Script. Re-running only
replaces this script's own collections (AinuVillage*) and the kit's collections.

| option | what it does |
|---|---|
| `--tex-src dir` | the ainu_atlas_derived_PBR_4K folder (default: searched next to the repo) |
| `--tex-res n` | game texture size (default 2048; 4096 keeps the source resolution) |
| `--force-textures` | rebuild the game textures even if they are up to date |
| `--godot-dir dir` | glTF + textures + shaders + materials + import script + main scene for Godot |
| `--godot-res-dir res://path` | where that folder will live in the Godot project (default res://ainu_village) |
| `--export-prefabs` | also one .gltf per prefab (houses, pu, racks, ...) in <godot-dir>/prefabs |
| `--interiors` | 5 m houses get the kit's interior props (floor, hearth, mats, rack, shelf) |
| `--no-flora` | skip the grass and plants (ainu_flora.py) |
| `--render file.png` | Cycles render from the concept camera |
| `--samples n` | Cycles samples (default 32) |
| `--save file.blend` | save a copy of the scene |

Game-readiness choices (see README_Godot.md, written into --godot-dir):
  * One merged mesh per prefab (houses: about 60 kit pieces each -> 1 mesh, 4-5 surfaces). Every
    instance shares the prefab mesh, and glTF / Godot keep that sharing.
  * Materials: Principled BSDF wired the way the glTF exporter understands. Albedo goes to base
    colour, the normal map goes through a Normal Map node, and the ORM texture (R = AO,
    G = roughness, B = metallic) goes through Separate Color and the "glTF Material Output"
    Occlusion socket. Godot imports them as StandardMaterial3D with all maps.
  * Texture tiling is baked into the UVs (metres per tile per material), so there are no
    Mapping nodes or KHR_texture_transform.
  * Collision uses Godot import hints. `Terrain-col` gets a trimesh. Every building, rack, boat
    and rock has convex `*-convcolonly` children.
  * Terrain splat weights are in COLOR_0 (R sand, G earth, B grass, A rock). The terrain and sea
    get the bundled Godot shaders through the post-import script village_import.gd.
  * Smoke markers are `FX_Smoke_*` empties. The import script turns them into GPUParticles3D.
  * Godot generates mesh LODs on import, and small props get visibility ranges.
  * Grass and Rishiri flora come from ainu_flora.py, which paints its own foliage atlas. They are
    merged into 16 m chunks (Flora_Grass_*, Flora_Plants_*), and Godot gives them a wind shader.

Texture notes (found while preparing the atlas maps):
  * The atlas swatch named "weathered_timber" is really the bound reed wall, and "woven_reed" is
    split-log timber with a reed strip on its left edge. The outputs are named for what they
    show (T_ReedWall_*, T_Timber_*).
  * packed_earth, coastal_sand, coastal_water and indigo_textile carry strips of neighbouring
    swatches. They are cropped to a clean square and made tileable again (offset cross-fade).
  * The estimated normal maps are very flat, so they are boosted and renormalised. They stay
    OpenGL (+Y), which is Godot's convention, so no green-channel flip is needed.
"""

import bpy
import bmesh
import json
import math
import os
import runpy
import sys
import numpy as np
from mathutils import Vector, Matrix

# --------------------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------------------
CONFIG = {
    "name": "AinuVillage",
    "prefix": "SM_AV",
    "seed": 23,
    "kit_script": "ainu_chise_kit.py",
    "tex_src_name": "ainu_atlas_derived_PBR_4K",
    "tex_res": 2048,
    "godot_res_dir": "res://ainu_village",
    "interiors": False,
    "samples": 32,
    # terrain: one grid, dense in the village and coarse towards the mountains (sinh spacing)
    # terrain: one grid, ~1.5 m cells in the village growing (sinh) to ~45 m at the mountains.
    # (extent m, cells) per side: west, east, south, north
    "terrain_x": ((1400.0, 50), (600.0, 36)),
    "terrain_y": ((400.0, 26), (1550.0, 84)),
    "terrain_cell": 1.5,
    "sea_size": 6000.0,
    "sun_dir": (-0.62, -0.42, 0.66),   # towards the sun (afternoon, over the sea)
}

# Game textures: output name -> atlas folder, clean crop (u0, u1, v0, v1; v from the top),
# normal boost, whether to make it tileable again after cropping.
TEXTURES = {
    "Thatch":   dict(src="thatch_reed",      crop=None,                     nboost=2.5),
    "ReedWall": dict(src="weathered_timber", crop=None,                     nboost=2.5),
    "Timber":   dict(src="woven_reed",       crop=(0.26, 0.94, 0.0, 1.0),   nboost=2.5),
    "Earth":    dict(src="packed_earth",     crop=(0.0, 1.0, 0.19, 0.88),   nboost=2.0),
    "Sand":     dict(src="coastal_sand",     crop=(0.0, 1.0, 0.19, 0.90),   nboost=2.0),
    "Stone":    dict(src="mossy_stone",      crop=None,                     nboost=2.0),
    "Textile":  dict(src="indigo_textile",   crop=(0.0, 0.86, 0.0, 0.86),   nboost=1.5, tile=False),
    "Water":    dict(src="coastal_water",    crop=(0.0, 1.0, 0.07, 0.96),   nboost=1.5),
}

# Village materials. tile = metres per texture repeat (baked into the UVs).
MATERIALS = {
    "M_Thatch":      dict(tex="Thatch",   tile=1.6),
    "M_ReedWall":    dict(tex="ReedWall", tile=1.5),
    "M_ReedMat":     dict(tex="ReedWall", tile=0.7, tint="#d8cdb4"),
    "M_Timber":      dict(tex="Timber",   tile=1.2),
    "M_TimberDark":  dict(tex="Timber",   tile=1.2, tint="#57504a"),
    "M_TimberLight": dict(tex="Timber",   tile=1.2, tint="#f2e6d0"),
    "M_Earth":       dict(tex="Earth",    tile=2.5),
    "M_Ash":         dict(tex="Earth",    tile=1.0, tint="#9a9894"),
    "M_Rock":        dict(tex="Stone",    tile=3.0),     # big tile: one boulder face, not a wall of cobbles
    "M_Textile":     dict(tex="Textile",  tile=1.0),
    "M_Water":       dict(tex="Water",    tile=8.0, rough=0.1, tint="#3d6f92"),
    "M_Fish":        dict(color="#5d6366", rough=0.4),
    "M_Iron":        dict(color="#1e1c1a", rough=0.55, metal=0.85),
    "M_Lacquer":     dict(color="#3a0e09", rough=0.25),
    "M_Embers":      dict(color="#1a0602", rough=0.9, emission="#ff6a1a", strength=4.0),
}
# Kit (procedural) material -> village material
KIT_MAT_MAP = {
    "Thatch_Roof": "M_Thatch", "Thatch_Wall": "M_ReedWall", "Wood_Pole": "M_Timber",
    "Wood_Smoked": "M_TimberDark", "Wood_Light": "M_TimberLight", "Gable_Vent": "M_TimberDark",
    "Earth_Floor": "M_Earth", "Mat_Woven": "M_ReedMat", "Mat_Pattern": "M_Textile",
    "Ash": "M_Ash", "Embers": "M_Embers", "Iron": "M_Iron", "Lacquer": "M_Lacquer",
}

# Layout: prefab, object name, x, y, heading (deg). +X east, +Y north, the sea is to the west.
# Positions come from the concept's framing: each object's place across the frame and its
# distance from CAMERA, so the render lines up with the concept painting.
# A position can also be ("@<placed object>", local x, local y): the heading is then relative too.
LAYOUT = [
    ("Chise_7m",   "Chise_Main",   2.4, -11.2,  43),
    ("Chise_5m",   "Chise_02",    13.6,   0.1,  36),
    ("Chise_5m",   "Chise_03",    -0.2,  16.0,  35),
    ("Chise_7m",   "Chise_04",    21.5,  -8.5,  32),
    ("Chise_5m",   "Chise_05",   -12.2,  32.8,  41),
    ("Pu",         "Pu_01",       -6.4,  -5.8,  15),
    ("Pu",         "Pu_02",      -15.5,  11.2,  36),
    ("FirePit",    "FirePit_01",  -0.6, -17.5,   0),
    ("Firewood",   "Firewood_01",  5.9, -15.7, -12),
    ("FishRack_A", "FishRack_01",  5.5, -19.9, -43),
    ("FishRack_B", "FishRack_03", -8.0, -16.7,  41),
    ("FishRack_A", "FishRack_02", -11.5, -5.6,  45),
    ("LeanPoles",  "LeanPoles_01", 4.5, -23.3,   0),
    ("LeanPoles",  "LeanPoles_02", -14.5, -2.6, 40),
    ("Basket_A",   "Basket_01",   -1.8, -18.5,   0),
    ("Basket_B",   "Basket_02",    0.4, -17.6,   0),
    ("Basket_B",   "Basket_03",   -6.5, -16.7,   0),
    ("Basket_A",   "Basket_04",   ("@Chise_Main", -4.3, 0.9), 0),
    ("Screen",     "Screen_01",   ("@Chise_Main", -3.62, -1.3), -90),
    ("Screen",     "Screen_02",   ("@Chise_Main", -3.62, 1.35), -90),
    ("Boat",       "Boat_01",    -15.3, -15.6, 140),
    ("Boat",       "Boat_02",    -19.7,  -7.4, 155),
    ("Rock_C",     "Boulder_01",   1.5, -22.1,  30),
]
FENCE = [(-10.5, -21.5), (-4.9, -23.1), (-1.6, -24.4), (0.4, -26.0)]
CAMERA = dict(loc=(0.0, -30.0), height=4.2, heading=-8.0, pitch=-11.5, lens=21.0)
# Mountain ridge (x, y, height): headland in the sea on the left, main peak left of centre,
# green hills coming down to the village on the right
RIDGE = [(-1250, 1180, 40), (-1079, 1151, 90), (-725, 1167, 190), (-314, 1231, 250), (30, 970, 150),
         (132, 657, 75), (156, 338, 70), (135, 181, 60), (110, 90, 25)]

# Flat building pads (prefab -> half size x, y in local space)
PAD_SIZE = {"Chise_7m": (4.6, 3.2), "Chise_5m": (3.6, 3.2), "Pu": (1.6, 1.6), "FirePit": (1.5, 1.5),
            "Firewood": (1.0, 0.8), "FishRack_A": (2.2, 0.9), "FishRack_B": (1.3, 0.8)}

INF = 1e9


def log(*a):
    print("[village]", *a)


# --------------------------------------------------------------------------------------
# Paths, kit and generator
# --------------------------------------------------------------------------------------
def _script_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return bpy.path.abspath("//") or os.getcwd()


def find_tex_src(opt):
    here = _script_dir()
    cands = [opt] if opt else []
    cands += [os.path.join(here, *([".."] * k), CONFIG["tex_src_name"]) for k in range(4)]
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.abspath(c)
    raise RuntimeError("texture folder %s not found - pass --tex-src" % CONFIG["tex_src_name"])


def load_kit():
    """Run ainu_chise_kit.py as a library: builds the house (if needed) and the kit pieces."""
    path = os.path.join(_script_dir(), CONFIG["kit_script"])
    if not os.path.isfile(path):
        raise RuntimeError("%s not found next to this script" % CONFIG["kit_script"])
    K = runpy.run_path(path, run_name="ainu_chise_kit_lib")
    kb, parts, kit = K["build"]()
    gen = K["load_generator"]()
    root = bpy.data.collections.get(K["CONFIG"]["name"])
    if root:     # keep the kit in the file, but out of the village view layer and renders
        lc = _find_layer_collection(bpy.context.view_layer.layer_collection, root.name)
        if lc:
            lc.exclude = True
    return K, gen, parts, kit


def _find_layer_collection(lc, name):
    if lc.name == name:
        return lc
    for c in lc.children:
        r = _find_layer_collection(c, name)
        if r:
            return r
    return None


def collection(name, parent=None):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    parent = parent or bpy.context.scene.collection
    if c.name not in parent.children:
        parent.children.link(c)
    return c


def purge():
    name = CONFIG["name"]
    for c in list(bpy.data.collections):
        if c.name.startswith(name) or c.name == "_AV_tmp":
            for ob in list(c.objects):
                bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.collections.remove(c)
    for me in list(bpy.data.meshes):
        if me.users == 0 and (me.name.startswith(CONFIG["prefix"]) or me.name.startswith("_av")):
            bpy.data.meshes.remove(me)


# --------------------------------------------------------------------------------------
# Game textures (crop, re-tile, boost normals, pack ORM)
# --------------------------------------------------------------------------------------
def _load_px(path):
    im = bpy.data.images.load(path)
    im.colorspace_settings.name = "Non-Color"     # raw values, no transform
    w, h = im.size
    a = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a.reshape(h, w, 4)


def _resize(a, res):
    h, w = a.shape[:2]
    if (w, h) == (res, res):
        return a
    im = bpy.data.images.new("_av_rs", w, h, alpha=True, float_buffer=True)
    im.colorspace_settings.name = "Non-Color"
    im.pixels.foreach_set(a.ravel())
    im.scale(res, res)
    out = np.empty(res * res * 4, np.float32)
    im.pixels.foreach_get(out)
    bpy.data.images.remove(im)
    return out.reshape(res, res, 4)


def _crop(a, crop):
    """crop = (u0, u1, v0, v1) with v measured from the top; pixel rows run bottom-up."""
    if not crop:
        return a, ()
    h, w = a.shape[:2]
    u0, u1, v0, v1 = crop
    side = min((u1 - u0) * w, (v1 - v0) * h)          # largest clean square
    cx, cy = (u0 + u1) / 2 * w, (1 - (v0 + v1) / 2) * h
    x0, y0 = int(cx - side / 2), int(cy - side / 2)
    s = int(side)
    axes = tuple(ax for ax, cut in ((1, s < w), (0, s < h)) if cut)
    return a[y0:y0 + s, x0:x0 + s], axes


def _make_tileable(a, axes, blend=0.25):
    """Offset cross-fade, one axis at a time: the edges come from a half-rolled copy (which
    wraps), and the rolled copy's own seam sits in the middle where the original wins."""
    for ax in axes:
        n = a.shape[ax]
        t = np.arange(n, dtype=np.float32) / (n - 1)
        w = np.clip(np.minimum(t, 1 - t) / blend, 0, 1)
        w = w * w * (3 - 2 * w)
        shape = [1, 1, 1]
        shape[ax] = n
        w = w.reshape(shape)
        a = a * w + np.roll(a, n // 2, axis=ax) * (1 - w)
    return a


def _boost_normals(a, k):
    v = a[..., :3] * 2.0 - 1.0
    v[..., 0:2] *= k
    v[..., 2] = np.maximum(v[..., 2], 0.05)
    v /= np.linalg.norm(v, axis=2, keepdims=True)
    out = a.copy()
    out[..., :3] = v * 0.5 + 0.5
    out[..., 3] = 1.0
    return out


def _save_px(a, path, fmt, quality=92):
    h, w = a.shape[:2]
    im = bpy.data.images.new("_av_save", w, h, alpha=False, float_buffer=False)
    im.colorspace_settings.name = "Non-Color"
    px = np.clip(a, 0, 1).astype(np.float32)
    px[..., 3] = 1.0
    im.pixels.foreach_set(px.ravel())
    im.filepath_raw = path
    im.file_format = fmt
    im.save(quality=quality)
    bpy.data.images.remove(im)


def prepare_textures(src, out_dir, res, force=False):
    """Writes T_<Name>_BaseColor.jpg, T_<Name>_Normal.png and T_<Name>_ORM.png per material."""
    os.makedirs(out_dir, exist_ok=True)
    stamp_path = os.path.join(out_dir, "_prep.json")
    stamp = {"res": res, "textures": TEXTURES, "version": 2}
    info = {}
    for name in TEXTURES:
        info[name] = {k: os.path.join(out_dir, "T_%s_%s" % (name, s))
                      for k, s in (("albedo", "BaseColor.jpg"), ("normal", "Normal.png"), ("orm", "ORM.png"))}
    try:
        with open(stamp_path) as fh:
            same = json.load(fh) == json.loads(json.dumps(stamp))
    except (OSError, ValueError):
        same = False
    if same and not force and all(os.path.isfile(p) for d in info.values() for p in d.values()):
        log("game textures up to date in", out_dir)
        return info
    for name, spec in TEXTURES.items():
        folder = os.path.join(src, spec["src"])
        maps = {}
        for kind in ("albedo", "normal", "roughness", "ao"):
            a = _load_px(os.path.join(folder, "%s_%s_4k.jpg" % (spec["src"], kind)))
            a, axes = _crop(a, spec.get("crop"))
            a = _resize(np.ascontiguousarray(a), res)
            if axes and spec.get("tile", True):
                a = _make_tileable(a, axes)
            maps[kind] = a
        nrm = _boost_normals(maps["normal"], spec.get("nboost", 1.0))
        orm = np.ones_like(maps["albedo"])
        orm[..., 0] = maps["ao"][..., 0]
        orm[..., 1] = maps["roughness"][..., 0]
        orm[..., 2] = 0.0
        _save_px(maps["albedo"], info[name]["albedo"], "JPEG", 92)
        _save_px(nrm, info[name]["normal"], "PNG")
        _save_px(orm, info[name]["orm"], "PNG")
        mean = nrm[..., :3].reshape(-1, 3).mean(0)
        log("texture %-8s from %-16s crop=%s  normal mean=(%.2f %.2f %.2f)"
            % (name, spec["src"], spec.get("crop"), *mean))
    with open(stamp_path, "w") as fh:
        json.dump(stamp, fh, indent=1)
    return info


# --------------------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------------------
def srgb(hexstr):
    h = hexstr.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)


def _sock(sockets, ident):
    return next(s for s in sockets if s.identifier == ident)


def gltf_output_group():
    """The node group the glTF exporter reads the occlusion texture from."""
    ng = bpy.data.node_groups.get("glTF Material Output")
    if ng is None:
        ng = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
        ng.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
        ng.interface.new_socket("Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
    return ng


def _image(path, colorspace):
    im = bpy.data.images.load(path, check_existing=True)
    im.colorspace_settings.name = colorspace
    return im


def _tex_node(nt, path, colorspace, x, y):
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = _image(path, colorspace)
    n.extension = "REPEAT"
    n.location = (x, y)
    return n


def make_material(name, spec, tex):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if "tex" in spec:
        t = tex[spec["tex"]]
        alb = _tex_node(nt, t["albedo"], "sRGB", -600, 300)
        col = alb.outputs["Color"]
        if spec.get("tint"):
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
            mix.location = (-250, 300)
            _sock(mix.inputs, "Factor_Float").default_value = 1.0
            nt.links.new(col, _sock(mix.inputs, "A_Color"))
            _sock(mix.inputs, "B_Color").default_value = srgb(spec["tint"])
            col = _sock(mix.outputs, "Result_Color")
        nt.links.new(col, bsdf.inputs["Base Color"])
        nrm = _tex_node(nt, t["normal"], "Non-Color", -600, -300)
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (-250, -300)
        nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        orm = _tex_node(nt, t["orm"], "Non-Color", -600, 0)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (-250, 0)
        nt.links.new(orm.outputs["Color"], sep.inputs["Color"])
        if "rough" in spec:
            bsdf.inputs["Roughness"].default_value = spec["rough"]
        else:
            nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
        g = nt.nodes.new("ShaderNodeGroup")
        g.node_tree = gltf_output_group()
        g.location = (200, -450)
        nt.links.new(sep.outputs["Red"], g.inputs["Occlusion"])
    else:
        bsdf.inputs["Base Color"].default_value = srgb(spec["color"])
        bsdf.inputs["Roughness"].default_value = spec.get("rough", 0.8)
        bsdf.inputs["Metallic"].default_value = spec.get("metal", 0.0)
    if spec.get("emission"):
        bsdf.inputs["Emission Color"].default_value = srgb(spec["emission"])
        bsdf.inputs["Emission Strength"].default_value = spec.get("strength", 1.0)
    m["tile_m"] = spec.get("tile", 1.0)
    m.diffuse_color = srgb(spec.get("tint") or spec.get("color") or "#9a8a70")
    return m


def make_terrain_material(tex):
    """Blender preview of the splat terrain (the Godot side uses shaders/terrain_splat.gdshader).
    COLOR_0 = (sand, earth, grass, rock); grass is the earth texture tinted green."""
    name = "M_Terrain"
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    L = nt.links.new
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (1400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (1100, 0)
    L(bsdf.outputs["BSDF"], out.inputs["Surface"])
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "Splat"
    attr.location = (-900, 600)
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-700, 600)
    L(attr.outputs["Color"], sep.inputs["Color"])
    weights = [sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"], attr.outputs["Alpha"]]
    uv = nt.nodes.new("ShaderNodeTexCoord")
    uv.location = (-1300, 0)
    layers = [("Sand", 3.0, None), ("Earth", 2.5, None), ("Earth", 2.0, "#7e9a46"), ("Stone", 5.0, None)]
    acc = {"albedo": None, "normal": None, "rough": None}
    for i, (tname, tile, tint) in enumerate(layers):
        y = 400 - i * 450
        sc = nt.nodes.new("ShaderNodeVectorMath")
        sc.operation = "SCALE"
        sc.location = (-1100, y)
        L(uv.outputs["UV"], sc.inputs[0])
        sc.inputs["Scale"].default_value = 1.0 / tile
        t = tex[tname]
        a = _tex_node(nt, t["albedo"], "sRGB", -900, y)
        n = _tex_node(nt, t["normal"], "Non-Color", -900, y - 150)
        o = _tex_node(nt, t["orm"], "Non-Color", -900, y - 300)
        for node in (a, n, o):
            L(sc.outputs["Vector"], node.inputs["Vector"])
        col = a.outputs["Color"]
        if tint:
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
            mix.location = (-650, y)
            _sock(mix.inputs, "Factor_Float").default_value = 1.0
            L(col, _sock(mix.inputs, "A_Color"))
            _sock(mix.inputs, "B_Color").default_value = srgb(tint)
            col = _sock(mix.outputs, "Result_Color")
        osep = nt.nodes.new("ShaderNodeSeparateColor")
        osep.location = (-650, y - 300)
        L(o.outputs["Color"], osep.inputs["Color"])
        for key, sock in (("albedo", col), ("normal", n.outputs["Color"]), ("rough", osep.outputs["Green"])):
            w = nt.nodes.new("ShaderNodeVectorMath")
            w.operation = "SCALE"
            w.location = (-400 + 150 * ("albedo", "normal", "rough").index(key), y)
            L(sock, w.inputs[0])
            L(weights[i], w.inputs["Scale"])
            if acc[key] is None:
                acc[key] = w.outputs["Vector"]
            else:
                add = nt.nodes.new("ShaderNodeVectorMath")
                add.operation = "ADD"
                add.location = (300 + 150 * ("albedo", "normal", "rough").index(key), y)
                L(acc[key], add.inputs[0])
                L(w.outputs["Vector"], add.inputs[1])
                acc[key] = add.outputs["Vector"]
    L(acc["albedo"], bsdf.inputs["Base Color"])
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nm.location = (900, -300)
    L(acc["normal"], nm.inputs["Color"])
    L(nm.outputs["Normal"], bsdf.inputs["Normal"])
    rs = nt.nodes.new("ShaderNodeSeparateXYZ")
    rs.location = (900, -100)
    L(acc["rough"], rs.inputs[0])
    L(rs.outputs["X"], bsdf.inputs["Roughness"])
    m["tile_m"] = 1.0
    m.diffuse_color = srgb("#8a7a5a")
    return m


def build_materials(tex):
    mats = {name: make_material(name, spec, tex) for name, spec in MATERIALS.items()}
    mats["M_Terrain"] = make_terrain_material(tex)
    return mats


# --------------------------------------------------------------------------------------
# Mesh helpers
# --------------------------------------------------------------------------------------
def apply_tiling(me):
    """World-scale UVs (1 UV = 1 m) -> texture repeats, per material."""
    if me.get("tiled") or not me.uv_layers:
        return
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[0].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    mi = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("material_index", mi)
    ls = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", ls)
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", lt)
    scale = np.array([1.0 / float(m.get("tile_m", 1.0)) if m else 1.0 for m in me.materials] or [1.0], np.float32)
    loop_mat = np.repeat(mi, lt)
    uv *= scale[loop_mat][:, None]
    me.uv_layers[0].data.foreach_set("uv", uv.ravel())
    me["tiled"] = True


def dedupe_slots(me):
    mats = list(me.materials)
    uniq = []
    remap = []
    for m in mats:
        if m not in uniq:
            uniq.append(m)
        remap.append(uniq.index(m))
    if len(uniq) == len(mats):
        return
    mi = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("material_index", mi)
    mi = np.array(remap, np.int32)[mi]
    me.materials.clear()
    for m in uniq:
        me.materials.append(m)
    me.polygons.foreach_set("material_index", mi)


def remap_materials(me, mats):
    for i, m in enumerate(me.materials):
        if m and m.name in KIT_MAT_MAP:
            me.materials[i] = mats[KIT_MAT_MAP[m.name]]
    dedupe_slots(me)


def merge_meshes(name, items):
    """[(mesh, Matrix)] -> one new mesh in local space; custom (seam) normals are kept."""
    tmp = collection("_AV_tmp")
    base = bpy.data.objects.new("_av_base", bpy.data.meshes.new("_av_base"))
    tmp.objects.link(base)
    obs = [base]
    for me, mtx in items:
        o = bpy.data.objects.new("_av_part", me.copy())
        o.matrix_world = mtx
        tmp.objects.link(o)
        obs.append(o)
    bpy.context.view_layer.update()
    with bpy.context.temp_override(active_object=base, object=base, selected_objects=obs,
                                   selected_editable_objects=obs):
        bpy.ops.object.join()
    me = base.data
    me.name = name
    bpy.data.objects.remove(base, do_unlink=True)
    bpy.data.collections.remove(tmp)
    for m in list(bpy.data.meshes):
        if m.users == 0 and m.name.startswith("_av_"):
            bpy.data.meshes.remove(m)
    return me


def fix_winding(me):
    """Closed meshes from the builders: make sure the faces point outwards."""
    bm = bmesh.new()
    bm.from_mesh(me)
    if bm.calc_volume(signed=True) < 0:
        for f in bm.faces:
            f.normal_flip()
        bm.to_mesh(me)
    bm.free()


def tri_count(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


# --------------------------------------------------------------------------------------
# Value noise (deterministic, from the generator's hash)
# --------------------------------------------------------------------------------------
class Noise:
    def __init__(self, hn, seed):
        self.hn, self.seed = hn, seed

    def v(self, x, y):
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        h = self.hn
        a, b = h(ix, iy, self.seed), h(ix + 1, iy, self.seed)
        c, d = h(ix, iy + 1, self.seed), h(ix + 1, iy + 1, self.seed)
        return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy

    def fbm(self, x, y, octaves=4):
        s, amp, f, norm = 0.0, 1.0, 1.0, 0.0
        for o in range(octaves):
            s += amp * self.v(x * f + o * 17.3, y * f - o * 9.1)
            norm += amp
            amp *= 0.5
            f *= 2.03
        return s / norm


def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# --------------------------------------------------------------------------------------
# Land: height + splat as functions, so props can sit on the same surface as the mesh
# --------------------------------------------------------------------------------------
class Land:
    def __init__(self, noise, pads):
        self.n = noise
        self.pads = []
        for cx, cy, hx, hy, rot in pads:      # pad height = unflattened height at its centre
            self.pads.append((cx, cy, hx, hy, math.radians(rot), self.base(cx, cy)))
        self.clearings = [(6.0, -3.0, 30.0, 25.0)]

    @staticmethod
    def shore_x(y):
        if y < -12.8:
            return -19.5 + 0.3 * (-12.8 - y)
        return -19.5 - 0.62 * (y + 12.8) - 0.0002 * max(0.0, y - 300.0) ** 2

    def mountains(self, x, y):
        best = 0.0
        for (x0, y0, h0), (x1, y1, h1) in zip(RIDGE, RIDGE[1:]):
            dx, dy = x1 - x0, y1 - y0
            t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy)))
            d = math.hypot(x - x0 - dx * t, y - y0 - dy * t)
            h = h0 + (h1 - h0) * t
            w = 140.0 + 1.6 * h
            if d < w:
                best = max(best, h * (1.0 - d / w) ** 1.6)
        if best <= 0.0:
            return 0.0
        n = self.n
        r = 1.0 - abs(n.fbm(x / 120.0 - 5.0, y / 120.0 + 2.0, 4))      # ridged: gullies and spurs
        return best * (0.62 + 0.55 * r * r) + 9.0 * n.fbm(x / 35.0, y / 35.0, 3) * min(1.0, best / 40.0)

    def base(self, x, y):
        n = self.n
        d = x - self.shore_x(y)                      # + inland
        z = max(-9.0, -0.05 + d * 0.11) if d < 0 else min(d, 14.0) * 0.095
        z += 0.75 * smoothstep((d - 13.0) / 9.0)     # berm up to the village terrace
        z += 0.015 * max(0.0, d - 22.0)
        z += smoothstep((d - 10.0) / 16.0) * 0.45 * n.fbm(x / 16.0, y / 16.0, 3)
        z += self.mountains(x, y)
        return z

    def height(self, x, y):
        z = self.base(x, y)
        for cx, cy, hx, hy, a, h0 in self.pads:
            dx, dy = x - cx, y - cy
            lx = dx * math.cos(a) + dy * math.sin(a)
            ly = -dx * math.sin(a) + dy * math.cos(a)
            q = max(abs(lx) - hx, abs(ly) - hy, 0.0)
            w = 1.0 - smoothstep(q / 3.0)
            if w > 0:
                z += (h0 - z) * w
        return z

    def splat(self, x, y, z, nz):
        n = self.n
        d = x - self.shore_x(y)
        jit = 2.2 * n.fbm(x / 7.0 + 11.0, y / 7.0, 3)
        sand = (1.0 - smoothstep((d + jit - 12.0) / 5.0)) * (1.0 - smoothstep((z - 2.5) / 3.0))
        earth = 0.0
        for cx, cy, rx, ry in self.clearings:
            e = math.hypot((x - cx) / rx, (y - cy) / ry)
            earth = max(earth, 1.0 - smoothstep((e - 0.55 + 0.35 * n.fbm(x / 9.0, y / 9.0 + 4.0, 3)) / 0.45))
        for cx, cy, hx, hy, a, h0 in self.pads:
            dd = math.hypot(x - cx, y - cy) - max(hx, hy)
            earth = max(earth, 1.0 - smoothstep(dd / 3.5))
        earth *= 1.0 - sand
        slope = 1.0 - nz
        rock = smoothstep((slope - 0.16 + 0.08 * n.fbm(x / 25.0, y / 25.0, 3)) / 0.2)
        rock = max(rock, smoothstep((z - 150.0) / 120.0) * (0.4 + 0.6 * n.fbm(x / 40.0, y / 40.0, 3)))
        rock *= smoothstep((d - 2.0) / 30.0) if z < 30.0 else 1.0
        grass = max(0.0, 1.0 - sand - earth) * (1.0 - rock)
        earth *= 1.0 - rock
        sand *= 1.0 - rock
        s = sand + earth + grass + rock + 1e-6
        return (sand / s, earth / s, grass / s, rock / s)


def _sinh_axis(sides, d0):
    """Grid coordinates: first cell d0 at the origin, growing with sinh out to each extent."""
    out = [0.0]
    for sign, (A, n) in zip((-1, 1), sides):
        lo, hi = 1e-3, 30.0
        for _ in range(60):            # solve A * sinh(k / n) / sinh(k) = d0
            k = 0.5 * (lo + hi)
            lo, hi = (k, hi) if A * math.sinh(k / n) / math.sinh(k) > d0 else (lo, k)
        pts = [sign * A * math.sinh(k * i / n) / math.sinh(k) for i in range(1, n + 1)]
        out = (pts[::-1] + out) if sign < 0 else (out + pts)
    return out


def build_terrain(land, mats):
    c = CONFIG
    xs = _sinh_axis(c["terrain_x"], c["terrain_cell"])
    ys = _sinh_axis(c["terrain_y"], c["terrain_cell"])
    verts = [(x, y, land.height(x, y)) for y in ys for x in xs]
    W = len(xs)
    faces = []
    for j in range(len(ys) - 1):
        for i in range(W - 1):
            q = [j * W + i, j * W + i + 1, (j + 1) * W + i + 1, (j + 1) * W + i]
            zs = [verts[v][2] for v in q]
            mx, my = (verts[q[0]][0] + verts[q[2]][0]) / 2, (verts[q[0]][1] + verts[q[2]][1]) / 2
            if max(zs) < -4.0 and math.hypot(mx, my) > 140.0:
                continue                              # deep sea floor nobody sees
            faces.append(q)
    me = bpy.data.meshes.new(c["prefix"] + "_Terrain")
    me.from_pydata(verts, [], faces)
    me.polygons.foreach_set("use_smooth", [True] * len(faces))
    me.update()
    uvl = me.uv_layers.new(name="UVMap")
    uv = []
    for p in me.polygons:
        for vi in p.vertices:
            uv += verts[vi][:2]
    uvl.data.foreach_set("uv", uv)
    nz = [v.vector.z for v in me.vertex_normals]
    ca = me.color_attributes.new("Splat", "FLOAT_COLOR", "POINT")
    cols = []
    for i, v in enumerate(verts):
        cols += land.splat(v[0], v[1], v[2], nz[i])
    ca.data.foreach_set("color", cols)
    me.color_attributes.active_color = ca
    me.materials.append(mats["M_Terrain"])
    # drop the vertices of the removed sea-floor faces
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me["tiled"] = True
    return me


def build_sea(mats):
    s = CONFIG["sea_size"] / 2
    n = 6
    verts, faces = [], []
    for j in range(n + 1):
        for i in range(n + 1):
            verts.append((-s + 2 * s * i / n, -s + 2 * s * j / n, 0.0))
    for j in range(n):
        for i in range(n):
            faces.append([j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i])
    me = bpy.data.meshes.new(CONFIG["prefix"] + "_Sea")
    me.from_pydata(verts, [], faces)
    uvl = me.uv_layers.new(name="UVMap")
    uv = []
    for p in me.polygons:
        for vi in p.vertices:
            uv += verts[vi][:2]
    uvl.data.foreach_set("uv", uv)
    me.materials.append(mats["M_Water"])
    apply_tiling(me)
    return me


# --------------------------------------------------------------------------------------
# Prefabs
# --------------------------------------------------------------------------------------
class Prefabs:
    """Builds every prefab mesh once. Each entry: mesh, collider meshes, fx markers, vis range."""

    def __init__(self, K, gen, parts, kit, mats, parent):
        self.K, self.gen, self.parts, self.kit, self.mats = K, gen, parts, kit, mats
        self.g = gen
        self.MB, self.tube, self.loft, self.lathe = gen["MeshBuilder"], gen["tube"], gen["loft"], gen["lathe"]
        self.obox, self.ring, self.hn = gen["obox"], gen["ring"], gen["hn"]
        self.col = collection(CONFIG["name"] + "_Prefabs", parent)
        self.items = {}
        self.n = 0

    # ---------------------------------------------------------------- shared helpers
    def _obj(self, b, name):
        ob = self.g["to_object"](b, name, self.col)
        me = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        return me

    def finish(self, b, name, closed=False):
        me = self._obj(b, CONFIG["prefix"] + "_" + name)
        if closed:
            fix_winding(me)
        dedupe_slots(me)
        apply_tiling(me)
        return me

    def hull(self, name, pts):
        """Convex collider mesh from points."""
        bm = bmesh.new()
        for p in pts:
            bm.verts.new(p)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        res = bmesh.ops.convex_hull(bm, input=bm.verts)
        keep = {e for e in res["geom"] if isinstance(e, bmesh.types.BMVert)}
        keep |= {v for e in res["geom"] if isinstance(e, bmesh.types.BMFace) for v in e.verts}
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if v not in keep], context="VERTS")
        me = bpy.data.meshes.new(CONFIG["prefix"] + "_COL_" + name)
        bm.to_mesh(me)
        bm.free()
        return me

    def box_hull(self, name, c, h):
        return self.hull(name, [(c[0] + sx * h[0], c[1] + sy * h[1], c[2] + sz * h[2])
                                for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])

    def r(self, *a):
        self.n += 1
        return self.hn(*a, self.n)

    def add(self, key, me, colliders=(), fx=(), vis=0.0):
        self.items[key] = dict(mesh=me, colliders=list(colliders), fx=list(fx), vis=vis)
        ob = bpy.data.objects.new("PF_" + key, me)    # one source object per prefab (not exported)
        ob.location = (len(self.items) * 12.0, -400.0, 0.0)
        self.col.objects.link(ob)

    # ---------------------------------------------------------------- chise from the kit
    def chise(self, key, extra, props):
        K = self.K
        PROP = K["PROP"]
        kit_by_base = {p.base: o for p, o in self.kit}
        items = []
        shift = Vector((extra / 2.0, 0, 0))
        for pdef, ob in self.parts:
            if pdef.layer == PROP and not props:
                continue
            span = pdef.meta.get("span")
            if span is not None and pdef.meta.get("axis") == 0:
                east = span[0] >= -1e-6
            else:
                east = pdef.pivot.x > 1e-3
            loc = pdef.pivot + Vector((extra if east else 0.0, 0, 0)) - shift
            items.append((ob.data, Matrix.Translation(loc)))
        for base in ("Wall_S_1m", "Wall_N_1m", "Roof_S_1m", "Roof_N_1m", "RoofTop_1m", "Ridge_1m"):
            src = next((p for p, _ in self.parts if p.base == base), None)
            for i in range(extra if src else 0):
                items.append((kit_by_base[base].data, Matrix.Translation((i - extra / 2.0, src.pivot.y, 0))))
        me = merge_meshes(CONFIG["prefix"] + "_" + key, items)
        remap_materials(me, self.mats)
        apply_tiling(me)
        # collision: the generator's convex set for a house of this length
        cfg = dict(self.g["CONFIG"], length=self.g["CONFIG"]["length"] + extra)
        G = self.g["Geo"](cfg)
        cols = []
        for i, b in enumerate(self.g["build_collision"](G)):
            pts = [b.v[v] for f in b.f for v in f]
            if max(p[2] for p in pts) <= 0.01:
                continue                                 # floor slab: the terrain does that
            cols.append(self.hull("%s_%02d" % (key, i), pts))
        fx = [("FX_Smoke", Vector((G.Gx - 0.15, 0.0, G.zG + 0.45)))]
        self.add(key, me, cols, fx)
        log("prefab %-10s %5d tris  %d mats  %d pieces  %d colliders"
            % (key, tri_count(me), len(me.materials), len(items), len(cols)))

    # ---------------------------------------------------------------- pu (raised storehouse)
    def pu(self):
        b, tube, obox = self.MB(), self.tube, self.obox
        T, R, P, D = "M_Timber", "M_ReedWall", "M_Thatch", "M_TimberDark"
        hp, fx, fy = 1.45, 0.82, 0.72               # post height, post half spacing
        for sx in (-1, 1):
            for sy in (-1, 1):
                x, y = sx * fx + 0.02 * self.r(sx, sy), sy * fy + 0.02 * self.r(sy, sx)
                tube(b, [(x, y, -0.15), (x * 0.98, y * 0.98, hp)], 0.085, T, sides=6)
        for sy in (-1, 1):                           # sleepers on the posts
            tube(b, [(-1.05, sy * fy, hp + 0.06), (1.05, sy * fy, hp + 0.06)], 0.07, T, sides=6)
        obox(b, (0, 0, hp + 0.18), (1.05, 0, 0), (0, 0.95, 0), (0, 0, 0.05), T)    # floor deck
        # body with a door on -Y
        z0, z1, hx, hy = hp + 0.23, hp + 1.45, 0.82, 0.70
        dw, dh = 0.34, 1.0
        b.poly([(hx, -hy, z0), (hx, hy, z0), (hx, hy, z1), (hx, -hy, z1)], R, facing=(1, 0, 0))
        b.poly([(-hx, -hy, z0), (-hx, hy, z0), (-hx, hy, z1), (-hx, -hy, z1)], R, facing=(-1, 0, 0))
        b.poly([(-hx, hy, z0), (hx, hy, z0), (hx, hy, z1), (-hx, hy, z1)], R, facing=(0, 1, 0))
        f = [(-hx, -hy, z0), (-dw, -hy, z0), (-dw, -hy, z0 + dh), (dw, -hy, z0 + dh), (dw, -hy, z0),
             (hx, -hy, z0), (hx, -hy, z1), (-hx, -hy, z1)]
        b.poly(f, R, facing=(0, -1, 0))
        b.poly([(-dw, -hy + 0.18, z0), (dw, -hy + 0.18, z0), (dw, -hy + 0.18, z0 + dh), (-dw, -hy + 0.18, z0 + dh)],
               D, facing=(0, -1, 0))               # dark recess
        for s in (-1, 1):
            b.poly([(s * dw, -hy, z0), (s * dw, -hy + 0.18, z0), (s * dw, -hy + 0.18, z0 + dh), (s * dw, -hy, z0 + dh)],
                   D, facing=(-s, 0, 0))
        b.poly([(-dw, -hy, z0 + dh), (dw, -hy, z0 + dh), (dw, -hy + 0.18, z0 + dh), (-dw, -hy + 0.18, z0 + dh)],
               D, facing=(0, 0, -1))
        for s in (-1, 1):                            # door posts + lintel
            tube(b, [(s * (dw + 0.05), -hy - 0.04, z0), (s * (dw + 0.05), -hy - 0.04, z0 + dh + 0.08)], 0.04, T, sides=5)
        tube(b, [(-dw - 0.15, -hy - 0.05, z0 + dh + 0.06), (dw + 0.15, -hy - 0.05, z0 + dh + 0.06)], 0.04, T, sides=5)
        for zz in (z0 + 0.35, z0 + 0.9):           # binding poles
            for s in (-1, 1):
                tube(b, [(s * (hx + 0.03), -hy - 0.03, zz), (s * (hx + 0.03), hy + 0.03, zz)], 0.025, T, sides=4, caps=False)
            tube(b, [(-hx - 0.03, hy + 0.03, zz), (hx + 0.03, hy + 0.03, zz)], 0.025, T, sides=4, caps=False)
        # hipped thatch roof in three courses (rounded corners)
        ring, loft = self.ring, self.loft
        ze, zt = z1 - 0.2, z1 + 1.55
        X0, Y0, X1, Y1 = 1.32, 1.2, 0.34, 0.12
        rows = []

        def rr(t, out, dz):
            X, Y = X0 + (X1 - X0) * t + out, Y0 + (Y1 - Y0) * t + out
            z = ze + (zt - ze) * t + dz
            return b.verts([(x, y, z) for x, y, _ in ring(X, Y, 0.35 * (1 - t) + 0.08, 3, 3, 2)])

        tiers = 3
        for k in range(tiers):
            t0, t1 = k / tiers, (k + 1) / tiers
            rows += [rr(t0, 0.07, -0.06), rr(t0 + 0.08, 0.04, 0.0), rr(t1, 0.0, 0.0)]
        loft(b, rows, P, smooth=True)
        under = [rr(0.0, 0.07, -0.06)]
        inner = b.verts([(x, y, z1 - 0.02) for x, y, _ in ring(hx + 0.02, hy + 0.02, 0.1, 3, 3, 2)])
        loft(b, [inner, under[0]], P, smooth=False)
        top = rows[-1]
        c = b.vert((0, 0, zt + 0.05))
        for j in range(len(top)):
            b.face([top[j], top[(j + 1) % len(top)], c], P, True)
        tube(b, [(-0.55, 0, zt + 0.06), (0.55, 0, zt + 0.06)], (0.13, 0.11), P, sides=6)   # ridge bundle
        for s in (-0.35, 0.0, 0.35):               # crossed ridge sticks
            tube(b, [(s, -0.22, zt - 0.05), (s, 0.12, zt + 0.32)], 0.025, T, sides=4)
            tube(b, [(s, 0.22, zt - 0.05), (s, -0.12, zt + 0.32)], 0.025, T, sides=4)
        tube(b, [(0.1, -1.9, -0.1), (0.05, -0.95, hp + 0.22)], 0.08, T, sides=6)        # notched log ladder
        me = self.finish(b, "Pu")
        cols = [self.box_hull("Pu_body", (0, 0, (hp + z1) / 2), (1.05, 0.95, (z1 - hp) / 2 + 0.1)),
                self.hull("Pu_roof", [(x * s, y * t, ze - 0.06) for x, y in ((X0 + 0.07, Y0 + 0.07),) for s in (-1, 1)
                                      for t in (-1, 1)] + [(-0.55, 0, zt + 0.2), (0.55, 0, zt + 0.2)])]
        cols += [self.box_hull("Pu_post%d" % i, (sx * fx, sy * fy, hp / 2), (0.09, 0.09, hp / 2))
                 for i, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1)))]
        self.add("Pu", me, cols)

    # ---------------------------------------------------------------- small props
    def fish(self, b, top, length=0.62, yaw=0.0):
        st = [(0.0, 0.020, 0.010), (0.10, 0.045, 0.018), (0.32, 0.085, 0.028), (0.62, 0.080, 0.028),
              (0.86, 0.048, 0.020), (1.0, 0.008, 0.005)]
        ca, sa = math.cos(yaw), math.sin(yaw)
        rows = []
        for t, hh, th in st:
            z = top[2] - t * length
            pts = []
            for k in range(6):
                a = 2 * math.pi * k / 6
                lx, ly = th * math.cos(a), hh * math.sin(a)
                pts.append((top[0] + lx * ca - ly * sa, top[1] + lx * sa + ly * ca, z))
            rows.append(b.verts(pts))
        self.loft(b, rows[::-1], "M_Fish", smooth=True)
        b.poly([b.v[i] for i in rows[-1]], "M_Fish", facing=(0, 0, -1))
        b.poly([b.v[i] for i in rows[0]], "M_Fish", facing=(0, 0, 1))
        # tail fin above the tail joint, two-sided
        p0 = Vector(top)
        u = Vector((-sa, ca, 0.0))
        fin = [p0, p0 + u * 0.075 + Vector((0, 0, 0.09)), p0 - u * 0.075 + Vector((0, 0, 0.09))]
        b.poly(fin, "M_Fish", facing=Vector((ca, sa, 0)))
        b.poly(fin, "M_Fish", facing=Vector((-ca, -sa, 0)))

    def fish_rack_a(self):
        """X-legged ends, a bar resting in the crotches, salmon hanging head down."""
        b, tube = self.MB(), self.tube
        L, H, a, bb = 3.4, 2.6, 0.55, 0.2
        zc = H * a / (a + bb)
        for x in (-L / 2, L / 2):
            tube(b, [(x, -a, -0.15), (x + 0.03, bb, H)], 0.055, "M_Timber", sides=6)
            tube(b, [(x, a, -0.15), (x - 0.03, -bb, H)], 0.055, "M_Timber", sides=6)
        tube(b, [(-L / 2 - 0.35, 0, zc + 0.05), (L / 2 + 0.3, 0, zc + 0.06)], 0.05, "M_Timber", sides=6)
        n = 8
        for i in range(n):
            x = -L / 2 + 0.35 + (L - 0.7) * i / (n - 1) + 0.04 * self.r(i, 1)
            self.fish(b, (x, 0.0, zc - 0.03), 0.6 + 0.06 * self.r(i, 2), yaw=1.57 + 0.5 * self.r(i, 3))
        me = self.finish(b, "FishRack_A")
        cols = [self.box_hull("RackA_%d" % i, (x, 0, H / 2), (0.12, a, H / 2)) for i, x in enumerate((-L / 2, L / 2))]
        self.add("FishRack_A", me, cols, vis=160.0)

    def fish_rack_b(self):
        """Post frame on the beach: two bars, a hanging reed mat and a few fish."""
        b, tube, obox = self.MB(), self.tube, self.obox
        w, H = 1.0, 2.3
        for x in (-w, w):
            tube(b, [(x, 0, -0.2), (x * 0.97, 0.02, H)], 0.06, "M_Timber", sides=6)
        for z in (2.05, 1.25):
            tube(b, [(-w - 0.2, 0.0, z), (w + 0.2, 0.0, z + 0.02)], 0.04, "M_Timber", sides=5)
        obox(b, (-0.38, 0.06, 1.55), (0.45, 0, 0), (0, 0.012, 0), (0, 0, 0.46), "M_ReedMat")
        for i, x in enumerate((0.25, 0.5, 0.75)):
            self.fish(b, (x, 0.0, 2.02), 0.58, yaw=1.6 + 0.4 * self.r(i, 7))
        for i in range(3):                           # skins / mats on the lower bar
            x = -0.8 + 0.3 * i
            self.fish(b, (x + 0.1, 0.0, 1.22), 0.5, yaw=1.5 + 0.3 * self.r(i, 9))
        me = self.finish(b, "FishRack_B")
        cols = [self.box_hull("RackB", (0, 0, H / 2), (w + 0.1, 0.1, H / 2))]
        self.add("FishRack_B", me, cols, vis=140.0)

    def lean_poles(self):
        b = self.MB()
        top = Vector((0.1, 0.05, 3.7))
        for i in range(11):
            a = 2 * math.pi * i / 11 + 0.3 * self.r(i, 1)
            rad = 0.75 + 0.2 * self.r(i, 2)
            base = Vector((rad * math.cos(a), rad * math.sin(a), -0.1))
            tip = top + Vector((0.25 * self.r(i, 3), 0.25 * self.r(i, 4), 0.5 * self.r(i, 5)))
            tip = base + (tip - base) * 1.15
            self.tube(b, [base, tip], 0.035 + 0.01 * self.r(i, 6), "M_Timber", sides=5)
        me = self.finish(b, "LeanPoles")
        self.add("LeanPoles", me, [self.hull("LeanPoles", [(0.8 * math.cos(a), 0.8 * math.sin(a), 0) for a in
                                                            (0, 2.1, 4.2)] + [(0.1, 0.05, 3.6)])], vis=180.0)

    def firewood(self):
        b, tube = self.MB(), self.tube
        for y in (-0.45, 0.45):
            tube(b, [(-0.8, y, 0.06), (0.8, y, 0.06)], 0.06, "M_TimberDark", sides=5)
        z, rows = 0.18, [6, 5, 4, 3]
        for k, n in enumerate(rows):
            r = 0.085
            for i in range(n):
                y = (i - (n - 1) / 2) * 2 * r * 1.02
                j = self.r(k, i)
                tube(b, [(-0.68 + 0.08 * j, y, z), (0.68 + 0.06 * j, y, z + 0.02 * j)], r * (0.9 + 0.12 * j), "M_Timber", sides=6)
            z += 2 * r * 0.88
        me = self.finish(b, "Firewood", closed=False)
        self.add("Firewood", me, [self.box_hull("Firewood", (0, 0, z / 2), (0.75, 0.55, z / 2))], vis=120.0)

    def rock_into(self, b, c, size, subdiv, seed, mat="M_Rock", flat=0.55):
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=1.0)
        idx = []
        sx, sy, sz = size
        for v in bm.verts:
            d = v.co.normalized()
            n = 1.0 + 0.22 * self.hn(round(d.x * 3), round(d.y * 3), round(d.z * 3), seed) \
                + 0.08 * self.hn(round(d.x * 7), round(d.y * 7), round(d.z * 7), seed + 1)
            p = Vector((d.x * sx * n, d.y * sy * n, max(d.z * sz * n, -sz * flat)))
            idx.append(b.vert(Vector(c) + p))
        for f in bm.faces:
            b.face([idx[v.index] for v in f.verts], mat, True)
        bm.free()

    def rocks(self):
        specs = {"Rock_A": ((0.42, 0.36, 0.3), 2), "Rock_B": ((0.7, 0.4, 0.32), 2),
                 "Rock_C": ((0.85, 0.7, 0.6), 3), "Rock_D": ((0.32, 0.28, 0.16), 2)}
        for i, (key, (size, sub)) in enumerate(specs.items()):
            b = self.MB()
            self.rock_into(b, (0, 0, size[2] * 0.35), size, sub, 50 + i)
            me = self.finish(b, key, closed=True)
            cols = [self.hull(key, [v.co.copy() for v in me.vertices])] if sub > 2 or size[0] > 0.4 else []
            self.add(key, me, cols, vis=0.0 if sub > 2 else 140.0)

    def fire_pit(self):
        b, tube = self.MB(), self.tube
        for i in range(9):
            a = 2 * math.pi * i / 9 + 0.15 * self.r(i, 1)
            rr = 0.62
            s = 0.17 + 0.04 * self.r(i, 2)
            self.rock_into(b, (rr * math.cos(a), rr * math.sin(a), 0.04), (s * 1.2, s, s * 0.8), 2, 80 + i)
        ash = [b.vert((0, 0, 0.03))]
        ring_ = b.verts([(0.55 * math.cos(2 * math.pi * k / 10), 0.55 * math.sin(2 * math.pi * k / 10), 0.0)
                         for k in range(10)])
        for k in range(10):
            b.face([ash[0], ring_[k], ring_[(k + 1) % 10]], "M_Ash", True)
        emb = b.verts([(0.22 * math.cos(2 * math.pi * k / 7), 0.22 * math.sin(2 * math.pi * k / 7), 0.05) for k in range(7)])
        b.face(emb, "M_Embers", facing=(0, 0, 1))
        for i in range(4):                           # charred logs pointing into the fire
            a = math.pi / 2 * i + 0.4
            tube(b, [(0.5 * math.cos(a), 0.5 * math.sin(a), 0.08), (0.08 * math.cos(a), 0.08 * math.sin(a), 0.12)],
                 0.05, "M_TimberDark", sides=5)
        apex = Vector((0, 0, 1.75))
        for i in range(3):                           # tripod
            a = 2 * math.pi * i / 3 + 0.5
            foot = Vector((1.0 * math.cos(a), 1.0 * math.sin(a), -0.1))
            tube(b, [foot, apex + (apex - foot).normalized() * 0.25], 0.035, "M_Timber", sides=5)
        me_b = self._obj(b, "_av_firepit")
        pot = next((o for p, o in self.kit if p.base == "Prop_Pot"), None)
        items = [(me_b, Matrix())]
        pot_top = 0.0
        if pot:
            zs = [v.co.z for v in pot.data.vertices]
            pot_z = 0.55
            pot_top = pot_z + max(zs)
            items.append((pot.data, Matrix.Translation((0, 0, pot_z))))
        rope = self.MB()
        tube(rope, [(0, 0, 1.72), (0, 0, pot_top - 0.02)], 0.012, "M_TimberDark", sides=4, caps=False)
        items.append((self._obj(rope, "_av_rope"), Matrix()))
        me = merge_meshes(CONFIG["prefix"] + "_FirePit", items)
        remap_materials(me, self.mats)
        apply_tiling(me)
        cols = [self.hull("FirePit", [(0.85 * math.cos(a), 0.85 * math.sin(a), z) for a in
                                      (k * math.pi / 4 for k in range(8)) for z in (0.0, 0.3)])]
        self.add("FirePit", me, cols, fx=[("FX_Fire", Vector((0, 0, 0.1))), ("FX_Smoke", Vector((0, 0, 0.6)))])

    def baskets(self):
        prof_a = [(0, 0.0), (0.15, 0.0), (0.2, 0.06), (0.23, 0.2), (0.24, 0.34), (0.215, 0.34), (0.2, 0.2),
                  (0.17, 0.06), (0.0, 0.04)]
        prof_b = [(0, 0.0), (0.22, 0.0), (0.3, 0.06), (0.34, 0.15), (0.31, 0.15), (0.27, 0.07), (0.0, 0.04)]
        for key, prof in (("Basket_A", prof_a), ("Basket_B", prof_b)):
            b = self.MB()
            self.lathe(b, prof, "M_ReedMat", (0, 0, 0), segs=12)
            me = self.finish(b, key)
            self.add(key, me, [self.hull(key, [(prof[2][0] * math.cos(a), prof[2][0] * math.sin(a), z)
                                               for a in (0, 2.1, 4.2) for z in (0, prof[4][1])])], vis=70.0)

    def screen(self):
        b = self.MB()
        lean = math.radians(12)
        up = Vector((0, math.sin(lean), math.cos(lean)))
        self.obox(b, up * 0.74, (0.5, 0, 0), Vector((0, math.cos(lean), -math.sin(lean))) * 0.025,
                  up * 0.74, "M_ReedWall")
        for x in (-0.48, 0.48):
            self.tube(b, [Vector((x, -0.04, -0.03)), Vector((x, -0.04, 0)) + up * 1.55], 0.025, "M_Timber", sides=4)
        me = self.finish(b, "Screen")
        self.add("Screen", me, vis=90.0)

    def boat(self):
        """Dugout canoe: a closed loft of U sections (outside, then inside back)."""
        b = self.MB()
        L, W, H, th = 6.0, 0.42, 0.5, 0.05
        rows = []
        n = 12
        for i in range(n + 1):
            t = -0.97 + 1.94 * i / n
            w = W * (1 - abs(t) ** 2.4) ** 0.55 + 0.02
            sheer = H + 0.14 * t * t
            keel = 0.04 * t * t
            x = t * L / 2
            sec = [(w, sheer), (w * 0.92, keel + 0.22), (w * 0.55, keel + 0.03), (0.0, keel),
                   (-w * 0.55, keel + 0.03), (-w * 0.92, keel + 0.22), (-w, sheer),
                   (-(w - th), sheer), (-(w - th) * 0.9, keel + 0.24), (0.0, keel + th + 0.02),
                   ((w - th) * 0.9, keel + 0.24), ((w - th), sheer)]
            rows.append(b.verts([(x, y, z) for y, z in sec]))
        # rows run -X -> +X, so the default winding faces inwards: flip
        self.loft(b, rows, "M_Timber", flip=True, smooth=[True] * 6 + [False] + [True] * 4 + [False],
                  uvf=lambda p, a: (p[0], a))
        b.poly([b.v[i] for i in rows[0]], "M_Timber", facing=(-1, 0, 0))
        b.poly([b.v[i] for i in rows[-1]], "M_Timber", facing=(1, 0, 0))
        for x in (-1.2, 0.9):                        # thwarts
            self.obox(b, (x, 0, H - 0.06), (0.06, 0, 0), (0, W - 0.03, 0), (0, 0, 0.02), "M_Timber")
        me = self.finish(b, "Boat")
        self.add("Boat", me, [self.hull("Boat", [b.v[i] for i in rows[0] + rows[n // 2] + rows[-1]])], vis=0.0)

    def build_all(self):
        self.chise("Chise_5m", 0, CONFIG["interiors"])
        self.chise("Chise_7m", 2, False)
        self.pu()
        self.fish_rack_a()
        self.fish_rack_b()
        self.lean_poles()
        self.firewood()
        self.rocks()
        self.fire_pit()
        self.baskets()
        self.screen()
        self.boat()
        for key, it in self.items.items():
            if key.startswith("Chise"):
                continue
            log("prefab %-10s %5d tris  %d mats  %d colliders"
                % (key, tri_count(it["mesh"]), len(it["mesh"].materials), len(it["colliders"])))
        return self.items


def build_fence(pf, land):
    """Foreground fence along FENCE: lashed posts and two rails, in world space (unique mesh)."""
    b, tube = pf.MB(), pf.tube
    pts = [Vector((x, y, 0)) for x, y in FENCE]
    posts = []
    for i in range(len(pts) - 1):
        a, c = pts[i], pts[i + 1]
        n = max(1, int((c - a).length / 1.15))
        for k in range(n + (1 if i == len(pts) - 2 else 0)):
            posts.append(a + (c - a) * (k / n))
    tops = []
    for i, p in enumerate(posts):
        z = land.height(p.x, p.y) - 0.15
        h = 1.15 + 0.25 * pf.hn(i, 3) + 0.15 * pf.hn(i, 4)
        lean = Vector((0.06 * pf.hn(i, 5), 0.06 * pf.hn(i, 6), 0))
        tube(b, [(p.x, p.y, z), (p.x + lean.x, p.y + lean.y, z + h)], 0.065 + 0.015 * pf.hn(i, 7), "M_Timber", sides=6)
        tops.append((p, z))
    for hgt, r in ((0.45, 0.035), (0.88, 0.04)):
        for s in range(0, len(posts) - 1, 3):
            seg = tops[s:s + 4]
            if len(seg) < 2:
                continue
            line = []
            for j, (p, z) in enumerate(seg):
                off = Vector((0, 0, hgt + 0.03 * pf.hn(s, j, int(hgt * 10))))
                d = (seg[-1][0] - seg[0][0]).normalized()
                ext = d * (0.25 if j == len(seg) - 1 else (-0.25 if j == 0 else 0))
                line.append(Vector((p.x, p.y, z + 0.15)) + off + ext + Vector((-d.y, d.x, 0)) * 0.08)
            tube(b, line, r, "M_Timber", sides=5)
    me = pf.finish(b, "Fence")
    cols = []
    for i in range(len(pts) - 1):
        a, c = pts[i], pts[i + 1]
        za, zc = land.height(a.x, a.y), land.height(c.x, c.y)
        d = (c - a).normalized()
        nrm = Vector((-d.y, d.x, 0)) * 0.12
        cols.append(pf.hull("Fence_%d" % i, [a + nrm, a - nrm, c + nrm, c - nrm] +
                            [p + Vector((0, 0, z)) for p, z in ((a + nrm, za + 1.2), (a - nrm, za + 1.2),
                                                                (c + nrm, zc + 1.2), (c - nrm, zc + 1.2))]
                            + [p + Vector((0, 0, z - 0.2)) for p, z in ((a, za), (c, zc))]))
    return me, cols


# --------------------------------------------------------------------------------------
# Scene assembly
# --------------------------------------------------------------------------------------
def pads_from_layout():
    pads = []
    for entry in LAYOUT:
        if len(entry) != 5:
            continue
        key, name, x, y, rot = entry
        if key in PAD_SIZE:
            hx, hy = PAD_SIZE[key]
            pads.append((x, y, hx, hy, rot))
    return pads


def place(items, key, name, loc, rot, cols, scale=1.0):
    it = items[key]
    ob = bpy.data.objects.new(name, it["mesh"])
    ob.location = loc
    ob.rotation_euler = (0.0, 0.0, math.radians(rot))
    ob.scale = (scale, scale, scale)
    cols["world"].objects.link(ob)
    ob["prefab"] = key
    if it["vis"]:
        ob["godot_vis_range"] = it["vis"]
    for i, cm in enumerate(it["colliders"]):
        c = bpy.data.objects.new("%s_COL%02d-convcolonly" % (name, i), cm)
        c.parent = ob
        c.display_type = "WIRE"
        c.hide_render = True
        cols["collision"].objects.link(c)
    for kind, p in it["fx"]:
        e = bpy.data.objects.new("%s_%s" % (kind, name), None)
        e.empty_display_type = "SPHERE"
        e.empty_display_size = 0.3
        e.location = p
        e.parent = ob
        cols["fx"].objects.link(e)
    return ob


def scatter_rocks(items, land, cols, pf):
    """Beach pebbles and rocks along the shore and near the fence."""
    out = []
    k = 0
    for i in range(60):
        y = -25.0 + 70.0 * (0.5 + 0.5 * pf.hn(i, 101))
        d = 9.0 * (0.5 + 0.5 * pf.hn(i, 102)) - 1.5
        x = land.shore_x(y) + d
        key = ("Rock_D", "Rock_A", "Rock_B")[int(abs(pf.hn(i, 103)) * 2.99)]
        s = 0.6 + 0.9 * (0.5 + 0.5 * pf.hn(i, 104))
        if any(math.hypot(x - e[2], y - e[3]) < 3.5 for e in LAYOUT
               if len(e) == 5 and e[0] in ("Boat", "FishRack_B", "FishRack_A", "LeanPoles")):
            continue
        z = land.height(x, y) - 0.08 * s
        out.append(place(items, key, "%s_%03d" % (key, k), (x, y, z), 360 * pf.hn(i, 105), cols, s))
        k += 1
    for i, (x, y, s) in enumerate([(-6.0, -24.5, 1.0), (3.2, -24.8, 0.8), (-12.5, -19.5, 1.2), (6.5, -27.0, 1.4),
                                   (9.0, -18.5, 0.9), (-3.5, -14.0, 0.7)]):
        out.append(place(items, "Rock_A", "Rock_A_F%02d" % i, (x, y, land.height(x, y) - 0.1), 77 * i, cols, s))
    return out


def setup_world(scene):
    sd = Vector(CONFIG["sun_dir"]).normalized()
    w = scene.world or bpy.data.worlds.new("AinuVillage_World")
    scene.world = w
    if w.node_tree is None:
        w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    sky = nt.nodes.new("ShaderNodeTexSky")
    types = [i.identifier for i in sky.bl_rna.properties["sky_type"].enum_items]
    for t in ("MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "NISHITA"):
        if t in types:
            sky.sky_type = t
            break
    for attr, val in (("sun_elevation", math.asin(sd.z)), ("sun_rotation", math.atan2(sd.x, sd.y) * -1 + math.pi / 2),
                      ("altitude", 50.0), ("air_density", 1.0), ("aerosol_density", 1.6), ("ozone_density", 1.0)):
        if hasattr(sky, attr):
            try:
                setattr(sky, attr, val)
            except (TypeError, AttributeError):
                pass
    if hasattr(sky, "sun_disc"):
        sky.sun_disc = False
    bg.inputs["Strength"].default_value = 0.32
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return sd


def build_scene():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    root = collection(CONFIG["name"])
    cols = {"world": collection(CONFIG["name"] + "_World", root),
            "collision": collection(CONFIG["name"] + "_Collision", root),
            "fx": collection(CONFIG["name"] + "_FX", root),
            "env": collection(CONFIG["name"] + "_Env", root)}
    return root, cols


def build(opts):
    K, gen, parts, kit = load_kit()
    gen["_SEED"][0] = CONFIG["seed"]
    purge()
    tex_src = find_tex_src(opts.get("tex-src"))
    godot_dir = os.path.abspath(opts.get("godot-dir") or os.path.join(_script_dir(), "out", "godot", "ainu_village"))
    tex = prepare_textures(tex_src, os.path.join(godot_dir, "textures"), CONFIG["tex_res"], "force-textures" in opts)
    mats = build_materials(tex)
    root, cols = build_scene()
    pf = Prefabs(K, gen, parts, kit, mats, root)
    items = pf.build_all()
    lc = _find_layer_collection(bpy.context.view_layer.layer_collection, pf.col.name)
    if lc:      # prefab sources stay in the file but out of renders and exports
        lc.exclude = True

    land = Land(Noise(gen["hn"], CONFIG["seed"]), pads_from_layout())
    t = bpy.data.objects.new("Terrain-col", build_terrain(land, mats))
    cols["world"].objects.link(t)
    sea = bpy.data.objects.new("Sea", build_sea(mats))
    cols["world"].objects.link(sea)

    placed = []
    by_name = {}
    for entry in LAYOUT:
        key, name = entry[0], entry[1]
        x, y, rot = (entry[2], entry[3], entry[4]) if len(entry) == 5 else (entry[2], None, entry[3])
        if isinstance(x, tuple):                     # ("@parent", local x, local y)
            par = by_name[x[0][1:]]
            a = par.rotation_euler.z
            lx, ly = x[1], x[2]
            x, y = (par.location.x + lx * math.cos(a) - ly * math.sin(a),
                    par.location.y + lx * math.sin(a) + ly * math.cos(a))
            rot += math.degrees(a)
        z = land.height(x, y) - (0.02 if key.startswith("Chise") or key == "Pu" else 0.03)
        by_name[name] = place(items, key, name, (x, y, z), rot, cols)
        placed.append(by_name[name])
    placed += scatter_rocks(items, land, cols, pf)
    fence_me, fence_cols = build_fence(pf, land)
    items["Fence"] = dict(mesh=fence_me, colliders=fence_cols, fx=[], vis=220.0)
    placed.append(place(items, "Fence", "Fence_Front", (0, 0, 0), 0, cols))

    flora = None
    if "no-flora" not in opts:
        FL = runpy.run_path(os.path.join(_script_dir(), "ainu_flora.py"), run_name="ainu_flora_lib")
        flora = FL["build"](gen, land, placed, root, os.path.join(godot_dir, "textures"), CONFIG["tex_res"],
                            "force-textures" in opts, camera=CAMERA["loc"], fence=FENCE)
        flora["module"] = FL
        for key, me in flora["species"].items():
            items["Flora_" + key] = dict(mesh=me, colliders=[], fx=[], vis=0.0)
            src = bpy.data.objects.new("PF_Flora_" + key, me)
            pf.col.objects.link(src)

    # camera + sun (the sun is for Blender renders; the Godot main scene has its own)
    sd = setup_world(bpy.context.scene)
    cam = bpy.data.objects.new("CAM_Concept", bpy.data.cameras.new("CAM_Concept"))
    cx, cy = CAMERA["loc"]
    cam.location = (cx, cy, land.height(cx, cy) + CAMERA["height"])
    hd, pt = math.radians(CAMERA["heading"]), math.radians(CAMERA["pitch"])
    d = Vector((math.sin(hd) * math.cos(pt), math.cos(hd) * math.cos(pt), math.sin(pt)))
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = CAMERA["lens"]
    cam.data.clip_start, cam.data.clip_end = 0.1, 5000.0
    cols["env"].objects.link(cam)
    bpy.context.scene.camera = cam
    sun = bpy.data.objects.new("SUN_Key", bpy.data.lights.new("SUN_Key", "SUN"))
    sun.data.energy = 3.4
    sun.data.angle = math.radians(1.5)
    sun.data.color = (1.0, 0.94, 0.84)
    sun.rotation_euler = (-sd).to_track_quat("-Z", "Y").to_euler()
    cols["env"].objects.link(sun)
    report(items, t, sea, placed)
    return dict(items=items, land=land, terrain=t, sea=sea, placed=placed, mats=mats, tex=tex,
                godot_dir=godot_dir, cols=cols, sun_dir=sd, camera=cam, flora=flora)


def report(items, terrain, sea, placed):
    per = {}
    for ob in placed:
        per.setdefault(ob.get("prefab", ob.name), []).append(ob)
    total = tri_count(terrain.data) + tri_count(sea.data)
    draws = len(terrain.data.materials) + len(sea.data.materials)
    print("\n%-14s %8s %6s %10s %6s" % ("prefab", "tris", "count", "scene tris", "mats"))
    for key, obs in sorted(per.items()):
        me = items[key]["mesh"]
        tc = tri_count(me)
        total += tc * len(obs)
        draws += len(me.materials) * len(obs)
        print("%-14s %8d %6d %10d %6d" % (key, tc, len(obs), tc * len(obs), len(me.materials)))
    print("%-14s %8d %6d %10d %6d" % ("Terrain", tri_count(terrain.data), 1, tri_count(terrain.data), 1))
    print("%-14s %8d %6d %10d %6d" % ("Sea", tri_count(sea.data), 1, tri_count(sea.data), 1))
    ncol = sum(len(items[o.get("prefab", "")]["colliders"]) for o in placed if o.get("prefab") in items)
    print("scene total %d tris, %d mesh surfaces (draw calls before Godot culling/LOD), %d convex colliders + terrain trimesh\n"
          % (total, draws, ncol))


# --------------------------------------------------------------------------------------
# Godot export
# --------------------------------------------------------------------------------------
GD_TERRAIN = r"""// Splat terrain for ainu_village.gltf (generated by ainu_village_scene.py).
// COLOR = (sand, earth, grass, rock). Grass is the earth set tinted green, rock is triplanar.
shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_back, diffuse_burley, specular_schlick_ggx;

uniform sampler2D sand_albedo : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D sand_normal : hint_normal, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D sand_orm : hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D earth_albedo : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D earth_normal : hint_normal, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D earth_orm : hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D rock_albedo : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D rock_normal : hint_normal, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D rock_orm : hint_default_white, filter_linear_mipmap_anisotropic, repeat_enable;

uniform vec4 sand_tint : source_color = vec4(0.86, 0.80, 0.72, 1.0);
uniform vec4 grass_tint : source_color = vec4(0.49, 0.60, 0.27, 1.0);
uniform float sand_tile = 3.0;
uniform float earth_tile = 2.5;
uniform float grass_tile = 2.0;
uniform float rock_tile = 5.0;
uniform float far_scale = 7.0;     // texture scale multiplier in the distance (hides repetition)
uniform float far_start = 35.0;
uniform float far_end = 160.0;
uniform float blend_depth : hint_range(0.01, 1.0) = 0.25;
uniform float normal_strength = 1.0;

varying vec3 v_wpos;
varying vec3 v_wnrm;
varying vec4 v_splat;

void vertex() {
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	v_wnrm = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
	v_splat = COLOR;
}

vec3 tri_albedo(sampler2D tex, vec3 p, vec3 n, float tile) {
	vec3 w = pow(abs(n), vec3(4.0));
	w /= (w.x + w.y + w.z);
	return texture(tex, p.zy / tile).rgb * w.x + texture(tex, p.xz / tile).rgb * w.y + texture(tex, p.xy / tile).rgb * w.z;
}

void fragment() {
	vec2 p = v_wpos.xz;
	float dist = length(v_wpos - CAMERA_POSITION_WORLD);
	float far = smoothstep(far_start, far_end, dist);
	float fs = mix(1.0, far_scale, far);

	vec3 a_s = texture(sand_albedo, p / (sand_tile * fs)).rgb * sand_tint.rgb;
	vec3 a_e = texture(earth_albedo, p / (earth_tile * fs)).rgb;
	vec3 a_g = texture(earth_albedo, p / (grass_tile * fs) + vec2(0.37, 0.71)).rgb * grass_tint.rgb;
	vec3 a_r = tri_albedo(rock_albedo, v_wpos, v_wnrm, rock_tile * fs);
	vec3 o_s = texture(sand_orm, p / (sand_tile * fs)).rgb;
	vec3 o_e = texture(earth_orm, p / (earth_tile * fs)).rgb;
	vec3 o_g = texture(earth_orm, p / (grass_tile * fs) + vec2(0.37, 0.71)).rgb;
	vec3 o_r = texture(rock_orm, p / (rock_tile * fs)).rgb;

	// height blend: AO is a stand-in for height, so pebbles and stones poke through the edges
	vec4 h = v_splat + vec4(o_s.r, o_e.r, o_g.r, o_r.r) * 0.35;
	float m = max(max(h.x, h.y), max(h.z, h.w)) - blend_depth;
	vec4 w = max(h - vec4(m), vec4(0.0));
	w /= (w.x + w.y + w.z + w.w + 1e-5);

	ALBEDO = a_s * w.x + a_e * w.y + a_g * w.z + a_r * w.w;
	vec3 orm = o_s * w.x + o_e * w.y + o_g * w.z + o_r * w.w;
	ROUGHNESS = orm.g;
	AO = orm.r;
	AO_LIGHT_AFFECT = 0.25;

	vec3 n_s = texture(sand_normal, p / sand_tile).rgb;
	vec3 n_e = texture(earth_normal, p / earth_tile).rgb;
	vec3 n_g = texture(earth_normal, p / grass_tile + vec2(0.37, 0.71)).rgb;
	vec3 n_r = texture(rock_normal, p / rock_tile).rgb;
	NORMAL_MAP = mix(n_s * w.x + n_e * w.y + n_g * w.z + n_r * w.w, vec3(0.5, 0.5, 1.0), far);
	NORMAL_MAP_DEPTH = normal_strength;
}
"""

GD_WATER = r"""// Sea for ainu_village.gltf (generated by ainu_village_scene.py). Forward+ / Mobile.
// Two scrolling normal layers, depth tint and shore foam from the depth buffer.
shader_type spatial;
render_mode blend_mix, depth_draw_always, cull_back, diffuse_burley, specular_schlick_ggx;

uniform sampler2D water_normal : hint_normal, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D water_albedo : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D depth_tex : hint_depth_texture, filter_linear_mipmap;

uniform vec4 shallow_color : source_color = vec4(0.22, 0.46, 0.52, 1.0);
uniform vec4 deep_color : source_color = vec4(0.02, 0.11, 0.25, 1.0);
uniform vec4 foam_color : source_color = vec4(0.93, 0.95, 0.95, 1.0);
uniform float tile = 8.0;
uniform vec2 wind = vec2(0.018, 0.011);
uniform float depth_falloff = 0.35;
uniform float foam_width = 0.9;
uniform float albedo_detail : hint_range(0.0, 1.0) = 0.25;
uniform float normal_strength = 1.0;

varying vec3 v_wpos;

void vertex() {
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec2 uv = v_wpos.xz / tile;
	vec3 n1 = texture(water_normal, uv + TIME * wind).rgb;
	vec3 n2 = texture(water_normal, uv * 0.43 - TIME * wind * 0.7 + vec2(0.31, 0.17)).rgb;
	NORMAL_MAP = normalize((n1 + n2) - 1.0) * 0.5 + 0.5;
	NORMAL_MAP_DEPTH = normal_strength;

	float raw = texture(depth_tex, SCREEN_UV).r;
	vec4 view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, raw, 1.0);
	float scene_z = -view.z / view.w;
	float depth = max(scene_z - (-VERTEX.z), 0.0);

	float deep = 1.0 - exp(-depth * depth_falloff);
	vec3 col = mix(shallow_color.rgb, deep_color.rgb, deep);
	col = mix(col, col * texture(water_albedo, uv * 0.5).rgb * 2.2, albedo_detail * (1.0 - deep));
	float wave = 0.5 + 0.5 * sin(TIME * 1.3 - v_wpos.x * 0.45 + n1.r * 3.0);
	float foam = (1.0 - smoothstep(0.0, foam_width * (0.6 + 0.6 * wave), depth));
	ALBEDO = mix(col, foam_color.rgb, foam);
	ROUGHNESS = mix(0.04, 0.7, foam);
	SPECULAR = 0.5;
	ALPHA = clamp(mix(0.55, 1.0, max(deep, foam)), 0.0, 1.0);
}
"""

GD_IMPORT = r"""@tool
extends EditorScenePostImport
## Post-import script for ainu_village.gltf (generated by ainu_village_scene.py).
## - Terrain / Sea get the bundled shader materials
## - FX_Smoke_* / FX_Fire_* markers become GPUParticles3D
## - props get visibility ranges (from the glTF extras "godot_vis_range", else by name)

const VIS_BY_PREFIX := {"Basket": 70.0, "Screen": 90.0, "Firewood": 120.0, "Rock_D": 90.0,
		"Rock_A": 140.0, "Rock_B": 140.0, "FishRack": 160.0, "LeanPoles": 180.0}

var _terrain_mat: Material
var _water_mat: Material
var _foliage_mats := {}


func _post_import(scene: Node) -> Object:
	var base := get_source_file().get_base_dir()
	_terrain_mat = load(base.path_join("materials/terrain_splat.tres"))
	_water_mat = load(base.path_join("materials/water.tres"))
	for pair in [["M_Foliage", "materials/foliage.tres"], ["M_FoliageGrass", "materials/foliage_grass.tres"]]:
		if FileAccess.file_exists(base.path_join(pair[1])):
			_foliage_mats[pair[0]] = load(base.path_join(pair[1]))
	_walk(scene, scene)
	return scene


func _walk(n: Node, root: Node) -> void:
	for c in n.get_children():
		_walk(c, root)
	var nm := String(n.name)
	if n is MeshInstance3D:
		var mi := n as MeshInstance3D
		if nm.begins_with("Terrain"):
			mi.material_override = _terrain_mat
		elif nm.begins_with("Sea"):
			mi.material_override = _water_mat
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		for s in mi.mesh.get_surface_count():
			var sm := mi.mesh.surface_get_material(s)
			if sm and _foliage_mats.has(sm.resource_name):
				mi.set_surface_override_material(s, _foliage_mats[sm.resource_name])
		if nm.begins_with("Flora_Grass"):
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		var r := _vis_range(mi)
		if r > 0.0:
			mi.visibility_range_end = r
			mi.visibility_range_end_margin = r * 0.1
			# alpha-clipped flora pops instead of fading (a fade would make it transparent)
			mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_DISABLED if nm.begins_with("Flora") \
					else GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
	elif nm.begins_with("FX_Smoke"):
		_add(n, root, _smoke(1.1 if nm.contains("FirePit") else 2.6))
	elif nm.begins_with("FX_Fire"):
		_add(n, root, _fire())


func _vis_range(mi: MeshInstance3D) -> float:
	var ex = mi.get_meta("extras", {})
	if ex is Dictionary and ex.has("godot_vis_range"):
		return float(ex["godot_vis_range"])
	for p in VIS_BY_PREFIX:
		if String(mi.name).begins_with(p):
			return VIS_BY_PREFIX[p]
	return 0.0


func _add(parent: Node, root: Node, p: Node) -> void:
	parent.add_child(p)
	p.owner = root


func _soft_quad(size: float, emissive: bool) -> QuadMesh:
	var g := Gradient.new()
	g.offsets = PackedFloat32Array([0.0, 1.0])
	g.colors = PackedColorArray([Color(1, 1, 1, 1), Color(1, 1, 1, 0)])
	var tex := GradientTexture2D.new()
	tex.gradient = g
	tex.fill = GradientTexture2D.FILL_RADIAL
	tex.fill_from = Vector2(0.5, 0.5)
	tex.fill_to = Vector2(0.5, 0.0)
	tex.width = 64
	tex.height = 64
	var m := StandardMaterial3D.new()
	m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	m.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	m.vertex_color_use_as_albedo = true
	m.albedo_texture = tex
	m.proximity_fade_enabled = true
	m.proximity_fade_distance = 0.6
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	if emissive:
		m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		m.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	var q := QuadMesh.new()
	q.size = Vector2(size, size)
	q.material = m
	return q


func _ramp(colors: PackedColorArray, offsets: PackedFloat32Array) -> GradientTexture1D:
	var g := Gradient.new()
	g.offsets = offsets
	g.colors = colors
	var t := GradientTexture1D.new()
	t.gradient = g
	return t


func _smoke(size: float) -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.name = "Smoke"
	p.amount = 18
	p.lifetime = 7.0
	p.preprocess = 7.0
	p.visibility_aabb = AABB(Vector3(-5, -1, -5), Vector3(10, 16, 10))
	p.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var pm := ParticleProcessMaterial.new()
	pm.direction = Vector3(0, 1, 0)
	pm.spread = 10.0
	pm.initial_velocity_min = 0.4
	pm.initial_velocity_max = 0.8
	pm.gravity = Vector3(0.25, 0.12, -0.1)
	pm.damping_min = 0.05
	pm.damping_max = 0.1
	pm.scale_min = 0.7
	pm.scale_max = 1.2
	var c := Curve.new()
	c.add_point(Vector2(0, 0.35))
	c.add_point(Vector2(1, 1.0))
	var ct := CurveTexture.new()
	ct.curve = c
	pm.scale_curve = ct
	pm.color_ramp = _ramp(PackedColorArray([Color(0.8, 0.8, 0.8, 0.0), Color(0.82, 0.82, 0.82, 0.22),
			Color(0.9, 0.9, 0.9, 0.0)]), PackedFloat32Array([0.0, 0.2, 1.0]))
	p.process_material = pm
	p.draw_pass_1 = _soft_quad(size, false)
	return p


func _fire() -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.name = "Fire"
	p.amount = 24
	p.lifetime = 0.9
	p.visibility_aabb = AABB(Vector3(-1, -0.5, -1), Vector3(2, 3, 2))
	p.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var pm := ParticleProcessMaterial.new()
	pm.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	pm.emission_sphere_radius = 0.18
	pm.direction = Vector3(0, 1, 0)
	pm.spread = 8.0
	pm.initial_velocity_min = 0.6
	pm.initial_velocity_max = 1.1
	pm.gravity = Vector3(0, 0.4, 0)
	pm.scale_min = 0.5
	pm.scale_max = 0.9
	pm.color_ramp = _ramp(PackedColorArray([Color(1.0, 0.75, 0.3, 0.0), Color(1.0, 0.55, 0.15, 0.9),
			Color(0.6, 0.15, 0.05, 0.0)]), PackedFloat32Array([0.0, 0.2, 1.0]))
	p.process_material = pm
	p.draw_pass_1 = _soft_quad(0.55, true)
	var light := OmniLight3D.new()
	light.name = "FireLight"
	light.light_color = Color(1.0, 0.6, 0.3)
	light.light_energy = 1.6
	light.omni_range = 6.0
	light.position = Vector3(0, 0.5, 0)
	p.add_child(light)
	return p
"""


def _tres_shader_material(shader_rel, params, textures):
    lines = ['[gd_resource type="ShaderMaterial" load_steps=%d format=3]' % (len(textures) + 2), ""]
    lines.append('[ext_resource type="Shader" path="%s" id="1_shader"]' % shader_rel)
    ids = {}
    for i, (uni, rel) in enumerate(textures.items()):
        ids[uni] = "%d_tex" % (i + 2)
        lines.append('[ext_resource type="Texture2D" path="%s" id="%s"]' % (rel, ids[uni]))
    lines += ["", "[resource]", 'shader = ExtResource("1_shader")']
    for uni in textures:
        lines.append('shader_parameter/%s = ExtResource("%s")' % (uni, ids[uni]))
    for k, v in params.items():
        lines.append("shader_parameter/%s = %s" % (k, v))
    return "\n".join(lines) + "\n"


def _blender_to_godot(v):
    return Vector((v.x, v.z, -v.y))


def _basis_looking(d):
    """Godot basis whose -Z points along d (rows written row-major for Transform3D)."""
    z = -d.normalized()
    up = Vector((0, 1, 0)) if abs(z.y) < 0.99 else Vector((1, 0, 0))
    x = up.cross(z).normalized()
    y = z.cross(x)
    return [[x[i], y[i], z[i]] for i in range(3)]


def write_godot_files(state, res_dir):
    gd = state["godot_dir"]
    for sub in ("shaders", "materials"):
        os.makedirs(os.path.join(gd, sub), exist_ok=True)
    with open(os.path.join(gd, "shaders", "terrain_splat.gdshader"), "w", newline="\n") as fh:
        fh.write(GD_TERRAIN)
    with open(os.path.join(gd, "shaders", "water.gdshader"), "w", newline="\n") as fh:
        fh.write(GD_WATER)
    with open(os.path.join(gd, "village_import.gd"), "w", newline="\n") as fh:
        fh.write(GD_IMPORT)
    T = lambda n, k: "../textures/T_%s_%s" % (n, k)
    terrain = _tres_shader_material("../shaders/terrain_splat.gdshader", {}, {
        "sand_albedo": T("Sand", "BaseColor.jpg"), "sand_normal": T("Sand", "Normal.png"), "sand_orm": T("Sand", "ORM.png"),
        "earth_albedo": T("Earth", "BaseColor.jpg"), "earth_normal": T("Earth", "Normal.png"), "earth_orm": T("Earth", "ORM.png"),
        "rock_albedo": T("Stone", "BaseColor.jpg"), "rock_normal": T("Stone", "Normal.png"), "rock_orm": T("Stone", "ORM.png")})
    water = _tres_shader_material("../shaders/water.gdshader", {}, {
        "water_normal": T("Water", "Normal.png"), "water_albedo": T("Water", "BaseColor.jpg")})
    with open(os.path.join(gd, "materials", "terrain_splat.tres"), "w", newline="\n") as fh:
        fh.write(terrain)
    with open(os.path.join(gd, "materials", "water.tres"), "w", newline="\n") as fh:
        fh.write(water)
    if state.get("flora"):
        FL = state["flora"]["module"]
        with open(os.path.join(gd, "shaders", "foliage.gdshader"), "w", newline="\n") as fh:
            fh.write(FL["GD_FOLIAGE"])
        for fn, up, wind in (("foliage_grass.tres", True, 0.07), ("foliage.tres", False, 0.05)):
            with open(os.path.join(gd, "materials", fn), "w", newline="\n") as fh:
                fh.write(FL["godot_material_tres"](up, wind))

    # import settings for the glTF: post-import script, LODs, shadow meshes
    imp = "\n".join([
        "[remap]", "", 'importer="scene"', "importer_version=1", 'type="PackedScene"', "",
        "[params]", "",
        "nodes/root_type=\"\"", "nodes/root_name=\"AinuVillage\"", "nodes/apply_root_scale=true",
        "nodes/root_scale=1.0", "meshes/ensure_tangents=true", "meshes/generate_lods=true",
        "meshes/create_shadow_meshes=true", "meshes/light_baking=1", "meshes/lightmap_texel_size=0.2",
        "skins/use_named_skins=true", "animation/import=false",
        'import_script/path="%s/village_import.gd"' % res_dir.rstrip("/"),
        "_subresources={}", "gltf/naming_version=2", "gltf/embedded_image_handling=1", ""])
    with open(os.path.join(gd, "ainu_village.gltf.import"), "w", newline="\n") as fh:
        fh.write(imp)
    # texture import settings: VRAM compressed with mipmaps; normal maps as normal maps (RGTC,
    # OpenGL +Y as authored); ORM and normals stay linear. Godot fills in uid/dest on import.
    for fn in sorted(os.listdir(os.path.join(gd, "textures"))):
        if not fn.startswith("T_") or fn.endswith(".import"):
            continue
        normal = "_Normal." in fn
        timp = "\n".join([
            "[remap]", "", 'importer="texture"', 'type="CompressedTexture2D"', "", "[params]", "",
            "compress/mode=2", "compress/high_quality=false", "compress/lossy_quality=0.7",
            "compress/normal_map=%d" % (1 if normal else 2), "compress/channel_pack=0",
            "mipmaps/generate=true", "mipmaps/limit=-1", "roughness/mode=0",
            "process/fix_alpha_border=true", "process/normal_map_invert_y=false",
            "detect_3d/compress_to=0", ""])
        with open(os.path.join(gd, "textures", fn + ".import"), "w", newline="\n") as fh:
            fh.write(timp)

    # main scene: environment, sun, the village
    sd = _blender_to_godot(state["sun_dir"])
    B = _basis_looking(-sd)
    tr = "Transform3D(%s, 0, 60, 0)" % ", ".join("%.6f" % B[i][j] for i in range(3) for j in range(3))
    tscn = "\n".join([
        '[gd_scene load_steps=5 format=3]', "",
        '[ext_resource type="PackedScene" path="ainu_village.gltf" id="1_village"]', "",
        '[sub_resource type="ProceduralSkyMaterial" id="Sky_mat"]',
        "sky_top_color = Color(0.22, 0.42, 0.76, 1)", "sky_horizon_color = Color(0.66, 0.77, 0.88, 1)",
        "ground_bottom_color = Color(0.16, 0.22, 0.3, 1)", "ground_horizon_color = Color(0.66, 0.77, 0.88, 1)",
        "sun_angle_max = 20.0", "",
        '[sub_resource type="Sky" id="Sky_1"]', 'sky_material = SubResource("Sky_mat")', "",
        '[sub_resource type="Environment" id="Env_1"]',
        "background_mode = 2", 'sky = SubResource("Sky_1")', "ambient_light_source = 3",
        "ambient_light_sky_contribution = 0.85", "tonemap_mode = 3", "tonemap_white = 6.0",
        "ssao_enabled = true", "ssao_radius = 1.2", "glow_enabled = true", "glow_intensity = 0.25",
        "fog_enabled = true", "fog_light_color = Color(0.68, 0.77, 0.87, 1)", "fog_density = 0.0006",
        "fog_aerial_perspective = 0.6", "fog_sky_affect = 0.0", "",
        '[node name="AinuVillageMain" type="Node3D"]', "",
        '[node name="WorldEnvironment" type="WorldEnvironment" parent="."]', 'environment = SubResource("Env_1")', "",
        '[node name="Sun" type="DirectionalLight3D" parent="."]', "transform = %s" % tr,
        "light_color = Color(1, 0.94, 0.84, 1)", "light_energy = 1.25", "shadow_enabled = true",
        "directional_shadow_max_distance = 180.0", "",
        '[node name="Village" parent="." instance=ExtResource("1_village")]', ""])
    with open(os.path.join(gd, "ainu_village_main.tscn"), "w", newline="\n") as fh:
        fh.write(tscn)
    with open(os.path.join(gd, "README_Godot.md"), "w", newline="\n") as fh:
        fh.write(README_GODOT.replace("{RES}", res_dir.rstrip("/")))


README_GODOT = """# Ainu village - Godot 4 import

Generated by `blender/ainu_village_scene.py`. Copy this whole folder into your Godot project at
`{RES}/` (or re-run the script with `--godot-res-dir res://your/path`). Open
`ainu_village_main.tscn` and press F6 to see the concept camera.

| file | what it is |
|---|---|
| `ainu_village.gltf` + `.bin` | the village: terrain, sea, houses, props, colliders, smoke markers, camera |
| `ainu_village.gltf.import` | import settings: LOD generation, shadow meshes, post-import script |
| `village_import.gd` | post-import script: terrain and sea shaders, smoke and fire particles, visibility ranges |
| `textures/T_*_BaseColor.jpg` | albedo (sRGB) |
| `textures/T_*_Normal.png` | normal map, OpenGL / +Y (Godot convention, no flip needed) |
| `textures/T_*_ORM.png` | R = AO, G = roughness, B = metallic (glTF / Godot ORM layout) |
| `shaders/terrain_splat.gdshader` | 4-layer splat from vertex colour (sand, earth, grass, rock) |
| `shaders/water.gdshader` | scrolling normals, depth tint and shore foam (Forward+ / Mobile) |
| `shaders/foliage.gdshader` | grass and plants: alpha clip, wind sway from COLOR_0, backlight |
| `materials/*.tres` | ShaderMaterials for the shaders, wired to the textures |
| `textures/T_Foliage_*.png` | foliage atlas painted by ainu_flora.py (BaseColor has alpha) |
| `ainu_village_main.tscn` | sky, fog, sun with shadows, and the village instance |
| `prefabs/*.gltf` | (with `--export-prefabs`) every prefab on its own, pivot at the origin |

Collision comes from Godot's import hints. `Terrain-col` gets a trimesh body, and every
`*-convcolonly` node becomes a StaticBody3D with a convex shape (walls are split around the
door, and the roof hull starts at the 2 m door clearance).

If the import settings did not apply (for example because the folder sits somewhere other than
`{RES}`), select `ainu_village.gltf`, open the Import dock, set **Import Script** to
`village_import.gd` and click **Reimport**.

Normal maps are detected on first use. If any T_*_Normal.png shows banding, set
**Normal Map = Enable** in its Import dock.
"""


def _select_export(objs):
    vl = bpy.context.view_layer
    vl.update()
    for o in bpy.context.scene.objects:
        try:
            o.select_set(False)
        except RuntimeError:
            pass
    for o in objs:
        try:
            o.hide_set(False)
            o.select_set(True)
        except RuntimeError:
            pass
    if objs:
        vl.objects.active = objs[0]


def _gltf(path, objs, texdir="textures"):
    _select_export(objs)
    kw = dict(filepath=path, export_format="GLTF_SEPARATE", export_texture_dir=texdir, use_selection=True,
              export_extras=True, export_cameras=True, export_lights=False, export_vertex_color="ACTIVE",
              export_all_vertex_colors=False, export_active_vertex_color_when_no_material=False,
              export_image_format="AUTO", export_yup=True, export_apply=False, export_tangents=False)
    bpy.ops.export_scene.gltf(**kw)


def export_godot(state, res_dir, prefabs=False):
    gd = state["godot_dir"]
    cols = state["cols"]
    objs = list(cols["world"].objects) + list(cols["collision"].objects) + list(cols["fx"].objects) + [state["camera"]]
    if state.get("flora"):
        objs += state["flora"]["chunks"]
    path = os.path.join(gd, "ainu_village.gltf")
    _gltf(path, objs)
    log("exported", path)
    if prefabs:
        pdir = os.path.join(gd, "prefabs")
        os.makedirs(pdir, exist_ok=True)
        tmp = collection("_AV_tmp")
        for key, it in state["items"].items():
            ob = bpy.data.objects.new(key, it["mesh"])
            tmp.objects.link(ob)
            extra = [ob]
            for i, cm in enumerate(it["colliders"]):
                c = bpy.data.objects.new("%s_COL%02d-convcolonly" % (key, i), cm)
                c.parent = ob
                tmp.objects.link(c)
                extra.append(c)
            _gltf(os.path.join(pdir, key + ".gltf"), extra, "../textures")
            for o in extra:
                bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(tmp)
        log("exported %d prefabs to %s" % (len(state["items"]), pdir))
    write_godot_files(state, res_dir)


# --------------------------------------------------------------------------------------
# Render, CLI
# --------------------------------------------------------------------------------------
def render(path, samples):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = 1672, 941
    sc.view_settings.view_transform = "AgX" if "AgX" in [i.identifier for i in
                                                          sc.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic"
    sc.view_settings.exposure = -0.75
    sc.render.filepath = os.path.abspath(path)
    os.makedirs(os.path.dirname(sc.render.filepath), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    log("rendered", sc.render.filepath)


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
    if "tex-res" in opts:
        CONFIG["tex_res"] = int(opts["tex-res"])
    if "interiors" in opts:
        CONFIG["interiors"] = True
    res_dir = opts.get("godot-res-dir", CONFIG["godot_res_dir"])
    state = build(opts)
    if "godot-dir" in opts:
        export_godot(state, res_dir, "export-prefabs" in opts)
    if "render" in opts:
        render(opts["render"], int(opts.get("samples", CONFIG["samples"])))
    if "save" in opts:
        p = os.path.abspath(opts["save"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=p, copy=True)
        log("saved", p)


if __name__ == "__main__":
    main()
