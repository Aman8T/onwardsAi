/*
 * GLSL ES 3.00 sources for the cursor light. One full-screen triangle; every pixel inside 3.2 radii of
 * the heart computes its own colour in OKLab (Ottosson's matrices), clips chroma to the sRGB gamut, and
 * dithers ±1 LSB before the 8-bit output so the long pastel falloff never bands. The numbers come from
 * palette.ts so the contrast proof in scripts/palette-check.ts measures the same colours.
 */
import { LIGHT } from './palette';

export const UNIFORMS = ['uCenter', 'uDir', 'uStretch', 'uRadius', 'uTime', 'uPhase', 'uHue', 'uHue2', 'uK', 'uAlpha', 'uSeed', 'uTone'] as const;

export type Uniform = (typeof UNIFORMS)[number];

const f = (n: number): string => (Number.isInteger(n) ? `${n}.0` : String(n));

export const VERT = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID & 1) << 2) - 1.0, float((gl_VertexID & 2) << 1) - 1.0);
  gl_Position = vec4(p, 0.0, 1.0);
}`;

export const FRAG = `#version 300 es
precision highp float;
uniform vec2 uCenter;    // the heart, backing px, y up
uniform vec2 uDir;       // unit direction of travel
uniform float uStretch;  // 0..maxStretch
uniform float uRadius;   // body radius, backing px
uniform float uTime;     // seconds
uniform float uPhase;    // travel-driven noise phase
uniform float uHue;      // degrees, the heart
uniform float uHue2;     // degrees, the body
uniform float uK;        // chroma multiplier (warm-sector easing)
uniform float uAlpha;    // 0..1 overall strength
uniform float uSeed;     // per-session
uniform vec4 uTone;      // bodyL, heartL, bodyC, heartC: the light field's or the dark field's (palette.ts)
out vec4 o;

vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec2 mod289(vec2 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec3 permute(vec3 x) { return mod289(((x * 34.0) + 1.0) * x); }
// 2D simplex noise (Ian McEwan, Ashima Arts; MIT).
float snoise(vec2 v) {
  const vec4 C = vec4(0.211324865405187, 0.366025403784439, -0.577350269189626, 0.024390243902439);
  vec2 i = floor(v + dot(v, C.yy));
  vec2 x0 = v - i + dot(i, C.xx);
  vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
  vec4 x12 = x0.xyxy + C.xxzz;
  x12.xy -= i1;
  i = mod289(i);
  vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
  vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
  m = m * m;
  m = m * m;
  vec3 x = 2.0 * fract(p * C.www) - 1.0;
  vec3 h = abs(x) - 0.5;
  vec3 ox = floor(x + 0.5);
  vec3 a0 = x - ox;
  m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
  vec3 g;
  g.x = a0.x * x0.x + h.x * x0.y;
  g.yz = a0.yz * x12.xz + h.yz * x12.yw;
  return 130.0 * dot(m, g);
}
float fbm(vec2 p) { return 0.68 * snoise(p) + 0.32 * snoise(p * 2.1 + 7.3); }

vec3 oklabToLinear(vec3 c) {
  float l_ = c.x + 0.3963377774 * c.y + 0.2158037573 * c.z;
  float m_ = c.x - 0.1055613458 * c.y - 0.0638541728 * c.z;
  float s_ = c.x - 0.0894841775 * c.y - 1.2914855480 * c.z;
  float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
  return vec3(
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s);
}
vec3 linearToSrgb(vec3 c) {
  return mix(12.92 * c, 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}
// OKLCH → gamma sRGB, reducing chroma until the colour fits the gamut (lightness and hue are kept).
vec3 oklch(float L, float C, float hueDeg) {
  float h = radians(hueDeg);
  vec3 lin = oklabToLinear(vec3(L, C * cos(h), C * sin(h)));
  for (int i = 0; i < 4; i++) {
    if (all(greaterThanEqual(lin, vec3(-0.0005))) && all(lessThanEqual(lin, vec3(1.0005)))) break;
    C *= 0.72;
    lin = oklabToLinear(vec3(L, C * cos(h), C * sin(h)));
  }
  return linearToSrgb(clamp(lin, 0.0, 1.0));
}
float mixHue(float a, float b, float w) { float d = mod(b - a + 540.0, 360.0) - 180.0; return a + d * w; }
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  vec2 q = (gl_FragCoord.xy - uCenter) / uRadius;
  // the field elongates along the direction of travel
  float along = dot(q, uDir);
  vec2 perp = q - along * uDir;
  q = perp + uDir * (along / (1.0 + uStretch));
  float r = length(q);
  if (r > 3.2) { o = vec4(0.0); return; }
  float t = uTime;
  vec2 s = vec2(uSeed, uSeed * 1.7);
  // the body, and a wider halo that trails a little behind it; each warped by its own slow noise
  float n1 = fbm(q * 0.95 + vec2(t * ${f(LIGHT.noise.body[0])}, -t * ${f(LIGHT.noise.body[1])}) + uPhase * 0.6 + s);
  vec2 qh = q + uDir * uStretch * 0.6;
  float n2 = fbm(qh * 0.5 - vec2(t * ${f(LIGHT.noise.halo[0])}, t * ${f(LIGHT.noise.halo[1])}) + uPhase * 0.3 + s + 11.0);
  float rc = r * (1.0 + ${f(LIGHT.noise.warpBody)} * n1);
  float rh = length(qh) * 0.55 * (1.0 + ${f(LIGHT.noise.warpHalo)} * n2);
  float body = exp(-rc * rc * 2.0);
  float halo = exp(-rh * rh * 1.4);
  float d = clamp(body * 0.78 + halo * 0.5, 0.0, 1.0);
  // light through paper: the heart is paler and less saturated, the colour sits in the body
  float w = smoothstep(0.0, 1.0, body);
  float L = mix(uTone.x, uTone.y, w);
  float C = mix(uTone.z, uTone.w, w) * uK;
  float H = mixHue(uHue2, uHue, w);
  vec3 c = oklch(L, C, H);
  // ±1 LSB triangular dither on colour and alpha, so 8-bit output never bands
  float dn = (hash(gl_FragCoord.xy + fract(t) * 13.0) + hash(gl_FragCoord.xy * 1.3 + 9.1 + fract(t) * 7.0)) - 1.0;
  float a = clamp(d * uAlpha + dn / 255.0, 0.0, 1.0);
  c = clamp(c + dn / 255.0, 0.0, 1.0);
  o = vec4(c * a, a);
}`;
