shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_back, diffuse_burley, specular_schlick_ggx;

// Snow ground surface (SNOWPLAN section 2).
// Displaces a subdivided plane by the simulated heightfield, rebuilds normals
// from finite differences at full field resolution, and blends a fresh-powder
// layer against a compacted-trail layer using the freshness channel.
//
// Assumes the ground mesh is unrotated and unscaled, so a model-space Y offset
// equals a world-space Y offset.

uniform sampler2D snow_state : filter_linear, repeat_disable;

uniform vec2 field_center = vec2(0.0, 0.0);
uniform float field_size = 120.0;
uniform float texel = 0.000833333;

uniform vec3 powder_albedo : source_color = vec3(0.93, 0.95, 1.0);
uniform vec3 packed_albedo : source_color = vec3(0.64, 0.70, 0.83);
uniform vec3 crease_tint : source_color = vec3(0.45, 0.53, 0.72);
uniform float powder_roughness : hint_range(0.0, 1.0) = 0.90;
uniform float packed_roughness : hint_range(0.0, 1.0) = 0.42;
uniform float crease_strength : hint_range(0.0, 4.0) = 1.1;
uniform float normal_strength : hint_range(0.0, 4.0) = 1.0;

// Distance LOD: past lod_start the normal taps widen and the crease term fades,
// past lod_end displacement itself relaxes toward the smoothed height.
uniform float lod_start = 40.0;
uniform float lod_end = 90.0;
uniform float lod_tap_scale = 6.0;

// UV footprint of one mesh quad. Displacement averages over it instead of
// point-sampling, so 0.5 m vertices cannot chase a 10 cm print and tear the
// surface into facets. Normals still come from full-resolution taps, so the
// print keeps its crisp edge.
uniform float vertex_footprint_uv = 0.0041667;

// Moving cloud shadows (SKYPLAN task H2). SkyController pushes the same noise
// texture, scroll and coverage the sky shader uses, so a shadow on the ground
// lines up with the cloud overhead. Inert while cloud_shadow_strength is 0.
//
// Forward Mobile has no light projectors for directional lights, so the shadow
// is folded into AO rather than multiplying the direct-light term exactly: with
// AO_LIGHT_AFFECT at 0.7 it takes 70 % out of direct light and all of it out of
// ambient, which for an overcast-lit snowfield reads correctly enough.
uniform sampler2D cloud_noise : filter_linear_mipmap, repeat_enable;
uniform float cloud_tile_size = 8000.0;
uniform vec2 cloud_scroll = vec2(0.0);
// Density thresholds, matching the sky shader's low_edges (see SkyController).
uniform vec2 cloud_edges = vec2(1.0, 1.0);
uniform float cloud_shadow_strength : hint_range(0.0, 1.0) = 0.0;

varying float lod_fade;
varying vec2 cloud_world_xz;

vec2 field_uv(vec2 world_xz) {
	return (world_xz - (field_center - vec2(field_size * 0.5))) / field_size;
}

void vertex() {
	vec3 wp = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	vec2 uv = field_uv(wp.xz);
	lod_fade = smoothstep(lod_start, lod_end, distance(wp, CAMERA_POSITION_WORLD));

	float f = vertex_footprint_uv * 0.5 * mix(1.0, lod_tap_scale, lod_fade);
	float h = (
		2.0 * textureLod(snow_state, uv, 0.0).r +
		textureLod(snow_state, uv + vec2(f, 0.0), 0.0).r +
		textureLod(snow_state, uv - vec2(f, 0.0), 0.0).r +
		textureLod(snow_state, uv + vec2(0.0, f), 0.0).r +
		textureLod(snow_state, uv - vec2(0.0, f), 0.0).r) / 6.0;

	VERTEX.y += h;
	UV = uv;
	cloud_world_xz = wp.xz;
}


// Density of the low cloud layer directly overhead, sampled from the same noise
// and scroll the sky shader projects onto its 1.5 km sheet.
float cloud_shadow_at(vec2 world_pos) {
	if (cloud_shadow_strength <= 0.0 || cloud_edges.y >= 1.0) {
		return 1.0;
	}
	float n = texture(cloud_noise, world_pos / max(cloud_tile_size, 1.0) + cloud_scroll).r;
	float d = smoothstep(cloud_edges.x, cloud_edges.y, n);
	return 1.0 - d * cloud_shadow_strength;
}

void fragment() {
	vec2 uv = UV;
	float o = texel * mix(1.0, lod_tap_scale, lod_fade);
	float hl = texture(snow_state, uv - vec2(o, 0.0)).r;
	float hr = texture(snow_state, uv + vec2(o, 0.0)).r;
	float hu = texture(snow_state, uv - vec2(0.0, o)).r;
	float hd = texture(snow_state, uv + vec2(0.0, o)).r;

	float step_m = 2.0 * o * field_size;
	vec2 grad = vec2(hr - hl, hd - hu) / step_m;

	vec3 n = normalize(vec3(-grad.x * normal_strength, 1.0, -grad.y * normal_strength));
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);

	float fresh = clamp(texture(snow_state, uv).g, 0.0, 1.0);
	vec3 albedo = mix(packed_albedo, powder_albedo, fresh);
	float rough = mix(packed_roughness, powder_roughness, fresh);

	// Crisp trail-edge darkening: steep walls of a print read as shadowed snow.
	float crease = clamp(length(grad) * crease_strength, 0.0, 1.0) * (1.0 - lod_fade);
	albedo = mix(albedo, crease_tint, crease);

	ALBEDO = albedo;
	ROUGHNESS = rough;
	SPECULAR = mix(0.5, 0.35, fresh);
	// Stands in for the subsurface tint: powder scatters at grazing angles.
	RIM = mix(0.08, 0.35, fresh);
	RIM_TINT = 0.55;
	AO = (1.0 - crease * 0.45) * cloud_shadow_at(cloud_world_xz);
	AO_LIGHT_AFFECT = 0.7;
}
