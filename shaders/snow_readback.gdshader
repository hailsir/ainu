shader_type canvas_item;
render_mode unshaded, blend_disabled;

// Downsamples the 1200x1200 snow state into the small CPU-readable copy
// (SNOWPLAN section 5). A straight bilinear stretch only reaches four of the
// hundred source texels per 1 m cell, so a narrow trail can fall between taps.
// Averaging a grid of taps gives gameplay a stable "how deep here" reading.

uniform sampler2D snow_state : filter_linear, repeat_disable;
// UV extent of one destination cell, i.e. 1.0 / readback_resolution.
uniform float cell_uv = 0.008333333;
uniform int taps = 4;

void fragment() {
	vec2 cell = vec2(cell_uv);
	vec4 total = vec4(0.0);
	float count = 0.0;
	for (int y = 0; y < taps; y++) {
		for (int x = 0; x < taps; x++) {
			vec2 offset = (vec2(float(x), float(y)) + 0.5) / float(taps) - 0.5;
			total += texture(snow_state, UV + offset * cell);
			count += 1.0;
		}
	}
	COLOR = total / count;
}
