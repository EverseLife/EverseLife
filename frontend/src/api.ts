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
import type { RecipeBook } from "./wire/craft";
import type { Door, Line } from "./wire/person";
import type { Book } from "./wire/trade";
import type { WorldMap } from "./wire/travel";

async function read<T>(path: string, token?: string): Promise<T> {
  //: The token travels in the ordinary header and only where it means
  //: something. Catalogs are the same for everybody and are asked for without
  //: one; the map is not (D-240) -- what it answers with depends on where the
  //: body stands, and without a token it answers with the sky.
  const answer = await fetch(HTTP + path, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
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
/** Doors into the world: read before identification -- a newcomer has no identity yet. */
export const doors = () => read<{ doors: Door[] }>("/public/doors");
/** Character lines and the number of players -- also before identification (D-187). */
export const lines = () => read<{ lines: Line[] }>("/public/lines");
/** The two rulers a book is read by: quality tiers and the price steps rows glue at. */
export const tiers = () =>
  read<{ tiers: { from: number; to: number; name: string }[]; steps: number[] }>(
    "/public/quality/tiers",
  );
/**
 * The map as it looks from where you stand (D-240).
 *
 * Two steps of the graph around the body, one step of the planet's surface,
 * and the sky. Without a token -- the sky alone: the surface asks for a body.
 * So this is the one public read that takes one.
 */
export const worldMap = (token?: string) => read<WorldMap>("/public/map", token);
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
  MapNode,
  MapRoute,
  RoadWork,
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
export type { Air, Doing, Foraging, Frost, Outlook, Sight } from "./wire/body";

/** The deed and the bill that come with a plot (`wire/land.ts`). */
export type { DeedView, Holding } from "./wire/land";

/** The answer to `look` and the readings of it (`wire/look.ts`). */
export { compose, houseOf, isCivic, isMine, isWild, PART_OF_TOUCH, stationsOf } from "./wire/look";
export type { LiveLook, Look, Parts } from "./wire/look";

/** The city as a polity (`wire/city.ts`). */
export { CITY_ABOUT_LIMIT, LAW_SCOPE, POWERS } from "./wire/city";
export type {
  CityLoans,
  CityPanel,
  CityView,
  CityVote,
  CourtCase,
  Law,
  Office,
  SanctionKind,
  WorksBoard,
  WorksOrder,
} from "./wire/city";

/** Buying and selling (`wire/trade.ts`). */
export type { Book, Level, Loaded, Order, Reservation, Taken } from "./wire/trade";

/** The recipe book, the estimate and the work (`wire/craft.ts`). */
export type { Batch, Invention, Operation, Plan, Recipe, RecipeBook } from "./wire/craft";
