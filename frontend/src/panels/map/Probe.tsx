// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The reading under the cursor (D-331 addendum, owner 2026-09-12): on the
 * biomes layer the biome's word, on the relief the height, on the
 * temperature layer the mean temperature -- and on the rain and moisture
 * layers their numbers, the same way. A small slab beside the pointer,
 * read off the rasters the map already holds (`reading.ts`), nothing asked
 * of the server. On the terrain layer there is nothing to read out and the
 * slab stays away.
 *
 * Listens on the svg itself, outside React's render: the map's own pointer
 * handler is the camera's, and a state written on every move would
 * re-render the whole map at the pointer's rate. Only this slab re-renders,
 * once a frame at most.
 */

import { useEffect, useRef, useState, type RefObject } from "react";

import { useBook, useNames } from "../../actions";
import { t } from "../../locale";
import { geoUnder, type Eye } from "./globe";
import { useTerrain } from "./Ground";
import { readingAt } from "./reading";
import { useRasters } from "./rasters";
import { dryLaw, moistureOf, type Layer } from "./shade";
import { biomeWord } from "./words";

/** How far the slab stands from the pointer, so the pointer does not cover it. */
const OFFSET_PX = 14;

export function Probe({
  svg,
  eye,
  radius,
  planet,
  layer,
}: {
  svg: RefObject<SVGSVGElement | null>;
  eye: Eye;
  radius: number;
  planet: string;
  layer: Layer;
}) {
  const rasters = useRasters(planet);
  const passport = useTerrain(planet)?.raster ?? null;
  const book = useBook();
  const names = useNames();
  const [shown, setShown] = useState<{ x: number; y: number; text: string } | null>(null);
  //: What the listener reads at the moment of the move, without being
  //: re-attached on every render: the eye moves with every pan.
  const live = useRef({ eye, radius, rasters, passport, layer, book, names });
  live.current = { eye, radius, rasters, passport, layer, book, names };
  //: The element, not the ref: the svg is unmounted and mounted again when
  //: the scene empties and fills, and listeners left on the old one would
  //: hear nothing. Read at render, when the ref is already set.
  const el = svg.current;
  useEffect(() => {
    if (!el) return;
    let frame = 0;
    let last: PointerEvent | null = null;
    const read = () => {
      frame = 0;
      const e = last;
      const { eye, radius, rasters, passport, layer, book, names } = live.current;
      if (!e || !rasters || !passport || layer === "terrain") {
        setShown(null);
        return;
      }
      const ctm = el.getScreenCTM();
      const box = el.parentElement?.getBoundingClientRect();
      if (!ctm || !box) return;
      //: The pointer in the svg's own units, then on the ball.
      const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
      const at = geoUnder(eye, radius, { x: pt.x, y: pt.y });
      if (!at) {
        setShown(null);
        return;
      }
      const reading = readingAt(rasters, passport, at);
      let text: string | null = null;
      switch (layer) {
        case "biomes": {
          const id = reading.biome === null ? null : passport.biomes[reading.biome];
          text = id ? biomeWord(id, names, book) : null;
          break;
        }
        case "relief":
          text = t("ui-map-probe-height", { m: Math.round(reading.heightM) });
          break;
        case "temperature":
          text = t("ui-map-degrees", { c: Math.round(reading.temperatureC) });
          break;
        case "rain":
          text = t("ui-map-probe-rain", { percent: reading.rainPercent });
          break;
        case "moisture": {
          //: The same arithmetic the layer is drawn by (`shade.moistureOf`).
          const law = dryLaw(book?.constants);
          const beside = reading.riverM <= law.reachM ? 1 : 0;
          const share = moistureOf(
            law,
            reading.temperatureC,
            reading.rainPercent / 100,
            beside,
            passport.temperature_c.hot,
          );
          text = t("ui-map-probe-moisture", { percent: Math.round(share * 100) });
          break;
        }
        default:
          text = null;
      }
      setShown(
        text === null
          ? null
          : { x: e.clientX - box.left + OFFSET_PX, y: e.clientY - box.top + OFFSET_PX, text },
      );
    };
    const move = (e: PointerEvent) => {
      //: A pressed hand is a pan or a tap, and a slab under it is noise.
      if (e.buttons) return;
      last = e;
      if (!frame) frame = requestAnimationFrame(read);
    };
    const gone = () => {
      last = null;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      setShown(null);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerleave", gone);
    //: A press is a pan or a tap, and a slab under a moving hand is noise.
    el.addEventListener("pointerdown", gone);
    return () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerleave", gone);
      el.removeEventListener("pointerdown", gone);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [el]);
  //: The layer changed under a resting pointer: the old word would be a lie.
  useEffect(() => setShown(null), [layer]);
  if (!shown) return null;
  return (
    <div className="map-probe" style={{ left: shown.x, top: shown.y }} aria-hidden="true">
      {shown.text}
    </div>
  );
}
