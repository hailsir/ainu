@tool
extends Node3D
class_name SnowField
## Dynamic snow heightfield (see plans/SNOWPLAN.md).
##
## Owns the whole pipeline:
##   - a ping-pong pair of HDR SubViewports holding the runtime state
##     (R = height in metres, G = freshness),
##   - an orthographic deform camera looking up from under the ground plane,
##   - a small downsampled SubViewport used for CPU readback and gameplay queries.
##
## The node's global XZ is the centre of the field and its global Y is the
## ground plane. Everything else keys off world_to_uv().

## Emitted after each CPU readback completes, so gameplay can react on the same
## cadence the readback runs at.
signal readback_updated()

const SIM_SHADER := preload("res://shaders/snow_sim.gdshader")
const DEFORM_SHADER := preload("res://shaders/snow_deform_encode.gdshader")
const READBACK_SHADER := preload("res://shaders/snow_readback.gdshader")
const SHELTER_SHADER := preload("res://shaders/snow_shelter.gdshader")
const DEFORMER_GROUP := "snow_deformer"
const FIELD_GROUP := "snow_field"
const SAVE_MAGIC := "SNOW"
const SAVE_VERSION := 1

@export_group("Field")
## Side length of the square field in metres.
@export var field_size: float = 120.0
## Heightfield resolution. 1200 over 120 m is 10 cm per texel.
@export var resolution: int = 1200
## Height that a stored value of 1.0 maps to, in metres.
@export var max_depth: float = 2.0
## R = BaseDepth, G = Capacity, B = Compaction, A = ShelterFactor, all 0..1.
@export var authored_maps: Texture2D
## Ground mesh whose material gets the snow shader driven by this field.
@export var ground_mesh_path: NodePath
## Render layer used by SnowDeformer proxy meshes. Keep it off every gameplay camera.
@export_flags_3d_render var deform_layer: int = 1 << 11

@export_group("Wind")
@export var wind_enabled: bool = true
## Compass-style direction the wind blows toward, in degrees around +X.
@export_range(0.0, 360.0, 1.0) var wind_direction_deg: float = 35.0
@export_range(0.0, 4.0, 0.01) var wind_strength: float = 1.0
## Peak refill rate in a blizzard, before shelter and freshness scaling.
@export var base_rate_cm_per_min: float = 2.0
@export var snowfall: bool = false
@export var snowfall_cm_per_min: float = 5.0
## Seconds between wind passes. The plan calls for 0.25 - 1 s, not per frame.
@export_range(0.05, 4.0, 0.05) var accum_interval: float = 0.5
@export_range(0.0, 1.0, 0.01) var slope_wind_influence: float = 0.8
## How much faster a fresh trail refills than pristine ground.
@export_range(0.0, 1.0, 0.01) var pristine_growth: float = 0.35
## Seconds for a print to read as pristine again (drives the material blend).
@export var freshness_recovery_seconds: float = 50.0

@export_group("Shelter")
## Re-render the shelter factor on the GPU for the live wind direction instead
## of reading the single direction baked into authored_maps' alpha channel.
@export var use_dynamic_shelter: bool = true
## Resolution of the shelter pass. Matches SnowMapsBaker.SHELTER_RESOLUTION's
## intent: coarse enough to be cheap, fine enough to resolve a lee pocket.
@export var shelter_resolution: int = 256
## Degrees the wind must turn before the shelter pass is re-rendered.
@export_range(0.5, 45.0, 0.5) var shelter_redraw_threshold_deg: float = 3.0
## Extra node names excluded when collecting wind-blocking obstacles.
@export var obstacle_skip_names: PackedStringArray = PackedStringArray()
## How much of the shelter pattern falling snow follows, per unit wind strength.
@export_range(0.0, 2.0, 0.01) var snowfall_wind_bias_per_strength: float = 0.35
@export_range(0.0, 1.0, 0.01) var snowfall_wind_bias_max: float = 0.7

@export_group("Deformation")
## World-space band above the ground the deform camera can resolve.
@export var deform_range: float = 2.0
## Height added to texels ringing a print, per frame, for a displaced-snow rim.
@export var rim_rate: float = 0.002

@export_group("Gameplay")
## Resolution of the CPU-readable copy. 120 over 120 m is 1 m per cell.
@export var readback_resolution: int = 120
@export var readback_interval: float = 0.5

@export_group("Editor")
## Run a few simulation passes in the editor so the authored maps are visible.
@export var preview_in_editor: bool = true

var _sim_viewport: Array[SubViewport] = []
var _sim_material: Array[ShaderMaterial] = []
var _cur: int = 0
var _deform_viewport: SubViewport = null
var _deform_camera: Camera3D = null
var _readback_viewport: SubViewport = null
var _readback_rect: ColorRect = null
var _readback_material: ShaderMaterial = null
var _deform_material: ShaderMaterial = null
var _shelter_viewport: SubViewport = null
var _shelter_material: ShaderMaterial = null
var _shelter_mask: ImageTexture = null
var _shelter_rendered_deg: float = INF

## Number of shelter re-renders since startup. Probe-visible so a test can show
## the pass fires on a direction change and not on jitter.
var shelter_pass_count: int = 0

var _ground_material: ShaderMaterial = null
var _built: bool = false
var _pending_init_mode: int = 1
var _restore_texture: Texture2D = null
var _accum_timer: float = 0.0
var _readback_timer: float = 0.0
var _readback_pending: bool = false
var _readback_image: Image = null
var _maps_image: Image = null
var _editor_frames_left: int = 0


func _ready() -> void:
	add_to_group(FIELD_GROUP)
	_build()
	# Deferred so the rest of the world scene is in the tree to be collected.
	call_deferred("rebuild_obstacle_mask")
	if Engine.is_editor_hint():
		_editor_frames_left = 4 if preview_in_editor else 0


## Nearest active snow field, or null when the scene has none.
static func find_field(from: Node) -> SnowField:
	if from == null or not from.is_inside_tree():
		return null
	var found := from.get_tree().get_first_node_in_group(FIELD_GROUP)
	return found as SnowField


## Keeps SnowDeformer proxy meshes out of a gameplay camera. Call this for any
## camera created at runtime; authored cameras have the bit cleared in-scene.
static func hide_deformers_from(camera: Camera3D) -> void:
	if camera == null:
		return
	var field := find_field(camera)
	var layer := field.deform_layer if field else (1 << 11)
	camera.cull_mask &= ~layer


func _build() -> void:
	if _built:
		return
	resolution = maxi(resolution, 16)
	readback_resolution = maxi(readback_resolution, 8)

	_cache_maps_image()
	_build_sim_viewports()
	_build_deform_viewport()
	_build_readback_viewport()
	_build_shelter_viewport()
	_bind_ground_material()
	_built = true
	_pending_init_mode = 1


func _cache_maps_image() -> void:
	_maps_image = null
	if authored_maps:
		_maps_image = authored_maps.get_image()


func _make_viewport(vp_size: int, name_hint: String) -> SubViewport:
	var vp := SubViewport.new()
	vp.name = name_hint
	vp.size = Vector2i(vp_size, vp_size)
	vp.use_hdr_2d = true
	vp.disable_3d = true
	vp.transparent_bg = false
	vp.gui_disable_input = true
	vp.render_target_clear_mode = SubViewport.CLEAR_MODE_ALWAYS
	vp.render_target_update_mode = SubViewport.UPDATE_DISABLED
	add_child(vp)
	return vp


func _build_sim_viewports() -> void:
	_sim_viewport.clear()
	_sim_material.clear()
	for i in 2:
		var vp := _make_viewport(resolution, "SnowSim%d" % i)
		var mat := ShaderMaterial.new()
		mat.shader = SIM_SHADER
		var rect := ColorRect.new()
		rect.name = "SimRect"
		rect.size = Vector2(resolution, resolution)
		rect.position = Vector2.ZERO
		rect.color = Color.WHITE
		rect.material = mat
		rect.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
		vp.add_child(rect)
		_sim_viewport.append(vp)
		_sim_material.append(mat)


func _build_deform_viewport() -> void:
	_deform_viewport = SubViewport.new()
	_deform_viewport.name = "SnowDeform"
	_deform_viewport.size = Vector2i(resolution, resolution)
	_deform_viewport.own_world_3d = false
	_deform_viewport.transparent_bg = true
	_deform_viewport.gui_disable_input = true
	_deform_viewport.msaa_3d = Viewport.MSAA_DISABLED
	_deform_viewport.positional_shadow_atlas_size = 0
	_deform_viewport.render_target_clear_mode = SubViewport.CLEAR_MODE_ALWAYS
	_deform_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	add_child(_deform_viewport)

	_deform_camera = Camera3D.new()
	_deform_camera.name = "DeformCamera"
	_deform_camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	_deform_camera.size = field_size
	_deform_camera.near = 0.05
	_deform_camera.far = deform_range + 2.0
	_deform_camera.cull_mask = deform_layer
	_deform_camera.current = true

	# A flat clear so nothing but deformers contributes; alpha 0 means "no
	# deformer here" to the simulation pass.
	var env := Environment.new()
	env.background_mode = Environment.BG_CLEAR_COLOR
	env.ambient_light_source = Environment.AMBIENT_SOURCE_DISABLED
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.tonemap_exposure = 1.0
	env.tonemap_white = 1.0
	_deform_camera.environment = env

	_deform_viewport.add_child(_deform_camera)
	_position_deform_camera()

	_deform_material = ShaderMaterial.new()
	_deform_material.shader = DEFORM_SHADER


func _position_deform_camera() -> void:
	if _deform_camera == null:
		return
	# Sits 1 m under the ground plane looking straight up (+Y). Depth testing
	# then keeps the lowest surface of each deformer, which is its underside.
	var centre := global_position
	_deform_camera.global_position = Vector3(centre.x, centre.y - 1.0, centre.z)
	_deform_camera.global_rotation = Vector3(deg_to_rad(90.0), 0.0, 0.0)
	_deform_camera.size = field_size
	_deform_camera.far = deform_range + 2.0
	_deform_camera.cull_mask = deform_layer


func _build_readback_viewport() -> void:
	_readback_viewport = _make_viewport(readback_resolution, "SnowReadback")
	_readback_material = ShaderMaterial.new()
	_readback_material.shader = READBACK_SHADER
	_readback_material.set_shader_parameter("cell_uv", 1.0 / float(readback_resolution))
	_readback_rect = ColorRect.new()
	_readback_rect.name = "ReadbackRect"
	_readback_rect.size = Vector2(readback_resolution, readback_resolution)
	_readback_rect.color = Color.WHITE
	_readback_rect.material = _readback_material
	_readback_viewport.add_child(_readback_rect)


func _build_shelter_viewport() -> void:
	shelter_resolution = maxi(shelter_resolution, 32)
	_shelter_viewport = _make_viewport(shelter_resolution, "SnowShelter")
	_shelter_material = ShaderMaterial.new()
	_shelter_material.shader = SHELTER_SHADER
	_shelter_material.set_shader_parameter("open_exposure", SnowMapsBaker.OPEN_EXPOSURE)
	_shelter_material.set_shader_parameter("lee_shelter", SnowMapsBaker.LEE_SHELTER)
	_shelter_material.set_shader_parameter("windward_shelter", SnowMapsBaker.WINDWARD_SHELTER)
	_shelter_material.set_shader_parameter("lee_distance_m", SnowMapsBaker.LEE_DISTANCE_M)
	_shelter_material.set_shader_parameter("windward_distance_m", SnowMapsBaker.WINDWARD_DISTANCE_M)

	var rect := ColorRect.new()
	rect.name = "ShelterRect"
	rect.size = Vector2(shelter_resolution, shelter_resolution)
	rect.position = Vector2.ZERO
	rect.color = Color.WHITE
	rect.material = _shelter_material
	rect.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_shelter_viewport.add_child(rect)


## Rasterises the scene's static geometry into the mask the shelter pass marches
## against. Call after moving, adding or removing wind-blocking obstacles.
func rebuild_obstacle_mask(root: Node = null) -> void:
	if _shelter_material == null:
		return
	if root == null:
		root = owner if owner != null else get_parent()
	if root == null:
		return

	var skip := PackedStringArray([
		"Ground", "Player", "CameraRig", "SnowField",
		"TacticalCombat", "SnowPrecipitation", "SnowWeather",
	])
	for extra in obstacle_skip_names:
		if not skip.has(extra):
			skip.append(extra)

	var res := SnowMapsBaker.SHELTER_RESOLUTION
	var obstacles := SnowMapsBaker.collect_obstacles(root, skip)
	var centre := Vector2(global_position.x, global_position.z)
	var mask := SnowMapsBaker.rasterise_mask(obstacles, centre, field_size, res)
	_shelter_mask = ImageTexture.create_from_image(SnowMapsBaker.mask_to_image(mask, res))

	_shelter_material.set_shader_parameter("obstacle_mask", _shelter_mask)
	_shelter_material.set_shader_parameter("field_size", field_size)
	_shelter_material.set_shader_parameter("mask_resolution", res)
	# The mask changed, so the cached direction no longer describes what was
	# rendered; force the next step to redraw.
	_shelter_rendered_deg = INF


## Re-renders the shelter pass when the wind has turned far enough to matter.
## The march is ~40 texture fetches per texel, so redrawing every frame would be
## wasteful for a direction that moves a few degrees an hour.
func _update_shelter_pass() -> void:
	if not use_dynamic_shelter or _shelter_viewport == null or _shelter_mask == null:
		return
	var turned := absf(rad_to_deg(angle_difference(
		deg_to_rad(_shelter_rendered_deg), deg_to_rad(wind_direction_deg))))
	if is_finite(_shelter_rendered_deg) and turned < shelter_redraw_threshold_deg:
		return
	_shelter_material.set_shader_parameter("wind_dir", _wind_vector())
	_shelter_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	_shelter_rendered_deg = wind_direction_deg
	shelter_pass_count += 1


func _bind_ground_material() -> void:
	_ground_material = null
	if ground_mesh_path.is_empty():
		return
	var mesh_node := get_node_or_null(ground_mesh_path) as MeshInstance3D
	if mesh_node == null:
		push_warning("SnowField: ground_mesh_path does not resolve to a MeshInstance3D.")
		return
	var mat := mesh_node.get_active_material(0)
	if not (mat is ShaderMaterial):
		push_warning("SnowField: ground mesh has no ShaderMaterial; displacement is off.")
		return
	_ground_material = mat as ShaderMaterial

	# Tell the shader how wide one mesh quad is, so vertex displacement can
	# average over its own footprint rather than point-sampling the field.
	var plane := mesh_node.mesh as PlaneMesh
	if plane:
		var quads := float(maxi(plane.subdivide_width, 0) + 1)
		var spacing := plane.size.x / quads
		_ground_material.set_shader_parameter("vertex_footprint_uv", spacing / field_size)


func _process(delta: float) -> void:
	if not _built:
		return
	if Engine.is_editor_hint():
		if _editor_frames_left > 0:
			_editor_frames_left -= 1
			_position_deform_camera()
			_step(0.0)
		return

	_position_deform_camera()
	_deform_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE

	var accum_dt := 0.0
	_accum_timer += delta
	if wind_enabled and _accum_timer >= accum_interval:
		accum_dt = _accum_timer
		_accum_timer = 0.0
	_step(accum_dt)

	_readback_timer += delta
	if _readback_timer >= readback_interval and not _readback_pending:
		_readback_timer = 0.0
		_run_readback()


## Runs one simulation pass into the free half of the ping-pong pair.
func _step(accum_dt: float) -> void:
	_update_shelter_pass()

	var write_idx := 1 - _cur
	var mat := _sim_material[write_idx]
	var texel := 1.0 / float(resolution)

	mat.set_shader_parameter("prev_state", _sim_viewport[_cur].get_texture())
	mat.set_shader_parameter("authored_maps", authored_maps)
	mat.set_shader_parameter("deform_tex", _deform_viewport.get_texture())
	mat.set_shader_parameter("restore_tex", _restore_texture)
	mat.set_shader_parameter("init_mode", _pending_init_mode)
	mat.set_shader_parameter("max_depth", max_depth)
	mat.set_shader_parameter("cell_size", field_size / float(resolution))
	mat.set_shader_parameter("texel", texel)
	mat.set_shader_parameter("deform_range", deform_range)
	mat.set_shader_parameter("accum_dt", accum_dt)
	mat.set_shader_parameter("base_rate", base_rate_cm_per_min * 0.01 / 60.0)
	mat.set_shader_parameter("snowfall_rate", (snowfall_cm_per_min * 0.01 / 60.0) if snowfall else 0.0)
	mat.set_shader_parameter("wind_strength", wind_strength)
	mat.set_shader_parameter("wind_dir", _wind_vector())
	mat.set_shader_parameter("slope_wind_influence", slope_wind_influence)
	mat.set_shader_parameter("pristine_growth", pristine_growth)
	mat.set_shader_parameter("shelter_tex", get_shelter_texture())
	mat.set_shader_parameter("use_dynamic_shelter", use_dynamic_shelter and _shelter_mask != null)
	mat.set_shader_parameter("snowfall_wind_bias", clampf(
		wind_strength * snowfall_wind_bias_per_strength, 0.0, snowfall_wind_bias_max))
	mat.set_shader_parameter("freshness_recovery", 1.0 / maxf(freshness_recovery_seconds, 0.01))
	mat.set_shader_parameter("rim_rate", rim_rate)

	_sim_viewport[write_idx].render_target_update_mode = SubViewport.UPDATE_ONCE
	_cur = write_idx
	if _pending_init_mode != 0:
		_pending_init_mode = 0
		_restore_texture = null

	_publish_state()


func _publish_state() -> void:
	var state := _sim_viewport[_cur].get_texture()
	if _ground_material:
		_ground_material.set_shader_parameter("snow_state", state)
		_ground_material.set_shader_parameter("field_center", Vector2(global_position.x, global_position.z))
		_ground_material.set_shader_parameter("field_size", field_size)
		_ground_material.set_shader_parameter("texel", 1.0 / float(resolution))
	if _readback_material:
		_readback_material.set_shader_parameter("snow_state", state)


func _wind_vector() -> Vector2:
	var a := deg_to_rad(wind_direction_deg)
	return Vector2(cos(a), sin(a))


func _run_readback() -> void:
	_readback_pending = true
	_readback_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	await RenderingServer.frame_post_draw
	_readback_pending = false
	if not is_inside_tree():
		return
	_readback_image = _readback_viewport.get_texture().get_image()
	readback_updated.emit()


# --- public query API (SNOWPLAN section 5) ------------------------------------

## Normalised field coordinates for a world position. Outside 0..1 means off-field.
func world_to_uv(world_pos: Vector3) -> Vector2:
	var half := field_size * 0.5
	var origin := Vector2(global_position.x - half, global_position.z - half)
	return (Vector2(world_pos.x, world_pos.z) - origin) / field_size


func is_inside_field(world_pos: Vector3) -> bool:
	var uv := world_to_uv(world_pos)
	return uv.x >= 0.0 and uv.x <= 1.0 and uv.y >= 0.0 and uv.y <= 1.0


## Snow depth in metres above the ground plane. Falls back to the authored base
## depth until the first GPU readback lands.
func get_snow_depth(world_pos: Vector3) -> float:
	var uv := world_to_uv(world_pos)
	if uv.x < 0.0 or uv.x > 1.0 or uv.y < 0.0 or uv.y > 1.0:
		return 0.0
	if _readback_image != null:
		return _sample_image(_readback_image, uv).r
	if _maps_image != null:
		return _sample_image(_maps_image, uv).r * max_depth
	return 0.0


## World-space Y of the snow surface at a position.
func get_snow_surface_y(world_pos: Vector3) -> float:
	return global_position.y + get_snow_depth(world_pos)


## Surface normal of the snow, from the 1 m readback grid.
func get_snow_normal(world_pos: Vector3) -> Vector3:
	if _readback_image == null:
		return Vector3.UP
	var step := field_size / float(readback_resolution)
	var dx := get_snow_depth(world_pos + Vector3(step, 0.0, 0.0)) - get_snow_depth(world_pos - Vector3(step, 0.0, 0.0))
	var dz := get_snow_depth(world_pos + Vector3(0.0, 0.0, step)) - get_snow_depth(world_pos - Vector3(0.0, 0.0, step))
	return Vector3(-dx, 2.0 * step, -dz).normalized()


## Authored values at a position: x = base depth (m), y = capacity (m),
## z = compaction fraction, w = shelter factor.
func get_authored(world_pos: Vector3) -> Vector4:
	if _maps_image == null:
		return Vector4(0.0, 0.0, 0.0, 1.0)
	var uv := world_to_uv(world_pos)
	if uv.x < 0.0 or uv.x > 1.0 or uv.y < 0.0 or uv.y > 1.0:
		return Vector4(0.0, 0.0, 0.0, 1.0)
	var c := _sample_image(_maps_image, uv)
	return Vector4(c.r * max_depth, c.g * max_depth, c.b, c.a)


## Bilinear sample, so gameplay queries do not stair-step across readback cells
## or across the lower-resolution authored maps.
func _sample_image(img: Image, uv: Vector2) -> Color:
	var w := img.get_width()
	var h := img.get_height()
	var fx := uv.x * float(w) - 0.5
	var fy := uv.y * float(h) - 0.5
	var x0 := clampi(int(floor(fx)), 0, w - 1)
	var y0 := clampi(int(floor(fy)), 0, h - 1)
	var x1 := mini(x0 + 1, w - 1)
	var y1 := mini(y0 + 1, h - 1)
	var tx := clampf(fx - floor(fx), 0.0, 1.0)
	var ty := clampf(fy - floor(fy), 0.0, 1.0)
	var top := img.get_pixel(x0, y0).lerp(img.get_pixel(x1, y0), tx)
	var bottom := img.get_pixel(x0, y1).lerp(img.get_pixel(x1, y1), tx)
	return top.lerp(bottom, ty)


## Material worn by SnowDeformer proxy meshes. Shared, so range changes apply
## to every deformer at once.
func get_deform_material() -> ShaderMaterial:
	if _deform_material:
		_deform_material.set_shader_parameter("ground_y", global_position.y)
		_deform_material.set_shader_parameter("deform_range", deform_range)
	return _deform_material


## Live state texture: R = height in metres, G = freshness.
func get_state_texture() -> Texture2D:
	if not _built:
		return null
	return _sim_viewport[_cur].get_texture()


## Live shelter factor for the current wind direction, in R. Null until the
## obstacle mask has been built.
func get_shelter_texture() -> Texture2D:
	if _shelter_viewport == null:
		return null
	return _shelter_viewport.get_texture()


func set_wind(direction_deg: float, strength: float) -> void:
	wind_direction_deg = direction_deg
	wind_strength = strength


## Sets both the snowfall flag and its rate from one value, so callers driving
## the field from a weather model do not have to keep the two in step.
func set_snowfall(cm_per_min: float) -> void:
	snowfall_cm_per_min = maxf(cm_per_min, 0.0)
	snowfall = snowfall_cm_per_min > 0.0


## World-space unit vector the wind blows toward.
func get_wind_vector3() -> Vector3:
	var v := _wind_vector()
	return Vector3(v.x, 0.0, v.y)


## Discards runtime state and re-seeds from the authored maps.
func reset_field() -> void:
	_pending_init_mode = 1
	_readback_image = null


# --- persistence (SNOWPLAN section 6) -----------------------------------------

## Writes the runtime height and freshness channels to a zstd-compressed file.
func save_state(path: String = "user://snow_state.snow") -> bool:
	if not _built:
		return false
	var img := _sim_viewport[_cur].get_texture().get_image()
	if img == null:
		push_warning("SnowField: could not read state texture for save.")
		return false
	var file := FileAccess.open_compressed(path, FileAccess.WRITE, FileAccess.COMPRESSION_ZSTD)
	if file == null:
		push_warning("SnowField: could not open %s for writing." % path)
		return false
	file.store_string(SAVE_MAGIC)
	file.store_32(SAVE_VERSION)
	file.store_32(img.get_width())
	file.store_32(img.get_height())
	file.store_32(int(img.get_format()))
	var data := img.get_data()
	file.store_32(data.size())
	file.store_buffer(data)
	file.close()
	return true


## Restores a saved field. The next simulation pass seeds from it, clamped back
## into the authored compaction floor and capacity.
func load_state(path: String = "user://snow_state.snow") -> bool:
	if not _built:
		return false
	if not FileAccess.file_exists(path):
		return false
	var file := FileAccess.open_compressed(path, FileAccess.READ, FileAccess.COMPRESSION_ZSTD)
	if file == null:
		return false
	var magic := file.get_buffer(SAVE_MAGIC.length()).get_string_from_ascii()
	if magic != SAVE_MAGIC:
		push_warning("SnowField: %s is not a snow state file." % path)
		return false
	var version := file.get_32()
	if version != SAVE_VERSION:
		push_warning("SnowField: unsupported snow state version %d." % version)
		return false
	var w := file.get_32()
	var h := file.get_32()
	var fmt := file.get_32()
	var byte_count := file.get_32()
	var data := file.get_buffer(byte_count)
	file.close()
	if data.size() != byte_count:
		push_warning("SnowField: snow state file is truncated.")
		return false

	var img := Image.create_from_data(w, h, false, fmt as Image.Format, data)
	if img == null:
		return false
	_restore_texture = ImageTexture.create_from_image(img)
	_pending_init_mode = 2
	_readback_image = null
	return true
