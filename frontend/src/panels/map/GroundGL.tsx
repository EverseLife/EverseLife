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
  //: What never changes: the light and the three sampler units.
  gl.uniform3fv(at("u_light"), sunDirection());
  gl.uniform1i(at("u_height"), 0);
  gl.uniform1i(at("u_biome"), 1);
  gl.uniform1i(at("u_form"), 2);
  gl.uniform1i(at("u_rock"), 3);
  gl.uniform1i(at("u_wet"), 4);
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
function upload(gl: WebGL2RenderingContext, passport: RasterPassport, rasters: Rasters): Textures {
  const { rows, cols } = passport;
  const height = gl.createTexture();
  if (!height) throw new Error("no texture");
  gl.bindTexture(gl.TEXTURE_2D, height);
  const heights = rasters.height;
  const chain = mipChain(heights, cols, rows);
  chain.forEach((level, index) => {
    gl.texImage2D(gl.TEXTURE_2D, index, gl.R16F, level.cols, level.rows, 0, gl.RED, gl.FLOAT, level.data);
  });
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, chain.length - 1);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const classes = (bytes: Uint8Array): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8UI, cols, rows, 0, gl.RED_INTEGER, gl.UNSIGNED_BYTE, bytes);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  const measure = (bytes: Uint8Array): WebGLTexture => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, cols, rows, 0, gl.RED, gl.UNSIGNED_BYTE, bytes);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  return {
    height,
    biome: classes(rasters.biome),
    form: classes(rasters.form),
    rock: measure(rasters.rock),
    lake: measure(rasters.lake),
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
  gl.uniform2f(at("u_cells"), passport.cols, passport.rows);
  gl.uniform1f(at("u_step"), passport.step_m);
  gl.uniform1f(at("u_relief"), passport.relief_m);
  gl.uniform1f(at("u_deep"), textures.deep);
  gl.uniform1f(at("u_high_from"), highFrom);
  gl.uniform3fv(at("u_biomes[0]"), palette.biomes.subarray(0, PALETTE_SLOTS * 3));
  gl.uniform3fv(at("u_sea_shallow"), palette.seaShallow);
  gl.uniform3fv(at("u_sea_deep"), palette.seaDeep);
  gl.uniform3fv(at("u_lake"), palette.lake);
  gl.uniform3fv(at("u_high"), palette.high);
  const codes = formCodes(passport);
  gl.uniform3ui(at("u_water_forms"), ...codes.water);
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
    setPalette(probe && passport ? paletteOf(probe, planet, passport.biomes) : null);
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
    const box = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(box.width * dpr));
    const height = Math.max(1, Math.round(box.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
    }
    const ctm = svgEl.getScreenCTM();
    if (!ctm || !(ctm.a > 0)) return;
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(at("u_size"), width, height);
    gl.uniform2f(at("u_origin"), (ctm.e - box.left) * dpr, (ctm.f - box.top) * dpr);
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
  //: The box: a resize of the pane is a resize of the canvas.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const watcher = new ResizeObserver(() => draw());
    watcher.observe(canvas);
    return () => watcher.disconnect();
  }, [draw]);

  return (
    <>
      <canvas ref={canvasRef} className="ground-gl" aria-hidden="true" />
      {/* The probe the palette is read through: a hidden span the theme's
          colours are computed on, one expression at a time. */}
      <span ref={probeRef} className="ground-probe" aria-hidden="true" />
    </>
  );
});
