# Dynamic Snow System — Implementation Notes

Implements `SNOWPLAN.md` and `WEATHERPLAN.md` in Godot 4.7, Forward Mobile
renderer.

## Decisions taken

| Plan's open decision | Choice |
|---|---|
| Engine / pipeline | Godot 4.7, Forward Mobile kept. Simulation runs as **fragment-shader ping-pong passes in HDR SubViewports**, not RenderingDevice compute. Same math, no compute plumbing, works on the Mobile renderer untouched. |
| Deformation | **Deform camera** (plan §3), orthographic, looking up from 1 m under the ground plane. |
| Who deforms | Any node carrying a `SnowDeformer`. `SnowWalker` creates one automatically; NPCs, tactical units and props opt in by adding either node. |

## Files

| File | Role |
|---|---|
| `scripts/snow/snow_field.gd` | `SnowField`. Owns the viewports, uniforms, save/load and the query API. |
| `scripts/snow/snow_deformer.gd` | `SnowDeformer`. Proxy volume rendered only into the deform camera. |
| `scripts/snow/snow_walker.gd` | `SnowWalker`. Character coupling: sink, speed penalty, stride signal. |
| `scripts/snow/snow_maps_baker.gd` | `SnowMapsBaker`. Generates the authored maps and bakes the shelter channel. |
| `scripts/snow/snow_weather.gd` | `SnowWeather`. Game clock, climate model, fronts. **Publishes** atmosphere; writes nothing to the scene. |
| `scripts/snow/snow_precipitation.gd` | `SnowPrecipitation`. Camera-following flake and ground-blizzard emitters. |
| `scripts/sky/solar.gd` | `Solar`. Static sun/moon geometry, sunrise/sunset. No scene access. |
| `scripts/sky/sky_controller.gd` | `SkyController`. **Single writer** of Sun, Moon, Environment and sky material. |
| `scripts/debug/debug_menu.gd` | F3 debug menu: presets, sliders, snow map panel, status line, F-keys. |
| `scripts/debug/debug_theme.gd` | `DebugTheme`. Builds the menu's navy/gold `Theme` at runtime. |
| `scripts/player/camera_rig.gd` | Third-person boom. Lifts itself to clear the snow surface (see below). |
| `shaders/snow_sim.gdshader` | Deformation + wind accumulation pass. |
| `shaders/snow_shelter.gdshader` | Directional shelter factor, re-rendered for the live wind. |
| `shaders/snow_deform_encode.gdshader` | Writes deformer underside height. |
| `shaders/snow_ground.gdshader` | Displacement, normals, layer blend, LOD, cloud shadows. |
| `shaders/snow_readback.gdshader` | 1200² → 120² downsample for CPU readback. |
| `shaders/snow_debug.gdshader` | False-colour channel view. |
| `shaders/sky.gdshader` | Gradient, sun and moon discs, stars, two cloud layers. |
| `assets/snow/snow_maps.png` | RGBA8: R BaseDepth, G Capacity, B Compaction, A Shelter (fallback only). |

## Data layout

Runtime state lives in a pair of 1200×1200 `use_hdr_2d` SubViewports (RGBA16F):
`R` = height in metres, `G` = freshness (0 just deformed, 1 pristine),
`B` = pending deposition not yet folded into `R` (see the accumulator note
below). Authored maps are a 512×512 RGBA8 PNG imported lossless with mipmaps
off, so channel values round-trip exactly. Repaint R/G/B in any image editor;
the alpha channel is now only the fallback for `use_dynamic_shelter = false`.

## Weather (WEATHERPLAN.md)

`SnowWeather` (group `snow_weather`) owns a game clock and drives `SnowField`,
`SnowPrecipitation`, the `WorldEnvironment` and the `Sun`. It is not a `@tool`
script, so none of this runs in the editor.

### Time scale

`time_scale = 60`: one real second is one game minute. Every rate is authored
in **cm per game-hour** and converted on the way into the field, which wants cm
per real minute — at 60× the two are numerically equal, which is why
`drift_refill_cm_per_game_hour = 2.0` arrives as `base_rate_cm_per_min = 2.0`.
`erosion_rate` and `freshness_recovery_seconds` stay **real-time** visual
tunables and deliberately do not scale with the clock.

### Compass convention

World north is −Z, east is +X. `wind_from_deg` is the bearing the wind blows
**from**, clockwise from north; its unit vector is `(sin β, 0, −cos β)`.
`SnowField.wind_direction_deg` is the direction the wind blows **toward**,
measured from +X toward +Z, because that axis pair maps straight onto field UV.

```
field_deg    = fposmod(from_deg + 90.0, 360.0)
from_deg     = fposmod(field_deg - 90.0, 360.0)
world_toward = Vector3(cos(field_deg), 0, sin(field_deg))
```

From W (270) → field 0 → blows toward +X. From N (0) → field 90 → toward +Z.
The old hand-authored default `wind_direction_deg = 35` was a wind **from WNW
(305°)**. `snow_sim.gdshader` and `SnowMapsBaker._march()` already treated the
vector as "toward", so none of the sim's wind math changed.

### Climate data

JMA 1991–2020 normals, embedded as `SnowWeather.MONTHS`. Rishiri's own
Kutsugata AMeDAS (14 m) has no snow gauge, so snowfall and snowy days come from
Wakkanai (JMA 47401, ~50 km NE, same Sea-of-Japan winter-monsoon regime); wind
speed is Kutsugata itself.

| Month | Snowfall cm | Snowy days | Mean wind m/s | Prevailing FROM | dir σ |
|---|---|---|---|---|---|
| Nov | 41 | 20.5 | 4.8 | W | 25° |
| Dec | 122 | 28.5 | 4.8 | W | 20° |
| Jan | 129 | 30.4 | 4.6 | W | 20° |
| Feb | 105 | 26.0 | 4.4 | W | 20° |
| Mar | 68 | 24.3 | 4.2 | W | 25° |
| Apr | 9 | 12.6 | 3.7 | SSW | 35° |

Sources:
- <https://www.data.jma.go.jp/stats/etrn/view/nml_sfc_ym.php?prec_no=11&block_no=47401> (Wakkanai)
- <https://www.data.jma.go.jp/stats/etrn/view/nml_amd_ym.php?prec_no=11&block_no=0002> (Kutsugata AMeDAS)
- <https://en.wikipedia.org/wiki/Wakkanai> (snowy-day counts)
- <https://www.town.rishirifuji.hokkaido.jp/rishirifuji/1158.htm>

One figure is **not** from JMA: `snowing_hours_per_snow_day = 12`. It sets the
snowing/not-snowing duty cycle only; the monthly total is calibrated exactly
regardless of it.

### Calibration

`_calibrate()` runs at `_ready`, and again on `set_month()` or `reroll_wind()`.
It samples the intensity noise every 0.25 h over 1500 game-hours and solves two
constants:

- **`thr`** is the empirical quantile of the raw noise that makes the snowing
  fraction equal `(snow_days / days) × snowing_hours_per_snow_day / 24`
  (January 0.49). Using an empirical quantile rather than a closed form keeps
  the model independent of the noise's exact distribution.
- **`K`** scales the shaped noise so the month's expected total matches the JMA
  normal. The expectation is split between ordinary hours and frontal ones and
  averaged over the same samples.

Intensity per frame:

```
u     = noise(game_hours / intensity_noise_hours) * 0.5 + 0.5
raw   = pow(clamp((u - thr) / (1 - thr), 0, 1), intensity_shape_power)
raw   = max(raw, FRONT_RAW_FLOOR * front_env)
I     = K * raw * flutter * lerp(1, front_mult, front_env)
```

### Fronts

Exponential inter-arrival at `front_mean_interval_hours = 30`, so ~25 fronts a
month and ~27 % of the time inside one. Each rolls a duration (4–12 h), a peak
wind (12–18 m/s), an intensity multiplier (2–4×) and a direction swing
(30–90°, clockwise 70 % of the time). The envelope rises over 1 h, holds, and
falls over the last 1.5 h; the direction offset ramps in over the first 40 % of
the duration and then relaxes with an 8 h exponential after the front passes,
so the air mass keeps its new heading for a while.

### Directional shelter

The baked alpha channel of `snow_maps.png` was correct for exactly one wind
direction, so it becomes wrong the moment the wind is randomised. `SnowField`
now owns a 256² `SnowShelter` SubViewport running `snow_shelter.gdshader`,
which mirrors `SnowMapsBaker._march()` on the GPU against an obstacle mask
rasterised by `rebuild_obstacle_mask()`. It re-renders only when the wind has
turned past `shelter_redraw_threshold_deg` (3°), which in practice is a handful
of passes per session rather than one per frame.

The baked alpha remains the fallback for `use_dynamic_shelter = false`, and
`bake_shelter_into()` is still the offline path. Obstacle collection is
name-based; the skip list covers `Ground`, `Player`, `CameraRig`, `SnowField`,
`TacticalCombat`, `SnowPrecipitation` and `SnowWeather`, because tactical unit
capsules are `MeshInstance3D`s and must not block wind.

Falling snow now follows the shelter pattern in proportion to wind strength:
`h += (rate + snowfall_rate * mix(1, shelter, snowfall_wind_bias)) * accum_dt`,
where `snowfall_wind_bias = clamp(wind_strength * 0.35, 0, 0.7)`. Still air
lays an even blanket; a blizzard scours windward faces and packs the lee.

### Atmosphere

Continuous severity `s ∈ [0, 4]` from intensity (breakpoints 0.3 / 1.0 / 2.5 /
5.0 cm per game-hour), raised by wind. Adjacent rows are lerped and republished
at 10 Hz with 4 s exponential smoothing.

**The weather publishes; the sky applies.** `SnowWeather` no longer touches the
`Sun`, the `WorldEnvironment` or the sky material, and there is no baseline to
capture or restore. It exposes `atmosphere_row()`, `severity()` and
`cloud_coverage_target()`, and `SkyController` polls them at its own 10 Hz. The
baseline-multiplier model broke the moment the sun started moving: a multiplier
over "whatever the scene was authored with" has no meaning once the authored
value is itself a function of time. `set_time_of_day(hour)` moves the clock for
the debug presets, so the clock and the sky can never disagree.

`sun_path` and `environment_path` remain as exports for one release, unused and
documented, so scenes and probes that still set them do not break.

| s | fog_density | fog_light_color | fog_sky_affect | sun × | shadow_opacity | sky_energy × | ambient × | height-fog density |
|---|---|---|---|---|---|---|---|---|
| 0 calm | 0.0025 | (0.72, 0.78, 0.88) | 0.10 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| 1 light | 0.0060 | (0.76, 0.80, 0.87) | 0.35 | 0.80 | 0.80 | 0.90 | 1.00 | 0 |
| 2 moderate | 0.0150 | (0.79, 0.82, 0.86) | 0.60 | 0.55 | 0.50 | 0.75 | 1.10 | 0.02 |
| 3 heavy | 0.0300 | (0.82, 0.84, 0.86) | 0.85 | 0.35 | 0.30 | 0.60 | 1.25 | 0.05 |
| 4 blizzard | 0.0600 | (0.85, 0.86, 0.87) | 1.00 | 0.22 | 0.15 | 0.50 | 1.35 | 0.10 |

Exponential fog, so 1/e visibility runs 400 / 170 / 67 / 33 / 17 m. Ambient
light rises as the sun dims, because a whiteout is bright and flat rather than
dark. Sky colours lerp toward `fog_light_color` by `s/4`; sky-material writes
are skipped below a 0.002 change because each one re-renders the radiance map.
Nothing volumetric — the project is on Forward Mobile.

## Sky (SKYPLAN.md)

`SkyController` (group `sky`) is the **single writer** of the `Sun`, the `Moon`,
the `Environment` and the sky `ShaderMaterial`. Nothing else in the project sets
those properties. It reads the clock from `SnowWeather` rather than owning one,
and falls back to exported values when a scene has no weather at all, so
`tactical_test.tscn` lights the same way with the same three nodes.

### Solar model

Everything is keyed off the sun's **elevation**, not the hour: a December noon
at 45.2 °N sits at 21°, and should look like it.

```
N   = MONTH_START_DOY[month] + day − 1        (Nov…Apr = 305/335/1/32/60/91)
δ   = 23.44° · sin(360/365 · (N − 81))
TC  = 4 · (141.2 − 135) + EoT(N)              minutes, east positive
H   = 15° · (hour + TC/60 − 12)
sin(el) = sin φ sin δ + cos φ cos δ cos H     φ = 45.2°
cos(az) = (sin δ − sin el sin φ) / (cos el cos φ),  az = 360° − az when H > 0
dir     = (cos el · sin az,  sin el,  −cos el · cos az)
```

Compass as everywhere else in the project: north is −Z, east is +X, azimuth
clockwise from north. At solar noon the sun is due south, so `sun_dir.z > 0`.

`DirectionalLight3D` emits along its local −Z, so the light's basis is
`Basis.looking_at(−dir)` and it is **`+global_basis.z`** that points at the sun.
(SKYPLAN's verification text asks for `−global_basis.z == sun_dir`, which is
backwards; the scene's own authored Sun basis agrees with the code.)

Three corrections the plan's sketch left out, all required to hit its own ±0.25 h
acceptance against JMA:

- **longitude** — Rishiri is 141.2 °E, 6.2° east of the 135 °E JST meridian.
  That is 25 minutes, not the "~1°" the plan assumed; without it December
  sunrise lands 40 minutes late.
- **equation of time** — up to a quarter hour either way.
- **−0.833° horizon altitude** — half the solar disc plus mean refraction.

`sunrise_hour()` / `sunset_hour()` solve `cos H` in closed form and cache per
day, rather than the plan's bisection: the equation is solvable, and solving it
keeps sunrise exactly consistent with `sun_elevation_deg`.

### Moon, and what is wrong with it

Deliberately not an ephemeris. The moon's hour angle is the sun's dragged back
by `360° · phase`, and its declination swings from the sun's at new to the
sun's negated at full:

```
p        = fposmod(day + phase_offset, 29.5306) / 29.5306
H_moon   = H − 360° · p
decl     = δ · 0.95 · cos(2π p)
illum    = 0.5 · (1 − cos(2π p))
```

SKYPLAN specified a constant `−0.8 · δ`. That gets a winter full moon right
(high) but a winter crescent wrong (also high, when a crescent hangs near the
sun and must be low). `cos(2π p)` costs one cosine and gets both ends right.

Measured on 15 January: a full moon rises at sunset (el +0.03°) and culminates
at +64.8°, a new moon tracks the sun to within 1.1°, a first quarter stands at
+40.9° at sunset. **Rise and set times of the in-between phases are still wrong
by hours.** That is acceptable for the look; do not file it as a bug.

### Elevation table

Lerped between adjacent rows exactly like `SnowWeather.ATMOSPHERE`. The el = 45
row reproduces the scene's authored daytime values, so a clear noon is the
reference everything else hangs off.

| el | sun colour | sun × | zenith | horizon | ambient colour | ambient × | stars |
|---|---|---|---|---|---|---|---|
| −18 | — | 0 | (0.010, 0.014, 0.035) | (0.020, 0.028, 0.060) | (0.07, 0.09, 0.16) | 0.12 | 1.0 |
| −12 | — | 0 | (0.015, 0.025, 0.070) | (0.060, 0.070, 0.140) | (0.09, 0.11, 0.20) | 0.14 | 0.9 |
| −6 | (1.00, 0.45, 0.25) | 0 | (0.06, 0.09, 0.22) | (0.55, 0.32, 0.22) | (0.16, 0.16, 0.26) | 0.20 | 0.5 |
| 0 | (1.00, 0.55, 0.30) | 0.30 | (0.20, 0.32, 0.55) | (0.95, 0.55, 0.30) | (0.35, 0.30, 0.34) | 0.28 | 0.1 |
| 5 | (1.00, 0.75, 0.50) | 0.80 | (0.28, 0.45, 0.72) | (0.85, 0.72, 0.60) | (0.45, 0.47, 0.55) | 0.34 | 0 |
| 15 | (1.00, 0.92, 0.82) | 1.10 | (0.33, 0.52, 0.80) | (0.68, 0.76, 0.86) | (0.52, 0.58, 0.70) | 0.40 | 0 |
| 45 | (1.00, 0.95, 0.85) | 1.20 | (0.35, 0.55, 0.82) | (0.65, 0.75, 0.85) | (0.55, 0.62, 0.75) | 0.40 | 0 |

The sun's energy is additionally **gated to zero below the horizon**
(`smoothstep(−1°, 0.5°)`), applied as a ceiling *after* smoothing. The table's
el = 0 row is the colour of sunlight *at* sunset; without the gate, a 60× clock
outran the 2 s smoothing and left a directional light burning at el −15°,
lighting the undersides of things from underground.

Ambient moved from `SKY` to an explicit `COLOR` fill with
`ambient_light_sky_contribution = 0`, because the radiance map is updated lazily
and the debug menu needs a direct control. Reflections still come from the sky.

**Night floor.** `sun + moon + ambient` is never allowed below
`night_floor_energy` (0.10); the shortfall goes into ambient, the only term that
still works with both bodies under the horizon. Midnight under a new moon under
a blizzard measured 0.120.

**Shadow hand-over.** Only one light ever has `shadow_enabled`. The sun hands
over below −4° (±0.5° of hysteresis) and the hand-over fades `shadow_opacity`
*through zero* over two game minutes, so the pair are never both on — which is
what the plan's probe asserts — and the ground does not pop at dusk. Moon
shadows cap at `shadow_opacity` 0.5.

**Fog colour** takes the weather's colour and the sky's brightness and hue:
`row.fog_color · mix(1, horizon/luma(horizon), 0.6) · clamp(luma(horizon)/0.736, 0, 1.2)`.
A whiteout at noon is bright and flat; the same whiteout at midnight measured a
fog luminance of 0.053. The normalisation is against the el = 45 horizon, so a
clear day reproduces the weather table's authored colour — SKYPLAN's `fog_tint`
of 1/0.736 belongs to its version of the formula, which did not divide by the
reference horizon, and `fog_tint` is now a free knob defaulting to 1.

### Sky shader

`shaders/sky.gdshader` replaces the `ProceduralSkyMaterial`. `Sky.process_mode`
is `INCREMENTAL` and `radiance_size` is 128 (256 is the default and measurably
slower on Mobile).

Pass layout, and the reason for it:

- **half-res pass** — the whole sky bar the two discs: gradient, stars, then the
  cloud layers composited over them.
- **background pass** — that result plus the sun and moon discs at full
  resolution.
- **cubemap pass** — gradient and discs only. The half-res pass does not run for
  the radiance cubemap, which is what we want: reflections stay clear and cheap.

The half-res pass composites the clouds *itself* rather than handing back a
premultiplied colour and a coverage in `ALPHA`. Measured on Forward Mobile,
`HALF_RES_COLOR.a` does not return what the half-res pass wrote to `ALPHA`: a
written 1.0 came back as ≈1.94, which drove green and blue negative and turned
every cloud gold. **Only `.rgb` is trustworthy.** The consequence is that the
discs are not occluded per pixel by cloud; they are dimmed by the
`sun_occlusion` uniform instead, which is what SKYPLAN §4 specifies anyway.

### Cloud layers

Two planar sheets, one seamless 512² FBM texture at two scales, projected as
`uv = EYEDIR.xz / EYEDIR.y · height / tile + scroll` and faded out below
`EYEDIR.y = 0.12` where the projection blows up.

| Layer | Height | Tile | Wind × | Coverage from |
|---|---|---|---|---|
| High (cirrus) | 8 km | 60 km | 0.25 | `high_cloud_coverage` (0.35) ± 0.1 on a 6 h noise |
| Low (stratus) | 1.5 km | 8 km | 1.0 | `SnowWeather.cloud_coverage_target()` |

**Coverage is calibrated against the noise's own distribution.** An FBM's values
pile up around its median, so the plan's `smoothstep(1 − coverage, …)` threshold
covers almost nothing: measured, coverage 0.30 put cloud over **2 %** of the
sky. `SkyController` samples the noise once at startup, builds a 129-point
inverse CDF, and sends the shader the two *noise values* that actually cover the
requested fraction. Same empirical-quantile trick as `_calibrate()`, and for the
same reason: it does not depend on the noise's exact shape.

Cloud undersides are lit by `horizon · 0.35 + sun_color · 0.45 · daylight`, not
by the horizon alone — tying them to the horizon made an overcast darker than a
clear zenith, which read as holes in the sky rather than as cloud. `daylight`
here is the sunlight **before** cloud occlusion, or a cloud crossing the sun
darkens itself.

Scroll is accumulated on the CPU from the live wind and pushed as a uniform, so
a wind reroll turns the field instead of teleporting it and the shader needs no
`TIME`. A scene with no weather uses `fallback_wind_ms` / `fallback_wind_from_deg`
for the same purpose: a deck that never moves parks whatever happens to be over
the sun there for the whole session, which is exactly what `tactical_test.tscn`
did before the fallback existed.

`sun_occlusion` is the low layer's density sampled on the CPU along `sun_dir`
from the same image, so the disc, the glow and `DirectionalLight3D.light_energy`
all dim on the same frame. It is combined with the weather's `sun` multiplier by
`min()`, not by multiplying: the two are descriptions of the same sky, and
multiplying them dimmed a blizzard twice.

**Cloud shadows** (SKYPLAN task H2) are merged. `snow_ground.gdshader` samples
the same texture at `world_xz / tile + scroll` and folds the result into `AO`
with `AO_LIGHT_AFFECT = 0.7` — Forward Mobile has no light projectors for
directional lights, so this is the available approximation. They fade out with
the daylight and again as coverage passes 0.85, because a total overcast has no
discrete shadows, only the flat dimness the weather row already applies.

### Radiance churn

Every sky-material write can re-render the radiance map, so `_set_sky_param()`
gates each one behind the same 0.002 epsilon the weather uses. Measured in
steady state at `time_scale = 60`: **1.83 uniform writes per 10 Hz tick out of
23 parameters (8 %)**. `sun_dir` and `moon_dir` move 20 % of ticks, cloud scroll
every tick, everything else rarely. The sun's elevation is no longer sent in
degrees — 0.025° per tick is over the epsilon, so it dirtied the map every tick
for a horizon-glow term that is zero from mid-morning to late afternoon; the
window is computed on the CPU and sent as a 0–1 `horizon_glow` instead.

Because the cloud scroll moves every tick, the sky is *marked* dirty on
essentially every tick while the wind blows. That is the cost of a sky that
moves, and the measured GPU cost says it does not matter (below).

## Debug menu

`DebugMenu` (group `debug_menu`, CanvasLayer `layer = 10`) replaces
`SnowDebugOverlay`, which is deleted. Everything the overlay did still works:
F3 toggles, F4 cycles the map channel, F5/F6 save and load the field, F7 forces
a blizzard, F8 rerolls the wind, and the snow map moved to the bottom-left as a
panel with the status line as a superset of the old label.

The menu never writes to a light, the environment or the sky material. Every
control sets an `override_*` export on `SkyController` or `SnowWeather` — the
same path a runtime probe uses — so the menu and the probes cannot disagree. A
slider whose override is off tracks the live value at 60 % alpha, so the panel
shows what the sky is doing as well as what it has been told to do.

Presets move the **real clock** via `SnowWeather.set_time_of_day()` rather than
pinning `override_hour`; a probe that wants to pin conditions without disturbing
the weather timeline should still use `override_hour`.

`Node._input` runs *before* the GUI sees a click, so `world_setup.gd` asks the
menu (`is_point_over_ui`) before running its tactical raycast. Without that, a
click on a slider also selected whatever unit was behind the panel.

**The settings panel scrolls.** A `PanelContainer` takes its height from its
content's minimum size, so anchoring its bottom does nothing: with the controls
added directly it grew to 670 px in a 648 px viewport and ran under the preset
bar, and trimming rows only bought a few pixels at a time. The controls live in
a `ScrollContainer`, whose minimum size is small, so the panel honours its
anchors and the layout holds at any window size (measured 544 px tall, bottom at
560, preset bar at 572, at 1152×648, 1280×720 and 1024×576). The status block
additionally sits in a fixed-height clipping holder, because a wrapping label
has no height bound at all.

## Camera clearance over deep snow

The snow surface is vertex displacement in `snow_ground.gdshader` (`VERTEX.y +=
h`) and carries **no collision** — the only collider under it is `Ground`'s
flat 120 × 0.1 box whose top sits at y = 0. `SpringArm3D` therefore sees level
ground everywhere and happily parks the camera inside a drift: measured 1.62 m
below the surface at pitch +40 in the drift valley, rendering the world from
under the ground through the snow's back faces.

`camera_rig.gd` now samples `SnowField.get_snow_surface_y()` at four points
along the boom and raises the arm until the whole boom clears the surface by
`snow_clearance` (0.4 m, comfortably over the camera's 0.1 m near plane).
Details that matter:

- The lift is measured against where the boom would sit with **no lift
  applied**, so it is an absolute value, not an increment. Applying it as an
  increment to the camera's current position would compound whenever `_process`
  ran more than once per physics frame.
- It is written into `_spring_arm.position.y` inside `_orient()`, which already
  sets that vector absolutely every frame. The rig carries yaw only, so the
  arm's local +Y is world +Y.
- Sampling along the boom rather than only under the lens means a drift crest
  *between* the player and the camera lifts it too.
- Raising the arm lets it clear the ground collider and extend to full length,
  which drops the camera again, so the converged lift runs above the raw snow
  depth — 2.97 m of lift in 1.66 m of snow at pitch +60. `max_snow_lift`
  defaults to 4.0 to leave headroom.
- `_aim_camera()` re-aims at the *un-lifted* boom origin so the player stays
  framed. It uses `look_at`, which writes a **local** rotation, so the rotation
  is explicitly cleared once the lift relaxes; leaving it would skew the view
  permanently.
- Rise is fast (6 m/s) and settle slow (1.5 m/s), so the camera never dips into
  a drift but cresting one does not bob the view.

With no `SnowField` in the scene the whole path is inert and the rig behaves
exactly as before; `snow_clearance_enabled` turns it off explicitly.

Not addressed, because it is the existing `SnowWalker` sink design rather than
a camera problem: the player capsule stays at y = 0 on the flat collider, so in
1.44 m of snow it is visually submerged to the shoulders.

## Deviations from the plan, and why

- **Viewport ping-pong instead of GPU compute.** Chosen with the renderer left
  on Mobile. The accumulation and deformation math is unchanged.
- **Wind refill on pristine snow.** The plan's literal `rate × (1 – Freshness)`
  is zero on untouched ground, which would stop drifts forming at all. The
  shader uses `pristine_growth + (1 – pristine_growth) × (1 – Freshness)`
  instead, default `pristine_growth = 0.35`. Trails still refill first —
  measured 2.85× faster than pristine — and lee drifts still build.
- **Deform decode is sRGB-aware.** Godot's 3D SubViewport render target is RGBA8
  with sRGB storage, and a canvas shader samples it without the hardware decode.
  `snow_sim.gdshader` applies `srgb_to_linear()` before undoing the sqrt encode.
  Measured round trip after the fix: 0.10 m → 0.106 m, 1.50 m → 1.519 m, the
  residual being 8-bit quantisation. Toggle `deform_srgb_storage` if a future
  renderer hands back linear values.
- **Sink reference is sampled ahead of the character**, not underfoot. Sampling
  underfoot feeds the character's own trench back into its sink depth and creeps
  to the compaction floor within a few frames.
- **Vertex displacement averages over one mesh quad.** Point-sampling a 10 cm
  field with 50 cm vertices tore deep trenches into hard facets. Normals still
  use full-resolution taps, so print edges stay crisp.
- **Footstep audio/VFX is a signal, not an implementation.** `SnowWalker`
  emits `snow_step(depth)` every stride; the project has no audio system to
  wire it to yet. Stamina is likewise left to the caller.
- **Save format** is a zstd-compressed raw dump rather than an image file:
  Godot can write EXR but cannot load one back at runtime. ~1.4 MB for the full
  1200² field.

### From WEATHERPLAN

- **Deposition is banked in a spare state channel.** The state target is
  RGBA16F, whose spacing between representable values is ~1/2048 of the value —
  about 1 mm at a metre of lying snow. Realistic Rishiri deposition is ~1e-5 m
  per 0.5 s accumulation step, so adding it straight to the height rounded back
  to the height and the field **froze**, deep snow first. Measured before the
  fix: at h = 1.209 m under 6 cm/min of snowfall the texel did not move at all
  over 8 s, while shallower ground kept growing. `snow_sim.gdshader` now
  accumulates into `B` and folds it into `R` once it exceeds
  `max(h × 0.0025, 0.0005)` m. `B` is dropped when a deformer presses the snow
  down or the capacity clamp rejects the growth. The saved state carries `B`, so
  save/load still round-trips exactly. Probes that measure deposition must read
  `R + B`, not `R` alone, or they see up to one bank of lag.
- **The intensity calibration accounts for the front floor.** The plan's
  formula is `K = mean_cm_per_h / (mean(raw) × (1 + f_front × (m_front − 1)))`,
  which models a front as just its intensity multiplier and ignores
  `raw = max(raw, 0.35 × env)`. That floor lifts otherwise-dry hours inside a
  front to 0.35, several times the ordinary mean, and simulated months came out
  wildly high — March +335 %, April +558 %. `_calibrate()` now averages the
  whole per-frame expression, split between ordinary and frontal hours.
  Simulated totals afterwards: Nov −13 %, Dec −10 %, Jan +4 %, Feb +16 %,
  Mar +11 %, Apr −35 %, each a single stochastic realisation.
- **The wind-driven severity floor starts at 1, not 2.** The plan's
  `max(s, 2 + 2 × smoothstep(8, 16, wind))` pins severity at 2 (67 m visibility,
  sun × 0.55) for *any* snowfall, because the smoothstep is 0 below 8 m/s and
  January's mean wind is 4.6 m/s — most snowing hours would have been
  permanently murky. `1 + 3 × blow` is used instead: it keeps the plan's
  endpoint (4 at 16 m/s), gives 2.5 at the 12 m/s blizzard threshold, and lets
  a calm flurry read as light.
- **The flake box is placed by camera pitch.** The plan's fixed
  `forward × 8 + up × 5 − wind × min(speed × 1.5, 18)` put the box a full
  half-extent upwind at 18 m/s, leaving the camera outside it, and above a
  steeply pitched camera. Both were visible in captures. The lead and lift now
  lerp against `-forward.y`, so the free-roam rig gets the box ahead and above
  and the tactical `CombatCamera` gets it in the space between camera and
  ground. The upwind shift is capped at half the box's own X extent.
- **Streaks are textured.** Untextured quads read as hard white sticks; they use
  the same procedural radial-gradient dot as the flakes, shortened to
  0.03 × 0.5 m at 0.28 alpha.

## Not implemented

- The 20 m moving deform window (plan §3.1). Not needed: the whole snow
  pipeline measures 0.23 ms of GPU time across all six viewports. (The "6 ms"
  once quoted here was the 165 Hz vsync period, not a render cost — wall-clock
  frame timing cannot see past vsync. Use
  `RenderingServer.viewport_get_measured_render_time_gpu()`.)
- Tiling at 60×60 (plan §6). Unnecessary at 120×120.

## Verified

Measured in a running session on a Radeon RX 7900 XTX, Forward Mobile:

| Task | Evidence |
|---|---|
| A | BaseDepth spans 0.047 m (windswept ridge) to 1.671 m (drift valley); readable in-editor. |
| B | 165 fps with the 1200² field and a 240×240-quad ground. The 6.06 ms once recorded here is the vsync period; measured GPU cost is 0.23 ms. |
| C | Walking leaves persistent trails; pressing a deformer to −0.5 m clamps at the compaction floor. |
| D | Trench refills 2.85× faster than pristine ground; shelter 0.93 builds 1.74× faster than shelter 0.55. |
| E | 1.20 m snow gives 0.70 m sink and a 0.40× speed multiplier; bare ridge gives 0.95×. |
| F | Save → `reset_field()` → load restores height 0.280 m and freshness 0.04 exactly. |
| G | F3 toggles the debug menu, F4 cycles seven channels, F5/F6 save and load. |

### Weather

| Check | Evidence |
|---|---|
| Compass round trip | `from 270 → field 0 → back 270`, error 0.0000°, for 270/315/0/35. From W blows toward +X, from N toward +Z. |
| Field push | `snowfall_cm_per_min` tracks `I × time_scale / 60` within the 0.25 s push interval; `base_rate_cm_per_min` 2.0 from 2.0 cm/game-h at scale 60. |
| Direction wanders | 265.3° → 261.8° → 258.4° → 265.3° → 274.0° over five game-hours. F8 / `reroll_wind()` jumped it 36.5°. |
| Shelter is directional | Wind toward +X: lee of Obstacle6 at (−8, 0) reads 0.965, windward at (−12.5, 0) reads 0.292, under it 0.000. Wind toward −X and the two swap (0.931 / 0.206). A 1° change triggers no redraw, 59° does. |
| Deposition | Measured as `R + B` over 30 s at an open spot: wind 0 + 3 cm/h → +1.379 cm vs 1.500 expected (−8 %); wind 9.2 m/s → +1.239 vs 1.412 (−12 %); wind 4.6 m/s at the calibrated January mean → +0.237 vs 0.265 (−11 %). The consistent shortfall is the 0.5 s `accum_interval` not aligning with the probe's wait boundaries. |
| Monthly totals | A full simulated month per month: Nov 35.7/41, Dec 110.0/122, Jan 134.4/129, Feb 121.8/105, Mar 75.2/68, Apr 5.8/9 cm. |
| Visuals | Light snow shows sparse flakes under a clear sun; F7 blizzard shows dense flakes, ground-blizzard streaks, whiteout fog and a dimmed sun. Snow stays visible after T into tactical combat. F3 + six F4 presses reach the DynShelter channel with the weather line. |
| Performance | GPU render time across all six viewports: 0.226 ms with precipitation hidden, 0.231 ms in moderate snowfall, 0.233 ms in a forced blizzard (+0.008 ms). Two shelter passes over a whole session. |
| Camera clearance | In 1.66 m of snow the boom camera sank to −1.620 m below the surface at pitch +40 and bottomed out on the collider at y = 0.001. With the fix, clearance is exactly +0.400 m at every pitch from −40 to +60; the lift converges (2.1643 m held unchanged over 2.5 s), is 0.000 wherever it is not needed, relaxes to 0.0000 on shallow ground with the camera's local rotation back to exactly (0, 0, 0), and changes by at most 0.025 m per frame (1.50 m/s, the settle cap) when walking out of a drift. Player head stays near frame centre at (462, 381) of 1152×648. |
| Regression | Compaction floor still clamps (expected 0.0569 m, measured 0.0551 m); trails still persist with freshness 0.000 underfoot and off-trail control depth higher; F5/F6 round-trips height to +0.000000 m with fog enabled. |

### Sky

Measured on the same machine, Forward Mobile, 1152×648.

| Check | Evidence |
|---|---|
| Solar vs JMA (Wakkanai) | 21 Dec sunrise 07:11 / sunset 15:55 / noon el 21.36° against 07:05 / 16:00 / 21° — errors 0.111 h, 0.067 h, 0.36°. 21 Mar 05:39 / 17:46 / 44.40° against 05:40 / 17:50 / 45° — errors 0.001 h, 0.064 h, 0.60°. Both inside the plan's ±0.25 h and ±1.5°. December daylight 8.74 h. |
| Compass | Noon `sun_dir` = (−0.105, 0.360, 0.927), az 186.4° — due south, +Z. 06:00 `sun_dir.x` = +0.911 — east. `Sun.global_basis.z` matches `sun_dir` to 0.000000 at 08:00, 12:00 and 16:00. |
| Moon | Full moon rises at sunset (el +0.03°), culminates +64.8° at midnight; new moon tracks the sun to 1.1°; first quarter +40.9° at sunset. |
| Single writer | `snow_weather.gd` has 0 hits for `.light_energy =`, `sky_material.`, `ambient_light_energy =`, `shadow_opacity =` and `fog_light_color =`. |
| Cycle sweep | 96 samples over one game day at `time_scale = 3600`: both lights casting at once 0, sun lit below −1° 0, sun energy falling as elevation rose 0, total light below the 0.10 floor 0 (minimum 0.1204), ambient colour NaN 0 and changed on 96 of 96 samples. |
| Radiance churn | 1.83 uniform writes per 10 Hz tick of 23 parameters (8 %) in steady state at `time_scale = 60`. `low_scroll` 100/100 ticks, `sun_dir` and `moon_dir` 20/100, everything else ≤ 6/100. |
| Presets | All six land on their hour to 0.0000 h. Sun above the horizon for Sunrise / Daytime / Afternoon / Sunset (+1.36°, +20.07°, +17.37°, +1.36°), below −5° for Twilight (−6.35°) and Midnight (−67.33°). Midnight forces a full moon (phase 0.50) at 0.159 energy. *Live* clears all eight overrides; moving any control drops the active preset. |
| Weather → sky | Blizzard at Daytime: coverage 0.98–1.00, and `Sun.light_energy` equals the published `ATMOSPHERE` row's `sun` multiplier. The plan's 3 cm/h + 14 m/s reaches severity ≈3.5, not 4, so it gives ≈0.28× rather than the 0.22× the plan quotes; 6 cm/h + 18 m/s reaches row 4. Midnight blizzard fog luminance 0.053 (dark whiteout), total light 0.304 against the 0.10 floor. |
| Performance | GPU time on the root viewport, 300-frame averages: bare sky 0.224 ms, clear with clouds and stars 0.225 ms (+0.001), overcast 0.225 ms (+0.001), midnight 0.247 ms (+0.023, the star field), cloud shadows +0.013 ms. All far inside the plan's +1.5 ms budget and the 0.3 ms H2 gate, so cloud shadows are merged. |
| Menu input | With the menu open in combat, a click on a slider moves the slider and leaves `_tac_state` at IDLE; a click on a unit at its projected screen position selects it (`_tac_state` 1). |
| Menu layout | Settings panel 320 × 544 at the top right, bottom edge 560 against a preset bar at 572, at 1152×648, 1280×720 and 1024×576. |
| Cloud shadows | Strength 0.422 under broken afternoon cloud (coverage 0.45), 0.0000 under total overcast (coverage 0.999), 0.0004 at midnight. |
| Regression | Compass round trip still 0.0000° error for 270/315/0/35; field push and `base_rate` unchanged; F5/F6 round-trips height to +0.000000 m. |
| Tactical scene | `tactical_test.tscn` runs the same trio with no `SnowWeather` and no `SnowField`: sun el +21.40° at the fallback 10:30, dusk at 16:30 gives el −2.72° with sun energy 0 and ambient 0.264. |

## Observations for later

- **Realised mean wind runs above the JMA normal**, by design rather than by
  accident: the plan normalises the *base* wind to the monthly mean and then
  blends toward a 12–18 m/s front peak on top. With fronts occupying ~27 % of
  the month the realised mean is ~6.7 m/s against a 4.2–4.8 m/s normal.
  Reconciling the two exactly would require a calm-weather base of about
  1.1 m/s, which is unrealistically still for Rishiri. The knobs are
  `front_mean_interval_hours` and `front_wind_ms`; neither was changed, because
  both are recorded decisions.
- **MODERATE and HEAVY are rare states.** A front sets the wind to 12–18 m/s,
  and the plan's rule classifies any snowfall at ≥ 12 m/s as BLIZZARD, so
  frontal snow skips the middle of the ladder. A sampled game-day came out
  CALM 29 % / LIGHT 38 % / MODERATE 0 % / HEAVY 0 % / BLIZZARD 33 %.
- **A forced blizzard makes tactical combat nearly unreadable.** The
  `CombatCamera` sits 18 m up and ~21 m from the ground, against a severity-4
  1/e visibility of 17 m, so units fade almost completely. This is the agreed
  atmosphere table behaving as specified; moderate weather reads fine. If it
  needs softening, either cap severity while `GameManager.is_tactical()` or
  raise the row-4 `fog_density`.

## Controls

`F3` menu · `F4` channel · `F5` save field · `F6` load field ·
`F7` force blizzard · `F8` reroll wind direction.

### From SKYPLAN

- **The plan's longitude aside is wrong.** "Rishiri sits ~1° from the JST
  meridian, the offset is noise here" — it is 6.2°, worth 25 minutes, and
  without it December sunrise misses the plan's own ±0.25 h acceptance by 40
  minutes. The model carries longitude, the equation of time and a −0.833°
  horizon altitude. See the Sky section.
- **`-global_basis.z == sun_dir` is backwards** in the plan's verification 1.
  A `DirectionalLight3D` emits along −Z, so `+global_basis.z` points at the sun.
  The scene's authored Sun basis already agreed with the code.
- **Sunrise/sunset is closed form, not bisection.** The equation is solvable,
  and solving it keeps the answer exactly consistent with `sun_elevation_deg`.
- **The moon's declination swings with phase** (`δ · 0.95 · cos(2π p)`) rather
  than the plan's constant `−0.8 · δ`, which put a winter crescent overhead.
- **Cloud coverage needed calibrating against the noise's distribution.** The
  plan's `smoothstep(1 − coverage, …)` put 2 % of the sky under cloud at
  coverage 0.30.
- **`ALPHA` does not survive the sky half-res pass** on Forward Mobile, so the
  clouds are composited inside that pass instead of being handed back
  premultiplied. Written 1.0 read back as ≈1.94.
- **Weather dimming and cloud occlusion combine by `min()`, not by product.**
  They are two descriptions of the same sky; multiplying them dimmed a blizzard
  twice and missed the row's own `sun` multiplier by a factor of 4.
- **Verification 5's daytime fog expectation is the wrong sign.** It asks for
  storm fog to be *darker* than clear fog by day, but section 2's own formula
  ties fog brightness to the horizon, which at midday is unchanged — so a
  daytime whiteout comes out slightly brighter, which is what "a whiteout is
  bright and flat" means. The midnight case (fog luminance 0.053) is the rule
  that actually discriminates, and it holds.
- **Verification 5's 0.22× sun ratio needs severity 4**, which its own inputs
  (3 cm/h, 14 m/s) do not reach — they give ≈3.5. The probe asserts against the
  row the weather actually published and adds a second case that does reach 4.
- **The horizon gate is a ceiling applied after smoothing**, not a factor on the
  target. At `time_scale = 3600` the sun crosses the horizon inside one 2 s
  smoothing constant, and a target-side gate left the light burning at −15°.
- **Reserialising `snow_ground.gdshader` pruned the ground material's
  `shader_parameter/*` entries** from `test_world.tscn`, including the authored
  `ViewportTexture` preview binding for `snow_state`. Every one of them was
  either equal to the shader's own default or is pushed at runtime by
  `SnowField._publish_state()`, so nothing changed at runtime; the only loss is
  the in-editor preview binding, which `preview_in_editor` re-publishes anyway.

## Runtime probes

Reusable probes live in `res://.fennara/scripts/runtime/`, with shared helpers
in `snow_probe_lib.gd` (scene root, group lookups, frame settling, texture
sampling). Note that a running Godot process caches a probe by path, so patching
one and rerunning it in the same session runs the old code — restart the session
or use a new filename.
