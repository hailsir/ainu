extends Node3D
## Main world scene script. Wires up the player, camera, and combat transition.

var _combat_transition: Node = null
var _gm: Node = null

## Tactical combat input handling.
var _last_mouse_pos: Vector2 = Vector2.ZERO
var _grid: Node = null
var _grid_visual: Node = null
var _turn_manager: Node = null
var _combat_camera: Camera3D = null
var _selected_unit: Node = null
var _reachable_cells: Array = []
var _attack_cells: Array = []

enum TacState { IDLE, UNIT_SELECTED, ANIMATING, ENEMY_TURN }
var _tac_state: TacState = TacState.IDLE


func _ready() -> void:
	_gm = get_node_or_null("/root/GameManager")

	# Create combat transition node.
	_combat_transition = Node.new()
	_combat_transition.name = "CombatTransition"
	_combat_transition.set_script(load("res://scripts/core/combat_transition.gd"))
	add_child(_combat_transition)

	# Wire references.
	var player: Node = get_node_or_null("Player")
	var camera_rig: Node = get_node_or_null("CameraRig")
	_combat_transition.set("world_root", self)
	_combat_transition.set("player", player)
	_combat_transition.set("camera_rig", camera_rig)

	# Connect transition signals.
	_combat_transition.connect("transition_to_combat_finished", _on_combat_entered)
	_combat_transition.connect("transition_to_freeroam_finished", _on_freeroam_entered)


func _on_combat_entered() -> void:
	# Find the tactical nodes that were just created.
	var tactical_root: Node = get_node_or_null("TacticalCombat")
	if tactical_root == null:
		return
	_grid = tactical_root.get_node_or_null("TacticalGrid")
	_grid_visual = _grid.get_node_or_null("GridVisual") if _grid else null
	_turn_manager = tactical_root.get_node_or_null("TurnManager")
	_combat_camera = tactical_root.get_node_or_null("CombatCamera") as Camera3D
	_tac_state = TacState.IDLE

	# Connect turn manager for enemy AI state tracking.
	if _turn_manager:
		_turn_manager.connect("turn_started", _on_tac_turn_started)
		_turn_manager.connect("turn_ended", _on_tac_turn_ended)


func _on_freeroam_entered() -> void:
	_grid = null
	_grid_visual = null
	_turn_manager = null
	_combat_camera = null
	_selected_unit = null
	_reachable_cells.clear()
	_attack_cells.clear()
	_tac_state = TacState.IDLE


func _input(event: InputEvent) -> void:
	if _gm == null or not (_gm.call("is_tactical") as bool):
		return
	if _grid == null or _combat_camera == null:
		return

	if event is InputEventMouseMotion:
		var motion: InputEventMouseMotion = event as InputEventMouseMotion
		_last_mouse_pos = motion.position

	if _tac_state == TacState.ANIMATING or _tac_state == TacState.ENEMY_TURN:
		return

	if event is InputEventMouseButton:
		var mouse_event: InputEventMouseButton = event as InputEventMouseButton
		# Node._input runs before the GUI ever sees the click, so a click on the
		# debug menu would otherwise select a unit behind the panel.
		if _over_debug_menu(mouse_event.position):
			return
		_last_mouse_pos = mouse_event.position
		if mouse_event.pressed and mouse_event.button_index == MOUSE_BUTTON_LEFT:
			_handle_tac_click()
			get_viewport().set_input_as_handled()
		elif mouse_event.pressed and mouse_event.button_index == MOUSE_BUTTON_RIGHT:
			_tac_deselect()
			get_viewport().set_input_as_handled()


## True when the debug menu is open and one of its panels covers `point`.
func _over_debug_menu(point: Vector2) -> bool:
	var menu: Node = get_tree().get_first_node_in_group("debug_menu")
	if menu == null or not menu.has_method("is_point_over_ui"):
		return false
	return menu.call("is_point_over_ui", point) as bool


func _handle_tac_click() -> void:
	var cell: Variant = _raycast_to_grid()
	if cell == null:
		return

	match _tac_state:
		TacState.IDLE:
			_try_select_unit(cell)
		TacState.UNIT_SELECTED:
			# Attack?
			if cell.call("is_occupied") as bool:
				var occupant: Node = cell.get("occupant") as Node
				if occupant != null and occupant.call("is_enemy") as bool:
					if _selected_unit != null and _selected_unit.call("can_attack") as bool:
						var su_coords: Vector2i = _selected_unit.get("grid_coords") as Vector2i
						var occ_coords: Vector2i = cell.get("coords") as Vector2i
						var dist: int = maxi(absi(su_coords.x - occ_coords.x), absi(su_coords.y - occ_coords.y))
						if dist <= (_selected_unit.get("attack_range") as int):
							_execute_attack(occupant)
							return
			# Move?
			if _is_cell_reachable(cell) and _selected_unit != null and _selected_unit.call("can_move") as bool:
				_execute_move(cell)


func _try_select_unit(cell: Variant) -> void:
	if not (cell.call("is_occupied") as bool):
		return
	var unit: Node = cell.get("occupant") as Node
	if unit == null:
		return
	if not (unit.call("is_player_unit") as bool) or not (unit.get("is_alive") as bool):
		return
	if _turn_manager and _turn_manager.call("get_active_unit") != unit:
		return

	_selected_unit = unit
	_tac_state = TacState.UNIT_SELECTED
	if _grid_visual:
		_grid_visual.call("clear_highlights")

	if unit.call("can_move") as bool:
		var coords: Vector2i = unit.get("grid_coords") as Vector2i
		var mr: int = unit.get("move_range") as int
		_reachable_cells = _grid.call("get_reachable_cells", coords, mr) as Array
		_grid_visual.call("highlight_cells", _reachable_cells, Color(0.2, 0.6, 1.0, 0.35))
	if unit.call("can_attack") as bool:
		var coords: Vector2i = unit.get("grid_coords") as Vector2i
		var ar: int = unit.get("attack_range") as int
		_attack_cells = _grid.call("get_cells_in_range", coords, ar) as Array
		_grid_visual.call("highlight_cells", _attack_cells, Color(1.0, 0.2, 0.2, 0.3))
	_grid_visual.call("show_selected", cell)


func _tac_deselect() -> void:
	_selected_unit = null
	_reachable_cells.clear()
	_attack_cells.clear()
	if _grid_visual:
		_grid_visual.call("clear_highlights")
	_tac_state = TacState.IDLE


func _execute_move(target_cell: Variant) -> void:
	if _selected_unit == null:
		return
	var su_coords: Vector2i = _selected_unit.get("grid_coords") as Vector2i
	var tc_coords: Vector2i = target_cell.get("coords") as Vector2i
	var path: Array = _grid.call("find_path", su_coords, tc_coords) as Array
	if path.is_empty():
		return

	_tac_state = TacState.ANIMATING
	if _grid_visual:
		_grid_visual.call("clear_highlights")
	_selected_unit.connect("move_animation_finished", _on_tac_move_finished, CONNECT_ONE_SHOT)
	_selected_unit.call("move_along_path", path)


func _execute_attack(target: Node) -> void:
	if _selected_unit == null or not (_selected_unit.call("can_attack") as bool):
		return
	_tac_state = TacState.ANIMATING
	_selected_unit.call("attack_target", target)
	if _grid_visual:
		_grid_visual.call("clear_highlights")

	await get_tree().create_timer(0.5).timeout

	if not (target.get("is_alive") as bool):
		var t_coords: Vector2i = target.get("grid_coords") as Vector2i
		_grid.call("remove_unit_from_cell", t_coords)
		target.set("visible", false)

	_after_tac_action()


func _on_tac_move_finished() -> void:
	_after_tac_action()


func _after_tac_action() -> void:
	if _selected_unit == null:
		return
	if (_selected_unit.get("action_points") as int) <= 0:
		if _turn_manager:
			_turn_manager.call("end_current_turn")
		_tac_deselect()
	else:
		var unit: Node = _selected_unit
		_tac_deselect()
		var coords: Vector2i = unit.get("grid_coords") as Vector2i
		var cell: Variant = _grid.call("get_cell", coords)
		if cell != null:
			_try_select_unit(cell)


func _on_tac_turn_started(unit: Node) -> void:
	if unit.call("is_enemy") as bool:
		_tac_state = TacState.ENEMY_TURN
	else:
		_tac_state = TacState.IDLE


func _on_tac_turn_ended(_unit: Node) -> void:
	_tac_deselect()


func _is_cell_reachable(cell: Variant) -> bool:
	var c_coords: Vector2i = cell.get("coords") as Vector2i
	for rc: Variant in _reachable_cells:
		if (rc.get("coords") as Vector2i) == c_coords:
			return true
	return false


func _raycast_to_grid() -> Variant:
	if _combat_camera == null or _grid == null:
		return null
	var mouse_pos: Vector2 = _last_mouse_pos
	var from: Vector3 = _combat_camera.project_ray_origin(mouse_pos)
	var dir: Vector3 = _combat_camera.project_ray_normal(mouse_pos)
	var plane_y: float = (_grid.get("grid_origin") as Vector3).y
	if absf(dir.y) < 0.001:
		return null
	var t: float = (plane_y - from.y) / dir.y
	if t < 0.0:
		return null
	var hit: Vector3 = from + dir * t
	var coords: Vector2i = _grid.call("world_to_grid", hit) as Vector2i
	return _grid.call("get_cell", coords)
