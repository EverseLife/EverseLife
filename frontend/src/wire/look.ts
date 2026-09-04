// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The answer to `look`, and the readings of it every panel would repeat.
 *
 * `Look` is not a subject of its own -- it is the assembly of all the others,
 * the body and the place and the pocket in one object -- so what it needs is
 * not a smaller file but a single place to be assembled in. It comes in two
 * halves on the wire (D-226): `LiveLook` is what `look` actually answers with,
 * `Parts` are the slow halves fetched by their own commands and kept until an
 * event's `touches` names one, and `compose` puts them back together. The
 * three maps keyed by `keyof Parts` -- the command that reads a part, the key
 * it answers under, the touch that invalidates it -- belong beside `Parts`
 * because adding a part means adding a row to each, and three rows in one
 * file are hard to forget in a way that three rows in three files are not.
 *
 * The functions at the end are readings, not shapes: what the server declines
 * to send because the client can derive it (D-225). `isMine`, `isWild` and
 * `isCivic` are the ownership questions the windows ask over and over;
 * `houseOf` fills the gaps a storey leaves; `stationsOf` folds `bench` and
 * `furniture` into the kinds standing here. They live with `Look` because
 * each of them is a sentence about `Look` and about nothing else.
 */
import { compare } from "../locale";
import type { Names } from "../names";
import type { Air, Doing, Foraging, Frost, Sight } from "./body";
import type { Batch } from "./craft";
import type { DeedView } from "./land";
import type { Printer, Profile } from "./person";
import type { Bench, Carry, Storage, Thing, VarietyRef } from "./thing";
import type { Order, Reservation } from "./trade";
import type { Convoy, Exit, InSight, Transit, Vehicle } from "./travel";

/**
 * The slow parts of the player's state (D-226, 08-session-protocol, step 2):
 * read by their own commands, kept by the client, reread when an event's
 * `touches` names them. `Look` as the panels see it is `LiveLook` and these,
 * put together by `compose()`.
 */
/** A remembered care text (D-296): the crop, the cultivar if it is one, and
 *  the words -- said by the server in the reader's language. */
export type CareNote = { key: string; culture: string; variety?: VarietyRef; text: string };

export type Parts = {
  knowledge: {
    knows: string[];
    discovered: string[];
    care: CareNote[];
    /** The first discoverer's name per known recipe (D-064, D-259). */
    pioneers: Record<string, string>;
  };
  profile: Profile;
  orders: { orders: Order[]; reservations: Reservation[]; batches: Batch[] };
  deeds: DeedView[];
  /** The library here: empty where there is none. Reread on arrival too.
   *  Each entry may carry the first discoverer's name (D-259): the
   *  contribution and the discovery are different names, and a founding
   *  recipe has no pioneer key at all (D-225). */
  shelf: { recipe: string; contributor?: string; pioneer?: string }[];
};

/** Which command reads a part. Exported for `Session.part()` alone: the two
 *  maps below stay next to `Parts`, where the row that has to be added with a
 *  new part is, rather than beside the one caller that walks them. */
export const PART_COMMANDS: Record<keyof Parts, string> = {
  knowledge: "knowledge",
  profile: "account.profile",
  orders: "orders",
  deeds: "deeds",
  shelf: "shelf",
};

/** Which part an event's `touches` entry names, when it names one. */
export const PART_OF_TOUCH: Record<string, keyof Parts> = {
  knowledge: "knowledge",
  profile: "profile",
  orders: "orders",
  deeds: "deeds",
  shelf: "shelf",
};

/** What `look` returns: everything but the slow parts. */
export type LiveLook = Omit<
  Look,
  | "profile"
  | "knows"
  | "discovered"
  | "care"
  | "pioneers"
  | "orders"
  | "reservations"
  | "batches"
  | "deeds"
>;

/** The panels' view: the live part with the slow parts folded back in. */
export function compose(live: LiveLook, parts: Parts): Look {
  const node = live.node && parts.shelf.length ? { ...live.node, shelf: parts.shelf } : live.node;
  return {
    ...live,
    node,
    profile: parts.profile,
    ...parts.knowledge,
    ...parts.orders,
    deeds: parts.deeds,
  };
}

export type Look = {
  identity: string;
  profile: Profile;
  money: string;
  knows: string[];
  /** Which of the known recipes were opened by one's own experiment (D-064, D-209). */
  discovered: string[];
  /** The care texts remembered in the Library (D-296): the knowledge is the words. */
  care: CareNote[];
  /** The first discoverer's name per known recipe (D-064, D-259): the name is
   *  bound to the recipe forever. Founding recipes have no entry at all.
   *  Optional here, required in `Parts`: an older server's part lacks the
   *  key, and the composed look must say so to its readers. */
  pioneers?: Record<string, string>;
  orders: Order[];
  reservations: Reservation[];
  batches: Batch[];
  carry?: Carry;
  body?: {
    id: string;
    stamina: number;
    sleeping_since?: string;
    sleeping_home: boolean;
    /** Until this moment the stamina spend is reduced: a meal, not a buff (D-119). */
    satiated_until?: string;
  };
  /**
   * The node as facts the client cannot derive by itself (D-225). Whatever
   * follows from other fields is not sent: what stands here is `bench` and
   * `furniture` (`stationsOf`), a library or a hall is read off them through
   * the class book (`anyOfClass`), "mine" is `owner`
   * against one's own name (`isMine`), "wild" is no owner and no city
   * (`isWild`). A key that would carry nothing -- no shelf, no door lists,
   * not for sale, nothing built -- is absent rather than null or [].
   */
  node?: {
    key: string;
    name: string;
    /** Place-sign property ids ("woods", "stones"): place extraction is shown by them (D-177). */
    features: string[];
    /** The owner's map mark, if one is nailed on (D-238). */
    emblem?: string | null;
    /** The place's own words, written by whoever disposes of it (D-238). */
    about?: string;
    fertility: number;
    /**
     * The place's climate as farming reads it (D-261): the node's mean and
     * the planet's swing, the day's light and the rainfall. The current
     * temperature and the night are this client's arithmetic over
     * `look.clock` (D-225) -- alive between looks by construction.
     * Absent where exploration never wrote a temperature -- no gate there.
     */
    climate?: {
      temperature: { mean: number; swing: number };
      light: { day: number };
      precipitation: number;
    } | null;
    /** Whose plot: the holder runs the estate (06-farming). */
    owner?: string;
    /** The owning city, if the land is civic: ownership is public (D-178). */
    owner_city?: string;
    /**
     * The location is shut for entry: only the holder and the white list come in
     * (D-199, D-204). Visible to everyone -- and passage through it stays open to
     * everyone too: shutting stops entry, not passage.
     */
    gated: boolean;
    /**
     * When the Forerunners' reactor standing here goes silent (D-232). Absent
     * where there is none. The output itself is not sent: the fading is a
     * straight line, and `reactor.output` with `reactor.lifetime` from the
     * catalog hold both its ends.
     */
    reactor_until?: string;
    /**
     * When the ground here is due to move (D-197, P6). Absent everywhere but
     * Pyroxis, and there only while a warning stands. The free signal is an
     * event, and an event reaches whoever is connected in the second it is
     * written -- this is the same warning carried by the place itself, so
     * somebody who logs in ten minutes into a six-hour window still sees it.
     */
    shaking_at?: string;
    /**
     * Own plots marked out here. Absent where there are none -- and its
     * absence is what hides the farming window: a strip is marked out in the
     * land window, and the cycle that follows gets a place of its own once
     * there is a strip for it to happen on.
     */
    plots?: number;
    /**
     * Which floor of a house one is standing on (D-247). Absent on the ground:
     * the ground floor **is** the plot, and there the land windows have their
     * answers. Upstairs there is no land at all -- no yard, no purchase, no
     * strips, no city founded -- and one window instead, «Этаж».
     */
    storey?: number;
    /** Disconnected for non-payment: machines do not work (D-149). */
    cut_off: boolean;
    /**
     * Whose bill the household of this node is (D-149): `owner` -- the holder's,
     * `city` -- the treasury's, and it pays with energy rather than money,
     * `nobody` -- there is nobody to bill. Empty outside the city grid: no meter
     * there at all, one works from a battery.
     */
    upkeep?: "owner" | "city" | "nobody";
    /** Plot area, m2 (D-125). */
    area: number;
    /** Daily land tax for the built area, minor units (D-127, D-220).
     *
     * Zero outside a city, or where the city has set no rate. Falls with
     * every node from the bioprinter, like the purchase price does. */
    tax: number;
    /**
     * What this library holds and who brought each recipe (D-068, D-209),
     * with the first discoverer's name where one exists (D-259). Only when a
     * library stands here; the catalog table is this shelf, not the vault.
     */
    shelf?: { recipe: string; contributor?: string; pioneer?: string }[];
    /**
     * The door lists, by names, and only to the holder (D-204): `allowed` enter
     * a shut location, `barred` enter nowhere. Black beats white.
     */
    door?: { allowed: string[]; barred: string[] };
    /** Whether the viewer may name the plot (D-178): present only when they may. */
    may_name?: true;
    /** Building and capacity: a machine takes area (D-106). Absent on an
     * empty plot with nothing under way.
     *
     * On a plot `area` is the usable area -- the sum of the floors -- and
     * `ground` is what the house takes from the plot: storeys made these two
     * different (D-125). On a **storey** (D-247) the block is about the floor
     * one stands on -- its metres, its places and how high the plot reaches --
     * and the keys about the house are absent: the type, the wear, the repair
     * and the sites all belong to the plot below, and a key carrying nothing is
     * not sent (D-225). Read it through `houseOf`, which fills the gaps.
     */
    building?: {
      area: number;
      ground?: number;
      floors: number;
      /** What it is built of (D-218): the type sets the bill and the decay. */
      kind?: string;
      /** How sound it is, 0..100. At nothing the house falls (D-218). */
      condition?: number;
      /** Condition lost per day -- the type's own rate. */
      decay?: number;
      slots: number;
      used: number;
      /**
       * Work in progress. A city order's build carries only its term; a
       * construction site (D-266) carries its phase, its bill and what was
       * brought -- the window draws the progress from these and the buttons
       * from `state` and `owner`.
       */
      sites?: {
        area: number;
        floors: number;
        kind?: string;
        ready_at: string | null;
        site?: string;
        state?: "gathering" | "building" | "ready";
        owner?: string;
        needed?: Record<string, number>;
        brought?: Record<string, number>;
        started_at?: string | null;
      }[];
    };
    /** Purchase price of an empty civic plot, in minor units (D-089). Absent when not for sale. */
    price?: number;
  };
  /** The city whose territory we stand on, and our own rights in it (D-154, D-155). */
  city?: {
    id: string;
    name: string;
    node: string;
    /** Rights as strings: broad (`treasury`) and narrow (`law:import_duty`). */
    powers: string[];
    /** Whether a citizen of this city (D-160). */
    citizen: boolean;
    /** How they admit: open, by application or by invitation. */
    admission: "open" | "application" | "invite";
    /** An application filed or an invitation received -- waits for its side. */
    requested: boolean;
  };
  /** Where the identity belongs: citizenship is one and visible from everywhere (D-160).
   *
   *  Two fields and no more: leaving is instant (D-281), so there is no filed
   *  declaration to count down and no term the print held one by. */
  citizenship?: {
    city?: string;
    since: string;
  };
  /** An ongoing exploration run, if any (D-152). */
  survey?: { returns_at: string };
  /**
   * Foraging on the empty land of the place (D-210). Empty where the land is
   * built up or somebody else's -- unless a search of ours is already going here.
   */
  forage?: Foraging;
  /**
   * The cold, and only where there is any (D-231). Absent on a planet without
   * a climate: there is nothing to show and nothing to fear on Terra.
   */
  frost?: Frost;
  /** The air, and only where there is none (D-233, D-234): in flight and on an
   *  airless world. Absent on Terra and Aurora, for everybody, always. */
  air?: Air;
  /**
   * Everything the body is at: one body does one thing (D-211), but a frozen
   * batch or a plough of one's own can stand beside the thing running now.
   * "Дела" draws the list; the rest of the client greys out what would be
   * refused, with the reason on the button.
   */
  doings?: Doing[];
  /** Letters and posts that have arrived and are not read (D-222): the tab's count. */
  net_unread?: number;
  /** Polls of one's own city still waiting for an answer (D-161). Counted
   *  apart from the letters: the tab adds them up, an unread letter and an
   *  unanswered ballot are not the same thing. */
  net_votes?: number;
  /** Does a drilling rig stand in this node: the stand shows the row by it. */
  rig_here?: boolean;
  /** Ships within sight of this node (D-201): moored at the pier one stands
   *  on, or the rooms of the one being stood in. Empty everywhere else. */
  ships?: InSight;
  /** The planet's clock: where the count starts and how long a day is (D-029). */
  clock?: { planet: string; epoch?: string; day_hours: number };
  /** Whether a city can be founded here and what is missing for that (D-159).
   *  Empty -- the place or the person is unsuitable: foreign land, a city over
   *  the node, not a planet, or a citizenship already held elsewhere (D-281).
   *  Only the keys of the roles this node lacks: the roles themselves and the
   *  machines that fill them are a constant and come from `/public/founding`. */
  foundation?: {
    missing: string[];
  };
  /** Own convoy: what it is harnessed to and what it carries (D-157). */
  convoy?: Convoy;
  /** Vehicles standing in this node: one harnesses to what is nearby. */
  vehicles?: Vehicle[];
  /** No body -- the identity is in the cloud: where to print and whether a print is ongoing (D-033). */
  printers?: Printer[];
  printing?: { ready_at: string };
  /** The node's machines by name: which one can be taken right now (D-150). */
  bench?: Bench[];
  /** The node's furniture: a bed and a shelf are not machines, they have their own window (D-090). */
  furniture?: Bench[];
  /** The node's storages and what lies in them (D-181). */
  storages?: Storage[];
  /** The open ground of the place: the plot outside the building's footprint
   *  (D-244). Area nought where a house covers the whole node -- then there is
   *  no ground to put anything on, and the window says nothing. */
  ground?: {
    space: {
      area: number;
      used: number;
      cargo_mass: number;
      free: number;
    };
    things: Thing[];
  };
  /** The floor of the house: what lies indoors and how much room is left
   *  (D-192, D-244). Area nought where no building stands: then everything is
   *  out under the sky, and the open ground below is the only surface. */
  floor?: {
    space: {
      /** Capacity: the building's usable area, m². Nought without a building --
       *  and that nought is how one tells there is no house (D-225). */
      area: number;
      used: number;
      cargo_mass: number;
      free: number;
      slots: number;
      slots_used: number;
    };
    things: Thing[];
    /**
     * Whether the viewer may reach the floor: everyone inside may put things down
     * and take them (D-204). False for a passer-by through a shut location.
     */
    open: boolean;
    /** Whether the place itself is the viewer's: the window says so in words. */
    mine: boolean;
  };
  /** Own deeds for plots: electronic documents of the Net (D-116). */
  deeds?: DeedView[];
  inventory: Thing[];
  stall?: Thing[];
  veins?: { id: string; resource: string; richness: number }[];
  exits?: Exit[];
  travel?: Transit;
  /** An open face survives the player leaving: on return the session is in place. */
  mining?: Sight;
};

/**
 * The plot's building block, or an empty yard where the server sent none:
 * the windows that build and place machines count from zero on bare land.
 *
 * The gaps are filled rather than guarded at every call site: a storey sends
 * the four keys about the floor and none of the ones about the house (D-247),
 * and a window reading `ground` or `sites` there wants nought, not `undefined`.
 */
export function houseOf(node: Look["node"]): Required<
  Pick<NonNullable<NonNullable<Look["node"]>["building"]>,
    "area" | "ground" | "floors" | "decay" | "slots" | "used" | "sites">
> & { kind?: string; condition?: number } {
  return {
    area: 0, ground: 0, floors: 0, decay: 0, slots: 0, used: 0, sites: [],
    ...(node?.building ?? {}),
  };
}

/**
 * Kinds of things standing in the node, one name per kind: machines and
 * furniture together. The node scene is built from them (D-176), and the
 * windows ask them by class. Assembled here, not sent: `bench` and
 * `furniture` already name every instance.
 *
 * The entries are ids (D-251). Most callers treat the list as a set; whoever
 * lays it out for the player passes `names`, and the order follows the
 * Russian display words rather than the ASCII of the ids.
 */
export function stationsOf(
  look: Pick<Look, "bench" | "furniture">,
  names?: Names | null,
): string[] {
  const kinds = new Set<string>();
  for (const thing of [...(look.bench ?? []), ...(look.furniture ?? [])]) kinds.add(thing.goods);
  const word = (id: string) => names?.goods?.[id] ?? id;
  return [...kinds].sort((a, b) => compare(word(a), word(b)));
}

/** The node's plot is the viewer's own: the holder is named, and it is us (D-178). */
export function isMine(look: Pick<Look, "identity" | "node">): boolean {
  return look.node?.owner != null && look.node?.owner === look.identity;
}

/** Nobody's land outside a city: never privatized, open to all (D-198). */
export function isWild(node: Look["node"]): boolean {
  return Boolean(node) && node!.owner == null && node!.owner_city == null;
}

/** Civic land: the city holds it, whether or not a person has bought it. */
export function isCivic(node: Look["node"]): boolean {
  return node?.owner_city != null;
}
