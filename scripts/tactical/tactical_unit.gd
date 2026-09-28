class_name TacticalUnit
extends CharacterBody3D
## Base class for a unit on the tactical grid.
## Handles grid position, movement points, actions, and health.

signal unit_moved(from: Vector2i, to: Vector2i)
signal unit_attacked(target: TacticalUnit)
signal unit_died()
signal turn_ended()
signal move_animation_finished()

@export_group("Stats")
@export var unit_name: String = "Unit"
@export var max_hp: int = 100
@export var move_range: int = 4
@export var attack_range: int = 1
@export var attack_damage: int = 25
@export var action_points_max: int = 2

@export_group("Team")
## 0 = player, 1 = enemy.
@export var team: int = 0

var current_hp: int = 100
var grid_coords: Vector2i = Vector2i.ZERO
var action_points: int = 2
var has_moved: bool = false
var has_attacked: bool = false
var is_alive: bool = true

## Reference to the grid this unit lives on.
var tactical_grid: TacticalGrid = null

## Movement animation speed (cells per second).
var move_speed: float = 5.0
var _move_path: Array[GridCell] = []
var _is_moving: bool = false


func _ready() -> void:
	current_hp = max_hp
	action_points = action_points_max


func start_turn() -> void:
	action_points = action_points_max
	has_moved = false
	has_attacked = false


func can_move() -> bool:
	return is_alive and not has_moved and action_points > 0


func can_attack() -> bool:
	return is_alive and not has_attacked and action_points > 0


## Begin animated movement along a path of cells.
func move_along_path(path: Array[GridCell]) -> void:
	if path.is_empty():
		return
	_move_path = path
	_is_moving = true
	has_moved = true
	action_points -= 1

	# Update grid data.
	var destination: GridCell = path[path.size() - 1]
	var old_coords: Vector2i = grid_coords
	if tactical_grid:
		tactical_grid.move_unit(self, grid_coords, destination.coords)
	grid_coords = destination.coords
	unit_moved.emit(old_coords, destination.coords)


func _physics_process(delta: float) -> void:
	if not _is_moving or _move_path.is_empty():
		return

	var target_cell: GridCell = _move_path[0]
	var target_pos: Vector3 = target_cell.world_position
	target_pos.y = global_position.y  # Keep same height.

	var direction: Vector3 = (target_pos - global_position)
	var distance: float = direction.length()

	if distance < 0.05:
		global_position = target_pos
		_move_path.remove_at(0)
		if _move_path.is_empty():
			_is_moving = false
			move_animation_finished.emit()
	else:
		# Move toward target.
		var step: float = move_speed * delta
		global_position = global_position.move_toward(target_pos, step)
		# Rotate to face movement direction.
		if direction.length_squared() > 0.001:
			var look_target: float = atan2(direction.x, direction.z)
			rotation.y = lerp_angle(rotation.y, look_target, 10.0 * delta)


## Execute an attack on a target unit.
func attack_target(target: TacticalUnit) -> int:
	if not can_attack():
		return 0
	has_attacked = true
	action_points -= 1
	var damage: int = attack_damage
	target.take_damage(damage)
	unit_attacked.emit(target)
	return damage


func take_damage(amount: int) -> void:
	current_hp = maxi(current_hp - amount, 0)
	if current_hp <= 0:
		is_alive = false
		unit_died.emit()


func end_turn() -> void:
	turn_ended.emit()


func is_enemy() -> bool:
	return team == 1


func is_player_unit() -> bool:
	return team == 0
