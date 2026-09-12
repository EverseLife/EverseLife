// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The legend of a layer that is read rather than looked at (owner,
 * 2026-09-12): the biomes' colours by name, the climate's two ramps between
 * their ends, the soil's moisture between dry and wet. It stands in the
 * field's lower left corner, out of the way of the bar and the zoom, and
 * not beside the menu's item: a legend is read with the map, the menu is
 * shut once the layer is chosen. The terrain and the relief have none --
 * the one is the country as it is, the other reads by its light.
 *
 * The ramps are the shader's own stops (`shade.RAMPS`), written as a CSS
 * gradient: the bar in the corner and the ground under it are one table,
 * and a stop moved in one place moves in both.
 */

import { useMemo } from "react";

import { useBook, useNames } from "../../actions";
import { t } from "../../locale";
import { useTerrain } from "./Ground";
import { useRasters } from "./rasters";
import { NO_BIOME, RAMPS, cssRamp, type Layer } from "./shade";
import { wordOf } from "./Switcher";
import { biomeWord } from "./words";

/** A ramp between two words. */
function Ramp({ title, stops, low, high }: { title: string; stops: keyof typeof RAMPS; low: string; high: string }) {
  return (
    <aside className="map-legend" aria-label={title}>
      <span className="legend-title">{title}</span>
      <div className="legend-ramp" style={{ background: cssRamp(RAMPS[stops]) }} />
      <div className="legend-ends">
        <span>{low}</span>
        <span>{high}</span>
      </div>
    </aside>
  );
}

export function Legend({ layer, planet }: { layer: Layer; planet: string | null }) {
  const passport = useTerrain(planet)?.raster ?? null;
  const book = useBook();
  const names = useNames();
  //: The biomes this planet has, off its own raster (D-225): the passport
  //: lists the registry's sixteen for every planet, and a legend of taiga
  //: and rainforest on Pyroxis would name what is not there. One pass over
  //: the raster a planet, kept.
  const rasters = useRasters(planet);
  const present = useMemo(() => {
    const seen = new Set<number>();
    if (rasters) for (const code of rasters.biome) if (code !== NO_BIOME) seen.add(code);
    return seen;
  }, [rasters]);
  if (!passport) return null;
  const title = wordOf(layer);
  switch (layer) {
    case "temperature": {
      //: The planet's own ends (D-329): the ramp runs between them.
      const { cold, hot } = passport.temperature_c;
      return (
        <Ramp
          title={title}
          stops="temperature"
          low={t("ui-map-degrees", { c: Math.round(cold) })}
          high={t("ui-map-degrees", { c: Math.round(hot) })}
        />
      );
    }
    case "rain":
      //: The rain raster is the field's own share, nought to one over the
      //: vault's conventional `site.rain_range` (D-126) -- a scale, not a
      //: measure in millimetres: the ends are words.
      return <Ramp title={title} stops="rain" low={t("ui-map-legend-rain-less")} high={t("ui-map-legend-rain-more")} />;
    case "moisture":
      return <Ramp title={title} stops="moisture" low={t("ui-map-legend-moisture-fast")} high={t("ui-map-legend-moisture-slow")} />;
    case "weather":
      //: The hour's rain (D-335), nought to a downpour; the clouds ride on
      //: the layer as a haze and need no bar.
      return <Ramp title={title} stops="weather" low={t("ui-map-legend-weather-dry")} high={t("ui-map-legend-weather-heavy")} />;
    case "biomes": {
      const nameOf = (id: string) => biomeWord(id, names, book);
      return (
        <aside className="map-legend" aria-label={title}>
          <span className="legend-title">{title}</span>
          <ul className="legend-swatches">
            {passport.biomes.filter((_, code) => present.has(code)).map((id) => (
              <li key={id}>
                <i style={{ background: `var(--biome-${id}, magenta)` }} aria-hidden="true" />
                {nameOf(id)}
              </li>
            ))}
          </ul>
        </aside>
      );
    }
    default:
      return null;
  }
}
