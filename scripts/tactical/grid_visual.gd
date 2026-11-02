class_name GridVisual
extends Node3D
## Renders the tactical grid using MeshInstance3D nodes.
## Shows grid lines, highlights reachable cells, attack range, etc.

@export var grid: TacticalGrid = null

@export_group("Colors")
@export var grid_line_color: Color = Color(0.5, 0.5, 0.5, 0.3)
@export var reachable_color: Color = Color(0.2, 0.6, 1.0, 0.35)
@export var attack_range_color: Color = Color(1.0, 0.2, 0.2, 0.3)
@export var selected_color: Color = Color(1.0, 0.9, 0.2, 0.5)
@export var path_color: Color = Color(0.2, 1.0, 0.4, 0.5)

## Height offset above ground to prevent z-fighting.
const CELL_Y_OFFSET: float = 0.02

var _cell_meshes: Dictionary = {}  # Vector2i -> MeshInstance3D
var _grid_lines_mesh: MeshInstance3D = null
var _highlighted_cells: Dictionary = {}  # Vector2i -> Color


func _ready() -> void:
	if grid == null:
		grid = get_parent() as TacticalGrid
	if grid:
		# Wait one frame for grid to generate.
		await get_tree().process_frame
		_create_grid_lines()


func _create_grid_lines() -> void:
	if _grid_lines_mesh:
		_grid_lines_mesh.queue_free()

	var im: ImmediateMesh = ImmediateMesh.new()
	var mat: StandardMaterial3D = StandardMaterial3D.new()
	mat.albedo_color = grid_line_color
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.no_depth_test = true

	im.surface_begin(Mesh.PRIMITIVE_LINES, mat)

	var origin: Vector3 = grid.grid_origin
	var cs: float = grid.cell_size
	var y: float = origin.y + CELL_Y_OFFSET

	# Horizontal lines.
	for row: int in range(grid.grid_height + 1):
		var z: float = origin.z + row * cs
		im.surface_add_vertex(Vector3(origin.x, y, z))
		im.surface_add_vertex(Vector3(origin.x + grid.grid_width * cs, y, z))

	# Vertical lines.
	for col: int in range(grid.grid_width + 1):
		var x: float = origin.x + col * cs
		im.surface_add_vertex(Vector3(x, y, origin.z))
		im.surface_add_vertex(Vector3(x, y, origin.z + grid.grid_height * cs))

	im.surface_end()

	_grid_lines_mesh = MeshInstance3D.new()
	_grid_lines_mesh.name = "GridLines"
	_grid_lines_mesh.mesh = im
	add_child(_grid_lines_mesh)


## Highlight a set of cells with a given color.
func highlight_cells(cell_list: Array[GridCell], color: Color) -> void:
	for cell: GridCell in cell_list:
		_set_cell_highlight(cell.coords, color)


## Clear all cell highlights.
func clear_highlights() -> void:
	for coords: Vector2i in _cell_meshes:
		var mesh_inst: MeshInstance3D = _cell_meshes[coords] as MeshInstance3D
		if mesh_inst and is_instance_valid(mesh_inst):
			mesh_inst.queue_free()
	_cell_meshes.clear()
	_highlighted_cells.clear()


## Highlight reachable cells for movement.
func show_reachable(cells_list: Array[GridCell]) -> void:
	highlight_cells(cells_list, reachable_color)


## Highlight attack range cells.
func show_attack_range(cells_list: Array[GridCell]) -> void:
	highlight_cells(cells_list, attack_range_color)


## Highlight a path.
func show_path(path_cells: Array[GridCell]) -> void:
	highlight_cells(path_cells, path_color)


## Highlight a single selected cell.
func show_selected(cell: GridCell) -> void:
	_set_cell_highlight(cell.coords, selected_color)


func _set_cell_highlight(coords: Vector2i, color: Color) -> void:
	if grid == null:
		return
	# Remove existing highlight for this cell.
	if _cell_meshes.has(coords):
		var old_mesh: MeshInstance3D = _cell_meshes[coords] as MeshInstance3D
		if old_mesh and is_instance_valid(old_mesh):
			old_mesh.queue_free()

	var cell: GridCell = grid.get_cell(coords)
	if cell == null:
		return

	var mesh_inst: MeshInstance3D = MeshInstance3D.new()
	mesh_inst.name = "CellHighlight_%d_%d" % [coords.x, coords.y]

	var quad: PlaneMesh = PlaneMesh.new()
	quad.size = Vector2(grid.cell_size * 0.9, grid.cell_size * 0.9)
	mesh_inst.mesh = quad

	var mat: StandardMaterial3D = StandardMaterial3D.new()
	mat.albedo_color = color
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.no_depth_test = true
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mesh_inst.set_surface_override_material(0, mat)

	mesh_inst.position = Vector3(
		cell.world_position.x,
		cell.world_position.y + CELL_Y_OFFSET,
		cell.world_position.z
	)

	add_child(mesh_inst)
	_cell_meshes[coords] = mesh_inst
	_highlighted_cells[coords] = color


## Show/hide the entire grid visual.
func set_grid_visible(visible_flag: bool) -> void:
	self.visible = visible_flag
