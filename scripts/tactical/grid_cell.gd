class_name GridCell
extends RefCounted
## Data for a single cell in the tactical grid.

## Grid coordinates (column, row).
var coords: Vector2i = Vector2i.ZERO
## World-space center position of this cell.
var world_position: Vector3 = Vector3.ZERO
## Whether units can walk on this cell.
var walkable: bool = true
## The unit currently occupying this cell, or null.
var occupant: Node3D = null
## Movement cost to enter this cell (1.0 = normal).
var move_cost: float = 1.0
## Cover type: 0=none, 1=half, 2=full.
var cover: int = 0


func is_occupied() -> bool:
	return occupant != null


func is_passable() -> bool:
	return walkable and not is_occupied()


func _to_string() -> String:
	return "GridCell(%d,%d walkable=%s occupied=%s)" % [coords.x, coords.y, walkable, is_occupied()]
