// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The ground drawn by the GPU (landscape plan wave 5, §9.4 level A): one
 * canvas under the map's SVG, a quad over it, and `shade.ts`'s fragment
 * shader reading the field's rasters for every pixel. Nodes, roads, the
 * scout's field, the names and the night stay SVG and stay on top.
 *
 * The canvas follows the SVG, not the other way round: at every frame the
 * camera paints, the SVG's own screen matrix (`getScreenCTM`) says where
 * the eye's point and a map unit land on the canvas, and the shader gets
 * those two numbers. Nothing is measured twice, and a frame the camera
 * moves without React -- the chase, the wheel -- moves the ground with it
 * because the camera's `onFrame` calls `draw` here as it sets the viewBox.
 *
 * Rasters are asked once per planet and kept for the page like the sketch;
 * the textures are built once per planet per context. The palette is read
 * off the theme when it changes and handed to the shader once (`sync`),
 * not every frame: a frame uploads the five numbers that move.
 *
 * From the city frame in the ground also carries its grain (wave 8): the
 * same shader, one more texture -- the rock's hardness -- and a strength
 * the frame's width sets, nought on the wider frames where a texture of
 * forty metres would be a screen of noise.
 *
 * The map is told how this is going (`onState`): until the textures are up
 * the SVG ground keeps drawing the land, and if the GPU refuses -- no
 * context, a shader the driver will not take, a context lost and not
 * given back -- the SVG ground stays for good. A lost context is a fact of
 * browsers (a dozen canvases and the oldest goes): it is listened for, and
 * a restored one is rebuilt.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import type { RasterPassport } from "../../api";
import { useTerrain } from "./Ground";
import { UNITS_PER_METRE, type Eye } from "./globe";
import { rastersOf, type Rasters } from "./rasters";
import {
  FRAGMENT,
  PALETTE_SLOTS,
  VERTEX,
  deepOf,
  EDGE_M,
  GRAIN_M,
  edgeStrength,
  formCodes,
  grainStrength,
  latticeAt,
  byteChain,
  mipChain,
  paletteOf,
  sunDirection,
  type Palette,
} from "./shade";

export type GroundGLHandle = { draw: () => void };
/** How the GPU ground is doing: on its way, drawing, or given up. */
export type GroundGLState = "loading" | "ready" | "failed";

type Textures = {
  height: WebGLTexture;
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
  passport: RasterPassport;
  /** The deepest sea of the raster, metres: the water's shade runs to it. */
  deep: number;
};

type Program = {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  at: (name: string) => WebGLUniformLocation | null;
  textures: Map<string, Textures>;
  /** What the shader was last handed of the things that do not move
   *  between frames: the planet's textures, the palette, the mountain line. */
  synced: { planet: string; palette: Palette; highFrom: number } | null;
};

function compile(gl: WebGL2RenderingContext, kind: number, source: string): WebGLShader {
  const shader = gl.createShader(kind);
  if (!shader) throw new Error("no shader");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const why = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`shader: ${why}`);
  }
  return shader;
}

function setUp(canvas: HTMLCanvasElement): Program | null {
  const gl = canvas.getContext("webgl2", { premultipliedAlpha: true, antialias: false });
  if (!gl) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`program: ${gl.getProgramInfoLog(program)}`);
  }
  gl.useProgram(program);
  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  const pos = gl.getAttribLocation(program, "a_pos");
  gl.enableVertexAttribArray(pos);
  gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  const places = new Map<string, WebGLUniformLocation | null>();
  const at = (name: string) => {
    if (!places.has(name)) places.set(name, gl.getUniformLocation(program, name));
    return places.get(name) ?? null;
  };
  //: What never changes: the light and the sampler units.
  gl.uniform3fv(at("u_light"), sunDirection());
  gl.uniform1i(at("u_height"), 0);
  gl.uniform1i(at("u_biome"), 1);
  gl.uniform1i(at("u_form"), 2);
  gl.uniform1i(at("u_rock"), 3);
  gl.uniform1i(at("u_wet"), 4);
  gl.uniform1i(at("u_stream"), 5);
  return { gl, program, at, textures: new Map(), synced: null };
}

/** Give the context back: its textures at once, and the context itself
 *  once the canvas has really left the page -- a browser that counts
 *  contexts must not lose an older one for this. React in development
 *  runs an effect's cleanup and setup again on the same canvas, and a
 *  context lost then would come back lost to the setup: so the context is
 *  let go only when the canvas is no longer in the document. */
function tearDown(program: Program | null, canvas: HTMLCanvasElement): void {
  if (!program) return;
  const { gl } = program;
  for (const t of program.textures.values()) {
    gl.deleteTexture(t.height);
    gl.deleteTexture(t.biome);
    gl.deleteTexture(t.form);
    gl.deleteTexture(t.rock);
    gl.deleteTexture(t.stream);
    gl.deleteTexture(t.lake);
  }
  program.textures.clear();
  program.synced = null;
  setTimeout(() => {
    if (!canvas.isConnected) gl.getExtension("WEBGL_lose_context")?.loseContext();
  }, 0);
}

/** The rasters as textures: the height a half-float red with its own mip
 *  chain and linear filtering; the classes unsigned bytes, read nearest --
 *  a class, not a mean of two (plan §9.3); the rock a byte read back as a
 *  number between nought and one, and that one blends -- hardness is a
 *  measure, and a mean of two hardnesses is a hardness. */
//: The rasters are an atlas of twelve square faces, not a cylinder of
//: latitude and longitude: nothing wraps round its right edge any more, and
//: a sample that walks off a face lands on the face that is really there,
//: because the projection put it there. Both axes clamp.
function upload(gl: WebGL2RenderingContext, passport: RasterPassport, rasters: Rasters): Textures {
  const { rows, cols } = passport;
  const height = gl.createTexture();
  if (!height) throw new Error("no texture");
  gl.bindTexture(gl.TEXTURE_2D, height);
  const heights = rasters.height;
  const chain = mipChain(heights, cols, rows, passport.nside + 2 * passport.border);
  chain.forEach((level, index) => {
    gl.texImage2D(gl.TEXTURE_2D, index, gl.R16F, level.cols, level.rows, 0, gl.RED, gl.FLOAT, level.data);
  });
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, chain.length - 1);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const tile = passport.nside + 2 * passport.border;
  //: Every raster gets the chain the height has had from the first day.
  //: Without one a pixel covering ten texels reads one of them and shimmers
  //: as the hand moves: a one-texel river blinking in and out on the globe,
  //: a coast fizzing along its length, a biome speckling at the far frames.
  //: How a level is made differs by what the byte means (`byteChain`) --
  //: a share is averaged, a class is picked, because the mean of two codes
  //: is a third code that means something else.
  const classes = (bytes: Uint8Array): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    const chain = byteChain(bytes, cols, rows, "pick", tile);
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
  const measure = (bytes: Uint8Array, mipped: boolean): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    //: A chain where the coarse level still means something, and none where
    //: it would not. The grain of the rock is a property of a region and
    //: averages honestly; the lake's share and the river's ribbon are a cell
    //: or two wide, and their mean over four cells is already under the
    //: knife the shader cuts them by -- mipped, the rivers go out at the
    //: first level up. So those two are read at the finest level and have
    //: no other (`shade.ts` asks for level nought by name).
    const chain = mipped ? byteChain(bytes, cols, rows, "mean", tile) : [{ data: bytes, cols, rows }];
    chain.forEach((level, index) => {
      gl.texImage2D(
        gl.TEXTURE_2D, index, gl.R8, level.cols, level.rows, 0,
        gl.RED, gl.UNSIGNED_BYTE, level.data,
      );
    });
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, chain.length - 1);
    gl.texParameteri(
      gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER,
      mipped ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR,
    );
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  return {
    height,
    biome: classes(rasters.biome),
    form: classes(rasters.form),
    rock: measure(rasters.rock, true),
    lake: measure(rasters.lake, false),
    stream: measure(rasters.stream, false),
    passport,
    deep: deepOf(heights),
  };
}

/** Hand the shader what does not move between frames, when it changed:
 *  the planet's textures and their passport, the palette, the mountain line. */
function sync(program: Program, planet: string, palette: Palette, highFrom: number): boolean {
  const textures = program.textures.get(planet);
  if (!textures) return false;
  const was = program.synced;
  if (was && was.planet === planet && was.palette === palette && was.highFrom === highFrom) return true;
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
  gl.uniform3fv(at("u_sea_deep"), palette.seaDeep);
  gl.uniform3fv(at("u_lake"), palette.lake);
  gl.uniform3fv(at("u_high"), palette.high);
  const codes = formCodes(passport);
  gl.uniform4ui(at("u_cliff_forms"), ...codes.cliff);
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
  program.synced = { planet, palette, highFrom };
  return true;
}

const RAD = Math.PI / 180;

export const GroundGL = forwardRef<
  GroundGLHandle,
  {
    planet: string;
    eye: Eye;
    radius: number;
    /** The SVG the ground lies under: its screen matrix places the eye. */
    svg: React.RefObject<SVGSVGElement | null>;
    /** Told when the ground starts drawing, and when it gives up. */
    onState: (state: GroundGLState) => void;
  }
>(function GroundGL({ planet, eye, radius, svg, onState }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const probeRef = useRef<HTMLSpanElement | null>(null);
  const programRef = useRef<Program | null>(null);
  const terrain = useTerrain(planet);
  const passport = terrain?.raster ?? null;
  //: Which planet's textures are up, and which life of the context: a lost
  //: and restored context is a new life, and everything is rebuilt for it.
  const [ready, setReady] = useState<string | null>(null);
  const [life, setLife] = useState(0);
  const tell = useRef(onState);
  tell.current = onState;

  //: The theme: re-read when the scheme or the `data-light` choice changes.
  const [theme, setTheme] = useState(0);
  useEffect(() => {
    const scheme = matchMedia("(prefers-color-scheme: light)");
    const bump = () => setTheme((n) => n + 1);
    scheme.addEventListener("change", bump);
    const watcher = new MutationObserver(bump);
    watcher.observe(document.documentElement, { attributes: true, attributeFilter: ["data-light"] });
    return () => {
      scheme.removeEventListener("change", bump);
      watcher.disconnect();
    };
  }, []);
  //: The palette is read through the probe -- not `getPropertyValue`, which
  //: hands back `light-dark(...)` unresolved -- and reading a computed style
  //: is a layout matter, so it is done after the probe is in the page, not
  //: during the render.
  const [palette, setPalette] = useState<Palette | null>(null);
  useLayoutEffect(() => {
    const probe = probeRef.current;
    setPalette(
      probe && passport ? paletteOf(probe, planet, passport.biomes, passport.fluid) : null,
    );
  }, [theme, planet, passport]);

  //: The context: once per life of the canvas, given back on unmount.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      programRef.current = setUp(canvas);
      if (!programRef.current) tell.current("failed");
    } catch (why) {
      console.warn("ground shader:", why);
      programRef.current = null;
      tell.current("failed");
    }
    const lost = (event: Event) => {
      //: Saying so keeps the browser willing to give it back.
      event.preventDefault();
      programRef.current = null;
      setReady(null);
      tell.current("loading");
    };
    const restored = () => setLife((n) => n + 1);
    canvas.addEventListener("webglcontextlost", lost);
    canvas.addEventListener("webglcontextrestored", restored);
    return () => {
      canvas.removeEventListener("webglcontextlost", lost);
      canvas.removeEventListener("webglcontextrestored", restored);
      tearDown(programRef.current, canvas);
      programRef.current = null;
    };
  }, [life]);
  //: The textures: once per planet per life of the context.
  useEffect(() => {
    const program = programRef.current;
    if (!program || !passport) return;
    if (program.textures.has(planet)) {
      setReady(planet);
      tell.current("ready");
      return;
    }
    let live = true;
    tell.current("loading");
    rastersOf(planet).then(
      (rasters) => {
        const now = programRef.current;
        if (!live || !now) return;
        try {
          now.textures.set(planet, upload(now.gl, passport, rasters));
          setReady(planet);
          tell.current("ready");
        } catch (why) {
          console.warn(`rasters of ${planet}:`, why);
          tell.current("failed");
        }
      },
      (why) => {
        console.warn(`rasters of ${planet}:`, why);
        if (live) tell.current("failed");
      },
    );
    return () => {
      live = false;
    };
  }, [planet, passport, life]);

  //: The mountain line of the sketch: the hypsometric lightening starts
  //: where the SVG ground greys and the biome turns alpine, not at a line
  //: of the shader's own.
  const highFrom = terrain?.mountain_level ?? 1;
  const state = useRef({ eye, radius, palette, planet, highFrom });
  state.current = { eye, radius, palette, planet, highFrom };

  const draw = useCallback(() => {
    const program = programRef.current;
    const canvas = canvasRef.current;
    const svgEl = svg.current;
    const { eye, radius, palette, planet, highFrom } = state.current;
    if (!program || !canvas || !svgEl || !palette) return;
    if (!sync(program, planet, palette, highFrom)) return;
    const { gl, at } = program;
    const dpr = window.devicePixelRatio || 1;
    //: The canvas is laid over the svg's box and nowhere else. It paints the
    //: ground by the svg's own screen matrix, so wherever it reaches it
    //: draws correct land -- and where it reached past the svg it drew land
    //: with nothing on it: on a narrow pane the bars stand in flow under the
    //: map, and the ground ran on behind them (owner, 2026-09-11). Written
    //: here rather than in the stylesheet because only here are both boxes
    //: known: a rule that matched a sibling's size does not exist in CSS.
    //: One read of the layout, and the styles written only when they
    //: change -- **as the stylesheet keeps them**, to an eighth of a pixel.
    //: Written as the box came, "648.7999877929688px" read back as
    //: "648.8px", never matched, and was written again on every frame of
    //: the camera; a style written on a WebGL canvas is a relayout of it,
    //: and the pane stalled for a second on every frame that drew (owner,
    //: 2026-09-11: the camera froze on the move). The write must be rare
    //: for the same reason a read-after-write must not happen at all.
    const over = svgEl.getBoundingClientRect();
    const parent = canvas.offsetParent?.getBoundingClientRect();
    if (parent) {
      const eighth = (v: number) => `${Math.round(v * 8) / 8}px`;
      const place = [
        eighth(over.left - parent.left),
        eighth(over.top - parent.top),
        eighth(over.width),
        eighth(over.height),
      ] as const;
      if (canvas.style.left !== place[0]) canvas.style.left = place[0];
      if (canvas.style.top !== place[1]) canvas.style.top = place[1];
      if (canvas.style.width !== place[2]) canvas.style.width = place[2];
      if (canvas.style.height !== place[3]) canvas.style.height = place[3];
    }
    const width = Math.max(1, Math.round(over.width * dpr));
    const height = Math.max(1, Math.round(over.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
    }
    const ctm = svgEl.getScreenCTM();
    if (!ctm || !(ctm.a > 0)) return;
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(at("u_size"), width, height);
    //: The canvas stands exactly over the svg, so the svg's box is its own.
    gl.uniform2f(at("u_origin"), (ctm.e - over.left) * dpr, (ctm.f - over.top) * dpr);
    const units = 1 / (ctm.a * dpr);
    gl.uniform1f(at("u_units"), units);
    gl.uniform1f(at("u_radius"), radius);
    gl.uniform2f(at("u_eye"), eye.lat * RAD, eye.lon * RAD);
    //: Both textures of the ground -- its grain and the roughening of the
    //: colour's edge -- are fixed sizes **in metres of the country**, so
    //: what the zoom changes is never their shape, only whether they can be
    //: made out at all. Each fades in over its own couple of pixels: under
    //: one, a texture is aliasing rather than texture, and a wander finer
    //: than a pixel is salt and pepper rather than a rough edge.
    const perPixel = units / UNITS_PER_METRE;
    gl.uniform1f(at("u_grain"), grainStrength(GRAIN_M / perPixel));
    gl.uniform1f(at("u_edge"), edgeStrength(EDGE_M / perPixel));
    //: Where the eye stands in each lattice, reckoned in full precision
    //: here so that the fragment only ever adds a small number to it.
    const radiusM = radius / UNITS_PER_METRE;
    const atLattice = (cellM: number) =>
      latticeAt(eye.lat * RAD, eye.lon * RAD, radiusM, cellM);
    gl.uniform3fv(at("u_grain_at"), atLattice(GRAIN_M));
    gl.uniform3fv(at("u_edge_at"), atLattice(EDGE_M));
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }, [svg]);
  useImperativeHandle(ref, () => ({ draw }), [draw]);

  //: What React knows of -- the eye, the planet, the palette, the rasters'
  //: arrival -- redraws; the camera's frames redraw through the handle.
  useEffect(() => {
    draw();
  }, [draw, eye, radius, palette, ready, planet, highFrom]);
  //: The box: a resize of the pane is a resize of the canvas. Watched on the
  //: **svg**, because the canvas's own box is written by the draw above --
  //: watching it would be watching one's own hand, and the canvas would keep
  //: whatever size it was made with while the pane grew around it.
  useEffect(() => {
    const over = svg.current;
    if (!over) return;
    const watcher = new ResizeObserver(() => draw());
    watcher.observe(over);
    return () => watcher.disconnect();
  }, [draw, svg]);

  return (
    <>
      <canvas ref={canvasRef} className="ground-gl" aria-hidden="true" />
      {/* The probe the palette is read through: a hidden span the theme's
          colours are computed on, one expression at a time. */}
      <span ref={probeRef} className="ground-probe" aria-hidden="true" />
    </>
  );
});
