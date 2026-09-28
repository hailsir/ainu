class_name TurnManager
extends Node
## Manages turn order for tactical combat.
## Alternates between player phase and enemy phase.

signal turn_started(unit: TacticalUnit)
signal turn_ended(unit: TacticalUnit)
signal phase_started(team: int)
signal phase_ended(team: int)
signal round_started(round_number: int)
signal all_enemies_defeated()
signal all_players_defeated()

enum Phase {
	PLAYER,
	ENEMY,
}

var current_phase: Phase = Phase.PLAYER
var current_round: int = 0
var current_unit_index: int = 0
var active_unit: TacticalUnit = null

var player_units: Array = []
var enemy_units: Array = []

var _combat_active: bool = false


func start_combat(players: Array, enemies: Array) -> void:
	player_units = players
	enemy_units = enemies
	current_round = 0
	_combat_active = true
	_start_new_round()


func _start_new_round() -> void:
	current_round += 1
	round_started.emit(current_round)
	current_phase = Phase.PLAYER
	_start_phase(Phase.PLAYER)


func _start_phase(phase: Phase) -> void:
	current_phase = phase
	current_unit_index = 0

	var units: Array = _get_phase_units()
	for u: Variant in units:
		if u is TacticalUnit and u.is_alive:
			u.start_turn()

	phase_started.emit(0 if phase == Phase.PLAYER else 1)
	_activate_next_unit()


func _activate_next_unit() -> void:
	var units: Array = _get_phase_units()

	while current_unit_index < units.size():
		var unit: Variant = units[current_unit_index]
		if unit is TacticalUnit and unit.is_alive:
			active_unit = unit as TacticalUnit
			turn_started.emit(active_unit)
			return
		current_unit_index += 1

	_end_phase()


func end_current_turn() -> void:
	if active_unit:
		active_unit.end_turn()
		turn_ended.emit(active_unit)

	if _check_victory():
		return

	current_unit_index += 1
	_activate_next_unit()


func _end_phase() -> void:
	phase_ended.emit(0 if current_phase == Phase.PLAYER else 1)

	if current_phase == Phase.PLAYER:
		_start_phase(Phase.ENEMY)
	else:
		_start_new_round()


func _get_phase_units() -> Array:
	if current_phase == Phase.PLAYER:
		return player_units
	return enemy_units


func _check_victory() -> bool:
	var players_alive: bool = false
	for u: Variant in player_units:
		if u is TacticalUnit and u.is_alive:
			players_alive = true
			break

	var enemies_alive: bool = false
	for u: Variant in enemy_units:
		if u is TacticalUnit and u.is_alive:
			enemies_alive = true
			break

	if not enemies_alive:
		_combat_active = false
		all_enemies_defeated.emit()
		return true
	if not players_alive:
		_combat_active = false
		all_players_defeated.emit()
		return true
	return false


func is_player_phase() -> bool:
	return current_phase == Phase.PLAYER


func is_combat_active() -> bool:
	return _combat_active


func get_active_unit() -> TacticalUnit:
	return active_unit
