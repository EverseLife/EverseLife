// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the weather (D-335, D-336 item 13): cloud and rain as a
 * field over the sphere that is a function of the place and the moment,
 * and nothing else -- made of **systems**, not of a picture carried by the
 * wind.
 *
 * The same law the engine reads (`src/weather.py`, `weather_cover`) on the same
 * numbers of the book, so what the picture shows raining is what the
 * engine reads as rain there. A lattice of cells in latitude and
 * longitude, a cell the vault's `weather.cell_km` of arc; each cell bears
 * one cloud system per life -- born, grown and gone over
 * WX_LIFE_SLICES of `weather.change_days`, on a phase of its own, a new
 * system with a new place, size and texture each life. A system drifts
 * with the wind of its own row -- west in the trades and the polar
 * easterlies, east in the westerlies, the rain march's own belts -- so at
 * the edge of a belt the systems of the two winds pass one another and
 * nothing is smeared between them: a system is a body, not a sheet.
 * Where the wind shears -- the belt's edge -- the system spins: cyclonic
 * on the poleward edge (counter-clockwise seen from above in the north),
 * anticyclonic and clearer on the equatorward one, as the highs and lows
 * of a real sky; the spin is faster in the core than at the rim, so the
 * texture winds into a spiral as the system lives. The cover of a point
 * is the union of the systems that reach it. Everything hashes on whole
 * numbers, so a CPU and a GPU agree; the three copies (here,
 * `weather.weatherAt`, the engine) differ only by the blend's float.
 *
 * Glued into the fragment after its uniforms: it names u_wx_cell,
 * u_wx_wind, u_wx_time, u_wx_belts, u_wx_spin and u_wx_gain, and leaves
 * wxSky (cover and grain for the drawn clouds) and wxCover (the law).
 */

/** The law's shape, one on every side: how many slices a system lives,
 *  the smallest and the largest system in cells (at most one and a half,
 *  so the three cells about a point are all the systems that reach it),
 *  the step the wind's shear is read over, how much an anticyclone
 *  clears, how cloudy a system's texture is at its floor, the weight of
 *  the texture's finer octave, and the hash's own multipliers. Written
 *  here once and put into the GLSL below, so the three copies cannot
 *  part on a number without a test noticing. */
export const WX_LIFE_SLICES = 2;
export const WX_SIZE_MIN = 0.8;
export const WX_SIZE_MAX = 1.5;
export const WX_SHEAR_DEG = 1;
export const WX_CLEAR_HIGH = 0.5;
export const WX_TEX_FLOOR = 0.35;
export const WX_OCTAVE_2 = 0.35;
export const WX_HASH = [1597334677, 3812015801, 2798796415, 3367900313] as const;
export const WX_MIX = 0x45d9f3b;
/** What the ground's rain share is taken to be over the sea, where the
 *  rain raster holds nought (the march records nothing falling onto the
 *  sea): the neutral half, so the wet bias neither thins nor thickens the
 *  sky there. Read as nought, the sea thinned every cloud to the shore's
 *  line and the clouds drew the coasts (owner, 2026-09-13). */
export const WX_SEA_WET = 0.5;
/** The picture's grain of a system (D-336 item 9): two finer octaves of
 *  its own texture, this many times finer, riding its spin. */
export const WX_GRAIN_SCALE = 4;

export const WEATHER_GLSL = `
const float WX_SEA_WET = ${WX_SEA_WET.toFixed(2)};
//: The integer hash of a lattice corner and a slice: whole numbers in, so
//: a CPU and a GPU agree (weatherGlsl.ts).
float wxHash(ivec3 c, int w) {
  uint n = uint(c.x) * ${WX_HASH[0]}u ^ uint(c.y) * ${WX_HASH[1]}u ^ uint(c.z) * ${WX_HASH[2]}u ^ uint(w) * ${WX_HASH[3]}u;
  n = (n ^ (n >> 16u)) * ${WX_MIX}u;
  n = (n ^ (n >> 16u)) * ${WX_MIX}u;
  n ^= n >> 16u;
  return float(n & 0xffffffu) / 16777216.0;
}

//: A value noise on the plane, on the system's own corners (z is the system).
float wxNoise2(vec2 p, int z, int w) {
  vec2 i = floor(p);
  vec2 f = p - i;
  f = f * f * (3.0 - 2.0 * f);
  ivec3 c = ivec3(int(i.x), int(i.y), z);
  float n0 = mix(wxHash(c, w), wxHash(c + ivec3(1, 0, 0), w), f.x);
  float n1 = mix(wxHash(c + ivec3(0, 1, 0), w), wxHash(c + ivec3(1, 1, 0), w), f.x);
  return mix(n0, n1, f.y);
}

//: The rain march's own edge: a smooth step a belt's edge wide, centred.
float wxBelt(float x) {
  float t = clamp(x + 0.5, 0.0, 1.0);
  return t * t * (3.0 - 2.0 * t);
}

//: The eastward wind at a latitude (radians), minus one to one: west in
//: the trades and past the westerlies, east between, turning over the
//: belt's edge.
float wxEast(float lat) {
  float a = abs(lat);
  return 2.0 * wxBelt((a - u_wx_belts.x) / u_wx_belts.z) * (1.0 - wxBelt((a - u_wx_belts.y) / u_wx_belts.z)) - 1.0;
}

//: The wind's shear at a latitude, minus one to one: positive where the
//: eastward wind falls off poleward (the polar front -- cyclones), negative
//: where it rises (the subtropical edge -- anticyclones), nought in the
//: middle of a belt.
float wxShear(float lat) {
  float d = radians(${WX_SHEAR_DEG.toFixed(1)});
  float a = abs(lat);
  float s = (wxEast(a + d) - wxEast(max(0.0, a - d))) / (2.0 * d);
  return clamp(-s * u_wx_belts.z / 3.0, -1.0, 1.0);
}

//: The sky over a point of the ball: the cover, nought to one before the
//: gain and the ground's wetness, and the grain of the picture, the
//: systems' own finer texture weighted by their share of the cover.
void wxSky(vec3 p, out float cover, out float grain) {
  float lat = degrees(asin(clamp(p.z, -1.0, 1.0)));
  float lon = degrees(atan(p.y, p.x));
  float cell = u_wx_cell;
  float nRows = max(2.0, floor(180.0 / cell + 0.5));
  float dr = 180.0 / nRows;
  float r0 = floor((lat + 90.0) / dr);
  float keep = 1.0;
  float grainSum = 0.0;
  float grainWeight = 0.0;
  for (int i = -1; i <= 1; i++) {
    float r = r0 + float(i);
    if (r < 0.0 || r >= nRows) continue;
    float latR = -90.0 + (r + 0.5) * dr;
    float nCols = max(4.0, floor(360.0 * cos(radians(latR)) / cell + 0.5));
    float dc = 360.0 / nCols;
    float speed = u_wx_wind * wxEast(radians(latR));
    float c0 = floor((lon - speed * u_wx_time.x) / dc);
    int ri = int(r);
    for (int j = -1; j <= 1; j++) {
      float c = c0 + float(j);
      int cc = int(mod(c, nCols));
      //: The system's life: which life the cell is on, and how far along.
      float phase = wxHash(ivec3(cc, ri, 0), 11);
      float life = u_wx_time.x / (${WX_LIFE_SLICES.toFixed(1)} * u_wx_time.y) + phase;
      float k = floor(life);
      float age = life - k;
      float env = sin(3.14159265 * age);
      int ki = int(k);
      float jx = wxHash(ivec3(cc, ri, ki), 12) - 0.5;
      float jy = wxHash(ivec3(cc, ri, ki), 13) - 0.5;
      float size = ${WX_SIZE_MIN.toFixed(2)} + ${(WX_SIZE_MAX - WX_SIZE_MIN).toFixed(2)} * wxHash(ivec3(cc, ri, ki), 14);
      float latS = latR + jy * dr;
      float lonS = (c + 0.5 + jx) * dc + speed * u_wx_time.x;
      float dlon = mod(lon - lonS + 180.0, 360.0) - 180.0;
      float dx = dlon * cos(radians(latS)) / cell;
      float dy = (lat - latS) / cell;
      float d2 = (dx * dx + dy * dy) / (size * size);
      if (d2 >= 1.0) continue;
      float fall = (1.0 - d2) * (1.0 - d2);
      float z = wxShear(radians(latS));
      float hemi = latS >= 0.0 ? 1.0 : -1.0;
      //: The spin: faster in the core than at the rim, so the texture
      //: winds into a spiral over the system's life.
      float theta = u_wx_spin * z * hemi * age * ${WX_LIFE_SLICES.toFixed(1)} * u_wx_time.y * (1.0 - d2);
      float cs = cos(theta);
      float sn = sin(theta);
      vec2 u = vec2(dx * cs + dy * sn, -dx * sn + dy * cs) / size * 2.0 + 7.0 * vec2(jx, jy);
      int sid = (ri * 4096 + cc) * 64 + (ki & 63);
      float tex = ${(1 - WX_OCTAVE_2).toFixed(2)} * wxNoise2(u, sid, 21) + ${WX_OCTAVE_2.toFixed(2)} * wxNoise2(u * 2.0, sid, 22);
      tex = ${WX_TEX_FLOOR.toFixed(2)} + ${(1 - WX_TEX_FLOOR).toFixed(2)} * tex;
      float clear = 1.0 - ${WX_CLEAR_HIGH.toFixed(2)} * max(0.0, -z);
      float share = tex * env * fall * clear;
      keep *= 1.0 - share;
      //: The picture's grain rides the system's own frame.
      float fine = 0.6 * wxNoise2(u * ${WX_GRAIN_SCALE.toFixed(1)}, sid, 23) + 0.4 * wxNoise2(u * ${(WX_GRAIN_SCALE * 2).toFixed(1)}, sid, 24);
      grainSum += share * fine;
      grainWeight += share;
    }
  }
  cover = 1.0 - keep;
  grain = grainWeight > 0.0 ? grainSum / grainWeight : 0.5;
}

//: The cover alone, spread by the gain about a half: the law the engine
//: and the probe read.
float wxCover(vec3 p) {
  float cover;
  float grain;
  wxSky(p, cover, grain);
  return clamp(0.5 + (cover - 0.5) * u_wx_gain, 0.0, 1.0);
}
`;
