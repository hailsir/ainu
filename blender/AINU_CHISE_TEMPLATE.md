# Template: procedural game-ready building in Blender (Ainu chise reference build)

This is a template for making a procedural, game-optimised building model with a Python script for
Blender. Its reference build is `ainu_chise.py`, a traditional Ainu thatched house. Use this file
when you need another building (a different chise, a *pu* storehouse, any thatched or timber
structure) so you don't have to repeat the discovery work. It records the environment, the
architecture, the conventions, the formulas, the API pitfalls and the verification loop.

---

## 1. Environment (already verified, don't rediscover)

| Item | Value |
|---|---|
| Blender | `C:\Program Files\Blender Foundation\Blender 5.0\blender.exe` (5.0.1, Python 3.11) |
| Working folder | `C:\Users\Erek\Documents\Claude` (reference photos live here too) |
| Shell | Bash tool = Git Bash. Use POSIX paths: `"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe"` |
| Python outside Blender | No PIL. To crop or inspect images, render a close-up camera in Blender (see §7) |
| Render engine for previews | **Cycles CPU** at 24 samples with denoise, 1280×720: about 10 s exterior, about 20 s interior. Don't use EEVEE headless |

Headless run (build, render previews, export, save):

```bash
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background --factory-startup --python ainu_chise.py -- --render out/exterior.png --render-rear out/rear.png --render-interior out/interior.png --export out/ainu_chise.glb --save out/ainu_chise.blend --samples 24
```

- `--render-<view> path`: renders any entry in `PREVIEW_VIEWS`. `--render` means `exterior`.
- `--export x.glb` exports the LOD0 meshes. `--export x.fbx` exports LOD0 plus the `UCX_` collision.
- Run one export per invocation if you need both formats.
- Interactive use: in the Scripting workspace, Open, then Run Script. The script rebuilds idempotently
  and only touches its own collections.
- In `--background` mode the script first deletes every object, which removes the factory cube, camera and light.

---

## 2. Request template (fill in for the next building)

```
Building:        <name / culture / type>
References:      <photo files: exterior, interior, historic>
Footprint:       <L x W m>  (state whether measured at outer wall faces; chise = outer faces)
Heights:         wall <m>, ridge <m> (or pitch in degrees)
Roof form:       hip | gable | hip-and-gable (chise) | pyramid (pu) ; thatch tiers yes/no
Openings:        door(s) + window(s) with wall side, width, height, sill
Interior:        hearth, frame, props wanted?
Budget:          max tris for LOD0 (chise: 30,000) ; target engine (Unreal/Unity/Godot)
```

Defaults if the request is silent: Z-up metres, origin at floor centre, LOD0 + 2 exterior LODs,
UCX collision, world-scale UVs, procedural materials, and three preview renders.

---

## 3. Script architecture (copy `ainu_chise.py` and edit; keep this layout)

```
CONFIG            all user-facing dimensions & openings (the only thing most variants change)
LOD_QUALITY[0..2] per-LOD exterior detail (tiers, ring resolution, which details exist)
MATERIALS         name -> procedural spec (pattern + colour ramp + roughness/bump)
PREVIEW_VIEWS     name -> (camera loc, target, lens mm, exposure)  => --render-<name>

helpers           hn() hash noise, srgb(), newell(), box_uv()
MeshBuilder       python lists of verts/faces/mat/smooth/uv -> ONE object via to_object()
                  face(..., facing=vec) auto-fixes winding; >4-gons are tessellated to tris
primitives        loft(rows) | tube(polyline) | obox()/box() | lathe(profile) | ring(rounded rect)
Geo               every derived dimension + roof_z()/z_in_side()/z_in_end()/door_lift()
build_*           build_lower_roof, build_upper_roof, build_ridge, build_wall  -> build_exterior(q)
                  build_frame (interior timber), build_props, build_collision
make_material     node graph from MATERIALS spec
build()           reset collections -> materials -> objects -> LODs -> UCX -> preview -> tri report
CLI               _cli_args, _render(view), _export
```

Design rules that made this work on the first headless run:
- **Don't use `bpy.ops` for modelling.** All geometry is computed in Python and created with
  `mesh.from_pydata`. That makes it deterministic, avoids context errors in `--background`, and
  is fast (under 1 s).
- **One builder per output object**, with material slots added on demand. This merges parts and
  keeps draw calls low without needing `object.join`.
- **All randomness comes from `hn(*ints)`**, a seeded hash, so reruns are identical. Change `CONFIG["seed"]` for a variant.
- **Every part is a function of `Geo`**, so changing the footprint or pitch moves everything
  consistently. Never hard-code a world height that depends on the roof. Use `G.z_in_side(y)`,
  `G.z_in_end(x)` or `G.roof_z(x, y)`.

---

## 4. Conventions

| Topic | Convention |
|---|---|
| Axes / units | Z up, metres, 1 BU = 1 m. Long axis = X. Origin (0,0,0) = floor centre, so the object origin sits on the ground |
| Orientation | Door on the −X gable end (W). Sacred window *rorun-puyar* on +X (E). Side windows on −Y (S) and +Y (N) |
| Wall keys | `S` and `N` are long walls (+u = +X / −X). `E` and `W` are gable ends (+u = +Y / −Y). The short walls fit between the long walls so corners don't z-fight |
| Objects | `SM_<Name>_Exterior` (roof, walls, frames, poles), `SM_<Name>_Frame` (interior timber), `SM_<Name>_Props` |
| LODs | `SM_<Name>_Exterior_LOD1`, `_LOD2` in the hidden collection `<Name>_LODs`. They are exterior-only, so cull Frame and Props by distance in the engine |
| Collision | `UCX_SM_<Name>_Exterior_NN`: convex boxes for the walls (split around the door), a floor slab, and a roof hull that starts at `door_clearance` (Unreal convention; Godot uses `-col`, Unity uses separate mesh colliders) |
| Collections | `<Name>` > `_LOD0`, `_LODs`, `_Collision`, `_Preview` (camera, sun, ground ring, interior fill; never exported) |
| UVs | World scale (1 UV = 1 m), tiling. **V follows the fibres**: up walls and slopes, and along poles. Lofts use (arc length, z). Tubes use (circumference, length). Everything else uses box projection |
| Shading | Per-face smooth flags. Thatch surfaces and poles are smooth. Bands, lip undersides, caps and boxes are flat. Flat neighbours act as sharp edges, so no auto-smooth is needed |
| Polygons | Quads and tris only. n-gons are tessellated in `MeshBuilder.face`, because FBX can't build tangent space for n-gons |
| Winding | Always pass `facing=` for hand-placed polys. Lofts rely on row order (see the comments in `build_upper_roof`) |
| Budget | Printed at the end of every run: `LOD0 total N / max_tris`. It warns if over |

---

## 5. Geometry recipe (Ainu chise)

Reference reading of the photos: there is a low reed/bamboo-grass wall with 2–3 horizontal binding
poles. The roof is a steep, very thick thatch **hip roof with a small smoke gable** under each
ridge end (a hip-and-gable form). The thatch is laid in **visible stepped courses**
(the historic photo and the *pu* photo) and has **rounded hip corners**. The ridge has a thick
bundle, **pairs of crossed sticks** and a long pole resting in the crossings. Inside there is a
central sunken **hearth** (*apeoi*) with a wooden frame, a **drying rack** (*tuna*) hung above it,
a pot on a hook, log **tie beams**, **A-frames** carrying the ridge pole, radiating **rafters**,
**purlins**, floor mats (*kina*), patterned wall mats, and a treasure shelf with lacquerware plus
the house-guardian *inau* (*chisekorkamuy*) in the north-east corner.

Derived values (`Geo.__init__`). `P` and `Q` are the side and end pitches, `t` is the thatch vertical thickness:

```
Ly = Wy + overhang
Lx = Wx + (ov + wt)·tanP/tanQ − wt        # end overhang chosen so the eave is level
zE = zW − (ov + wt)·tanP + t              # underside passes exactly through the inner wall top
zR = zE + Ly·tanP                         # ridge
zG = zR − gable_height                    # base of smoke gable
yg = (zR − zG)/tanP ,  Gx = Lx − (zG − zE)/tanQ   # half-size of hip top / gable position
X(h) = Lx − (h−zE)/tanQ ,  Y(h) = Ly − (h−zE)/tanP # hip ring at height h (rounded rect, r tapers rc0→rc1)
wall outer top = zW − wt·tan(pitch)       # wall tops are bevelled parallel to the roof underside
```

Defaults: 5.0 × 4.0 m footprint = **20 m²**. Wall height 2.3 m, wall thickness 0.22 m, pitches
52° and 54°, so the ridge is at 4.93 m and the ridge-stick tops are at about 5.2 m. The eave
bottom is 1.51 m, but over the door it is notched up to 2.0 m (`door_lift`: eave points slide up
the roof plane, so the surface stays planar and no gaps open).

Construction per part:
1. **Lower roof**: `tiers` courses. Each course is a loft of 3 rings (lip at the bottom, a bulge
   at mid height, the base surface at the top) plus a flat underside strip back to the course
   below. The inner surface is a separate loft with tighter corners (`inner_corner_factor`), and
   there are flat eave and top bands. Lip jitter is **outward-only**: two-sided jitter let a lip
   sink into the course below and exposed the underside faces as light flecks.
2. **Upper roof**: one closed cross-section (stepped outer profile, then the inner V) lofted
   along X, with concave end caps that get tessellated. The two-sided smoke-gable triangles sit
   at `x = ±Gx`, and their edges follow the underside line exactly.
3. **Ridge**: an elliptical thatch bundle, crossed stick pairs, and a top pole.
4. **Walls**: a grid built from cut lines (wall ends, opening edges, and extra cuts for an
   outward-only bulge jitter). Cells inside openings are skipped. The reveals reuse the same
   jittered `outer()` function, so there are no cracks. Pole frames and a rolled reed blind are
   added per opening, and binding poles are broken around the openings. **Top vertices are
   clamped to `G.roof_z(x, y) − 0.05`.** Without the clamp, the rounded hip corners let the wall
   corners poke through the thatch.
5. **Frame**: posts (they skip openings), wall plates, 3 tie beams, 2 A-frames standing on the
   outer tie beams (they must stay inside `x < Gx` or they pierce the hip), a ridge pole,
   rafters lying 0.14 m under the underside, hip rafters that follow the rounded corner diagonal,
   and purlin rings.
6. **Props**: the floor (with a hole for the hearth and a threshold under the door), the hearth
   pit with ash, emissive embers, the frame and firewood, mats, two patterned hangings, the rack
   with ropes up to the underside, a rod with the fish-shaped hook and an iron pot, the
   lacquerware shelf, and the inau.

---

## 6. Blender 5.0 API notes (verified)

- `Material.use_nodes` and `World.use_nodes` are **deprecated** (to be removed in 6.0), and new
  materials already have a node tree. Only set `use_nodes = True` if `node_tree is None`.
- `ShaderNodeMix` has duplicate socket names. Avoid colour mixing: drive **one ColorRamp from a
  scalar** built with `ShaderNodeMath` (`MULTIPLY`, `ADD`, `PINGPONG`, `MINIMUM`). That scalar also feeds Bump.
- Principled BSDF sockets: `Base Color`, `Roughness`, `Metallic`, `Normal`, `Emission Color`, `Emission Strength`.
- `polygons.foreach_set("use_smooth", ...)` still works. The fallback is the `sharp_face` attribute.
- After deleting objects in a session, `view_layer.objects` can return `None`. Call
  `view_layer.update()` and iterate `scene.objects` before `select_set`.
- glTF: `bpy.ops.export_scene.gltf(filepath, export_format="GLB", use_selection=True)`.
  FBX: `bpy.ops.export_scene.fbx(..., use_selection=True, apply_scale_options="FBX_SCALE_UNITS", mesh_smooth_type="FACE", use_tspace=True)`.
- Cycles headless works: `scene.cycles.samples` and `use_denoising = True`. The exposure is in `scene.view_settings.exposure`.
- `mathutils.geometry.tessellate_polygon([[Vector,...]])` triangulates concave polygons correctly.

---

## 7. Verification loop (do this every time)

1. Run headless with `--render`, `--render-rear` and `--render-interior`, then **Read the PNGs**.
   Check the tri report and confirm there's no `Traceback` or `Warning`.
2. For a suspicious spot, render a close-up without editing the script. Use a scratchpad driver:
   ```python
   import bpy, sys, runpy
   sys.argv = ["blender", "--"]
   for ob in list(bpy.data.objects): bpy.data.objects.remove(ob, do_unlink=True)
   m = runpy.run_path(r"C:\Users\Erek\Documents\Claude\ainu_chise.py", run_name="chise")
   m["build"]()
   m["PREVIEW_VIEWS"]["close"] = ((4.6, 4.2, 3.6), (2.0, 1.6, 2.6), 40, 0.0)
   m["_render"](r"<scratchpad>\close.png", 16, "close")
   ```
3. Export both GLB and FBX and confirm FBX prints no tangent-space warnings.

Defects already hit and fixed (check for these first in a new variant):

| Symptom | Cause | Fix |
|---|---|---|
| Light flecks on the roof | A course lip sank into the course below, exposing the underside faces | Outward-only lip jitter (`outward=True`) |
| Tan sliver at a hip corner | The wall's top corner poked through the rounded thatch | Clamp the wall top to `roof_z − 0.05` |
| Green "ash" in the hearth | The preview ground plane covered the sunken pit | Make the ground a ring around the footprint |
| Player can't enter | The eave bottom (zE − t ≈ 1.5 m) blocked the door | `door_clearance` eyebrow notch, and the collision roof hull starts at that height |
| FBX tangent warnings | n-gon caps | Tessellate >4-gons in `MeshBuilder.face` |
| A-frame pierced the roof | The legs reached past the smoke-gable line `Gx` | Stand the A-frames on tie beams at `x ≤ Gx − 0.05` |
| Gaps where the roof meets the walls | The roof underside was not tied to the wall top | Derive `zE` from the inner wall top, and bevel the wall top along the pitch |

---

## 8. Result of the reference build

| Object | Tris | Materials |
|---|---:|---:|
| SM_AinuChise_Exterior | 5,320 | 5 |
| SM_AinuChise_Frame | 1,448 | 1 |
| SM_AinuChise_Props | 1,066 | 9 |
| **LOD0 total** | **7,834 / 30,000** | |
| Exterior_LOD1 / LOD2 | 1,822 / 502 | 5 / 3 |
| UCX collision (8 hulls) | 92 | — |

Outputs: `out/ainu_chise.glb`, `out/ainu_chise.fbx`, `out/ainu_chise.blend`, and
`out/exterior.png`, `rear.png`, `interior.png`.

There is plenty of budget left. If more detail is wanted, raise `LOD_QUALITY[0]` (`tiers` 8,
`nx` 20, `ny` 14, `nc` 6 measures 8,192 tris for the exterior) before adding new geometry.

---

## 9. Adapting to a new building (checklist)

1. Copy `ainu_chise.py` to `<name>.py`. Set `CONFIG["name"]`. Object, collection and UCX names follow it.
2. Footprint, heights and pitches: edit `CONFIG`, run, and read the printed eave and ridge heights.
   `Geo` raises an error if the roof can't close (if so, lower `gable_height` or `pitch_end`).
3. Openings: edit `CONFIG["openings"]`. Validation rejects openings closer than 0.25 m to a wall
   end, or taller than the wall's outer top minus 0.12 m. Exactly one `kind: "door"` drives the eave notch and the collision gap.
4. Roof form:
   - **Pure hip**: set `gable_height` very small and drop `build_upper_roof`, or make the ridge a short bundle only.
   - **Pyramid** (e.g. a *pu* storehouse): use `length == width` and the same pitch for both, so `Gx ≈ 0`. Skip the upper roof and cap the top ring with a bundle.
   - **Gable**: a loft of a profile along X like `build_upper_roof`, applied from the eave, with walls rising into the gables.
5. *Pu* (raised storehouse, 5th reference photo): a small square body (about 2 × 2 m) on 4–6 posts
   about 1.2 m high, a floor slab at the top of the posts, a 3–4 tier pyramid thatch, a door on
   one side and no windows. Reuse `build_wall`, `roof_ring`/`loft`, `tube` for the posts, and the
   collision boxes. Offset every z by the post height (or add `base_z` to `Geo`).
6. Interior: re-place the props relative to `G.Wxi`, `G.Wyi` and the hearth config. Keep paths
   clear of the door, and keep ropes, poles and the rack out of the tie beams' x positions.
7. Materials: add an entry to `MATERIALS`. Keep each object at 5 or fewer materials where you can,
   because each material is one draw call. Props currently use 9; atlas them if that matters.
8. Run the verification loop (§7) and update the table in §8 for the new build.

---

## 10. Known limitations and next steps

- **Procedural materials don't export** to engines (GLB/FBX carry geometry, UVs and material
  slots only). For engine use, either bake each material to tiling textures with Cycles Bake
  using the existing world-scale UVs, or assign engine materials per slot name.
- There is no second (lightmap) UV channel. Let Unreal generate one on import, or add a Smart UV
  Project pass if baked lighting is needed.
- The door is an open doorway with a rolled reed curtain (*apa-sa*) above it. A hanging door mat could be added as a separate prop.
- The traditional entrance vestibule (*sem*), the outdoor *nusa* altar (opposite the sacred
  window), and the *pu* storehouse are not modelled. They would be separate assets.
