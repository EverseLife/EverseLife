// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The city as a polity: who decides, by what rule, and out of whose purse.
 *
 * A city in this world is not a place -- the place is a node -- it is an
 * authority over one, and every shape here is a part of that authority. The
 * charter and the code-laws are what has been decided, a vote is a decision
 * being made, an office is who may make it, a court case is what happens when
 * somebody will not abide by it, and the panel, the works board and the loans
 * are the same authority counted in money.
 *
 * The three constants at the end are the client's half of the same subject:
 * the names of the broad rights, the prefix that makes a narrow one, and the
 * limit the field must not let a mayor exceed before the server refuses it
 * (D-183).
 */

/**
 * One line of the founding threshold: a role a settlement cannot live without,
 * and the machines any one of which fills it.
 *
 * A catalog constant -- the same for every player, every place and every
 * language -- so it comes from `/public/founding` and not from `look`, which
 * carries only the keys of the roles the node one stands in still lacks
 * (D-225). The role is a key; its word is the world's own message,
 * `city-role-<role>`, the very one the door's refusal quotes.
 */
export type FoundingRole = {
  role: string;
  any_of: string[];
};

/**
 * A code-law as it stands in the city: the value in force, and whether the
 * city decided it or the vault did. Nothing else -- the name, the unit and
 * the note are vault text, held by id in the names table in every language
 * (`lawName`, `lawUnit`, `lawNote`); a copy on the wire would be a second
 * list of the same words, and those drift (D-225).
 */
export type Law = {
  value?: string;
  own: boolean;
};

/** The choice that means "switched off" in every law that is a choice: the
 *  city prints for nobody, nobody may take the rings. Named here because two
 *  windows read it and neither should spell it out. */
export const NOBODY = "nobody";

/**
 * The law book out of `/public/laws`: the catalog half, which the city's own
 * answer no longer repeats. Only the option **ids** are read -- a law that has
 * them is picked from a list, one that has none is typed -- and the word for
 * each comes from the names table (`lawOption`), not from here: the label the
 * vault ships is one language, and the table holds every one.
 */
export type LawBook = {
  code_laws: { id: string; options?: { id: string }[] }[];
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
  /** The world's one mint (D-270): present only on the capital. */
  capital?: boolean;
  /** The emission counter (D-270), with the capital: how many hands hold the
   *  right, how many print, and the proposal collecting signatures if one stands. */
  emission?: {
    holders: number;
    needed: number;
    proposal?: {
      id: string;
      /** Minor units, like the treasury. */
      money: number;
      who: string;
      signed: number;
      expires_at: string;
      mine: boolean;
    };
  };
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
  /** Candidates in the election: they nominate themselves while the poll runs (D-162).
   *  `own` marks the asker: a name is not an identity, so the client cannot tell.
   *  Not `mine` -- the poll's own `mine` below is a ballot, not a person. */
  candidates: { id: string; name?: string; votes: number; own: boolean }[];
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
    /** Lent to its own citizens and not yet back (D-283): money that is out,
     *  not money that is gone -- the difference an empty balance hides. */
    lent: number;
    collected: Record<string, number>;
    spent: Record<string, number>;
  };
};

/** One order on the state works board (D-248): what the fund pays for now. */
export type WorksOrder = {
  id: string;
  kind: "road_mend" | "building_repair" | "building_build" | "fuel_delivery";
  tariff: number;
  posted_at: string;
  /** Kind-specific details; keys the kind does not need are absent (D-225). */
  about: {
    surface?: string;
    building_kind?: string;
    footprint?: number;
    floors?: number;
    type_key?: string;
    left?: number;
  };
  edge?: string;
  /** Road orders: the names of the edge's two ends. */
  between?: [string | null, string | null];
  node?: string | null;
};

export type WorksBoard = { orders: WorksOrder[]; fund: number };

/** The treasury's own loans and the city line with the capital (D-175, D-248). */
export type CityLoans = {
  line: { permitted: number; occupied: number; free: number };
  loans: { id: string; principal: number; outstanding: number; rate: number; taken_at: string }[];
};

/** Broad rights. Narrow ones -- `law:<id>` -- are assembled from the law catalog.
 *
 * Message **keys**, not words: this map is built once when the module is first
 * evaluated, and a `t()` here would freeze whatever language was being spoken
 * at that moment for the rest of the session. The caller says the word. */
export const POWERS: Record<string, string> = {
  laws: "ui-power-laws",
  charter: "ui-power-charter",
  treasury: "ui-power-treasury",
  offices: "ui-power-offices",
  land: "ui-power-land",
  dashboard: "ui-power-dashboard",
  justice: "ui-power-justice",
  citizens: "ui-power-citizens",
  channel: "ui-power-channel",
  emission: "ui-power-emission",
};

/** The right to one law: `law:import_duty` (D-155). */
export const LAW_SCOPE = "law:";

/** The limit of the city's word (D-183). The server counts it (`runtime.CITY_ABOUT_LIMIT`);
 *  it is here so that the field does not let one type what is refused in advance. */
export const CITY_ABOUT_LIMIT = 300;

/** The limit of the city's name. The server counts it (`runtime.CITY_NAME_LIMIT`);
 *  it is here for the same reason as the word's -- so the field stops where the
 *  refusal would. The city's official channel is named after the city, which is
 *  why this is no higher than a channel's own limit. */
export const CITY_NAME_LIMIT = 40;
