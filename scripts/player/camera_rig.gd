extends Node3D
## Over-the-shoulder third-person camera.
## Follows a target node, orbits with mouse, and provides
## a slight horizontal offset for the OTS look.

@export_group("Follow")
## The node this camera follows.
@export var follow_target: Node3D = null
@export var follow_speed: float = 8.0
@export var follow_offset: Vector3 = Vector3(0.0, 1.8, 0.0)

@export_group("Orbit")
@export var mouse_sensitivity: float = 0.002
@export var pitch_min: float = -40.0
@export var pitch_max: float = 60.0

@export_group("Boom")
## Distance from pivot to camera.
@export var boom_length: float = 3.5
## Horizontal offset for over-the-shoulder framing (positive = right).
@export var shoulder_offset: float = 0.6
## Vertical offset on the boom arm.
@export var boom_vertical_offset: float = 0.3

@export_group("Collision")
@export var collision_margin: float = 0.2
@export var collision_mask: int = 1

@export_group("Snow clearance")
## Lift the boom so the camera stays above the snow surface.
##
## The ground collider is a flat plane at y = 0, but the snow is drawn by
## vertex displacement in snow_ground.gdshader and carries no collision, so the
## SpringArm3D happily parks the camera 1.6 m inside a drift and renders the
## world from under the ground. SnowField's readback gives the real surface
## height, so the boom is raised to clear it.
@export var snow_clearance_enabled: bool = true
## Metres to keep between the camera and the snow surface. Must comfortably
## exceed the camera's near plane.
@export var snow_clearance: float = 0.4
## Points tested along the boom. More than one so a drift crest between the
## player and the camera lifts the boom too, not just the snow under the lens.
@export_range(1, 8, 1) var snow_clearance_samples: int = 4
## Metres per second the boom rises out of snow. Fast, so the camera never dips.
@export var snow_rise_speed: float = 6.0
## Metres per second the boom settles back. Slow, so cresting a drift is smooth.
@export var snow_settle_speed: float = 1.5
## Ceiling on the lift, as a guard against a bad field reading. Needs headroom
## over SnowField.max_depth: raising the boom lets the arm clear the ground
## collider and extend to full length, which drops the camera again, so the
## converged lift runs above the raw snow depth. Measured 2.97 m at pitch +60
## in 1.66 m of snow.
@export var max_snow_lift: float = 4.0

var _yaw: float = 0.0
var _pitch: float = deg_to_rad(-10.0)

@onready var _spring_arm: SpringArm3D = $SpringArm3D
@onready var _camera: Camera3D = $SpringArm3D/Camera3D

var _gm: Node = null
var _snow_field: SnowField = null
var _snow_lift: float = 0.0


func _ready() -> void:
	Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)
	# Configure spring arm.
	if _spring_arm:
		_spring_arm.spring_length = boom_length
		_spring_arm.margin = collision_margin
		_spring_arm.collision_mask = collision_mask
	# If no follow target set, try to find Player sibling.
	if follow_target == null:
		var player: Node = get_node_or_null("../Player")
		if player is Node3D:
			follow_target = player as Node3D
	# Register with game manager.
	_gm = get_node_or_null("/root/GameManager")
	if _gm:
		_gm.call("register_camera", self)
	# Deferred: the field registers its group in its own _ready.
	call_deferred("_bind_snow_field")


func _bind_snow_field() -> void:
	# Optional. Without a SnowField the clearance logic stays inert and the rig
	# behaves exactly as it did before.
	_snow_field = SnowField.find_field(self)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		var motion: InputEventMouseMotion = event as InputEventMouseMotion
		_yaw -= motion.relative.x * mouse_sensitivity
		_pitch -= motion.relative.y * mouse_sensitivity
		_pitch = clampf(_pitch, deg_to_rad(pitch_min), deg_to_rad(pitch_max))

	if event is InputEventKey:
		var key_event: InputEventKey = event as InputEventKey
		if key_event.pressed and key_event.keycode == KEY_ESCAPE:
			if Input.get_mouse_mode() == Input.MOUSE_MODE_CAPTURED:
				Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
			else:
				Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)


func _physics_process(delta: float) -> void:
	_follow(delta)
	_update_snow_lift(delta)
	_orient()
	_aim_camera()


func _follow(delta: float) -> void:
	if follow_target == null:
		return
	var target_pos: Vector3 = follow_target.global_position + follow_offset
	global_position = global_position.lerp(target_pos, follow_speed * delta)


func _orient() -> void:
	# Apply yaw/pitch to the rig itself, then shoulder offset via spring arm.
	rotation = Vector3.ZERO
	rotate_y(_yaw)
	if _spring_arm:
		_spring_arm.rotation = Vector3(_pitch, 0.0, 0.0)
		# Shoulder offset: shift the spring arm sideways. The snow lift is
		# folded in here as an absolute value rather than accumulated onto the
		# camera, so it cannot drift frame to frame. The rig carries yaw only,
		# so the arm's local +Y is world +Y.
		_spring_arm.position = Vector3(shoulder_offset, boom_vertical_offset + _snow_lift, 0.0)


## Raises the boom until the whole arm clears the snow surface.
##
## Measured against where the boom would sit with no lift applied, so the
## result is an absolute lift rather than an increment, and repeated frames
## converge instead of creeping upwards.
func _update_snow_lift(delta: float) -> void:
	var needed := 0.0
	if snow_clearance_enabled and _snow_field and _spring_arm and _camera:
		var drop := Vector3.UP * _snow_lift
		var from := _spring_arm.global_position - drop
		var to := _camera.global_position - drop
		for i in snow_clearance_samples:
			var t := float(i + 1) / float(snow_clearance_samples)
			var p := from.lerp(to, t)
			needed = maxf(needed, _snow_field.get_snow_surface_y(p) + snow_clearance - p.y)
		needed = clampf(needed, 0.0, max_snow_lift)
	var speed := snow_rise_speed if needed > _snow_lift else snow_settle_speed
	_snow_lift = move_toward(_snow_lift, needed, speed * delta)


## Keeps the framing the lift would otherwise throw off. The camera normally
## looks straight down the boom at the arm's origin; once the arm is raised it
## is re-aimed at where that origin would have been, so the player stays put in
## frame and only the viewing height changes.
func _aim_camera() -> void:
	if _camera == null or _spring_arm == null:
		return
	if _snow_lift <= 0.001:
		# No lift, so the camera looks straight down the boom as the spring arm
		# intends. look_at writes a local rotation, so clear anything an earlier
		# lift left behind rather than leaving the view permanently skewed.
		if not _camera.rotation.is_zero_approx():
			_camera.rotation = Vector3.ZERO
		return
	var aim := _spring_arm.global_position - Vector3.UP * _snow_lift
	var offset := aim - _camera.global_position
	# look_at fails on a zero-length basis or one parallel to the up vector.
	if offset.length_squared() < 0.0001 or Vector2(offset.x, offset.z).length() < 0.01:
		return
	_camera.look_at(aim, Vector3.UP)


func get_camera() -> Camera3D:
	return _camera


func get_yaw() -> float:
	return _yaw


func get_pitch() -> float:
	return _pitch
