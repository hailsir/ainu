extends RefCounted
class_name DebugTheme
## Runtime-built theme for the debug menu (plans/SKYPLAN.md section 6).
##
## Nothing is saved to disk: build() returns a Theme that the menu assigns to
## its root Control, and every child inherits it. The whole visual lives here so
## the in-game HUD can reuse it later.
##
## Dark navy panels, gold highlights, white text. Slider grabbers and checkbox
## marks are rasterised at load because the project ships no UI atlas.

const NAVY := Color(0.055, 0.10, 0.22, 0.92)
const NAVY_SOLID := Color(0.055, 0.10, 0.22, 1.0)
const GOLD := Color(0.83, 0.68, 0.21)
const GOLD_DIM := Color(0.83, 0.68, 0.21, 0.4)
const TEXT := Color(1, 1, 1)
const TEXT_DIM := Color(1, 1, 1, 0.6)
const TRACK := Color(1, 1, 1, 0.25)

const FONT_SIZE := 14
const TITLE_FONT_SIZE := 12
## The status block is five wrapped lines; it has to stay inside the panel.
const STATUS_FONT_SIZE := 11
const CORNER_RADIUS := 6

## Alpha applied to a slider row that is only reporting the live value.
const TRACKING_ALPHA := 0.6


static func build() -> Theme:
	var theme := Theme.new()
	theme.default_font_size = FONT_SIZE

	_build_panel(theme)
	_build_label(theme)
	_build_button(theme)
	_build_slider(theme)
	_build_checkbox(theme)
	_build_scrollbar(theme)
	_build_containers(theme)
	return theme


static func panel_style(alpha: float = NAVY.a) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(NAVY.r, NAVY.g, NAVY.b, alpha)
	sb.set_corner_radius_all(CORNER_RADIUS)
	sb.set_border_width_all(1)
	sb.border_color = GOLD_DIM
	sb.set_content_margin_all(10)
	return sb


## Section heading: small, gold, uppercase and tracked out.
static func make_title(label: Label) -> void:
	label.text = label.text.to_upper()
	label.add_theme_color_override(&"font_color", GOLD)
	label.add_theme_font_size_override(&"font_size", TITLE_FONT_SIZE)
	var fv := FontVariation.new()
	fv.base_font = ThemeDB.fallback_font
	fv.set_spacing(TextServer.SPACING_GLYPH, 2)
	label.add_theme_font_override(&"font", fv)


# --- theme types ---------------------------------------------------------------

static func _build_panel(theme: Theme) -> void:
	theme.add_type(&"PanelContainer")
	theme.set_stylebox(&"panel", &"PanelContainer", panel_style())


static func _build_label(theme: Theme) -> void:
	theme.add_type(&"Label")
	theme.set_color(&"font_color", &"Label", TEXT)
	theme.set_font_size(&"font_size", &"Label", FONT_SIZE)


static func _build_button(theme: Theme) -> void:
	theme.add_type(&"Button")

	var normal := StyleBoxFlat.new()
	normal.bg_color = Color(0, 0, 0, 0)
	normal.set_corner_radius_all(4)
	normal.set_content_margin_all(6)
	normal.content_margin_left = 12
	normal.content_margin_right = 12

	var hover := normal.duplicate() as StyleBoxFlat
	hover.set_border_width_all(1)
	hover.border_color = GOLD

	var pressed := normal.duplicate() as StyleBoxFlat
	pressed.bg_color = GOLD

	var focus := normal.duplicate() as StyleBoxFlat
	focus.set_border_width_all(1)
	focus.border_color = GOLD_DIM

	theme.set_stylebox(&"normal", &"Button", normal)
	theme.set_stylebox(&"hover", &"Button", hover)
	theme.set_stylebox(&"pressed", &"Button", pressed)
	theme.set_stylebox(&"hover_pressed", &"Button", pressed)
	theme.set_stylebox(&"disabled", &"Button", normal)
	theme.set_stylebox(&"focus", &"Button", focus)

	theme.set_color(&"font_color", &"Button", TEXT)
	theme.set_color(&"font_hover_color", &"Button", GOLD)
	theme.set_color(&"font_focus_color", &"Button", TEXT)
	# The active preset is a gold fill, so its label goes navy to stay legible.
	theme.set_color(&"font_pressed_color", &"Button", NAVY_SOLID)
	theme.set_color(&"font_hover_pressed_color", &"Button", NAVY_SOLID)
	theme.set_color(&"font_disabled_color", &"Button", TEXT_DIM)
	theme.set_font_size(&"font_size", &"Button", FONT_SIZE)


static func _build_slider(theme: Theme) -> void:
	theme.add_type(&"HSlider")

	var track := StyleBoxFlat.new()
	track.bg_color = TRACK
	track.set_corner_radius_all(2)
	track.content_margin_top = 2
	track.content_margin_bottom = 2

	var filled := StyleBoxFlat.new()
	filled.bg_color = GOLD
	filled.set_corner_radius_all(2)
	filled.content_margin_top = 2
	filled.content_margin_bottom = 2

	theme.set_stylebox(&"slider", &"HSlider", track)
	theme.set_stylebox(&"grabber_area", &"HSlider", filled)
	theme.set_stylebox(&"grabber_area_highlight", &"HSlider", filled)

	var grabber := _disc_icon(14, GOLD)
	theme.set_icon(&"grabber", &"HSlider", grabber)
	theme.set_icon(&"grabber_highlight", &"HSlider", _disc_icon(14, Color(1, 0.86, 0.42)))
	theme.set_icon(&"grabber_disabled", &"HSlider", _disc_icon(14, TRACK))
	theme.set_constant(&"center_grabber", &"HSlider", 1)


static func _build_checkbox(theme: Theme) -> void:
	theme.add_type(&"CheckBox")

	var empty := StyleBoxEmpty.new()
	empty.set_content_margin_all(4)
	for style: StringName in [&"normal", &"pressed", &"disabled", &"focus"]:
		theme.set_stylebox(style, &"CheckBox", empty)
	var hover := StyleBoxFlat.new()
	hover.bg_color = Color(GOLD.r, GOLD.g, GOLD.b, 0.12)
	hover.set_corner_radius_all(4)
	hover.set_content_margin_all(4)
	theme.set_stylebox(&"hover", &"CheckBox", hover)
	theme.set_stylebox(&"hover_pressed", &"CheckBox", hover)

	theme.set_color(&"font_color", &"CheckBox", TEXT)
	theme.set_color(&"font_hover_color", &"CheckBox", GOLD)
	theme.set_color(&"font_pressed_color", &"CheckBox", TEXT)
	theme.set_color(&"font_hover_pressed_color", &"CheckBox", GOLD)
	theme.set_font_size(&"font_size", &"CheckBox", FONT_SIZE)

	var checked := _box_icon(16, true)
	var unchecked := _box_icon(16, false)
	for icon: StringName in [&"checked", &"checked_disabled"]:
		theme.set_icon(icon, &"CheckBox", checked)
	for icon: StringName in [&"unchecked", &"unchecked_disabled"]:
		theme.set_icon(icon, &"CheckBox", unchecked)
	theme.set_constant(&"h_separation", &"CheckBox", 6)


## The settings panel scrolls when it runs out of room, so the bar has to match.
static func _build_scrollbar(theme: Theme) -> void:
	theme.add_type(&"VScrollBar")

	var track := StyleBoxFlat.new()
	track.bg_color = Color(1, 1, 1, 0.08)
	track.set_corner_radius_all(3)
	track.content_margin_left = 3
	track.content_margin_right = 3

	var grabber := StyleBoxFlat.new()
	grabber.bg_color = GOLD_DIM
	grabber.set_corner_radius_all(3)
	grabber.content_margin_left = 3
	grabber.content_margin_right = 3

	var grabber_hl := grabber.duplicate() as StyleBoxFlat
	grabber_hl.bg_color = GOLD

	theme.set_stylebox(&"scroll", &"VScrollBar", track)
	theme.set_stylebox(&"scroll_focus", &"VScrollBar", track)
	theme.set_stylebox(&"grabber", &"VScrollBar", grabber)
	theme.set_stylebox(&"grabber_highlight", &"VScrollBar", grabber_hl)
	theme.set_stylebox(&"grabber_pressed", &"VScrollBar", grabber_hl)


static func _build_containers(theme: Theme) -> void:
	theme.add_type(&"VBoxContainer")
	theme.set_constant(&"separation", &"VBoxContainer", 4)
	theme.add_type(&"HBoxContainer")
	theme.set_constant(&"separation", &"HBoxContainer", 6)


# --- icon rasterisers ----------------------------------------------------------

static func _blank(size: int) -> Image:
	var img := Image.create(size, size, false, Image.FORMAT_RGBA8)
	img.fill(Color(0, 0, 0, 0))
	return img


static func _disc_icon(size: int, col: Color) -> ImageTexture:
	var img := _blank(size)
	var r := float(size) * 0.5
	for y in size:
		for x in size:
			# One-pixel analytic edge, so the grabber is not a jagged blob.
			var a := clampf(r - Vector2(float(x) + 0.5 - r, float(y) + 0.5 - r).length(), 0.0, 1.0)
			if a > 0.0:
				img.set_pixel(x, y, Color(col.r, col.g, col.b, col.a * a))
	return ImageTexture.create_from_image(img)


## Checked is a gold box with a navy tick; unchecked is a white outline.
static func _box_icon(size: int, checked: bool) -> ImageTexture:
	var img := _blank(size)
	var last := size - 1
	for y in size:
		for x in size:
			var on_border := x == 0 or y == 0 or x == last or y == last
			if checked:
				img.set_pixel(x, y, GOLD)
			elif on_border:
				img.set_pixel(x, y, TEXT)

	if checked:
		# Two strokes of a tick, thickened by a pixel each way.
		_stroke(img, Vector2(3.5, 8.0), Vector2(6.5, 11.5), NAVY_SOLID)
		_stroke(img, Vector2(6.5, 11.5), Vector2(12.0, 4.5), NAVY_SOLID)
	return ImageTexture.create_from_image(img)


static func _stroke(img: Image, from: Vector2, to: Vector2, col: Color) -> void:
	var steps := int(maxf(from.distance_to(to) * 2.0, 1.0))
	for i in steps + 1:
		var p := from.lerp(to, float(i) / float(steps))
		for dy: int in [-1, 0]:
			for dx: int in [-1, 0]:
				var x := int(p.x) + dx
				var y := int(p.y) + dy
				if x >= 0 and y >= 0 and x < img.get_width() and y < img.get_height():
					img.set_pixel(x, y, col)
