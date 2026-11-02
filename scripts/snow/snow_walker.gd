@tool
extends Node3D
class_name SnowWalker
## Gameplay coupling between a character and the snow field
## (see plans/SNOWPLAN.md section 5).
##
## Attach as a child of a CharacterBody3D. It owns that character's
## SnowDeformer, sinks the visual model into the snow, and publishes a
## move-speed multiplier the controller reads.
##
## The sink reference is sampled slightly ahead of the character rather than
## directly underfoot. Sampling underfoot would feed the character's own trench
## back into its sink depth and creep to the compaction floor within a few
## frames.

## Fired on each stride while wading, for footstep audio or VFX to hook.
signal snow_step(depth: float)

@export_group("Sink")
## Fraction of the local snow depth the character sinks into.
@export_range(0.0, 1.0, 0.01) var sink_ratio: float = 0.55
## Hard cap on sink, in metres.
@export var max_sink: float = 0.7
## How fast the visual sink chases its target.
@export var sink_smoothing: float = 8.0
## Node moved down as the character sinks. Defaults to the sibling "Model".
@export var model_path: NodePath = NodePath("../Model")

@export_group("Movement")
## Depth at which the speed penalty reaches its floor.
@export var full_penalty_depth: float = 0.8
## Speed multiplier at full_penalty_depth and beyond.
@export_range(0.05, 1.0, 0.01) var min_speed_multiplier: float = 0.4

@export_group("Deformer")
@export var deformer_radius: float = 0.42
@export_range(0.05, 1.0, 0.01) var deformer_flatten: float = 0.45
## How far ahead of the character the sink reference is sampled, in metres.
@export var sample_lookahead: float = 0.8

@export_group("Steps")
## Distance between stride events, in metres.
@export var stride_length: float = 0.9

var speed_multiplier: float = 1.0
var sink: float = 0.0
var snow_depth: float = 0.0

var _field: SnowField = null
var _body: CharacterBody3D = null
var _model: Node3D = null
var _deformer: SnowDeformer = null
var _sink_ref: float = 0.0
var _sink_ref_valid: bool = false
var _stride_accum: float = 0.0
var _last_pos: Vector3 = Vector3.ZERO


func _ready() -> void:
	_body = get_parent() as CharacterBody3D
	_model = get_node_or_null(model_path) as Node3D
	_deformer = SnowDeformer.new()
	_deformer.name = "SnowDeformer"
	_deformer.radius = deformer_radius
	_deformer.flatten = deformer_flatten
	add_child(_deformer)
	if Engine.is_editor_hint():
		return
	_last_pos = global_position
	call_deferred("_bind_field")


func _bind_field() -> void:
	_field = SnowField.find_field(self)
	if _field == null:
		push_warning("SnowWalker: no SnowField found; snow coupling is inactive.")


func _physics_process(delta: float) -> void:
	if Engine.is_editor_hint() or _field == null or _body == null:
		return

	var pos := _body.global_position
	var planar_velocity := Vector3(_body.velocity.x, 0.0, _body.velocity.z)
	var moving := planar_velocity.length() > 0.35

	snow_depth = _field.get_snow_depth(pos)

	# Sample the snow the character is about to wade into, not the trench it is
	# standing in. Hold the last reading while stationary.
	if moving:
		var ahead := pos + planar_velocity.normalized() * sample_lookahead
		_sink_ref = _field.get_snow_depth(ahead)
		_sink_ref_valid = true
	elif not _sink_ref_valid:
		_sink_ref = snow_depth
		_sink_ref_valid = true

	var target_sink: float = minf(_sink_ref * sink_ratio, max_sink)
	sink = lerpf(sink, target_sink, clampf(sink_smoothing * delta, 0.0, 1.0))

	# The character body rides the flat collision floor, so the visual model
	# carries the sink: it stands on the snow surface less however far it sank.
	var stand_height: float = maxf(_sink_ref - sink, 0.0)
	if _model:
		_model.position.y = stand_height

	# Press the snow to exactly the height the character stands at, so the
	# trench depth matches the visible sink.
	_deformer.position.y = stand_height + _deformer.radius * _deformer.flatten

	speed_multiplier = _depth_speed_multiplier(_sink_ref)
	if _body.has_method("set_snow_speed_multiplier"):
		_body.call("set_snow_speed_multiplier", speed_multiplier)

	_accumulate_stride(pos, moving)


func _depth_speed_multiplier(depth: float) -> float:
	if full_penalty_depth <= 0.0:
		return 1.0
	var t := clampf(depth / full_penalty_depth, 0.0, 1.0)
	return lerpf(1.0, min_speed_multiplier, t)


func _accumulate_stride(pos: Vector3, moving: bool) -> void:
	if not moving:
		_stride_accum = 0.0
		_last_pos = pos
		return
	_stride_accum += Vector2(pos.x - _last_pos.x, pos.z - _last_pos.z).length()
	_last_pos = pos
	if _stride_accum >= stride_length:
		_stride_accum = 0.0
		snow_step.emit(snow_depth)


## Snow depth under the character's feet, in metres.
func get_snow_depth_under_feet() -> float:
	return snow_depth
