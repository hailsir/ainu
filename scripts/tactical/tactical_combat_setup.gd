extends Node3D
## Initializes tactical combat when the scene loads.
## Finds all TacticalUnit children, registers them on the grid,
## and starts combat through the TurnManager.

@onready var grid: TacticalGrid = $TacticalGrid
@onready var turn_manager: TurnManager = $TurnManager
@onready var combat_camera: Camera3D = $CombatCamera
@onready var grid_visual: GridVisual = $TacticalGrid/GridVisual

enum CombatState {
	IDLE,
	UNIT_SELECTED,
	ANIMATING,
	ENEMY_TURN,
}

var state: CombatState = CombatState.IDLE
var _selected_unit: TacticalUnit = null
var _reachable_cells: Array[GridCell] = []
var _attack_cells: Array[GridCell] = []

var player_units: Array[TacticalUnit] = []
var enemy_units: Array[TacticalUnit] = []


func _ready() -> void:
	Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)

	# Wait a frame for grid to generate cells.
	await get_tree().process_frame

	# Find all TacticalUnit children and register them on the grid.
	for child: Node in get_children():
		if child is TacticalUnit:
			var unit: TacticalUnit = child as TacticalUnit
			unit.tactical_grid = grid
			# Convert world position to grid coords and snap.
			var coords: Vector2i = grid.world_to_grid(unit.global_position)
			unit.grid_coords = coords
			grid.place_unit(unit, coords)
			if unit.is_player_unit():
				player_units.append(unit)
			else:
				enemy_units.append(unit)

	# Connect turn manager signals.
	turn_manager.turn_started.connect(_on_turn_started)
	turn_manager.turn_ended.connect(_on_turn_ended)
	turn_manager.all_enemies_defeated.connect(_on_victory)
	turn_manager.all_players_defeated.connect(_on_defeat)

	# Start combat.
	turn_manager.start_combat(player_units, enemy_units)


var _last_mouse_pos: Vector2 = Vector2.ZERO


func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		var motion: InputEventMouseMotion = event as InputEventMouseMotion
		_last_mouse_pos = motion.position

	if state == CombatState.ANIMATING or state == CombatState.ENEMY_TURN:
		return

	if event is InputEventMouseButton:
		var mouse_event: InputEventMouseButton = event as InputEventMouseButton
		_last_mouse_pos = mouse_event.position
		if mouse_event.pressed and mouse_event.button_index == MOUSE_BUTTON_LEFT:
			_handle_left_click()
			get_viewport().set_input_as_handled()
		elif mouse_event.pressed and mouse_event.button_index == MOUSE_BUTTON_RIGHT:
			_deselect()
			get_viewport().set_input_as_handled()


func _handle_left_click() -> void:
	var cell: GridCell = _raycast_to_grid()
	if cell == null:
		return

	match state:
		CombatState.IDLE:
			_try_select_unit(cell)
		CombatState.UNIT_SELECTED:
			# Check if clicking an enemy for attack.
			if cell.is_occupied() and cell.occupant is TacticalUnit:
				var target: TacticalUnit = cell.occupant as TacticalUnit
				if target.is_enemy() and _selected_unit and _selected_unit.can_attack():
					# Check if in attack range.
					var dist: int = maxi(
						absi(target.grid_coords.x - _selected_unit.grid_coords.x),
						absi(target.grid_coords.y - _selected_unit.grid_coords.y)
					)
					if dist <= _selected_unit.attack_range:
						_execute_attack(target)
						return
			# Check if clicking a reachable cell for movement.
			if _is_cell_reachable(cell) and _selected_unit and _selected_unit.can_move():
				_execute_move(cell)


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
	_selected_unit.attack_target(target)
	grid_visual.clear_highlights()

	await get_tree().create_timer(0.5).timeout

	if not target.is_alive:
		grid.remove_unit_from_cell(target.grid_coords)
		target.visible = false

	_after_action()


func _on_move_finished() -> void:
	_after_action()


func _after_action() -> void:
	if _selected_unit == null:
		return
	if _selected_unit.action_points <= 0:
		turn_manager.end_current_turn()
		_deselect()
	else:
		var unit: TacticalUnit = _selected_unit
		_deselect()
		var cell: GridCell = grid.get_cell(unit.grid_coords)
		if cell:
			_try_select_unit(cell)


func _on_turn_started(unit: TacticalUnit) -> void:
	if unit.is_enemy():
		state = CombatState.ENEMY_TURN
		_run_enemy_ai(unit)
	else:
		state = CombatState.IDLE


func _on_turn_ended(_unit: TacticalUnit) -> void:
	_deselect()


func _on_victory() -> void:
	state = CombatState.IDLE
	print("VICTORY!")


func _on_defeat() -> void:
	state = CombatState.IDLE
	print("DEFEAT!")


func _run_enemy_ai(unit: TacticalUnit) -> void:
	await get_tree().create_timer(0.5).timeout

	var nearest: TacticalUnit = null
	var nearest_dist: float = INF
	for pu: TacticalUnit in player_units:
		if pu.is_alive:
			var dist: float = float(maxi(
				absi(pu.grid_coords.x - unit.grid_coords.x),
				absi(pu.grid_coords.y - unit.grid_coords.y)
			))
			if dist < nearest_dist:
				nearest_dist = dist
				nearest = pu

	if nearest == null:
		turn_manager.end_current_turn()
		return

	# Attack if in range.
	if nearest_dist <= float(unit.attack_range) and unit.can_attack():
		unit.attack_target(nearest)
		await get_tree().create_timer(0.3).timeout
		if not nearest.is_alive:
			grid.remove_unit_from_cell(nearest.grid_coords)
			nearest.visible = false
		turn_manager.end_current_turn()
		return

	# Move toward nearest player.
	if unit.can_move():
		var reachable: Array[GridCell] = grid.get_reachable_cells(unit.grid_coords, unit.move_range)
		var best_cell: GridCell = null
		var best_dist: float = INF
		for cell: GridCell in reachable:
			var d: float = float(maxi(
				absi(cell.coords.x - nearest.grid_coords.x),
				absi(cell.coords.y - nearest.grid_coords.y)
			))
			if d < best_dist:
				best_dist = d
				best_cell = cell

		if best_cell:
			var path: Array[GridCell] = grid.find_path(unit.grid_coords, best_cell.coords)
			if not path.is_empty():
				state = CombatState.ANIMATING
				unit.move_along_path(path)
				await unit.move_animation_finished
				# Try attack after move.
				if nearest.is_alive and unit.can_attack():
					var attack_dist: int = maxi(
						absi(unit.grid_coords.x - nearest.grid_coords.x),
						absi(unit.grid_coords.y - nearest.grid_coords.y)
					)
					if attack_dist <= unit.attack_range:
						unit.attack_target(nearest)
						await get_tree().create_timer(0.3).timeout
						if not nearest.is_alive:
							grid.remove_unit_from_cell(nearest.grid_coords)
							nearest.visible = false

	turn_manager.end_current_turn()


func _is_cell_reachable(cell: GridCell) -> bool:
	for rc: GridCell in _reachable_cells:
		if rc.coords == cell.coords:
			return true
	return false


func _raycast_to_grid() -> GridCell:
	if combat_camera == null or grid == null:
		return null
	var mouse_pos: Vector2 = _last_mouse_pos
	var from: Vector3 = combat_camera.project_ray_origin(mouse_pos)
	var dir: Vector3 = combat_camera.project_ray_normal(mouse_pos)
	var plane_y: float = grid.grid_origin.y
	if absf(dir.y) < 0.001:
		return null
	var t: float = (plane_y - from.y) / dir.y
	if t < 0.0:
		return null
	var hit: Vector3 = from + dir * t
	var coords: Vector2i = grid.world_to_grid(hit)
	return grid.get_cell(coords)
