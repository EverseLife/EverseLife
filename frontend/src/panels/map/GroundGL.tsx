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
 * because the camera's `onFrame` asks for a paint here as it sets the viewBox.
 *
 * Rasters are asked once per planet and kept for the page like the sketch;
 * the textures are built once per planet per context (`groundProgram.ts`).
 * The palette is read off the theme when it changes and handed to the
 * shader once (`sync`), not every frame: a frame uploads the numbers that
 * move.
 *
 * From the city frame in the ground also carries its grain (wave 8): the
 * same shader, one more texture -- the rock's hardness -- and a strength
 * the frame's width sets, nought on the wider frames where a texture of
 * forty metres would be a screen of noise.
 *
 * **How often and how finely it paints** (2026-09-18, the phones). A paint
 * is the whole canvas through the whole fragment, so the ground paints at
 * most once a frame however many ask: `draw` books the paint for the next
 * frame and every other ask before it lands is the same paint -- a pinch
 * moves two fingers and was two full paints a frame. `drawNow` paints at
 * once, for a caller already inside a frame whose svg moves in it (the
 * camera's chase). And how finely is `sharpness.ts`: a paint in motion is
 * drawn as fine as the GPU has been keeping up with, the still ground at
 * the full scale.
 *
 * The map is told how this is going (`onState`): until the program is
 * linked and the textures are up the SVG ground keeps drawing the land,
 * and if the GPU refuses -- no context, a shader the driver will not take,
 * a context lost and not given back -- the SVG ground stays for good. A
 * lost context is a fact of browsers (a dozen canvases and the oldest
 * goes): it is listened for, and a restored one is rebuilt.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  useMemo,
} from "react";

import { useBook } from "../../actions";
import { useTerrain } from "./Ground";
import { weatherMoment, type WeatherLaw } from "./weather";
import { UNITS_PER_METRE, type Eye, type Geo } from "./globe";
import { heldRasters, previewOf, rastersOf } from "./rasters";
import { previewPassport, type Prepared } from "./groundPrep";
import {
  EDGE_M,
  edgeStrength,
  paletteOf,
  sunVector,
  LAYERS,
  dryLaw,
  type Layer,
  type Palette,
} from "./shade";
import {
  type Season,
  seasonC,
} from "./season";
import {
  GRAIN_M,
  grainStrength,
  latticeAt,
} from "./grain";
import {
  drop,
  linkDone,
  prepareOff,
  setUp,
  sync,
  tearDown,
  upload,
  type Program,
} from "./groundProgram";
import {
  JUDGE_MS,
  SETTLE_MS,
  SHARP_START,
  judged,
  ratioOf,
  type Sharpness,
} from "./sharpness";

export type GroundGLHandle = {
  /** The ground is due a paint: at the next frame, once however often asked. */
  draw: () => void;
  /** Paint at once: for a caller inside a frame already, whose svg moves
   *  in this very frame -- booked, the ground would trail it by one. */
  drawNow: () => void;
};
/** How the GPU ground is doing: on its way, drawing, or given up. */
export type GroundGLState = "loading" | "ready" | "failed";

const RAD = Math.PI / 180;

//: What the page has learned of its GPU's pace (`sharpness.ts`). One GPU
//: draws every ground the page shows -- the entry globe's and the map's --
//: so one pace, carried from the door to the map.
let pace: Sharpness = SHARP_START;

/** The start of the frame under way, as its own callbacks are told it: the
 *  document's timeline stands still through a frame's work. A paint's
 *  verdict is counted from here, so what the frame spent before the paint
 *  counts against it, as it does against the frame. */
function frameStart(): number {
  const now = document.timeline?.currentTime;
  return typeof now === "number" ? now : performance.now();
}

export const GroundGL = forwardRef<
  GroundGLHandle,
  {
    planet: string;
    eye: Eye;
    radius: number;
    /** The SVG the ground lies under: its screen matrix places the eye. */
    svg: React.RefObject<SVGSVGElement | null>;
    /** The subsolar point as of this render (`Ground.sunOf`), for the
     *  light and the night; null without a clock, and then the light is
     *  the map's own north-west and there is no night. */
    sun: Geo | null;
    /** The season as of this render (`Ground.seasonOf`, D-334): the snow
     *  and the ice the ground wears, and the swing of its temperature. */
    season: Season;
    /** The weather's law and the real days since the epoch at the moment
     *  shown (D-335): the fragment turns the field to it. */
    weather: WeatherLaw;
    weatherDays: number;
    /** Whether the clouds are drawn over the terrain layer (the overlay). */
    clouds: boolean;
    /** What the ground is coloured by (D-331): the map's layer. */
    layer: Layer;
    /** Told when the ground starts drawing, and when it gives up. */
    onState: (state: GroundGLState) => void;
  }
>(function GroundGL(
  { planet, eye, radius, svg, sun, season, weather, weatherDays, clouds, layer, onState },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const probeRef = useRef<HTMLSpanElement | null>(null);
  const programRef = useRef<Program | null>(null);
  const terrain = useTerrain(planet);
  const passport = terrain?.raster ?? null;
  //: Which planet's textures are up, which program is linked, and which
  //: life of the context: a lost and restored context is a new life, and
  //: everything is rebuilt for it.
  //: `ready` a new object at every set of textures, so the whole picture
  //: put in the quick copy's place is drawn.
  const [ready, setReady] = useState<{ planet: string; whole: boolean } | null>(null);
  const [linked, setLinked] = useState<Program | null>(null);
  const [broken, setBroken] = useState(false);
  const [life, setLife] = useState(0);
  const tell = useRef(onState);
  tell.current = onState;
  //: The paint booked for the next frame, and the watch that follows the
  //: paints: the verdicts on those made in motion, each the fence it left in
  //: the GPU's queue and the start of the frame it was made in; and whether
  //: the last paint fell short of the full scale, with when -- the still
  //: paint it is owed.
  const booked = useRef(0);
  const watching = useRef(0);
  const pending = useRef<{ fence: WebGLSync; at: number }[]>([]);
  const owed = useRef({ short: false, at: 0 });
  //: Everything the watch holds of a context, let go with it: a fence of a
  //: lost context asked of the restored one is an error that reads as a
  //: late paint, and a still paint owed to it would be asked for every
  //: frame of a page that never gets it back.
  const forget = useCallback((gl: WebGL2RenderingContext | null) => {
    for (const { fence } of pending.current) if (gl && !gl.isContextLost()) gl.deleteSync(fence);
    pending.current = [];
    owed.current = { short: false, at: 0 };
  }, []);

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

  //: The context: once per life of the canvas, given back on unmount. The
  //: program's link is waited for a frame at a time, never on the page's
  //: thread (`groundProgram.linkDone`).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let waiting = 0;
    const failed = (why: unknown) => {
      console.warn("ground shader:", why);
      programRef.current = null;
      setBroken(true);
    };
    try {
      const program = setUp(canvas);
      programRef.current = program;
      if (!program) setBroken(true);
      else {
        const poll = () => {
          waiting = 0;
          if (programRef.current !== program) return;
          try {
            if (linkDone(program)) setLinked(program);
            else waiting = requestAnimationFrame(poll);
          } catch (why) {
            failed(why);
          }
        };
        poll();
      }
    } catch (why) {
      failed(why);
    }
    const lost = (event: Event) => {
      //: Saying so keeps the browser willing to give it back.
      event.preventDefault();
      programRef.current = null;
      forget(null);
      setReady(null);
      setLinked(null);
    };
    const restored = () => setLife((n) => n + 1);
    canvas.addEventListener("webglcontextlost", lost);
    canvas.addEventListener("webglcontextrestored", restored);
    return () => {
      cancelAnimationFrame(waiting);
      canvas.removeEventListener("webglcontextlost", lost);
      canvas.removeEventListener("webglcontextrestored", restored);
      forget(programRef.current?.gl ?? null);
      tearDown(programRef.current, canvas);
      programRef.current = null;
    };
  }, [life, forget]);
  //: The textures: once per planet per life of the context. They go up
  //: while the program links -- they are the context's, not the program's --
  //: and the rasters are made ready off the page's thread on the way.
  //:
  //: In two steps (2026-09-18): the picture's quick copy first, a sixteenth
  //: of the bytes (`rasters.previewOf`) -- the real planet a moment after
  //: the passport, soft up close -- and the whole picture in its place when
  //: it comes. Until the first of them the map shows no ground of its own
  //: (`Planet`): the old vector ground stood there for seconds, another
  //: planet than the one that then appeared.
  useEffect(() => {
    const program = programRef.current;
    if (!program || !passport) return;
    const held = program.textures.get(planet);
    if (held?.whole) {
      setReady({ planet, whole: true });
      return;
    }
    let live = true;
    /** Up with a set of textures, in the place of whatever this planet had;
     *  never the copy in the place of the whole. Whether they went up. */
    const put = (prepared: Prepared, whole: boolean): boolean => {
      const now = programRef.current;
      if (!live || !now) return false;
      const was = now.textures.get(planet);
      if (was?.whole && !whole) return false;
      //: One planet's textures at a time: the last planet's textures are given
      //: back before the next is uploaded. Wide as the atlas is now, four
      //: planets kept would be two hundred megabytes on a phone's GPU.
      for (const [other, them] of now.textures) {
        if (other !== planet) {
          drop(now.gl, them);
          now.textures.delete(other);
        }
      }
      now.textures.set(planet, upload(now.gl, prepared, whole));
      if (was) drop(now.gl, was);
      setReady({ planet, whole });
      return true;
    };
    //: The copy is only worth its round trip while nothing is up and the
    //: whole picture has not come already -- back on a planet shown before,
    //: its rasters are in the page and only the worker's pass is left.
    const small = held || heldRasters(planet) ? null : previewPassport(passport);
    //: A chain whose ground has gone -- another planet, the map closed --
    //: stops where it stands: the worker is one and works in turn, and a
    //: whole picture made ready for nobody holds up the next planet's copy.
    const gone = new Error("the ground has gone");
    const still = <T,>(value: T): T => {
      if (!live) throw gone;
      return value;
    };
    const quick: Promise<unknown> = small
      ? previewOf(planet, passport).then(
          (rasters) => {
            if (!live) return;
            prepareOff(small, rasters)
              .then((prepared) => put(prepared, false))
              .catch((why) => console.warn(`preview of ${planet}:`, why));
          },
          (why) => console.warn(`preview of ${planet}:`, why),
        )
      : Promise.resolve();
    //: The whole picture is asked for once the copy's bytes are in, or the
    //: copy has failed -- not beside it: the two would share the line, and
    //: the copy would come no sooner than the rest.
    quick
      .then(() => rastersOf(still(planet)))
      .then((rasters) => prepareOff(passport, still(rasters)))
      .then((prepared) => put(prepared, true))
      .catch((why) => {
        if (why === gone) return;
        console.warn(`rasters of ${planet}:`, why);
        //: A copy already drawn is kept: it is the real planet, only soft.
        if (live && !programRef.current?.textures.has(planet)) setBroken(true);
      });
    return () => {
      live = false;
    };
  }, [planet, passport, life]);
  //: Drawing only once both are there: told ready over an unlinked program,
  //: the map would put the svg's land away over an empty canvas. One word
  //: for the map, said in one place.
  const drawing = !broken && ready?.planet === planet && linked !== null;
  useEffect(() => {
    tell.current(broken ? "failed" : drawing ? "ready" : "loading");
  }, [broken, drawing]);

  //: The mountain line of the sketch: the hypsometric lightening starts
  //: where the SVG ground greys and the biome turns alpine, not at a line
  //: of the shader's own.
  const highFrom = terrain?.mountain_level ?? 1;
  //: The drying law off the book of constants, one object per book, so
  //: `sync` sees the same law until the book changes.
  const book = useBook();
  const law = useMemo(() => dryLaw(book?.constants), [book]);
  //: The vault's word for each biome's grain (biome.grain), one object per
  //: book like the law, so `sync` sees the same table until the book changes.
  const grains = useMemo(
    () => (book?.constants?.["biome.grain"] as Record<string, unknown> | undefined) ?? null,
    [book],
  );
  const state = useRef({ eye, radius, palette, planet, highFrom, sun, season, weather, weatherDays, clouds, layer, law, grains });
  state.current = { eye, radius, palette, planet, highFrom, sun, season, weather, weatherDays, clouds, layer, law, grains };

  const tick = useRef<(t: number) => void>(() => {});
  const watch = useCallback(() => {
    if (!watching.current) watching.current = requestAnimationFrame((t) => tick.current(t));
  }, []);

  /** One paint of the whole canvas: in motion as fine as the pace allows,
   *  still at the full scale. `frame` is the start of the frame it is made
   *  in, which its verdict is counted from. Whether it painted at all. */
  const paint = useCallback((moving: boolean, frame: number): boolean => {
    const program = programRef.current;
    const canvas = canvasRef.current;
    const svgEl = svg.current;
    const { eye, radius, palette, planet, highFrom, sun, season, weather, weatherDays, clouds, layer, law, grains } = state.current;
    if (!program || !canvas || !svgEl || !palette) return false;
    if (!sync(program, planet, palette, highFrom, law, grains)) return false;
    const { gl, at } = program;
    const dpr = window.devicePixelRatio || 1;
    const ratio = ratioOf(dpr, pace, moving);
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
    //: The canvas's own pixels, by the scale of this paint: the stylesheet
    //: stretches them over the svg's box, so a coarser paint is the same
    //: ground at fewer pixels, and changing the scale moves nothing.
    const width = Math.max(1, Math.round(over.width * ratio));
    const height = Math.max(1, Math.round(over.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
    }
    const ctm = svgEl.getScreenCTM();
    if (!ctm || !(ctm.a > 0)) return false;
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(at("u_size"), width, height);
    //: The canvas stands exactly over the svg, so the svg's box is its own.
    gl.uniform2f(at("u_origin"), (ctm.e - over.left) * ratio, (ctm.f - over.top) * ratio);
    const units = 1 / (ctm.a * ratio);
    gl.uniform1f(at("u_units"), units);
    //: And a pixel of the screen, which the frame's scale is read by --
    //: the same number at every scale of the canvas.
    gl.uniform1f(at("u_screen_units"), 1 / (ctm.a * dpr));
    gl.uniform1f(at("u_radius"), radius);
    gl.uniform2f(at("u_eye"), eye.lat * RAD, eye.lon * RAD);
    //: The sun, for the light and the night (owner, 2026-09-12): the
    //: subsolar point as a direction on the ball, and whether there is one.
    gl.uniform3fv(at("u_sun"), sun ? sunVector(sun) : [0, 0, 1]);
    gl.uniform1f(at("u_sunlit"), sun ? 1 : 0);
    //: The season (D-334): the swing of the mean temperature at the pole as
    //: of now -- the fragment scales it by the sine of its own latitude --
    //: and the lines the snow and the ice lie below.
    gl.uniform1f(at("u_season_c"), seasonC(season, 90));
    gl.uniform3f(at("u_snow"), season.snowC, season.bandC, season.iceC);
    gl.uniform2f(at("u_snow_dry"), season.dryRain, season.dryKeep);
    //: The weather (D-335): the lattice's scale and where the wind has
    //: carried it, which slice of time the field is in, the gates from
    //: cover to cloud and to rain, and whether the clouds are shown.
    const moment = weatherMoment(weather, weatherDays);
    gl.uniform1f(at("u_wx_cell"), weather.cellDeg);
    gl.uniform1f(at("u_wx_wind"), weather.windDeg);
    gl.uniform2f(at("u_wx_time"), moment.days, moment.changeDays);
    gl.uniform3f(at("u_wx_belts"), weather.tradeLat, weather.westerlyLat, weather.beltEdge);
    gl.uniform1f(at("u_wx_spin"), weather.spin);
    gl.uniform4f(at("u_wx_gates"), weather.cloudFrom, weather.cloudFull, weather.rainFrom, weather.rainFull);
    gl.uniform1f(at("u_wx_bias"), weather.bias);
    gl.uniform1f(at("u_wx_gain"), weather.gain);
    gl.uniform1f(at("u_clouds"), clouds ? 1 : 0);
    gl.uniform1i(at("u_layer"), Math.max(0, LAYERS.indexOf(layer)));
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
    //: A paint in motion leaves a fence behind it, and is judged a frame
    //: later by whether the GPU has passed it (`sharpness.judged`). The
    //: still paint is not judged: it is allowed to take its time, once.
    if (moving) {
      const fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
      if (fence) {
        //: Sent now, not at the end of the task, or the time until the
        //: verdict would be spent partly waiting to be sent.
        gl.flush();
        pending.current.push({ fence, at: frame });
      }
    }
    owed.current = { short: ratio < ratioOf(dpr, pace, false), at: frame };
    if (pending.current.length || owed.current.short) watch();
    return true;
  }, [svg, watch]);

  //: The watch, once a frame while there is something to judge or a still
  //: paint owed, and not at all otherwise.
  tick.current = (t: number) => {
    watching.current = 0;
    const program = programRef.current;
    const queue = pending.current;
    while (queue.length && t - queue[0].at >= JUDGE_MS) {
      const { fence } = queue.shift()!;
      //: A lost context has no fences to ask about, and its paints were
      //: never slow: they are forgotten, not judged.
      if (!program || program.gl.isContextLost()) continue;
      const { gl } = program;
      pace = judged(pace, gl.getSyncParameter(fence, gl.SYNC_STATUS) === gl.SIGNALED);
      gl.deleteSync(fence);
    }
    const debt = owed.current;
    //: The still paint, once nothing has asked for one for a while. One
    //: that cannot be made now -- no textures yet, no screen matrix -- is
    //: let go: the next paint that can be made will owe it again.
    if (debt.short && !booked.current && t - debt.at >= SETTLE_MS && !paint(false, t)) {
      owed.current = { short: false, at: t };
    }
    if (queue.length || owed.current.short) watch();
  };

  const draw = useCallback(() => {
    if (booked.current) return;
    booked.current = requestAnimationFrame((t) => {
      booked.current = 0;
      paint(true, t);
    });
  }, [paint]);
  const drawNow = useCallback(() => {
    if (booked.current) cancelAnimationFrame(booked.current);
    booked.current = 0;
    paint(true, frameStart());
  }, [paint]);
  useImperativeHandle(ref, () => ({ draw, drawNow }), [draw, drawNow]);
  //: Nothing booked or watched outlives the ground; the fences go with the
  //: context (`forget`, in the context's own cleanup).
  useEffect(
    () => () => {
      cancelAnimationFrame(booked.current);
      cancelAnimationFrame(watching.current);
      booked.current = 0;
      watching.current = 0;
    },
    [],
  );

  //: What React knows of -- the eye, the planet, the palette, the rasters'
  //: arrival, the layer, the sun's place -- redraws; the camera's frames
  //: redraw through the handle. The sun as a key and not as the object:
  //: `sunOf` makes the point afresh on every render, quantised to a quarter
  //: of a degree, so a render that moved nothing draws nothing.
  const sunKey = sun ? `${sun.lat},${sun.lon}` : "";
  const seasonKey = [season.turns, season.swingC, season.snowC, season.bandC, season.iceC, season.dryRain, season.dryKeep].join(",");
  //: The weather redraws by its own moment, to a thousandth of a slice (a
  //: couple of minutes of Terra's day, not every render), and by the law
  //: itself -- one object per book and radius (`useClimateView`).
  const weatherKey = [(weatherDays / weather.changeDays).toFixed(3), clouds].join(",");
  useEffect(() => {
    draw();
  }, [draw, eye, radius, palette, drawing, ready, planet, highFrom, layer, sunKey, seasonKey, weather, weatherKey, law, grains]);
  //: The box: a resize of the pane is a resize of the canvas. Watched on the
  //: **svg**, because the canvas's own box is written by the draw above --
  //: watching it would be watching one's own hand, and the canvas would keep
  //: whatever size it was made with while the pane grew around it. Painted
  //: at once: the observer is told inside the frame that resized, and a
  //: paint booked from there would leave the old box on the screen a frame.
  useEffect(() => {
    const over = svg.current;
    if (!over) return;
    const watcher = new ResizeObserver(() => drawNow());
    watcher.observe(over);
    return () => watcher.disconnect();
  }, [drawNow, svg]);

  return (
    <>
      <canvas ref={canvasRef} className="ground-gl" aria-hidden="true" />
      {/* The probe the palette is read through: a hidden span the theme's
          colours are computed on, one expression at a time. */}
      <span ref={probeRef} className="ground-probe" aria-hidden="true" />
    </>
  );
});
