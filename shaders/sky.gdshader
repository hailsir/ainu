shader_type sky;
render_mode use_half_res_pass;

// Day/night sky for the Forward Mobile renderer (plans/SKYPLAN.md sections 3
// and 4). Every uniform is pushed by SkyController; the shader reads TIME only
// for the star twinkle, and only inside the half-resolution pass, so the
// radiance cubemap never churns on animation.
//
// Pass layout:
//   AT_HALF_RES_PASS  the whole sky bar the sun and moon discs: gradient, then
//                     stars, then the two cloud layers composited over it.
//   background pass   that result, plus the discs at full resolution.
//   AT_CUBEMAP_PASS   gradient + sun + moon only. The half-res passes do not run
//                     for the radiance cubemap, which is exactly what we want:
//                     reflections stay clear and cheap.
//
// The half-res pass composites the clouds itself rather than handing back a
// premultiplied colour and a coverage in ALPHA: measured on Forward Mobile,
// HALF_RES_COLOR.a does not return what the half-res pass wrote to ALPHA (1.0
// came back as ~1.94), which drove the green and blue channels negative and
// turned every cloud gold. Only .rgb is trustworthy, so only .rgb is used.
//
// The consequence is that the sun and moon discs are not occluded per pixel by
// cloud. They are dimmed by the sun_occlusion uniform instead, which is what
// SKYPLAN section 4 specifies anyway, and which also drives the DirectionalLight.

group_uniforms Gradient;
uniform vec3 zenith_color : source_color = vec3(0.35, 0.55, 0.82);
uniform vec3 horizon_color : source_color = vec3(0.65, 0.75, 0.85);
uniform vec3 ground_color : source_color = vec3(0.15, 0.12, 0.10);
// Weather: the whole gradient lerps toward the fog colour as severity rises.
uniform vec3 fog_color : source_color = vec3(0.72, 0.78, 0.88);
uniform float fog_blend : hint_range(0.0, 1.0) = 0.0;
uniform float sky_energy : hint_range(0.0, 4.0) = 1.0;

group_uniforms Sun;
uniform vec3 sun_dir = vec3(0.0, 0.5, 0.86);
uniform vec3 sun_color : source_color = vec3(1.0, 0.95, 0.85);
// Brightness of the disc itself. Kept well under the filmic tonemap's knee so
// the disc stays a disc instead of blooming into a white blob.
uniform float sun_disc_energy : hint_range(0.0, 8.0) = 6.0;
uniform float sun_glow_strength : hint_range(0.0, 4.0) = 0.6;
uniform float horizon_glow_strength : hint_range(0.0, 4.0) = 1.1;
// Strength of the dawn/dusk horizon glow, 0 for most of the day. Windowed on
// the CPU rather than passed as an elevation in degrees: degrees move 0.025 per
// 10 Hz tick, which is over the write epsilon, so it dirtied the radiance map
// every single tick for a term that is zero from mid-morning to late afternoon.
uniform float horizon_glow : hint_range(0.0, 1.0) = 0.0;
// 0 in the dark, 1 in full day. Scales how strongly clouds are lit.
uniform float sun_strength : hint_range(0.0, 1.0) = 1.0;
// CPU-sampled low-cloud density along sun_dir, so a cloud crossing the sun
// dims the disc and the glow the same frame it dims DirectionalLight3D.
uniform float sun_occlusion : hint_range(0.0, 1.0) = 0.0;

group_uniforms Moon;
uniform vec3 moon_dir = vec3(0.0, -0.5, -0.86);
uniform vec3 moon_color : source_color = vec3(0.62, 0.70, 0.95);
uniform float moon_disc_energy : hint_range(0.0, 8.0) = 1.6;
uniform float moon_illum : hint_range(0.0, 1.0) = 1.0;
// Scene moonlight, used to keep clouds faintly visible at night.
uniform float moon_energy : hint_range(0.0, 1.0) = 0.0;

group_uniforms Stars;
uniform float star_alpha : hint_range(0.0, 1.0) = 0.0;
uniform float star_density : hint_range(0.9, 1.0) = 0.974;
uniform float star_twinkle : hint_range(0.0, 1.0) = 1.0;

group_uniforms Clouds;
uniform sampler2D cloud_noise : filter_linear_mipmap, repeat_enable;
uniform float cloud_amount : hint_range(0.0, 1.0) = 1.0;
// Density thresholds, NOT coverage fractions. SkyController turns a 0..1
// coverage into the pair of noise values that actually covers that fraction of
// the sky, because an FBM's values pile up around its median and a naive
// 1 - coverage threshold covers almost nothing.
// High cirrus: thin, far, slow, and lit before anything on the ground is.
uniform vec2 high_edges = vec2(0.72, 0.80);
uniform float high_uv_scale = 0.1333;
uniform vec2 high_scroll = vec2(0.0);
uniform float high_opacity : hint_range(0.0, 1.0) = 0.6;
// Low stratus: the overcast that comes with weather severity.
uniform vec2 low_edges = vec2(0.66, 0.82);
uniform float low_uv_scale = 0.1875;
uniform vec2 low_scroll = vec2(0.0);
// Below this the planar projection blows up, so the layers fade out.
uniform float horizon_fade_lo = 0.02;
uniform float horizon_fade_hi = 0.12;


vec3 hash33(vec3 p) {
	p = fract(p * vec3(0.1031, 0.1030, 0.0973));
	p += dot(p, p.yxz + 33.33);
	return fract((p.xxy + p.yxx) * p.zyx);
}


vec3 base_gradient(vec3 dir) {
	vec3 up_half = mix(horizon_color, zenith_color, pow(max(dir.y, 0.0), 0.6));
	vec3 down_half = mix(horizon_color, ground_color, pow(max(-dir.y, 0.0), 0.5));
	vec3 col = dir.y >= 0.0 ? up_half : down_half;
	return mix(col, fog_color, fog_blend);
}


vec3 sun_term(vec3 dir) {
	float sd = dot(dir, sun_dir);
	// The sun subtends 0.53 deg, so the disc is 0.265 deg of half-angle with a
	// 0.135 deg soft edge.
	float disc = smoothstep(cos(radians(0.40)), cos(radians(0.25)), sd);
	float glow = pow(max(sd, 0.0), 64.0);

	// Dawn and dusk warm the horizon, but only on the sun's own side and only
	// while the sun is low. Deliberately un-normalised: near the zenith the
	// horizontal dot falls off on its own, which is the wanted behaviour.
	float side = pow(max(dot(dir.xz, sun_dir.xz), 0.0), 4.0);
	float band = side * (1.0 - abs(dir.y));

	float clear = 1.0 - sun_occlusion;
	return sun_color * clear * (
		disc * sun_disc_energy
		+ glow * sun_glow_strength
		+ band * horizon_glow * horizon_glow_strength);
}


vec3 moon_term(vec3 dir) {
	float md = dot(dir, moon_dir);
	float disc = smoothstep(cos(radians(0.40)), cos(radians(0.25)), md);
	if (disc <= 0.0) {
		return moon_color * pow(max(md, 0.0), 700.0) * moon_disc_energy * 0.15 * moon_illum;
	}

	// Disc-local frame: +x toward the sun, so the terminator is an ellipse
	// x = (1 - 2 * illum) * sqrt(1 - y * y) and the lit side is x above it.
	vec3 tangent = sun_dir - moon_dir * dot(sun_dir, moon_dir);
	float tl = length(tangent);
	vec3 tx = tl > 1e-4 ? tangent / tl : vec3(1.0, 0.0, 0.0);
	vec3 ty = cross(moon_dir, tx);
	vec3 off = (dir - moon_dir * md) / radians(0.25);
	float px = dot(off, tx);
	float py = dot(off, ty);
	float edge = (1.0 - 2.0 * moon_illum) * sqrt(max(1.0 - py * py, 0.0));
	float lit = smoothstep(edge - 0.15, edge + 0.15, px);

	// Earthshine keeps the dark limb readable instead of a bitten-off disc.
	return moon_color * disc * moon_disc_energy * mix(0.05, 1.0, lit);
}


float star_field(vec3 dir) {
	vec3 q = dir * 260.0;
	vec3 cell = floor(q);
	vec3 f = fract(q) - 0.5;
	vec3 h = hash33(cell);

	float present = step(star_density, h.z);
	float d = length(f - (h - 0.5) * 0.7);
	float core = smoothstep(0.22, 0.0, d);
	float magnitude = mix(0.25, 1.0, h.x);
	float twinkle = mix(1.0, 0.5 + 0.5 * sin(h.y * 40.0 + TIME * 1.7), star_twinkle * 0.5);

	// Fade into the murk near the horizon rather than ending on a hard line.
	return present * core * magnitude * twinkle * smoothstep(-0.02, 0.15, dir.y);
}


float cloud_layer(vec3 dir, float uv_scale, vec2 scroll, vec2 edges) {
	if (dir.y <= horizon_fade_lo || edges.y >= 1.0) {
		return 0.0;
	}
	// Planar projection: the layer is a flat sheet, so the ray hits it at
	// dir.xz / dir.y scaled by height / tile_size (folded into uv_scale).
	vec2 uv = dir.xz / dir.y * uv_scale + scroll;
	float n = texture(cloud_noise, uv).r;
	float d = smoothstep(edges.x, edges.y, n);
	// The projection blows up at the horizon, so the layers fade out into it.
	return d * smoothstep(horizon_fade_lo, horizon_fade_hi, dir.y);
}


void sky() {
	if (AT_HALF_RES_PASS) {
		float d_high = cloud_layer(EYEDIR, high_uv_scale, high_scroll, high_edges) * cloud_amount;
		float d_low = cloud_layer(EYEDIR, low_uv_scale, low_scroll, low_edges) * cloud_amount;

		float a_high = d_high * high_opacity;
		float a_low = d_low;
		float a_total = 1.0 - (1.0 - a_high) * (1.0 - a_low);

		// Moonlight keeps clouds as pale patches against the stars instead of
		// letting them vanish into the night gradient.
		vec3 moon_fill = moon_color * moon_energy * 4.0;
		vec3 lit_col = sun_color * 1.15 * sun_strength + moon_fill;
		// Seen from below, an overcast is a mid grey lit by sun and sky both.
		// Tying it to the horizon colour alone made it darker than a clear
		// zenith, which read as holes in the sky rather than cloud.
		vec3 under_col = horizon_color * 0.35 + sun_color * 0.45 * sun_strength + moon_fill * 0.3;
		float facing = pow(max(dot(EYEDIR, sun_dir), 0.0), 2.0) * sun_strength;
		vec3 low_col = mix(under_col, lit_col, facing);
		// Cirrus is thin ice: brighter, whiter, and lit before anything below.
		vec3 high_col = mix(under_col, lit_col, max(facing, 0.5)) * 1.1;

		// Stars sit behind the cloud deck, so they go in before it.
		vec3 col = base_gradient(EYEDIR)
			+ vec3(1.0, 0.97, 0.92) * star_field(EYEDIR) * star_alpha;
		col = col * (1.0 - a_total)
			+ high_col * a_high * (1.0 - a_low) + low_col * a_low;

		COLOR = col;
		ALPHA = 1.0;
	} else {
		// The half-resolution pass does not run for the radiance cubemap.
		vec3 col = AT_CUBEMAP_PASS ? base_gradient(EYEDIR) : HALF_RES_COLOR.rgb;
		col += sun_term(EYEDIR) + moon_term(EYEDIR);
		COLOR = col * sky_energy;
		ALPHA = 1.0;
	}
}
