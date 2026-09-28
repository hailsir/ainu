@tool
extends MeshInstance3D
class_name SnowDeformer
## Proxy volume that presses snow down (see plans/SNOWPLAN.md section 3).
##
## Add one as a child of anything that should leave a trail, then put the owner
## in the "snow_deformer" group if gameplay wants to find it later. The proxy
## renders only into SnowField's deform camera: the camera looks up from below,
## so ordinary depth testing keeps this mesh's underside, and the simulation
## takes min(current height, underside height).
##
## Nothing here is visible to gameplay cameras, because SnowField owns the
## render layer and the main cameras cull it.

## Footprint radius in metres.
@export var radius: float = 0.4:
	set(value):
		radius = maxf(value, 0.01)
		_rebuild_mesh()
## Vertical squash of the proxy. 1.0 is a sphere, lower gives a flatter print.
@export_range(0.05, 1.0, 0.01) var flatten: float = 0.5:
	set(value):
		flatten = clampf(value, 0.05, 1.0)
		_rebuild_mesh()
var _field: SnowField = null


func _ready() -> void:
	_rebuild_mesh()
	cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	gi_mode = GeometryInstance3D.GI_MODE_DISABLED
	_attach_to_field()


func _attach_to_field() -> void:
	_field = SnowField.find_field(self)
	if _field == null:
		# The field may be built later in the frame; retry once the tree settles.
		call_deferred("_attach_deferred")
		return
	layers = _field.deform_layer
	material_override = _field.get_deform_material()


func _attach_deferred() -> void:
	if _field != null or not is_inside_tree():
		return
	_field = SnowField.find_field(self)
	if _field == null:
		push_warning("SnowDeformer: no SnowField in the scene; this deformer is inert.")
		return
	layers = _field.deform_layer
	material_override = _field.get_deform_material()


func _rebuild_mesh() -> void:
	var sphere := mesh as SphereMesh
	if sphere == null:
		sphere = SphereMesh.new()
		sphere.radial_segments = 16
		sphere.rings = 8
		mesh = sphere
	sphere.radius = radius
	sphere.height = radius * 2.0 * flatten


## Lowest world-space Y this proxy presses to, useful for gameplay checks.
func get_underside_y() -> float:
	return global_position.y - radius * flatten
