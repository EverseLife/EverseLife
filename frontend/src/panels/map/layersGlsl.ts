// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the map's layers (D-331, D-334, D-335): the colours of the
 * relief, the biomes, the temperature of the moment, the year's rain, the
 * soil's moisture and the hour's weather, blended into the terrain's
 * colour by the weights off `u_layer`.
 *
 * Glued into the fragment's `main` (`fragment.ts`) after the terrain's
 * colour is known, and it reads what stands there by then: `col` (the
 * terrain, lit and tinted), `tone`, `share`, `water_col`, `wet`,
 * `daylight`, `flat_ground`, `t_now`, `rain01`, `rain_now`, `cloud`, `h`,
 * `uv` and `lod`, the ramps of `shade.RAMPS`, `WX_HAZE` and the uniforms
 * of the climate layers. It writes `col`. Cut out of `fragment.ts` on 2026-09-12
 * when the file crossed the bar with the weather.
 */

export const LAYERS_GLSL = `
  //: The layers (D-331), blended by weights off the one uniform rather
  //: than chosen by a branch on it: every colour is a few multiplies over
  //: what the fragment already holds, and a branch on a uniform is what
  //: stalled the frames on 2026-09-11. The relief layer keeps the light
  //: and the night; the legends -- biomes, the climate's two -- keep half
  //: the light so the ground still reads as ground, and no night, for a
  //: legend is read and not looked at; the soil's moisture is a legend too.
  float w_relief = u_layer == 1 ? 1.0 : 0.0;
  float w_biome = u_layer == 2 ? 1.0 : 0.0;
  float w_temp = u_layer == 3 ? 1.0 : 0.0;
  float w_rain = u_layer == 4 ? 1.0 : 0.0;
  float w_moist = u_layer == 5 ? 1.0 : 0.0;
  float w_weather = u_layer == 6 ? 1.0 : 0.0;
  float w_terrain = 1.0 - w_relief - w_biome - w_temp - w_rain - w_moist - w_weather;
  float legend_tone = LEGEND_AMBIENT + (1.0 - LEGEND_AMBIENT) * tone;
  vec3 relief_col = mix(rampHeight(share) * tone, water_col, wet);
  relief_col = mix(relief_col * NIGHT_TINT, relief_col, daylight);
  vec3 biome_col = mix(flat_ground * legend_tone, u_lake, wet);
  //: The moment's temperature (D-334): the season is in the layer, as it
  //: is in the engine's temperature of the moment.
  float warmth = clamp((t_now - u_temp_cold) / max(u_temp_hot - u_temp_cold, 1.0), 0.0, 1.0);
  vec3 temp_col = rampTemp(warmth) * legend_tone;
  temp_col = mix(temp_col, temp_col * LEGEND_WET_DIM, wet);
  vec3 rain_col = rampRain(rain01) * legend_tone;
  rain_col = mix(rain_col, rain_col * LEGEND_WET_DIM, wet);
  //: The soil's moisture (owner, 2026-09-12: a layer for the farmer, not
  //: the water alone): the ground by the drying law of D-296
  //: (farm.life.dry_rate), read off the rasters the fragment already
  //: holds and the law's numbers off the book (shade.dryLaw). The pace a
  //: bed dries at, against the reference: every degree over u_dry.w adds
  //: u_dry.z of it, the rain closes up to u_dry.x of it, and water within
  //: reach leaves u_dry.y of it -- "within reach" by the river raster,
  //: the engine's own metres to the nearest river or lake (terrain.marks_at
  //: against terrain.river_reach_km), with a cell's soft edge; a byte of
  //: metres saturates at 255, and past that the ground is far from water
  //: for any reach the vault has set. One minus the pace, against the
  //: fastest the planet has -- bare ground at its hottest (u_temp_hot) --
  //: so the ramp runs over the whole planet and not over its cold half
  //: alone: the wet end is the bed that keeps what was poured. The water
  //: itself stays water, in the theme's own tones, as the biome layer
  //: draws it. shade.moistureOf is this arithmetic in TypeScript, for the
  //: tests.
  float heat = max(0.0, 1.0 + u_dry.z * (t_now - u_dry.w));
  float river_m = textureLod(u_river, uv, lod).r * 255.0;
  float beside = 1.0 - smoothstep(u_reach_m, u_reach_m + u_step, river_m);
  float pace = heat * (1.0 - u_dry.x * rain01) * mix(1.0, u_dry.y, beside);
  float fastest = max(1.0 + u_dry.z * (u_temp_hot - u_dry.w), 0.05);
  vec3 water_flat = mix(u_lake, u_sea_deep, h >= 0.0 ? 0.0 : clamp(-h / u_deep, 0.0, 1.0));
  vec3 moist_col = mix(rampMoist(clamp(1.0 - pace / fastest, 0.0, 1.0)) * legend_tone, water_flat, min(1.0, wet * LEGEND_WET_EDGE));
  //: The weather layer (D-335): the hour's rain on its ramp, the clouds
  //: as a haze over it -- a legend, lit like one.
  vec3 wx_col = mix(rampWeather(rain_now) * legend_tone, CLOUD_TONE * legend_tone, cloud * WX_HAZE);
  wx_col = mix(wx_col, wx_col * LEGEND_WET_DIM, wet);
  col = col * w_terrain + relief_col * w_relief + biome_col * w_biome + temp_col * w_temp + rain_col * w_rain + moist_col * w_moist + wx_col * w_weather;
`;
