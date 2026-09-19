// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The graph, and moving along it.
 *
 * One subject in two grains. `MapNode`, `MapEdge`, `MapRoute` and `WorldMap`
 * are the world as `/public/map` draws it; `Exit`, `Transit`, `RoadWork`,
 * `Vehicle` and `Convoy` are the same graph as the body meets it -- where one
 * may step from here, how long the step takes, what is harnessed for it and
 * what the road is made of. `InSight` is the third grain: a ship's own little
 * graph, which is never on the public map (D-201).
 *
 * `SURFACE` and `spell` are here rather than in a panel because the surface
 * and the seconds are fields of these shapes, and every screen that shows an
 * edge shows both.
 */
import { t } from "../locale";
import type { Thing } from "./thing";

/** Vehicles standing in the node: one harnesses to them, not stands at them (D-157). */
export type Vehicle = {
  id: string;
  goods: string;
  condition: number;
  /** Hold capacity, kg. Empty -- the vault did not name it. */
  capacity?: number;
  /** Multiplier to walking speed: a barrow is slower than legs, a wagon faster. */
  speed_k: number;
  /** Taken by somebody else's harness. */
  taken: boolean;
};

/** Convoy: what it is harnessed to and what it carries (D-157). */
export type Convoy = {
  id: string;
  type_key: string;
  condition: number;
  capacity: number;
  /** How much it already carries, kg. */
  mass: number;
  speed_k: number;
  heavy: boolean;
  /** The hold, in the rows every list of things has: a batch reaches into
   *  one's own hold (D-315), so the window counts it like the pocket -- with
   *  the tier, the mark and, for a vessel, what is poured into it (D-230). */
  cargo: Thing[];
};

/** A road as work on an edge (D-107, D-158). */
export type RoadWork = {
  edge: string;
  /** Where it leads, by key: what the column picks its one road by. */
  node: string;
  /** Where it leads, in words. Not an identifier: a city has many «Квартира». */
  to: string;
  surface: "wild" | "trail" | "road" | "paved";
  /** Surface condition 0..100: overgrows without maintenance. */
  condition: number;
  seconds: number;
  /** The next tier, or empty for a highway. */
  next?: "road" | "paved";
  /** How much surface laying a tier takes, and how much resurfacing does. */
  needs?: number;
  mend_needs?: number;
  /** How much surface is in the hands right now. */
  at_hand: number;
  working: boolean;
};

/** Where one can go from here, how much it costs in time and how much in body (D-147). */
export type Exit = {
  key: string;
  name: string;
  surface: "wild" | "trail" | "road" | "paved";
  seconds: number;
  /** Stamina spend for the road. With a vehicle -- zero. */
  stamina: number;
};

/** While walking -- you are absent: everything in-person is closed (D-107). */
export type Transit = {
  to: string;
  to_key: string;
  from_key: string;
  started_at: string;
  arrives_at: string;
  /** Autopath (D-045): the route's final goal, if it is beyond this leg. */
  final?: string;
  final_key?: string;
  legs_left?: number;
};

/**
 * A run of the scout under way (D-327): the way out to the find.
 *
 * Not a `Transit`, and it cannot be one: the far end is a **place**, because
 * the node it will become does not exist yet. The client cannot work any of
 * this out for itself either -- after a reload it does not even know what was
 * aimed at -- so it travels (D-225). Where the scout is right now does not:
 * that is the two stamps and a straight line, counted frame by frame (D-226).
 */
export type Scouting = {
  /** The node the run set out from, and the one it returns to if given up. */
  from_key: string;
  /** The centre of the cell being opened -- where the find will stand. */
  place: { lat: number; lon: number };
  started_at: string;
  arrives_at: string;
};

/** What the field says at an aimed point, before the walk (D-321 addendum,
 *  owner 2026-09-12; `explore.peek`): the readings of the public field at
 *  the cell and the chances of what the run rolls there -- never the roll,
 *  which is the find's own. */
export type Peek = {
  /** The node already standing in the cell, when somebody found it first. */
  found: string | null;
  biome: string;
  facet: string | null;
  province: string | null;
  water: "river" | "lake" | "none";
  /** Where the map draws no water, the chance the run rolls a stream the
   *  relief is too coarse to draw, per cent (`site.river_share`). */
  stream_chance: number;
  mountain: boolean;
  temperature_c: number;
  /** Per cent of the vault's scale (`site.rain_range`). */
  rain: number;
  /** The day's swing, degrees. */
  swing_c: number;
  /** The chances of the place's marks, per cent, by mark (woods, stones, meadow). */
  marks: Record<string, number>;
  vein_chance: number;
  complex_chance: number;
};

/** The world map: nodes and edges. Cities and highways are public (D-097). */
export type MapNode = {
  key: string;
  name: string;
  /** Display layer: the world is one graph, layers are a way to look at it (D-045). */
  layer: "space" | "planet" | "city" | "location";
  /** The group the node belongs to: location -> city -> planet. */
  parent: string | null;
  /** The land under the node, square metres (D-323 addendum): a city's
   *  outline is the land of its nodes joined. Absent off the ground. */
  area?: number | null;
  /** How near and how far one may scout from here, metres (D-321 item 4):
   *  the biome's reach, sent with the node the body stands in alone. */
  reach?: { min: number; max: number };
  /** The spaceport: the city's second door, the one ships couple to (D-206). */
  port: boolean;
  /** Which planet the node belongs to. The space layer paints by it. */
  planet: string;
  /** Where the node stands, once and for everybody (D-237). Given by the
   *  server when the node is created and never recomputed, so the map is the
   *  same map for every player and the same one tomorrow. A surface node
   *  stands on its planet's sphere in degrees (D-319); a floor or a room
   *  stands on the flat plan of the inside in map units. Absent on the space
   *  layer -- a planet's point comes from the clock. */
  place?: { lat: number; lon: number } | { x: number; y: number } | null;
  /** A planet's place in the system: display radius, a full circle in real
   *  days and the phase at the world's epoch. Only planets have one -- on the
   *  space layer a place is a function of time, not of a settled layout. */
  orbit: { radius: number; period_days: number; phase: number } | null;
  /** A ship lies at this pier (D-319 item 10): sent only when so. To
   *  everybody else a moored hull is the port wearing a mark -- the hull's
   *  own point comes only to whoever stands at that pier or aboard -- and a
   *  pier's row cannot tell itself from the parking, both hanging under the
   *  planet, so the port says which it is. */
  moored?: boolean;
  /** Known from a map in the hands and nothing else (D-319 item 6): the
   *  day the map was drawn on -- the map's own mark of "old". */
  drawn?: number;
  /** Drawn, but not playable yet: Aquatica is out of the alpha (D-104). */
  deferred: boolean;
  /** Part of a ship: its delegate on the space layer or a room aboard (D-201). */
  aboard: boolean;
  /** A ship under way. It has no edges at all while it flies, so its place on
   *  the map is a share of the way between the port it left and the one it is
   *  due at -- nothing in the graph could say it. */
  flight: {
    /** Where it is bound, or nothing for a drifter (D-289): its line is the coast ahead. */
    to: string | null;
    started_at: string;
    arrives_at: string;
    /** The arc a crossing flies, map units, at equal time steps (D-271): the
     *  hull is drawn along it at the share of the time gone. Absent on the
     *  legs to and from the ground. */
    arc?: [number, number][] | null;
  } | null;
  /** Place-sign property ids ("woods", "stones"): the map draws the node's
   *  type glyph by them (D-238, D-251). Optional: older servers do not send it. */
  features?: string[];
  /** The owner's nailed mark, if any (D-238): beats the place signs. */
  emblem?: string | null;
  /** The province the node lies in (landscape plan, wave 3): an id named
   *  through `/public/renames`. Absent on a node without one. */
  province?: string;
  /** The face the node's ground wears (landscape plan, wave 7): the facet's
   *  id, named through `/public/renames` like the province. It is what a
   *  found node is called -- «Опушка» rather than a sixth «Берег». */
  facet?: string;
  /** Shown dark: remembered or public, not in sight (D-319). */
  faded?: boolean;
  /** The city whose outline this node draws, by the key of the city's own
   *  node (D-332, D-356) -- sent only where `parent` does not say: a plot
   *  under its city is the city's by its parent, a find taken in by a
   *  highway still hangs under the planet. A find the outline merely covers
   *  is the city's land and carries none: it does not draw the line. */
  territory?: string;
  /** The name of the city this node is the centre of. Sent on a **city's**
   *  row alone, and it is the row of the node with the city's bioprinter
   *  (D-330): standing in it one reads `name` -- «Ядро: Принтер Предтеч» --
   *  and from afar, where the whole city is one mark, the map writes this
   *  instead. The client cannot work it out (D-225): a city's name is copied
   *  at founding and never follows the node. */
  city?: string | null;
  /** The node the city grew from -- its bioprinter. Sent on a **city's** row
   *  alone: from afar a city is drawn as that node's point (D-319). Normally
   *  the row's own key, a city being founded where a printer stands; it parts
   *  from it only where the machine the city grew from is gone and another is
   *  the oldest left (D-312). The client cannot work it out (D-225) -- the map
   *  carries no machines, and "the oldest printer that is not the prison's"
   *  is the engine's own reading of a centre (`city.lookup.core`). */
  core?: string | null;
};

export type MapEdge = {
  a: string;
  b: string;
  surface: Exit["surface"];
  seconds: number;
};

/** One day of a corridor's calendar: the cheapest passage leaving then --
 *  its delta-v and how long that arc takes (D-271). */
export type ForecastDay = { day: number; dv: number; hours: number };

/** A corridor between two planets: not an edge of the graph but the price of a
 *  passage (D-037, D-271). The engine forecasts the cheapest arc for each of
 *  the coming days -- the window is where the calendar dips -- and the client
 *  leafs through it as it winds the sky. Ends by planet, not by node key. */
export type MapRoute = {
  a: string;
  b: string;
  days: ForecastDay[];
};

/** A way out of sight (D-319 п. 6): drawn from its seen end a little way
 *  towards where it leads, and no farther. The bearing is degrees clockwise
 *  from north; where the way ends and how far is the fog's to keep. */
export type MapStub = {
  from: string;
  bearing: number;
  surface: Exit["surface"];
};

/** A planet's relief as the globe draws it (D-319): a grid of heights, the
 *  two levels that make sea and mountain of it, the rivers as runs of
 *  lat/lon, the lakes as cells, and the warmth of each row -- everybody's
 *  from the world's first day, the same for everybody. */
export type Terrain = {
  rows: number;
  cols: number;
  sea_level: number;
  mountain_level: number;
  grid: number[][];
  rivers: [number, number][][];
  lakes: [number, number][];
  warmth: number[];
  /** The tiling of the local relief (D-323): degrees a tile, steps a tile. */
  tile: { deg: number; n: number };
  /** The local noise's levels of a peak and of a basin, and whether a basin
   *  holds water on this planet. */
  peak_level: number;
  basin_level: number;
  wet: boolean;
  /** The rasters the shader draws by (landscape plan wave 5): their shape
   *  and what their bytes mean. Absent on a build older than the wave. */
  raster?: RasterPassport;
};

/** What `/public/terrain/{planet}/raster/{kind}` answers with: the atlas of
 *  the equal-area grid, `rows` by `cols` texels row by row -- twelve square
 *  faces of `nside` cells with a border of `border` each (D-328), and every
 *  cell `step_m` metres a side wherever it lies. `height` is a signed
 *  sixteen-bit metre, `biome` a byte into `biomes` (255 on water), `form` a
 *  byte into `forms`, `water` a byte into `water`. */
export type RasterKind =
  | "height"
  | "biome"
  | "form"
  | "water"
  | "rock"
  | "province"
  | "flow"
  | "lake"
  /** The river as a share of the cell, as `lake` is: the shader cuts its
   *  bank between the cells off this, so a stream bends where the water
   *  bends and not where the grid does. */
  | "stream"
  | "temperature"
  | "rain"
  | "river";
export type RasterPassport = {
  /** What flows on this planet: water on Terra and Aquatica, lava on
   *  Pyroxis. One raster says where the fluid lies on every planet -- a cell
   *  is either land or under it, and either way it cannot be walked into --
   *  but a lava ocean must not be painted blue, so the substance comes with
   *  the picture rather than being guessed from the planet's name. */
  fluid: "water" | "lava";
  /** The grid the picture is cut on (D-328): twelve square faces of `nside`
   *  cells a side, equal in area everywhere -- no rows of latitude, and so
   *  no pole where a cell shrinks to nothing. */
  grid: "healpix";
  nside: number;
  cells: number;
  /** The fineness of the picture's quick copy (2026-09-18): the same rasters
   *  two rungs coarser, a sixteenth of the bytes, asked for by this number
   *  and drawn while the whole picture comes (`rasters.previewOf`). Absent
   *  from a server that keeps none. */
  preview_nside?: number;
  /** The rasters the quick copy is cut for: the shader's. A copy that
   *  lacks one the shader reads is no copy to the client. */
  preview_kinds?: string[];
  /** The name of the whole picture's bytes, both finenesses (2026-09-18):
   *  the rasters are asked for by it and kept by the browser a year under
   *  it, so they are always the set this passport describes. */
  version?: string;
  /** The texture: the twelve faces `across` by `down`, each with a `border`
   *  of cells taken from the face over the edge so the blending between
   *  cells stays continuous across a seam. The texel of cell (face, x, y)
   *  is ((face % across) * (nside + 2 border) + border + x,
   *      (face / across) * (nside + 2 border) + border + y). */
  rows: number;
  cols: number;
  across: number;
  down: number;
  border: number;
  /** The metres a cell spans -- everywhere, not just at the equator. */
  step_m: number;
  relief_m: number;
  /** What one step of the height raster is worth, metres (a decimetre):
   *  whole metres flattened the shore and the water's edge ran along the
   *  lattice. Required: guessed at a metre, every height would be ten
   *  times over with nothing failing. */
  height_unit_m: number;
  biomes: string[];
  forms: string[];
  /** The water raster's classes by code: land, sea, lake, river. */
  water: string[];
  /** The provinces by the code of the province raster (0 is none, k is the
   *  k-th of this list): the map draws their boundary and their name. */
  provinces?: string[];
  /** The temperature raster's scale: a byte a cell is `min + byte * step`
   *  degrees on the planet's own scale, and `cold` to `hot` is the planet's
   *  range, the ends of the climate layer's ramp (D-331). */
  temperature_c: { min: number; step: number; cold: number; hot: number };
  /** The land's mean rain share, nought to one: what the sky over the sea
   *  is stretched by (the engine's `Field.land_rain`). */
  sea_wet: number;
  /** What a full byte of the flow raster stands for on a log scale: how
   *  much land drains through the river a cell belongs to. The map draws a
   *  river of its own width by it -- a brook a thread, the continent's
   *  river two hundred metres across (wave 6's debt). */
  flow_max_km2?: number;
};

/** A tile of a planet's local relief (D-323): `n + 1` rows and columns of
 *  the local noise from the tile's south-west corner, `step` degrees apart
 *  -- the very numbers a scout's feet read. The heights the page has from
 *  the grid (D-225); a peak and a basin are cut from the noise against the
 *  sketch's levels. */
export type Tile = {
  row: number;
  col: number;
  lat0: number;
  lon0: number;
  step: number;
  n: number;
  local: number[][];
};

export type WorldMap = {
  nodes: MapNode[];
  edges: MapEdge[];
  stubs: MapStub[];
  routes: MapRoute[];
  /** Where the sky's count starts -- the planets' days, seasons and weather
   *  run from it. On the anonymous map alone: a body has it in
   *  `look.clock` (D-225). Null in a world with no node yet. */
  epoch?: string | null;
};

/** What of ships is visible from where one stands, and nothing beyond it
 *  (D-201): at a pier the moored ships, aboard the rooms between which one
 *  walks. None of it is on the public map -- from outside a ship is a single
 *  hull, and its layout is what a boarder would want to know. */
export type InSight = {
  nodes: MapNode[];
  edges: MapEdge[];
  /** Aboard: the hull is off its pier, under way or adrift, as against moored
   *  at a pier or in orbit (D-333). The rooms say nothing of it themselves.
   *  Absent from a pier, and from older servers. */
  underway?: boolean;
};

/** Surface in words, by message key: a module-scope map holds keys, not text. */
export const SURFACE: Record<Exit["surface"], string> = {
  wild: "ui-map-surface-wild",
  trail: "ui-map-surface-trail",
  road: "ui-map-surface-road",
  paved: "ui-map-surface-paved",
};

/** Travel time in words: seconds for a step across the city, minutes for a road. */
export function spell(seconds: number): string {
  //: Rounded before the unit is chosen, so 59.7 seconds reads as a minute
  //: rather than as "60 с" -- the same carry `clock.duration` takes.
  //:
  //: The units are `clock`'s own messages rather than a second set: the two
  //: print the same "3 мин", and one of them would have gone stale. Every
  //: count goes in as a string -- a term is read, not summed, and `NUMBER`
  //: would put a separator inside "1 200 ч".
  if (Math.round(seconds) < 60) {
    return t("ui-clock-seconds", { n: String(Math.round(seconds)) });
  }
  if (Math.round(seconds / 60) < 60) {
    return t("ui-clock-minutes", { n: String(Math.round(seconds / 60)) });
  }
  return t("ui-clock-hours", { n: (seconds / 3600).toFixed(1) });
}
