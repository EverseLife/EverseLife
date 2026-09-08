// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The reads open to everybody, and the client's one door onto the wire.
 *
 * Two surfaces talk to the server, and they differ not by accident. `/public/*`
 * is everything anyone may know without being anyone: catalogs, tiers, order
 * books, the doors a newcomer may print at. Everyone knows the prices (D-047),
 * and there is no reason to hide them, so these are plain GETs with no session
 * behind them. The other surface -- the only place the player **acts** -- is
 * the socket, and it lives in `session.ts`.
 *
 * The rest of this file is a door rather than a room. Some seventy modules
 * import their wire types from `api` and have done since there was only one
 * file to import from; the shapes now live in `wire/*` by subject and the
 * socket in `session.ts`, and every name either of them ever exported is
 * re-exported below. That is deliberate and not a transitional measure: a
 * panel has no business knowing which subject a type was filed under, and
 * `import { Look, Session } from "../api"` says what it means.
 */

import { HTTP } from "./host";
import type { WordsBundle } from "./locale";
import type { Renames } from "./names";
import type { FoundingRole, LawBook } from "./wire/city";
import type { RecipeBook } from "./wire/craft";
import type { Door, Line } from "./wire/person";
import type { Book } from "./wire/trade";
import type { Terrain, Tile, WorldMap } from "./wire/travel";

async function read<T>(path: string, token?: string, cache?: RequestCache): Promise<T> {
  //: The token travels in the ordinary header and only where it means
  //: something. Catalogs are the same for everybody and are asked for without
  //: one; the map is not (D-240) -- what it answers with depends on where the
  //: body stands, and without a token it answers with the sky.
  const answer = await fetch(HTTP + path, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    cache,
  });
  if (!answer.ok) throw new Error(`${path}: ${answer.status}`);
  return answer.json();
}

export const constants = () => read<{ digest: string; values: Record<string, any> }>(
  "/public/constants",
);
export const recipes = () => read<RecipeBook>("/public/recipes");
/** Names for the wire's ids (D-251): what `NAME()` in a message resolves
 *  through, and what every list is sorted by. Same load pattern as the
 *  other catalogs. */
export const renames = () => read<Renames>("/public/renames");
/** The words of one language, as the FTL the server itself renders (D-251).
 *  One file feeds both ends, so a message cannot drift between them. */
export const words = (locale: string) =>
  read<WordsBundle>(`/public/i18n/${encodeURIComponent(locale)}`);
/** The law book: which laws exist and, for the ones that are a choice, what
 *  may be chosen. Catalog constants belong in `/public`, not in a socket
 *  answer (D-225) -- the city sends only what it decided. */
export const laws = () => read<LawBook>("/public/laws");
/** A planet's relief (D-319): a constant of the world, asked for without a
 *  token and once per planet -- the globe draws its ground from it. */
export const terrain = (planet: string) =>
  read<Terrain>(`/public/terrain/${encodeURIComponent(planet)}`);
/** A tile of a planet's local relief (D-323): asked for under a close
 *  frame, once per tile, without a token. */
export const terrainTile = (planet: string, row: number, col: number) =>
  read<Tile>(`/public/terrain/${encodeURIComponent(planet)}/${row}/${col}`);
/** Doors into the world: read before identification -- a newcomer has no identity yet. */
export const doors = () => read<{ doors: Door[] }>("/public/doors");
/** Character lines and the number of players -- also before identification (D-187). */
export const lines = () => read<{ lines: Line[] }>("/public/lines");
/** The two rulers a book is read by: quality tiers and the price steps rows glue at. */
export const tiers = () =>
  read<{ tiers: { from: number; to: number; name: string }[]; steps: number[] }>(
    "/public/quality/tiers",
  );
/** What a place must have before a city can be founded on it: a role and the
 *  machines that fill it. A catalog constant, read once from here instead of
 *  riding in every `look`; `look` says only which roles the node lacks. */
export const founding = () =>
  read<{ roles: FoundingRole[] }>("/public/founding");
/**
 * The map as it looks from where you stand (D-240, D-319).
 *
 * With a token: what the body sees and the identity remembers, and the
 * public. Without one -- the sky, and every planet's surface as it was
 * `map.public_delay_days` ago (D-319 item 7): the entry globe reads that,
 * and the landing picker asks both and lays one over the other (owner,
 * 2026-09-08). So this is the one public read that takes one.
 */
//: The anonymous map is served with a public cache of minutes and an ETag
//: (D-319 item 7); asked with `no-cache` it is revalidated every time, so a
//: fresh snapshot is seen at once and an unchanged one costs a 304.
export const worldMap = (token?: string) =>
  read<WorldMap>("/public/map", token, token ? undefined : "no-cache");

//: The one personal map the windows share, held between them by the stand it
//: was read from.
let standing: { key: string; map: Promise<WorldMap> } | null = null;

/**
 * The personal map, read once per stand rather than once per window.
 *
 * The anonymous half is a snapshot behind an ETag and costs a 304; the
 * personal one is not cached at all and is a walk of the whole node and edge
 * tables per call (`engine/sight.py` names that cost out loud). Two windows
 * wanting it -- the map and the ship's console -- were two walks, and neither
 * had a reason to disagree with the other: what the body sees changes when
 * the body moves, not when a panel opens.
 *
 * `at` is that reason, and the caller states it: the node stood in and the
 * exits out of it. A different `at` is a different map and is read again; the
 * same `at` twice is one read. A read that fails is not kept, so the next
 * window tries rather than inheriting the failure.
 */
export function standingMap(token: string, at: string): Promise<WorldMap> {
  const key = `${token} ${at}`;
  if (standing?.key !== key) {
    const map = worldMap(token);
    standing = { key, map };
    void map.catch(() => {
      if (standing?.map === map) standing = null;
    });
  }
  return standing.map;
}
export const plants = () =>
  read<{
    plants: {
      id: string;
      name: string;
      gives: string;
      /** What is sown with: seeds are an item separate from the harvest (D-057). */
      seed: string;
      cycle_days: number;
    }[];
  }>("/public/plants");
export const positions = (node: string) =>
  read<{
    node: string;
    positions: { goods: string; tier: string }[];
    /** Last deal per goods name, any tier, in minor units. Never traded -- absent. */
    prices: Record<string, number>;
  }>(`/public/market/${encodeURIComponent(node)}`);
/** The book for one position. `step` omitted -- the server picks the finest that fits. */
export const book = (node: string, goods: string, tier: string, step?: number | null) =>
  read<Book>(
    `/public/market/${encodeURIComponent(node)}/book` +
      `?goods=${encodeURIComponent(goods)}&tier=${encodeURIComponent(tier)}` +
      (step ? `&step=${step}` : ""),
  );

/** Money comes in minor units: 1 TC = 10 000. Not a cent is lost. It lives in
 *  `money.ts`, which no browser is needed to load, and is re-exported here so
 *  that every call site that ever knew it still does. */
export { MONEY_SCALE, minor, tk } from "./money";

//: What follows is the door described at the top of the file: every name the
//: one-file `api` used to export, re-exported from the module that now holds
//: it. The lists are written out rather than starred through on purpose --
//: `export *` would carry along whatever a wire module grows next, and this
//: surface is a promise to seventy call sites, not a side effect.

/** The socket and what it carries (`session.ts`). */
export { Refused, Session } from "./session";
export type { Happening, Listener } from "./session";

/** A thing and what holds it (`wire/thing.ts`). */
export { varietyText } from "./wire/thing";
export type { Bench, Carry, Storage, Thing, VarietyRef } from "./wire/thing";

/** The graph and moving along it (`wire/travel.ts`). */
export { SURFACE, spell } from "./wire/travel";
export type {
  Convoy,
  Exit,
  InSight,
  MapEdge,
  MapStub,
  MapNode,
  ForecastDay,
  MapRoute,
  RoadWork,
  Terrain,
  Tile,
  Scouting,
  Transit,
  Vehicle,
  WorldMap,
} from "./wire/travel";

/** Speech, letters and channels (`wire/talk.ts`). */
export type {
  Channel,
  ChannelFound,
  ChatLine,
  Circle,
  Letter,
  Post,
  Thread,
} from "./wire/talk";

/** Who somebody is and how they come to be (`wire/person.ts`). */
export type { Card, Door, Enrollment, Line, Printer, Profile } from "./wire/person";

/** The body's occupations and its scales (`wire/body.ts`). */
export type { Air, Doing, Foraging, Frost, Sight } from "./wire/body";

/** The deed and the bill that come with a plot (`wire/land.ts`). */
export type { DeedView, Holding } from "./wire/land";

/** The answer to `look` and the readings of it (`wire/look.ts`). */
export { compose, houseOf, isCivic, isMine, isWild, PART_OF_TOUCH, stationsOf } from "./wire/look";
export type { LiveLook, Look, Parts } from "./wire/look";

/** The city as a polity (`wire/city.ts`). */
export { CITY_ABOUT_LIMIT, CITY_NAME_LIMIT, LAW_SCOPE, POWERS } from "./wire/city";
export type {
  CityLoans,
  CityPanel,
  CityView,
  CityVote,
  CourtCase,
  FoundingRole,
  Law,
  LawBook,
  Office,
  SanctionKind,
  WorksBoard,
  WorksOrder,
} from "./wire/city";

/** Buying and selling (`wire/trade.ts`). */
export type { Book, Level, Loaded, Order, Reservation, Taken } from "./wire/trade";

/** The recipe book, the estimate and the work (`wire/craft.ts`). */
export type { Batch, Invention, Operation, Plan, Recipe, RecipeBook } from "./wire/craft";
