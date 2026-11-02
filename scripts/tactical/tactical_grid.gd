class_name TacticalGrid
extends Node3D
## Square grid for tactical combat. Handles grid data, world<->grid conversion,
## pathfinding (A*), and range queries. Supports 8-directional movement.

signal cell_hovered(cell: GridCell)
signal cell_clicked(cell: GridCell)

@export var grid_width: int = 12
@export var grid_height: int = 12
@export var cell_size: float = 1.5
## Offset from world origin to the grid corner (0,0).
@export var grid_origin: Vector3 = Vector3.ZERO

## 2D array stored as Dictionary[Vector2i, GridCell].
var cells: Dictionary = {}

## 8-directional neighbor offsets.
const DIRECTIONS_8: Array[Vector2i] = [
	Vector2i(0, -1),   # N
	Vector2i(1, -1),   # NE
	Vector2i(1, 0),    # E
	Vector2i(1, 1),    # SE
	Vector2i(0, 1),    # S
	Vector2i(-1, 1),   # SW
	Vector2i(-1, 0),   # W
	Vector2i(-1, -1),  # NW
]

## Diagonal cost multiplier (sqrt(2) ≈ 1.414).
const DIAGONAL_COST: float = 1.414


func _ready() -> void:
	_generate_grid()


func _generate_grid() -> void:
	cells.clear()
	for x: int in range(grid_width):
		for y: int in range(grid_height):
			var cell: GridCell = GridCell.new()
			cell.coords = Vector2i(x, y)
			cell.world_position = grid_to_world(Vector2i(x, y))
			cells[Vector2i(x, y)] = cell


## Convert grid coordinates to world position (center of cell).
func grid_to_world(coords: Vector2i) -> Vector3:
	var wx: float = grid_origin.x + (coords.x + 0.5) * cell_size
	var wz: float = grid_origin.z + (coords.y + 0.5) * cell_size
	return Vector3(wx, grid_origin.y, wz)


## Convert world position to grid coordinates.
func world_to_grid(world_pos: Vector3) -> Vector2i:
	var gx: int = floori((world_pos.x - grid_origin.x) / cell_size)
	var gy: int = floori((world_pos.z - grid_origin.z) / cell_size)
	return Vector2i(gx, gy)


## Check if coordinates are within grid bounds.
func is_valid(coords: Vector2i) -> bool:
	return coords.x >= 0 and coords.x < grid_width and coords.y >= 0 and coords.y < grid_height


## Get cell at coordinates, or null if out of bounds.
func get_cell(coords: Vector2i) -> GridCell:
	return cells.get(coords, null) as GridCell


## Get all 8-directional neighbors of a cell.
func get_neighbors(coords: Vector2i) -> Array[GridCell]:
	var result: Array[GridCell] = []
	for dir: Vector2i in DIRECTIONS_8:
		var neighbor_coords: Vector2i = coords + dir
		var cell: GridCell = get_cell(neighbor_coords)
		if cell != null:
			result.append(cell)
	return result


## Check if movement between two adjacent cells is valid for diagonal movement.
## Prevents corner-cutting through blocked cells.
func _can_move_diagonal(from_coords: Vector2i, to_coords: Vector2i) -> bool:
	var diff: Vector2i = to_coords - from_coords
	if abs(diff.x) == 1 and abs(diff.y) == 1:
		# Check the two orthogonal neighbors to prevent corner-cutting.
		var side_a: GridCell = get_cell(Vector2i(from_coords.x + diff.x, from_coords.y))
		var side_b: GridCell = get_cell(Vector2i(from_coords.x, from_coords.y + diff.y))
		if side_a == null or not side_a.walkable:
			return false
		if side_b == null or not side_b.walkable:
			return false
	return true


## A* pathfinding from start to end. Returns array of GridCell (excluding start).
## Returns empty array if no path found.
func find_path(start: Vector2i, end: Vector2i) -> Array[GridCell]:
	var start_cell: GridCell = get_cell(start)
	var end_cell: GridCell = get_cell(end)
	if start_cell == null or end_cell == null or not end_cell.walkable:
		return []

	var open_set: Array[Vector2i] = [start]
	var came_from: Dictionary = {}  # Vector2i -> Vector2i
	var g_score: Dictionary = {start: 0.0}  # Vector2i -> float
	var f_score: Dictionary = {start: _heuristic(start, end)}  # Vector2i -> float
	var closed_set: Dictionary = {}  # Vector2i -> bool

	while open_set.size() > 0:
		# Find node in open set with lowest f_score.
		var current: Vector2i = open_set[0]
		var current_f: float = f_score.get(current, INF) as float
		for candidate: Vector2i in open_set:
			var candidate_f: float = f_score.get(candidate, INF) as float
			if candidate_f < current_f:
				current = candidate
				current_f = candidate_f

		if current == end:
			return _reconstruct_path(came_from, current)

		open_set.erase(current)
		closed_set[current] = true

		for dir: Vector2i in DIRECTIONS_8:
			var neighbor_coords: Vector2i = current + dir
			if closed_set.has(neighbor_coords):
				continue
			var neighbor_cell: GridCell = get_cell(neighbor_coords)
			if neighbor_cell == null or not neighbor_cell.is_passable():
				continue
			if not _can_move_diagonal(current, neighbor_coords):
				continue

			var is_diagonal: bool = abs(dir.x) == 1 and abs(dir.y) == 1
			var step_cost: float = DIAGONAL_COST if is_diagonal else 1.0
			var tentative_g: float = (g_score.get(current, INF) as float) + step_cost * neighbor_cell.move_cost

			if tentative_g < (g_score.get(neighbor_coords, INF) as float):
				came_from[neighbor_coords] = current
				g_score[neighbor_coords] = tentative_g
				f_score[neighbor_coords] = tentative_g + _heuristic(neighbor_coords, end)
				if not open_set.has(neighbor_coords):
					open_set.append(neighbor_coords)

	return []  # No path found.


func _heuristic(a: Vector2i, b: Vector2i) -> float:
	# Chebyshev distance for 8-directional movement.
	var dx: int = absi(a.x - b.x)
	var dy: int = absi(a.y - b.y)
	return float(maxi(dx, dy)) + (DIAGONAL_COST - 1.0) * float(mini(dx, dy))


func _reconstruct_path(came_from: Dictionary, current: Vector2i) -> Array[GridCell]:
	var path: Array[GridCell] = []
	var node: Vector2i = current
	while came_from.has(node):
		var cell: GridCell = get_cell(node)
		if cell:
			path.push_front(cell)
		node = came_from[node] as Vector2i
	return path


## Get all cells reachable within a given movement range from start.
func get_reachable_cells(start: Vector2i, move_range: int) -> Array[GridCell]:
	var result: Array[GridCell] = []
	var visited: Dictionary = {start: 0.0}  # Vector2i -> float (cost to reach)
	var frontier: Array[Vector2i] = [start]

	while frontier.size() > 0:
		var current: Vector2i = frontier.pop_front()
		var current_cost: float = visited.get(current, 0.0) as float

		for dir: Vector2i in DIRECTIONS_8:
			var neighbor_coords: Vector2i = current + dir
			var neighbor_cell: GridCell = get_cell(neighbor_coords)
			if neighbor_cell == null or not neighbor_cell.is_passable():
				continue
			if not _can_move_diagonal(current, neighbor_coords):
				continue

			var is_diagonal: bool = abs(dir.x) == 1 and abs(dir.y) == 1
			var step_cost: float = DIAGONAL_COST if is_diagonal else 1.0
			var total_cost: float = current_cost + step_cost * neighbor_cell.move_cost

			if total_cost <= float(move_range) and total_cost < (visited.get(neighbor_coords, INF) as float):
				visited[neighbor_coords] = total_cost
				frontier.append(neighbor_coords)

	# Convert to cells (exclude start).
	for coords: Vector2i in visited:
		if coords != start:
			var cell: GridCell = get_cell(coords)
			if cell:
				result.append(cell)
	return result


## Get all cells within attack range (line of sight not checked here).
func get_cells_in_range(center: Vector2i, attack_range: int) -> Array[GridCell]:
	var result: Array[GridCell] = []
	for x: int in range(-attack_range, attack_range + 1):
		for y: int in range(-attack_range, attack_range + 1):
			if x == 0 and y == 0:
				continue
			# Chebyshev distance for 8-directional.
			var dist: int = maxi(absi(x), absi(y))
			if dist <= attack_range:
				var coords: Vector2i = center + Vector2i(x, y)
				var cell: GridCell = get_cell(coords)
				if cell:
					result.append(cell)
	return result


## Place a unit on the grid at the given coordinates.
func place_unit(unit: Node3D, coords: Vector2i) -> bool:
	var cell: GridCell = get_cell(coords)
	if cell == null or cell.is_occupied():
		return false
	cell.occupant = unit
	unit.global_position = cell.world_position
	return true


## Remove a unit from its current cell.
func remove_unit_from_cell(coords: Vector2i) -> void:
	var cell: GridCell = get_cell(coords)
	if cell:
		cell.occupant = null


## Move a unit from one cell to another.
func move_unit(unit: Node3D, from: Vector2i, to: Vector2i) -> bool:
	var from_cell: GridCell = get_cell(from)
	var to_cell: GridCell = get_cell(to)
	if from_cell == null or to_cell == null:
		return false
	if not to_cell.is_passable():
		return false
	from_cell.occupant = null
	to_cell.occupant = unit
	return true
