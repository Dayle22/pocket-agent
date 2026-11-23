"""Offscreen PyQt6 OpenGL GLSL shader renderer for the 4 raw-WebGL VoiceOrbs.

Executes the upstream GLSL fragment shaders from amunozdev/voiceorbs with a
3D spherical glass optics, cursor-tracking lighting, and live desktop
refraction pass:
- siri-sheet
- aura-field
- iridescent-flow
- duotone-flow
"""

from __future__ import annotations

import ctypes
from typing import Any

from PyQt6.QtGui import (
    QImage,
    QOffscreenSurface,
    QOpenGLContext,
    QSurfaceFormat,
    QVector2D,
    QVector3D,
    QVector4D,
)
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVersionFunctionsFactory,
    QOpenGLVersionProfile,
)

SHADER_ORB_IDS: tuple[str, ...] = (
    "siri-sheet",
    "aura-field",
    "iridescent-flow",
    "duotone-flow",
)

_VERT_SRC = """
attribute vec2 aPos;
void main() {
  gl_Position = vec4(aPos, 0.0, 1.0);
}
"""

_GLSL_HEADER = """
#ifdef GL_ES
precision highp float;
#endif
"""

_SPHERE_GLASS_GLSL = """
vec3 sphereLightDir(vec2 pointer, float hover) {
  vec2 ptr = vec2(pointer.x, -pointer.y);
  vec3 lIdle = normalize(vec3(-0.42, 0.58, 0.70));
  vec3 lPtr = normalize(vec3(ptr * 0.78, 0.82));
  return normalize(mix(lIdle, lPtr, 0.75 * clamp(hover, 0.0, 1.0)));
}

vec3 applySphereGlass(
    vec3 col,
    vec2 sp,
    float sr,
    vec3 L,
    vec3 accentA,
    vec3 accentB,
    float hover,
    float rimStrength,
    float volumeDarken,
    float specStrength,
    float disabledAmt
) {
  float rr = dot(sp, sp);
  float z = sqrt(max(0.0, 1.0 - min(rr, 1.0)));
  vec3 N = normalize(vec3(sp, max(z, 0.001)));
  vec3 V = vec3(0.0, 0.0, 1.0);
  float ndotv = clamp(N.z, 0.0, 1.0);
  float limb = 1.0 - ndotv;

  // Volume shading: gentle limb darkening on the lower-back curve
  float backCurve = smoothstep(0.35, -0.75, dot(N, L));
  float shade = 1.0 - volumeDarken * (0.44 * limb * limb + 0.56 * backCurve * (0.30 + 0.70 * limb));
  col *= shade;

  // Soft tinted internal bounce on the opposite inner wall (attenuated in already-bright zones)
  float curLum = dot(col, vec3(0.299, 0.587, 0.114));
  float headroom = clamp(1.15 - curLum * 0.75, 0.2, 1.0);
  vec3 L_opp = normalize(vec3(-L.x * 0.85, -L.y * 0.85 - 0.12, 0.35));
  float bounceMask = smoothstep(0.20, 0.88, limb) * pow(max(dot(N, L_opp), 0.0), 1.9);
  vec3 bounceCol = mix(accentA, accentB, 0.5);
  col += bounceCol * bounceMask * (0.20 + 0.08 * hover) * headroom * (1.0 - 0.65 * disabledAmt);

  // Subtle dark TIR glass bevel just inside the silhouette for crisp separation on light backgrounds
  float tir = smoothstep(0.84, 0.985, sr) * (1.0 - smoothstep(0.985, 1.01, sr));
  col *= 1.0 - tir * (0.26 + 0.10 * (1.0 - rimStrength));

  // Fresnel rim light + subtle chromatic glass edge for separation on light & dark backgrounds
  float fresnel = pow(limb, 3.0);
  float rimBand = smoothstep(0.74, 0.96, sr);
  float edgeDir = clamp(dot(normalize(sp + vec2(1e-4)), L.xy) * 0.5 + 0.5, 0.0, 1.0);
  vec3 chromRim = mix(mix(accentB, vec3(1.0, 0.62, 0.88), 0.35), mix(accentA, vec3(0.55, 0.92, 1.0), 0.45), edgeDir);
  vec3 glassRim = mix(chromRim, vec3(1.0), 0.38);
  col += glassRim * fresnel * (0.30 + 0.22 * rimBand) * rimStrength * headroom * (0.85 + 0.28 * hover) * (1.0 - 0.55 * disabledAmt);

  // 3D cursor-tracking specular highlights (crisp glossy front dome + softer secondary inner bounce)
  vec3 H = normalize(L + V);
  float specTight = pow(max(dot(N, H), 0.0), 96.0);
  float specBroad = pow(max(dot(N, H), 0.0), 18.0);
  vec3 H_sec = normalize(normalize(vec3(-L.x * 0.82, -L.y * 0.82 - 0.16, 0.45)) + V);
  float specSec = pow(max(dot(N, H_sec), 0.0), 16.0) * smoothstep(0.18, 0.84, limb);

  float activeSpec = specStrength * (1.0 - 0.65 * disabledAmt);
  vec3 primarySpecCol = mix(vec3(1.0), mix(accentA, vec3(1.0), 0.72), 0.18);
  col += primarySpecCol * (specTight * (0.42 + 0.30 * hover) + specBroad * (0.03 + 0.04 * hover) * headroom) * activeSpec;
  col += mix(bounceCol, vec3(1.0), 0.45) * specSec * (0.12 + 0.09 * hover) * headroom * activeSpec;

  // Gentle highlight rolloff so bright iridescence stays dimensional instead of clipping to flat white
  col = col / (1.0 + max(col - 0.72, 0.0) * 0.42);
  return col;
}

vec3 sampleLiquidGlassBg(
    sampler2D bgTex,
    vec2 uv,
    vec2 sp,
    float sr,
    float z,
    vec2 rippleOffset,
    float overscan
) {
  float invScale = 0.5 / max(overscan, 1.0);
  vec2 baseTex = vec2(0.5 + uv.x * invScale, 0.5 - uv.y * invScale);

  // Spherical lens bending: gentle magnification at center, strong curvature toward rim
  vec2 refractUv = -sp * (0.14 + 0.34 * pow(max(1.0 - z, 0.0), 1.25)) + rippleOffset;
  vec2 dir = normalize(sp + vec2(1e-4));
  float disp = (0.008 + 0.042 * pow(max(1.0 - z, 0.0), 1.45)) * smoothstep(0.08, 0.95, sr);

  vec2 uvR = baseTex + vec2(refractUv.x - dir.x * disp, -(refractUv.y - dir.y * disp)) * invScale;
  vec2 uvG = baseTex + vec2(refractUv.x, -refractUv.y) * invScale;
  vec2 uvB = baseTex + vec2(refractUv.x + dir.x * disp, -(refractUv.y + dir.y * disp)) * invScale;

  float r = texture2D(bgTex, clamp(uvR, 0.001, 0.999)).r;
  float g = texture2D(bgTex, clamp(uvG, 0.001, 0.999)).g;
  float b = texture2D(bgTex, clamp(uvB, 0.001, 0.999)).b;
  return vec3(r, g, b);
}
"""

_SIRI_SHEET_FRAG = _GLSL_HEADER + _SPHERE_GLASS_GLSL + """
uniform float uSize;
uniform float uPhase;
uniform float uLevel;
uniform float uWarp;
uniform float uRidge;
uniform float uSharp;
uniform float uZoom;
uniform float uExposure;
uniform float uGlow;
uniform float uRim;
uniform float uHear;
uniform float uVoice;
uniform float uFade;
uniform float uHover;
uniform vec2 uPointer;
uniform sampler2D uBgTex;
uniform float uHasBg;
uniform float uBgOverscan;
uniform vec3 uColA;
uniform vec3 uColB;
uniform vec3 uColC;
uniform vec3 uColD;
uniform vec3 uHi;
uniform vec3 uCool;
uniform vec3 uWarm;
uniform vec3 uGlowCol;

const float RAD = 0.86;
const float SOFT = 0.005;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

float fbm(vec2 p) {
  float s = 0.0;
  float a = 0.5;
  for (int i = 0; i < 4; i++) {
    s += a * noise(p);
    p = mat2(0.8, 0.6, -0.6, 0.8) * p * 2.03;
    a *= 0.5;
  }
  return s / 0.9375;
}

vec2 band(vec2 q, float drift, float offset, float amp, float mainY, float env, float soft) {
  float y = amp * env * sin(q.x * 1.0 + drift + offset);
  float d = abs(q.y - y);
  float line = 0.018 / (sqrt(d * d + soft * soft) + 0.026);
  float bd = max(0.0, max(q.y - max(mainY, y), min(mainY, y) - q.y));
  float fillB = 0.014 / (bd * bd * 5.0 + 0.06);
  return vec2(line, fillB);
}

vec3 sheet(vec2 p, float t) {
  float scale = 0.74 + uZoom * 0.34;
  vec2 q = p / scale;
  vec2 w = vec2(fbm(q * 1.1 + vec2(0.0, t * 0.09)), fbm(q * 1.1 + vec2(7.7, -t * 0.07))) - 0.5;
  q += uWarp * 0.075 * w;
  float xn = q.x;
  float envB = cos(1.57079633 * min(abs(0.9 * xn), 1.0));
  float env = envB * envB;
  float low = 0.5 + 0.5 * cos(t * 0.37);
  float mid = 0.5 + 0.5 * sin(t * 0.51 + 1.2);
  float high = 0.5 + 0.5 * cos(t * 0.73 + 2.1);
  float drift = t * 2.4;
  float mainAmp = 0.1 + uRidge * 0.17 + low * 0.018;
  float bandAmp = mainAmp + mid * 0.025 + high * 0.018;
  float mainY = mainAmp * env * sin(q.x * 1.1 + drift);
  float sep = 1.85 + uWarp * 0.12 + mid * 0.28;
  float soft = 0.03 / max(uSharp, 0.2) + mid * 0.006;

  vec2 b0 = band(q, drift, -sep, bandAmp, mainY, env, soft);
  vec2 b1 = band(q, drift, -sep * 0.34, bandAmp, mainY, env, soft);
  vec2 b2 = band(q, drift, sep * 0.34, bandAmp, mainY, env, soft);
  vec2 b3 = band(q, drift, sep, bandAmp, mainY, env, soft);
  float w0 = b0.x + b0.y;
  float w1 = b1.x + b1.y;
  float w2 = b2.x + b2.y;
  float w3 = b3.x + b3.y;
  float total = w0 + w1 + w2 + w3;
  float d0 = w0 * w0;
  float d1 = w1 * w1;
  float d2 = w2 * w2;
  float d3 = w3 * w3;
  vec3 spectral = (uColA * d0 + uColC * d1 + uColB * d2 + uColD * d3) / max(d0 + d1 + d2 + d3, 0.0001);
  float energy = (1.0 - exp(-max(total - 0.12, 0.0) * 0.75)) * env;
  float md = abs(q.y - mainY);
  float core = exp(-md * md / (0.0028 / max(uSharp, 0.2))) * env;

  float haze = fbm(q * 1.6 + vec2(t * 0.05, -t * 0.03));
  vec3 atmos = mix(uColD, uColB, smoothstep(-0.7, 0.7, q.y)) * (0.012 + 0.022 * haze);
  atmos += mix(uColA, uColC, haze) * 0.035 * exp(-q.y * q.y * 7.0) * haze;

  vec3 col = atmos + spectral * energy * 1.14;
  col += uHi * core * (0.2 + 0.1 * low);
  col = col / (1.0 + col * 0.18);
  col = mix(col, uHi, 0.06 * smoothstep(0.15, 1.15, dot(p, vec2(-0.32, 0.78))));
  col *= 1.0 - 0.3 * smoothstep(-0.1, 1.2, dot(p, vec2(0.45, -0.62)));
  return col;
}

float profile(float t) {
  float d = clamp(t, 0.0, 1.0);
  return 1.0 - sqrt(max(1.0 - (1.0 - d) * (1.0 - d), 0.0));
}

float lobe(vec2 n, vec2 dir, float cut, float power) {
  return pow(max(clamp((dot(n, dir) - cut) / max(1.0 - cut, 0.001), 0.0, 1.0), 0.0001), power);
}

vec3 over(vec3 dst, vec3 src, float a) {
  float k = clamp(a, 0.0, 1.0);
  return src * k + dst * (1.0 - k);
}

void main() {
  vec2 uv = (gl_FragCoord.xy * 2.0 - uSize) / uSize;
  float r = length(uv);
  float ang = atan(uv.y, uv.x);
  float t = uPhase;

  float hearReact = uHear * uLevel;
  float react = max(hearReact, uVoice);
  float contour = hearReact * (0.011 * sin(ang * 3.0 + t * 1.9) + 0.006 * sin(ang * 5.0 - t * 1.3 + 1.7))
    + uVoice * (0.014 * sin(ang * 3.0 + t * 2.4) + 0.008 * sin(ang * 5.0 - t * 1.8 + 1.7));
  float rad = RAD * (1.0 + contour);
  vec3 glowCol = uGlowCol * (0.6 + 0.4 * react);
  float glowAmt = uGlow * (1.0 + 0.9 * react);

  if (r > rad * (1.01 + SOFT)) {
    vec3 halo = glowCol * glowAmt * exp(-(r - rad) * 11.0) * (1.0 - smoothstep(rad, 0.995, r));
    halo = clamp(halo, 0.0, 1.0);
    float ha = max(halo.r, max(halo.g, halo.b));
    gl_FragColor = vec4(clamp(halo, 0.0, ha), ha) * uFade;
    return;
  }

  vec2 p = uv / rad;
  float pd = length(p);
  vec2 n = pd > 0.0001 ? p / pd : vec2(0.0);
  float edge = max(1.0 - pd, 0.0);
  float prof = pow(max(profile(edge / 0.3), 0.0001), 0.68);
  float z = sqrt(max(0.0, 1.0 - min(dot(p, p), 1.0)));
  vec2 ptr = vec2(uPointer.x, -uPointer.y);
  vec2 pLensed = p * (0.89 + 0.14 * (1.0 - z)) - ptr * (0.04 * z * uHover);
  vec2 rp = pLensed - n * prof * 0.48;

  vec3 fcol;
  if (prof > 0.002) {
    float split = 0.12 * prof;
    fcol = vec3(sheet(rp - n * split, t).r, sheet(rp, t).g, sheet(rp + n * split, t).b);
  } else {
    fcol = sheet(pLensed, t);
  }

  float lum = dot(fcol, vec3(0.213, 0.715, 0.072));
  vec3 col = clamp(vec3(lum) + (fcol - vec3(lum)) * 1.18, 0.0, 1.0);

  float rimReact = uRim * (1.0 + 0.45 * react);
  float surfW = 0.035 + 0.03 * react;
  float surf = 1.0 - smoothstep(0.0, surfW, edge);
  float optical = pow(max(surf, 0.0001), 1.8);
  col = over(col, mix(uColA, uHi, 0.5) * 0.35, optical * 0.1 * rimReact);

  float coolS = lobe(n, normalize(vec2(0.84, 0.54)), 0.05, 1.6);
  float warmS = lobe(n, normalize(vec2(-0.62, -0.78)), 0.05, 1.8);
  float disp = optical * 0.5 * rimReact;
  col = over(col, uCool, disp * coolS);
  col = over(col, uWarm, disp * warmS);

  col *= 1.0 - optical * 0.12 * (0.15 + 0.85 * max(dot(n, vec2(0.45, -0.89)), 0.0));

  float key = optical * lobe(n, normalize(vec2(-0.68, 0.73)), 0.2, 2.8) * 0.6 * rimReact;
  float fillL = optical * lobe(n, normalize(vec2(0.74, -0.67)), 0.4, 3.6) * 0.4 * rimReact;
  col = over(col, mix(uHi, vec3(1.0), 0.35), key);
  col = over(col, mix(uColC, uHi, 0.55), fillL);

  col *= 1.0 - 0.4 * smoothstep(0.95, 1.0, pd);
  col += mix(uColA, uColB, 0.3 + 0.3 * sin(ang + t * 0.6)) * optical * 0.3 * react;

  float ballA = 1.0 - smoothstep(0.99 - SOFT, 1.01 + SOFT, pd);
  col = clamp(col * max(uExposure, 0.0), 0.0, 1.0);
  float disabledAmt = clamp((1.0 - uFade) / 0.38, 0.0, 1.0);
  if (uHasBg > 0.5) {
    vec3 bg = sampleLiquidGlassBg(uBgTex, uv, p, pd, z, (pLensed - p) * 0.45, uBgOverscan);
    vec3 glassTint = mix(vec3(0.92, 0.94, 0.98), mix(uColA, uColB, 0.5) * 0.72 + 0.32, 0.16);
    vec3 crystalBg = bg * glassTint * (0.96 - 0.14 * (1.0 - z));
    float waveVis = clamp(max(col.r, max(col.g, col.b)) * 1.35, 0.0, 1.0);
    float bgLum = dot(bg, vec3(0.299, 0.587, 0.114));
    // Subtle dark backing only directly under the bright ribbon on light desktops so the wave stays legible
    float waveBack = waveVis * (0.22 + 0.28 * bgLum);
    col = crystalBg * (1.0 - waveBack) + col * (0.95 + 0.18 * (1.0 - bgLum));
  }
  vec3 lightDir = sphereLightDir(uPointer, uHover);
  col = applySphereGlass(
      col, p, pd, lightDir, uColA, uColB, uHover,
      0.78 * max(uRim, 0.25), uHasBg > 0.5 ? 0.12 : 0.18, 0.95, disabledAmt
  );
  col = clamp(col, 0.0, 1.0) * ballA;
  float outside = smoothstep(rad - SOFT, rad + SOFT, r);
  col += glowCol * glowAmt * exp(-max(r - rad, 0.0) * 11.0) * (1.0 - smoothstep(rad, 0.995, r)) * outside;
  col = clamp(col, 0.0, 1.0);
  float a = clamp(max(ballA, max(col.r, max(col.g, col.b))), 0.0, 1.0);
  col = clamp(col, 0.0, a);
  gl_FragColor = vec4(col, a) * uFade;
}
"""

_AURA_FIELD_FRAG = _GLSL_HEADER + _SPHERE_GLASS_GLSL + """
uniform float uRes;
uniform float uPhase;
uniform float uSweep;
uniform float uRadius;
uniform float uThick;
uniform float uWarp;
uniform float uBright;
uniform float uShimmer;
uniform float uDesat;
uniform float uHover;
uniform vec2 uPointer;
uniform sampler2D uBgTex;
uniform float uHasBg;
uniform float uBgOverscan;
uniform vec3 uFrom;
uniform vec3 uTo;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

void main() {
  vec2 p = (gl_FragCoord.xy * 2.0 - uRes) / uRes;
  float r = length(p);
  float edge = 1.0 - smoothstep(0.8, 1.0, r);
  if (edge <= 0.0) {
    gl_FragColor = vec4(0.0);
    return;
  }
  float a = atan(p.y, p.x);
  vec2 ptr = vec2(uPointer.x, -uPointer.y);

  vec3 acc = vec3(0.0);
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    vec2 lp = p - ptr * ((fi - 1.0) * 0.032 * uHover);
    float lr = length(lp);
    float la = atan(lp.y, lp.x);
    float ph = uPhase * (1.0 + 0.35 * fi) + fi * 2.1;
    float amp1 = 0.6 + 0.8 * noise(vec2(ph * 0.35, fi * 7.3));
    float amp2 = 0.4 + 0.8 * noise(vec2(fi * 3.9, ph * 0.28 + 4.0));
    float n = 0.5 * amp1 * sin(3.0 * la + ph * 1.3)
      + 0.32 * amp2 * sin(5.0 * la - ph * 1.7 + fi * 2.0)
      + 0.22 * sin(2.0 * la + ph * 0.8 + fi);
    n *= 0.5;
    float rr = uRadius * (1.0 + n * uWarp * 2.2) + (fi - 1.0) * uThick * 0.9;
    float d = abs(lr - rr);
    float w = uThick * (0.8 + 0.6 * abs(n));
    float band = exp(-(d * d) / max(w * w, 1e-5));
    float bloom = exp(-d / max(w * 2.6, 1e-4));
    float hue = 0.5 + 0.5 * sin(la + uPhase * 0.3 + fi * 1.4 + n * 3.0);
    vec3 c = mix(uFrom, uTo, smoothstep(0.1, 0.9, hue));
    float layer = i == 1 ? 1.0 : 0.55;
    acc += c * (band * 1.0 + bloom * 0.28) * layer;
    acc += mix(c, vec3(1.0), 0.45) * pow(max(band, 0.0001), 6.0) * 0.35 * layer;
  }

  float hollow = smoothstep(uRadius * 0.25, uRadius * 0.95, r);
  acc *= mix(0.1, 1.0, hollow);

  float sweep = pow(max(0.5 + 0.5 * cos(a - uSweep), 0.0001), 6.0);
  float sweep2 = pow(max(0.5 + 0.5 * cos(a - uSweep * 0.7 + 2.6), 0.0001), 10.0);
  acc *= 1.0 + uShimmer * (sweep * 1.5 + sweep2 * 0.8);

  // Subtle luminous depth & cursor-tracking rim polish along the ring filament
  vec2 light2d = normalize(mix(vec2(-0.58, 0.81), ptr + vec2(1e-4, 0.0), 0.85 * uHover));
  float dirGlow = pow(max(0.5 + 0.5 * dot(normalize(p + vec2(1e-4)), light2d), 0.0001), 4.0);
  float ringDensity = smoothstep(0.12, 0.92, max(max(acc.r, acc.g), acc.b));
  acc += mix(uTo, vec3(1.0), 0.65) * dirGlow * ringDensity * (0.28 + 0.45 * uHover) * (1.0 - uDesat);

  acc *= uBright;

  float lum = dot(acc, vec3(0.299, 0.587, 0.114));
  acc = mix(acc, vec3(lum), uDesat);

  float peak = max(max(acc.r, acc.g), acc.b);
  vec3 hueKeep = acc * ((1.0 - exp(-peak * 1.3)) / max(peak, 1e-4));
  vec3 col = mix(hueKeep, 1.0 - exp(-acc * 1.3), 0.45);
  col *= edge;
  if (uHasBg > 0.5) {
    float ringVis = clamp(max(max(col.r, col.g), col.b) * 1.1, 0.0, 1.0);
    float ringZ = sqrt(max(0.0, 1.0 - min(r * r, 1.0)));
    vec3 bg = sampleLiquidGlassBg(uBgTex, p, p, r, ringZ, ptr * 0.03, uBgOverscan);
    col = mix(col, max(col, bg * 0.55 + col * 0.72), 0.36 * ringVis * (1.0 - uDesat));
  }
  float alpha = clamp(max(max(col.r, col.g), col.b) * 1.35, 0.0, 1.0);
  col *= mix(1.0, 0.78, uDesat);
  col = clamp(col, 0.0, alpha);
  gl_FragColor = vec4(col, alpha);
}
"""

_IRIDESCENT_FLOW_FRAG = _GLSL_HEADER + _SPHERE_GLASS_GLSL + """
uniform float uSize;
uniform float uTime;
uniform float uFlow;
uniform float uLevel;
uniform vec3 uColorFrom;
uniform vec3 uColorTo;
uniform float uConnect;
uniform float uListen;
uniform float uThink;
uniform float uSpeak;
uniform float uError;
uniform float uDisabled;
uniform float uHover;
uniform vec2 uPointer;
uniform sampler2D uBgTex;
uniform float uHasBg;
uniform float uBgOverscan;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float amp = 0.55;
  for (int i = 0; i < 3; i++) {
    v += amp * noise(p);
    p = p * 2.03 + vec2(11.7, 5.3);
    amp *= 0.5;
  }
  return v;
}

void main() {
  vec2 p = (gl_FragCoord.xy * 2.0 - uSize) / uSize;
  float r = length(p);
  float mask = 1.0 - smoothstep(0.86, 0.92, r);
  if (mask < 0.003) {
    gl_FragColor = vec4(0.0);
    return;
  }

  vec2 sp = p / 0.89;
  float sr = length(sp);
  float z = sqrt(max(0.0, 1.0 - min(dot(sp, sp), 1.0)));
  vec2 ptr = vec2(uPointer.x, -uPointer.y);
  vec2 flowP = (sp * (0.76 + 0.36 * (1.0 - z)) - ptr * (0.055 * z * uHover)) * 0.89;
  float flowR = length(flowP);

  vec2 drift = vec2(uFlow * 0.32, -uFlow * 0.21);
  vec2 q = vec2(fbm(flowP * 2.1 + drift), fbm(flowP * 2.1 + drift.yx + vec2(4.7, 1.9)));
  float n = fbm(flowP * 2.6 + 2.2 * q + vec2(uFlow * 0.42, uFlow * 0.27));

  float inter = sin(flowR * 24.0 - uTime * 3.1) * sin(flowR * 15.0 + uTime * 2.3);
  n += uThink * inter * (0.14 + 0.12 * uLevel);

  float ripple = sin(flowR * 30.0 + uTime * 6.5);
  n += uListen * ripple * (0.05 + 0.2 * uLevel);

  float emit = sin(flowR * 16.0 - uTime * 4.6);
  n += uSpeak * emit * (0.04 + 0.16 * uLevel);

  n = clamp(n, 0.0, 1.0);

  float fres = pow(max(smoothstep(0.3, 0.9, r), 0.0001), 2.0);

  float th = n * 1.7 + fres * 1.15 + uFlow * 0.1 + 0.14 * uSpeak - 0.1 * uListen;
  vec3 film = 0.5 + 0.5 * cos(6.28318 * (th + vec3(0.0, 0.33, 0.67)));

  float tone = clamp(smoothstep(0.15, 0.85, n) + 0.28 * uListen - 0.3 * uSpeak, 0.0, 1.0);
  vec3 base = mix(uColorFrom, uColorTo, tone);
  float irid = clamp(0.4 + 0.35 * uSpeak + 0.18 * uListen + 0.3 * uLevel, 0.0, 1.0);
  irid *= 1.0 - 0.75 * uError;
  irid *= 1.0 - 0.7 * uDisabled;

  vec3 col = base * (0.62 + 0.85 * n);
  col += mix(uColorFrom, uColorTo, 0.5) * (1.0 - smoothstep(0.05, 0.75, flowR)) * 0.3;
  col += film * irid * (0.35 + 0.65 * fres);

  float ang = atan(p.y, p.x);
  float sweep = pow(max(0.5 + 0.5 * cos(ang - uTime * 2.4), 0.0001), 10.0);
  col += mix(uColorTo, vec3(1.0), 0.55) * sweep * smoothstep(0.35, 0.85, r) * uConnect * 1.1;

  col *= 1.0 + uListen * 0.22 * max(ripple, 0.0) * (0.4 + 0.6 * uLevel);
  col += mix(uColorFrom, vec3(1.0), 0.35) * uSpeak * max(emit, 0.0) * (1.0 - smoothstep(0.1, 0.8, flowR)) * (0.08 + 0.3 * uLevel);
  col *= 1.0 + uThink * (0.12 * sin(uTime * 4.2) + 0.1 * inter);

  vec3 rim = mix(uColorTo, vec3(1.0), 0.45);
  col += rim * pow(max(smoothstep(0.55, 0.9, r), 0.0001), 3.0) * (0.4 + 0.55 * uLevel);
  col += vec3(0.98, 0.28, 0.35) * fres * uError * 0.35;

  col *= 0.9 + 0.45 * uLevel + 0.25 * uSpeak * uLevel;

  float lum = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(col, vec3(lum), uDisabled * 0.85);
  col *= 1.0 - 0.35 * uDisabled;

  col = col / (1.0 + 0.28 * col);
  if (uHasBg > 0.5) {
    vec3 bg = sampleLiquidGlassBg(uBgTex, p, sp, sr, z, (flowP - p) * 0.35, uBgOverscan);
    float glassMix = mix(0.24, 0.46, pow(max(1.0 - z, 0.0), 0.85)) * (1.0 - 0.65 * uDisabled);
    vec3 bgTinted = bg * mix(vec3(1.0), base + 0.35, 0.32);
    col = mix(col, bgTinted * 0.78 + col * 0.42, glassMix);
  }
  vec3 lightDir = sphereLightDir(uPointer, uHover);
  col = applySphereGlass(
      col, sp, sr, lightDir, uColorFrom, uColorTo, uHover,
      0.42, 0.38, 0.88, uDisabled
  );
  col = clamp(col, 0.0, 1.0);
  float a = mask * (1.0 - 0.5 * uDisabled);
  col = clamp(col * a, 0.0, a);
  gl_FragColor = vec4(col, a);
}
"""

_DUOTONE_FLOW_FRAG = _GLSL_HEADER + _SPHERE_GLASS_GLSL + """
uniform float uSize;
uniform float uFlow;
uniform float uSpin;
uniform float uIn;
uniform float uOut;
uniform float uSat;
uniform float uBright;
uniform float uHover;
uniform vec2 uPointer;
uniform sampler2D uBgTex;
uniform float uHasBg;
uniform float uBgOverscan;
uniform vec3 uFrom;
uniform vec3 uTo;

vec2 rotate(vec2 p, float a) {
  float c = cos(a);
  float s = sin(a);
  return vec2(c * p.x - s * p.y, s * p.x + c * p.y);
}

vec3 toLinear(vec3 c) {
  return pow(max(c, vec3(0.0001)), vec3(2.2));
}

vec3 toSrgb(vec3 c) {
  return pow(max(c, vec3(0.0001)), vec3(1.0 / 2.2));
}

void main() {
  vec2 p = (gl_FragCoord.xy * 2.0 - uSize) / uSize;
  float r = length(p);
  float aa = 3.0 / max(uSize, 1.0);
  float edge = 0.96;
  float mask = 1.0 - smoothstep(edge - aa, edge, r);
  if (mask <= 0.0) {
    gl_FragColor = vec4(0.0);
    return;
  }

  vec2 sp = p / edge;
  float sr = length(sp);
  float z = sqrt(max(0.0, 1.0 - min(dot(sp, sp), 1.0)));
  vec2 ptr = vec2(uPointer.x, -uPointer.y);
  vec2 lp = sp * (0.77 + 0.35 * (1.0 - z)) - ptr * (0.055 * z * uHover);
  vec2 flowP = lp * edge;
  float lr = mix(r, length(flowP), 0.55);

  vec2 q = rotate(flowP, uSpin);
  float amount = 0.08 + 0.17 * uOut;
  vec2 warp = vec2(
    sin(q.y * 3.1 + uFlow * 1.7) + 0.5 * sin(q.x * 5.3 - uFlow * 2.3),
    cos(q.x * 2.7 - uFlow * 1.3) + 0.5 * cos(q.y * 4.9 + uFlow * 1.9)
  );
  q += amount * warp;

  float num = 0.0;
  float den = 0.0;
  for (int i = 0; i < 7; i++) {
    float fi = float(i);
    float a = fi * 0.8976 + uFlow * (0.32 + 0.06 * fi);
    float b = fi * 1.7 + uFlow * (0.27 + 0.05 * (6.0 - fi));
    float rad = 0.22 + 0.42 * fract(fi * 0.618 + 0.13);
    vec2 c = vec2(cos(a), sin(b)) * rad;
    vec2 d = q - c;
    float w = exp(-dot(d, d) * 6.0);
    num += w * mod(fi, 2.0);
    den += w;
  }
  float t = num / max(den, 1e-5);
  float tone = smoothstep(0.14, 0.86, t);

  vec3 from = toLinear(uFrom);
  vec3 to = toLinear(uTo);
  vec3 col = mix(from, to, tone);

  float seam = 1.0 - abs(2.0 * t - 1.0);
  col += mix(from, to, 0.5) * seam * 0.14;
  col *= 0.82 + 0.3 * (1.0 - r * r);

  float ang = atan(q.y, q.x);
  float ringAlpha = 0.2 + 0.4 * uIn;
  vec3 ringCol = mix(col, vec3(1.0), 0.75);
  for (int k = 0; k < 3; k++) {
    float fk = float(k);
    float radius = 0.5 + 0.12 * fk + 0.2 * uIn * (0.55 + 0.225 * fk);
    radius = min(radius, 0.915);
    float wobble = 0.01 * sin(ang * 3.0 + uFlow * (0.6 + 0.3 * fk) + fk * 2.1) * (0.5 + uIn);
    float width = 0.009 + 0.008 * uIn;
    float dr = (lr + wobble - radius) / width;
    float line = exp(-dr * dr);
    float fade = 0.55 + 0.225 * fk;
    col = mix(col, ringCol, clamp(line * ringAlpha * fade, 0.0, 1.0));
  }

  float rim = smoothstep(edge - 0.07, edge, r);
  col = mix(col, col * 0.7, rim * 0.6);
  col += mix(from, to, 0.5) * smoothstep(edge - 0.035, edge - 0.005, r) * (1.0 - smoothstep(edge - 0.005, edge, r)) * 0.35;

  float lum = dot(col, vec3(0.2126, 0.7152, 0.0722));
  col = mix(vec3(lum), col, uSat) * uBright;

  vec3 outCol = clamp(toSrgb(col), 0.0, 1.0);
  float disabledAmt = clamp((1.0 - uSat) / 0.88, 0.0, 1.0);
  if (uHasBg > 0.5) {
    vec3 bg = sampleLiquidGlassBg(uBgTex, p, sp, sr, z, (flowP - p) * 0.30, uBgOverscan);
    float glassMix = mix(0.22, 0.44, pow(max(1.0 - z, 0.0), 0.90)) * (1.0 - 0.65 * disabledAmt);
    vec3 bgTinted = bg * mix(vec3(0.95), outCol * 1.15, 0.42);
    outCol = mix(outCol, bgTinted + outCol * 0.28, glassMix);
  }
  vec3 lightDir = sphereLightDir(uPointer, uHover);
  outCol = applySphereGlass(
      outCol, sp, sr, lightDir, uFrom, uTo, uHover,
      0.78, 0.36, 0.92, disabledAmt
  );
  outCol = clamp(outCol, 0.0, 1.0);
  outCol = clamp(outCol * mask, 0.0, mask);
  gl_FragColor = vec4(outCol, mask);
}
"""

_SHADER_SOURCES: dict[str, str] = {
    "siri-sheet": _SIRI_SHEET_FRAG,
    "aura-field": _AURA_FIELD_FRAG,
    "iridescent-flow": _IRIDESCENT_FLOW_FRAG,
    "duotone-flow": _DUOTONE_FLOW_FRAG,
}


class OrbGlRenderer:
    """Shared offscreen OpenGL context rendering the 4 verbatim VoiceOrb shaders."""

    _instance: OrbGlRenderer | None = None

    def __init__(self) -> None:
        self._ctx: QOpenGLContext | None = None
        self._surface: QOffscreenSurface | None = None
        self._gl: Any = None
        self._vbo: QOpenGLBuffer | None = None
        self._programs: dict[str, QOpenGLShaderProgram] = {}
        self._custom_sources: dict[str, str] = {}
        self._compile_errors: dict[str, str] = {}
        self._fbos: dict[int, QOpenGLFramebufferObject] = {}
        self._bg_tex_id: int | None = None
        self._bg_tex_size: tuple[int, int] = (0, 0)
        self._dummy_tex_id: int | None = None
        self._init_failed = False

    @classmethod
    def instance(cls) -> OrbGlRenderer:
        """Return the shared OrbGlRenderer singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_available(self) -> bool:
        """Return True if an offscreen OpenGL context can be created."""
        return self._ensure_gl()

    def _ensure_gl(self) -> bool:
        if self._init_failed:
            return False
        if self._ctx is not None and self._surface is not None and self._gl is not None:
            return True
        try:
            fmt = QSurfaceFormat()
            fmt.setAlphaBufferSize(8)
            fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)

            ctx = QOpenGLContext()
            ctx.setFormat(fmt)
            if not ctx.create():
                self._init_failed = True
                return False

            surf = QOffscreenSurface()
            surf.setFormat(ctx.format())
            surf.create()
            if not surf.isValid() or not ctx.makeCurrent(surf):
                self._init_failed = True
                return False

            prof = QOpenGLVersionProfile()
            prof.setVersion(2, 0)
            gl = QOpenGLVersionFunctionsFactory.get(prof, ctx)
            if gl is None:
                self._init_failed = True
                return False

            verts = (ctypes.c_float * 6)(-1.0, -1.0, 3.0, -1.0, -1.0, 3.0)
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            if not vbo.create() or not vbo.bind():
                self._init_failed = True
                return False
            vbo.allocate(verts, ctypes.sizeof(verts))

            self._ctx = ctx
            self._surface = surf
            self._gl = gl
            self._vbo = vbo
            return True
        except Exception:
            self._init_failed = True
            return False

    def register_shader(self, orb_id: str, frag_src: str) -> None:
        """Register or replace a custom GLSL fragment shader source for `orb_id`."""
        if self._custom_sources.get(orb_id) == frag_src and orb_id in self._programs:
            return
        self._custom_sources[orb_id] = frag_src
        self._programs.pop(orb_id, None)
        self._compile_errors.pop(orb_id, None)

    def unregister_shader(self, orb_id: str) -> None:
        """Remove a dynamically registered shader program and cached compile state."""
        self._custom_sources.pop(orb_id, None)
        self._programs.pop(orb_id, None)
        self._compile_errors.pop(orb_id, None)

    def compile_shader(self, orb_id: str, frag_src: str | None = None) -> str:
        """Compile and link `orb_id`, returning an empty string on success or the GLSL error log."""
        if frag_src is not None:
            self.register_shader(orb_id, frag_src)
        if not self._ensure_gl():
            return "OpenGL context unavailable"
        prog = self._get_program(orb_id)
        return "" if prog is not None else self._compile_errors.get(orb_id, "Unknown shader error")

    def _get_program(self, orb_id: str) -> QOpenGLShaderProgram | None:
        if orb_id in self._programs:
            return self._programs[orb_id]
        frag_src = self._custom_sources.get(orb_id) or _SHADER_SOURCES.get(orb_id)
        if frag_src is None or not self._ensure_gl():
            return None
        if not self._ctx.makeCurrent(self._surface):  # type: ignore[union-attr]
            return None

        prog = QOpenGLShaderProgram()
        ok_v = prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERT_SRC)
        ok_f = prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, frag_src)
        ok_l = ok_v and ok_f and prog.link()
        if not ok_l:
            self._compile_errors[orb_id] = prog.log() or "GLSL compile/link failed"
            return None
        self._compile_errors[orb_id] = ""
        self._programs[orb_id] = prog
        return prog

    def compile_all(self) -> dict[str, str]:
        """Compile and link all 4 VoiceOrb GLSL shaders, returning per-orb error logs."""
        if not self._ensure_gl():
            return {oid: "OpenGL context unavailable" for oid in SHADER_ORB_IDS}
        results: dict[str, str] = {}
        for oid in SHADER_ORB_IDS:
            prog = self._get_program(oid)
            results[oid] = "" if prog is not None else self._compile_errors.get(oid, "Unknown error")
        return results

    def _bind_bg_texture(self, bg_image: QImage | None) -> bool:
        """Bind `bg_image` (or a 1x1 transparent fallback) to texture unit 0."""
        gl = self._gl
        gl.glActiveTexture(0x84C0)  # GL_TEXTURE0

        if bg_image is not None and not bg_image.isNull():
            rgba = (
                bg_image
                if bg_image.format() == QImage.Format.Format_RGBA8888
                else bg_image.convertToFormat(QImage.Format.Format_RGBA8888)
            )
            w, h = rgba.width(), rgba.height()
            if w > 0 and h > 0 and rgba.bytesPerLine() == w * 4:
                if self._bg_tex_id is None:
                    self._bg_tex_id = int(gl.glGenTextures(1))
                    gl.glBindTexture(0x0DE1, self._bg_tex_id)
                    gl.glTexParameteri(0x0DE1, 0x2801, 0x2601)  # GL_TEXTURE_MIN_FILTER = GL_LINEAR
                    gl.glTexParameteri(0x0DE1, 0x2800, 0x2601)  # GL_TEXTURE_MAG_FILTER = GL_LINEAR
                    gl.glTexParameteri(0x0DE1, 0x2802, 0x812F)  # GL_TEXTURE_WRAP_S = GL_CLAMP_TO_EDGE
                    gl.glTexParameteri(0x0DE1, 0x2803, 0x812F)  # GL_TEXTURE_WRAP_T = GL_CLAMP_TO_EDGE
                else:
                    gl.glBindTexture(0x0DE1, self._bg_tex_id)

                ptr = rgba.constBits()
                ptr.setsize(rgba.sizeInBytes())
                gl.glPixelStorei(0x0CF5, 4)  # GL_UNPACK_ALIGNMENT
                if self._bg_tex_size == (w, h):
                    gl.glTexSubImage2D(0x0DE1, 0, 0, 0, w, h, 0x1908, 0x1401, ptr)
                else:
                    gl.glTexImage2D(0x0DE1, 0, 0x1908, w, h, 0, 0x1908, 0x1401, ptr)
                    self._bg_tex_size = (w, h)
                return True

        if self._dummy_tex_id is None:
            self._dummy_tex_id = int(gl.glGenTextures(1))
            gl.glBindTexture(0x0DE1, self._dummy_tex_id)
            gl.glTexParameteri(0x0DE1, 0x2801, 0x2600)  # GL_NEAREST
            gl.glTexParameteri(0x0DE1, 0x2800, 0x2600)
            gl.glTexParameteri(0x0DE1, 0x2802, 0x812F)
            gl.glTexParameteri(0x0DE1, 0x2803, 0x812F)
            zero = (ctypes.c_ubyte * 4)(0, 0, 0, 0)
            gl.glTexImage2D(0x0DE1, 0, 0x1908, 1, 1, 0, 0x1908, 0x1401, zero)
        else:
            gl.glBindTexture(0x0DE1, self._dummy_tex_id)
        return False

    def render(
        self,
        orb_id: str,
        size: int,
        uniforms: dict[str, Any],
        bg_image: QImage | None = None,
    ) -> QImage | None:
        """Render one frame of `orb_id` at `size x size` pixels into a premultiplied QImage."""
        px = max(32, min(512, int(size)))
        if not self._ensure_gl():
            return None
        if not self._ctx.makeCurrent(self._surface):  # type: ignore[union-attr]
            return None

        prog = self._get_program(orb_id)
        if prog is None:
            return None

        fbo = self._fbos.get(px)
        if fbo is None:
            fbo = QOpenGLFramebufferObject(px, px)
            if not fbo.isValid():
                return None
            self._fbos[px] = fbo

        has_bg = self._bind_bg_texture(bg_image)

        fbo.bind()
        gl = self._gl
        gl.glViewport(0, 0, px, px)
        gl.glDisable(0x0BE2)  # GL_BLEND
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT

        prog.bind()
        self._vbo.bind()  # type: ignore[union-attr]
        loc = prog.attributeLocation("aPos")
        if loc >= 0:
            prog.enableAttributeArray(loc)
            prog.setAttributeBuffer(loc, 0x1406, 0, 2)

        for name, val in uniforms.items():
            if isinstance(val, (int, float)):
                prog.setUniformValue(name, float(val))
            elif isinstance(val, (tuple, list)):
                n = len(val)
                if n == 2:
                    prog.setUniformValue(name, QVector2D(float(val[0]), float(val[1])))
                elif n == 3:
                    prog.setUniformValue(name, QVector3D(float(val[0]), float(val[1]), float(val[2])))
                elif n == 4:
                    prog.setUniformValue(name, QVector4D(float(val[0]), float(val[1]), float(val[2]), float(val[3])))

        prog.setUniformValue("uBgTex", 0)
        if "uHasBg" not in uniforms:
            prog.setUniformValue("uHasBg", 1.0 if has_bg else 0.0)
        if "uBgOverscan" not in uniforms:
            prog.setUniformValue("uBgOverscan", 1.35)

        gl.glDrawArrays(0x0004, 0, 3)  # GL_TRIANGLES
        gl.glFinish()
        prog.release()
        fbo.release()
        return fbo.toImage(True)
