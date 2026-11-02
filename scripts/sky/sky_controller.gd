extends Node
class_name SkyController
## Day/night sky, sun, moon and clouds (see plans/SKYPLAN.md section 2).
##
## Single-writer rule: after this node exists, nothing else in the project sets
## properties on the Sun, the Moon, the WorldEnvironment or the sky material.
## SnowWeather computes severity and publishes an atmosphere row; this node
## decides what the scene actually looks like.
##
## It reads the clock from SnowWeather rather than owning one. With no weather
## in the scene (the tactical test, for instance) it falls back to the exported
## fallback_* values and a neutral atmosphere, so the same trio of nodes lights
## every scene the same way.
##
## Everything is keyed off the sun's elevation, not off the hour, because a
## December noon at 45.2N sits at 21 degrees and should look like it.

const SKY_GROUP := "sky"

## Every sky-material write can re-render the radiance map, so skip changes
## smaller than this. Same rule and same number as SnowWeather.
const SKY_WRITE_EPSILON := 0.002
const UPDATE_INTERVAL := 0.1

## Look-up table keyed by sun elevation in degrees. Adjacent rows are lerped,
## exactly like SnowWeather.ATMOSPHERE. The el = 45 row reproduces the scene's
## authored daytime values, so a clear noon is the reference the rest hangs off.
## The two night rows carry the -6 sun colour so the lerp stays continuous;
## their sun energy is 0, so the colour is never seen.
const ELEVATION := [
	{"el": -18.0, "sun_color": Color(1.00, 0.45, 0.25), "sun_energy": 0.00,
		"zenith": Color(0.010, 0.014, 0.035), "horizon": Color(0.020, 0.028, 0.060),
		"ground": Color(0.010, 0.010, 0.015), "ambient_color": Color(0.07, 0.09, 0.16),
		"ambient_energy": 0.12, "star_alpha": 1.0},
	{"el": -12.0, "sun_color": Color(1.00, 0.45, 0.25), "sun_energy": 0.00,
		"zenith": Color(0.015, 0.025, 0.070), "horizon": Color(0.060, 0.070, 0.140),
		"ground": Color(0.015, 0.015, 0.025), "ambient_color": Color(0.09, 0.11, 0.20),
		"ambient_energy": 0.14, "star_alpha": 0.9},
	{"el": -6.0, "sun_color": Color(1.00, 0.45, 0.25), "sun_energy": 0.00,
		"zenith": Color(0.06, 0.09, 0.22), "horizon": Color(0.55, 0.32, 0.22),
		"ground": Color(0.05, 0.04, 0.05), "ambient_color": Color(0.16, 0.16, 0.26),
		"ambient_energy": 0.20, "star_alpha": 0.5},
	{"el": 0.0, "sun_color": Color(1.00, 0.55, 0.30), "sun_energy": 0.30,
		"zenith": Color(0.20, 0.32, 0.55), "horizon": Color(0.95, 0.55, 0.30),
		"ground": Color(0.20, 0.16, 0.14), "ambient_color": Color(0.35, 0.30, 0.34),
		"ambient_energy": 0.28, "star_alpha": 0.1},
	{"el": 5.0, "sun_color": Color(1.00, 0.75, 0.50), "sun_energy": 0.80,
		"zenith": Color(0.28, 0.45, 0.72), "horizon": Color(0.85, 0.72, 0.60),
		"ground": Color(0.30, 0.26, 0.24), "ambient_color": Color(0.45, 0.47, 0.55),
		"ambient_energy": 0.34, "star_alpha": 0.0},
	{"el": 15.0, "sun_color": Color(1.00, 0.92, 0.82), "sun_energy": 1.10,
		"zenith": Color(0.33, 0.52, 0.80), "horizon": Color(0.68, 0.76, 0.86),
		"ground": Color(0.15, 0.12, 0.10), "ambient_color": Color(0.52, 0.58, 0.70),
		"ambient_energy": 0.40, "star_alpha": 0.0},
	{"el": 45.0, "sun_color": Color(1.00, 0.95, 0.85), "sun_energy": 1.20,
		"zenith": Color(0.35, 0.55, 0.82), "horizon": Color(0.65, 0.75, 0.85),
		"ground": Color(0.15, 0.12, 0.10), "ambient_color": Color(0.55, 0.62, 0.75),
		"ambient_energy": 0.40, "star_alpha": 0.0},
]

## Luminance of the el = 45 horizon row. fog_tint is normalised against it so a
## clear day reproduces the fog colour the weather table already authored.
const DAY_HORIZON_LUMA := 0.7359

## The sun hands shadow casting to the moon below this elevation; the hysteresis
## band keeps a sun hovering on the boundary from flickering between the two.
const SHADOW_HANDOVER_EL := -4.0
const SHADOW_HYSTERESIS_DEG := 0.5
## Game seconds the outgoing caster takes to fade out (and the incoming to fade
## in). Two game minutes, as specified; scaled into real time by time_scale.
const SHADOW_FADE_GAME_SECONDS := 120.0

## Resolution of the cloud-noise quantile table; see _build_quantiles().
const QUANTILE_STEPS := 128
## Width of each layer's soft edge, as a fraction of the noise's distribution.
const LOW_SOFTNESS := 0.25
const HIGH_SOFTNESS := 0.12
## How much direct sunlight a cloud directly over the sun takes away. Not 1.0:
## at 30 % cover the sun is behind something roughly a third of the time, and a
## harder figure strobes the whole scene as the deck drifts.
const CLOUD_SUN_DIM := 0.6

@export_group("Links")
@export var weather_path: NodePath
@export var environment_path: NodePath
@export var sun_path: NodePath
@export var moon_path: NodePath
## Only used to place the height-fog plane; falls back to SnowField.find_field.
@export var snow_field_path: NodePath

@export_group("Site")
@export var latitude_deg: float = 45.2

@export_group("Fallback clock")
## Used only when the scene has no SnowWeather.
@export_range(0.0, 24.0, 0.25) var fallback_hour: float = 10.5
@export_enum("November", "December", "January", "February", "March", "April")
var fallback_month: int = 2
@export var fallback_day: int = 15
## Without a weather node there is no wind, and a cloud deck that never moves
## parks whatever happens to be over the sun there for the whole session.
@export var fallback_wind_ms: float = 4.0
@export_range(0.0, 360.0, 1.0) var fallback_wind_from_deg: float = 270.0

@export_group("Moon")
@export var moon_base_energy: float = 0.18
@export var moon_phase_offset_days: float = 0.0
## Moon shadows are softer than the sun's; this caps their opacity.
@export_range(0.0, 1.0, 0.01) var moon_shadow_opacity: float = 0.5

@export_group("Night")
## Sun + moon + ambient never sum below this, so a new moon under a blizzard at
## midnight is still navigable.
@export var night_floor_energy: float = 0.10

@export_group("Smoothing")
@export var sky_smoothing_seconds: float = 2.0

@export_group("Fog")
## Free multiplier on top of the horizon tint, which is already normalised so
## that a clear day reproduces the weather table's authored fog colour. 1 is
## "exactly what the table says"; the plan's 1/0.736 belongs to its version of
## the formula, which did not divide by the reference horizon.
@export var fog_tint: float = 1.0
## How far the fog colour leans toward the horizon's hue. 0 keeps the weather
## table's grey exactly; 1 would make fog the horizon's colour outright.
@export_range(0.0, 1.0, 0.01) var fog_hue_pickup: float = 0.6

@export_group("Clouds")
@export var cloud_seed: int = 20240117
@export_range(0.0, 1.0, 0.01) var base_cloud_coverage: float = 0.30
@export_range(0.0, 1.0, 0.01) var high_cloud_coverage: float = 0.35
## Metres per tile of the noise texture, per layer, and the layer altitudes.
@export var low_tile_size: float = 8000.0
@export var low_height: float = 1500.0
@export var high_tile_size: float = 60000.0
@export var high_height: float = 8000.0
@export var low_wind_scale: float = 1.0
@export var high_wind_scale: float = 0.25
@export var cloud_speed_scale: float = 1.0
## Moving cloud shadows on the snow ground (SKYPLAN task H2). Measured at
## +0.013 ms of GPU time, against a 0.3 ms gate, so it is on.
@export var cloud_shadows_enabled: bool = true
@export_range(0.0, 1.0, 0.01) var cloud_shadow_strength: float = 0.5
@export var ground_material_path: NodePath

@export_group("Debug overrides")
## -1 / alpha 0 means off. Applied last, so a probe or the menu pins exactly
## what it asks for. Same convention as SnowWeather.
@export var override_hour: float = -1.0
@export var override_sun_energy: float = -1.0
@export var override_moon_energy: float = -1.0
@export var override_ambient_energy: float = -1.0
@export var override_ambient_color: Color = Color(0, 0, 0, 0)
@export var override_cloud_coverage: float = -1.0
@export var override_cloud_speed: float = -1.0
@export var override_fog_density: float = -1.0
@export var weather_affects_sky: bool = true
@export var stars_enabled: bool = true
@export var clouds_enabled: bool = true

# --- public read-only state ---------------------------------------------------

var sun_elevation_deg: float = 0.0
var sun_azimuth_deg: float = 0.0
var sun_direction: Vector3 = Vector3.UP
var moon_elevation_deg: float = 0.0
var moon_direction: Vector3 = Vector3.DOWN
var moon_phase: float = 0.0
var moon_illum: float = 0.0
var effective_hour: float = 0.0
var effective_month: int = 2
var effective_day: int = 1
var cloud_coverage: float = 0.0
var high_cloud_now: float = 0.0
var sun_occlusion: float = 0.0
## Counts actual writes to the sky material, for the radiance-churn probe.
var sky_write_count: int = 0
var sky_write_attempts: int = 0

var is_night: bool = false

# --- internals ----------------------------------------------------------------

var _weather: SnowWeather = null
var _environment: Environment = null
var _sky: Sky = null
var _sky_material: ShaderMaterial = null
var _sun: DirectionalLight3D = null
var _moon: DirectionalLight3D = null
var _field: SnowField = null
var _ground_material: ShaderMaterial = null

var _cloud_image: Image = null
var _cloud_texture: ImageTexture = null
## Inverse CDF of the cloud noise, QUANTILE_STEPS + 1 points.
var _quantiles := PackedFloat32Array()
var _noise_high := FastNoiseLite.new()

var _low_scroll := Vector2.ZERO
var _high_scroll := Vector2.ZERO

var _sky_params: Dictionary = {}
var _tick: float = 0.0
var _primed: bool = false

## 0 = the sun owns shadows, 1 = the moon does. Fades through zero, so the two
## are never enabled at the same time.
var _shadow_owner: int = 0
var _shadow_fade: float = 1.0

# Smoothed scene values. Everything the scene sees passes through these.
var _sun_color := Color(1, 0.95, 0.85)
var _sun_energy: float = 1.2
## Sunlight before cloud occlusion, used to light the clouds themselves.
var _daylight: float = 1.2
var _moon_energy: float = 0.0
var _ambient_color := Color(0.55, 0.62, 0.75)
var _ambient_energy: float = 0.4
var _zenith := Color(0.35, 0.55, 0.82)
var _horizon := Color(0.65, 0.75, 0.85)
var _ground := Color(0.15, 0.12, 0.10)
var _fog_color := Color(0.72, 0.78, 0.88)
var _fog_density: float = 0.0025
var _fog_height_density: float = 0.0
var _fog_blend: float = 0.1
var _sky_energy: float = 1.0
var _star_alpha: float = 0.0
var _shadow_mult: float = 1.0
var _low_coverage: float = 0.30
var _high_coverage: float = 0.35


func _ready() -> void:
	add_to_group(SKY_GROUP)
	_resolve_links()
	_build_cloud_noise()
	_noise_high.seed = cloud_seed + 7919
	_noise_high.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	_noise_high.fractal_type = FastNoiseLite.FRACTAL_FBM
	_noise_high.fractal_octaves = 2
	_noise_high.frequency = 1.0
	if _environment:
		_environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
		# ambient_light_color/energy are ignored while the sky contributes
		# everything, and the debug menu needs an explicit colour fill.
		_environment.ambient_light_sky_contribution = 0.0
		_environment.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	_update_time()
	_update_solar()
	_apply(UPDATE_INTERVAL)


func _resolve_links() -> void:
	_weather = get_node_or_null(weather_path) as SnowWeather
	if _weather == null:
		_weather = get_tree().get_first_node_in_group(SnowWeather.WEATHER_GROUP) as SnowWeather

	_sun = get_node_or_null(sun_path) as DirectionalLight3D
	_moon = get_node_or_null(moon_path) as DirectionalLight3D

	_field = get_node_or_null(snow_field_path) as SnowField
	if _field == null:
		_field = SnowField.find_field(self)

	var we := get_node_or_null(environment_path) as WorldEnvironment
	if we and we.environment:
		_environment = we.environment
		_sky = _environment.sky
		if _sky:
			_sky_material = _sky.sky_material as ShaderMaterial
			if _sky_material == null:
				push_warning("SkyController: Sky has no ShaderMaterial; sky uniforms are inert.")

	var gm := get_node_or_null(ground_material_path)
	if gm is GeometryInstance3D:
		_ground_material = (gm as GeometryInstance3D).get_active_material(0) as ShaderMaterial


## Seamless FBM, generated once. Kept as an Image as well as a texture so the
## sun-occlusion sample can be taken on the CPU without a GPU readback.
func _build_cloud_noise() -> void:
	var noise := FastNoiseLite.new()
	noise.seed = cloud_seed
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	noise.fractal_type = FastNoiseLite.FRACTAL_FBM
	noise.fractal_octaves = 4
	noise.frequency = 0.012
	_cloud_image = noise.get_seamless_image(512, 512)
	if _cloud_image == null:
		push_warning("SkyController: cloud noise generation failed; clouds disabled.")
		return
	var tex_image := Image.create_from_data(
		_cloud_image.get_width(), _cloud_image.get_height(), false,
		_cloud_image.get_format(), _cloud_image.get_data())
	tex_image.generate_mipmaps()
	_cloud_texture = ImageTexture.create_from_image(tex_image)
	_build_quantiles()


## An FBM's values pile up around its median, so thresholding at 1 - coverage
## covers almost none of the sky: measured, coverage 0.30 came out at 2 % cover.
## Sampling the noise's own distribution once and inverting it makes "coverage"
## mean what it says. Same empirical-quantile trick SnowWeather._calibrate uses,
## and for the same reason: it does not depend on the noise's exact shape.
func _build_quantiles() -> void:
	var w := _cloud_image.get_width()
	var h := _cloud_image.get_height()
	var stride := 4
	var samples := PackedFloat32Array()
	samples.resize(int(ceil(float(w) / float(stride))) * int(ceil(float(h) / float(stride))))
	var i := 0
	for y in range(0, h, stride):
		for x in range(0, w, stride):
			samples[i] = _cloud_image.get_pixel(x, y).r
			i += 1
	samples.sort()

	_quantiles.resize(QUANTILE_STEPS + 1)
	for q in QUANTILE_STEPS + 1:
		var idx := clampi(int(round(float(q) / float(QUANTILE_STEPS) * float(samples.size() - 1))),
			0, samples.size() - 1)
		_quantiles[q] = samples[idx]


## Noise value below which fraction `f` of the sky sits.
func _quantile(f: float) -> float:
	if _quantiles.is_empty():
		return 1.0 - f
	var x := clampf(f, 0.0, 1.0) * float(QUANTILE_STEPS)
	var lo := int(floor(x))
	var hi := mini(lo + 1, QUANTILE_STEPS)
	return lerpf(_quantiles[lo], _quantiles[hi], x - float(lo))


## The pair of noise values the shader smoothsteps between, placed so that
## exactly `coverage` of the sky ends up at half density or more.
func _cloud_edges(coverage: float, softness: float) -> Vector2:
	if coverage <= 0.0:
		return Vector2(1.0, 1.0)
	var lo := _quantile(1.0 - coverage - softness * 0.5)
	var hi := _quantile(1.0 - coverage + softness * 0.5)
	return Vector2(lo, maxf(hi, lo + 1e-4))


# --- public API ---------------------------------------------------------------

func sunrise_hour() -> float:
	return Solar.sunrise_hour(effective_month, effective_day, latitude_deg)


func sunset_hour() -> float:
	return Solar.sunset_hour(effective_month, effective_day, latitude_deg)


## Clears every debug override back to "off".
func clear_overrides() -> void:
	override_hour = -1.0
	override_sun_energy = -1.0
	override_moon_energy = -1.0
	override_ambient_energy = -1.0
	override_ambient_color = Color(0, 0, 0, 0)
	override_cloud_coverage = -1.0
	override_cloud_speed = -1.0
	override_fog_density = -1.0


func has_overrides() -> bool:
	return override_hour >= 0.0 or override_sun_energy >= 0.0 \
		or override_moon_energy >= 0.0 or override_ambient_energy >= 0.0 \
		or override_ambient_color.a > 0.0 or override_cloud_coverage >= 0.0 \
		or override_cloud_speed >= 0.0 or override_fog_density >= 0.0


## Live values the debug menu shows while the matching override is off.
func live_sun_energy() -> float:
	return _sun_energy


func live_moon_energy() -> float:
	return _moon_energy


func live_ambient_energy() -> float:
	return _ambient_energy


func live_ambient_color() -> Color:
	return _ambient_color


func live_fog_density() -> float:
	return _fog_density


# --- per-frame ----------------------------------------------------------------

func _process(delta: float) -> void:
	_update_time()
	_update_solar()
	_update_scroll(delta)
	_update_shadow_owner(delta)

	_tick += delta
	if _tick >= UPDATE_INTERVAL:
		_apply(_tick)
		_tick = 0.0


func _update_time() -> void:
	if _weather:
		effective_month = _weather.month
		effective_day = _weather.day
		effective_hour = _weather.clock_hour
	else:
		effective_month = fallback_month
		effective_day = maxi(fallback_day, 1)
		effective_hour = fallback_hour
	if override_hour >= 0.0:
		effective_hour = fposmod(override_hour, 24.0)


func _update_solar() -> void:
	var sun_h := Solar.sun_horizontal(effective_month, effective_day, effective_hour, latitude_deg)
	sun_elevation_deg = sun_h.x
	sun_azimuth_deg = sun_h.y
	sun_direction = Solar.horizontal_to_world(sun_h.x, sun_h.y)
	is_night = sun_elevation_deg < 0.0

	var moon_h := Solar.moon_horizontal(effective_month, effective_day, effective_hour,
		moon_phase_offset_days, latitude_deg)
	moon_elevation_deg = moon_h.x
	moon_direction = Solar.horizontal_to_world(moon_h.x, moon_h.y)
	moon_phase = Solar.moon_phase(effective_day, moon_phase_offset_days)
	moon_illum = Solar.moon_illum(effective_day, moon_phase_offset_days)

	# Orientation is a basis assignment, so it runs at full rate: cheap, and it
	# keeps the shadow cascade from stepping at 10 Hz.
	if _sun:
		_sun.global_basis = Solar.light_basis(sun_direction)
	if _moon:
		_moon.global_basis = Solar.light_basis(moon_direction)


## Cloud scroll is accumulated on the CPU from the live wind, so a wind reroll
## turns the field instead of teleporting it, and the shader needs no TIME.
func _update_scroll(delta: float) -> void:
	var wind := Vector2.ZERO
	var scale := 60.0
	if _weather:
		wind = Vector2(_weather.wind_vector.x, _weather.wind_vector.z) * _weather.wind_speed_ms
		scale = _weather.time_scale
	else:
		# Same compass helpers the weather uses, so a scene without weather
		# drifts its clouds the same way a scene with weather does.
		var toward := SnowWeather.field_deg_to_world(
			SnowWeather.from_bearing_to_field_deg(fallback_wind_from_deg))
		wind = Vector2(toward.x, toward.z) * fallback_wind_ms
	var speed := cloud_speed_scale
	if override_cloud_speed >= 0.0:
		speed = override_cloud_speed
	# Metres of cloud travel this frame, in game time. The sheet moves with the
	# wind, so the UV that samples it moves the other way.
	var travel := wind * delta * scale * speed
	_low_scroll -= travel * low_wind_scale / maxf(low_tile_size, 1.0)
	_high_scroll -= travel * high_wind_scale / maxf(high_tile_size, 1.0)
	# Keep the accumulator small; the texture repeats, so this is invisible.
	_low_scroll = Vector2(fposmod(_low_scroll.x, 1.0), fposmod(_low_scroll.y, 1.0))
	_high_scroll = Vector2(fposmod(_high_scroll.x, 1.0), fposmod(_high_scroll.y, 1.0))


## Cross-fades shadow casting between the two lights *through zero*, so exactly
## one of them ever has shadow_enabled set.
func _update_shadow_owner(delta: float) -> void:
	var want := _shadow_owner
	if sun_elevation_deg < SHADOW_HANDOVER_EL - SHADOW_HYSTERESIS_DEG:
		want = 1
	elif sun_elevation_deg > SHADOW_HANDOVER_EL + SHADOW_HYSTERESIS_DEG:
		want = 0

	var scale := _weather.time_scale if _weather else 60.0
	var fade_seconds := SHADOW_FADE_GAME_SECONDS / maxf(scale, 1.0)
	var step := delta / maxf(fade_seconds, 0.05)

	if want != _shadow_owner:
		_shadow_fade = maxf(_shadow_fade - step, 0.0)
		if _shadow_fade <= 0.0:
			_shadow_owner = want
	else:
		_shadow_fade = minf(_shadow_fade + step, 1.0)


# --- table ---------------------------------------------------------------------

func _elevation_row(el: float) -> Dictionary:
	var last: int = ELEVATION.size() - 1
	if el <= float(ELEVATION[0]["el"]):
		return ELEVATION[0]
	if el >= float(ELEVATION[last]["el"]):
		return ELEVATION[last]
	var i := 0
	while i < last and el > float(ELEVATION[i + 1]["el"]):
		i += 1
	var a: Dictionary = ELEVATION[i]
	var b: Dictionary = ELEVATION[i + 1]
	var lo := float(a["el"])
	var hi := float(b["el"])
	var t := clampf((el - lo) / maxf(hi - lo, 1e-6), 0.0, 1.0)
	var out: Dictionary = {}
	for key: String in a.keys():
		if a[key] is Color:
			out[key] = (a[key] as Color).lerp(b[key] as Color, t)
		else:
			out[key] = lerpf(float(a[key]), float(b[key]), t)
	return out


func _weather_row() -> Dictionary:
	if _weather and weather_affects_sky:
		return _weather.atmosphere_row()
	return SnowWeather.ATMOSPHERE[0]


static func _luma(c: Color) -> float:
	return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b


# --- apply ---------------------------------------------------------------------

func _apply(delta: float) -> void:
	var row := _elevation_row(sun_elevation_deg)
	var w := _weather_row()

	# Sampled from last tick's coverage, which is 0.1 s of lag on a value that
	# takes seconds to move.
	sun_occlusion = _sample_sun_occlusion()

	# --- targets -------------------------------------------------------------
	var t_sun_color: Color = row["sun_color"]
	# The weather's sun multiplier and the cloud layer are two descriptions of
	# the same sky, so take the stronger attenuation rather than both: a cumulus
	# crossing a clear sun dims it, and a blizzard's 0.22 is not dimmed twice.
	var cloud_dim := 1.0 - sun_occlusion * CLOUD_SUN_DIM
	# Hard gate at the horizon. The table's el = 0 row is the colour and energy
	# of sunlight *at* sunset; a directional light left on below the horizon
	# would go on lighting the undersides of things from underground.
	var horizon_gate := smoothstep(-1.0, 0.5, sun_elevation_deg)
	# Daylight before any cloud dimming. The clouds are lit by this, not by the
	# dimmed value -- otherwise a cloud crossing the sun darkens itself.
	var t_daylight := float(row["sun_energy"]) * horizon_gate * float(w["sun"])
	var t_sun_energy := float(row["sun_energy"]) * horizon_gate * minf(float(w["sun"]), cloud_dim)
	if override_sun_energy >= 0.0:
		t_sun_energy = override_sun_energy

	var moon_rise := smoothstep(-2.0, 6.0, moon_elevation_deg)
	var t_moon_energy := moon_base_energy * moon_illum * moon_rise * float(w["sun"])
	if override_moon_energy >= 0.0:
		t_moon_energy = override_moon_energy

	var t_ambient_color: Color = row["ambient_color"]
	if override_ambient_color.a > 0.0:
		t_ambient_color = Color(override_ambient_color.r, override_ambient_color.g,
			override_ambient_color.b)
	var t_ambient_energy := float(row["ambient_energy"]) * float(w["ambient"])
	if override_ambient_energy >= 0.0:
		t_ambient_energy = override_ambient_energy

	# Playability floor: something has to light the ground, and ambient is the
	# only term that still works with both bodies under the horizon.
	var total := t_sun_energy + t_moon_energy + t_ambient_energy
	if total < night_floor_energy:
		t_ambient_energy += night_floor_energy - total

	var t_star_alpha := float(row["star_alpha"]) if stars_enabled else 0.0
	# Overcast hides the stars as surely as daylight does.
	t_star_alpha *= 1.0 - clampf(_low_coverage, 0.0, 1.0) * 0.85

	var t_fog_density := float(w["fog_density"])
	if override_fog_density >= 0.0:
		t_fog_density = override_fog_density

	# --- clouds --------------------------------------------------------------
	var t_low := base_cloud_coverage
	if _weather and weather_affects_sky:
		t_low = _weather.cloud_coverage_target()
	if override_cloud_coverage >= 0.0:
		t_low = override_cloud_coverage
	if not clouds_enabled:
		t_low = 0.0
	var drift := 0.0
	if _weather:
		drift = 0.1 * _noise_high.get_noise_1d(_weather.game_hours / 6.0)
	var t_high := clampf(high_cloud_coverage + drift, 0.0, 1.0) if clouds_enabled else 0.0

	# --- smoothing -----------------------------------------------------------
	var k := 1.0 if not _primed else 1.0 - exp(-delta / maxf(sky_smoothing_seconds, 0.01))
	_primed = true

	_sun_color = _sun_color.lerp(t_sun_color, k)
	_sun_energy = lerpf(_sun_energy, t_sun_energy, k)
	_daylight = lerpf(_daylight, t_daylight, k)
	if override_sun_energy < 0.0:
		# Ceiling, not a multiplier: the smoothing must not be able to leave a
		# light burning under the horizon. At time_scale 3600 the sun crosses
		# the horizon inside one smoothing time constant, and without this it
		# went on lighting the ground at -15 degrees.
		_sun_energy = minf(_sun_energy, 2.0 * horizon_gate)
		_daylight = minf(_daylight, 2.0 * horizon_gate)
	_moon_energy = lerpf(_moon_energy, t_moon_energy, k)
	_ambient_color = _ambient_color.lerp(t_ambient_color, k)
	_ambient_energy = lerpf(_ambient_energy, t_ambient_energy, k)
	_zenith = _zenith.lerp(row["zenith"] as Color, k)
	_horizon = _horizon.lerp(row["horizon"] as Color, k)
	_ground = _ground.lerp(row["ground"] as Color, k)
	_fog_density = lerpf(_fog_density, t_fog_density, k)
	_fog_height_density = lerpf(_fog_height_density, float(w["height_density"]), k)
	_fog_blend = lerpf(_fog_blend, float(w["sky_affect"]), k)
	_sky_energy = lerpf(_sky_energy, float(w["sky_energy"]), k)
	_star_alpha = lerpf(_star_alpha, t_star_alpha, k)
	_shadow_mult = lerpf(_shadow_mult, float(w["shadow"]), k)
	_low_coverage = lerpf(_low_coverage, t_low, k)
	_high_coverage = lerpf(_high_coverage, t_high, k)

	cloud_coverage = _low_coverage
	high_cloud_now = _high_coverage

	_write_lights()
	_write_environment(w)
	_write_sky()
	_write_ground()


## Density of the low layer along the sun's own direction, sampled from the same
## noise the shader uses. Drives both the disc dimming and the sun's energy, so
## a cloud crossing the sun dims the ground on the same frame.
func _sample_sun_occlusion() -> float:
	if _cloud_image == null or not clouds_enabled or sun_direction.y <= 0.05:
		return 0.0
	var uv := Vector2(sun_direction.x, sun_direction.z) / sun_direction.y \
		* (low_height / maxf(low_tile_size, 1.0)) + _low_scroll
	var w := _cloud_image.get_width()
	var h := _cloud_image.get_height()
	var x := int(fposmod(uv.x, 1.0) * float(w)) % w
	var y := int(fposmod(uv.y, 1.0) * float(h)) % h
	var n := _cloud_image.get_pixel(x, y).r
	var edges := _cloud_edges(clampf(_low_coverage, 0.0, 1.0), LOW_SOFTNESS)
	return smoothstep(edges.x, edges.y, n)


func _write_lights() -> void:
	if _sun:
		_sun.light_color = _sun_color
		_sun.light_energy = _sun_energy
		# No point paying for a light that is under the horizon.
		_sun.visible = _sun_energy >= 0.01
		var sun_casts := _shadow_owner == 0
		_sun.shadow_enabled = sun_casts
		if sun_casts:
			_sun.shadow_opacity = clampf(_shadow_mult * _shadow_fade, 0.0, 1.0)
	if _moon:
		_moon.light_energy = _moon_energy
		_moon.visible = _moon_energy >= 0.005
		var moon_casts := _shadow_owner == 1
		_moon.shadow_enabled = moon_casts
		if moon_casts:
			_moon.shadow_opacity = clampf(
				minf(_shadow_mult, moon_shadow_opacity) * _shadow_fade, 0.0, 1.0)


func _write_environment(w: Dictionary) -> void:
	if _environment == null:
		return
	_environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	_environment.ambient_light_sky_contribution = 0.0
	_environment.ambient_light_color = _ambient_color
	_environment.ambient_light_energy = _ambient_energy

	_environment.fog_enabled = true
	_environment.fog_mode = Environment.FOG_MODE_EXPONENTIAL
	_environment.fog_density = _fog_density
	_environment.fog_sky_affect = _fog_blend
	_environment.fog_height_density = _fog_height_density
	if _field:
		# The blowing-snow layer hugs the ground, not the camera.
		_environment.fog_height = _field.global_position.y + 1.5

	_fog_color = _tinted_fog_color(w["fog_color"] as Color)
	_environment.fog_light_color = _fog_color


## The weather owns the fog's *character*, the sky owns its brightness and some
## of its hue. A whiteout at noon stays bright and flat, the same whiteout at
## midnight goes dark, and dawn fog picks up the horizon's warmth.
##
## The tint is normalised against the el = 45 horizon, so a clear day comes out
## at the values the weather table already authored.
func _tinted_fog_color(base: Color) -> Color:
	var luma := _luma(_horizon)
	var scale := clampf(luma / DAY_HORIZON_LUMA * fog_tint, 0.0, 1.2)
	# The horizon renormalised to unit luminance is its hue alone.
	var hue := Color(1, 1, 1)
	if luma > 1e-4:
		hue = Color(_horizon.r / luma, _horizon.g / luma, _horizon.b / luma)
	var mixed := base.lerp(base * hue, fog_hue_pickup) * scale
	return Color(clampf(mixed.r, 0.0, 1.0), clampf(mixed.g, 0.0, 1.0), clampf(mixed.b, 0.0, 1.0))


func _write_sky() -> void:
	if _sky_material == null:
		return
	_set_sky_param(&"zenith_color", _zenith)
	_set_sky_param(&"horizon_color", _horizon)
	_set_sky_param(&"ground_color", _ground)
	_set_sky_param(&"fog_color", _fog_color)
	_set_sky_param(&"fog_blend", _fog_blend)
	_set_sky_param(&"sky_energy", _sky_energy)

	_set_sky_param(&"sun_dir", sun_direction)
	_set_sky_param(&"sun_color", _sun_color)
	# Dawn and dusk only: rises from -8 deg, gone by 15 deg.
	_set_sky_param(&"horizon_glow", smoothstep(-8.0, 3.0, sun_elevation_deg)
		* (1.0 - smoothstep(3.0, 15.0, sun_elevation_deg)))
	_set_sky_param(&"sun_strength", clampf(_daylight / 1.2, 0.0, 1.0))
	_set_sky_param(&"sun_occlusion", sun_occlusion)

	_set_sky_param(&"moon_dir", moon_direction)
	_set_sky_param(&"moon_illum", moon_illum)
	_set_sky_param(&"moon_energy", _moon_energy)

	_set_sky_param(&"star_alpha", _star_alpha)
	_set_sky_param(&"star_twinkle", 1.0 if stars_enabled else 0.0)

	if _cloud_texture and not _sky_params.has(&"cloud_noise"):
		_sky_params[&"cloud_noise"] = _cloud_texture
		_sky_material.set_shader_parameter(&"cloud_noise", _cloud_texture)
	_set_sky_param(&"cloud_amount", 1.0 if clouds_enabled else 0.0)
	_set_sky_param(&"low_edges", _cloud_edges(_low_coverage, LOW_SOFTNESS))
	_set_sky_param(&"high_edges", _cloud_edges(_high_coverage, HIGH_SOFTNESS))
	_set_sky_param(&"low_uv_scale", low_height / maxf(low_tile_size, 1.0))
	_set_sky_param(&"high_uv_scale", high_height / maxf(high_tile_size, 1.0))
	_set_sky_param(&"low_scroll", _low_scroll)
	_set_sky_param(&"high_scroll", _high_scroll)


func _write_ground() -> void:
	if _ground_material == null:
		return
	# Shadows need something to cast them and gaps to cast them through: they
	# fade out with the daylight, and again as the deck closes over, because a
	# total overcast has no discrete shadows -- just the flat dimness the
	# weather row is already applying.
	var strength := 0.0
	if cloud_shadows_enabled:
		strength = cloud_shadow_strength \
			* clampf(_daylight / 1.2, 0.0, 1.0) \
			* (1.0 - smoothstep(0.85, 1.0, _low_coverage))
	_ground_material.set_shader_parameter(&"cloud_shadow_strength", strength)
	if strength <= 0.0:
		return
	if _cloud_texture:
		_ground_material.set_shader_parameter(&"cloud_noise", _cloud_texture)
	_ground_material.set_shader_parameter(&"cloud_tile_size", low_tile_size)
	_ground_material.set_shader_parameter(&"cloud_scroll", _low_scroll)
	_ground_material.set_shader_parameter(&"cloud_edges", _cloud_edges(_low_coverage, LOW_SOFTNESS))


## One gate for every sky-material write. Unchanged values never reach the
## material, because each write can re-render the radiance map.
func _set_sky_param(param: StringName, value: Variant) -> void:
	sky_write_attempts += 1
	if _sky_params.has(param) and _near(_sky_params[param], value):
		return
	_sky_params[param] = value
	_sky_material.set_shader_parameter(param, value)
	sky_write_count += 1


static func _near(a: Variant, b: Variant) -> bool:
	if a is float and b is float:
		return absf(float(a) - float(b)) <= SKY_WRITE_EPSILON
	if a is Color and b is Color:
		var x: Color = a
		var y: Color = b
		return absf(x.r - y.r) <= SKY_WRITE_EPSILON and absf(x.g - y.g) <= SKY_WRITE_EPSILON \
			and absf(x.b - y.b) <= SKY_WRITE_EPSILON
	if a is Vector2 and b is Vector2:
		return (a as Vector2).distance_to(b as Vector2) <= SKY_WRITE_EPSILON
	if a is Vector3 and b is Vector3:
		return (a as Vector3).distance_to(b as Vector3) <= SKY_WRITE_EPSILON
	return a == b
