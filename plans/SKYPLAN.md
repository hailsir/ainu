# Plan: Day/Night Sky Cycle, Cloud Layers, Unified Debug Menu

Godot 4.7, Forward Mobile renderer, Rishiri (45.2°N, 141.2°E), winter months
November–April. Plan only; nothing here is implemented. Follows the conventions
of `plans/SNOWPLAN.md` and `plans/WEATHERPLAN.md`: scene edits go through
Fennara `run_scene_edit_script`, scripts through `write_or_update_file`, and
verification through `runtime_session` probes saved under
`res://.fennara/scripts/runtime/`.

---

## Context (what exists today)

- `SnowWeather` (`scripts/snow/snow_weather.gd`) **owns the game clock**:
  `game_hours`, `clock_hour`, `day`, `time_scale = 60` (1 real s = 1 game min),
  `start_hour = 8`, `run_clock`. Everything downstream must use this clock; do
  not add a second one.
- `SnowWeather._apply_atmosphere()` currently **writes directly** to
  `WorldEnvironment.environment` (fog, ambient energy), the `Sun`
  (`light_energy`, `shadow_opacity`) and the `ProceduralSkyMaterial`
  (energy, top/horizon colour), as multipliers over a baseline captured in
  `_ready()`. That baseline model breaks the moment the sun moves, so this plan
  moves all light/sky writes into one new node and turns the weather into a
  data source.
- `test_world.tscn`: `WorldEnvironment` (background sky, `ambient_light_source
  = SKY`, energy 0.4, filmic tonemap), one `Sun` `DirectionalLight3D` at a fixed
  angle (`light_energy 1.2`, colour (1, 0.95, 0.85), shadows on), `SnowField`,
  `SnowPrecipitation`, `SnowWeather`, `SnowDebugOverlay` (last child).
- `SnowDebugOverlay` (`scripts/snow/snow_debug_overlay.gd`): CanvasLayer at
  `layer = 10`, top-right 300 px false-colour map of the snow field (F4 cycles
  seven channels), a status label, hotkeys F3 (toggle) F5/F6 (save/load)
  F7 (force blizzard) F8 (reroll wind).
- Mobile renderer limits that shape the design: **no volumetric fog, no auto
  exposure, one shadow-casting directional light is the practical budget,
  every sky-material write re-renders the radiance map** (hence the existing
  `SKY_WRITE_EPSILON`).
- Weather already publishes wind (`wind_vector`, `wind_speed_ms`), snowfall
  intensity, a continuous severity `target_severity() ∈ [0, 4]` and an
  `ATMOSPHERE` row table lerped by severity. Clouds and sky dimming should key
  off that severity, not off a second weather model.

---

## Architecture

```
SnowWeather (clock, climate, severity, wind)          -- unchanged owner of time
    │ reads: clock_hour, day, month, target_severity(), atmosphere_row(), wind_vector
    ▼
SkyController  (NEW, scripts/sky/sky_controller.gd, group "sky")
    │ single writer of: Sun, Moon, Environment (ambient + fog colour/density),
    │                   sky ShaderMaterial uniforms, cloud uniforms
    ▼
Sky shader     (NEW, shaders/sky.gdshader, shader_type sky)
    gradient + sun disc + moon disc/phase + stars + 2 cloud layers
    ▼
DebugMenu      (NEW, scripts/debug/debug_menu.gd, CanvasLayer)
    presets bar (bottom) + settings box (top-right) + snow map panel (moved in)
    writes overrides on SkyController / SnowWeather; never touches lights itself
```

**Single-writer rule.** After this work, only `SkyController` sets properties
on `Sun`, `Moon`, `Environment` and the sky material. `SnowWeather` keeps
computing severity and the atmosphere row but exposes them instead of applying
them (§5). `SnowPrecipitation` is untouched. The debug menu only sets
`override_*` exports (the `-1 = off` convention already used by `SnowWeather`)
so probes and the menu drive the same code path.

---

## 1. Clock and solar model (`scripts/sky/solar.gd`, static helper class `Solar`)

Pure functions, no scene access, unit-testable from a probe.

- **Day of year** from `SnowWeather.month` (0 = November … 5 = April) and
  `SnowWeather.day`: `N = MONTH_START_DOY[month] + day − 1` with
  `MONTH_START_DOY = [305, 335, 1, 32, 60, 91]` (non-leap; the 1840s year is
  cosmetic, we never show a date).
- **Declination** `δ = 23.44° · sin(deg_to_rad(360 / 365 · (N − 81)))`.
- **Hour angle** `H = 15° · (clock_hour − 12)`; local solar time is used as
  is (Rishiri sits ~1° from the JST meridian, the offset is noise here).
- **Elevation** `sin(el) = sin φ · sin δ + cos φ · cos δ · cos H`, `φ = 45.2°`.
- **Azimuth** (clockwise from north)
  `cos(az) = (sin δ − sin el · sin φ) / (cos el · cos φ)`, `az = 360° − az` when
  `H > 0`.
- **World vector** toward the sun, using the project compass (north −Z,
  east +X): `dir = Vector3(cos el · sin az, sin el, −cos el · cos az)`.
- **Light orientation**: a `DirectionalLight3D` shines along its local −Z, so
  `light.global_basis = Basis.looking_at(−dir, Vector3.UP)` (guard the
  near-vertical case with a fallback up vector).
- **Moon**: not worth a real ephemeris. Phase `p = fposmod(day + phase_offset,
  29.53) / 29.53` (0 new, 0.5 full). Position: reuse the solar formula with
  `H_moon = H − 360° · p` (a full moon rises at sunset, new moon tracks the sun),
  declination `−δ · 0.8` for a little separation from the sun's path.
  Illumination fraction `illum = 0.5 · (1 − cos(2π p))`.
- Reference values to test against (Wakkanai, JMA): 21 Dec sunrise ≈ 07:05,
  sunset ≈ 16:00, noon elevation ≈ 21°; 21 Mar sunrise ≈ 05:40, sunset ≈ 17:50,
  noon elevation ≈ 45°. December days are short; that is a real gameplay
  constraint and the model should not fudge it.
- Public API: `sun_dir(month, day, hour) -> Vector3`, `sun_elevation_deg(...)`,
  `moon_dir(...)`, `moon_phase(day) -> float`, `moon_illum(day) -> float`,
  `sunrise_hour(month, day)`, `sunset_hour(month, day)` (bisection on
  elevation = 0, cached per day).

---

## 2. SkyController (`scripts/sky/sky_controller.gd`, `class_name SkyController`, group `"sky"`, not `@tool`)

### Links (exports, NodePaths, resolved in `_ready`, all null-safe)

`weather_path` (falls back to group `snow_weather`), `environment_path`,
`sun_path`, `moon_path`, `snow_field_path` (only for fog height; falls back to
`SnowField.find_field`).

### Lookup tables keyed by sun elevation (degrees)

Stored as arrays of `{el, ...}` rows, lerped between adjacent rows exactly as
`SnowWeather._atmosphere_row()` does; keep the same helper style so one
reviewer can read both.

| el | sun colour | sun energy | zenith | horizon | ground | ambient colour | ambient energy | star alpha |
|---|---|---|---|---|---|---|---|---|
| −18 (night) | — | 0 | (0.010, 0.014, 0.035) | (0.020, 0.028, 0.060) | (0.010, 0.010, 0.015) | (0.07, 0.09, 0.16) | 0.12 | 1.0 |
| −12 | — | 0 | (0.015, 0.025, 0.070) | (0.060, 0.070, 0.140) | (0.015, 0.015, 0.025) | (0.09, 0.11, 0.20) | 0.14 | 0.9 |
| −6 (civil twilight) | (1.00, 0.45, 0.25) | 0 | (0.06, 0.09, 0.22) | (0.55, 0.32, 0.22) | (0.05, 0.04, 0.05) | (0.16, 0.16, 0.26) | 0.20 | 0.5 |
| 0 (sunrise/sunset) | (1.00, 0.55, 0.30) | 0.30 | (0.20, 0.32, 0.55) | (0.95, 0.55, 0.30) | (0.20, 0.16, 0.14) | (0.35, 0.30, 0.34) | 0.28 | 0.1 |
| 5 | (1.00, 0.75, 0.50) | 0.80 | (0.28, 0.45, 0.72) | (0.85, 0.72, 0.60) | (0.30, 0.26, 0.24) | (0.45, 0.47, 0.55) | 0.34 | 0 |
| 15 | (1.00, 0.92, 0.82) | 1.10 | (0.33, 0.52, 0.80) | (0.68, 0.76, 0.86) | (0.15, 0.12, 0.10) | (0.52, 0.58, 0.70) | 0.40 | 0 |
| 45 (max we ever see) | (1.00, 0.95, 0.85) | 1.20 | (0.35, 0.55, 0.82) | (0.65, 0.75, 0.85) | (0.15, 0.12, 0.10) | (0.55, 0.62, 0.75) | 0.40 | 0 |

The `el = 45` row equals today's scene values so the current daytime look is
the reference. Winter noon at 21° lands between the 15 and 45 rows, which is
right: low, warm-ish light all day. Colours are linear-space; the filmic
tonemap in the environment stays.

### Lights

- **Sun** (`DirectionalLight3D`, existing node): orientation from `Solar`,
  colour/energy from the table, times the weather `sun` multiplier (§5) and
  `override_sun_energy` if ≥ 0. `visible = false` once energy < 0.01 (no point
  paying for a light under the horizon).
- **Moon** (new `DirectionalLight3D` "Moon"): colour (0.62, 0.70, 0.95),
  energy `moon_base_energy (0.18) · moon_illum · smoothstep(−2°, 6°, moon_el)`,
  times the weather `sun` multiplier (clouds hide the moon too), times
  `override_moon_energy` semantics as above. `shadow_enabled` is handed over:
  **only one of Sun/Moon casts shadows at a time**, switched when the sun
  drops below −4° and back above it (with `shadow_opacity` faded over ~2 game
  minutes to hide the pop). Moon shadows at `shadow_opacity ≤ 0.5`.
- **Playability floor**: total scene light (`sun + moon + ambient`) never drops
  below `night_floor_energy` (0.10 default, exported). Midnight under a new
  moon under blizzard must still be navigable; the debug menu shows the floor
  as its own slider.

### Ambient

Switch `ambient_light_source` from `SKY` to `COLOR`. Reasons: sky-based
ambient depends on the radiance map, which we deliberately update lazily
(§3), and the debug spec asks for an explicit *Ambient Color Fill* control.
`ambient_light_color` and `ambient_light_energy` come from the table, times
the weather `ambient` multiplier, with `override_ambient_color` (alpha 0 =
off) and `override_ambient_energy` (−1 = off). Reflections still come from the
sky (`reflected_light_source` stays SKY).

### Fog colour

Fog density/height keep coming from the weather row (§5), but the **colour**
is now `row.fog_color · luminance(horizon colour) · fog_tint` so night fog is
dark and dawn fog picks up the horizon. The whiteout-is-bright rule from
`WEATHERPLAN.md` survives during the day and correctly fails at night.

### Update cadence

`_process` at full rate for light orientation (cheap), 10 Hz for
material/environment writes with the same `> 0.002` epsilon rule as the
weather, exponential smoothing `sky_smoothing_seconds = 2.0` on every
scalar/colour so a preset jump or a `time_scale` change eases rather than
snaps. Sky material uniforms are written through one `_set_sky_param()`
wrapper that skips unchanged values.

### Exports

```
@export_group("Site")        latitude_deg = 45.2
@export_group("Moon")        moon_base_energy = 0.18, moon_phase_offset_days = 0.0
@export_group("Night")       night_floor_energy = 0.10
@export_group("Smoothing")   sky_smoothing_seconds = 2.0
@export_group("Debug overrides")   # -1 / alpha 0 = off, applied last
    override_hour = -1.0            # pins the sky to an hour without touching the clock
    override_sun_energy = -1.0
    override_moon_energy = -1.0
    override_ambient_energy = -1.0
    override_ambient_color = Color(0,0,0,0)
    override_cloud_coverage = -1.0
    override_cloud_speed = -1.0
    weather_affects_sky = true
    stars_enabled = true
    clouds_enabled = true
```

Public read-only state for the menu/probes: `sun_elevation_deg`,
`sun_azimuth_deg`, `moon_elevation_deg`, `moon_phase`, `effective_hour`,
`cloud_coverage`, `is_night`.

---

## 3. Sky shader (`shaders/sky.gdshader`, `shader_type sky`)

Replaces the `ProceduralSkyMaterial` sub-resource with a `ShaderMaterial` on
the same `Sky`. Uniforms are all pushed by `SkyController`; the shader never
reads `TIME` for anything but cloud scroll so the radiance map does not churn.

- **Gradient**: `mix(horizon, zenith, pow(max(EYEDIR.y, 0), 0.6))`, ground
  half `mix(horizon, ground, pow(−EYEDIR.y, 0.5))`.
- **Sun**: disc `smoothstep(cos(0.53°·0.5), …)` on `dot(EYEDIR, sun_dir)`,
  plus a glow `pow(max(dot, 0), 64) · sun_color · glow_strength`, plus a wide
  horizon glow `pow(max(dot(EYEDIR.xz, sun_dir.xz), 0), 4) · (1 − |EYEDIR.y|)`
  scaled by `smoothstep(−8°, 3°, el) · (1 − smoothstep(3°, 15°, el))` so dawn
  and dusk warm the horizon on the sun's side only.
- **Moon**: disc (0.5° apparent) on `moon_dir` with a phase mask: project
  `EYEDIR` into the moon's local frame and darken the half facing away from
  the sun by `illum` (a `smoothstep` on the lit-fraction offset gives a
  believable crescent for the cost of two dots).
- **Stars**: hash-based point field on `EYEDIR` quantised to a 512 grid,
  brightness `star_alpha` from the table, slight twinkle from a low-frequency
  hash (no `TIME` dependence needed — twinkle from `sin(hash · 40 + TIME)` is
  allowed only when `stars_enabled`, and it lives in the half-res pass so the
  radiance map is unaffected). Stars are hidden under cloud coverage (§4).
- **Sky resource settings**: `process_mode = PROCESS_MODE_INCREMENTAL`,
  `radiance_size = RADIANCE_SIZE_128` (Mobile: 256 is the default and
  measurably slower). Static parts of the sky go in the full-res pass, the
  clouds and stars use `render_mode use_half_res_pass` and are written in the
  `AT_HALF_RES_PASS` branch, then composited.
- Output through `COLOR`; keep values ≤ ~8.0 so the filmic tonemap does not
  clip the sun disc into a white blob (the disc is a look feature, not a
  light source, `Sun` does the lighting).

---

## 4. Cloud layers

Two layers, both in the sky shader's half-res pass, each a planar projection
of a seamless FBM noise texture (`NoiseTexture2D`, `seamless = true`,
`FastNoiseLite` FBM 4 octaves, 512², generated once by `SkyController` and
kept as a uniform; one texture serves both layers at different scales).

| Layer | Height | Scale | Speed | Coverage source | Look |
|---|---|---|---|---|---|
| High (cirrus) | 8 km plane | 1 tile / 60 km | wind × 0.25 | `high_coverage` export (0.35), drifts ±0.1 with a 6 h noise | thin, lit warm at dawn/dusk (they catch light before the ground) |
| Low (stratus/cumulus) | 1.5 km plane | 1 tile / 8 km | wind × 1.0 | weather severity (§5) | soft-edged, grey underside, bright rim on the sun side |

- **Projection**: for `EYEDIR.y > 0.02`, `uv = EYEDIR.xz / EYEDIR.y · height
  / tile_size + scroll`; fade to horizon by `smoothstep(0.02, 0.12, EYEDIR.y)`
  to hide the projection singularity. Below the horizon the cloud term is 0.
- **Scroll**: `scroll += wind_vector.xz · speed · dt` accumulated on the CPU
  in `SkyController` (so a wind reroll turns the field rather than jumping
  it) and pushed as a uniform; not derived from `TIME` in the shader.
- **Coverage → density**: `d = smoothstep(1 − coverage, 1 − coverage + softness,
  noise)`; `softness` 0.25 for stratus, 0.12 for cirrus.
- **Lighting**: `cloud_col = mix(underside_col, lit_col, pow(max(dot(EYEDIR,
  sun_dir), 0), 2) · sun_strength)`; `lit_col = sun_color · 1.2`,
  `underside_col = horizon_col · 0.55`; at night both terms scale with
  moonlight (`moon_color · moon_energy · 4`) so clouds read as pale patches
  against stars instead of vanishing.
- **Composite**: `sky = mix(sky, cloud_col, d_high · 0.6)` then `mix(…,
  d_low)`; stars multiply by `(1 − d_high) · (1 − d_low)`; sun disc and glow
  attenuate by `1 − d_low` at the sun's own direction (sample the noise once
  more at the sun's projected uv on the CPU and pass `sun_occlusion` as a
  uniform — this also drives `Sun.light_energy` in §5 so a cloud crossing the
  sun dims the ground).
- **Cloud shadows on the ground (optional task H2)**: `snow_ground.gdshader`
  samples the same noise texture at `world_xz / low_tile_size + scroll` and
  multiplies the direct-light term by `1 − d_low · shadow_strength (0.5)`.
  Cheap, one texture fetch, and the only way to get moving cloud shadows on
  the Mobile renderer (directional lights have no projector). Grid combat at
  the top-down camera benefits most. Skip if the frame budget in the
  verification step is missed.
- **Precipitation coupling**: none needed; `SnowPrecipitation` already keys
  off intensity. The overcast that accompanies snow comes from severity.

---

## 5. Weather integration (`scripts/snow/snow_weather.gd` changes)

The weather stops writing to lights and sky and becomes a data source.

- Delete the writes in `_apply_atmosphere()` to `_sun`, `_sky_material`,
  `ambient_light_energy`, and the sky-colour lerp. Keep the fog writes
  (`fog_enabled/mode/density/height/height_density/sky_affect`) **or** move
  them too — decision: **move them**, so the fog colour can be time-tinted
  (§2) and there is exactly one writer. `_capture_baseline()` /
  `_restore_baseline()` become no-ops and are removed; `sun_path` and
  `environment_path` exports stay for one release (unused, documented) so
  probes referencing them do not break, then go.
- Keep `_severity` smoothing and `drive_atmosphere` (renamed in meaning:
  "publish atmosphere", default true).
- New public API:
  - `atmosphere_row() -> Dictionary` — the smoothed, lerped `ATMOSPHERE` row
    (`fog_density`, `fog_color`, `sky_affect`, `sun`, `shadow`, `sky_energy`,
    `ambient`, `height_density`).
  - `severity() -> float` — the smoothed value (not the target).
  - `cloud_coverage_target() -> float` — `0.30 + 0.70 · smoothstep(0, 3, severity)`,
    with `+0.15 · front_envelope` so a front overcasts before the snow arrives.
  - signal `atmosphere_changed` is **not** added; `SkyController` polls at
    10 Hz like the weather itself does.
- The `sky_energy` column is reinterpreted as a multiplier on the *sky
  shader's* overall brightness (`sky_energy` uniform), and `sky_affect` as
  the lerp of the gradient toward `fog_color` (was the ProceduralSky
  top/horizon lerp; same intent).
- `set_time_of_day(hour: float)` — new method: `game_hours = floor(game_hours /
  24) · 24 + hour`; re-evaluates and pushes to the field. This is what presets
  call, so the clock and the sky never disagree.

---

## 6. Debug menu (`scripts/debug/debug_menu.gd`, CanvasLayer `layer = 10`, group `"debug_menu"`)

Replaces `SnowDebugOverlay` as the debug entry point. The snow map view moves
into it as a panel; the overlay script is deleted once the menu owns all of
its features (F4 channel cycling, F5/F6 save/load, F7/F8 weather hotkeys and
the status line), so nothing regresses.

### Layout (built in code, no .tscn; sizes in px at the design resolution, `canvas_items` stretch handles scaling)

```
┌──────────────────────────────────────────────────────────────┬────────────────────┐
│                                                              │ SKY & WEATHER      │  top-right box,
│                                                              │ Time of day  ▮──── │  320 px wide,
│                                                              │ Time scale   ──▮── │  anchored right,
│                                                              │ Sunlight     ───▮─ │  16 px margin
│                                                              │ Moonlight    ▮──── │
│                                                              │ Ambient fill ──▮── │
│                                                              │ Ambient col  [■]   │
│                                                              │ Cloud cover  ──▮── │
│                                                              │ Cloud speed  ─▮─── │
│                                                              │ Fog density  ▮──── │
│                                                              │ Night floor  ▮──── │
│                                                              │ ☑ Run clock        │
│                                                              │ ☑ Weather → sky    │
│                                                              │ ☑ Clouds ☑ Stars   │
│                                                              │ ☐ Force blizzard   │
│                                                              │ ☐ Cloud shadows    │
│                                                              │ ☑ Snow map panel   │
│  ┌ snow map (300 px) ┐                                       ├────────────────────┤
│  │  [F4] Height       │                                      │ status: day/clock/ │
│  │                    │                                      │ el/az/phase/state  │
│  └────────────────────┘                                      └────────────────────┘
│      [ Sunrise ] [ Daytime ] [ Afternoon ] [ Sunset ] [ Twilight ] [ Midnight ]   │  bottom-centre bar
└───────────────────────────────────────────────────────────────────────────────────┘
```

- **Presets bar** (bottom-centre, `HBoxContainer` in a `PanelContainer`,
  anchored bottom, 24 px up): six `Button`s in a `ButtonGroup` (toggle mode,
  so the active preset shows the gold fill). Clicking calls
  `SnowWeather.set_time_of_day(hour)` and applies the preset's overrides.
  Moving any slider afterwards deselects the group (custom state, no preset
  is "active"). A seventh small button **Live** clears all overrides.
- **Settings box** (top-right, `PanelContainer` → `VBoxContainer`, 320 px):
  one row per control (`Label` 120 px + `HSlider` + value `Label` 56 px).
  Sliders write the matching `override_*` on `SkyController`/`SnowWeather`
  on `value_changed`; when an override is off (−1) the slider tracks the
  live value each frame and shows it at 60 % alpha so the user sees what the
  sky is doing. Checkboxes are `CheckBox` nodes.
- **Snow map panel**: the existing `ColorRect` + `snow_debug.gdshader` +
  channel label, verbatim, at bottom-left (it was top-right; the sliders take
  that corner now). Hidden by the *Snow map panel* checkbox.
- **Status line** under the sliders: `day N HH:MM | sun el +21.3° az 178° |
  moon 0.62 el −40° | CALM | snow 0.00 cm/h | wind 4.1 m/s from 265 | seed …`
  — a superset of the current overlay label.
- **Input**: F3 toggles the whole menu. While open, `Input.mouse_mode =
  MOUSE_MODE_VISIBLE` (previous mode restored on close) and the CanvasLayer's
  controls use `MOUSE_FILTER_STOP` so clicks on the panels do not reach the
  camera/tactical raycast; clicks elsewhere still play. The game is **not**
  paused (the point is to watch the sky move). Existing hotkeys F4–F8 keep
  working whether the menu is open or not.
- **Presets** (`const PRESETS`; hours are February defaults, the menu shows the
  actual sunrise/sunset for the current month next to the buttons as a hint):

| Preset | hour | sunlight × | moonlight × | ambient energy / colour | cloud cover | notes |
|---|---|---|---|---|---|---|
| Sunrise | `sunrise_hour + 0.25` | 1.0 | 0 | 0.28 / (0.35, 0.30, 0.34) | 0.35 | sun just above horizon, long shadows |
| Daytime | 10:30 | 1.0 | 0 | 0.40 / (0.55, 0.62, 0.75) | 0.30 | |
| Afternoon | 13:30 | 1.0 | 0 | 0.40 / (0.55, 0.62, 0.75) | 0.45 | past solar noon, still low |
| Sunset | `sunset_hour − 0.25` | 1.0 | 0 | 0.28 / (0.35, 0.30, 0.34) | 0.40 | |
| Twilight | `sunset_hour + 0.6` (el ≈ −6°) | 0 | 0.6 | 0.20 / (0.16, 0.16, 0.26) | 0.35 | civil twilight, first stars |
| Midnight | 00:00 | 0 | 1.0 | 0.12 / (0.07, 0.09, 0.16) | 0.30 | full moon phase forced for the preset |

  A preset sets `override_hour = −1` (it moves the real clock instead) and sets
  `override_sun_energy` only when the multiplier ≠ 1, so "Daytime" is simply
  the live sky at 10:30. "Midnight" also sets `moon_phase_offset_days` so the
  moon is full; *Live* restores it.

### Theme (`scripts/debug/debug_theme.gd`, builds a `Theme` at runtime; nothing saved to disk)

| Token | Value |
|---|---|
| panel background | `Color(0.055, 0.10, 0.22, 0.92)` (dark navy), corner radius 6, 1 px border in gold at 40 % alpha |
| text | white `Color(1, 1, 1)`; secondary/tracking values at 60 % alpha |
| gold highlight | `Color(0.83, 0.68, 0.21)` (#D4AE36) |
| button normal | transparent, white text |
| button hover | gold text, 1 px gold border |
| button pressed / active preset | gold fill, navy text |
| slider grabber + filled track | gold; empty track white at 25 % alpha |
| checkbox checked | gold check on navy; unchecked white outline |
| font | default project font, 14 px; section titles 12 px letter-spaced uppercase, gold |

Implemented as `StyleBoxFlat`s on `Theme` types `PanelContainer`, `Button`,
`HSlider`, `CheckBox`, `Label`; the theme is assigned to the menu's root
`Control` so every child inherits it. Keep the visual in one file so the
in-game HUD can reuse it later.

### Removal of `SnowDebugOverlay`

Task D deletes `scripts/snow/snow_debug_overlay.gd` and its scene node after
Task C reaches parity; `plans/SNOWPLAN-NOTES.md` Files table and the F-key
controls line are updated in the same change.

---

## 7. Scene wiring (Fennara `run_scene_edit_script` on `res://scenes/world/test_world.tscn`; never hand-edit the .tscn)

- `WorldEnvironment.environment.sky.sky_material` → new `ShaderMaterial`
  with `shaders/sky.gdshader`; `sky.process_mode = INCREMENTAL`,
  `radiance_size = 128`; `ambient_light_source = COLOR`,
  `ambient_light_color = (0.55, 0.62, 0.75)`, `ambient_light_energy = 0.4`
  (the daytime row, so the scene looks unchanged before `SkyController` runs).
- Add `Moon` (`DirectionalLight3D`, `light_color (0.62, 0.70, 0.95)`,
  `light_energy 0`, `shadow_enabled false`, `visible false`) after `Sun`.
- Add `SkyController` (Node) after `SnowWeather`, exports: `weather_path
  ../SnowWeather`, `environment_path ../WorldEnvironment`, `sun_path ../Sun`,
  `moon_path ../Moon`, `snow_field_path ../SnowField`.
- Replace `SnowDebugOverlay` with `DebugMenu` (CanvasLayer, script
  `debug_menu.gd`), still the **last child**.
- `SnowField.obstacle_skip_names` must gain `Moon`, `SkyController`,
  `DebugMenu` (obstacle collection is name-based; see WEATHERPLAN risks).
- `SnowWeather`: no export changes except the two deprecated paths noted in §5.
- `tactical_test.tscn`: add the same `Sky`/`Moon`/`SkyController` trio so the
  combat test scene lights the same way (no debug menu there).

---

## 8. Docs

- Save this plan as `plans/SKYPLAN.md`.
- `plans/SNOWPLAN-NOTES.md`: Files table gains `sky_controller.gd`, `solar.gd`,
  `sky.gdshader`, `debug_menu.gd`, `debug_theme.gd`, loses
  `snow_debug_overlay.gd`; new "Sky" section with the elevation table, the
  single-writer rule, the solar formulas and compass mapping, the moon
  approximation and its known inaccuracies, the cloud projection maths and
  the reason clouds do not use `TIME`; the "Atmosphere" section is rewritten
  to say the weather publishes and the sky applies; controls line becomes
  `F3 menu · F4 channel · F5/F6 save/load · F7 blizzard · F8 reroll`.

---

## Verification (Fennara `runtime_session` + `runtime_script`; save probes under `res://.fennara/scripts/runtime/`)

1. **Solar model** (`probe_sky_solar.gd`): for December (month 1, day 21) and
   March (month 4, day 21) assert `sunrise_hour`/`sunset_hour` within ±0.25 h
   of the JMA values in §1 and noon elevation within ±1.5°. Assert
   `sun_dir(…, 12.0).z < 0` is **false** (at noon the sun is due south, i.e.
   +Z in this world) and `sun_dir(…, 6.0).x > 0` (east). Assert the `Sun`
   node's `-global_basis.z` equals `sun_dir` within 1e-3.
2. **Single writer**: with `SkyController` present, monkey-check that
   `SnowWeather` has no remaining references to `_sun`, `_sky_material` or
   `_environment` writes (grep in the probe over `get_script().source_code`
   for `.light_energy =` and `sky_material.` — must be 0 hits).
3. **Cycle sweep** (`probe_sky_cycle.gd`): `time_scale = 3600` (1 real s = 1
   game h), sample every 0.25 real s for 24 s: sun energy is 0 while
   `el < −1°`, monotonic with `el` otherwise; moon and sun never both cast
   shadows; total light never below `night_floor_energy`; `ambient_light_color`
   changes every sample and is never NaN; no sky uniform write happens when
   the delta is below epsilon (count writes, expect < 20 % of samples at
   `time_scale = 60`).
4. **Presets** (`probe_sky_presets.gd`): iterate the six presets through the
   menu's public `apply_preset(name)`; after 3 s of smoothing assert
   `clock_hour` equals the preset hour ±0.02, the sun elevation is above 0
   for the four daytime presets and below −5° for Twilight/Midnight, and
   moon energy > 0.1 at Midnight. `apply_preset("Live")` clears every
   `override_*` back to −1 / alpha 0.
5. **Weather → sky**: `override_intensity_cm_per_h = 3.0`,
   `override_wind_ms = 14` at Daytime: within 10 s `cloud_coverage ≥ 0.9`,
   `Sun.light_energy` ≈ 0.22 × the clear-sky value ±10 %, fog colour
   luminance below 0.9 of clear; then at Midnight the fog colour luminance is
   < 0.1 (dark whiteout, not a bright one).
6. **Visuals** (`ctx.capture` from the rig camera, same spot, same yaw, sky
   in frame): one capture per preset, plus Daytime blizzard, plus Midnight
   F7 blizzard, plus the DebugMenu open showing the theme. Check: sun disc
   not clipped, horizon glow on the sun's side only, crescent/full moon
   reads, stars invisible at Daytime and visible at Midnight, clouds show
   parallax between two captures 5 s apart with wind ≥ 4 m/s, no seam at the
   half-res composite. Also one capture from the `CombatCamera` at Sunset
   to confirm long shadows across the grid.
7. **Performance**: 300-frame average at Daytime clear, Daytime overcast,
   Midnight; stay within ~1.5 ms of the existing 6.06 ms baseline on the same
   machine. If over: drop `radiance_size` to 64, then remove the cirrus layer,
   then the stars twinkle, in that order. Cloud shadows (H2) are only merged
   if they cost < 0.3 ms.
8. **Regression**: existing snow probes (`probe_weather_*.gd`, the trail and
   compaction probes) pass unchanged; F5/F6 save/load unaffected (sky state
   is not persisted; `game_hours` is not persisted either, by the same design
   choice as the weather — note as a follow-up if saves should keep the hour).
9. **Menu input**: with the menu open, an `InputEventMouseButton` at a slider
   position moves the slider and does **not** reach `world_setup.gd`'s
   tactical raycast (enter combat with T first, then click on the panel:
   `_tac_state` stays `IDLE`); a click on empty screen does select a unit.

---

## Task breakdown for agents

| # | Task | Depends on | Acceptance criteria |
|---|------|-----------|---------------------|
| A | `solar.gd` static helpers + probe 1 | — | Probe 1 passes; pure functions, no scene access |
| B | `SkyController` driving Sun/Moon/ambient/fog colour from the elevation table; `SnowWeather` converted to publisher (§5); scene wiring §7 for `test_world.tscn` | A | Probes 2, 3 pass; Daytime at 10:30 looks like today's scene; night has a visible floor |
| C | `sky.gdshader` gradient + sun + moon + stars; `Sky` resource settings | B | Visual captures for Sunrise/Midnight; radiance re-render count stays low (probe 3 write counter) |
| D | Cloud layers (two-layer projection, wind scroll, coverage from severity, sun occlusion uniform) | C | Overcast under blizzard, clear under calm, parallax capture pair, probe 5 |
| E | `DebugMenu` + `debug_theme.gd`: presets bar, settings box, checkboxes, status line, snow map panel migrated, F-keys migrated; delete `SnowDebugOverlay` | B (C/D for the cloud/star controls) | Probes 4, 9; capture of the open menu matches the theme table; every old overlay feature reachable |
| F | `tactical_test.tscn` wiring, CombatCamera sunset capture | B, C | Grid combat lit by the same sky; shadows on the grid at Sunset |
| G | Docs (§8), Verified table rows, controls line | all | `SNOWPLAN-NOTES.md` reflects the single-writer rule and the new files |
| H1 | Perf pass (probe 7) and the fallback ladder | C, D | ≤ +1.5 ms over baseline in all three scenarios |
| H2 | *Optional* cloud shadows in `snow_ground.gdshader` | D, H1 | Moving shadows on the ground under partial cover, < 0.3 ms |

A and B are the critical path; C and E can start in parallel once B lands
(E can build its panel against B's overrides and pick up cloud/star controls
when D merges). Agree on the **elevation table values and the override export
names first** — the menu, the probes and the controller all key off them.

---

## Risks and notes

- **Radiance map churn** is the main Mobile-renderer hazard: every sky
  uniform change can trigger a re-render. Incremental mode, 128 px radiance,
  the 10 Hz cadence and the epsilon guard are all required, not optional;
  probe 3 counts writes to keep it honest.
- `ambient_light_source = COLOR` loses the sky's directional tint in ambient.
  If the flat look bothers reviewers, the fallback is `SKY` with
  `ambient_light_sky_contribution = 0.5` plus the colour fill; the debug
  slider then blends rather than replaces. Decide on the Daytime capture.
- One shadow caster at a time: the Sun→Moon hand-over must be faded, or the
  ground pops at dusk. Check `DirectionalLight3D.shadow_opacity` behaviour
  during the fade on Mobile with `get_class_info` before relying on it.
- The moon model is an approximation (rise/set times can be off by hours
  against reality); acceptable for a game, but document it so nobody files
  it as a bug.
- Winter days at 45°N are ~9 h in December. Gameplay pacing (`time_scale`)
  may want a day/night ratio knob later; do not bake one in now.
- Presets move the *real* clock; a probe that pins conditions should keep
  using `override_hour` so the weather timeline is not disturbed.
- Sky-shader cloud projection breaks down near the horizon; the fade band is
  a look choice, tune it on the Sunset capture.
- Menu z-order: the menu is `layer = 10` like the old overlay; any future HUD
  must sit below it. The tactical UI (when it exists) goes on a lower layer.

## Files

- Create: `scripts/sky/solar.gd`, `scripts/sky/sky_controller.gd`,
  `shaders/sky.gdshader`, `scripts/debug/debug_menu.gd`,
  `scripts/debug/debug_theme.gd`, `plans/SKYPLAN.md`,
  `.fennara/scripts/runtime/probe_sky_solar.gd`, `probe_sky_cycle.gd`,
  `probe_sky_presets.gd`, `probe_sky_weather.gd`, `probe_debug_menu_input.gd`.
- Modify: `scripts/snow/snow_weather.gd`, `scenes/world/test_world.tscn`
  (via Fennara), `scenes/tactical/tactical_test.tscn` (via Fennara),
  `plans/SNOWPLAN-NOTES.md`, optionally `shaders/snow_ground.gdshader` (H2).
- Delete (Task E): `scripts/snow/snow_debug_overlay.gd`.
