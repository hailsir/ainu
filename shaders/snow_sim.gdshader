shader_type canvas_item;
render_mode unshaded, blend_disabled;

// Snow heightfield simulation pass (SNOWPLAN sections 1, 3, 4).
//
// Runs as a ping-pong full-rect pass inside a 1200x1200 HDR SubViewport.
// State layout:
//   R = snow height in metres above the ground plane
//   G = freshness. 0 = deformed this instant, 1 = pristine / undisturbed.
//   B = pending deposition not yet folded into R (see the accumulator below).
//   A = reserved.

uniform sampler2D prev_state : filter_nearest, repeat_disable;
// R = BaseDepth (0..1 of max_depth), G = Capacity, B = Compaction fraction, A = ShelterFactor.
uniform sampler2D authored_maps : filter_linear, repeat_disable;
uniform sampler2D deform_tex : filter_nearest, repeat_disable;
uniform sampler2D restore_tex : filter_nearest, repeat_disable;
// Live shelter factor for the current wind direction (shaders/snow_shelter.gdshader).
// When off, shelter falls back to the alpha channel baked into authored_maps.
uniform sampler2D shelter_tex : filter_linear, repeat_disable;
uniform bool use_dynamic_shelter = false;

// 0 = simulate, 1 = initialise from authored maps, 2 = restore from restore_tex.
uniform int init_mode = 1;

uniform float max_depth = 2.0;
uniform float cell_size = 0.1;
uniform float texel = 0.000833333;

// Deform decode. The deform pass writes sqrt(height01) so that shallow prints
// keep more of the 8-bit range; deform_decode_gamma inverts that encode.
uniform float deform_range = 2.0;
uniform float deform_decode_gamma = 2.0;
// Godot's 3D SubViewport render target is RGBA8 with sRGB storage, and a
// canvas shader samples it without the hardware decode. Measured round trip:
// srgb_to_linear() recovers the written value to within 8-bit quantisation
// (0.10 m read back as 0.106 m, 1.50 m as 1.519 m). Set false if a future
// renderer hands back linear values.
uniform bool deform_srgb_storage = true;

// Wind accumulation.
uniform float accum_dt = 0.0;
uniform float base_rate = 0.000333;
uniform float snowfall_rate = 0.0;
uniform float wind_strength = 1.0;
uniform vec2 wind_dir = vec2(1.0, 0.0);
uniform float slope_wind_influence = 0.8;
uniform float erosion_rate = 0.00006;
uniform float freshness_recovery = 0.02;
// Growth rate of pristine snow relative to a fresh trail. Trails still refill
// first, but drifts can form on untouched lee ground.
uniform float pristine_growth = 0.35;
// How far falling snow follows the wind's shelter pattern. 0 = falls evenly
// everywhere, 1 = deposits exactly in proportion to shelter. Driven from wind
// strength by SnowField, so still air lays an even blanket and a blizzard
// scours windward faces and packs the lee.
uniform float snowfall_wind_bias = 0.0;

// Optional displaced-snow rim around prints (SNOWPLAN section 2).
uniform float rim_rate = 0.002;
uniform float rim_max_scale = 1.1;

// Deposition accumulator. The state target is RGBA16F, whose spacing between
// representable values is about 1/2048 of the value itself: roughly 1 mm at a
// metre of lying snow. Real Rishiri deposition is around 1e-5 m per 0.5 s
// accumulation step, so adding it straight to the height rounds back to the
// height and the field freezes — deep snow first, because its texels are the
// coarsest. Growth is therefore banked in B, which starts near zero and keeps
// full precision there, and folded into R once it is large enough to survive
// the add. flush is the bank size as a fraction of the current height, with a
// floor for shallow snow.
uniform float accum_flush_fraction = 0.0025;
uniform float accum_flush_min = 0.0005;


float srgb_to_linear(float c) {
	return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4);
}

void fragment() {
	vec2 uv = UV;
	vec4 m = texture(authored_maps, uv);
	float base = m.r * max_depth;
	float comp = clamp(m.b, 0.0, 1.0);
	float shelter = clamp(use_dynamic_shelter ? texture(shelter_tex, uv).r : m.a, 0.0, 1.0);
	float floor_h = base * comp;
	float cap = max(m.g * max_depth, floor_h);

	float out_h = base;
	float out_fresh = 1.0;
	float out_pending = 0.0;

	if (init_mode == 2) {
		vec4 r = texture(restore_tex, uv);
		out_h = r.r;
		out_fresh = clamp(r.g, 0.0, 1.0);
		out_pending = r.b;
	} else if (init_mode == 0) {
		vec4 s = texture(prev_state, uv);
		float h = s.r;
		float fresh = s.g;
		float pending = s.b;

		// ---- deformation ------------------------------------------------
		// The deform camera looks up from below, so its screen V runs
		// opposite to the field's V.
		vec2 duv = vec2(uv.x, 1.0 - uv.y);
		vec4 d = texture(deform_tex, duv);
		bool deformed_here = false;
		if (d.a > 0.5) {
			float e = clamp(d.r, 0.0, 1.0);
			if (deform_srgb_storage) {
				e = srgb_to_linear(e);
			}
			float dh = pow(e, deform_decode_gamma) * deform_range;
			if (dh < h) {
				h = max(dh, floor_h);
				fresh = 0.0;
				deformed_here = true;
				// Banked growth belongs to snow that has just been pressed
				// away; releasing it later would make the print bounce back.
				pending = 0.0;
			}
		}

		if (rim_rate > 0.0 && !deformed_here) {
			float n = texture(deform_tex, duv + vec2(texel, 0.0)).a;
			n = max(n, texture(deform_tex, duv - vec2(texel, 0.0)).a);
			n = max(n, texture(deform_tex, duv + vec2(0.0, texel)).a);
			n = max(n, texture(deform_tex, duv - vec2(0.0, texel)).a);
			if (n > 0.5) {
				h = min(h + rim_rate, base * rim_max_scale);
			}
		}

		// ---- wind accumulation -------------------------------------------
		if (accum_dt > 0.0) {
			float hl = texture(prev_state, uv - vec2(texel, 0.0)).r;
			float hr = texture(prev_state, uv + vec2(texel, 0.0)).r;
			float hu = texture(prev_state, uv - vec2(0.0, texel)).r;
			float hd = texture(prev_state, uv + vec2(0.0, texel)).r;
			vec2 grad = vec2(hr - hl, hd - hu) / (2.0 * cell_size);
			float g = length(grad);
			float facing = 0.0;
			if (g > 1e-5) {
				facing = clamp(dot(grad / g, wind_dir), -1.0, 1.0);
			}
			// >0 where the surface climbs into the wind.
			float windward = max(facing, 0.0) * min(g, 1.0);

			float trail_bias = pristine_growth + (1.0 - pristine_growth) * (1.0 - fresh);
			float rate = base_rate * wind_strength * shelter * trail_bias;
			rate *= (1.0 - windward * slope_wind_influence);
			rate = max(rate, 0.0);

			pending += (rate + snowfall_rate * mix(1.0, shelter, snowfall_wind_bias)) * accum_dt;
			pending -= erosion_rate * windward * wind_strength * accum_dt;
			float flush = max(abs(h) * accum_flush_fraction, accum_flush_min);
			if (abs(pending) >= flush) {
				h += pending;
				pending = 0.0;
			}
			fresh = min(fresh + freshness_recovery * accum_dt, 1.0);
		}

		out_h = h;
		out_fresh = fresh;
		out_pending = pending;
	}

	float clamped = clamp(out_h, floor_h, cap);
	if (clamped != out_h) {
		// The capacity ceiling or compaction floor just rejected this growth,
		// so drop the bank instead of letting it press against the limit.
		out_pending = 0.0;
	}
	COLOR = vec4(clamped, clamp(out_fresh, 0.0, 1.0), out_pending, 1.0);
}
