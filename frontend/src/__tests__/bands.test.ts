// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The bands of scale (D-319, wave 4): what a height shows, where it hands over. */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import {
  CITY_OF_GLOBE,
  CITY_SCALE,
  GLOBE_FILL,
  OPEN_REACH,
  SKY_BOUNDS,
  SURFACE_NEAREST,
  boundsOf,
  DESCENT_STEPS,
  ABOVE_FLOOR,
  cityOpen,
  descentOf,
  globeScale,
  groundOf,
  groundReach,
  farOf,
  cityRadius,
  cityPixels,
  cityLabelEm,
  scaleAt,
  CITY_LABEL_PX,
  CITY_R_MIN,
  citySeen,
  CITY_R_MAX,
  KM_PER_NODE,
  leavesSurface,
  mapBounds,
  MAP_FURTHEST,
  openScale,
  tilted,
  planetUnder,
  reachesSurface,
  surfaceBounds,
  surfaceFloor,
} from "../panels/map/bands";
import { H, SPHERE_R, W } from "../panels/map/model";
import { drawnAt } from "../panels/map/model";
import {
  GAP_RADII,
  NODE_R,
  NODE_R_MAX,
  NODE_R_MIN,
  nodeRadius,
} from "../panels/map/bands";
import { edgesOf, visibleOf } from "../panels/map/useScene";
import type { Scouting } from "../api";
import { dotOn } from "../panels/map/useWalker";
import { nodeGlyph } from "../marks";
import { LAST_LAT, between } from "../panels/map/globe";
import { factsOf } from "../panels/map/useBands";
import { ZOOM_PACE, createCamera, zoomStep } from "../panels/map/camera";

//: The vault's `map.approach_km` as of D-319.
const APPROACH_KM = 50000;
const SURFACE = surfaceBounds(APPROACH_KM);
import {
  STUB_M,
  UNITS_PER_METRE,
  ahead,
  arc,
  project,
  radiusUnits,
} from "../panels/map/globe";
import { ZOOM_STEPS, notchOf, scaleOf } from "../panels/map/Switcher";
import { delegateAmong, settlementsOf } from "../panels/map/model";

const node = (over: Partial<MapNode>): MapNode =>
  ({
    key: "x",
    name: "Узел",
    layer: "city",
    parent: null,
    port: false,
    planet: "terra",
    orbit: null,
    ...over,
  }) as MapNode;

describe("the zoom slider", () => {
  it("maps a scale to a notch on the log of the bounds, and back", () => {
    const bounds = { nearest: 4, furthest: 1 / 64 };
    expect(notchOf(bounds.furthest, bounds)).toBe(0);
    expect(notchOf(bounds.nearest, bounds)).toBe(ZOOM_STEPS);
    expect(notchOf(0.25, bounds)).toBe(500);
    expect(scaleOf(500, bounds)).toBeCloseTo(0.25, 9);
    expect(scaleOf(notchOf(1, bounds), bounds)).toBeCloseTo(1, 6);
    //: Past the bounds the notch holds at the end; empty bounds give nothing.
    expect(notchOf(100, bounds)).toBe(ZOOM_STEPS);
    expect(notchOf(1, { nearest: 1, furthest: 1 })).toBe(0);
  });
});

describe("the bands", () => {
  it("stop the map tab at the disk: no floor, no descent, no sky beyond", () => {
    //: The console keeps the approach floor and the sky under it; the map
    //: tab's surface ends a little past the disk filling the frame.
    const small = radiusUnits(319);
    const globe = globeScale(small);
    const map = mapBounds(globe, small);
    expect(map.furthest).toBeCloseTo(globe * MAP_FURTHEST, 12);
    expect(map.furthest).toBeGreaterThan(SURFACE.furthest);
    //: At the map's own floor nothing is a fact: the frame is clamped there
    //: by the hand's bounds, and the sky never opens.
    const frame = {
      x: -W / (2 * map.furthest),
      y: -H / (2 * map.furthest),
      scale: map.furthest,
    };
    const onMap = factsOf(
      frame,
      { ...map, globe, radius: small, console: false },
      [],
    );
    expect(onMap.floor).toBe(false);
    expect(onMap.descent).toBe(0);
    //: The same frame on the console, at the console's floor, is the sky's.
    const deep = { ...frame, scale: SURFACE.furthest * 0.9 };
    const onConsole = factsOf(
      deep,
      { ...SURFACE, globe, radius: small, console: true },
      [],
    );
    expect(onConsole.floor).toBe(true);
    expect(onConsole.descent).toBe(1);
    //: Flat scenes keep the old floor: nothing to fall through either way.
    expect(mapBounds(globeScale(null), null).furthest).toBe(
      surfaceBounds(NaN).furthest,
    );
  });

  it("turn the eye the shortest way round and ease it at both ends", () => {
    //: Across the seam: from 170 east to 170 west is twenty degrees east,
    //: not three hundred and forty west.
    expect(between({ lat: 0, lon: 170 }, { lat: 10, lon: -170 }, 1)).toEqual({
      lat: 10,
      lon: 190,
    });
    expect(
      between({ lat: 0, lon: -170 }, { lat: 0, lon: 170 }, 0.5).lon,
    ).toBeCloseTo(-180, 9);
    //: Eased: halfway in time is halfway in angle, a tenth in time is less.
    expect(
      between({ lat: 0, lon: 0 }, { lat: 40, lon: 0 }, 0.5).lat,
    ).toBeCloseTo(20, 9);
    expect(
      between({ lat: 0, lon: 0 }, { lat: 40, lon: 0 }, 0.1).lat,
    ).toBeLessThan(4);
    expect(between({ lat: 0, lon: 0 }, { lat: 40, lon: 0 }, 0)).toEqual({
      lat: 0,
      lon: 0,
    });
  });

  it("draw the ground by the planet's cell, finer the nearer, flat only inside a cell", () => {
    //: A planet a twentieth of Earth (D-322): the disk fills the frame at
    //: the globe scale, and the ground is cells there -- on a scale pinned
    //: to Earth's size it went flat before the coast could be seen.
    const small = radiusUnits(319);
    const disk = globeScale(small);
    expect(groundOf(disk, small)).toEqual({ unit: 1, shown: true });
    //: Nearer, the drawn cell halves while fewer than two dozen fit, down
    //: to an eighth; the frame goes flat only inside a cell and a half of
    //: the finest reading.
    const cell = small * 2 * (Math.PI / 180);
    expect(groundOf(W / (12 * cell), small).unit).toBe(1 / 2);
    expect(groundOf(W / (3 * cell), small).unit).toBe(1 / 8);
    expect(groundOf(W / (0.5 * cell), small)).toEqual({
      unit: 1 / 32,
      shown: true,
    });
    //: Inside a cell the ground is still cut: the streets see the shore.
    expect(groundOf(W / (0.02 * cell), small).shown).toBe(true);
    //: Earth's size draws its cells at the same share of the disk.
    const earth = radiusUnits(6371);
    expect(groundOf(globeScale(earth), earth)).toEqual({
      unit: 1,
      shown: true,
    });
    expect(groundOf(1, null)).toEqual({ unit: 1, shown: false });
    //: The ground laid at a drawn cell reaches as far as the widest frame
    //: that cell serves: half of it, about the eye; a whole cell, no limit.
    expect(groundReach(1 / 8, small)).toBeCloseTo(24 * cell * (1 / 8), 6);
    expect(groundReach(1, small)).toBeUndefined();
  });

  it("draws a closed city larger the farther out, and fades the small ones", () => {
    //: At the closing nothing is far; a doubling of the span is two half-octaves.
    expect(farOf(CITY_SCALE)).toBe(0);
    expect(farOf(CITY_SCALE / 2)).toBe(2);
    expect(farOf(CITY_SCALE / 8)).toBe(6);
    expect(farOf(1)).toBe(0);
    //: The radius **in pixels** is the node count at the closing and grows
    //: with the distance, to a ceiling; a city smaller than the floor is
    //: drawn at the floor.
    expect(cityPixels(20, 0)).toBe(20);
    expect(cityPixels(3, 0)).toBe(CITY_R_MIN);
    expect(cityPixels(20, 2)).toBeCloseTo(20 * 1.6, 9);
    expect(cityPixels(40, 40)).toBe(CITY_R_MAX);
    //: And what the drawing takes is map units, which are pixels only at
    //: scale one: divided by the scale of the band, the circle holds its
    //: size on the glass however far out the frame goes (D-323 addendum,
    //: 2026-09-08). Drawn in units it shrank to a speck.
    expect(cityRadius(20, 0)).toBeCloseTo(20 / CITY_SCALE, 9);
    expect(cityRadius(20, 2) * scaleAt(2)).toBeCloseTo(cityPixels(20, 2), 9);
    expect(cityRadius(20, 8) * scaleAt(8)).toBeCloseTo(cityPixels(20, 8), 9);
    //: The name goes with it: the same pixels at every distance.
    expect(cityLabelEm(0) * scaleAt(0)).toBeCloseTo(CITY_LABEL_PX, 9);
    expect(cityLabelEm(10) * scaleAt(10)).toBeCloseTo(CITY_LABEL_PX, 9);
    //: A band's scale is the closing halved every octave, and `far` counts
    //: half-octaves.
    expect(scaleAt(0)).toBe(CITY_SCALE);
    expect(scaleAt(2)).toBeCloseTo(CITY_SCALE / 2, 12);
    //: A hamlet of three fades once the frame is wider than three of its
    //: allowances; the capital is seen from the whole disk.
    const closingKm = W / CITY_SCALE / UNITS_PER_METRE / 1000;
    const farEnough = 2 * Math.ceil(Math.log2((4 * KM_PER_NODE) / closingKm));
    expect(citySeen(3, 0)).toBe(true);
    expect(citySeen(3, farEnough)).toBe(false);
    expect(citySeen(13, farEnough)).toBe(true);
  });

  it("open a city at the city scale and close it a hair farther out", () => {
    expect(cityOpen(CITY_SCALE)).toBe(true);
    expect(cityOpen(1)).toBe(true);
    expect(cityOpen(CITY_SCALE - 1e-6)).toBe(false);
  });

  it("hand the surface to the sky only at the floor of the surface", () => {
    expect(leavesSurface(SURFACE.furthest, SURFACE.furthest)).toBe(true);
    expect(leavesSurface(SURFACE.furthest * 2, SURFACE.furthest)).toBe(false);
    //: The sky's own floor is far above the surface's: a hand zooming out
    //: through the sky must not fall back onto a planet.
    expect(leavesSurface(SKY_BOUNDS.furthest, SURFACE.furthest)).toBe(false);
  });

  it("read the floor from the vault's height: a right-angled view across the frame", () => {
    //: From `map.approach_km` up, the frame spans twice the height.
    expect(surfaceFloor(APPROACH_KM)).toBeCloseTo(
      H / (2 * APPROACH_KM * 1000 * UNITS_PER_METRE),
      12,
    );
    expect(surfaceFloor(APPROACH_KM)).toBeLessThan(
      globeScale(radiusUnits(6371)),
    );
    //: No book, no height: the floor sits under the disk, not on it.
    expect(surfaceFloor(0)).toBeLessThan(globeScale(radiusUnits(6371 * 1.2)));
    expect(surfaceFloor(Number.NaN)).toBe(surfaceFloor(0));
  });

  it("ask the sky to open a planet only at its ceiling", () => {
    expect(reachesSurface(SKY_BOUNDS.nearest)).toBe(true);
    expect(reachesSurface(SKY_BOUNDS.nearest / 2)).toBe(false);
  });

  it("open the surface as a globe of the planet's own size, not falling back into the sky", () => {
    const terra = radiusUnits(6371);
    const scale = globeScale(terra);
    //: The disk is a little taller than the frame, whatever the planet.
    expect(2 * terra * scale).toBeCloseTo(H * GLOBE_FILL, 6);
    expect(2 * radiusUnits(1000) * globeScale(radiusUnits(1000))).toBeCloseTo(
      H * GLOBE_FILL,
      6,
    );
    expect(scale).toBeGreaterThan(SURFACE.furthest);
    expect(leavesSurface(scale, SURFACE.furthest)).toBe(false);
    expect(cityOpen(scale)).toBe(false);
    //: No radius, no globe: the streets, as before.
    expect(globeScale(null)).toBe(1);
  });

  it("give each band its own bounds, the surface reaching farthest out", () => {
    expect(boundsOf("sky", SURFACE)).toBe(SKY_BOUNDS);
    expect(boundsOf("surface", SURFACE)).toBe(SURFACE);
    expect(boundsOf("inside", SURFACE).furthest).toBeGreaterThan(
      SURFACE.furthest,
    );
    expect(SURFACE.nearest).toBe(SURFACE_NEAREST);
    expect(SURFACE.furthest).toBeLessThan(CITY_SCALE);
  });
});

describe("planetUnder", () => {
  const spheres = [
    { key: "terra", planet: "terra", at: { x: 0, y: 0 } },
    { key: "aurora", planet: "aurora", at: { x: 200, y: 0 } },
  ];

  it("names the planet nearest the middle, within reach", () => {
    expect(planetUnder({ x: 10, y: 5 }, spheres)?.planet).toBe("terra");
    expect(planetUnder({ x: 190, y: 0 }, spheres)?.planet).toBe("aurora");
  });

  it("names nothing when the middle is over empty sky", () => {
    expect(planetUnder({ x: 100, y: 0 }, spheres)).toBe(null);
    expect(planetUnder({ x: 0, y: OPEN_REACH + 1 }, spheres)).toBe(null);
    expect(planetUnder({ x: 0, y: 0 }, [])).toBe(null);
  });
});

describe("delegateAmong", () => {
  const byKey: Record<string, MapNode> = {
    terra: node({ key: "terra", layer: "space" }),
    city: node({ key: "city", layer: "planet", parent: "terra" }),
    //: The `"city"` layer is put on by the client (`geo.withCityScene`)
    //: before the scene reads it: a member of a city is a surface node whose
    //: parent stands on the surface too. The fixture describes the world as
    //: it already is by then.
    gate: node({ key: "gate", layer: "city", parent: "city" }),
    floor: node({ key: "floor", layer: "location", parent: "gate" }),
  };
  //: The map knows a city by the surface nodes hanging under it, and by
  //: nothing else: a city has no layer of its own. Without this set a closed
  //: city would not answer for its own, and roads would be drawn to nodes the
  //: map no longer shows.
  const towns = settlementsOf(Object.values(byKey));

  it("stands a node for itself when its layer is drawn, else for its nearest drawn parent", () => {
    expect(delegateAmong(byKey, "gate", ["city", "planet"], towns)).toBe(
      "gate",
    );
    expect(delegateAmong(byKey, "gate", ["planet"], towns)).toBe("city");
    expect(delegateAmong(byKey, "floor", ["city", "planet"], towns)).toBe(
      "gate",
    );
    expect(delegateAmong(byKey, "floor", ["space"], towns)).toBe("terra");
    //: A planet is never a city: it is in the sky, not on the surface --
    //: else a closed city would climb past it and the whole world would
    //: answer for it.
    expect(towns).toEqual(new Set(["city"]));
  });

  it("answers null when nothing above the node is drawn, or the node is unknown", () => {
    expect(delegateAmong(byKey, "terra", ["planet"], towns)).toBe(null);
    expect(delegateAmong(byKey, "nowhere", ["planet"], towns)).toBe(null);
  });
});

describe("a stub into the fog", () => {
  const R = radiusUnits(6371);
  const home = { lat: 41, lon: 24 };

  it("sets out on its bearing: north is up the frame, east is to the right", () => {
    const eye = home;
    const north = project(eye, R, ahead(R, home, 0, STUB_M));
    const east = project(eye, R, ahead(R, home, 90, STUB_M));
    expect(north.y).toBeLessThan(0);
    expect(Math.abs(north.x)).toBeLessThan(1e-6);
    expect(east.x).toBeGreaterThan(0);
    expect(Math.abs(east.y)).toBeLessThan(1e-3);
  });

  it("is as long as the stub and no longer, at any bearing", () => {
    for (const bearing of [0, 45, 135, 200, 300]) {
      const end = project(home, R, ahead(R, home, bearing, STUB_M));
      expect(Math.hypot(end.x, end.y)).toBeCloseTo(STUB_M * 5, 3);
    }
  });

  it("is drawn as an arc from the seen end, like any edge", () => {
    const run = arc(home, R, home, ahead(R, home, 270, STUB_M));
    expect(run).not.toBe(null);
    expect(run![0].x).toBeCloseTo(0);
    expect(run![0].y).toBeCloseTo(0);
    expect(run![run!.length - 1].x).toBeLessThan(0);
  });
});

describe("the scene", () => {
  const byKey: Record<string, MapNode> = {
    terra: node({ key: "terra", layer: "space" }),
    aurora: node({ key: "aurora", layer: "space", planet: "aurora" }),
    city: node({ key: "city", layer: "planet", parent: "terra" }),
    gate: node({ key: "gate", layer: "city", parent: "city" }),
    field: node({ key: "field", layer: "planet", parent: "terra" }),
    floor: node({ key: "floor", layer: "location", parent: "gate" }),
    cellar: node({ key: "cellar", layer: "location", parent: "field" }),
    outpost: node({
      key: "outpost",
      layer: "planet",
      parent: "aurora",
      planet: "aurora",
    }),
    parked: node({
      key: "parked",
      layer: "space",
      parent: "terra",
      aboard: true,
    } as Partial<MapNode>),
    flying: node({
      key: "flying",
      layer: "space",
      parent: "terra",
      aboard: true,
      flight: { to: "aurora", started_at: "", arrives_at: "" },
    } as Partial<MapNode>),
  };
  const nodes = Object.values(byKey);
  const edges = [
    { a: "gate", b: "field", surface: "road", seconds: 60 },
    { a: "city", b: "field", surface: "wild", seconds: 300 },
    { a: "gate", b: "floor", surface: "paved", seconds: 5 },
  ];
  const repr = (layers: string[]) => (key: string) =>
    delegateAmong(byKey, key, layers, settlementsOf(Object.values(byKey)));

  it("keeps the node underfoot whatever layer it is drawn on", () => {
    //: With the cities closed only `planet` is drawn, and a member of a city
    //: wears the `city` layer (`geo.withCityScene`) -- so somebody standing in
    //: a city was filtered out by the layer before "this is me" was ever
    //: asked. The map from afar stayed empty and one lost oneself on it.
    expect(
      visibleOf(nodes, ["planet"], "gate", "terra", "gate").map((n) => n.key),
    ).toEqual(["city", "gate"]);
    //: Inside a house the same: somebody else's floor is not drawn, the one
    //: you stand on is.
    expect(
      visibleOf(nodes, ["location"], "gate", "terra", "cellar").map(
        (n) => n.key,
      ),
    ).toEqual(["floor", "cellar"]);
  });

  it("stands a city on its bioprinter, and everything else where it is", () => {
    //: From afar a city is its bioprinter's point (D-319): not the city's own
    //: coordinates, which are the anchor the layout hung it on, but the node
    //: it grew from. Asked in one place, so the roads, the labels and the
    //: camera's aim all follow the circle -- otherwise the circle would move
    //: to the printer and the lines would still meet at the old point.
    const map: Record<string, MapNode> = {
      city: node({
        key: "city",
        layer: "planet",
        core: "gate",
        place: { lat: 41, lon: 24 },
      }),
      gate: node({
        key: "gate",
        layer: "city",
        parent: "city",
        place: { lat: 40.9, lon: 24.1 },
      }),
      field: node({
        key: "field",
        layer: "planet",
        place: { lat: 10, lon: 10 },
      }),
    };
    const laid = Object.fromEntries(
      drawnAt(map, Object.values(map)).map((n) => [n.key, n.place]),
    );
    expect(laid.city).toEqual(map.gate.place);
    expect(laid.gate).toEqual(map.gate.place);
    expect(laid.field).toEqual(map.field.place);
    //: An older server sends no core -- and the city stands where it stood.
    const older = node({
      key: "city",
      layer: "planet",
      place: { lat: 41, lon: 24 },
    });
    expect(drawnAt({ city: older }, [older])[0].place).toEqual(older.place);
  });

  it("draws a surface's own planet, and an inside only its base's", () => {
    //: Closed, the surface is its cities: the wild field between them is
    //: not drawn -- unless one stands on it.
    expect(
      visibleOf(nodes, ["planet"], "gate", "terra").map((n) => n.key),
    ).toEqual(["city"]);
    expect(
      visibleOf(nodes, ["planet"], "gate", "terra", "field").map((n) => n.key),
    ).toEqual(["city", "field"]);
    //: Open, the members stand for the city and its own point is not drawn.
    expect(
      visibleOf(nodes, ["city", "planet"], "gate", "terra").map((n) => n.key),
    ).toEqual(["gate", "field"]);
    expect(
      visibleOf(nodes, ["location"], "gate", "terra").map((n) => n.key),
    ).toEqual(["floor"]);
    //: The sky: the planets and a hull under way; a ship parked or moored
    //: is not a point of the map (D-319 item 10).
    expect(
      visibleOf(nodes, ["space"], "gate", "terra").map((n) => n.key),
    ).toEqual(["terra", "aurora", "flying"]);
  });

  it("joins the delegates of an edge's ends: the city and the field when the city is closed", () => {
    const closed = edgesOf(edges, new Set(["city", "field"]), repr(["planet"]));
    //: The road from the gate and the wild way from the city both become
    //: city--field; the shorter one is kept.
    expect(closed).toEqual([
      { a: "city", b: "field", surface: "road", seconds: 60 },
    ]);
    const open = edgesOf(
      edges,
      new Set(["city", "gate", "field"]),
      repr(["city", "planet"]),
    );
    expect(open.map((e) => [e.a, e.b])).toEqual([
      ["gate", "field"],
      ["city", "field"],
    ]);
  });

  it("draws no edge to what the scene does not show", () => {
    //: The floor is not on the surface; the sky shows no ways at all.
    expect(
      edgesOf(
        edges,
        new Set(["city", "gate", "field"]),
        repr(["city", "planet"]),
      ),
    ).toHaveLength(2);
    expect(
      edgesOf(edges, new Set(["terra", "aurora"]), repr(["space"])),
    ).toEqual([]);
  });
});

describe("the approach", () => {
  const terra = radiusUnits(6371);
  const floor = SURFACE.furthest;
  const globe = globeScale(terra);

  it("opens the surface where the true disk is the marker's size, above the floor", () => {
    const scale = openScale(terra, floor);
    expect(terra * scale).toBeCloseTo(SPHERE_R * SKY_BOUNDS.nearest, 6);
    expect(scale).toBeGreaterThan(floor);
    expect(scale).toBeLessThan(globe);
    //: A planet so large that its disk would be the marker's size only
    //: below the floor opens just above the floor instead.
    expect(openScale(radiusUnits(1e9), floor)).toBeCloseTo(
      floor * ABOVE_FLOOR,
      12,
    );
    expect(openScale(null, floor)).toBe(1);
  });

  it("counts the descent by octaves between the globe and the floor, in steps", () => {
    expect(descentOf(globe, floor, globe)).toBe(0);
    expect(descentOf(globe * 3, floor, globe)).toBe(0);
    expect(descentOf(floor, floor, globe)).toBe(1);
    expect(descentOf(floor / 2, floor, globe)).toBe(1);
    const half = Math.sqrt(globe * floor);
    expect(descentOf(half, floor, globe)).toBeCloseTo(0.5, 12);
    expect(descentOf(half * 1.01, floor, globe) * DESCENT_STEPS).toBeCloseTo(
      Math.round(descentOf(half * 1.01, floor, globe) * DESCENT_STEPS),
      12,
    );
    //: No globe to speak of: nothing to descend.
    expect(descentOf(0.5, 1, 1)).toBe(0);
  });

  it("tilts the eye from over the pole down to where it stands, north up", () => {
    const stand = { lat: 41, lon: 24 };
    expect(tilted(stand, 0)).toEqual(stand);
    expect(tilted(stand, 1)).toEqual({ lat: LAST_LAT, lon: 24 });
    const halfway = tilted(stand, 0.5);
    expect(halfway.lat).toBeCloseTo((41 + LAST_LAT) / 2, 6);
    expect(halfway.lon).toBe(24);
    //: Eased: the first step off the marker is smaller than the middle one.
    const first = tilted(stand, 1 / DESCENT_STEPS).lat - 41;
    const mid = tilted(stand, 0.5 + 1 / DESCENT_STEPS).lat - halfway.lat;
    expect(first).toBeLessThan(mid);
  });
});

describe("a zoom over time", () => {
  it("goes at a steady pace in octaves whatever the scale, and arrives exactly", () => {
    const a = zoomStep(1, 1e-5, 125);
    const b = zoomStep(1e-3, 1e-8, 125);
    expect(Math.log2(1 / a)).toBeCloseTo(ZOOM_PACE / 8, 9);
    expect(Math.log2(1e-3 / b)).toBeCloseTo(ZOOM_PACE / 8, 9);
    expect(zoomStep(1, 1.0001, 120)).toBe(1.0001);
    expect(zoomStep(2, 2, 16)).toBe(2);
    //: Up as well as down.
    expect(zoomStep(1e-6, 1, 125)).toBeCloseTo(1e-6 * 2, 12);
  });

  it("shows the descent on the way down from the marker to the streets", () => {
    //: The click's flight: from where the sky opens Terra to the streets, in
    //: frames of sixteen milliseconds, the tilt must pass through its steps.
    const terra = radiusUnits(6371);
    const floor = SURFACE.furthest;
    const globe = globeScale(terra);
    const seen = new Set<number>();
    let scale = openScale(terra, floor);
    for (let i = 0; i < 1000 && scale !== 1; i++) {
      seen.add(descentOf(scale, floor, globe));
      scale = zoomStep(scale, 1, 16);
    }
    expect(seen.size).toBeGreaterThanOrEqual(DESCENT_STEPS / 2);
  });

  it("carries the camera to the scale about its middle, and the hand stops it", () => {
    const frames: number[] = [];
    const queue: { step: ((t: number) => void) | null } = { step: null };
    let t = 0;
    const cam = createCamera({
      onFrame: (f) => frames.push(f.scale),
      now: () => t,
      raf: (step) => {
        queue.step = step;
        return 1;
      },
      cancel: () => {
        queue.step = null;
      },
    });
    cam.cut({ x: 100, y: 50 });
    cam.zoomToward(0.25);
    expect(cam.descending()).toBe(true);
    for (let i = 0; i < 200 && queue.step; i++) {
      t += 16;
      const step = queue.step;
      queue.step = null;
      step(t);
    }
    expect(cam.frame().scale).toBe(0.25);
    expect(cam.descending()).toBe(false);
    //: About the middle: what was in the middle still is.
    const f = cam.frame();
    expect(f.x + 880 / (2 * f.scale)).toBeCloseTo(100, 6);
    expect(f.y + 540 / (2 * f.scale)).toBeCloseTo(50, 6);
    expect(frames.length).toBeGreaterThan(5);
    //: A cut on the way keeps the descent, and books the next frame of it.
    cam.zoomToward(1);
    cam.cut({ x: 0, y: 0 });
    expect(cam.descending()).toBe(true);
    expect(queue.step).not.toBe(null);
    //: The hand ends it: loose by taking the frame, tethered by a zoom of its own.
    cam.takeFrame();
    expect(cam.descending()).toBe(false);
    cam.zoomToward(1);
    cam.zoomOnMiddle(0.5);
    expect(cam.descending()).toBe(false);
    expect(cam.frame().scale).toBe(0.5);
  });
});

describe("a ship at the pier", () => {
  it("marks the port the server says it lies at, and nothing else", () => {
    expect(nodeGlyph({ port: true, moored: true })).toBe("moored");
    expect(nodeGlyph({ port: true, moored: false })).toBe("port");
    expect(nodeGlyph({ port: false, moored: true })).toBe(null);
  });
});

describe("the walker's dot", () => {
  const leg = {
    from_key: "a",
    to_key: "b",
    started_at: "2026-01-01T00:00:00Z",
    arrives_at: "2026-01-01T00:01:00Z",
  } as Parameters<typeof dotOn>[0];
  const t0 = new Date(leg.started_at).getTime();

  it("is on the leg by the share of the time gone, and clamped at both ends", () => {
    expect(dotOn(leg, { x: 0, y: 0 }, { x: 100, y: 50 }, t0 + 30_000)).toEqual({
      x: 50,
      y: 25,
    });
    expect(dotOn(leg, { x: 0, y: 0 }, { x: 100, y: 50 }, t0 - 5_000)).toEqual({
      x: 0,
      y: 0,
    });
    expect(dotOn(leg, { x: 0, y: 0 }, { x: 100, y: 50 }, t0 + 90_000)).toEqual({
      x: 100,
      y: 50,
    });
  });

  it("is nowhere when an end of the leg is not drawn", () => {
    expect(dotOn(leg, undefined, { x: 1, y: 1 }, t0)).toBe(null);
  });

  //: A scout's run goes by the very same clock (D-327): it has the same two
  //: stamps, and its far end is a place rather than a node. No share of its
  //: own had to be written for it, and that is what is pinned here --
  //: `Scouting` goes into `dotOn` as it is. Let the two part and a walker and
  //: a scout would cross the map at different speeds for one and the same
  //: term.
  it("counts a run of the scout by the very same clock", () => {
    const run: Scouting = {
      from_key: "a",
      place: { lat: 41, lon: 24 },
      started_at: leg.started_at,
      arrives_at: leg.arrives_at,
    };
    expect(dotOn(run, { x: 0, y: 0 }, { x: 100, y: 50 }, t0 + 30_000)).toEqual({
      x: 50,
      y: 25,
    });
  });
});

describe("nodeRadius", () => {
  //: Five radii between nodes is a derivation, not three numbers agreeing:
  //: the circle is counted from the seating gap, so the promise cannot come
  //: untrue.
  const book = (gap: unknown) => ({ constants: { "map.min_gap_m": gap } });

  it("makes the gap five radii whatever the vault says", () => {
    for (const gap of [4, 6, 8]) {
      const r = nodeRadius(book(gap));
      expect((gap * UNITS_PER_METRE) / r).toBeCloseTo(GAP_RADII, 6);
    }
    //: Today's gap comes to exactly the six that used to stand here by hand:
    //: the derivation moved nothing, it only tied the two together.
    expect(nodeRadius(book(6))).toBe(NODE_R);
  });

  it("holds the circle between a dot and a city, and falls back on silence", () => {
    //: The vault may say anything; half a line of a circle cannot be hit with
    //: a finger, and half a screen of one is not a node but a city.
    expect(nodeRadius(book(0.5))).toBe(NODE_R_MIN);
    expect(nodeRadius(book(1000))).toBe(NODE_R_MAX);
    //: The book has not arrived: the first frame is drawn with what the next
    //: one will be drawn with.
    expect(nodeRadius(null)).toBe(NODE_R);
    expect(nodeRadius({ constants: null })).toBe(NODE_R);
    expect(nodeRadius(book("шесть"))).toBe(NODE_R);
  });
});

describe("a city never covers its own planet", () => {
  //: The pixel ceiling keeps the circle within reason on a screen, but on
  //: the globe a screen is a whole world, and from afar the capital grew over
  //: half the planet (owner, 2026-09-08). A city is a place on a planet, and
  //: it must look like one.
  it("holds the circle to a share of the globe when the scene is one", () => {
    const globe = 1000;
    for (const far of [0, 4, 10, 20]) {
      expect(cityRadius(40, far, globe)).toBeLessThanOrEqual(
        globe * CITY_OF_GLOBE,
      );
    }
  });

  it("leaves a flat scene alone: there the pixel ceiling is the whole rule", () => {
    //: With no globe there is no share to take: a city's streets are drawn
    //: flat.
    for (const far of [0, 4, 10]) {
      expect(cityRadius(40, far, null)).toBe(cityRadius(40, far));
    }
  });

  it("still grows with the distance until it meets the share", () => {
    //: The ceiling must not stand in for the growth: from afar the cities are
    //: the map, and growing up to their own limit is their business
    //: (D-323 addendum).
    const globe = 1e9;
    expect(cityRadius(40, 4, globe)).toBeGreaterThan(cityRadius(40, 0, globe));
  });
});
