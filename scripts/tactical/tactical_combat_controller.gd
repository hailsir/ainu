extends Node3D
## Top-level controller for tactical combat.
## Handles mouse input for cell selection, unit commands, and AI turns.
## Manages the flow: select unit -> choose action -> execute -> next turn.

enum CombatState {
	IDLE,
	UNIT_SELECTED,
	CHOOSING_MOVE,
	CHOOSING_ATTACK,
	ANIMATING,
	ENEMY_TURN,
}

@onready var grid: TacticalGrid = $TacticalGrid
@onready var grid_visual: GridVisual = $TacticalGrid/GridVisual
@onready var turn_manager: TurnManager = $TurnManager
@onready var combat_camera: Camera3D = $CombatCamera

var state: CombatState = CombatState.IDLE
var _selected_unit: TacticalUnit = null
var _reachable_cells: Array[GridCell] = []
var _attack_cells: Array[GridCell] = []
var _current_path: Array[GridCell] = []
var _hovered_cell: GridCell = null


func _ready() -> void:
	turn_manager.turn_started.connect(_on_turn_started)
	turn_manager.turn_ended.connect(_on_turn_ended)
	turn_manager.phase_started.connect(_on_phase_started)
	turn_manager.all_enemies_defeated.connect(_on_victory)
	turn_manager.all_players_defeated.connect(_on_defeat)


func begin_combat(player_units: Array[TacticalUnit], enemy_units: Array[TacticalUnit]) -> void:
	turn_manager.start_combat(player_units, enemy_units)


func _unhandled_input(event: InputEvent) -> void:
	if state == CombatState.ANIMATING or state == CombatState.ENEMY_TURN:
		return

	if event is InputEventMouseMotion:
		_update_hover()

	if event is InputEventMouseButton:
		var mouse_event: InputEventMouseButton = event as InputEventMouseButton
		if mouse_event.pressed and mouse_event.button_index == MOUSE_BUTTON_LEFT:
			_handle_left_click()
		elif mouse_event.pressed and mouse_event.button_index == MOUSE_BUTTON_RIGHT:
			_handle_right_click()


func _update_hover() -> void:
	var cell: GridCell = _raycast_to_grid()
	if cell != _hovered_cell:
		_hovered_cell = cell
		_update_hover_visuals()


func _handle_left_click() -> void:
	if _hovered_cell == null:
		return

	match state:
		CombatState.IDLE:
			_try_select_unit(_hovered_cell)
		CombatState.UNIT_SELECTED, CombatState.CHOOSING_MOVE:
			if _hovered_cell.is_occupied() and _hovered_cell.occupant is TacticalUnit:
				var target: TacticalUnit = _hovered_cell.occupant as TacticalUnit
				if target.is_enemy() and _selected_unit and _selected_unit.can_attack():
					_execute_attack(target)
					return
			if _is_cell_reachable(_hovered_cell) and _selected_unit and _selected_unit.can_move():
				_execute_move(_hovered_cell)
		CombatState.CHOOSING_ATTACK:
			if _hovered_cell.is_occupied() and _hovered_cell.occupant is TacticalUnit:
				var target: TacticalUnit = _hovered_cell.occupant as TacticalUnit
				if target.is_enemy() and _selected_unit:
					_execute_attack(target)


func _handle_right_click() -> void:
	# Cancel / deselect.
	_deselect()


func _try_select_unit(cell: GridCell) -> void:
	if not cell.is_occupied():
		return
	if not (cell.occupant is TacticalUnit):
		return
	var unit: TacticalUnit = cell.occupant as TacticalUnit
	if not unit.is_player_unit() or not unit.is_alive:
		return
	if turn_manager.get_active_unit() != unit:
		return

	_selected_unit = unit
	state = CombatState.UNIT_SELECTED

	# Show reachable cells.
	grid_visual.clear_highlights()
	if unit.can_move():
		_reachable_cells = grid.get_reachable_cells(unit.grid_coords, unit.move_range)
		grid_visual.show_reachable(_reachable_cells)
	if unit.can_attack():
		_attack_cells = grid.get_cells_in_range(unit.grid_coords, unit.attack_range)
		grid_visual.show_attack_range(_attack_cells)
	grid_visual.show_selected(cell)


func _deselect() -> void:
	_selected_unit = null
	_reachable_cells.clear()
	_attack_cells.clear()
	_current_path.clear()
	grid_visual.clear_highlights()
	state = CombatState.IDLE


func _execute_move(target_cell: GridCell) -> void:
	if _selected_unit == null:
		return
	var path: Array[GridCell] = grid.find_path(_selected_unit.grid_coords, target_cell.coords)
	if path.is_empty():
		return

	state = CombatState.ANIMATING
	grid_visual.clear_highlights()
	_selected_unit.move_animation_finished.connect(_on_move_finished, CONNECT_ONE_SHOT)
	_selected_unit.move_along_path(path)


func _execute_attack(target: TacticalUnit) -> void:
	if _selected_unit == null or not _selected_unit.can_attack():
		return

	state = CombatState.ANIMATING
	var damage: int = _selected_unit.attack_target(target)
	grid_visual.clear_highlights()

	# Simple delay for attack feedback.
	await get_tree().create_timer(0.5).timeout

	if not target.is_alive:
		grid.remove_unit_from_cell(target.grid_coords)
		# Fade out dead unit.
		var tween: Tween = create_tween()
		tween.tween_property(target, "modulate", Color(1, 1, 1, 0), 0.5)
		await tween.finished

	# Check if unit has actions left.
	if _selected_unit.action_points <= 0:
		turn_manager.end_current_turn()
		_deselect()
	else:
		# Re-select to show updated options.
		var unit_cell: GridCell = grid.get_cell(_selected_unit.grid_coords)
		_deselect()
		if unit_cell:
			_try_select_unit(unit_cell)


func _on_move_finished() -> void:
	if _selected_unit == null:
		return
	if _selected_unit.action_points <= 0:
		turn_manager.end_current_turn()
		_deselect()
	else:
		var unit_cell: GridCell = grid.get_cell(_selected_unit.grid_coords)
		_deselect()
		if unit_cell:
			_try_select_unit(unit_cell)


func _on_turn_started(unit: TacticalUnit) -> void:
	if unit.is_enemy():
		state = CombatState.ENEMY_TURN
		_run_enemy_ai(unit)
	else:
		state = CombatState.IDLE


func _on_turn_ended(_unit: TacticalUnit) -> void:
	_deselect()


func _on_phase_started(team: int) -> void:
	if team == 1:
		state = CombatState.ENEMY_TURN
	else:
		state = CombatState.IDLE


func _on_victory() -> void:
	state = CombatState.IDLE
	print("VICTORY! All enemies defeated.")


func _on_defeat() -> void:
	state = CombatState.IDLE
	print("DEFEAT! All player units fallen.")


## Simple enemy AI: move toward nearest player unit and attack if in range.
func _run_enemy_ai(unit: TacticalUnit) -> void:
	await get_tree().create_timer(0.5).timeout

	# Find nearest player unit.
	var nearest: TacticalUnit = null
	var nearest_dist: float = INF
	for player_unit: TacticalUnit in turn_manager.player_units:
		if player_unit.is_alive:
			var dist: float = float(
				maxi(absi(player_unit.grid_coords.x - unit.grid_coords.x),
					 absi(player_unit.grid_coords.y - unit.grid_coords.y))
			)
			if dist < nearest_dist:
				nearest_dist = dist
				nearest = player_unit

	if nearest == null:
		turn_manager.end_current_turn()
		return

	# Try to attack if in range.
	if nearest_dist <= float(unit.attack_range) and unit.can_attack():
		unit.attack_target(nearest)
		await get_tree().create_timer(0.5).timeout
		if not nearest.is_alive:
			grid.remove_unit_from_cell(nearest.grid_coords)
		turn_manager.end_current_turn()
		return

	# Move toward nearest player.
	if unit.can_move():
		var reachable: Array[GridCell] = grid.get_reachable_cells(unit.grid_coords, unit.move_range)
		var best_cell: GridCell = null
		var best_dist: float = INF
		for cell: GridCell in reachable:
			var d: float = float(
				maxi(absi(cell.coords.x - nearest.grid_coords.x),
					 absi(cell.coords.y - nearest.grid_coords.y))
			)
			if d < best_dist:
				best_dist = d
				best_cell = cell

		if best_cell:
			var path: Array[GridCell] = grid.find_path(unit.grid_coords, best_cell.coords)
			if not path.is_empty():
				state = CombatState.ANIMATING
				unit.move_animation_finished.connect(
					func() -> void:
						# After moving, try to attack.
						if nearest.is_alive and unit.can_attack():
							var attack_dist: float = float(
								maxi(absi(unit.grid_coords.x - nearest.grid_coords.x),
									 absi(unit.grid_coords.y - nearest.grid_coords.y))
							)
							if attack_dist <= float(unit.attack_range):
								unit.attack_target(nearest)
						turn_manager.end_current_turn(),
					CONNECT_ONE_SHOT
				)
				unit.move_along_path(path)
				return

	turn_manager.end_current_turn()


func _is_cell_reachable(cell: GridCell) -> bool:
	for reachable: GridCell in _reachable_cells:
		if reachable.coords == cell.coords:
			return true
	return false


func _update_hover_visuals() -> void:
	# Show path preview when hovering reachable cells.
	if state == CombatState.UNIT_SELECTED and _selected_unit and _hovered_cell:
		if _is_cell_reachable(_hovered_cell) and _selected_unit.can_move():
			_current_path = grid.find_path(_selected_unit.grid_coords, _hovered_cell.coords)
			# Redraw highlights with path overlay.
			grid_visual.clear_highlights()
			grid_visual.show_reachable(_reachable_cells)
			if _selected_unit.can_attack():
				grid_visual.show_attack_range(_attack_cells)
			grid_visual.show_path(_current_path)


## Raycast from mouse position to find which grid cell is under the cursor.
func _raycast_to_grid() -> GridCell:
	if combat_camera == null:
		return null
	var mouse_pos: Vector2 = get_viewport().get_mouse_position()
	var from: Vector3 = combat_camera.project_ray_origin(mouse_pos)
	var dir: Vector3 = combat_camera.project_ray_normal(mouse_pos)

	# Intersect with the ground plane (y = grid_origin.y).
	var plane_y: float = grid.grid_origin.y if grid else 0.0
	if absf(dir.y) < 0.001:
		return null
	var t: float = (plane_y - from.y) / dir.y
	if t < 0.0:
		return null
	var hit: Vector3 = from + dir * t
	var coords: Vector2i = grid.world_to_grid(hit)
	return grid.get_cell(coords)
