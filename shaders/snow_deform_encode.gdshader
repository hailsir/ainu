shader_type spatial;
render_mode unshaded, cull_disabled, shadows_disabled, fog_disabled, specular_disabled;

// Worn by SnowDeformer proxy meshes, seen only by the snow deform camera.
// That camera looks straight up from under the ground plane, so ordinary depth
// testing already keeps the lowest surface of each deformer -- its underside.
// This shader just writes that surface's world height.
//
// Encoded as sqrt(height01) to spend more of the 8-bit range on shallow
// prints; snow_sim.gdshader undoes it with deform_decode_gamma.

uniform float ground_y = 0.0;
uniform float deform_range = 2.0;

varying float world_y;

void vertex() {
	world_y = (MODEL_MATRIX * vec4(VERTEX, 1.0)).y;
}

void fragment() {
	float h01 = clamp((world_y - ground_y) / deform_range, 0.0, 1.0);
	ALBEDO = vec3(sqrt(h01));
	ALPHA = 1.0;
}
