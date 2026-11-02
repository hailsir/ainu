# Plan: Visible Snowfall Weather + Random Wind (Rishiri, Hokkaido)

## Context

The snow heightfield (`scripts/snow/snow_field.gd`, `shaders/snow_sim.gdshader`) already refills trails and builds drifts, but the weather driving it is static: `wind_direction_deg` is a fixed export (35°), `snowfall` is a bool with a gamey 5 cm/min rate, and nothing is visible in the air. The shelter map (`assets/snow/snow_maps.png` alpha) was baked for that one fixed direction by `SnowMapsBaker.bake_shelter_into()`, so it becomes wrong the moment the wind is randomised.

Goal: a weather layer that (1) shows falling snow, ground-blizzard streaks, fog and dimming that respond to the current weather, (2) rolls a random wind direction per session and lets it wander, and (3) drives both falling-snow deposition and the wind refill/drift of the existing sim from Rishiri winter climate normals. Trails and drifts must depend on where the wind actually blows.

Decisions taken with the user:
- **Time scale**: 1 real second = 1 game minute (`time_scale = 60`). Rates are authored in cm per game-hour and converted to the sim's cm per real minute (at 60x, 1 cm/game-h = 1 cm/real-min).
- **Wind**: roll once at start around the prevailing winter westerly, then slow wander with mean reversion plus occasional front passages (30–90° swings).
- **Extras**: ground blizzard streaks, exponential fog/whiteout, sun and sky dimming. All in scope.
- **Season**: selectable month export (Nov–Apr), default January. No calendar progression.

## Climate data (embed as constants)

JMA 1991–2020 normals. Rishiri's own AMeDAS (Kutsugata, 14 m) has no snow gauge, so snowfall comes from Wakkanai (JMA 47401, ~50 km NE, same Sea-of-Japan winter-monsoon regime); wind speed comes from Kutsugata itself.

| Month | Snowfall cm | Snowy days | Mean wind m/s (Kutsugata) | Prevailing wind FROM | Mean temp °C |
|---|---|---|---|---|---|
| Nov | 41 | 20.5 | 4.8 | W | 4.0 |
| Dec | 122 | 28.5 | 4.8 | W | −1.8 |
| Jan | 129 | 30.4 | 4.6 | W | −4.0 |
| Feb | 105 | 26.0 | 4.4 | W | −3.8 |
| Mar | 68 | 24.3 | 4.2 | W | −0.3 |
| Apr | 9 | 12.6 | 3.7 | SSW | 4.6 |

Derived: January mean deposition = 129 / (31 × 24) = **0.173 cm per game-hour** (Dec 0.164, Feb 0.156, Mar 0.091, Nov 0.057, Apr 0.0125). A game day at 60x deposits ~4.2 cm on average. Rishirifuji town reports ~1.5 m seasonal accumulation, annual mean wind > 5 m/s and named winter snow gusts ("hitaki"); the JMA Wakkanai office warns of week-long blizzards and whiteouts. Intensity classes (cm per game-hour): CALM 0 · LIGHT < 0.3 · MODERATE 0.3–1.0 · HEAVY 1.0–2.5 · BLIZZARD ≥ 2.5, or any snow with wind ≥ 12 m/s.

One non-JMA assumption: **12 snowing hours per snowy day** (used only for the snowing/not-snowing duty cycle; the monthly total is calibrated exactly regardless).

Sources (record in SNOWPLAN-NOTES):
- https://www.data.jma.go.jp/stats/etrn/view/nml_sfc_ym.php?prec_no=11&block_no=47401 (Wakkanai)
- https://www.data.jma.go.jp/stats/etrn/view/nml_amd_ym.php?prec_no=11&block_no=0002 (Kutsugata AMeDAS)
- https://en.wikipedia.org/wiki/Wakkanai (snowy-day counts)
- https://www.town.rishirifuji.hokkaido.jp/rishirifuji/1158.htm

## Compass convention (write into SNOWPLAN-NOTES)

World north = −Z, east = +X. Bearing β = direction the wind blows **from**, clockwise from north; unit vector `(sin β, 0, −cos β)`. The field's `wind_direction_deg` α = direction the wind blows **toward**, from +X toward +Z (`Vector2(cos α, sin α)` maps onto field UV = world XZ).

```
field_deg   = fposmod(from_deg + 90.0, 360.0)   # from W (270) → 0 → blows toward +X (east)
from_deg    = fposmod(field_deg - 90.0, 360.0)
world_toward = Vector3(cos(α), 0, sin(α))
```
Checks: from NW (315) → α 45 → toward SE; from N (0) → α 90 → toward +Z (south). The existing default α = 35 is a wind from WNW (305). `snow_sim.gdshader` (`dot(grad, wind_dir)` marks slopes the wind climbs as windward) and `SnowMapsBaker._march(-wind)` (upwind hit = lee) already treat the vector as "toward"; nothing in the sim's wind math changes.

Verified facts that shape the design: `SnowField._process` only produces `accum_dt > 0` when `wind_enabled` is true, so snowfall is gated by it (weather keeps it true and expresses calm as low `wind_strength`). The deform camera has its own Environment and the sim viewports are `disable_3d`, so world fog cannot corrupt the height pipeline. Godot 4.7 exposes `GPUParticles3D.amount_ratio`, `transform_align` (incl. `TRANSFORM_ALIGN_Z_BILLBOARD_Y_TO_VELOCITY`), `ParticleProcessMaterial.turbulence_*`, `Environment.fog_*`, `ProceduralSkyMaterial.sky_energy_multiplier`, `GradientTexture2D.FILL_RADIAL`; all run on Forward Mobile.

## Architecture

```
TestWorld
├── SnowField            existing; gains a directional shelter pass + new uniforms/setters
├── SnowWeather          NEW Node: game clock, month table, intensity/wind/direction models,
│                        atmosphere driver → pushes into SnowField, SnowPrecipitation, env, sun
├── SnowPrecipitation    NEW Node3D: follows the current Camera3D; flake + streak GPUParticles3D
├── WorldEnvironment/Sun existing; driven by SnowWeather (baselines restored on exit)
└── SnowDebugOverlay     existing; weather line, DynShelter channel, F7/F8
```

Implementation order: (1) baker refactor + shelter shader + SnowField shelter pass + sim shader (testable alone), (2) SnowWeather core, (3) SnowPrecipitation, (4) atmosphere, (5) overlay + scene wiring, (6) docs, (7) verification.

---

## 1. Directional shelter (GPU pass inside SnowField)

**`scripts/snow/snow_maps_baker.gd`**: extract the AABB rasterisation loop of `bake_shelter_into` into `static func rasterise_mask(obstacles, field_centre, field_size, res := SHELTER_RESOLUTION) -> PackedByteArray`, add `static func mask_to_image(mask, res) -> Image` (`Image.create_from_data(res, res, false, FORMAT_R8, bytes)` with 0/255). `bake_shelter_into` keeps its signature and calls `rasterise_mask`; the CPU march remains the offline path and the `use_dynamic_shelter = false` fallback. Extend the default `skip_names` of `collect_obstacles` with `"TacticalCombat"`, `"SnowPrecipitation"`, `"SnowWeather"` (tactical unit capsules are MeshInstance3Ds and must not block wind).

**New `shaders/snow_shelter.gdshader`** (canvas_item, unshaded, blend_disabled). Uniforms: `obstacle_mask` (filter_nearest, repeat_disable), `vec2 wind_dir`, `field_size`, `int mask_resolution`, `lee_distance_m = 12`, `windward_distance_m = 4`, `open_exposure = 0.55`, `lee_shelter = 1.0`, `windward_shelter = 0.12`. Mirrors `_march` in mask-cell units: under obstacle → 0; march upwind (`UV - wind_dir * i * step_uv`) up to `ceil(lee_distance / cell_m)` steps, hit → `mix(lee_shelter, open_exposure, i/steps)`; else march downwind up to the windward steps, hit → `mix(windward_shelter, open_exposure, i/steps)`; else `open_exposure`. Explicit 0..1 bounds test each step (repeat_disable clamps to edge, which would read as a hit).

**`scripts/snow/snow_field.gd`** additions:
- Export group "Shelter": `use_dynamic_shelter = true`, `shelter_resolution = 256`, `shelter_redraw_threshold_deg = 3.0`, `obstacle_skip_names: PackedStringArray`, `snowfall_wind_bias_per_strength = 0.35`, `snowfall_wind_bias_max = 0.7`.
- `_build_shelter_viewport()` from `_build()` after `_build_readback_viewport()`: `_make_viewport(shelter_resolution, "SnowShelter")` + full-rect ColorRect with the shelter material (same pattern as `_build_readback_viewport`).
- `rebuild_obstacle_mask(root: Node = null)`: root defaults to `owner` or parent; `SnowMapsBaker.collect_obstacles` → `rasterise_mask` → `mask_to_image` → `ImageTexture.create_from_image`; sets `obstacle_mask`, `field_size`, `mask_resolution` on the shelter material; forces a redraw. Called `call_deferred` from `_ready`.
- `_update_shelter_pass()` at the top of `_step()`: when dynamic shelter is on and the angle difference from the last rendered direction exceeds the threshold (or never rendered), set `wind_dir = _wind_vector()`, viewport `UPDATE_ONCE`, record the direction, increment a probe-visible `shelter_pass_count`.
- In `_step()` uniform block: `shelter_tex = _shelter_viewport.get_texture()`, `use_dynamic_shelter`, `snowfall_wind_bias = clampf(wind_strength * snowfall_wind_bias_per_strength, 0, snowfall_wind_bias_max)`.
- New API: `set_snowfall(cm_per_min)` (`snowfall = cm_per_min > 0; snowfall_cm_per_min = cm_per_min`), `get_shelter_texture()`, `get_wind_vector3()`.

**`shaders/snow_sim.gdshader`**:
```
uniform sampler2D shelter_tex : filter_linear, repeat_disable;
uniform bool use_dynamic_shelter = false;
uniform float snowfall_wind_bias = 0.0;
float shelter = clamp(use_dynamic_shelter ? texture(shelter_tex, uv).r : m.a, 0.0, 1.0);
h += (rate + snowfall_rate * mix(1.0, shelter, snowfall_wind_bias)) * accum_dt;
```
Windward, erosion and freshness terms unchanged. Falling snow therefore deposits less on windward faces and more in lee pockets as the wind rises.

**`shaders/snow_debug.gdshader`**: `uniform sampler2D shelter_tex`; `mode == 6` → its `.r`. Overlay adds `"DynShelter"` to `CHANNEL_NAMES`.

---

## 2. Weather node: new `scripts/snow/snow_weather.gd` (`SnowWeather extends Node`, group `"snow_weather"`, not `@tool`)

**Exports** (grouped): Links `field_path`, `environment_path`, `sun_path`, `precipitation_path`. Clock `time_scale = 60`, `start_hour = 8`, `run_clock = true`. Climate `month` (`@export_enum` Nov..Apr, default January), `weather_seed = 0` (0 = random, chosen seed printed), `prevailing_from_deg = 270`, `direction_swing_deg = 35`, `direction_noise_hours = 10`, `snowing_hours_per_snow_day = 12`, `intensity_noise_hours = 8`, `intensity_shape_power = 1.8`, `flutter_period_hours = 0.5`, `flutter_amount = 0.2`, `wind_sigma = 0.45`, `wind_noise_hours = 3`, `gust_period_seconds = 7` (real seconds), `gust_amount = 0.25`, `reference_wind_ms = 4.6`, `max_wind_strength = 4`, `drift_refill_cm_per_game_hour = 2.0`. Fronts `front_mean_interval_hours = 30`, `front_duration_hours = (4, 12)`, `front_wind_ms = (12, 18)`, `front_intensity_mult = (2, 4)`, `front_swing_deg = (30, 90)`. Atmosphere `drive_atmosphere = true`, `atmosphere_smoothing_seconds = 4`. Debug overrides `override_intensity_cm_per_h`, `override_wind_ms`, `override_wind_from_deg` (−1 = off).

**Month table** `const MONTHS` with name, days, snowfall_cm, snow_days, wind_ms, dir_sigma (20° Dec–Feb, 25° Nov/Mar, 35° Apr) from the climate table above.

**Public state**: `game_hours`, `clock_hour`, `day`, `snow_intensity_cm_per_h`, `is_snowing`, `wind_speed_ms`, `wind_from_deg`, `wind_field_deg`, `wind_vector` (world, toward), `wind_strength`, `state: WeatherState {CALM, LIGHT, MODERATE, HEAVY, BLIZZARD}`, `front_active`, `force_blizzard`, `seed_used`. Methods `state_name()`, `reroll_wind()`, `set_force_blizzard(on)`, `get_month_data()`, static compass helpers from the convention section. Signals `state_changed(state)`, `front_started`, `front_ended`.

**Clock**: `game_hours += delta * time_scale / 3600`. Noise sources: five `FastNoiseLite` (SIMPLEX_SMOOTH, FBM, 3 octaves) seeded from `seed_used + k`, sampled with `get_noise_1d(game_hours / period)`; gusts alone sample `real_seconds / gust_period_seconds` so they feel right in real time.

**Snowfall intensity with startup calibration**:
```
u(t)   = noise_int(t / intensity_noise_hours) * 0.5 + 0.5
raw(t) = pow(clamp((u - thr) / (1 - thr), 0, 1), intensity_shape_power)
```
`_calibrate()` at `_ready` (and on reroll/month change): sample `u` every 0.25 h over 1500 game-hours; `thr` = empirical quantile so the snowing fraction equals `q = (snow_days / days) * snowing_hours_per_snow_day / 24` (Jan 0.49); `K = mean_cm_per_h / (mean(raw) * (1 + f_front * (m_front − 1)))` where `f_front = mean(front_duration) / front_mean_interval` and `m_front = mean(front_intensity_mult)`. Per frame: `raw = max(raw, 0.35 * front_env)`; `I = K * raw * flutter * lerp(1, front_int_mult, front_env)`; overrides apply last; `is_snowing = I > 0.005`.

**Fronts (blizzard episodes)**: exponential inter-arrival (`−log(rand) * front_mean_interval_hours`); on start roll duration, peak wind, intensity multiplier and a direction swing (± from `front_swing_deg`, clockwise 70% of the time). Envelope rises over 1 h, holds, falls over the last 1.5 h. Direction offset ramps in over 40% of the duration, then relaxes with an 8 h exponential. `force_blizzard` pins the envelope at 1 with maximum wind and multiplier.

**Wind speed**: `base = wind_ms * exp(wind_sigma * noise_wind) / exp_norm` (normalised so the mean equals the month's mean), `× gust`, blended toward `peak_wind` by the front envelope, clamped 0.3..25. `wind_strength = clamp(speed / reference_wind_ms, 0, max_wind_strength)` (4.6 m/s → 1.0, 18.4 → 4.0).

**Wind direction**: session roll `theta0 = prevailing + randfn(0, dir_sigma)` clamped to ±60°; `wind_from_deg = fposmod(theta0 + direction_swing_deg * noise_dir(game_hours / direction_noise_hours + offset) + front_dir, 360)`. Bounded and seed-deterministic, so no integrator stability issues. `reroll_wind()` rerolls `theta0` and the noise offset so the change is immediate.

**Push into SnowField** every 0.25 s (finer than `accum_interval`):
```
_field.wind_enabled = true
_field.set_wind(from_bearing_to_field_deg(wind_from_deg), wind_strength)
_field.set_snowfall(snow_intensity_cm_per_h * time_scale / 60.0)      # cm per real minute
_field.base_rate_cm_per_min = drift_refill_cm_per_game_hour * time_scale / 60.0
```
Sanity: Jan mean 0.173 cm/h → 0.17 cm per real minute; a 3 cm/h band adds 3 cm per real minute. Also calls `SnowPrecipitation.set_conditions(I, wind_vector, wind_speed_ms)`.

**State classification**: BLIZZARD if snowing and wind ≥ 12 m/s, or I ≥ 2.5; HEAVY I ≥ 1.0; MODERATE I ≥ 0.3; LIGHT if snowing; else CALM.

---

## 3. Visible snowfall: new `scripts/snow/snow_precipitation.gd` (`SnowPrecipitation extends Node3D`)

Built in code in `_ready`; every `_process` it positions itself relative to `get_viewport().get_camera_3d()` (works for the rig camera and the runtime `CombatCamera` from `combat_transition.gd`). `set_conditions(intensity, wind_vector, wind_speed)` from SnowWeather; exports `flake_amount = 3000`, `streak_amount = 800`, `streak_wind_threshold_ms = 8`, `flake_fall_speed = 1.3`, plus `debug_*` overrides for standalone testing. Both emitters: `layers = 1` (layer 12 is the deform camera), shadows off, `local_coords = false`, generous `visibility_aabb`, never change `amount` at runtime (restarts the system) — drive `amount_ratio` and `emitting`.

- **Flakes**: emission box half-extents ~(12, 7, 12); `lifetime = box_height / flake_fall_speed`; `direction`/`initial_velocity` from `wind_vector * wind_speed * 0.9 + DOWN * flake_fall_speed` each push, `spread 8`, small damping, `turbulence_enabled` (strength 0.6, scale 2, influence 0.05–0.15), `scale 0.6–1.4`, alpha `color_ramp` fading in/out over the first and last 10% of life. `draw_pass_1` = 5 cm QuadMesh, `transform_align = TRANSFORM_ALIGN_Z_BILLBOARD`. StandardMaterial3D: unshaded, alpha blend, `billboard_mode = DISABLED` (alignment comes from transform_align), `vertex_color_use_as_albedo`, cull disabled, fog enabled (distant flakes fade into fog), albedo texture = procedural `GradientTexture2D` radial soft dot (no asset). Box centre = camera + forward × 8 + up × 5 − wind_flat × min(speed × 1.5, 18) so density stays near the camera upwind. `amount_ratio = clamp(sqrt(I / 3), 0.08, 1)`, `emitting = is_snowing`.
- **Streaks (ground blizzard)**: box half-extents (15, 0.75, 15) at `snow_surface_y + 0.75` under the camera, shifted 10 m upwind; `direction = wind_flat`, velocity ≈ 1.2 × wind speed, no gravity, light turbulence, alpha 0.35 with the same fade ramp. `draw_pass_1` = QuadMesh (0.04 × 0.9), `transform_align = TRANSFORM_ALIGN_Z_BILLBOARD_Y_TO_VELOCITY` (camera-facing streak stretched along velocity). Fallback if it misbehaves: `TRANSFORM_ALIGN_Y_TO_VELOCITY` + thin BoxMesh. `emitting = wind_speed > streak_wind_threshold_ms`, `amount_ratio = clamp((speed − 8) / 8, 0.1, 1)`.

---

## 4. Atmosphere (driven by SnowWeather)

On `_ready` capture baselines from `WorldEnvironment.environment`, its `ProceduralSkyMaterial` and `Sun` (fog_*, ambient_light_energy, sky_energy_multiplier, sky colours, light_energy, shadow_opacity); restore them in `_exit_tree`. Continuous severity `s ∈ [0, 4]` from intensity (0.3 / 1.0 / 2.5 / 5.0 breakpoints), raised by wind (`max(s, 2 + 2·smoothstep(8, 16, wind))` while snowing; wind-only haze floor `1.5·smoothstep(8, 16, wind)` otherwise). Lerp between adjacent rows:

| s | fog_density | fog_light_color | fog_sky_affect | sun × | shadow_opacity | sky_energy × | ambient × | height-fog density |
|---|---|---|---|---|---|---|---|---|
| 0 calm | 0.0025 | (0.72, 0.78, 0.88) | 0.10 | 1.00 | 1.0 | 1.00 | 1.00 | 0 |
| 1 light | 0.006 | (0.76, 0.80, 0.87) | 0.35 | 0.80 | 0.8 | 0.90 | 1.00 | 0 |
| 2 moderate | 0.015 | (0.79, 0.82, 0.86) | 0.60 | 0.55 | 0.5 | 0.75 | 1.10 | 0.02 |
| 3 heavy | 0.030 | (0.82, 0.84, 0.86) | 0.85 | 0.35 | 0.3 | 0.60 | 1.25 | 0.05 |
| 4 blizzard | 0.060 | (0.85, 0.86, 0.87) | 1.00 | 0.22 | 0.15 | 0.50 | 1.35 | 0.10 |

Exponential fog (1/e visibility ≈ 400 / 170 / 67 / 33 / 17 m). Ambient rises while the sun dims: a whiteout is bright and flat. Sky colours lerp toward `fog_light_color` by `s/4`. Height fog at `ground + 1.5 m` gives the low blowing-snow layer; drop it if it fights the streaks. Update at 10 Hz with exponential smoothing; write sky-material properties only when the change exceeds 0.002 (each write re-renders the radiance map). Nothing volumetric (Mobile renderer).

---

## 5. Debug overlay (`scripts/snow/snow_debug_overlay.gd`)

Bind `_weather` via group `"snow_weather"` (null-safe). Add `"DynShelter"` channel and feed `shelter_tex`. Extend the label with: `clock day N HH:MM | <state> | snow X.XX cm/h | wind X.X m/s from DDD | front on/off | seed`. Hotkeys: `F7` toggles `force_blizzard`, `F8` calls `reroll_wind()`; both `set_input_as_handled()` and print a `[weather]` line.

## 6. Scene wiring (Fennara `run_scene_edit_script` on `res://scenes/world/test_world.tscn`, never hand-edit the .tscn)

Add `SnowWeather` (Node, script `snow_weather.gd`, `field_path ../SnowField`, `environment_path ../WorldEnvironment`, `sun_path ../Sun`, `precipitation_path ../SnowPrecipitation`) and `SnowPrecipitation` (Node3D, script `snow_precipitation.gd`) after `SnowField`, keeping `SnowDebugOverlay` last; `ctx.own()` both. No SnowField export defaults must change: `wind_direction_deg`, `wind_strength`, `snowfall`, `snowfall_cm_per_min`, `base_rate_cm_per_min` are overwritten by the weather every 0.25 s; `use_dynamic_shelter` defaults true in script; the authored PNG alpha stays as the fallback. Write scripts with `write_or_update_file` (diagnostics run automatically) and fix any errors before continuing.

## 7. Docs

- Save this plan as `plans/WEATHERPLAN.md` (project convention alongside `SNOWPLAN.md`).
- Update `plans/SNOWPLAN-NOTES.md`: new files in the Files table; a "Weather" section covering the time scale, month table + sources + the 12 h assumption, calibration method, front model, compass convention paragraph (incl. "old 35° = from WNW 305°"), shelter-pass design and why the baked alpha is now a fallback, atmosphere table; controls line adds F7/F8; the Verified table gains the rows below.

## Verification (Fennara `runtime_session` + `runtime_script`; save probes under `res://.fennara/scripts/runtime/`)

1. **Weather → field mapping**: read `SnowWeather` state and confirm `fposmod(SnowField.wind_direction_deg − 90, 360) == wind_from_deg` (±0.01) and `snowfall_cm_per_min == intensity × time_scale / 60`; calibration prints `thr` and `K`.
2. **Direction wanders**: sample `wind_direction_deg` every 60 real s (1 game-hour) × 5; expect drift of several degrees; `reroll_wind()` / F8 (via `Input.parse_input_event`) jumps it.
3. **Shelter pass is directional**: with `override_wind_from_deg = 270` (toward +X) read `get_shelter_texture().get_image()` at `world_to_uv(Vector3(-8, 0, 0))` (lee of Obstacle6 at x = −10) ≈ 0.95–1.0, at `(-12.5, 0, 0)` ≈ 0.12–0.3, at `(-10, 0, 0)` = 0; set 90 and the two values swap; `shelter_pass_count` increments once per change and not for a 1° change.
4. **Accumulation matches the data**: `override_wind_ms = 0`, `override_intensity_cm_per_h = 3.0`, sample state `.r` at an open spot (e.g. `(30, 0, −30)`), wait 30 real s, expect +0.015 m ±10%. Repeat with `override_wind_ms = 9.2` and confirm the extra drift term (≈ +0.39 cm from `base_rate × 2 × 0.55 × 0.35 × 0.5`) and the `mix(1, 0.55, 0.7)` snowfall reduction, within 15%.
5. **Visuals** (`ctx.capture`): (a) light snow from the rig camera, (b) F7 blizzard at a low angle showing flakes, streaks, fog and dimmed sun, (c) T into tactical combat with snow visible from the CombatCamera, (d) F3 overlay on the DynShelter channel with the weather line. Compare (a)/(b).
6. **Performance**: 300-frame average with `force_blizzard` on vs off; stay within ~1 ms of the existing 6.06 ms; halve `flake_amount` if not.
7. **Regression**: existing trail/compaction-floor probes (`.fennara/tmp/runtime_scripts/script_3689672422.gd`, `script_3724472336.gd`) still pass with fog enabled; F5/F6 save/load unaffected (weather state is not persisted, by design).

## Risks and notes

- `base_rate_cm_per_min` (drift refill) now comes from `drift_refill_cm_per_game_hour` so it scales with `time_scale`; `erosion_rate` and `freshness_recovery_seconds` stay real-time visual tunables (document).
- Linear `speed / 4.6` → `wind_strength` is agreed; real drift transport grows ~speed³, so a `pow(x, 1.5)` knob is a later one-liner.
- Fronts are stochastic, so a given session's total can sit ±20% off the monthly mean; the calibration corrects the expectation only.
- Obstacle collection is name-based; the skip list must include `TacticalCombat` and the new nodes.
- `transform_align` requires `billboard_mode = DISABLED` on the material or the two fight; check on the first capture.
- Confirm `Light3D.shadow_opacity` with `get_class_info(["DirectionalLight3D"])` before use; `angle_difference()` exists in 4.7.
- Top-down CombatCamera reads thinner snowfall; tune the box offset after capture (c).

## Files

- Modify: `scripts/snow/snow_field.gd`, `shaders/snow_sim.gdshader`, `scripts/snow/snow_maps_baker.gd`, `scripts/snow/snow_debug_overlay.gd`, `shaders/snow_debug.gdshader`, `plans/SNOWPLAN-NOTES.md`, `scenes/world/test_world.tscn` (via Fennara).
- Create: `scripts/snow/snow_weather.gd`, `scripts/snow/snow_precipitation.gd`, `shaders/snow_shelter.gdshader`, `plans/WEATHERPLAN.md`.
