extends RefCounted
class_name Solar
## Solar and lunar geometry for Rishiri (see plans/SKYPLAN.md section 1).
##
## Pure static functions: no scene access, no state beyond a per-day cache of
## sunrise/sunset, so a probe can test the whole model without a running world.
##
## Compass convention matches the rest of the project: world north is -Z and
## east is +X. Azimuth is measured clockwise from north, elevation up from the
## horizon, and the returned world vector points FROM the observer TOWARD the
## body.
##
## The model is the standard first-order one (declination from day of year,
## hour angle from local solar time). It carries three corrections the plan's
## sketch left out, because without them December sunrise lands ~40 minutes
## late against the JMA reference the plan asks us to match:
##   - longitude: Rishiri is 141.2E, 6.2 deg east of the 135E JST meridian,
##     which is 25 minutes of solar time, not noise,
##   - the equation of time, worth up to a quarter hour either way,
##   - the -0.833 deg horizon altitude that defines sunrise/sunset.
##
## The moon is a deliberate fake; see moon_horizontal().

const LATITUDE_DEG := 45.2
const LONGITUDE_DEG := 141.2
## JST is UTC+9, so its reference meridian is 135E.
const TIMEZONE_MERIDIAN_DEG := 135.0
const AXIAL_TILT_DEG := 23.44

## Sunrise and sunset happen with the sun's centre this far below the true
## horizon: half the disc (0.267 deg) plus mean atmospheric refraction (0.567).
const HORIZON_ALTITUDE_DEG := -0.833

const SYNODIC_MONTH_DAYS := 29.530588

## November, December, January, February, March, April. Non-leap; the 1840s
## year is cosmetic and no date is ever shown.
const MONTH_START_DOY := [305, 335, 1, 32, 60, 91]

## How far the moon's declination swings against the sun's over a lunation.
## The plan specified a constant -0.8 * decl, which puts a winter full moon
## high (right) but also puts a winter crescent high (wrong -- a crescent hangs
## near the sun, so in winter it must be low). Scaling by cos(2*pi*phase)
## instead costs one cosine and gets both ends right: +decl at new, -decl at
## full. 0.95 keeps a little separation from the sun's exact track.
const MOON_DECLINATION_SWING := 0.95

## Cache of Vector2(sunrise, sunset) keyed by month * 100 + day, filled only
## for the default latitude. The debug menu asks for these every frame.
static var _day_cache: Dictionary = {}


# --- date and time ------------------------------------------------------------

static func day_of_year(month: int, day: int) -> int:
	var m := clampi(month, 0, MONTH_START_DOY.size() - 1)
	return int(MONTH_START_DOY[m]) + maxi(day, 1) - 1


static func declination_deg(doy: int) -> float:
	return AXIAL_TILT_DEG * sin(deg_to_rad(360.0 / 365.0 * float(doy - 81)))


## Minutes by which the sundial runs ahead of the clock.
static func equation_of_time_minutes(doy: int) -> float:
	var b := deg_to_rad(360.0 / 365.0 * float(doy - 81))
	return 9.87 * sin(2.0 * b) - 7.53 * cos(b) - 1.5 * sin(b)


## Hours to add to the game clock to get local solar time.
static func time_correction_hours(doy: int) -> float:
	return (4.0 * (LONGITUDE_DEG - TIMEZONE_MERIDIAN_DEG)
		+ equation_of_time_minutes(doy)) / 60.0


## Degrees of rotation past the local meridian: negative before solar noon.
static func hour_angle_deg(doy: int, hour: float) -> float:
	return 15.0 * (hour + time_correction_hours(doy) - 12.0)


# --- horizontal coordinates ---------------------------------------------------

## Vector2(elevation_deg, azimuth_deg) for a body at this declination and hour
## angle. Azimuth is clockwise from north.
static func horizontal(decl_deg: float, hour_angle: float, lat_deg: float = LATITUDE_DEG) -> Vector2:
	var phi := deg_to_rad(lat_deg)
	var dec := deg_to_rad(decl_deg)
	var h := deg_to_rad(hour_angle)

	var sin_el := clampf(sin(phi) * sin(dec) + cos(phi) * cos(dec) * cos(h), -1.0, 1.0)
	var el := asin(sin_el)

	var denom := cos(el) * cos(phi)
	var az := 180.0
	if absf(denom) > 1e-6:
		az = rad_to_deg(acos(clampf((sin(dec) - sin_el * sin(phi)) / denom, -1.0, 1.0)))
	# acos only spans the eastern half; after solar noon the body is west.
	if fposmod(hour_angle + 180.0, 360.0) - 180.0 > 0.0:
		az = 360.0 - az

	return Vector2(rad_to_deg(el), fposmod(az, 360.0))


## Unit vector toward a body, in the project compass (north -Z, east +X).
static func horizontal_to_world(elevation_deg: float, azimuth_deg: float) -> Vector3:
	var el := deg_to_rad(elevation_deg)
	var az := deg_to_rad(azimuth_deg)
	return Vector3(cos(el) * sin(az), sin(el), -cos(el) * cos(az))


## Basis for a DirectionalLight3D lighting the scene from `dir`. The light
## shines along its local -Z, so it must look at the point opposite the body.
static func light_basis(dir: Vector3) -> Basis:
	if dir.length_squared() < 1e-8:
		return Basis.IDENTITY
	var d := dir.normalized()
	# looking_at() degenerates when the forward axis is parallel to up.
	var up := Vector3.UP if absf(d.y) < 0.9995 else Vector3.FORWARD
	return Basis.looking_at(-d, up)


# --- sun ----------------------------------------------------------------------

static func sun_horizontal(month: int, day: int, hour: float, lat_deg: float = LATITUDE_DEG) -> Vector2:
	var doy := day_of_year(month, day)
	return horizontal(declination_deg(doy), hour_angle_deg(doy, hour), lat_deg)


static func sun_dir(month: int, day: int, hour: float, lat_deg: float = LATITUDE_DEG) -> Vector3:
	var h := sun_horizontal(month, day, hour, lat_deg)
	return horizontal_to_world(h.x, h.y)


static func sun_elevation_deg(month: int, day: int, hour: float, lat_deg: float = LATITUDE_DEG) -> float:
	return sun_horizontal(month, day, hour, lat_deg).x


static func sun_azimuth_deg(month: int, day: int, hour: float, lat_deg: float = LATITUDE_DEG) -> float:
	return sun_horizontal(month, day, hour, lat_deg).y


# --- moon ---------------------------------------------------------------------

## 0 is new, 0.5 is full. Days are whole, so the phase steps once per game day.
static func moon_phase(day: int, phase_offset_days: float = 0.0) -> float:
	return fposmod(float(day) + phase_offset_days, SYNODIC_MONTH_DAYS) / SYNODIC_MONTH_DAYS


## Lit fraction of the disc: 0 at new, 1 at full.
static func moon_illum(day: int, phase_offset_days: float = 0.0) -> float:
	return 0.5 * (1.0 - cos(TAU * moon_phase(day, phase_offset_days)))


## Not an ephemeris. The moon is placed by dragging the sun's hour angle back by
## a full turn times the phase, and by swinging its declination from the sun's
## (new) to the sun's negated (full). That reproduces what a player can actually
## check -- a full moon rises at sunset and rides high in winter, a new moon
## tracks the sun, a crescent hangs near the sun low in the sky -- and gets rise
## and set times of the in-between phases wrong by hours. Documented as an
## approximation in plans/SNOWPLAN-NOTES.md.
static func moon_horizontal(month: int, day: int, hour: float,
		phase_offset_days: float = 0.0, lat_deg: float = LATITUDE_DEG) -> Vector2:
	var doy := day_of_year(month, day)
	var p := moon_phase(day, phase_offset_days)
	var h := hour_angle_deg(doy, hour) - 360.0 * p
	var decl := declination_deg(doy) * MOON_DECLINATION_SWING * cos(TAU * p)
	return horizontal(decl, h, lat_deg)


static func moon_dir(month: int, day: int, hour: float,
		phase_offset_days: float = 0.0, lat_deg: float = LATITUDE_DEG) -> Vector3:
	var h := moon_horizontal(month, day, hour, phase_offset_days, lat_deg)
	return horizontal_to_world(h.x, h.y)


static func moon_elevation_deg(month: int, day: int, hour: float,
		phase_offset_days: float = 0.0, lat_deg: float = LATITUDE_DEG) -> float:
	return moon_horizontal(month, day, hour, phase_offset_days, lat_deg).x


# --- sunrise and sunset -------------------------------------------------------

## Clock hour of local solar noon.
static func solar_noon_hour(month: int, day: int) -> float:
	return 12.0 - time_correction_hours(day_of_year(month, day))


## Hours between solar noon and the sun crossing `altitude_deg`. 12 for a polar
## day, 0 for a polar night; neither happens at 45.2N, but the clamp keeps the
## acos honest if the latitude export is ever moved.
static func half_day_hours(month: int, day: int, lat_deg: float = LATITUDE_DEG,
		altitude_deg: float = HORIZON_ALTITUDE_DEG) -> float:
	var doy := day_of_year(month, day)
	var phi := deg_to_rad(lat_deg)
	var dec := deg_to_rad(declination_deg(doy))
	var denom := cos(phi) * cos(dec)
	if absf(denom) < 1e-9:
		return 12.0
	var cos_h := (sin(deg_to_rad(altitude_deg)) - sin(phi) * sin(dec)) / denom
	if cos_h <= -1.0:
		return 12.0
	if cos_h >= 1.0:
		return 0.0
	# Closed form rather than the plan's bisection: the equation is solvable,
	# and solving it keeps sunrise_hour() exactly consistent with elevation().
	return rad_to_deg(acos(cos_h)) / 15.0


## Vector2(sunrise_hour, sunset_hour), cached per day at the default latitude.
static func day_bounds(month: int, day: int, lat_deg: float = LATITUDE_DEG) -> Vector2:
	var cacheable := is_equal_approx(lat_deg, LATITUDE_DEG)
	var key := month * 100 + day
	if cacheable and _day_cache.has(key):
		return _day_cache[key]

	var noon := solar_noon_hour(month, day)
	var half := half_day_hours(month, day, lat_deg)
	var bounds := Vector2(noon - half, noon + half)
	if cacheable:
		_day_cache[key] = bounds
	return bounds


static func sunrise_hour(month: int, day: int, lat_deg: float = LATITUDE_DEG) -> float:
	return day_bounds(month, day, lat_deg).x


static func sunset_hour(month: int, day: int, lat_deg: float = LATITUDE_DEG) -> float:
	return day_bounds(month, day, lat_deg).y
