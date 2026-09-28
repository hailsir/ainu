extends Node3D
class_name SnowPrecipitation
## Camera-following snowfall and ground-blizzard particles (plans/WEATHERPLAN.md
## section 3).
##
## Two GPUParticles3D emitters, both built in code so the scene stays a plain
## Node3D. Every frame the node parks itself relative to whichever camera is
## current, which covers the free-roam rig camera and the CombatCamera that
## combat_transition.gd creates at runtime alike.
##
## SnowWeather calls set_conditions() with the live intensity and wind. Nothing
## here changes GPUParticles3D.amount at runtime, because that restarts the
## system and dumps every particle in flight; density rides on amount_ratio.

## Deform layer (bit 12) belongs to SnowField's deform camera, which must never
## see falling snow or it would carve the heightfield.
const VISIBLE_LAYER := 1

@export_group("Flakes")
@export var flake_amount: int = 3000
## Metres per second a flake falls in still air.
@export var flake_fall_speed: float = 1.3
@export var flake_box: Vector3 = Vector3(12.0, 7.0, 12.0)
## Flake quad side, in metres.
@export var flake_size: float = 0.05

@export_group("Streaks")
@export var streak_amount: int = 800
## Wind speed at which the ground blizzard starts.
@export var streak_wind_threshold_ms: float = 8.0
@export var streak_box: Vector3 = Vector3(15.0, 0.75, 15.0)

@export_group("Debug")
## Feed fixed conditions when no SnowWeather is driving this node. -1 = off.
@export var debug_intensity_cm_per_h: float = -1.0
@export var debug_wind_ms: float = -1.0
@export_range(0.0, 360.0, 1.0) var debug_wind_from_deg: float = 270.0

var _flakes: GPUParticles3D = null
var _streaks: GPUParticles3D = null
var _flake_process: ParticleProcessMaterial = null
var _streak_process: ParticleProcessMaterial = null

var _intensity: float = 0.0
var _wind: Vector3 = Vector3(1.0, 0.0, 0.0)
var _wind_speed: float = 0.0
var _field: SnowField = null


func _ready() -> void:
	_build_flakes()
	_build_streaks()
	call_deferred("_bind_field")
	if debug_intensity_cm_per_h >= 0.0:
		var field_deg := SnowWeather.from_bearing_to_field_deg(debug_wind_from_deg)
		set_conditions(
			debug_intensity_cm_per_h,
			SnowWeather.field_deg_to_world(field_deg),
			maxf(debug_wind_ms, 0.0))


func _bind_field() -> void:
	_field = SnowField.find_field(self)


## Called by SnowWeather. intensity is cm per game-hour, wind is the world-space
## unit vector the wind blows toward, speed is m/s.
func set_conditions(intensity: float, wind: Vector3, speed: float) -> void:
	_intensity = maxf(intensity, 0.0)
	_wind_speed = maxf(speed, 0.0)
	var flat := Vector3(wind.x, 0.0, wind.z)
	_wind = flat.normalized() if flat.length_squared() > 1e-6 else Vector3(1.0, 0.0, 0.0)
	_apply_conditions()


func _make_emitter(emitter_name: String, amount: int, extents: Vector3) -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.name = emitter_name
	p.amount = amount
	p.layers = VISIBLE_LAYER
	p.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	p.gi_mode = GeometryInstance3D.GI_MODE_DISABLED
	# World coordinates: reparenting the box under the camera each frame must
	# not drag the particles already in the air along with it.
	p.local_coords = false
	p.draw_order = GPUParticles3D.DRAW_ORDER_VIEW_DEPTH
	p.emitting = false
	# Generous, because the emitter moves with the camera and a tight box would
	# cull the system the moment it left the previous frame's view.
	var pad := extents * 2.0 + Vector3(8.0, 8.0, 8.0)
	p.visibility_aabb = AABB(-pad, pad * 2.0)
	add_child(p)
	return p


func _build_flakes() -> void:
	_flakes = _make_emitter("Flakes", flake_amount, flake_box)
	_flakes.lifetime = maxf(flake_box.y * 2.0 / maxf(flake_fall_speed, 0.01), 0.1)
	_flakes.transform_align = GPUParticles3D.TRANSFORM_ALIGN_Z_BILLBOARD

	_flake_process = ParticleProcessMaterial.new()
	_flake_process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	_flake_process.emission_box_extents = flake_box
	_flake_process.direction = Vector3.DOWN
	_flake_process.spread = 8.0
	_flake_process.gravity = Vector3.ZERO
	_flake_process.initial_velocity_min = flake_fall_speed
	_flake_process.initial_velocity_max = flake_fall_speed
	_flake_process.damping_min = 0.05
	_flake_process.damping_max = 0.15
	_flake_process.scale_min = 0.6
	_flake_process.scale_max = 1.4
	# Turbulence is what stops flakes falling like rain: they wander and swirl.
	_flake_process.turbulence_enabled = true
	_flake_process.turbulence_noise_strength = 0.6
	_flake_process.turbulence_noise_scale = 2.0
	_flake_process.turbulence_influence_min = 0.05
	_flake_process.turbulence_influence_max = 0.15
	_flake_process.color_ramp = _fade_ramp(1.0)
	_flakes.process_material = _flake_process

	var quad := QuadMesh.new()
	quad.size = Vector2(flake_size, flake_size)
	quad.material = _flake_material(_soft_dot_texture(), true)
	_flakes.draw_pass_1 = quad


func _build_streaks() -> void:
	_streaks = _make_emitter("Streaks", streak_amount, streak_box)
	_streaks.lifetime = 1.4
	# Camera-facing, but stretched along the velocity vector, which reads as a
	# streak of blowing snow rather than a flake.
	_streaks.transform_align = GPUParticles3D.TRANSFORM_ALIGN_Z_BILLBOARD_Y_TO_VELOCITY

	_streak_process = ParticleProcessMaterial.new()
	_streak_process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	_streak_process.emission_box_extents = streak_box
	_streak_process.direction = Vector3(1.0, 0.0, 0.0)
	_streak_process.spread = 6.0
	_streak_process.gravity = Vector3.ZERO
	_streak_process.initial_velocity_min = 8.0
	_streak_process.initial_velocity_max = 10.0
	_streak_process.scale_min = 0.7
	_streak_process.scale_max = 1.3
	_streak_process.turbulence_enabled = true
	_streak_process.turbulence_noise_strength = 0.4
	_streak_process.turbulence_noise_scale = 3.0
	_streak_process.turbulence_influence_min = 0.03
	_streak_process.turbulence_influence_max = 0.08
	_streak_process.color_ramp = _fade_ramp(0.28)
	_streaks.process_material = _streak_process

	var quad := QuadMesh.new()
	quad.size = Vector2(0.03, 0.5)
	# The same soft dot, stretched: an untextured quad reads as a hard white
	# stick rather than a smear of blowing snow.
	quad.material = _flake_material(_soft_dot_texture(), true)
	_streaks.draw_pass_1 = quad


## White ramp that fades in over the first 10% of life and out over the last
## 10%, so particles never pop in or out at the edge of the box.
func _fade_ramp(peak_alpha: float) -> GradientTexture1D:
	var g := Gradient.new()
	g.offsets = PackedFloat32Array([0.0, 0.1, 0.9, 1.0])
	g.colors = PackedColorArray([
		Color(1, 1, 1, 0.0),
		Color(1, 1, 1, peak_alpha),
		Color(1, 1, 1, peak_alpha),
		Color(1, 1, 1, 0.0),
	])
	var tex := GradientTexture1D.new()
	tex.gradient = g
	tex.width = 64
	return tex


## Procedural soft round dot, so no art asset is needed for a flake.
func _soft_dot_texture() -> GradientTexture2D:
	var g := Gradient.new()
	g.offsets = PackedFloat32Array([0.0, 0.55, 1.0])
	g.colors = PackedColorArray([
		Color(1, 1, 1, 1.0),
		Color(1, 1, 1, 0.7),
		Color(1, 1, 1, 0.0),
	])
	var tex := GradientTexture2D.new()
	tex.gradient = g
	tex.fill = GradientTexture2D.FILL_RADIAL
	tex.fill_from = Vector2(0.5, 0.5)
	tex.fill_to = Vector2(1.0, 0.5)
	tex.width = 32
	tex.height = 32
	return tex


func _flake_material(albedo: Texture2D, use_texture: bool) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	# Alignment comes from GPUParticles3D.transform_align; leaving the material's
	# own billboard mode on would fight it.
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_DISABLED
	mat.vertex_color_use_as_albedo = true
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.disable_receive_shadows = true
	# Distant flakes should dissolve into the fog rather than hang in front of it.
	mat.set_flag(BaseMaterial3D.FLAG_DISABLE_FOG, false)
	mat.albedo_color = Color(1, 1, 1, 1)
	if use_texture and albedo:
		mat.albedo_texture = albedo
	return mat


func _apply_conditions() -> void:
	if _flakes == null or _streaks == null:
		return

	# Flakes drift with the wind and fall through it.
	var fall := _wind * _wind_speed * 0.9 + Vector3.DOWN * flake_fall_speed
	var fall_speed := fall.length()
	_flake_process.direction = fall.normalized() if fall_speed > 1e-4 else Vector3.DOWN
	_flake_process.initial_velocity_min = fall_speed * 0.9
	_flake_process.initial_velocity_max = fall_speed * 1.1
	# Keep the box crossing time roughly constant as the wind speeds flakes up.
	_flakes.lifetime = clampf(flake_box.y * 2.0 / maxf(fall_speed, 0.2), 0.4, 20.0)
	# sqrt so a light flurry still reads on screen; 3 cm/h is a full screen.
	_flakes.amount_ratio = clampf(sqrt(_intensity / 3.0), 0.08, 1.0)
	_flakes.emitting = _intensity > 0.005

	_streak_process.direction = _wind
	_streak_process.initial_velocity_min = _wind_speed * 1.0
	_streak_process.initial_velocity_max = _wind_speed * 1.4
	_streaks.amount_ratio = clampf((_wind_speed - streak_wind_threshold_ms) / 8.0, 0.1, 1.0)
	_streaks.emitting = _wind_speed > streak_wind_threshold_ms


func _process(_delta: float) -> void:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return
	var cam_pos := cam.global_position
	var forward := -cam.global_transform.basis.z
	var flat_forward := Vector3(forward.x, 0.0, forward.z)
	if flat_forward.length_squared() < 1e-4:
		# Looking straight down; fall back to the camera's up vector projected
		# flat so the box still leads the view rather than snapping to an axis.
		var up := cam.global_transform.basis.y
		flat_forward = Vector3(up.x, 0.0, up.z)
	flat_forward = flat_forward.normalized() if flat_forward.length_squared() > 1e-4 else Vector3.FORWARD

	# 0 for a level view, 1 for straight down. The free-roam rig looks roughly
	# level and wants the box ahead of and above the camera; the tactical
	# CombatCamera looks down from 18 m and wants it below, in the space between
	# the camera and the ground, or every flake renders behind the viewer.
	var pitch_down := clampf(-forward.y, 0.0, 1.0)
	var lead := flat_forward * lerpf(8.0, 0.0, pitch_down) \
		+ forward * lerpf(0.0, flake_box.y * 1.5, pitch_down)
	var lift := Vector3.UP * lerpf(5.0, 0.0, pitch_down)

	# Push the box upwind so flakes have blown into view by the time they reach
	# it. The shift stays well inside the box's own half-extent: push it a full
	# box width upwind and the camera ends up outside, watching snow fall
	# somewhere else.
	var upwind := -_wind * minf(_wind_speed * 0.4, flake_box.x * 0.5)
	_flakes.global_position = cam_pos + lead + lift + upwind

	# Streaks hug the snow surface under the camera, not the camera itself.
	var ground_y := cam_pos.y
	if _field:
		ground_y = _field.get_snow_surface_y(cam_pos)
	_streaks.global_position = Vector3(cam_pos.x, ground_y + streak_box.y, cam_pos.z) - _wind * 10.0
