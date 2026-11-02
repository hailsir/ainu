@tool
extends RefCounted
class_name SnowMapsBaker
## Generates the authored snow maps for SnowField (plans/SNOWPLAN.md task A)
## and bakes the shelter channel from static geometry (task D).
##
## Output is a single RGBA8 PNG so it stays paintable in any image editor:
##   R = BaseDepth    (0..1 of SnowField.max_depth)
##   G = Capacity     (0..1 of SnowField.max_depth)
##   B = Compaction   (minimum depth after a footstep, as a fraction of base)
##   A = ShelterFactor (wind refill multiplier; 1 = fully sheltered lee pocket)
##
## Repaint R/G/B freely; rerun bake_shelter_into() after moving static geometry.

const DEFAULT_PATH := "res://assets/snow/snow_maps.png"

## Terrain layout, in normalised field coordinates (0..1 across the field).
const VALLEY_CENTRE := Vector2(0.30, 0.66)
const VALLEY_RADIUS := 0.26
const VALLEY_DEPTH := 1.25
const RIDGE_OFFSET := -0.18
const RIDGE_WIDTH := 0.17
const RIDGE_DEPTH := 0.05

const OPEN_EXPOSURE := 0.55
const LEE_SHELTER := 1.0
const WINDWARD_SHELTER := 0.12
const LEE_DISTANCE_M := 12.0
const WINDWARD_DISTANCE_M := 4.0
const SHELTER_RESOLUTION := 128


## Paints BaseDepth, Capacity and Compaction. Shelter is left fully open;
## call bake_shelter_into() afterwards.
static func bake_maps(resolution: int, max_depth: float, noise_seed: int = 20260916) -> Image:
	var img := Image.create_empty(resolution, resolution, false, Image.FORMAT_RGBA8)

	var broad := FastNoiseLite.new()
	broad.seed = noise_seed
	broad.noise_type = FastNoiseLite.TYPE_SIMPLEX
	broad.frequency = 2.4

	var detail := FastNoiseLite.new()
	detail.seed = noise_seed + 77
	detail.noise_type = FastNoiseLite.TYPE_SIMPLEX
	detail.frequency = 9.0

	var inv := 1.0 / float(resolution)
	for y in resolution:
		var v := (float(y) + 0.5) * inv
		for x in resolution:
			var u := (float(x) + 0.5) * inv

			# Gently rolling open field.
			var n := broad.get_noise_2d(u, v) * 0.5 + 0.5
			var n2 := detail.get_noise_2d(u, v) * 0.5 + 0.5
			var depth: float = lerp(0.18, 0.55, n) + (n2 - 0.5) * 0.10

			# Drift valley: a soft basin of deep, soft snow.
			var valley := 1.0 - clampf(Vector2(u, v).distance_to(VALLEY_CENTRE) / VALLEY_RADIUS, 0.0, 1.0)
			depth += smoothstep(0.0, 1.0, valley) * VALLEY_DEPTH

			# Windswept ridge: a diagonal band scoured back to bare crust.
			var ridge_dist := absf((u - v) - RIDGE_OFFSET) / RIDGE_WIDTH
			var ridge := smoothstep(0.0, 1.0, 1.0 - clampf(ridge_dist, 0.0, 1.0))
			depth = lerp(depth, RIDGE_DEPTH, ridge)

			depth = clampf(depth, 0.02, max_depth)

			# Wind may build to 1.5x the painted depth, but not on the ridge.
			var capacity := clampf(depth * 1.5, 0.12, max_depth)
			capacity = lerp(capacity, minf(capacity, 0.14), ridge)

			# Windpacked crust compresses less than soft valley powder.
			var compaction: float = lerp(0.15, 0.40, ridge)

			img.set_pixel(x, y, Color(
				depth / max_depth,
				capacity / max_depth,
				compaction,
				1.0))
	return img


## Rasterises obstacle footprints into a 0/1 cell mask over the field. Marching
## against the mask keeps the bake near-instant instead of testing every AABB
## per step, and the same mask feeds the GPU shelter pass in SnowField.
static func rasterise_mask(
		obstacles: Array[AABB],
		field_centre: Vector2,
		field_size: float,
		res: int = SHELTER_RESOLUTION) -> PackedByteArray:
	var metres_per_cell := field_size / float(res)
	var origin := field_centre - Vector2(field_size, field_size) * 0.5
	var mask := PackedByteArray()
	mask.resize(res * res)
	mask.fill(0)
	for box in obstacles:
		var min_cell := ((Vector2(box.position.x, box.position.z) - origin) / metres_per_cell).floor()
		var max_cell := ((Vector2(box.end.x, box.end.z) - origin) / metres_per_cell).ceil()
		for cy in range(maxi(int(min_cell.y), 0), mini(int(max_cell.y), res)):
			for cx in range(maxi(int(min_cell.x), 0), mini(int(max_cell.x), res)):
				mask[cy * res + cx] = 1
	return mask


## Single-channel image of a mask from rasterise_mask(), for upload to the GPU.
static func mask_to_image(mask: PackedByteArray, res: int) -> Image:
	var bytes := PackedByteArray()
	bytes.resize(res * res)
	for i in mask.size():
		bytes[i] = 255 if mask[i] == 1 else 0
	return Image.create_from_data(res, res, false, Image.FORMAT_R8, bytes)


## Writes the shelter factor into the alpha channel of an existing maps image.
## obstacles are world-space AABBs of the static geometry that blocks wind.
##
## This CPU march is the offline bake and the fallback used when SnowField has
## use_dynamic_shelter off; the runtime path is shaders/snow_shelter.gdshader,
## which mirrors this math on the GPU for the live wind direction.
static func bake_shelter_into(
		img: Image,
		obstacles: Array[AABB],
		field_centre: Vector2,
		field_size: float,
		wind_direction_deg: float) -> void:
	var res := SHELTER_RESOLUTION
	var metres_per_cell := field_size / float(res)
	var mask := rasterise_mask(obstacles, field_centre, field_size, res)

	var wind := Vector2(cos(deg_to_rad(wind_direction_deg)), sin(deg_to_rad(wind_direction_deg)))
	var lee_steps := int(ceil(LEE_DISTANCE_M / metres_per_cell))
	var windward_steps := int(ceil(WINDWARD_DISTANCE_M / metres_per_cell))

	var shelter_img := Image.create_empty(res, res, false, Image.FORMAT_RGBA8)
	for y in res:
		for x in res:
			if mask[y * res + x] == 1:
				# Under the obstacle itself: no snow may build.
				shelter_img.set_pixel(x, y, Color(0.0, 0.0, 0.0, 1.0))
				continue
			var here := Vector2(float(x) + 0.5, float(y) + 0.5)
			var value := OPEN_EXPOSURE

			# Upwind hit means this cell sits in an obstacle's lee.
			var lee_hit := _march(mask, res, here, -wind, lee_steps)
			if lee_hit >= 0.0:
				value = lerp(LEE_SHELTER, OPEN_EXPOSURE, lee_hit / float(lee_steps))
			else:
				# Downwind hit means this cell is the exposed approach face.
				var wind_hit := _march(mask, res, here, wind, windward_steps)
				if wind_hit >= 0.0:
					value = lerp(WINDWARD_SHELTER, OPEN_EXPOSURE, wind_hit / float(windward_steps))
			shelter_img.set_pixel(x, y, Color(value, value, value, 1.0))

	shelter_img.resize(img.get_width(), img.get_height(), Image.INTERPOLATE_BILINEAR)
	for y in img.get_height():
		for x in img.get_width():
			var c := img.get_pixel(x, y)
			c.a = shelter_img.get_pixel(x, y).r
			img.set_pixel(x, y, c)


## Cell distance to the first obstacle along dir, or -1.0 when nothing is hit.
static func _march(mask: PackedByteArray, res: int, from: Vector2, dir: Vector2, steps: int) -> float:
	for i in range(1, steps + 1):
		var p := from + dir * float(i)
		var px := int(p.x)
		var py := int(p.y)
		if px < 0 or py < 0 or px >= res or py >= res:
			return -1.0
		if mask[py * res + px] == 1:
			return float(i)
	return -1.0


## Collects wind-blocking AABBs from a world scene, skipping the ground and any
## moving actors. Transforms are accumulated by hand so this also works on a
## detached scene graph that is not inside a SceneTree.
##
## TacticalCombat is skipped because tactical unit capsules are MeshInstance3Ds
## spawned at runtime and must not block wind; the weather nodes are skipped
## because their particle emitters are visual only.
static func collect_obstacles(
		root: Node,
		skip_names: PackedStringArray = PackedStringArray([
			"Ground", "Player", "CameraRig", "SnowField",
			"TacticalCombat", "SnowPrecipitation", "SnowWeather",
		])
	) -> Array[AABB]:
	var out: Array[AABB] = []
	for node in root.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := node as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		var skip := false
		var walker: Node = mesh_node
		var xform := Transform3D.IDENTITY
		while walker != null:
			if skip_names.has(walker.name):
				skip = true
				break
			if walker is Node3D:
				xform = (walker as Node3D).transform * xform
			if walker == root:
				break
			walker = walker.get_parent()
		if skip:
			continue
		out.append(xform * mesh_node.mesh.get_aabb())
	return out


static func save_png(img: Image, path: String = DEFAULT_PATH) -> Error:
	var dir := path.get_base_dir()
	if not DirAccess.dir_exists_absolute(dir):
		DirAccess.make_dir_recursive_absolute(dir)
	return img.save_png(path)
