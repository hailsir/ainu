extends Node
## Manages the transition between free-roam and tactical combat.
## Spawns the tactical grid around the player, places units, switches cameras,
## and cleans up when combat ends.

signal transition_to_combat_started()
signal transition_to_combat_finished()
signal transition_to_freeroam_started()
signal transition_to_freeroam_finished()

## The world scene root (set by the world scene).
var world_root: Node3D = null
## Player controller reference.
var player: CharacterBody3D = null
## Camera rig reference.
var camera_rig: Node3D = null

## Tactical nodes created during combat.
var _tactical_root: Node3D = null
var _combat_camera: Camera3D = null
var _grid: Node3D = null  # TacticalGrid
var _grid_visual: Node3D = null  # GridVisual
var _turn_manager: Node = null
var _combat_controller: Node3D = null

## Tactical unit scenes / data.
var _player_units: Array = []
var _enemy_units: Array = []

var _gm: Node = null
var _is_transitioning: bool = false

const GRID_WIDTH: int = 12
const GRID_HEIGHT: int = 12
const CELL_SIZE: float = 1.5


func _ready() -> void:
	_gm = get_node_or_null("/root/GameManager")


func _input(event: InputEvent) -> void:
	if event.is_action_pressed("toggle_combat") and not _is_transitioning:
		if _gm and _gm.call("is_free_roam") as bool:
			enter_combat()
		elif _gm and _gm.call("is_tactical") as bool:
			exit_combat()

	if event.is_action_pressed("end_turn"):
		if _turn_manager and _gm and _gm.call("is_tactical") as bool:
			_turn_manager.call("end_current_turn")


func enter_combat() -> void:
	if _is_transitioning or player == null:
		return
	_is_transitioning = true
	transition_to_combat_started.emit()

	# Freeze player.
	player.set_physics_process(false)

	# Calculate grid origin centered on player.
	var player_pos: Vector3 = player.global_position
	var grid_origin: Vector3 = Vector3(
		player_pos.x - (GRID_WIDTH * CELL_SIZE) / 2.0,
		0.0,
		player_pos.z - (GRID_HEIGHT * CELL_SIZE) / 2.0
	)

	# Create tactical root.
	_tactical_root = Node3D.new()
	_tactical_root.name = "TacticalCombat"
	world_root.add_child(_tactical_root)

	# Create grid.
	_grid = Node3D.new()
	_grid.name = "TacticalGrid"
	_grid.set_script(load("res://scripts/tactical/tactical_grid.gd"))
	_grid.set("grid_width", GRID_WIDTH)
	_grid.set("grid_height", GRID_HEIGHT)
	_grid.set("cell_size", CELL_SIZE)
	_grid.set("grid_origin", grid_origin)
	_tactical_root.add_child(_grid)

	# Create grid visual.
	_grid_visual = Node3D.new()
	_grid_visual.name = "GridVisual"
	_grid_visual.set_script(load("res://scripts/tactical/grid_visual.gd"))
	_grid.add_child(_grid_visual)

	# Create turn manager.
	_turn_manager = Node.new()
	_turn_manager.name = "TurnManager"
	_turn_manager.set_script(load("res://scripts/tactical/turn_manager.gd"))
	_tactical_root.add_child(_turn_manager)

	# Create combat camera.
	_combat_camera = Camera3D.new()
	_combat_camera.name = "CombatCamera"
	_combat_camera.fov = 50.0
	_combat_camera.position = Vector3(player_pos.x, player_pos.y + 18.0, player_pos.z + 12.0)
	_combat_camera.rotation = Vector3(deg_to_rad(-55.0), 0.0, 0.0)
	_tactical_root.add_child(_combat_camera)
	SnowField.hide_deformers_from(_combat_camera)

	# Wait a frame for grid to generate.
	await get_tree().process_frame

	# Hide the player model during combat (they become a tactical unit).
	player.visible = false

	# Mark obstacles on the grid by raycasting or checking existing StaticBody3D.
	_mark_obstacles_on_grid(grid_origin)

	# Create player tactical unit at player's grid position.
	var player_grid_coords: Vector2i = _grid.call("world_to_grid", player_pos) as Vector2i
	var player_unit: CharacterBody3D = _create_tactical_unit(
		"Samurai", 0, 100, 4, 1, 30, Color(0.2, 0.4, 0.9)
	)
	_tactical_root.add_child(player_unit)
	_grid.call("place_unit", player_unit, player_grid_coords)
	player_unit.set("grid_coords", player_grid_coords)
	player_unit.set("tactical_grid", _grid)
	_player_units.append(player_unit)

	# Spawn enemies around the player.
	var enemy_offsets: Array[Vector2i] = [
		Vector2i(3, 4), Vector2i(4, 5), Vector2i(-2, 5)
	]
	for i: int in range(enemy_offsets.size()):
		var offset: Vector2i = enemy_offsets[i]
		var enemy_coords: Vector2i = player_grid_coords + offset
		if _grid.call("is_valid", enemy_coords) as bool:
			var cell: Variant = _grid.call("get_cell", enemy_coords)
			if cell != null and cell.call("is_passable") as bool:
				var enemy: CharacterBody3D = _create_tactical_unit(
					"Bandit %d" % (i + 1), 1, 80, 3, 1, 20, Color(0.9, 0.2, 0.15)
				)
				_tactical_root.add_child(enemy)
				_grid.call("place_unit", enemy, enemy_coords)
				enemy.set("grid_coords", enemy_coords)
				enemy.set("tactical_grid", _grid)
				_enemy_units.append(enemy)

	# Switch camera.
	_combat_camera.current = true
	Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)

	# Set game mode.
	if _gm:
		_gm.call("set_mode", 1)  # TACTICAL

	# Connect turn manager signals.
	_turn_manager.connect("turn_started", _on_turn_started)
	_turn_manager.connect("all_enemies_defeated", _on_combat_victory)
	_turn_manager.connect("all_players_defeated", _on_combat_defeat)

	# Start combat.
	_turn_manager.call("start_combat", _player_units, _enemy_units)

	_is_transitioning = false
	transition_to_combat_finished.emit()


func exit_combat() -> void:
	if _is_transitioning:
		return
	_is_transitioning = true
	transition_to_freeroam_started.emit()

	# Restore player.
	player.visible = true
	player.set_physics_process(true)

	# Switch camera back.
	if camera_rig:
		var cam: Camera3D = camera_rig.get_node_or_null("SpringArm3D/Camera3D") as Camera3D
		if cam:
			cam.current = true
	Input.set_mouse_mode(Input.MOUSE_MODE_CONFINED_HIDDEN)

	# Clean up tactical nodes.
	if _tactical_root and is_instance_valid(_tactical_root):
		_tactical_root.queue_free()
	_tactical_root = null
	_combat_camera = null
	_grid = null
	_grid_visual = null
	_turn_manager = null
	_combat_controller = null
	_player_units.clear()
	_enemy_units.clear()

	# Set game mode.
	if _gm:
		_gm.call("set_mode", 0)  # FREE_ROAM

	_is_transitioning = false
	transition_to_freeroam_finished.emit()


func _create_tactical_unit(unit_name: String, team: int, hp: int,
		move_range: int, attack_range: int, damage: int, color: Color) -> CharacterBody3D:
	var unit: CharacterBody3D = CharacterBody3D.new()
	unit.name = unit_name.replace(" ", "")
	unit.set_script(load("res://scripts/tactical/tactical_unit.gd"))
	unit.set("unit_name", unit_name)
	unit.set("team", team)
	unit.set("max_hp", hp)
	unit.set("move_range", move_range)
	unit.set("attack_range", attack_range)
	unit.set("attack_damage", damage)

	var mesh_inst: MeshInstance3D = MeshInstance3D.new()
	mesh_inst.name = "Mesh"
	var capsule: CapsuleMesh = CapsuleMesh.new()
	capsule.radius = 0.3
	capsule.height = 1.4
	mesh_inst.mesh = capsule
	mesh_inst.position = Vector3(0.0, 0.7, 0.0)
	var mat: StandardMaterial3D = StandardMaterial3D.new()
	mat.albedo_color = color
	mesh_inst.set_surface_override_material(0, mat)
	unit.add_child(mesh_inst)

	var col: CollisionShape3D = CollisionShape3D.new()
	col.name = "Collision"
	var shape: CapsuleShape3D = CapsuleShape3D.new()
	shape.radius = 0.3
	shape.height = 1.4
	col.shape = shape
	col.position = Vector3(0.0, 0.7, 0.0)
	unit.add_child(col)

	return unit


func _mark_obstacles_on_grid(grid_origin: Vector3) -> void:
	# Check each grid cell for overlap with existing StaticBody3D obstacles.
	if world_root == null or _grid == null:
		return
	for child: Node in world_root.get_children():
		if child is StaticBody3D and child.name.begins_with("Obstacle"):
			var obs: StaticBody3D = child as StaticBody3D
			var obs_pos: Vector3 = obs.global_position
			# Get collision shape size.
			for sub: Node in obs.get_children():
				if sub is CollisionShape3D:
					var col_shape: CollisionShape3D = sub as CollisionShape3D
					if col_shape.shape is BoxShape3D:
						var box: BoxShape3D = col_shape.shape as BoxShape3D
						var half_size: Vector3 = box.size / 2.0
						# Mark all grid cells overlapping this box as unwalkable.
						var min_pos: Vector3 = obs_pos - half_size
						var max_pos: Vector3 = obs_pos + half_size
						var min_grid: Vector2i = _grid.call("world_to_grid", min_pos) as Vector2i
						var max_grid: Vector2i = _grid.call("world_to_grid", max_pos) as Vector2i
						for gx: int in range(min_grid.x, max_grid.x + 1):
							for gy: int in range(min_grid.y, max_grid.y + 1):
								var cell: Variant = _grid.call("get_cell", Vector2i(gx, gy))
								if cell != null:
									cell.set("walkable", false)


func _on_turn_started(unit: Node) -> void:
	if unit.call("is_enemy") as bool:
		_run_enemy_ai(unit)


func _on_combat_victory() -> void:
	print("VICTORY! Returning to free roam...")
	# Auto-exit combat after a delay.
	await get_tree().create_timer(1.5).timeout
	exit_combat()


func _on_combat_defeat() -> void:
	print("DEFEAT!")


func _run_enemy_ai(unit: Node) -> void:
	await get_tree().create_timer(0.5).timeout

	var nearest: Node = null
	var nearest_dist: float = INF
	for pu: Node in _player_units:
		if pu.get("is_alive") as bool:
			var pu_coords: Vector2i = pu.get("grid_coords") as Vector2i
			var u_coords: Vector2i = unit.get("grid_coords") as Vector2i
			var dist: float = float(maxi(absi(pu_coords.x - u_coords.x), absi(pu_coords.y - u_coords.y)))
			if dist < nearest_dist:
				nearest_dist = dist
				nearest = pu

	if nearest == null:
		_turn_manager.call("end_current_turn")
		return

	# Attack if in range.
	var u_attack_range: int = unit.get("attack_range") as int
	if nearest_dist <= float(u_attack_range) and unit.call("can_attack") as bool:
		unit.call("attack_target", nearest)
		await get_tree().create_timer(0.3).timeout
		if not nearest.get("is_alive") as bool:
			var n_coords: Vector2i = nearest.get("grid_coords") as Vector2i
			_grid.call("remove_unit_from_cell", n_coords)
			nearest.set("visible", false)
		_turn_manager.call("end_current_turn")
		return

	# Move toward nearest.
	if unit.call("can_move") as bool:
		var u_coords: Vector2i = unit.get("grid_coords") as Vector2i
		var u_move_range: int = unit.get("move_range") as int
		var reachable: Array = _grid.call("get_reachable_cells", u_coords, u_move_range) as Array
		var best_cell: Variant = null
		var best_dist: float = INF
		var n_coords: Vector2i = nearest.get("grid_coords") as Vector2i
		for cell: Variant in reachable:
			var c_coords: Vector2i = cell.get("coords") as Vector2i
			var d: float = float(maxi(absi(c_coords.x - n_coords.x), absi(c_coords.y - n_coords.y)))
			if d < best_dist:
				best_dist = d
				best_cell = cell

		if best_cell != null:
			var bc_coords: Vector2i = best_cell.get("coords") as Vector2i
			var path: Array = _grid.call("find_path", u_coords, bc_coords) as Array
			if not path.is_empty():
				unit.call("move_along_path", path)
				await unit.move_animation_finished
				# Try attack after move.
				if nearest.get("is_alive") as bool and unit.call("can_attack") as bool:
					var new_u_coords: Vector2i = unit.get("grid_coords") as Vector2i
					var new_n_coords: Vector2i = nearest.get("grid_coords") as Vector2i
					var attack_dist: int = maxi(
						absi(new_u_coords.x - new_n_coords.x),
						absi(new_u_coords.y - new_n_coords.y)
					)
					if attack_dist <= u_attack_range:
						unit.call("attack_target", nearest)
						await get_tree().create_timer(0.3).timeout
						if not nearest.get("is_alive") as bool:
							_grid.call("remove_unit_from_cell", new_n_coords)
							nearest.set("visible", false)

	_turn_manager.call("end_current_turn")
