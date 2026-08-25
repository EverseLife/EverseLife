// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Talking to the server.
 *
 * Two surfaces, and they differ not by accident:
 *
 * - `/public/*` -- reads available to all: catalogs, tiers, order books.
 *   Everyone knows the prices (D-047), and there is no reason to hide them;
 * - `/session/ws` -- the only place where the player **acts**. There is and
 *   will be no convenient REST for "make a swing": it would turn mining into
 *   a script (60-meta/01-anti-cheat).
 *
 * The protocol is boring: one command -- one reply. Hence the queue below.
 */

//: The default server address is the same host the page was opened from.
//: Otherwise someone coming over the local network would look for the server
//: on their own phone: everyone has their own `localhost`. An explicit
//: `VITE_API` overrides this when the server is not nearby.
//:
//: In development the server lives on a separate port, in production behind
//: the same origin as the client, at the `/api` path: so the built image does
//: not know the production domain and suits anyone, and the socket gets
//: `wss://` without separate configuration.
const HTTP =
  import.meta.env.VITE_API ??
  (import.meta.env.PROD
    ? `${window.location.origin}/api`
    : `${window.location.protocol}//${window.location.hostname}:8000`);
const WS = HTTP.replace(/^http/, "ws") + "/session/ws";

export type Thing = {
  id: string;
  goods: string;
  amount: number;
  quality?: number;
  tier: string;
  condition: number;
  /** Dish kind: the combination decides the kind, not the quality (D-128). */
  flavor?: string;
  /** Edibility comes from vault data, not the client's guesses. */
  food: boolean;
  /** Fits the pot: a product, not a pickaxe (16-cooking). */
  ingredient: boolean;
  spoils_at?: string;
  /** Coin fineness in thousandths: a coin has no quality, it has metal (D-016). */
  fineness?: number;
  /** The mark: whose work this is (D-058). */
  maker?: string;
  /** For seeds: cultivar and batch strength, % (D-057). */
  variety?: string;
  vigor?: number;
  /** For a battery: charge with self-discharge (D-071). */
  charge?: number;
  /** Unit weight, kg, and the slot if this is gear (D-146). */
  mass: number;
  slot?: string;
  /**
   * For a knowledge carrier: the recipe written on it, and the name the counter
   * knows the stack by -- "Рецепт: Стекло" (D-209). `key` equals `goods` for
   * everything else.
   */
  recipe?: string;
  key: string;
  /**
   * For a vessel only -- a canister, a tank (D-230): what is poured into it.
   * A liquid never lies in the pocket by itself, so without this the water in
   * the hands would be invisible. The capacity is the catalog's (`store`).
   */
  content?: Thing[];
};

/** Carried load: how much is carried, how much can be, and what is worn (D-146). */
export type Carry = {
  load: number;
  capacity: number;
  slots: string[];
  equipped: Record<string, { id: string; goods: string }>;
};

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
  cargo: { id: string; type_key: string; amount: number; quality?: number }[];
};

/** A road as work on an edge (D-107, D-158). */
export type RoadWork = {
  edge: string;
  /** Where it leads. */
  to: string;
  surface: "trail" | "road" | "paved";
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
  surface: "trail" | "road" | "paved";
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

/** The world map: nodes and edges. Cities and highways are public (D-097). */
export type MapNode = {
  key: string;
  name: string;
  /** Display layer: the world is one graph, layers are a way to look at it (D-045). */
  layer: "space" | "planet" | "city" | "location";
  /** The group the node belongs to: location -> city -> planet. */
  parent: string | null;
  ring: number | null;
  /** The city gate: every road beyond the walls starts here (D-206). */
  exit: boolean;
  /** The spaceport: the city's second door, the one ships couple to (D-206). */
  port: boolean;
  /** Which planet the node belongs to. The space layer paints by it. */
  planet: string;
  /** A planet's place in the system: display radius, a full circle in real
   *  days and the phase at the world's epoch. Only planets have one -- on the
   *  space layer a place is a function of time, not of a settled layout. */
  orbit: { radius: number; period_days: number; phase: number } | null;
  /** Drawn, but not playable yet: Aquatica is out of the alpha (D-104). */
  deferred: boolean;
  /** Part of a ship: its delegate on the space layer or a room aboard (D-201). */
  aboard: boolean;
  /** A ship under way. It has no edges at all while it flies, so its place on
   *  the map is a share of the way between the port it left and the one it is
   *  due at -- nothing in the graph could say it. */
  flight: { to: string; started_at: string; arrives_at: string } | null;
};
export type MapEdge = { a: string; b: string; surface: Exit["surface"]; seconds: number };
/** A corridor between two planets: not an edge of the graph but the price of a
 *  passage (D-037). The two ends are the vault's -- in conjunction and in
 *  opposition -- and where between them a given hour falls is decided by where
 *  the planets stand then. Ends by planet, not by node key. */
export type MapRoute = {
  a: string;
  b: string;
  window_hours: number;
  apart_hours: number;
};
export type WorldMap = { nodes: MapNode[]; edges: MapEdge[]; routes: MapRoute[] };
/** What of ships is visible from where one stands, and nothing beyond it
 *  (D-201): at a pier the moored ships, aboard the rooms between which one
 *  walks. None of it is on the public map -- from outside a ship is a single
 *  hull, and its layout is what a boarder would want to know. */
export type InSight = { nodes: MapNode[]; edges: MapEdge[] };

/** A remark as heard by someone standing in the location (D-043, D-050). */
export type ChatLine = {
  id: string;
  who: string;
  kind: "speech" | "action" | "ooc";
  quiet: boolean;
  text: string;
  overheard: boolean;
  source?: string;
  at: string;
};

/** A circle: membership visible, content not. */
export type Circle = { id: string; name?: string; members: string[]; mine: boolean };

/** The Net (D-222): correspondence kept, arriving by the road. */
export type Thread = {
  id: string;
  /** The other party. */
  who: string;
  surname: string;
  last_at?: string;
  /** The last letter the reader can already see. */
  preview?: string;
  unread: number;
};
export type Letter = {
  id: string;
  who: string;
  mine: boolean;
  text: string;
  sent_at: string;
  /** When it reaches the reader: for one's own, "on the way" until then. */
  delivered_at: string;
};
export type Channel = {
  id: string;
  name: string;
  about: string;
  /** The city's: marked as official. */
  official: boolean;
  /** The reader writes here. */
  writable: boolean;
  /** Implied by citizenship: cannot be dropped. */
  implied: boolean;
  /** Who writes it: the author's name, or the city's. */
  by: string;
  last_at?: string;
  unread: number;
};
/** A channel found by search: subscribed or not. */
export type ChannelFound = Pick<Channel, "id" | "name" | "about" | "official" | "by"> & {
  subscribed: boolean;
};
export type Post = { id: string; who: string; text: string; at: string; delivered_at: string };
/** Somebody's card: self-description and citizenship, nothing of the body. */
export type Card = {
  name: string;
  surname: string;
  age?: number;
  about: string;
  line: "human" | "nymph";
  since: string;
  city?: string;
};

/** What an exploration run from here will cost (D-156).
 *
 * The price is a property of the place, not the player: untrodden
 * surroundings give a find in minutes, trodden ones in hours and not always.
 * Shown before leaving, otherwise it reads as engine randomness. */
export type Outlook = {
  /** How many finds have already been made from this node. */
  explored: number;
  minutes: { min: number; max: number };
  /** The largest stamina price -- by the longest run. */
  stamina: number;
  /** Chance with the requested species in mind: the rare is found worse (D-151). */
  chance: number;
  /** By how much the species request narrowed the chance; 1 -- no request. */
  aim?: number;
  /**
   * By how much the crowding of the graph narrowed it; 1 -- roomy here (D-207).
   * Edges pile up where everybody wants to be, and a crowded place searches worse.
   */
  crowding?: number;
  /** The node a find will hang on, when it is not this one: from a city, the gate. */
  anchor?: string;
  /** Which species is requested, if any. */
  resource?: string;
};

/** Account panel (D-187): self-description next to the name. Nothing game-related here. */
export type Profile = {
  email?: string;
  /** The name is unique and unchangeable (D-011): reputation rests on it. */
  name: string;
  surname: string;
  age?: number;
  about: string;
  line: "human" | "nymph";
  since: string;
};

/** A character line on the selection screen: one is playable in the alpha (D-104). */
export type Line = {
  id: "human" | "nymph";
  name: string;
  world: string;
  playable: boolean;
  summary: string;
  traits: string[];
  /** How many play it: a living world is seen as a number. */
  players: number;
};

/** Registration request: four client steps -- one server command. */
export type Enrollment = {
  email: string;
  password: string;
  password_again: string;
  line: Line["id"];
  name: string;
  surname: string;
  age: number | null;
  about: string;
  node: string;
};

/**
 * One occupation of the body (D-211): the road, the field, sleep, a search, a
 * plot under the plough, a batch, a working face.
 *
 * `kind` is a stable id -- "sleep", "forage", "plot" -- and the client decides
 * by it what to draw and what button ends it; `title` and `what` are the
 * server's words for the same thing, and they may change without notice.
 */
export type Doing = {
  kind: string;
  title: string;
  what: string;
  until?: string;
};

/**
 * The slow parts of the player's state (D-226, 08-session-protocol, step 2):
 * read by their own commands, kept by the client, reread when an event's
 * `touches` names them. `Look` as the panels see it is `LiveLook` and these,
 * put together by `compose()`.
 */
export type Parts = {
  knowledge: { knows: string[]; discovered: string[]; agrotech: string[] };
  profile: Profile;
  orders: { orders: Order[]; reservations: Reservation[]; batches: Batch[] };
  deeds: DeedView[];
  /** The library here: empty where there is none. Reread on arrival too. */
  shelf: { recipe: string; contributor?: string }[];
};

const PART_COMMANDS: Record<keyof Parts, string> = {
  knowledge: "knowledge",
  profile: "account.profile",
  orders: "orders",
  deeds: "deeds",
  shelf: "shelf",
};
const PART_ANSWERS: Record<keyof Parts, string> = {
  knowledge: "knowledge",
  profile: "profile",
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
  "profile" | "knows" | "discovered" | "agrotech" | "orders" | "reservations" | "batches" | "deeds"
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
  /** Learned agrotech: crops whose norm the identity has already studied (D-057). */
  agrotech: string[];
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
    /** Place-sign properties ("forest", "outcrop"): place extraction is shown by them (D-177). */
    features: string[];
    fertility: number;
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
     * What this library holds and who brought each recipe (D-068, D-209). Only
     * when a library stands here; the catalog table is this shelf, not the vault.
     */
    shelf?: { recipe: string; contributor?: string }[];
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
     * `area` is the usable area -- the sum of the floors; `ground` is what the
     * house takes from the plot. Storeys made these two different (D-125).
     */
    building?: {
      area: number;
      ground: number;
      floors: number;
      /** What it is built of (D-218): the type sets the bill and the decay. */
      kind?: string;
      /** How sound it is, 0..100. At nothing the house falls (D-218). */
      condition?: number;
      /** Condition lost per day -- the type's own rate. */
      decay: number;
      slots: number;
      used: number;
      /** Work in progress: ordered, paid for, not yet standing. */
      sites: {
        area: number;
        floors: number;
        kind?: string;
        ready_at: string;
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
  /** Where the identity belongs: citizenship is one and visible from everywhere (D-160). */
  citizenship?: {
    city?: string;
    since: string;
    /** An exit declaration is filed: citizenship lapses by this date. */
    leaving_at?: string;
    /** An obligation taken as a print condition: no leaving before this date (D-184). */
    bound_until?: string;
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
  /**
   * Everything the body is at: one body does one thing (D-211), but a frozen
   * batch or a plough of one's own can stand beside the thing running now.
   * "Дела" draws the list; the rest of the client greys out what would be
   * refused, with the reason on the button.
   */
  doings?: Doing[];
  /** Letters and posts that have arrived and are not read (D-222): the tab's count. */
  net_unread?: number;
  /** Does a drilling rig stand in this node: the stand shows the row by it. */
  rig_here?: boolean;
  /** Ships within sight of this node (D-201): moored at the pier one stands
   *  on, or the rooms of the one being stood in. Empty everywhere else. */
  ships?: InSight;
  /** The planet's clock: where the count starts and how long a day is (D-029). */
  clock?: { planet: string; epoch?: string; day_hours: number };
  /** Whether a city can be founded here and what is missing for that (D-159).
   *  Empty -- the place is unsuitable: foreign land, a foreign city or not a planet. */
  foundation?: {
    missing: string[];
    needs: { role: string; any_of: string[] }[];
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
  /** The floor of the place: what lies here and how much room is left (D-192). */
  floor?: {
    space: {
      /** Capacity: the building's area, or the whole plot without one, m². */
      area: number;
      roofed: number;
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

/** A deed for a plot: ownership documented (D-116). */
export type DeedView = {
  id: string;
  node?: string;
  name?: string;
  area?: number;
  owner?: string;
  /** The issue price: the purchase price, zero for taken wild land. */
  paid: number;
  /** Listed for sale: the price and the addressee, if the contract is addressed. */
  sale_price?: number;
  sale_to?: string;
  issued_at: string;
};

/** A machine in the node: one person works at a machine (D-150). */
export type Bench = {
  id: string;
  goods: string;
  quality?: number;
  condition: number;
  busy: boolean;
  mine: boolean;
  /** Charge belongs to the battery standing here as a machine (D-179). */
  charge?: number;
};

/** A node storage: a chest or a shelf (D-181).
 *
 * The chest itself is visible to anyone -- it stands in the room; the contents
 * come only to whoever may open it, for the rest `content` is empty.
 */
export type Storage = {
  id: string;
  goods: string;
  /** Capacity, kg. */
  capacity: number;
  /** How many kilograms are already taken. */
  mass: number;
  /** Whether the viewer may put and take. */
  mine: boolean;
  content: Thing[];
};

/** A door into the world: where to print a body and for how much (D-028, D-033). */
export type Printer = {
  node: string;
  name: string;
  city?: string;
  /** That very eternal printer: free, but twelve hours. */
  precursor: boolean;
  energy: number;
  iron: number;
  cost: number;
  minutes: number;
  iron_here: number;
  /** The city prints at its own expense: code-law `body_print` (D-032). */
  at_city_expense: boolean;
};

/** A door for a newcomer: where to print for the first time (D-013, D-182).
 *
 * Neither price nor term: the first body is printed at once and for free at
 * any door (D-040). The choice here is about people, not money.
 */
export type Door = {
  node: string;
  name: string;
  city: string | null;
  /** The city's word to newcomers: its promise, not a contract (D-183). Empty -- silent. */
  about: string;
  /** Print conditions -- the engine enforces them (D-184): citizenship at the
   *  moment of printing, its term in days and the sales tax, %. */
  citizenship: boolean;
  term: number;
  tax: number;
  /** The Forerunners' Printer: an eternal machine, needs nobody's treasury. */
  precursor: boolean;
  citizens: number;
  /** Living bodies on the city's land now -- whom you will meet, not who is registered. */
  population: number;
  /** The settlement grant from the city charter, in minor units. Zero -- does not pay. */
  grant: number;
};

/** Own node and the household bill (D-149). */
export type Holding = {
  node: string;
  name: string;
  area: number;
  /** Whether there is a city grid: outside a city there are no bills at all. */
  grid: boolean;
  energy_per_period: number;
  cost_per_period: number;
  debt: number;
  cut_off: boolean;
  last_energy: number;
};

/** The city's code-law in force: its own decision or the vault default (D-130). */
export type Law = {
  name: string;
  unit?: string;
  note?: string;
  value?: string;
  own: boolean;
};

export type Office = {
  id: string;
  who: string;
  identity: string;
  title: string;
  powers: string[];
};

/** City summary: charter, laws, offices, treasury (D-154). */
export type CityView = {
  id: string;
  name: string;
  /** The city's word to newcomers: the authority writes it, everyone sees it (D-183). */
  about: string;
  node: string;
  treasury: number;
  /**
   * What the city's own nodes burn per meter period (D-149). The treasury pays
   * for them with energy, not money: `worth` is what the same energy would have
   * fetched at the city tariff had it been sold, and nobody is billed it.
   */
  upkeep: {
    nodes: number;
    hours: number;
    energy: number;
    worth: number;
    tariff: number;
  };
  offices: Office[];
  charter: Record<string, string>;
  charter_params: Record<string, number>;
  /** Charter questions in words: the text lives in the vault, not the client (D-130). */
  charter_questions: {
    id: string;
    section: string;
    question: string;
    options: { id: string; label: string }[];
  }[];
  laws: Record<string, Law>;
  powers: string[];
  /** Whether decisions are made here: authority is in-person (D-155). */
  at_hall: boolean;
  lots: { key: string; name: string; area: number; owner?: string; free: boolean }[];
  citizens: string[];
};

/** The city's economic panel (D-124, D-140). The public snapshot is visible to all. */
/** A case in the city court (D-166). */
export type CourtCase = {
  id: string;
  plaintiff?: string;
  defendant?: string;
  claim: string;
  state: "open" | "judged" | "dismissed";
  verdict?: string;
  opened_at: string;
};

/** A sanction primitive from the vault: the engine enforces not all (D-166). */
export type SanctionKind = { id: string; name: string; enforced: boolean };

/** An ongoing citizens' poll (D-161). */
export type CityVote = {
  id: string;
  kind: "law" | "election" | "recall" | "charter" | "council";
  /** Who votes: all citizens or council members (D-164). */
  voters: "citizens" | "council";
  law?: string;
  value: unknown;
  /** Candidates in the election: they nominate themselves while the poll runs (D-162). */
  candidates: { id: string; name?: string; votes: number }[];
  /** Whom one's own vote in the election is for. */
  choice?: string;
  closes_at: string;
  threshold: "simple" | "two_thirds" | "unanimous";
  /** The share of eligible voters needed for a quorum; 0 -- no quorum required. */
  quorum: number;
  electorate: number;
  yes: number;
  no: number;
  /** Own vote, if cast. */
  mine?: boolean;
  may_vote: boolean;
};

export type CityPanel = {
  city: string;
  window_hours: number;
  at: string;
  /** Without an administration the city is blind: the data does not update. */
  blind: boolean;
  full: boolean;
  market: { trades: number; volume: number; prices: Record<string, number> };
  people: { here: number; printed: number };
  production: {
    mined: Record<string, number>;
    harvested: number;
    crafted: Record<string, number>;
  };
  energy: { stored: number; tariff: number; spent_work: number; spent_home: number };
  goods: Record<string, number>;
  /** Imports, exports, trips and collected duty over the window (D-123, D-124). */
  trade: {
    imported: Record<string, number>;
    exported: Record<string, number>;
    trips_in: number;
    trips_out: number;
    duty_collected: number;
  };
  treasury?: {
    balance: number;
    collected: Record<string, number>;
    spent: Record<string, number>;
  };
};

/** Broad rights. Narrow ones -- `law:<id>` -- are assembled from the law catalog. */
export const POWERS: Record<string, string> = {
  laws: "все законы",
  charter: "устав",
  treasury: "казна",
  offices: "должности",
  land: "участки",
  dashboard: "панель города",
  justice: "суд",
  citizens: "граждане",
  channel: "канал города",
};

/** The right to one law: `law:import_duty` (D-155). */
export const LAW_SCOPE = "law:";

/** The limit of the city's word (D-183). The server counts it (`runtime.CITY_ABOUT_LIMIT`);
 *  it is here so that the field does not let one type what is refused in advance. */
export const CITY_ABOUT_LIMIT = 300;

export const SURFACE: Record<Exit["surface"], string> = {
  trail: "бездорожье",
  road: "дорога",
  paved: "тракт",
};

/** Travel time in words: seconds for a step across the city, minutes for a road. */
export function spell(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} с`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`;
  return `${(seconds / 3600).toFixed(1)} ч`;
}

/**
 * The heat reserve and the node it is spent in (D-231).
 *
 * The hours are **not** a number the server refreshes: `hours` was true at
 * `at`, and it moves by `per_hour` from there. The client counts the hand
 * itself, the way it counts the planet's clock -- the server would otherwise
 * have to speak once a second (D-226).
 */
export type Frost = {
  /** «мерзлота» or «пекло»: what the planet does to a body left in it. */
  climate: string;
  /** Whether **this** node is warm: a stove works here, or it is the board. */
  warm: boolean;
  hours: number;
  at: string;
  /** Hours of reserve gained per hour here; negative is the countdown. */
  per_hour: number;
  /**
   * The ceiling, which depends on what is worn -- the client cannot derive it
   * (D-225). What the frozen body pays is not here for the opposite reason:
   * `frost.frozen_stamina` and `frost.frozen_drain_k` are catalog constants and
   * live in `/public/constants`.
   */
  max: number;
};

/** The foraging window: the plot's empty land, the search and its find (D-210). */
export type Foraging = {
  /** Empty land, m2: the plot minus the building footprint. */
  area: number;
  /** Below this much there is nowhere to forage. */
  min_area: number;
  /** Whether a new search may start here: own or nobody's land with room. */
  allowed: boolean;
  /** The mean length of one search here, seconds; empty if nothing is found here at all. */
  seconds?: number;
  /** What one search costs in stamina, found or passed. */
  stamina: number;
  /** What the land gives at all and how often, by share; the handful per find. */
  finds: { goods: string; share: number; units: number }[];
  /** No search; a search under way; a find waiting for the decision. */
  state: "idle" | "searching" | "found";
  started_at?: string;
  ready_at?: string;
  found?: { goods: string; units: number; quality: number; mass: number };
};

export type Order = {
  id: string;
  side: "buy" | "sell";
  goods: string;
  tier: string;
  price: number;
  left: number;
};

export type Batch = {
  id: string;
  work: "make" | "repair" | "recycle";
  output: string;
  units: number;
  quality: number;
  /** The machine it needs; empty for what is made by hand. */
  station?: string;
  /**
   * Under way, or waiting (D-209): behind another work of yours (`queued`),
   * frozen in another node (`away`), or here but with no free machine
   * (`no_station`).
   */
  state: "running" | "waiting";
  waiting?: "queued" | "away" | "no_station";
  /** Where the work is: a frozen batch is waited for in its node. */
  node?: string;
  /** The current run's ends: the deadline bar shows a share, and a share needs a beginning. */
  started_at?: string;
  ready_at?: string;
  /** Work left while waiting, seconds. */
  left_seconds?: number;
  /** For a carrier being written: which recipe goes onto it. */
  recipe?: string;
};

/** What came of an attempt to make something without a recipe (D-064, D-209). */
export type Invention = {
  success: boolean;
  learned: string[];
  burned: Record<string, number>;
  note?: string;
  batch?: { id: string; output: string; quality: number; ready_at?: string };
};

/** Everything the player sees about the face. Roof stability is not here and cannot be. */
export type Sight = {
  sign: string;
  mined: number;
  swings: number;
  timbers: number;
  stamina: number;
  pace: "steady" | "fast";
  state: "active" | "left" | "collapsed";
  session: string;
};

export type Plan = {
  output: string;
  units: number;
  quality: number;
  spread: number;
  ceiling: number;
  accuracy: number;
  waste: number;
  minutes: number;
  consumes: Record<string, number>;
  /** Industrial mode: the batch runs on the automaton (D-035). */
  auto: boolean;
  /** How much energy the automaton eats and what that costs at the city tariff. */
  energy: number;
  energy_cost: number;
};

/** Reservation: the only way to buy remotely -- with a deposit and a term (D-047). */
export type Reservation = {
  id: string;
  goods: string;
  tier: string;
  amount: number;
  price: number;
  deposit: number;
  node: string;
  node_key: string;
  /** When it was taken -- the other end of the term for the deadline bar. */
  placed_at: string;
  expires_at: string;
};

export type Level = { price: number; amount: number };
export type Book = {
  node: string;
  type_key: string;
  tier: string;
  bids: Level[];
  asks: Level[];
  last: number | null;
  spread: number | null;
};

export class Refused extends Error {}

type Waiting = {
  resolve: (answer: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

/** Where the session token lives between page refreshes (D-187). */
const TOKEN_KEY = "everselife.token";

/**
 * What the server says on its own (D-226). `event` is the journal kind,
 * `touches` names the parts of the player's state it changed -- the client
 * rereads those whether or not it knows the kind. `seq` is the journal row:
 * the last one seen goes back to the server on reconnect, and the server
 * replays what was missed.
 */
export type Happening = {
  event: string;
  seq?: number;
  at?: string;
  touches: string[];
  /** Who did it, when it was not you. */
  who?: string;
  [key: string]: unknown;
};

export type Listener = (happening: Happening) => void;

/** How long the session waits before rising again after a break, and the cap. */
const REVIVE_DELAY_MS = 1000;
const REVIVE_DELAY_CAP_MS = 30_000;
/** How long a command waits for its answer. */
const ANSWER_TIMEOUT_MS = 30_000;

/** The client session. Holds the socket, the commands in flight, and the listeners.
 *
 * The socket is two-way (D-226): commands go out numbered and come back by
 * number; in between the server speaks on its own, and whoever subscribed
 * with `on()` hears it. A session never reads answers by order.
 *
 * The socket does not live forever: the server and proxies cut idle
 * connections. A broken session rises by itself -- on close it reconnects,
 * identifies by token and names the last event it saw, so nothing said in
 * the meantime is lost. A command that finds a dead socket first waits for
 * that rise, and only then goes.
 *
 * Identification is email and password (D-187). The password is entered
 * once: the server gives a token, it lives in `localStorage`, and by it the
 * session rises after F5 and after a break. Logging out of the account panel
 * revokes and forgets the token.
 */
export class Session {
  private socket: WebSocket | null = null;
  private pending = new Map<number, Waiting>();
  private ticket = 0;
  private reviving: Promise<void> | null = null;
  private reviveTimer: ReturnType<typeof setTimeout> | null = null;
  private reviveDelay = REVIVE_DELAY_MS;
  private listeners = new Map<string, Set<Listener>>();
  /** The last journal row heard: the `since` of the next `hello`. */
  private seq = 0;
  account = "";
  name = "";
  token = "";
  /**
   * The alpha's debug widget, if this copy opens it for this name (D-229).
   * Said once at the greeting rather than in every `look`: it cannot be
   * derived from anything else the server sends (D-225), and it does not
   * change while the session lasts.
   */
  admin = false;

  /** The token of the last login, if any: auto-login starts from it. */
  static remembered(): string {
    try {
      return localStorage.getItem(TOKEN_KEY) ?? "";
    } catch {
      return "";
    }
  }

  private remember(token: string): void {
    this.token = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* приватный режим: жетон живёт только в памяти */
    }
  }

  /**
   * Hear what the server says: one kind (`"knowledge.learned"`), a prefix
   * (`"market."`), or everything (`"*"`). Returns the way to stop hearing.
   */
  on(kind: string, listener: Listener): () => void {
    let set = this.listeners.get(kind);
    if (!set) {
      set = new Set();
      this.listeners.set(kind, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
    };
  }

  private hear(happening: Happening): void {
    if (typeof happening.seq === "number" && happening.seq > this.seq) this.seq = happening.seq;
    const heard = new Set<Listener>();
    for (const [kind, set] of this.listeners) {
      const fits =
        kind === "*" ||
        kind === happening.event ||
        (kind.endsWith(".") && happening.event.startsWith(kind));
      if (fits) set.forEach((listener) => heard.add(listener));
    }
    heard.forEach((listener) => {
      try {
        listener(happening);
      } catch (error) {
        console.error("listener failed on", happening.event, error);
      }
    });
  }

  /** Bring up the socket. Identification is a separate step: a newcomer has nothing to identify with yet. */
  private async connect(): Promise<void> {
    await this.close();
    const socket = new WebSocket(WS);
    this.socket = socket;

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error("сервер не отвечает"));
    });
    this.reviveDelay = REVIVE_DELAY_MS;

    socket.onmessage = (message) => {
      const parsed = JSON.parse(message.data);
      if (typeof parsed.event === "string") {
        this.hear({ touches: [], ...parsed });
        return;
      }
      const waiting = typeof parsed.id === "number" ? this.pending.get(parsed.id) : undefined;
      if (!waiting) {
        console.warn("answer to nobody", parsed);
        return;
      }
      this.pending.delete(parsed.id);
      const { id: _id, ...answer } = parsed;
      if (typeof answer.refused === "string") {
        waiting.reject(new Refused(answer.refused));
      } else {
        waiting.resolve(answer);
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.pending.forEach((w) => w.reject(new Error("сессия закрыта")));
      this.pending.clear();
      //: The server speaks first now (D-226): a closed socket is a deaf one,
      //: so it rises on its own and not at the next command.
      if (this.token) this.scheduleRevive();
    };
  }

  private scheduleRevive(): void {
    if (this.reviveTimer) return;
    this.reviveTimer = setTimeout(() => {
      this.reviveTimer = null;
      this.revive().catch(() => {
        this.reviveDelay = Math.min(this.reviveDelay * 2, REVIVE_DELAY_CAP_MS);
        if (this.token) this.scheduleRevive();
      });
    }, this.reviveDelay);
  }

  /** Login by email and password. */
  async open(email: string, password: string): Promise<Record<string, unknown>> {
    await this.connect();
    return this.greet("hello", { email, password });
  }

  /** Login with last time's token: F5 does not ask for the password. */
  async resume(token: string): Promise<Record<string, unknown>> {
    await this.connect();
    try {
      return await this.greet("hello", { token });
    } catch (error) {
      //: A revoked or expired token is forgotten at once -- otherwise every
      //: login would start with the same refusal.
      if (error instanceof Refused) this.remember("");
      throw error;
    }
  }

  /** Registration (D-187): there is no identity yet -- it is printed at the
   * chosen door (D-153, D-182). Four client steps go as one command. */
  async create(application: Enrollment): Promise<Record<string, unknown>> {
    await this.connect();
    return this.greet("join", { ...application });
  }

  /** Logout: the token is revoked and forgotten, the socket closed. */
  async logout(): Promise<void> {
    try {
      if (this.socket?.readyState === WebSocket.OPEN) await this.send("account.logout");
    } catch {
      /* отзыв — вежливость: забыть жетон важнее, чем дождаться ответа */
    }
    this.remember("");
    this.name = "";
    this.account = "";
    this.admin = false;
    this.seq = 0;
    await this.close();
  }

  private async greet(
    cmd: string,
    args: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    //: `since` turns the stream of events on: the last row heard, 0 for "from now".
    const hello = await this.send(cmd, { ...args, since: this.seq });
    this.account = String(hello.account ?? "");
    this.name = String(hello.hello ?? "");
    this.admin = hello.admin === true;
    if (typeof hello.token === "string") this.remember(hello.token);
    return hello;
  }

  async send(cmd: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      //: Nothing to identify with yet: there was no session, nothing to repair.
      if (!this.token) throw new Error("нет сессии");
      await this.revive();
    }
    const socket = this.socket!;
    const id = ++this.ticket;
    return new Promise((resolve, reject) => {
      //: An answer that never comes must not hang a button forever.
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error("сервер не ответил"));
      }, ANSWER_TIMEOUT_MS);
      this.pending.set(id, {
        resolve: (answer) => {
          clearTimeout(timer);
          resolve(answer);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      socket.send(JSON.stringify({ id, cmd, ...args }));
    });
  }

  /** Bring a broken session back up. One rise for everyone who caught it.
   *
   * A refused token -- revoked, expired -- is not a broken socket: the
   * session is over, the token is forgotten, and `session.lost` tells the
   * screen to show the login instead of rising forever. */
  private revive(): Promise<void> {
    this.reviving ??= (async () => {
      try {
        await this.connect();
        await this.greet("hello", { token: this.token });
      } catch (error) {
        if (error instanceof Refused) {
          this.remember("");
          this.name = "";
          await this.close();
          this.hear({ event: "session.lost", touches: [], reason: error.message });
        }
        throw error;
      } finally {
        this.reviving = null;
      }
    })();
    return this.reviving;
  }

  /** The live part of what the player sees (D-226): body, place, pocket, the road. */
  async look(): Promise<LiveLook> {
    const answer = await this.send("look");
    return answer.look as LiveLook;
  }

  /** One of the slow parts, by name; the client keeps it until an event touches it. */
  async part<K extends keyof Parts>(name: K): Promise<Parts[K]> {
    const answer = await this.send(PART_COMMANDS[name]);
    return answer[PART_ANSWERS[name]] as Parts[K];
  }

  /** Every slow part at once: the first read, and the reread after `session.reread`. */
  async parts(): Promise<Parts> {
    const [knowledge, profile, orders, deeds, shelf] = await Promise.all([
      this.part("knowledge"),
      this.part("profile"),
      this.part("orders"),
      this.part("deeds"),
      this.part("shelf"),
    ]);
    return { knowledge, profile, orders, deeds, shelf };
  }

  async close(): Promise<void> {
    if (this.reviveTimer) {
      clearTimeout(this.reviveTimer);
      this.reviveTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }
}

async function read<T>(path: string): Promise<T> {
  const answer = await fetch(HTTP + path);
  if (!answer.ok) throw new Error(`${path}: ${answer.status}`);
  return answer.json();
}

export const constants = () => read<{ digest: string; values: Record<string, any> }>(
  "/public/constants",
);
export const recipes = () => read<RecipeBook>("/public/recipes");
/** Doors into the world: read before identification -- a newcomer has no identity yet. */
export const doors = () => read<{ doors: Door[] }>("/public/doors");
/** Character lines and the number of players -- also before identification (D-187). */
export const lines = () => read<{ lines: Line[] }>("/public/lines");
export const tiers = () => read<{ tiers: { from: number; to: number; name: string }[] }>(
  "/public/quality/tiers",
);
export const worldMap = () => read<WorldMap>("/public/map");
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
  read<{ node: string; positions: { goods: string; tier: string }[] }>(
    `/public/market/${encodeURIComponent(node)}`,
  );
export const book = (node: string, goods: string, tier: string) =>
  read<Book>(
    `/public/market/${encodeURIComponent(node)}/book` +
      `?goods=${encodeURIComponent(goods)}&tier=${encodeURIComponent(tier)}`,
  );

/**
 * The vault's recipe book as `/public/recipes` serves it -- the fields the
 * client reads (mirrors `constants/catalog.py`; the server sends more).
 */
export type Recipe = {
  name: string;
  level: number;
  kind: string;
  roles: boolean;
  food: boolean;
  inputs: string[];
  amounts: Record<string, number>;
  station?: string;
  /** Capacity as a storage, kg (D-181); `holds` says what it admits (D-230). */
  store?: number | null;
  holds?: string | null;
};

export type Operation = {
  name: string;
  requires: string[];
  gives: string[];
  consumes: string[];
  place?: string;
};

export type RecipeBook = {
  bulk: string[];
  /** Liquids (D-230): they exist only inside a vessel, never loose in the hands. */
  liquid?: string[];
  /**
   * Things no recipe makes: world raw material and operation products (D-215).
   * `/public/recipes` has always sent them; they are typed here because the
   * alpha widget prints by name and a name is either a material or a recipe's
   * output -- deriving the list beats a second server key for it (D-225).
   */
  /**
   * Everything that is not made by a recipe (D-215). `relic` marks what the
   * Forerunners left (D-232): it is machinery, but nobody makes it, takes it
   * down or carries it away -- and the client must not offer to.
   */
  materials: { name: string; class?: string | null; relic?: boolean }[];
  units: Record<string, string>;
  operations: Operation[];
  recipes: Recipe[];
  classes: Record<string, string[]>;
  tool_classes: Record<string, string[]>;
  synonyms: Record<string, string>;
  /** The world's constants ride along (D-209): one book through every panel. */
  constants?: Record<string, number>;
};

/** Money comes in minor units: 1 TC = 10 000. Not a cent is lost. */
export const MONEY_SCALE = 10_000;
export const tk = (minor: number) => (minor / MONEY_SCALE).toFixed(2).replace(/\.?0+$/, "");
export const minor = (tk: number) => Math.round(tk * MONEY_SCALE);

/**
 * The plot's building block, or an empty yard where the server sent none:
 * the windows that build and place machines count from zero on bare land.
 */
export function houseOf(node: Look["node"]): NonNullable<NonNullable<Look["node"]>["building"]> {
  return (
    node?.building ?? {
      area: 0, ground: 0, floors: 0, decay: 0, slots: 0, used: 0, sites: [],
    }
  );
}

/**
 * Kinds of things standing in the node, one name per kind: machines and
 * furniture together. The node scene is built from them (D-176), and the
 * windows ask them by class. Assembled here, not sent: `bench` and
 * `furniture` already name every instance.
 */
export function stationsOf(look: Pick<Look, "bench" | "furniture">): string[] {
  const names = new Set<string>();
  for (const thing of [...(look.bench ?? []), ...(look.furniture ?? [])]) names.add(thing.goods);
  return [...names].sort();
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
