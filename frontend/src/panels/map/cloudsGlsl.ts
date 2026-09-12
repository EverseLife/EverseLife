// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the clouds over the ground (D-335, D-336): the picture's
 * numbers, the grain of a cloud, and three pieces of the fragment's
 * `main`, cut out of `fragment.ts` on 2026-09-12 when the file crossed the
 * bar with the clouds' own light.
 *
 * `CLOUDS_CONSTS_GLSL` declares the picture's numbers off `shade.ts`.
 * `CLOUDS_DETAIL_GLSL` is the fine grain of a cloud (picture, not law):
 * finer octaves of the weather's own noise, blended and carried as the
 * deck is.
 * `CLOUDS_SHELL_GLSL` draws the clouds on their own sphere, CLOUD_KM over
 * the ground (D-336 item 7): the pixel's ray meets that shell, not the
 * ground, so the clouds stand off the surface toward the limb and ring
 * the ball past it. It runs right after the eye's frame is known and
 * needs `up`, `east`, `north`, `X`, `Y`, `rho`, `edge`, `metres`, `shell`
 * and `far_sky`; it leaves `cloud_col` and `a_sky`, and past the ground's
 * disc it writes the pixel and returns.
 * `CLOUDS_FIELD_GLSL` reads the weather over the pixel's own ground -- the
 * weather layer's and the probe's point -- and over the point toward the
 * sun for the cloud's shadow: it needs `here`, `h`, `rain01`, `high`,
 * `sunward` and `metres`, and leaves `cover`, `cloud`, `rain_now` and
 * `cloud_over` for the ground's tone and the layers.
 * `CLOUDS_OVER_GLSL` lays the shell's clouds over the finished colour
 * `col`, after the night and the layers. They read `TWILIGHT` and
 * `NIGHT_TINT` off the fragment and the weather's uniforms.
 */

import {
  CLOUD_DETAIL,
  CLOUD_DETAIL_OCTAVES,
  CLOUD_DETAIL_SCALE,
  CLOUD_FAR_MPX,
  CLOUD_FEATHER,
  CLOUD_KM,
  CLOUD_MOTTLE,
  CLOUD_NEAR_MPX,
  CLOUD_NIGHT,
  CLOUD_OPACITY,
  CLOUD_SHADE,
  CLOUD_SUN_MIN,
  CLOUD_TONE,
  WX_HAZE,
} from "./shade";

export const CLOUDS_CONSTS_GLSL = `
//: The picture's numbers of the clouds (shade.ts), put into the GLSL once.
const vec3 CLOUD_TONE = vec3(${CLOUD_TONE.map((v) => v.toFixed(2)).join(", ")});
const float CLOUD_OPACITY = ${CLOUD_OPACITY.toFixed(2)};
const float CLOUD_SHADE = ${CLOUD_SHADE.toFixed(2)};
const float CLOUD_KM = ${CLOUD_KM.toFixed(2)};
const float CLOUD_NEAR_MPX = ${CLOUD_NEAR_MPX.toFixed(1)};
const float CLOUD_FAR_MPX = ${CLOUD_FAR_MPX.toFixed(1)};
const float CLOUD_SUN_MIN = ${CLOUD_SUN_MIN.toFixed(2)};
const float CLOUD_NIGHT = ${CLOUD_NIGHT.toFixed(2)};
const float WX_HAZE = ${WX_HAZE.toFixed(2)};
const float CLOUD_DETAIL_SCALE = ${CLOUD_DETAIL_SCALE.toFixed(1)};
const int CLOUD_DETAIL_OCTAVES = ${CLOUD_DETAIL_OCTAVES};
const float CLOUD_DETAIL = ${CLOUD_DETAIL.toFixed(2)};
const float CLOUD_FEATHER = ${CLOUD_FEATHER.toFixed(2)};
const float CLOUD_MOTTLE = ${CLOUD_MOTTLE.toFixed(2)};
`;

/** A turn of the lattice about an axis, as a GLSL mat3 literal (column
 *  major). The fine octaves' lattices are tilted off the planet's axis:
 *  a value noise is smooth across its cells but not alike along them, and
 *  a lattice standing on the pole showed its planes as rings along the
 *  parallels, spaced a cell apart, on the first render. */
function tilted(axis: readonly [number, number, number], angle: number): string {
  const n = Math.hypot(...axis);
  const [x, y, z] = axis.map((v) => v / n);
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const t = 1 - c;
  const m = [
    t * x * x + c, t * x * y + s * z, t * x * z - s * y,
    t * x * y - s * z, t * y * y + c, t * y * z + s * x,
    t * x * z + s * y, t * y * z - s * x, t * z * z + c,
  ];
  return `mat3(${m.map((v) => v.toFixed(6)).join(", ")})`;
}

export const CLOUDS_DETAIL_GLSL = `
//: The fine grain of a cloud (picture, D-336 item 9; owner, 2026-09-12:
//: clouds that are not one white, with edges that fray): CLOUD_DETAIL_OCTAVES
//: octaves of the weather's own noise, the first CLOUD_DETAIL_SCALE times
//: finer than the weather's cell and each next twice finer and half as
//: loud, each on a lattice turned further off the planet's axis, on their
//: own slices of time, blended and carried for the slices' ages as the
//: deck is. Nought to one about a half. Not the law: the probe and the
//: engine never read it.
const mat3 CLOUD_TILT_1 = ${tilted([1, 1, 1], 0.9)};
const mat3 CLOUD_TILT_2 = ${tilted([1, -2, 1], 1.7)};
//: One grain field at one turn: CLOUD_DETAIL_OCTAVES octaves of the
//: cubic value noise, each on its own tilted lattice and its own slices,
//: carried for the slices' ages.
float wxGrain(vec3 p, float turn) {
  int wi = int(floor(u_wx_slice));
  float age0 = u_wx_slice - float(wi);
  float age1 = age0 - 1.0;
  float wf = age0 * age0 * (3.0 - 2.0 * age0);
  vec3 a = wxTurned(p, -turn * age0, 1.0);
  vec3 b = wxTurned(p, -turn * age1, 1.0);
  float k = u_wx_scale * CLOUD_DETAIL_SCALE;
  float amp = 1.0;
  float sum = 0.0;
  float norm = 0.0;
  mat3 tilt = CLOUD_TILT_1;
  for (int o = 0; o < CLOUD_DETAIL_OCTAVES; o++) {
    int w0 = wi + 2000 + o * 1000;
    sum += amp * mix(wxNoise(tilt * a * k, w0), wxNoise(tilt * b * k, w0 + 1), wf);
    norm += amp;
    amp *= 0.5;
    k *= 2.0;
    tilt = CLOUD_TILT_2 * tilt;
  }
  return sum / norm;
}
//: The grain over a point of the shell, blended as the field itself is
//: (weatherGlsl.ts): the westward and the eastward grains crossfaded over
//: the belt's edge, the standing grain blended in by the hold -- never a
//: turn that varies with the place. On the first render the grain turned
//: by the latitude's own wind, and across the belt's edge (which runs
//: through the capital) its cells were sheared into arcs along the
//: parallels, a cell apart.
float wxDetail(vec3 p, float hold) {
  float west = wxWest(p.z);
  float moving;
  if (west <= 0.0) moving = wxGrain(p, -u_wx_spin);
  else if (west >= 1.0) moving = wxGrain(p, u_wx_spin);
  else moving = mix(wxGrain(p, -u_wx_spin), wxGrain(p, u_wx_spin), west);
  return hold > 0.0 ? mix(moving, wxGrain(p, 0.0), hold) : moving;
}
`;

export const CLOUDS_SHELL_GLSL = `
  //: The clouds on their own sphere (D-336 item 7; owner, 2026-09-12: off
  //: the ground's lattice, above the surface). The projection is
  //: orthographic, so the ray of a pixel rho from the middle meets the
  //: shell of radius s at the height sqrt(s^2 - rho^2); the weather is
  //: read for the ground under that point -- its rain share and its
  //: height, at the shell's own level of detail -- and the cloud is lit by
  //: its own normal: it sees the sun past the ground's terminator by the
  //: lift of its height, and no cast shadow of the ground reaches it. On
  //: its own night it keeps CLOUD_NIGHT of its tone under the night's
  //: tint. Past the ground's disc only the shell is drawn, over nothing.
  float cloud_sky = 0.0;
  float cloud_sun = 1.0;
  float mottle = 1.0;
  if (far_sky > 0.0) {
    float zc = sqrt(max(0.0, shell * shell - rho * rho));
    vec3 sky = normalize(up * zc + east * X + north * Y);
    float across_sky = max(length(dFdx(sky)), length(dFdy(sky))) * metres;
    float lod_sky = max(0.0, log2(max(across_sky / u_step, 1.0)));
    vec2 uv_sky = atlasAt(facePlace(sky), marginOf(lod_sky));
    float rain_sky = textureLod(u_rain, uv_sky, lod_sky).r;
    float hold_sky = wxHold(heightAt(uv_sky, lod_sky));
    float cover_sky = clamp(wxCover(sky, hold_sky) * (1.0 + u_wx_bias * (2.0 * rain_sky - 1.0)), 0.0, 1.0);
    //: The grain (item 9): it frays the edge -- added to the cover before
    //: the gate, which for the drawing starts CLOUD_FEATHER below the
    //: vault's, so the thin edge fades out rather than stops -- and it
    //: mottles the body, grey in its hollows. The gate the probe reads is
    //: the vault's, untouched.
    float fine = wxDetail(sky, hold_sky);
    cloud_sky = smoothstep(u_wx_gates.x - CLOUD_FEATHER, u_wx_gates.y, cover_sky + CLOUD_DETAIL * (fine - 0.5));
    mottle = mix(1.0 - CLOUD_MOTTLE, 1.0, smoothstep(0.25, 0.8, fine));
    float high_sky = dot(sky, u_sun);
    float lift = sqrt(1.0 - 1.0 / (shell * shell));
    cloud_sun = mix(1.0, smoothstep(-lift, -lift + TWILIGHT, high_sky), u_sunlit);
  }
  vec3 cloud_col = CLOUD_TONE * mottle * mix(NIGHT_TINT * CLOUD_NIGHT, vec3(1.0), cloud_sun);
  float a_sky = cloud_sky * far_sky * CLOUD_OPACITY * (1.0 - smoothstep(shell - edge, shell + edge, rho));
  if (rho > 1.0 + edge) {
    o_color = vec4(cloud_col * a_sky, a_sky);
    return;
  }
`;

export const CLOUDS_FIELD_GLSL = `
  //: The weather (D-335): the cover of the sky over this point, pulled by
  //: the ground's own rain share, gated to cloud and to rain -- the law
  //: of weatherGlsl.ts, the same the engine reads; the weather layer's
  //: and the probe's reading (the drawn clouds are the shell's above). A
  //: cloud's shadow falls away from the sun by the cloud's height over the
  //: sun's climb, so it is the cloud toward the sun by as much that shades
  //: this ground. Over high ground part of the deck stands still (D-336),
  //: and the ground's own rain share stretches the cover as a factor: a
  //: wet windward slope thickens what the wind brings, a dry lee thins it,
  //: and neither makes weather of a clear sky. The height is the frame's
  //: mip of it (the engine and the probe read it exact, and over the
  //: ranges the three copies part by that much).
  float hold = wxHold(h);
  float cover = clamp(wxCover(here, hold) * (1.0 + u_wx_bias * (2.0 * rain01 - 1.0)), 0.0, 1.0);
  float cloud = smoothstep(u_wx_gates.x, u_wx_gates.y, cover);
  float rain_now = smoothstep(u_wx_gates.z, u_wx_gates.w, cover);
  float cloud_alt = max(asin(clamp(high, 0.0, 1.0)), CLOUD_SUN_MIN);
  float cloud_turn = (CLOUD_KM * 1000.0 / tan(cloud_alt)) / metres;
  vec3 q_cloud = here * cos(cloud_turn) + sunward * sin(cloud_turn);
  //: The cloud toward the sun is read over this pixel's ground -- its
  //: rain share and its hold -- as the shadow is a tint, not a reading:
  //: one point, one law.
  float cloud_over = smoothstep(u_wx_gates.x, u_wx_gates.y, clamp(wxCover(q_cloud, hold) * (1.0 + u_wx_bias * (2.0 * rain01 - 1.0)), 0.0, 1.0));
`;

export const CLOUDS_OVER_GLSL = `
  //: The shell's clouds last, over the ground's night and the layers, and
  //: on the terrain alone (the client hands u_clouds by the layer, D-336).
  col = mix(col, cloud_col, a_sky);
`;
