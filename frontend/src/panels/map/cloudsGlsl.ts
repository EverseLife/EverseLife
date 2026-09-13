// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the clouds over the ground (D-335, D-336): the picture's
 * numbers, the grain of a cloud, and three pieces of the fragment's
 * `main`, cut out of `fragment.ts` on 2026-09-12 when the file crossed the
 * bar with the clouds' own light.
 *
 * `CLOUDS_CONSTS_GLSL` declares the picture's numbers off `shade.ts`.
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
const float CLOUD_DETAIL = ${CLOUD_DETAIL.toFixed(2)};
const float CLOUD_FEATHER = ${CLOUD_FEATHER.toFixed(2)};
const float CLOUD_MOTTLE = ${CLOUD_MOTTLE.toFixed(2)};
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
    float h_sky = heightAt(uv_sky, lod_sky);
    //: Over the sea (the ground below nought) the rain raster is a hole,
    //: not a measure: the sky reads the land's mean there (u_wx_sea_wet,
    //: the passport's sea_wet), or the wet bias thinned the clouds over
    //: the sea against the land and the clouds drew the coasts (owner,
    //: 2026-09-13).
    float wet_sky = h_sky < 0.0 ? u_wx_sea_wet : textureLod(u_rain, uv_sky, lod_sky).r;
    float raw_sky;
    float fine;
    wxSky(sky, raw_sky, fine);
    float cover_sky = clamp(clamp(0.5 + (raw_sky - 0.5) * u_wx_gain, 0.0, 1.0) * (1.0 + u_wx_bias * (2.0 * wet_sky - 1.0)), 0.0, 1.0);
    //: The grain (item 9), the systems' own finer texture riding their
    //: spin: it frays the edge -- added to the cover before the gate, which
    //: for the drawing starts CLOUD_FEATHER below the vault's, so the thin
    //: edge fades out rather than stops -- and it mottles the body, grey in
    //: its hollows. The gate the probe reads is the vault's, untouched.
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
  //: this ground. The ground's own rain share stretches the cover as a
  //: factor: a wet windward slope thickens what the wind brings, a dry lee
  //: thins it, and neither makes weather of a clear sky.
  //: The sea's rain share is the land's mean, as over the shell above.
  float wet_here = h < 0.0 ? u_wx_sea_wet : rain01;
  float cover = clamp(wxCover(here) * (1.0 + u_wx_bias * (2.0 * wet_here - 1.0)), 0.0, 1.0);
  float cloud = smoothstep(u_wx_gates.x, u_wx_gates.y, cover);
  float rain_now = smoothstep(u_wx_gates.z, u_wx_gates.w, cover);
  float cloud_alt = max(asin(clamp(high, 0.0, 1.0)), CLOUD_SUN_MIN);
  float cloud_turn = (CLOUD_KM * 1000.0 / tan(cloud_alt)) / metres;
  vec3 q_cloud = here * cos(cloud_turn) + sunward * sin(cloud_turn);
  //: The cloud toward the sun is read over this pixel's ground -- its rain
  //: share -- as the shadow is a tint, not a reading: one point, one law.
  float cloud_over = smoothstep(u_wx_gates.x, u_wx_gates.y, clamp(wxCover(q_cloud) * (1.0 + u_wx_bias * (2.0 * wet_here - 1.0)), 0.0, 1.0));
`;

export const CLOUDS_OVER_GLSL = `
  //: The shell's clouds last, over the ground's night and the layers, and
  //: on the terrain alone (the client hands u_clouds by the layer, D-336).
  col = mix(col, cloud_col, a_sky);
`;
