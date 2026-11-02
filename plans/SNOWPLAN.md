# Dynamic Snow System — Implementation Plan (120×120 m)

Engine-agnostic; notes assume a GPU compute + custom shader pipeline (Unity HDRP/URP, Unreal, or Godot 4 all work). Adjust names to fit.

## 1. Core data model
- **Snow heightfield**: one 2D texture covering 120×120 m at 10 cm/texel → 1200×1200, R16 (0–65535 mapped to 0–2 m). ~2.9 MB. Double-buffer it for compute passes.
- **Three authored maps** (same resolution, can be lower):
  - `BaseDepth` — initial snow depth. This is where deep/shallow variation lives (paint it: e.g. 1.5 m in the drift valley, 5 cm on the windswept ridge).
  - `Capacity` — max depth wind may build to at each texel. Roughly BaseDepth × 1.5, lower on slopes/rock, zero on heated or covered areas.
  - `Compaction` — minimum depth after a footstep, as a fraction (e.g. 0.15). Snow gets compressed, not deleted.
- **Runtime**: `Height = clamp(current, BaseDepth × Compaction, Capacity)`.

## 2. Rendering
- Ground mesh subdivided to ~0.5 m (or tessellated) samples `Height` for vertex displacement; pixel shader samples at full res for normals (finite differences) and a crisp trail-edge darkening term.
- Two material layers blended by "freshness" (fresh powder vs. compacted trail): albedo, roughness, subsurface tint.
- Optional: small displaced-snow rim around footprints (1 texel ring, +5–10 % height) for readability.
- LOD: beyond ~40 m, sample a mip and skip displacement; normals only.

## 3. Deformation (character pushes snow down)
1. Orthographic "deform camera" pointing **up** from below the terrain, covering the 120×120 area (or a 20 m window following the player, written back into the big texture — cheaper).
2. Render only deformer objects (feet, body capsule, dragged props) on a dedicated layer into a depth target.
3. Compute pass: `newHeight = min(currentHeight, deformerDepth)`, floored at `BaseDepth × Compaction`.
4. Tag deformed texels with a `Freshness = 0` write into a second channel (used by shader blend and by wind refill rate).
- Alternative for simple needs: stamp radial brushes at foot bones on landing events instead of the camera. Cheaper, less accurate on slides/falls.

## 4. Wind accumulation (builds snow back up)
Run one compute pass every 0.25–1 s (not per frame):
- `Height += AccumRate × dt`, clamped to `Capacity`.
- `AccumRate = BaseRate × WindStrength × ShelterFactor × (1 – slopeFacingWind)`.
  - `ShelterFactor` from an obstacle map (SDF or occlusion baked from static geometry): lee sides build faster, windward faces slower.
  - Trails refill first: multiply rate by `(1 – Freshness)` so recent footprints soften before pristine areas grow.
- Global wind vector exposed to gameplay/weather. Direction change should optionally erode the windward edge of drifts slightly (subtract a small amount) so drifts migrate.
- Tunables: `BaseRate` (e.g. 2 cm/min in blizzard, 0 in calm), snowfall on/off flag adds a uniform rate.

## 5. Gameplay coupling
- CPU-readable copy of `Height` at 1 m resolution (120×120 floats, async readback every ~0.5 s).
- Character controller: depth under feet → move-speed multiplier (e.g. linear 1.0 → 0.4 across 0–0.8 m), IK foot sink, footstep audio/VFX variant, stamina cost if applicable.
- Query API: `GetSnowDepth(worldPos)`, `GetSnowNormal(worldPos)`.

## 6. Persistence & streaming
- Save `Height` and `Freshness` textures (compressed, ~1 MB) in save games.
- Whole 120×120 fits in memory; no streaming needed. If the project grows, tile at 60×60.

## 7. Task breakdown for agents
| # | Task | Depends on | Acceptance criteria |
|---|------|-----------|---------------------|
| A | Heightfield asset + authored maps + init | — | Painted BaseDepth shows visibly deep and shallow regions in-editor |
| B | Snow shader (displacement, normals, layer blend, LOD) | A | 1200×1200 field renders at ≥60 fps target hardware |
| C | Deform camera + min() compute pass + compaction floor | A | Walking leaves persistent footprints; can't go below floor |
| D | Wind accumulation compute + shelter map bake | A | Footprints fade over N minutes; drifts form on lee sides |
| E | CPU readback + gameplay queries + movement speed / IK | A, C | Player visibly slower in deep snow, feet sink |
| F | Save/load of runtime textures | C, D | Trails survive reload |
| G | Tuning pass + debug overlay (view depth, capacity, freshness) | all | Overlay toggle in debug menu |

A can be started immediately; B, C, D can proceed in parallel once A defines the texture formats and world-to-texel mapping. Agree on that mapping first — everything else keys off it.

## Open decisions to confirm before starting
- Engine and render pipeline.
- Camera-based vs. brush-stamp deformation.
- Whether non-player entities (NPCs, vehicles) also deform snow (affects deform layer setup and readback cost).