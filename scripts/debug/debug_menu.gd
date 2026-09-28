extends CanvasLayer
## Unified debug menu (plans/SKYPLAN.md section 6).
##
## Replaces SnowDebugOverlay: the snow-field map, its channel cycling, the
## save/load and weather hotkeys and the status line all live here, alongside
## sliders for the sky and a bar of time-of-day presets.
##
## Nothing here writes to a light, the environment or the sky material. Every
## control sets an override_* export on SkyController or SnowWeather -- the same
## path a runtime probe uses -- so the menu and the probes can never disagree.
##
## F3 toggles the menu. F4 cycles the map channel, F5/F6 save and load the
## field, F7 forces a blizzard and F8 rerolls the wind, open or closed.

const DEBUG_SHADER := preload("res://shaders/snow_debug.gdshader")
const CHANNEL_NAMES := [
	"Height", "Freshness", "BaseDepth", "Capacity", "Compaction", "Shelter",
	"DynShelter",
]
const MENU_GROUP := "debug_menu"
## Fixed height of the status block, so the settings panel cannot grow past the
## bottom of the screen however long the status text gets.
const STATUS_HEIGHT := 68.0

## Hours are February defaults; the two anchored presets follow the real
## sunrise and sunset of whatever month the weather is running.
## sun_override / moon_override use the -1 = off convention; moon_override is a
## multiple of SkyController.moon_base_energy.
const PRESETS := [
	{"name": "Sunrise", "anchor": "sunrise", "hour": 0.25,
		"sun_override": -1.0, "moon_override": -1.0,
		"ambient_energy": 0.28, "ambient_color": Color(0.35, 0.30, 0.34),
		"clouds": 0.35, "full_moon": false},
	{"name": "Daytime", "anchor": "", "hour": 10.5,
		"sun_override": -1.0, "moon_override": -1.0,
		"ambient_energy": 0.40, "ambient_color": Color(0.55, 0.62, 0.75),
		"clouds": 0.30, "full_moon": false},
	{"name": "Afternoon", "anchor": "", "hour": 13.5,
		"sun_override": -1.0, "moon_override": -1.0,
		"ambient_energy": 0.40, "ambient_color": Color(0.55, 0.62, 0.75),
		"clouds": 0.45, "full_moon": false},
	{"name": "Sunset", "anchor": "sunset", "hour": -0.25,
		"sun_override": -1.0, "moon_override": -1.0,
		"ambient_energy": 0.28, "ambient_color": Color(0.35, 0.30, 0.34),
		"clouds": 0.40, "full_moon": false},
	{"name": "Twilight", "anchor": "sunset", "hour": 0.6,
		"sun_override": 0.0, "moon_override": 0.6,
		"ambient_energy": 0.20, "ambient_color": Color(0.16, 0.16, 0.26),
		"clouds": 0.35, "full_moon": false},
	{"name": "Midnight", "anchor": "", "hour": 0.0,
		"sun_override": 0.0, "moon_override": 1.0,
		"ambient_energy": 0.12, "ambient_color": Color(0.07, 0.09, 0.16),
		"clouds": 0.30, "full_moon": true},
]

@export var panel_size: int = 300
@export var settings_width: int = 320
@export var save_path: String = "user://snow_state.snow"
@export var open_on_start: bool = false

var _field: SnowField = null
var _weather: SnowWeather = null
var _sky: SkyController = null

var _root: Control = null
var _settings_panel: PanelContainer = null
var _presets_panel: PanelContainer = null
var _map_panel: PanelContainer = null
var _status: Label = null
var _map_rect: ColorRect = null
var _map_label: Label = null
var _map_material: ShaderMaterial = null
var _daylight_hint: Label = null
var _ambient_picker: ColorPickerButton = null

var _rows: Array[Dictionary] = []
var _checks: Dictionary = {}
var _preset_buttons: Dictionary = {}
var _hit_panels: Array[Control] = []

var _channel: int = 0
var _shown: bool = false
var _active_preset: String = ""
var _moon_offset_default: float = 0.0
var _restore_mouse_mode: int = Input.MOUSE_MODE_VISIBLE
var _suppress_deselect: bool = false


func _ready() -> void:
	layer = 10
	add_to_group(MENU_GROUP)
	_build_ui()
	_shown = open_on_start
	visible = _shown
	call_deferred("_bind_scene")


# --- wiring -------------------------------------------------------------------

func _bind_scene() -> void:
	# Every link is optional: the menu degrades to whatever the scene has.
	_weather = get_tree().get_first_node_in_group(SnowWeather.WEATHER_GROUP) as SnowWeather
	_sky = get_tree().get_first_node_in_group(SkyController.SKY_GROUP) as SkyController
	_field = SnowField.find_field(self)

	if _sky:
		_moon_offset_default = _sky.moon_phase_offset_days
	if _field:
		_map_material.set_shader_parameter(&"authored_maps", _field.authored_maps)
		_map_material.set_shader_parameter(&"max_depth", _field.max_depth)
	else:
		_map_panel.visible = false
		if _checks.has("map"):
			(_checks["map"] as CheckBox).button_pressed = false
	_sync_checks()


# --- UI construction ----------------------------------------------------------

func _build_ui() -> void:
	_root = Control.new()
	_root.name = "DebugMenuRoot"
	_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	# The backdrop must not eat clicks; only the panels below do.
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_root.theme = DebugTheme.build()
	add_child(_root)

	_build_settings()
	_build_presets()
	_build_map()


func _build_settings() -> void:
	_settings_panel = PanelContainer.new()
	_settings_panel.name = "Settings"
	_settings_panel.anchor_left = 1.0
	_settings_panel.anchor_right = 1.0
	_settings_panel.offset_left = -float(settings_width) - 16.0
	_settings_panel.offset_right = -16.0
	_settings_panel.offset_top = 16.0
	# Bounded at the bottom as well, so a long status line can never run off
	# the screen or under the presets bar.
	_settings_panel.anchor_bottom = 1.0
	_settings_panel.offset_bottom = -88.0
	_settings_panel.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_settings_panel.clip_contents = true
	_settings_panel.mouse_filter = Control.MOUSE_FILTER_STOP
	_root.add_child(_settings_panel)
	_hit_panels.append(_settings_panel)

	# A PanelContainer takes its height from its content's minimum size, so with
	# the controls added directly it grew past the preset bar however much the
	# rows were trimmed. A ScrollContainer has a small minimum, which lets the
	# panel honour its anchors and makes the layout hold at any window size.
	var scroll := ScrollContainer.new()
	scroll.name = "SettingsScroll"
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	_settings_panel.add_child(scroll)

	var box := VBoxContainer.new()
	box.name = "SettingsBox"
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override(&"separation", 3)
	scroll.add_child(box)

	var title := Label.new()
	title.text = "Sky & Weather"
	DebugTheme.make_title(title)
	box.add_child(title)

	_daylight_hint = Label.new()
	_daylight_hint.add_theme_color_override(&"font_color", DebugTheme.TEXT_DIM)
	_daylight_hint.add_theme_font_size_override(&"font_size", DebugTheme.TITLE_FONT_SIZE)
	box.add_child(_daylight_hint)

	_add_row(box, "Time of day", 0.0, 24.0, 0.01, "%5.2f h",
		func() -> float: return _sky.effective_hour if _sky else 0.0,
		func(v: float) -> void:
			if _weather:
				_weather.set_time_of_day(v)
			elif _sky:
				_sky.fallback_hour = v,
		func() -> bool: return true)

	_add_row(box, "Time scale", 0.0, 600.0, 1.0, "%4.0f x",
		func() -> float: return _weather.time_scale if _weather else 60.0,
		func(v: float) -> void:
			if _weather:
				_weather.time_scale = v,
		func() -> bool: return true)

	_add_row(box, "Sunlight", 0.0, 2.0, 0.01, "%5.2f",
		func() -> float:
			return _sky.override_sun_energy if _sky.override_sun_energy >= 0.0 else _sky.live_sun_energy(),
		func(v: float) -> void: _sky.override_sun_energy = v,
		func() -> bool: return _sky.override_sun_energy >= 0.0)

	_add_row(box, "Moonlight", 0.0, 1.0, 0.005, "%5.3f",
		func() -> float:
			return _sky.override_moon_energy if _sky.override_moon_energy >= 0.0 else _sky.live_moon_energy(),
		func(v: float) -> void: _sky.override_moon_energy = v,
		func() -> bool: return _sky.override_moon_energy >= 0.0)

	_add_row(box, "Ambient fill", 0.0, 2.0, 0.01, "%5.2f",
		func() -> float:
			return _sky.override_ambient_energy if _sky.override_ambient_energy >= 0.0 else _sky.live_ambient_energy(),
		func(v: float) -> void: _sky.override_ambient_energy = v,
		func() -> bool: return _sky.override_ambient_energy >= 0.0)

	_add_color_row(box)

	_add_row(box, "Cloud cover", 0.0, 1.0, 0.01, "%5.2f",
		func() -> float:
			return _sky.override_cloud_coverage if _sky.override_cloud_coverage >= 0.0 else _sky.cloud_coverage,
		func(v: float) -> void: _sky.override_cloud_coverage = v,
		func() -> bool: return _sky.override_cloud_coverage >= 0.0)

	_add_row(box, "Cloud speed", 0.0, 4.0, 0.05, "%5.2f",
		func() -> float:
			return _sky.override_cloud_speed if _sky.override_cloud_speed >= 0.0 else _sky.cloud_speed_scale,
		func(v: float) -> void: _sky.override_cloud_speed = v,
		func() -> bool: return _sky.override_cloud_speed >= 0.0)

	_add_row(box, "Fog density", 0.0, 0.08, 0.0005, "%6.4f",
		func() -> float:
			return _sky.override_fog_density if _sky.override_fog_density >= 0.0 else _sky.live_fog_density(),
		func(v: float) -> void: _sky.override_fog_density = v,
		func() -> bool: return _sky.override_fog_density >= 0.0)

	_add_row(box, "Night floor", 0.0, 0.5, 0.005, "%5.3f",
		func() -> float: return _sky.night_floor_energy,
		func(v: float) -> void: _sky.night_floor_energy = v,
		func() -> bool: return true)

	box.add_child(HSeparator.new())

	_add_check(box, "run_clock", "Run clock", true,
		func(on: bool) -> void:
			if _weather:
				_weather.run_clock = on)
	_add_check(box, "weather_sky", "Weather -> sky", true,
		func(on: bool) -> void: _sky.weather_affects_sky = on)
	# Clouds and stars share a row, as the plan's layout sketch has them.
	var sky_toggles := HBoxContainer.new()
	sky_toggles.name = "SkyToggles"
	box.add_child(sky_toggles)
	_add_check(sky_toggles, "clouds", "Clouds", true,
		func(on: bool) -> void: _sky.clouds_enabled = on)
	_add_check(sky_toggles, "stars", "Stars", true,
		func(on: bool) -> void: _sky.stars_enabled = on)
	_add_check(box, "blizzard", "Force blizzard", false,
		func(on: bool) -> void:
			if _weather:
				_weather.set_force_blizzard(on))
	_add_check(box, "cloud_shadows", "Cloud shadows", false,
		func(on: bool) -> void: _sky.cloud_shadows_enabled = on)
	_add_check(box, "map", "Snow map panel", true,
		func(on: bool) -> void: _map_panel.visible = on and _field != null)

	box.add_child(HSeparator.new())

	# A PanelContainer cannot shrink below its content's minimum size, so a
	# wrapping label with no height bound pushes the whole panel off the bottom
	# of the screen. The status lives in a fixed-height clipping holder instead.
	var status_holder := Control.new()
	status_holder.name = "StatusHolder"
	status_holder.custom_minimum_size = Vector2(float(settings_width) - 24.0, STATUS_HEIGHT)
	status_holder.clip_contents = true
	status_holder.mouse_filter = Control.MOUSE_FILTER_IGNORE
	box.add_child(status_holder)

	_status = Label.new()
	_status.name = "Status"
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status.set_anchors_preset(Control.PRESET_FULL_RECT)
	_status.add_theme_color_override(&"font_color", DebugTheme.TEXT_DIM)
	_status.add_theme_font_size_override(&"font_size", DebugTheme.STATUS_FONT_SIZE)
	status_holder.add_child(_status)


## One control row: name, slider, value. `current` is what the slider shows,
## `apply` writes it through, `active` says whether the value is being forced
## (full alpha) or merely reported (60 %).
func _add_row(parent: Container, label_text: String, min_v: float, max_v: float,
		step: float, fmt: String, current: Callable, apply: Callable, active: Callable) -> void:
	var row := HBoxContainer.new()
	row.name = label_text.replace(" ", "")
	parent.add_child(row)

	var name_label := Label.new()
	name_label.text = label_text
	name_label.custom_minimum_size = Vector2(110, 0)
	row.add_child(name_label)

	var slider := HSlider.new()
	slider.min_value = min_v
	slider.max_value = max_v
	slider.step = step
	slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	slider.custom_minimum_size = Vector2(0, 16)
	row.add_child(slider)

	var value_label := Label.new()
	value_label.text = fmt % min_v
	value_label.custom_minimum_size = Vector2(56, 0)
	value_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	row.add_child(value_label)

	var entry := {
		"row": row, "slider": slider, "value": value_label, "fmt": fmt,
		"current": current, "apply": apply, "active": active, "dragging": false,
	}
	_rows.append(entry)

	slider.value_changed.connect(func(v: float) -> void: _on_slider_changed(entry, v))
	slider.drag_started.connect(func() -> void: entry["dragging"] = true)
	slider.drag_ended.connect(func(_changed: bool) -> void: entry["dragging"] = false)


func _add_color_row(parent: Container) -> void:
	var row := HBoxContainer.new()
	row.name = "AmbientColor"
	parent.add_child(row)

	var name_label := Label.new()
	name_label.text = "Ambient col"
	name_label.custom_minimum_size = Vector2(110, 0)
	row.add_child(name_label)

	_ambient_picker = ColorPickerButton.new()
	_ambient_picker.edit_alpha = false
	_ambient_picker.custom_minimum_size = Vector2(0, 18)
	_ambient_picker.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_ambient_picker.color_changed.connect(_on_ambient_color_changed)
	row.add_child(_ambient_picker)

	var clear := Button.new()
	clear.text = "auto"
	clear.custom_minimum_size = Vector2(56, 0)
	clear.pressed.connect(func() -> void:
		if _sky:
			_sky.override_ambient_color = Color(0, 0, 0, 0)
		_deselect_presets())
	row.add_child(clear)


func _add_check(parent: Container, key: String, label_text: String,
		default_on: bool, apply: Callable) -> void:
	var check := CheckBox.new()
	check.name = key
	check.text = label_text
	check.button_pressed = default_on
	parent.add_child(check)
	_checks[key] = check
	check.toggled.connect(func(on: bool) -> void:
		if _sky == null and key != "run_clock" and key != "blizzard" and key != "map":
			return
		apply.call(on)
		_deselect_presets())


func _build_presets() -> void:
	_presets_panel = PanelContainer.new()
	_presets_panel.name = "Presets"
	_presets_panel.anchor_left = 0.5
	_presets_panel.anchor_right = 0.5
	_presets_panel.anchor_top = 1.0
	_presets_panel.anchor_bottom = 1.0
	_presets_panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_presets_panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_presets_panel.offset_bottom = -24.0
	_presets_panel.mouse_filter = Control.MOUSE_FILTER_STOP
	_root.add_child(_presets_panel)
	_hit_panels.append(_presets_panel)

	var bar := HBoxContainer.new()
	bar.name = "PresetBar"
	_presets_panel.add_child(bar)

	var group := ButtonGroup.new()
	group.allow_unpress = true
	for preset: Dictionary in PRESETS:
		var button := Button.new()
		var preset_name: String = preset["name"]
		button.name = preset_name
		button.text = preset_name
		button.toggle_mode = true
		button.button_group = group
		button.pressed.connect(func() -> void: apply_preset(preset_name))
		bar.add_child(button)
		_preset_buttons[preset_name] = button

	bar.add_child(VSeparator.new())

	var live := Button.new()
	live.name = "Live"
	live.text = "Live"
	live.pressed.connect(func() -> void: apply_preset("Live"))
	bar.add_child(live)


func _build_map() -> void:
	_map_panel = PanelContainer.new()
	_map_panel.name = "SnowMap"
	_map_panel.anchor_top = 1.0
	_map_panel.anchor_bottom = 1.0
	_map_panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_map_panel.offset_left = 16.0
	# Leaves room for the presets bar, which is anchored 24 px off the bottom.
	_map_panel.offset_bottom = -72.0
	_map_panel.mouse_filter = Control.MOUSE_FILTER_STOP
	_root.add_child(_map_panel)
	_hit_panels.append(_map_panel)

	var box := VBoxContainer.new()
	box.name = "MapBox"
	_map_panel.add_child(box)

	_map_label = Label.new()
	_map_label.text = "Snow field"
	DebugTheme.make_title(_map_label)
	box.add_child(_map_label)

	_map_material = ShaderMaterial.new()
	_map_material.shader = DEBUG_SHADER

	_map_rect = ColorRect.new()
	_map_rect.name = "SnowDebugView"
	_map_rect.custom_minimum_size = Vector2(panel_size, panel_size)
	_map_rect.material = _map_material
	_map_rect.color = Color.WHITE
	box.add_child(_map_rect)

	# The key legend lives under the map rather than in the status block, where
	# it was two of the five lines the settings panel has room for.
	var keys := Label.new()
	keys.name = "Keys"
	keys.text = "F3 menu   F4 channel   F5 save   F6 load   F7 blizzard   F8 reroll"
	keys.add_theme_color_override(&"font_color", DebugTheme.TEXT_DIM)
	keys.add_theme_font_size_override(&"font_size", DebugTheme.STATUS_FONT_SIZE)
	box.add_child(keys)


# --- control callbacks --------------------------------------------------------

func _on_slider_changed(entry: Dictionary, value: float) -> void:
	if _sky == null and _weather == null:
		return
	(entry["apply"] as Callable).call(value)
	if not _suppress_deselect:
		_deselect_presets()


func _on_ambient_color_changed(col: Color) -> void:
	if _sky == null:
		return
	_sky.override_ambient_color = Color(col.r, col.g, col.b, 1.0)
	_deselect_presets()


## Moving any control means the sky is no longer "at" a preset.
func _deselect_presets() -> void:
	if _suppress_deselect or _active_preset == "":
		return
	_active_preset = ""
	for button: Button in _preset_buttons.values():
		button.set_pressed_no_signal(false)


func _sync_checks() -> void:
	_suppress_deselect = true
	if _weather:
		(_checks["run_clock"] as CheckBox).set_pressed_no_signal(_weather.run_clock)
		(_checks["blizzard"] as CheckBox).set_pressed_no_signal(_weather.force_blizzard)
	if _sky:
		(_checks["weather_sky"] as CheckBox).set_pressed_no_signal(_sky.weather_affects_sky)
		(_checks["clouds"] as CheckBox).set_pressed_no_signal(_sky.clouds_enabled)
		(_checks["stars"] as CheckBox).set_pressed_no_signal(_sky.stars_enabled)
		(_checks["cloud_shadows"] as CheckBox).set_pressed_no_signal(_sky.cloud_shadows_enabled)
	_suppress_deselect = false


# --- presets ------------------------------------------------------------------

## Public so probes drive exactly the same path the buttons do. "Live" clears
## every override and restores the moon phase.
func apply_preset(preset_name: String) -> void:
	if _sky == null:
		return

	if preset_name == "Live":
		_sky.clear_overrides()
		_sky.moon_phase_offset_days = _moon_offset_default
		_active_preset = ""
		for button: Button in _preset_buttons.values():
			button.set_pressed_no_signal(false)
		return

	for preset: Dictionary in PRESETS:
		if String(preset["name"]) != preset_name:
			continue

		var hour := preset_hour(preset)
		# Presets move the real clock rather than pinning override_hour, so the
		# weather timeline and the sky can never disagree about the time.
		_sky.override_hour = -1.0
		if _weather:
			_weather.set_time_of_day(hour)
		else:
			_sky.fallback_hour = hour

		var sun_override := float(preset["sun_override"])
		_sky.override_sun_energy = sun_override
		var moon_override := float(preset["moon_override"])
		_sky.override_moon_energy = -1.0 if moon_override < 0.0 \
			else _sky.moon_base_energy * moon_override
		_sky.override_ambient_energy = float(preset["ambient_energy"])
		var ambient: Color = preset["ambient_color"]
		_sky.override_ambient_color = Color(ambient.r, ambient.g, ambient.b, 1.0)
		_sky.override_cloud_coverage = float(preset["clouds"])

		if bool(preset["full_moon"]):
			# Phase 0.5 is full; solve fposmod(day + offset, synodic) / synodic.
			_sky.moon_phase_offset_days = Solar.SYNODIC_MONTH_DAYS * 0.5 \
				- float(_sky.effective_day)
		else:
			_sky.moon_phase_offset_days = _moon_offset_default

		_active_preset = preset_name
		_suppress_deselect = true
		for other_name: String in _preset_buttons:
			(_preset_buttons[other_name] as Button).set_pressed_no_signal(other_name == preset_name)
		_suppress_deselect = false
		return


## Clock hour a preset lands on, following the real sunrise/sunset of the month
## in play for the two anchored presets.
func preset_hour(preset: Dictionary) -> float:
	var hours := float(preset["hour"])
	match String(preset["anchor"]):
		"sunrise":
			return fposmod(_sky.sunrise_hour() + hours, 24.0)
		"sunset":
			return fposmod(_sky.sunset_hour() + hours, 24.0)
		_:
			return hours


func active_preset() -> String:
	return _active_preset


# --- input --------------------------------------------------------------------

## True when `point` (in viewport coordinates) lands on one of the menu's
## panels. world_setup.gd asks before running its tactical raycast, because
## Node._input runs before the GUI ever sees the click.
func is_point_over_ui(point: Vector2) -> bool:
	if not _shown:
		return false
	for panel: Control in _hit_panels:
		if panel.visible and panel.get_global_rect().has_point(point):
			return true
	return false


func is_open() -> bool:
	return _shown


func toggle(on: bool) -> void:
	if on == _shown:
		return
	_shown = on
	visible = _shown
	if _shown:
		_restore_mouse_mode = Input.get_mouse_mode()
		Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
	else:
		Input.set_mouse_mode(_restore_mouse_mode)


func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventKey) or not event.pressed or event.echo:
		return
	var key := event as InputEventKey
	match key.physical_keycode:
		KEY_F3:
			toggle(not _shown)
			get_viewport().set_input_as_handled()
		KEY_F4:
			_channel = (_channel + 1) % CHANNEL_NAMES.size()
			get_viewport().set_input_as_handled()
		KEY_F5:
			if _field and _field.save_state(save_path):
				print("[snow] saved field to %s" % save_path)
			get_viewport().set_input_as_handled()
		KEY_F6:
			if _field and _field.load_state(save_path):
				print("[snow] restored field from %s" % save_path)
			get_viewport().set_input_as_handled()
		KEY_F7:
			if _weather:
				_weather.set_force_blizzard(not _weather.force_blizzard)
				print("[weather] force_blizzard %s" % ("on" if _weather.force_blizzard else "off"))
				_sync_checks()
			get_viewport().set_input_as_handled()
		KEY_F8:
			if _weather:
				_weather.reroll_wind()
				print("[weather] wind rerolled to from %d deg" % int(_weather.wind_from_deg))
			get_viewport().set_input_as_handled()


# --- per-frame ----------------------------------------------------------------

func _process(_delta: float) -> void:
	if not _shown:
		return
	_update_rows()
	_update_map()
	_update_status()


func _update_rows() -> void:
	if _sky == null:
		return
	_suppress_deselect = true
	for entry: Dictionary in _rows:
		var slider: HSlider = entry["slider"]
		var forced: bool = (entry["active"] as Callable).call()
		if not entry["dragging"]:
			slider.set_value_no_signal((entry["current"] as Callable).call())
		var alpha := 1.0 if forced else DebugTheme.TRACKING_ALPHA
		(entry["row"] as Control).modulate.a = alpha
		(entry["value"] as Label).text = String(entry["fmt"]) % slider.value
	_suppress_deselect = false

	_daylight_hint.text = "sunrise %s   sunset %s" % [
		_hhmm(_sky.sunrise_hour()), _hhmm(_sky.sunset_hour())]
	if _ambient_picker and not _ambient_picker.get_popup().visible:
		var col := _sky.override_ambient_color if _sky.override_ambient_color.a > 0.0 \
			else _sky.live_ambient_color()
		_ambient_picker.color = Color(col.r, col.g, col.b, 1.0)


func _update_map() -> void:
	if _field == null or not _map_panel.visible:
		return
	_map_material.set_shader_parameter(&"snow_state", _field.get_state_texture())
	_map_material.set_shader_parameter(&"shelter_tex", _field.get_shelter_texture())
	_map_material.set_shader_parameter(&"mode", _channel)

	var marker := Vector2(-1.0, -1.0)
	var player := _find_player()
	if player and _field.is_inside_field(player.global_position):
		marker = _field.world_to_uv(player.global_position)
	_map_material.set_shader_parameter(&"marker_uv", marker)

	_map_label.text = "[F4] %s" % CHANNEL_NAMES[_channel]


func _update_status() -> void:
	var lines: Array[String] = []

	# Terse on purpose: the block has a fixed height, and every line that wraps
	# costs one that would otherwise be visible.
	if _sky:
		lines.append("sun %+.1f/%03.0f  moon %.2f %+.1f  cloud %.2f" % [
			_sky.sun_elevation_deg, _sky.sun_azimuth_deg,
			_sky.moon_phase, _sky.moon_elevation_deg, _sky.cloud_coverage])

	if _weather:
		lines.append("day %d %s  %s  snow %.2f  wind %.1f from %03d  front %s" % [
			_weather.day, _hhmm(_weather.clock_hour), _weather.state_name(),
			_weather.snow_intensity_cm_per_h, _weather.wind_speed_ms,
			int(_weather.wind_from_deg),
			"on" if (_weather.front_active or _weather.force_blizzard) else "off"])

	if _field:
		var player := _find_player()
		if player:
			var authored := _field.get_authored(player.global_position)
			lines.append("depth %.2f  base %.2f cap %.2f comp %.2f shelter %.2f" % [
				_field.get_snow_depth(player.global_position),
				authored.x, authored.y, authored.z, authored.w])
		lines.append("field %03d x%.2f%s   seed %d" % [
			int(_field.wind_direction_deg), _field.wind_strength,
			" +snow" if _field.snowfall else "",
			_weather.seed_used if _weather else 0])

	_status.text = "\n".join(lines)


static func _hhmm(hour: float) -> String:
	var h := fposmod(hour, 24.0)
	return "%02d:%02d" % [int(h), int(fposmod(h * 60.0, 60.0))]


func _find_player() -> Node3D:
	var gm := get_node_or_null("/root/GameManager")
	if gm and gm.get("player") != null:
		return gm.get("player") as Node3D
	return null
