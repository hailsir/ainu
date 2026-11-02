# Ainu coastal village + Rishiri flora

`ainu_village_scene.py` builds the coastal kotan from the concept art out of the chise kit
(`ainu_chise_kit.py`), and exports a folder that you can drop into Godot 4. `ainu_flora.py`
paints a foliage atlas in code, models eight Rishiri / Hokkaido plants and scatters them over
the village. The village script runs it automatically.

![the village in Godot 4.6](docs/village_godot.jpg)

## Run it

Headless, from this folder (Blender 5.0). The village needs the atlas textures folder
`ainu_atlas_derived_PBR_4K`, either next to the repo or given with `--tex-src`:

```bash
blender --background --factory-startup --python ainu_village_scene.py -- --godot-dir out/godot/ainu_village --export-prefabs --render out/village.png --save out/ainu_village.blend
```

| option | what it does |
|---|---|
| `--godot-dir dir` | glTF, textures, shaders, materials, import script and main scene for Godot |
| `--godot-res-dir res://path` | where that folder lives in the Godot project (default `res://ainu_village`) |
| `--export-prefabs` | also one `.gltf` per prefab (houses, pu, racks, boats, rocks, every plant) |
| `--tex-src dir` / `--tex-res n` / `--force-textures` | atlas folder, game texture size (2048), rebuild textures |
| `--no-flora` | skip grass and plants |
| `--interiors` | 5 m houses keep the kit's interior props |
| `--render file.png` / `--samples n` / `--save file.blend` | Cycles render from the concept camera, save the scene |

Plants on their own (atlas, a lineup render and `flora_species.gltf`):

```bash
blender --background --factory-startup --python ainu_flora.py -- --out out/flora --render out/flora.png
```

Godot: copy `out/godot/ainu_village` to `res://ainu_village`, then open `ainu_village_main.tscn`.
The generated `README_Godot.md` lists every file.

## The village

- **Houses:** five chise, all built from kit parts. The 7 m houses insert 1 m straights the same
  way the kit demo does. Each house is merged into one mesh (4-6 surfaces), and every copy shares it.
- **Props:** pu storehouses, fish racks, a fire pit (with the kit's pot), firewood, baskets, lean-to
  poles, screens, dugout boats, rocks and a fence.
- **Layout:** placed from the concept's framing (place across the frame plus distance from the camera).
- **Terrain:** one mesh with sinh-spaced cells, about 1.5 m in the village and growing out to the
  mountains. A 4-layer splat (sand, earth, grass, rock) is stored in COLOR_0.
- **Godot:** the import script puts splat, water and foliage shaders on the terrain, sea and
  plants. Smoke and fire markers become particles. Godot builds mesh LODs and applies visibility
  ranges. Colliders use the `-col` / `-convcolonly` import hints.
- **Atlas fixes:** the source swatches are cropped to remove bleed from their neighbours and made
  tileable again. The normals are boosted. AO, roughness and metallic are packed into one ORM map.
  The swatches named `weathered_timber` and `woven_reed` are swapped (reed wall and timber); the
  output textures are named for what they show.

## Flora

![species lineup](docs/flora_species.jpg)

| prefab | plant | tris | grows |
|---|---|---:|---|
| `Grass_Meadow` | O-nogariyasu meadow grass (*Calamagrostis langsdorffii*) | 12 | grass, thinning onto earth and away from the village |
| `Grass_Flowers` | meadow grass with buttercups | 12 | patches in the meadow |
| `Leymus_Mollis` | Hama-ninniku, beach wild rye | 12 | upper beach / dune line |
| `Sasa_Kurilensis` | Chishima-zasa dwarf bamboo | 108 | thickets on the slopes past the village edge |
| `Heracleum_Lanatum` | O-hanaudo cow parsnip, the white umbels in the concept | 102 | meadow rims, along the fence |
| `Hemerocallis_Esculenta` | Ezo-kanzo day lily | 40 | coastal meadow patches |
| `Rosa_Rugosa` | Hamanasu beach rose | 124 | dune and meadow edge |
| `Geranium_Erianthum` | Chishima-furo wild geranium | 44 | meadow patches |

- **Atlas:** painted with numpy at 2048 px: BaseColor with alpha, Normal (OpenGL +Y) and ORM. The
  transparent texels are filled with nearby colour, so alpha-clipped mipmaps get no dark fringes.
- **Materials:** two of them, `M_FoliageGrass` and `M_Foliage`, both glTF `MASK` at 0.45 and
  double-sided. Grass cards have up-facing normals so they light like the ground.
- **Chunks:** the 16 m chunks (`Flora_Grass_*`, `Flora_Plants_*`) each have one surface. The
  default scatter is about 3,000 plants and 78k triangles in 111 chunks. In Godot, grass draws to
  75 m without shadows, and the plants draw to 170 m.
- **Wind:** COLOR_0 holds sway (0 at the root, 1 at the tip) and a random phase. `foliage.gdshader`
  uses them; Blender ignores them.
- **Tuning:** densities and niches are in `SPECIES` and `suitability()` in `ainu_flora.py`.

## Known limitations

- The Cycles preview lights the backs of the grass cards darker than Godot does. The Godot shader
  keeps their up-facing normals; Blender can't.
- Alpha-clipped grass thins out in the distance as its mipmaps lose coverage. The grass draw
  distance keeps this mostly out of view.
- There are no trees yet (Ezo spruce, Erman's birch on the upper slopes). The mountains rely on
  the terrain shader's grass and rock layers.
