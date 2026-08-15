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
  quality: number | null;
  tier: string;
  condition: number;
  /** Dish kind: the combination decides the kind, not the quality (D-128). */
  flavor: string | null;
  /** Edibility comes from vault data, not the client's guesses. */
  food: boolean;
  /** Fits the pot: a product, not a pickaxe (16-cooking). */
  ingredient: boolean;
  spoils_at: string | null;
  /** Coin fineness in thousandths: a coin has no quality, it has metal (D-016). */
  fineness: number | null;
  /** The mark: whose work this is (D-058). */
  maker: string | null;
  /** For seeds: cultivar and batch strength, % (D-057). */
  variety: string | null;
  vigor: number | null;
  /** For a battery: charge with self-discharge (D-071). */
  charge: number | null;
  /** Unit weight, kg, and the slot if this is gear (D-146). */
  mass: number;
  slot: string | null;
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
  capacity: number | null;
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
  cargo: { id: string; type_key: string; amount: number; quality: number | null }[];
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
  next: "road" | "paved" | null;
  /** How much surface laying a tier takes, and how much resurfacing does. */
  needs: number | null;
  mend_needs: number | null;
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
  exit: boolean;
};
export type MapEdge = { a: string; b: string; surface: Exit["surface"]; seconds: number };
export type WorldMap = { nodes: MapNode[]; edges: MapEdge[] };

/** A remark as heard by someone standing in the location (D-043, D-050). */
export type ChatLine = {
  id: string;
  who: string;
  kind: "speech" | "action" | "ooc";
  quiet: boolean;
  text: string;
  overheard: boolean;
  source: string | null;
  at: string;
};

/** A circle: membership visible, content not. */
export type Circle = { id: string; name: string | null; members: string[]; mine: boolean };

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
  /** Which species is requested, if any. */
  resource?: string | null;
};

/** Account panel (D-187): self-description next to the name. Nothing game-related here. */
export type Profile = {
  email: string | null;
  /** The name is unique and unchangeable (D-011): reputation rests on it. */
  name: string;
  surname: string;
  age: number | null;
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

export type Look = {
  identity: string;
  profile: Profile;
  money: string;
  knows: string[];
  /** Learned agrotech: crops whose norm the identity has already studied (D-057). */
  agrotech: string[];
  orders: Order[];
  reservations: Reservation[];
  batches: Batch[];
  carry?: Carry;
  body: {
    id: string;
    stamina: number;
    sleeping_since: string | null;
    sleeping_home: boolean;
    /** Until this moment the stamina spend is reduced: a meal, not a buff (D-119). */
    satiated_until: string | null;
  } | null;
  node: {
    key: string;
    name: string;
    layer: "space" | "planet" | "city" | "location";
    library: boolean;
    stations: string[];
    /** Place-sign properties ("forest", "outcrop"): place extraction is shown by them (D-177). */
    features: string[];
    fertility: number;
    /** Whose plot: the holder runs the estate (06-farming). */
    owner: string | null;
    /** The owning city, if the land is civic: ownership is public (D-178). */
    owner_city: string | null;
    mine: boolean;
    city: boolean;
    /** Whether the viewer may name the plot (D-178). */
    may_name: boolean;
    /** Unowned and wild: such is taken in person (D-152). */
    wild: boolean;
    /** Disconnected for non-payment: machines do not work (D-149). */
    cut_off: boolean;
    /** Plot area, m2 (D-125). */
    area: number;
    /** Building and capacity: a machine takes area (D-106). */
    building: { area: number; slots: number; used: number };
    /** Purchase price of an empty civic plot, in minor units (D-089). */
    price: number | null;
  } | null;
  /** The city whose territory we stand on, and our own rights in it (D-154, D-155). */
  city?: {
    id: string;
    name: string;
    node: string;
    /** Rights as strings: broad (`treasury`) and narrow (`law:import_duty`). */
    powers: string[];
    /** Whether the administration stands here: decisions are made in it (D-155). */
    hall: boolean;
    /** Whether a citizen of this city (D-160). */
    citizen: boolean;
    /** How they admit: open, by application or by invitation. */
    admission: "open" | "application" | "invite";
    /** An application filed or an invitation received -- waits for its side. */
    requested: boolean;
  } | null;
  /** Where the identity belongs: citizenship is one and visible from everywhere (D-160). */
  citizenship?: {
    city: string | null;
    since: string;
    /** An exit declaration is filed: citizenship lapses by this date. */
    leaving_at: string | null;
    /** An obligation taken as a print condition: no leaving before this date (D-184). */
    bound_until: string | null;
  } | null;
  /** An ongoing exploration run, if any (D-152). */
  survey?: { returns_at: string } | null;
  /** The planet's clock: where the count starts and how long a day is (D-029). */
  clock?: { planet: string; epoch: string | null; day_hours: number };
  /** Whether a city can be founded here and what is missing for that (D-159).
   *  Empty -- the place is unsuitable: foreign land, a foreign city or not a planet. */
  foundation?: {
    missing: string[];
    needs: { role: string; any_of: string[] }[];
  } | null;
  /** Own convoy: what it is harnessed to and what it carries (D-157). */
  convoy?: Convoy | null;
  /** Vehicles standing in this node: one harnesses to what is nearby. */
  vehicles?: Vehicle[];
  /** No body -- the identity is in the cloud: where to print and whether a print is ongoing (D-033). */
  printers?: Printer[];
  printing?: { ready_at: string } | null;
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
    /** Whether the viewer may take from here. */
    mine: boolean;
  };
  /** Own deeds for plots: electronic documents of the Net (D-116). */
  deeds?: DeedView[];
  inventory: Thing[];
  stall?: Thing[];
  veins?: { id: string; resource: string; richness: number }[];
  exits?: Exit[];
  travel?: Transit | null;
  /** An open face survives the player leaving: on return the session is in place. */
  mining?: Sight | null;
};

/** A deed for a plot: ownership documented (D-116). */
export type DeedView = {
  id: string;
  node: string | null;
  name: string | null;
  area: number | null;
  owner: string | null;
  /** The issue price: the purchase price, zero for taken wild land. */
  paid: number;
  /** Listed for sale: the price and the addressee, if the contract is addressed. */
  sale_price: number | null;
  sale_to: string | null;
  issued_at: string;
};

/** A machine in the node: one person works at a machine (D-150). */
export type Bench = {
  id: string;
  goods: string;
  quality: number | null;
  condition: number;
  busy: boolean;
  mine: boolean;
  /** Charge belongs to the battery standing here as a machine (D-179). */
  charge: number | null;
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
  city: string | null;
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
  unit: string | null;
  note: string | null;
  value: string | null;
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
  lots: { key: string; name: string; area: number; owner: string | null; free: boolean }[];
  citizens: string[];
};

/** The city's economic panel (D-124, D-140). The public snapshot is visible to all. */
/** A case in the city court (D-166). */
export type CourtCase = {
  id: string;
  plaintiff: string | null;
  defendant: string | null;
  claim: string;
  state: "open" | "judged" | "dismissed";
  verdict: string | null;
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
  law: string | null;
  value: unknown;
  /** Candidates in the election: they nominate themselves while the poll runs (D-162). */
  candidates: { id: string; name: string | null; votes: number }[];
  /** Whom one's own vote in the election is for. */
  choice: string | null;
  closes_at: string;
  threshold: "simple" | "two_thirds" | "unanimous";
  /** The share of eligible voters needed for a quorum; 0 -- no quorum required. */
  quorum: number;
  electorate: number;
  yes: number;
  no: number;
  /** Own vote, if cast. */
  mine: boolean | null;
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
  ready_at: string;
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
const TOKEN_KEY = "octoverse.token";

/** The client session. Holds the socket and the "command -> reply" queue.
 *
 * The socket does not live forever: the server and proxies cut idle
 * connections. A broken session rises by itself -- a command that finds a
 * dead socket first reconnects and identifies by token, and only then goes.
 *
 * Identification is email and password (D-187). The password is entered
 * once: the server gives a token, it lives in `localStorage`, and by it the
 * session rises after F5 and after a break. Logging out of the account panel
 * revokes and forgets the token.
 */
export class Session {
  private socket: WebSocket | null = null;
  private queue: Waiting[] = [];
  private reviving: Promise<void> | null = null;
  account = "";
  name = "";
  token = "";

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

  /** Bring up the socket. Identification is a separate step: a newcomer has nothing to identify with yet. */
  private async connect(): Promise<void> {
    await this.close();
    const socket = new WebSocket(WS);
    this.socket = socket;

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error("сервер не отвечает"));
    });

    socket.onmessage = (event) => {
      const waiting = this.queue.shift();
      if (!waiting) return;
      const answer = JSON.parse(event.data);
      if (typeof answer.refused === "string") {
        waiting.reject(new Refused(answer.refused));
      } else {
        waiting.resolve(answer);
      }
    };
    socket.onclose = () => {
      this.queue.forEach((w) => w.reject(new Error("сессия закрыта")));
      this.queue = [];
    };
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
    await this.close();
  }

  private async greet(
    cmd: string,
    args: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    const hello = await this.send(cmd, args);
    this.account = String(hello.account ?? "");
    this.name = String(hello.hello ?? "");
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
    return new Promise((resolve, reject) => {
      this.queue.push({ resolve, reject });
      socket.send(JSON.stringify({ cmd, ...args }));
    });
  }

  /** Bring a broken session back up. One rise for everyone who caught it. */
  private revive(): Promise<void> {
    this.reviving ??= (async () => {
      try {
        await this.connect();
        await this.greet("hello", { token: this.token });
      } finally {
        this.reviving = null;
      }
    })();
    return this.reviving;
  }

  async look(): Promise<Look> {
    const answer = await this.send("look");
    return answer.look as Look;
  }

  async close(): Promise<void> {
    this.socket?.close();
    this.socket = null;
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
export const recipes = () => read<any>("/public/recipes");
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

/** Money comes in minor units: 1 TC = 10 000. Not a cent is lost. */
export const MONEY_SCALE = 10_000;
export const tk = (minor: number) => (minor / MONEY_SCALE).toFixed(2).replace(/\.?0+$/, "");
export const minor = (tk: number) => Math.round(tk * MONEY_SCALE);
