shader_type canvas_item;
render_mode unshaded, blend_disabled;

// Directional shelter pass (plans/WEATHERPLAN.md section 1).
//
// Re-renders the wind shelter factor for the *current* wind direction, so
// trails and drifts follow the weather instead of the one direction baked into
// the alpha channel of snow_maps.png. Mirrors SnowMapsBaker._march() exactly,
// in mask-cell units, so the CPU bake stays a valid fallback.
//
// Output: R = shelter factor. 0 under an obstacle, WINDWARD_SHELTER on an
// exposed approach face, LEE_SHELTER in a tight lee pocket, open_exposure in
// the open.

// 0 = open ground, 1 = obstacle footprint. Nearest + clamp: a march step must
// see crisp cell edges, and the explicit bounds test below handles the border.
uniform sampler2D obstacle_mask : filter_nearest, repeat_disable;

// Unit vector the wind blows *toward*, in field UV space (UV maps onto world XZ).
uniform vec2 wind_dir = vec2(1.0, 0.0);
uniform float field_size = 120.0;
uniform int mask_resolution = 128;

uniform float lee_distance_m = 12.0;
uniform float windward_distance_m = 4.0;
uniform float open_exposure = 0.55;
uniform float lee_shelter = 1.0;
uniform float windward_shelter = 0.12;


// Cell distance to the first obstacle along dir, or -1.0 when nothing is hit.
// step_uv converts one mask cell into UV, so steps match the CPU march 1:1.
float march(vec2 from_uv, vec2 dir, int steps, float step_uv) {
	for (int i = 1; i <= steps; i++) {
		vec2 p = from_uv + dir * (float(i) * step_uv);
		// repeat_disable clamps to the edge texel, which would read as a hit
		// for anything touching the border, so bound the walk explicitly.
		if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0) {
			return -1.0;
		}
		if (texture(obstacle_mask, p).r > 0.5) {
			return float(i);
		}
	}
	return -1.0;
}

void fragment() {
	vec2 uv = UV;

	if (texture(obstacle_mask, uv).r > 0.5) {
		// Under the obstacle itself: no snow may build.
		COLOR = vec4(0.0, 0.0, 0.0, 1.0);
	} else {
		float res = float(max(mask_resolution, 1));
		float step_uv = 1.0 / res;
		float metres_per_cell = field_size / res;
		int lee_steps = int(ceil(lee_distance_m / metres_per_cell));
		int windward_steps = int(ceil(windward_distance_m / metres_per_cell));

		vec2 dir = length(wind_dir) > 1e-6 ? normalize(wind_dir) : vec2(1.0, 0.0);
		float value = open_exposure;

		// Upwind hit means this cell sits in an obstacle's lee.
		float lee_hit = march(uv, -dir, lee_steps, step_uv);
		if (lee_hit >= 0.0) {
			value = mix(lee_shelter, open_exposure, lee_hit / float(lee_steps));
		} else {
			// Downwind hit means this cell is the exposed approach face.
			float wind_hit = march(uv, dir, windward_steps, step_uv);
			if (wind_hit >= 0.0) {
				value = mix(windward_shelter, open_exposure, wind_hit / float(windward_steps));
			}
		}

		COLOR = vec4(value, value, value, 1.0);
	}
}
