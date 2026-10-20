/*
 * GLSL ES 3.00 sources for the cursor light's viscous wake. One full-screen triangle (the engine scissors it
 * to the chain's bounding box); every pixel evaluates the head and ten followers as soft ellipses, joins them
 * with a capped union (never a sum, so overlaps cannot brighten into a glow), blends their colour by weight
 * in OKLCH along the short hue arc, clips chroma to the sRGB gamut and dithers ±1 LSB before the 8-bit
 * output so the long falloff never bands. The numbers come from palette.ts so the contrast proof in
 * scripts/palette-check.ts measures the same colours.
 */
import { LIGHT } from './palette';

export const UNIFORMS = ['uNodes', 'uDir', 'uStretch', 'uRadius', 'uTime', 'uSeed', 'uAlpha', 'uHue', 'uTone'] as const;

export type Uniform = (typeof UNIFORMS)[number];

const f = (n: number): string => (Number.isInteger(n) ? `${n}.0` : String(n));

const W = LIGHT.wake;

export const VERT = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID & 1) << 2) - 1.0, float((gl_VertexID & 2) << 1) - 1.0);
  gl_Position = vec4(p, 0.0, 1.0);
}`;

export const FRAG = `#version 300 es
precision highp float;
const int N = ${W.N};
const int NB = ${W.N + 1};
uniform vec2 uNodes[NB]; // the head, then the followers; backing px, y up
uniform vec2 uDir;       // unit direction of travel (y up)
uniform float uStretch;  // 0..stretch.max
uniform float uRadius;   // the head's radius, backing px
uniform float uTime;     // seconds
uniform float uSeed;     // per-session
uniform float uAlpha;    // peak opacity right now (the scheme's peak times the fade)
uniform float uHue;      // degrees, the head
uniform vec4 uTone;      // head L, head C, tail L, tail C (palette.ts, the scheme's own)
out vec4 o;

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
// the warm-sector chroma easing: the same curve as warmEasing() in palette.ts
float warm(float h) { float d = abs(mod(h - 85.0 + 540.0, 360.0) - 180.0); return 1.0 - 0.25 * max(0.0, 1.0 - d / 50.0); }
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  vec2 p = gl_FragCoord.xy;
  float miss = 1.0;   // product of (1 - a_i): the union is 1 - miss
  float wsum = 0.0;
  float L = 0.0, C = 0.0, H = uHue;
  float thin = sqrt(1.0 + uStretch);
  for (int i = 0; i < NB; i++) {
    float u = float(i) / float(N);
    float R = uRadius * (1.0 - ${f(W.radius.taper)} * u);
    vec2 a = uNodes[i];
    // the blob is a soft ellipse on its segment toward the tail, centred part-way back along it
    vec2 seg = (i < NB - 1) ? uNodes[i + 1] - a : a - uNodes[i - 1];
    float sl = length(seg);
    vec2 ax = sl > 0.01 ? seg / sl : uDir;
    vec2 c = (i < NB - 1) ? a + seg * ${f(W.centreBack)} : a;
    float lenF = 1.0 + uStretch + ${f(W.stretch.segment)} * sl / R;
    vec2 q = (p - c) / R;
    float al = dot(q, ax);
    vec2 pe = q - al * ax;
    float d2 = (al / lenF) * (al / lenF) + dot(pe, pe) * thin * thin;
    float g = exp(-2.0 * d2);
    float prof = pow(1.0 - ${f(W.alpha.fall)} * u, ${f(W.alpha.power)}) * (i > 0 ? ${f(W.alpha.follower)} : 1.0);
    float ai = g * prof;
    miss *= 1.0 - ai;
    float hi = uHue + ${f(W.hue.tail)} * u;
    float Li = mix(uTone.x, uTone.z, u);
    float Ci = mix(uTone.y, uTone.w, u) * warm(hi);
    float prev = wsum;
    wsum += ai;
    if (wsum > 1e-6) {
      float k = ai / wsum;
      L = mix(L, Li, k);
      C = mix(C, Ci, k);
      H = prev > 1e-6 ? mixHue(H, hi, k) : hi;
    }
  }
  if (wsum < 2e-3) { o = vec4(0.0); return; } // under half an 8-bit step: nothing to draw
  // a capped smooth union: x / (1 + x^4)^(1/4), normalised so a full overlap is exactly the peak
  float U = 1.0 - miss;
  float cap = 1.189207115 * U / pow(1.0 + U * U * U * U, 0.25);
  vec3 col = oklch(L, C, H);
  // ±1 LSB triangular dither on colour and alpha, so 8-bit output never bands
  float t = fract(uTime);
  float dn = (hash(gl_FragCoord.xy + t * 13.0 + uSeed) + hash(gl_FragCoord.xy * 1.3 + 9.1 + t * 7.0)) - 1.0;
  float a = clamp(cap * uAlpha + dn / 255.0, 0.0, 1.0);
  col = clamp(col + dn / 255.0, 0.0, 1.0);
  o = vec4(col * a, a);
}`;
