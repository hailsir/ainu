extends Node
## Global game state manager. Autoloaded as "GameManager".
## Tracks the current game mode (free_roam vs tactical) and provides
## signals for mode transitions.

signal mode_changed(new_mode: GameMode)
signal combat_started()
signal combat_ended()

enum GameMode {
	FREE_ROAM,
	TACTICAL,
	CUTSCENE,
	MENU,
}

var current_mode: GameMode = GameMode.FREE_ROAM
var player: CharacterBody3D = null
var camera_rig: Node3D = null


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS


func register_player(p: CharacterBody3D) -> void:
	player = p


func register_camera(c: Node3D) -> void:
	camera_rig = c


func set_mode(new_mode: GameMode) -> void:
	if current_mode == new_mode:
		return
	var old_mode: GameMode = current_mode
	current_mode = new_mode
	mode_changed.emit(new_mode)

	if new_mode == GameMode.TACTICAL and old_mode == GameMode.FREE_ROAM:
		combat_started.emit()
	elif new_mode == GameMode.FREE_ROAM and old_mode == GameMode.TACTICAL:
		combat_ended.emit()


func is_free_roam() -> bool:
	return current_mode == GameMode.FREE_ROAM


func is_tactical() -> bool:
	return current_mode == GameMode.TACTICAL
