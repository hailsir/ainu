extends Node
class_name SnowWeather
## Rishiri winter weather model (see plans/WEATHERPLAN.md).
##
## Owns the game clock and drives everything downstream from it:
##   - snowfall intensity, calibrated so a month's simulated total matches the
##     JMA normal for that month,
##   - wind speed and the direction the wind blows from, rolled once per session
##     around the prevailing winter westerly and then left to wander,
##   - blizzard fronts, as stochastic episodes of hard wind and heavy snow,
##   - SnowField's wind and snowfall inputs and SnowPrecipitation's emitters.
##
## It does not touch lights, the environment or the sky. It *publishes* an
## atmosphere row (atmosphere_row(), severity(), cloud_coverage_target()) and
## SkyController is the single node that turns those into what the scene looks
## like. See plans/SKYPLAN.md section 5.
##
## Time runs at time_scale (default 60): one real second is one game minute.
## Rates are authored in cm per *game* hour and converted on the way into the
## field, which wants cm per *real* minute.
##
## Compass convention: world north is -Z and east is +X. wind_from_deg is the
## bearing the wind blows FROM, clockwise from north. SnowField's
## wind_direction_deg is the direction it blows TOWARD, measured from +X toward
## +Z, because that axis pair maps directly onto the field's UV.

const WEATHER_GROUP := "snow_weather"

enum WeatherState { CALM, LIGHT, MODERATE, HEAVY, BLIZZARD }

const STATE_NAMES := ["CALM", "LIGHT", "MODERATE", "HEAVY", "BLIZZARD"]

## JMA 1991-2020 normals. Snowfall and snowy days are Wakkanai (47401), about
## 50 km NE and in the same Sea-of-Japan winter-monsoon regime; Rishiri's own
## Kutsugata AMeDAS has no snow gauge. Wind speed is Kutsugata itself.
## dir_sigma is the spread of the session's opening direction roll: winter
## monsoon months are tightly westerly, April much less so.
const MONTHS := [
	{"name": "November", "days": 30, "snowfall_cm": 41.0, "snow_days": 20.5, "wind_ms": 4.8, "dir_sigma": 25.0},
	{"name": "December", "days": 31, "snowfall_cm": 122.0, "snow_days": 28.5, "wind_ms": 4.8, "dir_sigma": 20.0},
	{"name": "January", "days": 31, "snowfall_cm": 129.0, "snow_days": 30.4, "wind_ms": 4.6, "dir_sigma": 20.0},
	{"name": "February", "days": 28, "snowfall_cm": 105.0, "snow_days": 26.0, "wind_ms": 4.4, "dir_sigma": 20.0},
	{"name": "March", "days": 31, "snowfall_cm": 68.0, "snow_days": 24.3, "wind_ms": 4.2, "dir_sigma": 25.0},
	{"name": "April", "days": 30, "snowfall_cm": 9.0, "snow_days": 12.6, "wind_ms": 3.7, "dir_sigma": 35.0},
]

## Atmosphere response, indexed by severity 0..4. Adjacent rows are lerped.
## Exponential fog, so 1/e visibility runs 400 / 170 / 67 / 33 / 17 m. Ambient
## rises as the sun dims, because a whiteout is bright and flat, not dark.
const ATMOSPHERE := [
	{"fog_density": 0.0025, "fog_color": Color(0.72, 0.78, 0.88), "sky_affect": 0.10,
		"sun": 1.00, "shadow": 1.00, "sky_energy": 1.00, "ambient": 1.00, "height_density": 0.00},
	{"fog_density": 0.0060, "fog_color": Color(0.76, 0.80, 0.87), "sky_affect": 0.35,
		"sun": 0.80, "shadow": 0.80, "sky_energy": 0.90, "ambient": 1.00, "height_density": 0.00},
	{"fog_density": 0.0150, "fog_color": Color(0.79, 0.82, 0.86), "sky_affect": 0.60,
		"sun": 0.55, "shadow": 0.50, "sky_energy": 0.75, "ambient": 1.10, "height_density": 0.02},
	{"fog_density": 0.0300, "fog_color": Color(0.82, 0.84, 0.86), "sky_affect": 0.85,
		"sun": 0.35, "shadow": 0.30, "sky_energy": 0.60, "ambient": 1.25, "height_density": 0.05},
	{"fog_density": 0.0600, "fog_color": Color(0.85, 0.86, 0.87), "sky_affect": 1.00,
		"sun": 0.22, "shadow": 0.15, "sky_energy": 0.50, "ambient": 1.35, "height_density": 0.10},
]

## Intensity, in cm per game-hour, at which each severity row is reached.
const SEVERITY_BREAKPOINTS := [0.0, 0.3, 1.0, 2.5, 5.0]

## Minimum shaped intensity while a front is at full envelope. A front brings
## its own snow even when the slow noise happens to be dry, and _calibrate()
## must use the same number or the monthly total runs high.
const FRONT_RAW_FLOOR := 0.35
## Hours the front envelope spends ramping in and out (1.0 up + 1.5 down), so
## a front does not sit at full strength for its whole duration.
const FRONT_RAMP_HOURS := 2.5

const FIELD_PUSH_INTERVAL := 0.25
const ATMOSPHERE_INTERVAL := 0.1

signal state_changed(state: int)
signal front_started()
signal front_ended()

@export_group("Links")
@export var field_path: NodePath
@export var precipitation_path: NodePath
## Deprecated and unused: SkyController owns the environment and the lights now.
## Kept for one release so scenes and probes that still set them do not break.
@export var environment_path: NodePath
@export var sun_path: NodePath

@export_group("Clock")
## Game seconds per real second. 60 means one real second is one game minute.
@export var time_scale: float = 60.0
@export_range(0.0, 24.0, 0.25) var start_hour: float = 8.0
@export var run_clock: bool = true

@export_group("Climate")
@export_enum("November", "December", "January", "February", "March", "April")
var month: int = 2
## 0 rolls a random seed and prints it, so a session can be reproduced.
@export var weather_seed: int = 0
## Prevailing winter wind blows FROM the west.
@export_range(0.0, 360.0, 1.0) var prevailing_from_deg: float = 270.0
## Amplitude of the slow directional wander around the session's roll.
@export var direction_swing_deg: float = 35.0
@export var direction_noise_hours: float = 10.0
## Hours of actual snowfall on a "snowy day". The one figure here that is not
## from JMA; it sets the duty cycle only, never the monthly total.
@export var snowing_hours_per_snow_day: float = 12.0
@export var intensity_noise_hours: float = 8.0
## Higher makes snowfall spikier: long quiet stretches, sharper bands.
@export var intensity_shape_power: float = 1.8
@export var flutter_period_hours: float = 0.5
@export_range(0.0, 1.0, 0.01) var flutter_amount: float = 0.2
## Log-normal spread of wind speed around the monthly mean.
@export var wind_sigma: float = 0.45
@export var wind_noise_hours: float = 3.0
## Gusts are sampled in real seconds, so they feel right regardless of time_scale.
@export var gust_period_seconds: float = 7.0
@export_range(0.0, 1.0, 0.01) var gust_amount: float = 0.25
## Wind speed that maps to SnowField wind_strength 1.0.
@export var reference_wind_ms: float = 4.6
@export var max_wind_strength: float = 4.0
## Peak drift refill rate fed to SnowField.base_rate_cm_per_min, in cm per
## game-hour so it scales with time_scale like every other rate here.
@export var drift_refill_cm_per_game_hour: float = 2.0

@export_group("Fronts")
@export var front_mean_interval_hours: float = 30.0
@export var front_duration_hours: Vector2 = Vector2(4.0, 12.0)
@export var front_wind_ms: Vector2 = Vector2(12.0, 18.0)
@export var front_intensity_mult: Vector2 = Vector2(2.0, 4.0)
@export var front_swing_deg: Vector2 = Vector2(30.0, 90.0)

@export_group("Atmosphere")
## Publish the atmosphere row. Off freezes the row where it stands; it does not
## restore anything, because nothing here writes to the scene any more.
@export var drive_atmosphere: bool = true
@export var atmosphere_smoothing_seconds: float = 4.0

@export_group("Debug overrides")
## All -1 to disable. Applied after the model, so a probe can pin a condition.
@export var override_intensity_cm_per_h: float = -1.0
@export var override_wind_ms: float = -1.0
@export var override_wind_from_deg: float = -1.0

# --- public state -------------------------------------------------------------

var game_hours: float = 0.0
var clock_hour: float = 0.0
var day: int = 1

var snow_intensity_cm_per_h: float = 0.0
var is_snowing: bool = false
var wind_speed_ms: float = 0.0
var wind_from_deg: float = 270.0
var wind_field_deg: float = 0.0
## World-space unit vector the wind blows toward.
var wind_vector: Vector3 = Vector3(1.0, 0.0, 0.0)
var wind_strength: float = 1.0
var state: WeatherState = WeatherState.CALM
var front_active: bool = false
var force_blizzard: bool = false
var seed_used: int = 0

# --- internals ----------------------------------------------------------------

var _field: SnowField = null
var _precipitation: Node = null

var _rng := RandomNumberGenerator.new()
var _noise_intensity := FastNoiseLite.new()
var _noise_wind := FastNoiseLite.new()
var _noise_direction := FastNoiseLite.new()
var _noise_flutter := FastNoiseLite.new()
var _noise_gust := FastNoiseLite.new()

var _real_seconds: float = 0.0
var _field_timer: float = 0.0
var _atmosphere_timer: float = 0.0

## Calibration outputs (printed at startup so a session can be checked).
var _intensity_threshold: float = 0.5
var _intensity_gain: float = 1.0
var _wind_exp_norm: float = 1.0

var _theta0_deg: float = 270.0
var _dir_noise_offset: float = 0.0

var _front_next_hours: float = 0.0
var _front_start_hours: float = 0.0
var _front_duration: float = 0.0
var _front_peak_wind: float = 0.0
var _front_mult: float = 1.0
var _front_swing: float = 0.0
var _front_envelope: float = 0.0
var _front_dir_offset: float = 0.0
var _front_dir_at_end: float = 0.0
var _front_end_hours: float = -1e9

var _severity: float = 0.0
var _severity_primed: bool = false
## Last published atmosphere row; see atmosphere_row().
var _row: Dictionary = {}


func _ready() -> void:
	add_to_group(WEATHER_GROUP)
	_resolve_links()
	game_hours = start_hour
	_reseed()
	_calibrate()
	_roll_direction()
	_schedule_next_front()
	_evaluate(0.0)
	_push_to_field()


# --- compass helpers ----------------------------------------------------------

## Bearing the wind blows FROM -> SnowField's "blows toward" angle.
## From W (270) gives 0, which blows toward +X (east).
static func from_bearing_to_field_deg(from_deg: float) -> float:
	return fposmod(from_deg + 90.0, 360.0)


## Inverse of from_bearing_to_field_deg(). From N (0) maps to field 90, which
## blows toward +Z (south).
static func field_to_from_bearing_deg(field_deg: float) -> float:
	return fposmod(field_deg - 90.0, 360.0)


## World-space unit vector for a field angle, i.e. the direction wind blows toward.
static func field_deg_to_world(field_deg: float) -> Vector3:
	var a := deg_to_rad(field_deg)
	return Vector3(cos(a), 0.0, sin(a))


# --- public API ---------------------------------------------------------------

func state_name() -> String:
	return STATE_NAMES[int(state)]


func get_month_data() -> Dictionary:
	return MONTHS[clampi(month, 0, MONTHS.size() - 1)]


## Mean deposition for the selected month, in cm per game-hour.
func mean_intensity_cm_per_h() -> float:
	var m := get_month_data()
	return float(m["snowfall_cm"]) / (float(m["days"]) * 24.0)


## Fraction of the month that should be snowing at all.
func target_snowing_fraction() -> float:
	var m := get_month_data()
	var f := (float(m["snow_days"]) / float(m["days"])) * snowing_hours_per_snow_day / 24.0
	return clampf(f, 0.01, 0.99)


## Rolls a fresh session direction. The noise offset moves too, so the wander
## jumps rather than easing back to where it was.
func reroll_wind() -> void:
	_roll_direction()
	_dir_noise_offset = _rng.randf() * 1000.0
	_evaluate(0.0)
	_push_to_field()


func set_force_blizzard(on: bool) -> void:
	force_blizzard = on


## Recalibrates against a different month without restarting the session.
func set_month(index: int) -> void:
	month = clampi(index, 0, MONTHS.size() - 1)
	_calibrate()
	_roll_direction()


## Jumps the clock to an hour of the current day, keeping the day number. This
## is what the debug menu's presets call, so the clock and the sky can never
## disagree about what time it is.
func set_time_of_day(hour: float) -> void:
	game_hours = floor(game_hours / 24.0) * 24.0 + clampf(hour, 0.0, 24.0)
	_evaluate(0.0)
	_push_to_field()


## The smoothed, lerped ATMOSPHERE row. This is the weather's contract with
## SkyController: keys fog_density, fog_color, sky_affect, sun, shadow,
## sky_energy, ambient, height_density.
func atmosphere_row() -> Dictionary:
	if _row.is_empty():
		_row = _atmosphere_row(_severity if _severity_primed else target_severity())
	return _row


## The smoothed severity actually in effect, as opposed to target_severity().
func severity() -> float:
	return _severity if _severity_primed else target_severity()


## Cloud cover the sky should show, 0..1. Severity drives it, and a front adds
## cover ahead of its own snow, because the sky goes over before it starts.
func cloud_coverage_target() -> float:
	var env := 1.0 if force_blizzard else _front_envelope
	return clampf(0.30 + 0.70 * smoothstep(0.0, 3.0, severity()) + 0.15 * env, 0.0, 1.0)


func _resolve_links() -> void:
	_field = get_node_or_null(field_path) as SnowField
	if _field == null:
		_field = SnowField.find_field(self)
	_precipitation = get_node_or_null(precipitation_path)


func _reseed() -> void:
	seed_used = weather_seed if weather_seed != 0 else int(Time.get_unix_time_from_system()) ^ (randi() & 0xFFFF)
	_rng.seed = seed_used
	var sources: Array[FastNoiseLite] = [
		_noise_intensity, _noise_wind, _noise_direction, _noise_flutter, _noise_gust,
	]
	for i in sources.size():
		var n := sources[i]
		n.seed = seed_used + i * 7919
		n.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
		n.fractal_type = FastNoiseLite.FRACTAL_FBM
		n.fractal_octaves = 3
		# Frequency 1 makes the "period" exports mean what they say: callers
		# divide game_hours by the period before sampling.
		n.frequency = 1.0
	print("[weather] seed %d, %s" % [seed_used, get_month_data()["name"]])


# --- calibration --------------------------------------------------------------

## Solves for the two free constants of the intensity model against the month's
## JMA normals: a threshold that reproduces the snowing/not-snowing duty cycle,
## and a gain that reproduces the monthly total. Fronts are accounted for by
## their long-run expectation, so a single stormy session can still run over.
func _calibrate() -> void:
	var samples := 6000
	var step := 0.25
	var raw_u := PackedFloat32Array()
	raw_u.resize(samples)
	var exp_sum := 0.0
	for i in samples:
		var t := float(i) * step
		raw_u[i] = _noise_intensity.get_noise_1d(t / maxf(intensity_noise_hours, 0.01)) * 0.5 + 0.5
		exp_sum += exp(wind_sigma * _noise_wind.get_noise_1d(t / maxf(wind_noise_hours, 0.01)))

	# Empirical quantile, so the model does not depend on the noise's exact
	# distribution the way a closed-form threshold would.
	var sorted := raw_u.duplicate()
	sorted.sort()
	var q := target_snowing_fraction()
	var idx := clampi(int(round((1.0 - q) * float(samples - 1))), 0, samples - 1)
	_intensity_threshold = sorted[idx]

	# Long-run front statistics: how much of the month a front occupies, how
	# hard it snows while one runs, and how much of a front is at full strength.
	var mean_duration := (front_duration_hours.x + front_duration_hours.y) * 0.5
	var f_front := clampf(mean_duration / maxf(front_mean_interval_hours, 0.01), 0.0, 1.0)
	var m_front := (front_intensity_mult.x + front_intensity_mult.y) * 0.5
	var env_mean := clampf(1.0 - FRONT_RAMP_HOURS * 0.5 / maxf(mean_duration, 0.01), 0.0, 1.0)

	# Expected shaped intensity, split between ordinary hours and frontal ones.
	# Averaging the *whole* per-frame expression rather than applying an
	# analytic multiplier is what keeps the FRONT_RAW_FLOOR in the budget: that
	# floor lifts dry hours inside a front to 0.35, which on a month like April
	# is several times the ordinary mean and would otherwise blow the total.
	var calm_sum := 0.0
	var front_sum := 0.0
	for i in samples:
		var r := _shape(raw_u[i])
		calm_sum += r
		front_sum += maxf(r, FRONT_RAW_FLOOR * env_mean) * lerpf(1.0, m_front, env_mean)
	var expected := (1.0 - f_front) * (calm_sum / float(samples)) \
		+ f_front * (front_sum / float(samples))

	_intensity_gain = mean_intensity_cm_per_h() / maxf(expected, 1e-6)
	_wind_exp_norm = maxf(exp_sum / float(samples), 1e-6)

	print("[weather] calibrated %s: thr=%.4f K=%.4f (target %.4f cm/game-h, snowing %.0f%%, fronts %.0f%% of the time)" % [
		get_month_data()["name"], _intensity_threshold, _intensity_gain,
		mean_intensity_cm_per_h(), q * 100.0, f_front * 100.0])


func _shape(u: float) -> float:
	var t := clampf((u - _intensity_threshold) / maxf(1.0 - _intensity_threshold, 1e-6), 0.0, 1.0)
	return pow(t, intensity_shape_power)


func _roll_direction() -> void:
	var sigma := float(get_month_data()["dir_sigma"])
	var offset := clampf(_rng.randfn(0.0, sigma), -60.0, 60.0)
	_theta0_deg = prevailing_from_deg + offset
	print("[weather] wind rolled to from %d deg (%+.0f from prevailing)" % [
		int(fposmod(_theta0_deg, 360.0)), offset])


# --- per-frame ----------------------------------------------------------------

func _process(delta: float) -> void:
	_real_seconds += delta
	if run_clock:
		game_hours += delta * time_scale / 3600.0

	_evaluate(delta)

	_field_timer += delta
	if _field_timer >= FIELD_PUSH_INTERVAL:
		_field_timer = 0.0
		_push_to_field()

	if drive_atmosphere:
		_atmosphere_timer += delta
		if _atmosphere_timer >= ATMOSPHERE_INTERVAL:
			_publish_atmosphere(_atmosphere_timer)
			_atmosphere_timer = 0.0


func _evaluate(_delta: float) -> void:
	clock_hour = fposmod(game_hours, 24.0)
	day = int(floor(game_hours / 24.0)) + 1

	_update_fronts()

	var env := 1.0 if force_blizzard else _front_envelope
	var mult := front_intensity_mult.y if force_blizzard else _front_mult

	# --- snowfall intensity ---
	var u := _noise_intensity.get_noise_1d(game_hours / maxf(intensity_noise_hours, 0.01)) * 0.5 + 0.5
	var raw := _shape(u)
	# A front brings its own snow even if the slow noise happens to be dry.
	raw = maxf(raw, FRONT_RAW_FLOOR * env)
	var flutter := 1.0 + flutter_amount * _noise_flutter.get_noise_1d(game_hours / maxf(flutter_period_hours, 0.01))
	snow_intensity_cm_per_h = _intensity_gain * raw * maxf(flutter, 0.0) * lerpf(1.0, mult, env)

	# --- wind speed ---
	var base := float(get_month_data()["wind_ms"])
	base *= exp(wind_sigma * _noise_wind.get_noise_1d(game_hours / maxf(wind_noise_hours, 0.01))) / _wind_exp_norm
	base *= 1.0 + gust_amount * _noise_gust.get_noise_1d(_real_seconds / maxf(gust_period_seconds, 0.01))
	var peak := front_wind_ms.y if force_blizzard else _front_peak_wind
	wind_speed_ms = clampf(lerpf(base, peak, env), 0.3, 25.0)

	# --- wind direction ---
	var wander := direction_swing_deg * _noise_direction.get_noise_1d(
		game_hours / maxf(direction_noise_hours, 0.01) + _dir_noise_offset)
	wind_from_deg = fposmod(_theta0_deg + wander + _front_dir_offset, 360.0)

	# --- debug overrides, applied last so a probe pins exactly what it asks for ---
	if override_intensity_cm_per_h >= 0.0:
		snow_intensity_cm_per_h = override_intensity_cm_per_h
	if override_wind_ms >= 0.0:
		wind_speed_ms = override_wind_ms
	if override_wind_from_deg >= 0.0:
		wind_from_deg = fposmod(override_wind_from_deg, 360.0)

	is_snowing = snow_intensity_cm_per_h > 0.005
	wind_field_deg = from_bearing_to_field_deg(wind_from_deg)
	wind_vector = field_deg_to_world(wind_field_deg)
	wind_strength = clampf(wind_speed_ms / maxf(reference_wind_ms, 0.01), 0.0, max_wind_strength)

	var next_state := _classify()
	if next_state != state:
		state = next_state
		state_changed.emit(int(state))

	if _precipitation and _precipitation.has_method("set_conditions"):
		_precipitation.call("set_conditions", snow_intensity_cm_per_h, wind_vector, wind_speed_ms)


func _classify() -> WeatherState:
	if not is_snowing:
		return WeatherState.CALM
	if wind_speed_ms >= 12.0 or snow_intensity_cm_per_h >= 2.5:
		return WeatherState.BLIZZARD
	if snow_intensity_cm_per_h >= 1.0:
		return WeatherState.HEAVY
	if snow_intensity_cm_per_h >= 0.3:
		return WeatherState.MODERATE
	return WeatherState.LIGHT


# --- fronts -------------------------------------------------------------------

func _schedule_next_front() -> void:
	# Exponential inter-arrival: fronts are memoryless, so a quiet week and two
	# storms in a day are both possible.
	var gap := -log(maxf(_rng.randf(), 1e-6)) * front_mean_interval_hours
	_front_next_hours = game_hours + gap


func _update_fronts() -> void:
	if not front_active and game_hours >= _front_next_hours:
		_begin_front()

	if front_active:
		var elapsed := game_hours - _front_start_hours
		if elapsed >= _front_duration:
			_end_front()
		else:
			# Rises over 1 h, holds, falls over the last 1.5 h.
			var rise := clampf(elapsed / 1.0, 0.0, 1.0)
			var fall := clampf((_front_duration - elapsed) / 1.5, 0.0, 1.0)
			_front_envelope = minf(rise, fall)
			# The direction swing leads the wind in, then stays for the duration.
			var ramp := clampf(elapsed / maxf(0.4 * _front_duration, 0.01), 0.0, 1.0)
			_front_dir_offset = _front_swing * ramp

	if not front_active:
		_front_envelope = 0.0
		if absf(_front_dir_at_end) > 0.05:
			# The air mass keeps its new direction for a while after the front
			# has passed, relaxing back to the prevailing wander over ~8 h.
			_front_dir_offset = _front_dir_at_end * exp(-(game_hours - _front_end_hours) / 8.0)
			if absf(_front_dir_offset) <= 0.05:
				_front_dir_offset = 0.0
				_front_dir_at_end = 0.0
		else:
			_front_dir_offset = 0.0


func _begin_front() -> void:
	front_active = true
	_front_start_hours = game_hours
	_front_duration = _rng.randf_range(front_duration_hours.x, front_duration_hours.y)
	_front_peak_wind = _rng.randf_range(front_wind_ms.x, front_wind_ms.y)
	_front_mult = _rng.randf_range(front_intensity_mult.x, front_intensity_mult.y)
	# Fronts back the wind clockwise more often than not, as a passing low does.
	var sign_ := 1.0 if _rng.randf() < 0.7 else -1.0
	_front_swing = _rng.randf_range(front_swing_deg.x, front_swing_deg.y) * sign_
	print("[weather] front in: %.1f h, peak %.1f m/s, snow x%.1f, swing %+.0f deg" % [
		_front_duration, _front_peak_wind, _front_mult, _front_swing])
	front_started.emit()


func _end_front() -> void:
	front_active = false
	_front_envelope = 0.0
	_front_dir_at_end = _front_dir_offset
	_front_end_hours = game_hours
	_schedule_next_front()
	print("[weather] front out at day %d %02d:%02d" % [day, int(clock_hour), int(fposmod(clock_hour * 60.0, 60.0))])
	front_ended.emit()


# --- SnowField ----------------------------------------------------------------

func _push_to_field() -> void:
	if _field == null:
		return
	# The field's accumulation only runs while wind_enabled, and that also gates
	# falling snow, so calm weather is expressed as low strength, never as off.
	_field.wind_enabled = true
	_field.set_wind(wind_field_deg, wind_strength)
	# cm per game-hour -> cm per real minute. At time_scale 60 these are equal.
	_field.set_snowfall(snow_intensity_cm_per_h * time_scale / 60.0)
	_field.base_rate_cm_per_min = drift_refill_cm_per_game_hour * time_scale / 60.0


# --- atmosphere ---------------------------------------------------------------

## Continuous 0..4 severity. Intensity sets the baseline; strong wind raises it,
## because blowing snow cuts visibility whether or not much is falling.
func target_severity() -> float:
	var s := 0.0
	for i in range(1, SEVERITY_BREAKPOINTS.size()):
		var lo: float = SEVERITY_BREAKPOINTS[i - 1]
		var hi: float = SEVERITY_BREAKPOINTS[i]
		if snow_intensity_cm_per_h >= hi:
			s = float(i)
		elif snow_intensity_cm_per_h > lo:
			s = float(i - 1) + (snow_intensity_cm_per_h - lo) / (hi - lo)
			break
		else:
			break

	var blow := smoothstep(8.0, 16.0, wind_speed_ms)
	if is_snowing:
		# Floor of 1 the moment anything falls, rising to full whiteout as the
		# wind picks up the surface as well.
		s = maxf(s, 1.0 + 3.0 * blow)
	else:
		s = maxf(s, 1.5 * blow)
	return clampf(s, 0.0, 4.0)


func _atmosphere_row(s: float) -> Dictionary:
	var lo := clampi(int(floor(s)), 0, ATMOSPHERE.size() - 1)
	var hi := clampi(lo + 1, 0, ATMOSPHERE.size() - 1)
	var t := clampf(s - float(lo), 0.0, 1.0)
	var a: Dictionary = ATMOSPHERE[lo]
	var b: Dictionary = ATMOSPHERE[hi]
	var out: Dictionary = {}
	for key: String in a.keys():
		if a[key] is Color:
			out[key] = (a[key] as Color).lerp(b[key] as Color, t)
		else:
			out[key] = lerpf(float(a[key]), float(b[key]), t)
	return out


## Advances the smoothed severity and republishes the row. Nothing is written to
## the scene: SkyController polls atmosphere_row() at its own 10 Hz cadence.
func _publish_atmosphere(delta: float) -> void:
	var target := target_severity()
	if not _severity_primed:
		_severity = target
		_severity_primed = true
	else:
		# Exponential smoothing, so a gust does not strobe the whole sky.
		var k := 1.0 - exp(-delta / maxf(atmosphere_smoothing_seconds, 0.01))
		_severity = lerpf(_severity, target, k)

	_row = _atmosphere_row(_severity)
