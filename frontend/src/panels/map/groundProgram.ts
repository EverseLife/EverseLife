// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GPU ground's resources (landscape plan wave 5): the context and its
 * program, the planet's textures, and what the shader is handed of the
 * things that do not move between frames. Cut out of `GroundGL.tsx` on
 * 2026-09-18, when the component was to take on how often and how finely
 * it paints (`sharpness.ts`) and would have crossed the eight-hundred-line
 * bar: this file is the WebGL, the component is the React lifecycle and
 * the frames.
 *
 * Two things here are slow and neither may stop the page:
 *
 * - **the program's link** -- the fragment is some sixty thousand
 *   characters, and a driver takes a second or two over it (1.9 s on a
 *   laptop's D3D11, 2026-09-18). Asking for the link's status waits for it
 *   on the page's thread, so it is asked only once the browser says the
 *   link is done (`KHR_parallel_shader_compile`) and the page is live all
 *   the while; the map shows the planet's disk alone meanwhile (`Ground`,
 *   `wait`). A browser without the extension waits as it always did;
 * - **the rasters made ready** -- `groundPrep.ts`, in a worker where there
 *   is one. The page only hands the finished levels to the GPU.
 */

import type { RasterPassport } from "../../api";
import { FRAGMENT, VERTEX } from "./fragment";
import { groundOf, prepare, type GroundRasters, type Level, type Prepared } from "./groundPrep";
import type { Answer, Job } from "./groundPrep.worker";
import {
  PALETTE_SLOTS,
  formCodes,
  sunDirection,
  type DryLaw,
  type Palette,
} from "./shade";
import { grainTable } from "./grain";

export type Textures = {
  height: WebGLTexture;
  /** The top of the ground: the height's max chain, read by the cast
   *  shadow a stretch at a time (`topChain`), and the tallest ground of
   *  the planet, metres, past which the march has nothing to find. */
  top: WebGLTexture;
  topM: number;
  biome: WebGLTexture;
  form: WebGLTexture;
  /** The hardness of the ground, a byte read back as nought to one: the
   *  grain takes its edge off it (wave 8). */
  rock: WebGLTexture;
  /** The lakes as a quantity rather than as a class, so their shore is cut
   *  between the cells as the sea's is by the height. */
  lake: WebGLTexture;
  /** And the rivers the same way, for the same reason (owner, 2026-09-11):
   *  as a class a river is a chain of whole cells with right angles. */
  stream: WebGLTexture;
  /** The climate's two, for the climate layers (D-331). */
  temperature: WebGLTexture;
  rain: WebGLTexture;
  /** Metres to the nearest fresh water, for the moisture layer: the
   *  engine's own "beside water" (D-331 addendum). */
  river: WebGLTexture;
  passport: RasterPassport;
  /** The deepest sea of the raster, metres: the water's shade runs to it. */
  deep: number;
  /** Whether these are the whole picture, or its quick copy standing in
   *  until the whole comes (`rasters.previewOf`, 2026-09-18). */
  whole: boolean;
};

export type Program = {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  /** The quad the fragment is drawn over, given back with the program. */
  quad: WebGLBuffer | null;
  /** Whether the link is done and the fixed uniforms are set: until then
   *  the textures may go up, but nothing is drawn (`linkDone`). */
  linked: boolean;
  /** The two shaders, kept for their logs should the link fail. */
  shaders: [WebGLShader, WebGLShader];
  /** The browser's word that the link is done, where it gives one. */
  parallel: KHR_parallel_shader_compile | null;
  at: (name: string) => WebGLUniformLocation | null;
  textures: Map<string, Textures>;
  /** What the shader was last handed of the things that do not move
   *  between frames: the planet's textures -- by the set itself, so the
   *  whole picture put in the quick copy's place is bound anew -- the
   *  palette, the mountain line. */
  synced: {
    textures: Textures;
    palette: Palette;
    highFrom: number;
    law: DryLaw;
    grains: Record<string, unknown> | null;
  } | null;
};

/** Where the quad's corners go in: bound before the link, so nothing about
 *  the attribute waits for it. */
const A_POS = 0;

function shaderOf(gl: WebGL2RenderingContext, kind: number, source: string): WebGLShader {
  const shader = gl.createShader(kind);
  if (!shader) throw new Error("no shader");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return shader;
}

/** The context and the program, the program's compile and link asked for
 *  and not waited on (`linkDone` says when they are through). Null where
 *  there is no WebGL2. */
export function setUp(canvas: HTMLCanvasElement): Program | null {
  const gl = canvas.getContext("webgl2", { premultipliedAlpha: true, antialias: false });
  if (!gl) return null;
  const program = gl.createProgram();
  if (!program) return null;
  //: Asked for before the compile: the extension is what makes the status
  //: of the compile and the link a question that does not wait.
  const parallel = gl.getExtension("KHR_parallel_shader_compile");
  const vertex = shaderOf(gl, gl.VERTEX_SHADER, VERTEX);
  const fragment = shaderOf(gl, gl.FRAGMENT_SHADER, FRAGMENT);
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.bindAttribLocation(program, A_POS, "a_pos");
  gl.linkProgram(program);
  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  gl.enableVertexAttribArray(A_POS);
  gl.vertexAttribPointer(A_POS, 2, gl.FLOAT, false, 0, 0);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  const places = new Map<string, WebGLUniformLocation | null>();
  const at = (name: string) => {
    if (!places.has(name)) places.set(name, gl.getUniformLocation(program, name));
    return places.get(name) ?? null;
  };
  return {
    gl,
    program,
    quad,
    linked: false,
    shaders: [vertex, fragment],
    parallel,
    at,
    textures: new Map(),
    synced: null,
  };
}

/** Whether the program is linked and ready to draw: asked without waiting
 *  while the browser says the link is under way, and once it is through,
 *  the program is taken into use and handed what never changes -- the
 *  light and the sampler units. A link that failed throws with the
 *  driver's words, and the svg ground stays. */
export function linkDone(p: Program): boolean {
  if (p.linked) return true;
  const { gl, program, parallel } = p;
  //: A lost context says the link is done and that it failed: neither is
  //: so, and the loss is answered by the context's own events.
  if (gl.isContextLost()) return false;
  if (parallel && !gl.getProgramParameter(program, parallel.COMPLETION_STATUS_KHR)) return false;
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const logs = p.shaders.map((shader) => gl.getShaderInfoLog(shader)).filter(Boolean);
    throw new Error(`program: ${gl.getProgramInfoLog(program)} ${logs.join(" ")}`);
  }
  gl.useProgram(program);
  //: What never changes: the light and the sampler units.
  gl.uniform3fv(p.at("u_light"), sunDirection());
  gl.uniform1i(p.at("u_height"), 0);
  gl.uniform1i(p.at("u_biome"), 1);
  gl.uniform1i(p.at("u_form"), 2);
  gl.uniform1i(p.at("u_rock"), 3);
  gl.uniform1i(p.at("u_wet"), 4);
  gl.uniform1i(p.at("u_stream"), 5);
  gl.uniform1i(p.at("u_temp"), 6);
  gl.uniform1i(p.at("u_rain"), 7);
  gl.uniform1i(p.at("u_river"), 8);
  gl.uniform1i(p.at("u_top"), 9);
  p.linked = true;
  return true;
}

/** A planet's textures given back to the context. */
export function drop(gl: WebGL2RenderingContext, t: Textures): void {
  gl.deleteTexture(t.height);
  gl.deleteTexture(t.top);
  gl.deleteTexture(t.biome);
  gl.deleteTexture(t.form);
  gl.deleteTexture(t.rock);
  gl.deleteTexture(t.stream);
  gl.deleteTexture(t.lake);
  gl.deleteTexture(t.temperature);
  gl.deleteTexture(t.rain);
  gl.deleteTexture(t.river);
}

/** Give the context back: its textures and its program at once, and the
 *  context itself once the canvas has really left the page -- a browser
 *  that counts contexts must not lose an older one for this. React in
 *  development runs an effect's cleanup and setup again on the same canvas,
 *  and a context lost then would come back lost to the setup: so the
 *  context is let go only when the canvas is no longer in the document. */
export function tearDown(program: Program | null, canvas: HTMLCanvasElement): void {
  if (!program) return;
  const { gl } = program;
  for (const t of program.textures.values()) drop(gl, t);
  program.textures.clear();
  program.synced = null;
  gl.deleteProgram(program.program);
  for (const shader of program.shaders) gl.deleteShader(shader);
  gl.deleteBuffer(program.quad);
  setTimeout(() => {
    if (!canvas.isConnected) gl.getExtension("WEBGL_lose_context")?.loseContext();
  }, 0);
}

//: One worker for the page, made the first time it is wanted: the atlas's
//: layout map is kept inside it (`atlas.widen`), so the planets after the
//: first are laid out without it. Null once a worker could not be had --
//: the page makes the rasters ready itself from then on.
let helper: Worker | null | undefined;
let jobs = 0;

function helperOf(): Worker | null {
  if (helper === undefined) {
    try {
      helper = new Worker(new URL("./groundPrep.worker.ts", import.meta.url), { type: "module" });
    } catch (why) {
      console.warn("ground worker:", why);
      helper = null;
    }
  }
  return helper;
}

/**
 * The rasters made ready for the GPU (`groundPrep.prepare`), off the page's
 * thread where a worker can be had. The rasters are copied over, not handed:
 * the page keeps them for the vector layer (`rastersOf`). The levels come
 * back handed over. A worker that fails is let go and the page does the
 * work itself, as it did before there was one.
 */
export function prepareOff(passport: RasterPassport, rasters: GroundRasters): Promise<Prepared> {
  const came = groundOf(rasters);
  const worker = helperOf();
  if (!worker) return Promise.resolve(prepare(passport, came));
  const id = ++jobs;
  return new Promise<Prepared>((resolve, reject) => {
    const done = () => {
      worker.removeEventListener("message", heard);
      worker.removeEventListener("error", broke);
      worker.removeEventListener("messageerror", broke);
    };
    const heard = ({ data }: MessageEvent<Answer>) => {
      if (data.id !== id) return;
      done();
      if ("prepared" in data) resolve(data.prepared);
      else reject(new Error(data.failed));
    };
    //: A worker that cannot even start -- a module the browser will not
    //: load as a worker -- or whose answer cannot be read is no worse than
    //: none: it is let go, and the page does the job, as it did before.
    const broke = (event: Event) => {
      done();
      console.warn("ground worker:", event instanceof ErrorEvent ? event.message : event.type);
      worker.terminate();
      helper = null;
      try {
        resolve(prepare(passport, came));
      } catch (why) {
        reject(why);
      }
    };
    worker.addEventListener("message", heard);
    worker.addEventListener("error", broke);
    worker.addEventListener("messageerror", broke);
    worker.postMessage({ id, passport, rasters: came } satisfies Job);
  });
}

/** The prepared levels as textures: the height a half-float red with its
 *  own chain and linear filtering; the classes unsigned bytes, read
 *  nearest -- a class, not a mean of two (plan sec. 9.3); the rock a byte read
 *  back as a number between nought and one, and that one blends -- hardness
 *  is a measure, and a mean of two hardnesses is a hardness. */
//: The rasters are an atlas of twelve square faces, not a cylinder of
//: latitude and longitude: nothing wraps round its right edge any more, and
//: a sample that walks off a face lands on the face that is really there,
//: because the projection put it there. Both axes clamp.
export function upload(gl: WebGL2RenderingContext, ready: Prepared, whole: boolean): Textures {
  const heights = (chain: Level<Float32Array>[]): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    chain.forEach((level, index) => {
      gl.texImage2D(gl.TEXTURE_2D, index, gl.R16F, level.cols, level.rows, 0, gl.RED, gl.FLOAT, level.data);
    });
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, chain.length - 1);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  //: Every raster gets the chain the height has had from the first day.
  //: Without one a pixel covering ten texels reads one of them and shimmers
  //: as the hand moves: a one-texel river blinking in and out on the globe,
  //: a coast fizzing along its length, a biome speckling at the far frames.
  const classes = (chain: Level<Uint8Array>[]): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    chain.forEach((level, index) => {
      gl.texImage2D(
        gl.TEXTURE_2D, index, gl.R8UI, level.cols, level.rows, 0,
        gl.RED_INTEGER, gl.UNSIGNED_BYTE, level.data,
      );
    });
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, chain.length - 1);
    //: An integer texture filters NEAREST and only NEAREST -- between the
    //: levels as within one. The level itself is chosen by the lod the
    //: shader asks for, and that is the whole of the cure here.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST_MIPMAP_NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  //: A chain whose coarse levels mean the share of the texel that is the
  //: thing: the rock's grain and the lake's share average honestly, the
  //: river's ribbon is cut at its bank first and then averaged. The near
  //: frames read the finest level and cut it; the far frames read the
  //: share at the frame's own level, and a river narrower than a pixel
  //: stays a line (owner, 2026-09-12: the rivers break).
  const measure = (chain: Level<Uint8Array>[]): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    chain.forEach((level, index) => {
      gl.texImage2D(
        gl.TEXTURE_2D, index, gl.R8, level.cols, level.rows, 0,
        gl.RED, gl.UNSIGNED_BYTE, level.data,
      );
    });
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, chain.length - 1);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  return {
    height: heights(ready.height),
    //: The same height once more, its coarse levels the top of the ground
    //: rather than its mean: what the cast shadow reads a stretch by.
    top: heights(ready.top),
    topM: ready.topM,
    biome: classes(ready.biome),
    form: classes(ready.form),
    rock: measure(ready.rock),
    lake: measure(ready.lake),
    stream: measure(ready.stream),
    temperature: measure(ready.temperature),
    rain: measure(ready.rain),
    river: measure(ready.river),
    passport: ready.passport,
    deep: ready.deep,
    whole,
  };
}

/** Hand the shader what does not move between frames, when it changed:
 *  the planet's textures and their passport, the palette, the mountain line. */
export function sync(
  program: Program,
  planet: string,
  palette: Palette,
  highFrom: number,
  law: DryLaw,
  grains: Record<string, unknown> | null,
): boolean {
  const textures = program.textures.get(planet);
  if (!textures || !program.linked) return false;
  const was = program.synced;
  if (
    was &&
    was.textures === textures &&
    was.palette === palette &&
    was.highFrom === highFrom &&
    was.law === law &&
    was.grains === grains
  )
    return true;
  const { gl, at } = program;
  const { passport } = textures;
  gl.uniform2f(at("u_atlas"), passport.cols, passport.rows);
  gl.uniform1f(at("u_nside"), passport.nside);
  gl.uniform1f(at("u_border"), passport.border);
  gl.uniform1f(at("u_across"), passport.across);
  gl.uniform1f(at("u_step"), passport.step_m);
  gl.uniform1f(at("u_relief"), passport.relief_m);
  gl.uniform1f(at("u_deep"), textures.deep);
  gl.uniform1f(at("u_high_from"), highFrom);
  gl.uniform3fv(at("u_biomes[0]"), palette.biomes.subarray(0, PALETTE_SLOTS * 3));
  //: The grain of each biome, by the passport's own order of them: the
  //: vault's word for the biome, the picture's numbers for the word.
  gl.uniform4fv(at("u_grains[0]"), grainTable(grains, passport.biomes));
  gl.uniform3fv(at("u_sea_deep"), palette.seaDeep);
  gl.uniform3fv(at("u_lake"), palette.lake);
  gl.uniform3fv(at("u_high"), palette.high);
  const codes = formCodes(passport);
  gl.uniform4ui(at("u_stone_forms"), ...codes.stone);
  gl.uniform2ui(at("u_sand_forms"), ...codes.sand);
  gl.uniform3ui(at("u_ice_forms"), ...codes.ice);
  gl.uniform1i(at("u_shore"), codes.shore);
  const bind = (unit: number, texture: WebGLTexture) => {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, texture);
  };
  bind(0, textures.height);
  bind(1, textures.biome);
  bind(2, textures.form);
  bind(3, textures.rock);
  bind(4, textures.lake);
  bind(5, textures.stream);
  bind(6, textures.temperature);
  bind(7, textures.rain);
  bind(8, textures.river);
  bind(9, textures.top);
  gl.uniform1f(at("u_top_m"), textures.topM);
  gl.uniform1f(at("u_temp_min"), passport.temperature_c.min);
  gl.uniform1f(at("u_temp_step"), passport.temperature_c.step);
  gl.uniform1f(at("u_temp_cold"), passport.temperature_c.cold);
  gl.uniform1f(at("u_temp_hot"), passport.temperature_c.hot);
  //: What the sky over the sea is stretched by: the land's mean rain share,
  //: the engine's own number (owner, 2026-09-13).
  gl.uniform1f(at("u_wx_sea_wet"), passport.sea_wet);
  //: The drying law for the moisture layer (D-331 addendum): off the book,
  //: with the reach in metres, read against the river raster.
  gl.uniform3f(at("u_dry"), law.share, law.perDegree, law.ref);
  gl.uniform1f(at("u_reach_m"), law.reachM);
  program.synced = { textures, palette, highFrom, law, grains };
  return true;
}
