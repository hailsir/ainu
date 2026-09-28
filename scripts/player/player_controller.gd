extends CharacterBody3D
## Third-person player controller with 8-directional movement.
## Rotates the character model to face movement direction.
## Camera-relative movement so forward is always "into the screen."

@export_group("Movement")
@export var walk_speed: float = 3.5
@export var run_speed: float = 6.5
@export var acceleration: float = 10.0
@export var deceleration: float = 12.0
@export var rotation_speed: float = 10.0

@export_group("Physics")
@export var gravity_multiplier: float = 2.5
@export var jump_velocity: float = 5.0

## The visual model root that gets rotated to face movement direction.
@onready var model: Node3D = $Model
## Reference to the camera rig (set during _ready).
var camera_rig: Node3D = null
## Cached reference to the GameManager autoload.
var _gm: Node = null

var _target_rotation: float = 0.0
var _is_sprinting: bool = false
## Terrain speed penalty, driven by SnowWalker. 1.0 is bare ground.
var _snow_speed_multiplier: float = 1.0


## Called by SnowWalker each physics frame with the local snow penalty.
func set_snow_speed_multiplier(value: float) -> void:
	_snow_speed_multiplier = clampf(value, 0.05, 1.0)


func _ready() -> void:
	# Find camera rig sibling.
	var rig: Node = get_node_or_null("../CameraRig")
	if rig is Node3D:
		camera_rig = rig as Node3D
	# Cache GameManager autoload.
	_gm = get_node_or_null("/root/GameManager")
	if _gm:
		_gm.call("register_player", self)


func _physics_process(delta: float) -> void:
	if _gm and not _gm.call("is_free_roam") as bool:
		return
	_apply_gravity(delta)
	_handle_movement(delta)
	move_and_slide()


func _apply_gravity(delta: float) -> void:
	if not is_on_floor():
		var grav: float = ProjectSettings.get_setting("physics/3d/default_gravity", 9.8) as float
		velocity.y -= grav * gravity_multiplier * delta


func _handle_movement(delta: float) -> void:
	var input_dir: Vector2 = Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	_is_sprinting = Input.is_action_pressed("sprint")

	var speed: float = (run_speed if _is_sprinting else walk_speed) * _snow_speed_multiplier

	if input_dir.length_squared() > 0.01:
		# Camera-relative movement direction.
		# get_vector returns y=-1 for forward (W), but camera forward is -Z,
		# so negate y to align "press W = move in camera forward direction."
		var cam_basis: Basis = _get_camera_flat_basis()
		var move_dir: Vector3 = (cam_basis * Vector3(input_dir.x, 0.0, -input_dir.y)).normalized()

		velocity.x = move_toward(velocity.x, move_dir.x * speed, acceleration * delta * speed)
		velocity.z = move_toward(velocity.z, move_dir.z * speed, acceleration * delta * speed)

		# Rotate model to face movement direction.
		_target_rotation = atan2(move_dir.x, move_dir.z)
		if model:
			model.rotation.y = lerp_angle(model.rotation.y, _target_rotation, rotation_speed * delta)
	else:
		velocity.x = move_toward(velocity.x, 0.0, deceleration * delta)
		velocity.z = move_toward(velocity.z, 0.0, deceleration * delta)


func _get_camera_flat_basis() -> Basis:
	if camera_rig:
		var cam_transform: Transform3D = camera_rig.global_transform
		var forward: Vector3 = -cam_transform.basis.z
		forward.y = 0.0
		forward = forward.normalized()
		var right: Vector3 = forward.cross(Vector3.UP).normalized()
		return Basis(right, Vector3.UP, forward)
	# Fallback: use global forward.
	return Basis.IDENTITY


func get_model_forward() -> Vector3:
	if model:
		return -model.global_transform.basis.z
	return Vector3.FORWARD


func is_moving() -> bool:
	var flat_vel: Vector2 = Vector2(velocity.x, velocity.z)
	return flat_vel.length_squared() > 0.5


func is_sprinting() -> bool:
	return _is_sprinting and is_moving()
