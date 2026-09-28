shader_type canvas_item;
render_mode unshaded;

// Debug overlay for the snow field (SNOWPLAN task G).
// Renders one channel of either the runtime state or the authored maps as a
// false-colour ramp, with a marker ring at the player's field position.

uniform sampler2D snow_state : filter_nearest, repeat_disable;
uniform sampler2D authored_maps : filter_nearest, repeat_disable;
// Live directional shelter pass, for comparison against the baked channel.
uniform sampler2D shelter_tex : filter_nearest, repeat_disable;

// 0 height, 1 freshness, 2 base depth, 3 capacity, 4 compaction, 5 shelter,
// 6 dynamic shelter.
uniform int mode = 0;
uniform float max_depth = 2.0;
uniform vec2 marker_uv = vec2(-1.0, -1.0);

vec3 ramp(float t) {
	t = clamp(t, 0.0, 1.0);
	vec3 cold = vec3(0.05, 0.08, 0.25);
	vec3 mid = vec3(0.10, 0.65, 0.75);
	vec3 hot = vec3(1.0, 0.95, 0.70);
	return t < 0.5 ? mix(cold, mid, t * 2.0) : mix(mid, hot, (t - 0.5) * 2.0);
}

void fragment() {
	vec2 uv = UV;
	vec4 s = texture(snow_state, uv);
	vec4 m = texture(authored_maps, uv);

	float v = 0.0;
	if (mode == 0) {
		v = s.r / max(max_depth, 0.001);
	} else if (mode == 1) {
		v = s.g;
	} else if (mode == 2) {
		v = m.r;
	} else if (mode == 3) {
		v = m.g;
	} else if (mode == 4) {
		v = m.b;
	} else if (mode == 5) {
		v = m.a;
	} else {
		v = texture(shelter_tex, uv).r;
	}

	vec3 col = ramp(v);

	if (marker_uv.x >= 0.0) {
		float d = distance(uv, marker_uv);
		float ring = smoothstep(0.016, 0.012, d) - smoothstep(0.010, 0.006, d);
		col = mix(col, vec3(1.0, 0.25, 0.25), clamp(ring, 0.0, 1.0));
	}

	COLOR = vec4(col, 1.0);
}
