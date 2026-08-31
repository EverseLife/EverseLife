// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The language the client speaks (D-249, D-251 wave III).
 *
 * The server no longer writes sentences into the code that produces them: the
 * engine raises a **key** with arguments and `src/i18n` assembles the sentence
 * in the language of whoever is listening. The client is the other end of the
 * same pipe. `/public/i18n/{locale}` hands over the very FTL the server
 * renders, so a message formatted here and the same message formatted there
 * come out identical -- one file, two runtimes.
 *
 * Three things follow from "identical", and all three are settings, not taste:
 *
 * - **no isolation marks.** Fluent wraps interpolations in invisible FSI/PDI
 *   control characters by default; the server turns that off, so this end must
 *   too, or the two strings would differ by characters nobody can see;
 * - **`NAME($id)` is ours to provide.** A message names a thing by its D-251
 *   id -- `NAME($goods)` -- and the bundle turns it into a word. The server
 *   looks the id up in the rename table; here the same table arrives as
 *   `/public/renames`, and the lookup order matches `constants.display_name`;
 * - **nothing here throws.** A missing key falls back to the key itself, a
 *   broken bundle to an empty one. A refusal that crashes while being rendered
 *   would hide the refusal, which is worse than showing `goods-not-enough`.
 *
 * There is a module-level "current language" cell, like `amounts.learn`: the
 * sorting helpers below are called from deep inside pure functions
 * (`arrange`, `market`, `recipes`) that have no business carrying a locale
 * argument through six layers. React reads the same value through
 * `useLocale()` in `actions.tsx`, which is what makes a switch re-render.
 */

import {
  FluentBundle,
  FluentResource,
  type FluentFunction,
  type FluentValue,
  type FluentVariable,
} from "@fluent/bundle";

import type { Names } from "./names";

/**
 * The language spoken until the server says otherwise, and the one every
 * unknown language falls back to. Russian is not "the original" -- D-249 makes
 * the languages equal -- it is the one that is complete today.
 */
export const DEFAULT_LOCALE = "ru";

/** What `/public/i18n/{locale}` answers with. */
export type WordsBundle = {
  /** The language actually served: the server normalises what was asked for. */
  locale: string;
  /** Every language that exists. The switcher must not guess (D-251). */
  locales: string[];
  /** The FTL source as written, the same text the server renders from. */
  ftl: string;
};

/**
 * Which domains of the names bundle each message function looks in, in order.
 *
 * The mirror of `constants.NAME_DOMAINS`, and the order is load-bearing rather
 * than tidy: ids collide between domains -- `stone` is «Камень» among goods and
 * «каменный» among building kinds -- so one merged lookup would render one of
 * them wrong and never say so. That is also why there is a function per
 * namespace instead of a single `NAME`.
 */
const NAME_DOMAINS: Record<string, readonly (keyof Names)[]> = {
  NAME: ["goods", "virtual_stations", "classes", "operations", "node_properties"],
  KIND: ["building_kinds"],
  PLANET: ["planets"],
  TIER: ["tiers"],
  SLOT: ["slots"],
  //: Not `PLANT`, which reads as `PLANET` at a glance in a message file.
  CULTURE: ["plants"],
};

/**
 * The plural of the two that are handed lists -- what a machine may burn, what
 * a house may be built from. Fluent takes no list argument, so the ids arrive
 * joined and are split here; the separator belongs to the language, not to the
 * engine. Mirrors `i18n.LIST_FUNCTIONS`.
 */
const LIST_FUNCTIONS: Record<string, string> = { NAMES: "NAME", KINDS: "KIND" };

/** What separates the ids on the way in, and the words on the way out. */
const LIST_IN = ",";
const LIST_OUT = ", ";

/**
 * Every function a served message may call, ours and Fluent's own.
 *
 * A message that calls something the bundle does not have renders as the
 * literal `{FUNCTION($arg)}` -- and, since a refusal is drawn from `code` in
 * preference to the server's own words, the player would be shown that
 * instead of a sentence that had arrived perfectly correct. The drift test
 * reads this.
 */
export const MESSAGE_FUNCTIONS: readonly string[] = [
  ...Object.keys(NAME_DOMAINS),
  ...Object.keys(LIST_FUNCTIONS),
  //: Fluent's builtins: the bundle brings these itself.
  "NUMBER",
  "DATETIME",
];

/** What a message function was handed: a plain string, or a Fluent value. */
function idOf(value: FluentValue | undefined): string {
  if (value == null) return "";
  return typeof value === "string" ? value : String(value.valueOf());
}

/** The whole family of id-to-word functions, bound to one language's names. */
function messageFunctions(names: Names | null): Record<string, FluentFunction> {
  /** One id to one word, through the domains that function looks in. */
  const wordFor = (fn: string) => (id: string) => {
    for (const domain of NAME_DOMAINS[fn] ?? NAME_DOMAINS.NAME) {
      const found = names?.[domain]?.[id];
      if (found) return found;
    }
    //: An id the table does not know reads as the id. Ugly and honest: the
    //: alternative is a hole in the middle of a sentence.
    return id;
  };

  const built: Record<string, FluentFunction> = {};
  for (const fn of Object.keys(NAME_DOMAINS)) {
    const say = wordFor(fn);
    built[fn] = (positional) => say(idOf(positional[0]));
  }
  for (const [many, one] of Object.entries(LIST_FUNCTIONS)) {
    const say = wordFor(one);
    built[many] = (positional) =>
      idOf(positional[0])
        .split(LIST_IN)
        .map((part) => part.trim())
        .filter(Boolean)
        .map(say)
        .join(LIST_OUT);
  }
  return built;
}

/** The words of one language, parsed and ready to render. */
/**
 * The client's own words, bundled rather than fetched.
 *
 * The served bundle is the world's voice -- refusals, occupations, the digest
 * -- and it arrives over the wire because the engine owns it. These are the
 * window's own words: headings, buttons, captions. They ship with the very
 * build that draws them, so there is no version of the client whose words
 * belong to a different one, and no hundred kilobytes between the player and
 * their first screen.
 *
 * Keys are prefixed `ui-`, so the two sets cannot collide in one bundle.
 */
const OWN = import.meta.glob("./locales/*/*.ftl", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** The files of one language, in the order the glob found them. */
function filesOf(locale: string): string[] {
  return Object.entries(OWN)
    .filter(([path]) => path.includes(`/locales/${locale}/`))
    .map(([, source]) => source);
}

/**
 * Everything this language says on its own, falling back to the default one.
 *
 * The fallback is not a nicety. The server may know a language before this
 * half of it is written -- which is the shape of wave V exactly, where the
 * engine gains English first -- and without it `src/locales/en/` merely not
 * existing yet would render the whole shell as bare keys: not English, not
 * Russian, `ui-side-doings-none`. Falling back leaves the untranslated half in
 * the language that is complete, beside the half that is already translated,
 * which is the honest state to be in halfway through a translation.
 *
 * Both are loaded and the new language goes first: `addResource` keeps the
 * first definition it sees, so a message the new language does have wins over
 * the same message in the old one.
 */
function ownWords(locale: string): string {
  const own = filesOf(locale);
  const fallback = locale === DEFAULT_LOCALE ? [] : filesOf(DEFAULT_LOCALE);
  return [...own, ...fallback].join("\n");
}

export class Words {
  readonly locale: string;
  readonly locales: string[];
  /**
   * Whether this language has its display names as well as its messages.
   *
   * The two are separate reads and either can fail on its own. Without the
   * names every `NAME($goods)` renders `iron_ore`, so a message drawn here
   * would be strictly worse than the one the server already sent.
   */
  readonly named: boolean;
  private readonly bundle: FluentBundle;

  constructor(answer: WordsBundle, names: Names | null) {
    this.locale = answer.locale || DEFAULT_LOCALE;
    //: A server that answered with nothing still leaves the player one
    //: language to be in: their own.
    this.locales = answer.locales?.length ? [...answer.locales] : [this.locale];
    this.named = names != null;
    this.bundle = new FluentBundle([this.locale], {
      //: Invisible control characters around every interpolation would make
      //: the client's sentence differ from the server's by bytes nobody can
      //: see. The server sets `use_isolating=False`; so do we.
      useIsolating: false,
      functions: messageFunctions(names),
    });
    //: Our own words first: they are always here, whatever the wire did.
    const own = ownWords(this.locale);
    if (own) {
      for (const error of this.bundle.addResource(new FluentResource(own))) {
        console.warn(`${this.locale}: ${error.message}`);
      }
    }
    if (answer.ftl) {
      //: Fluent drops a line it cannot parse in silence, so a typo costs one
      //: message. The server fails its boot on that; here the boot is the
      //: player's evening, so it is a warning and the rest still works.
      const errors = this.bundle.addResource(new FluentResource(answer.ftl));
      for (const error of errors) console.warn(`${this.locale}: ${error.message}`);
    }
  }

  /** Does this language have that message at all? */
  has(key: string): boolean {
    return this.bundle.hasMessage(key);
  }

  /**
   * The sentence for this key, in this language.
   *
   * Falls back to the key -- a player seeing `storage-chest-full` is a visible
   * bug report, and a visible bug report beats an exception thrown out of a
   * render.
   */
  t(key: string, args?: Record<string, unknown> | null): string {
    const message = this.bundle.getMessage(key);
    if (!message?.value) return key;
    const errors: Error[] = [];
    //: The arguments come off the wire as JSON, so their type is `unknown`
    //: until Fluent looks at them; it accepts strings, numbers and dates and
    //: complains into `errors` about anything else.
    const given = (args ?? null) as Record<string, FluentVariable> | null;
    const text = this.bundle.formatPattern(message.value, given, errors);
    for (const error of errors) console.warn(`message ${key} in ${this.locale}: ${error.message}`);
    return text;
  }
}

/** An empty language: what stands here before the first bundle arrives. */
function silence(locale: string): Words {
  return new Words({ locale, locales: [locale], ftl: "" }, null);
}

let spokenWords = silence(DEFAULT_LOCALE);
let collator: Compare | null = null;

/** Make these the words the client speaks. The React side re-renders on its own. */
export function learn(words: Words): void {
  spokenWords = words;
  collator = null;
}

/** Back to nothing: what logging out leaves behind for the next account. */
export function forget(): void {
  learn(silence(DEFAULT_LOCALE));
}

/** The current language's words, for whoever wants more than `t`. */
export function spoken(): Words {
  return spokenWords;
}

/** The language the client is speaking right now. */
export function currentLocale(): string {
  return spokenWords.locale;
}

/** The sentence for this key in the current language. Never throws. */
export function t(key: string, args?: Record<string, unknown> | null): string {
  return spokenWords.t(key, args);
}

/**
 * The message that tells about an event of this kind.
 *
 * A dot is not allowed in a Fluent message name and an event's kind is full
 * of them (`craft.finished`), so the dots become dashes and nothing else
 * changes -- an underscore is a legal identifier character and stays. The
 * same rule as the server's `i18n.event_key`, and the server checks its own
 * allowlist against the lines, so the two ends cannot drift apart.
 */
export function eventKey(kind: string): string {
  return "event-" + kind.replaceAll(".", "-");
}

/** Two display words, ordered. What every list the player picks from sorts by. */
export type Compare = (a: string, b: string) => number;

/**
 * The reading order of one language.
 *
 * Every list the player picks from is sorted by what they read, not by the
 * ASCII of the ids underneath, and "what they read" has a different order in
 * every language -- which is exactly why this must not be a hardcoded `"ru"`.
 *
 * Never throws, and that matters here more than anywhere else in this file:
 * `Intl.Collator` raises `RangeError` on a tag it cannot parse, and this runs
 * in the middle of rendering a list. A language read in the wrong order is a
 * nuisance; a blank screen is a broken client.
 */
export function collatorFor(locale: string): Compare {
  for (const tag of [locale, DEFAULT_LOCALE]) {
    try {
      //: The `compare` of a collator is a bound function by specification, so
      //: it is safe to carry away from the object that made it.
      return new Intl.Collator(tag).compare;
    } catch {
      /* тег языка, которого не бывает: следующий кандидат */
    }
  }
  //: No `Intl` in this browser at all. Code-point order is wrong in every
  //: language and still a stable one, which is what a list needs most.
  return (a, b) => (a < b ? -1 : a > b ? 1 : 0);
}

/** The reading order of the current language, for code outside React. */
export function compare(a: string, b: string): number {
  collator ??= collatorFor(spokenWords.locale);
  return collator(a, b);
}

/**
 * The sentence a refusal is shown as (D-251 wave III).
 *
 * The server sends the words it rendered, and -- where the refusal site has
 * been converted -- the key and arguments it rendered them from. Rendering
 * them again here is the point of the whole wave: one FTL file, two runtimes,
 * and if the two ends ever disagreed this is where it would show.
 *
 * Drawing it here has to be strictly better than keeping what arrived, so it
 * takes **both** halves of the language: the messages and the names. They are
 * two separate reads and either can fail alone -- and a client with the FTL
 * but no names would turn «Железная руда», which the server had already got
 * right, into `iron_ore`. A key we do not have, or a half we are missing,
 * keeps the server's own sentence.
 */
export function refusalText(
  refused: string,
  code?: string | null,
  args?: Record<string, unknown> | null,
): string {
  if (code && spokenWords.named && spokenWords.has(code)) {
    return spokenWords.t(code, quoted(args));
  }
  return refused;
}

/** One quoted message on the wire: a key and, if it has any, its arguments. */
type Quoted = { code: string; args?: Record<string, unknown> };

function isQuoted(value: unknown): value is Quoted[] {
  //: An empty list counts: it is "nothing to quote", and it must render to
  //: nothing rather than fall through to Fluent, which takes no array at all
  //: and would print the variable's own name into the sentence.
  return (
    Array.isArray(value) &&
    value.every(
      (one) => typeof one === "object" && one !== null && typeof (one as Quoted).code === "string",
    )
  );
}

/**
 * A refusal's arguments with its quoted halves said rather than passed on.
 *
 * Some refusals quote another one -- «тело занято: идёт разведка (ещё 12 мин)»,
 * «для города не хватает: биопринтер, рынок». The engine sends those halves as
 * **keys**, so that each end says them in its own language, and they arrive as
 * `{"what": [{"code": "doing-field-what"}]}`.
 *
 * Fluent takes strings, numbers and dates and nothing else. Handed that array
 * it prints the literal `{$what}` into the sentence and complains into an
 * error list nobody reads -- and because a refusal is drawn from `code` in
 * preference to the server's own words, the player was shown `{$what}` instead
 * of a sentence that had arrived perfectly correct.
 *
 * So the quoted halves are rendered first, exactly as the server's `_refused`
 * does, and joined by the language's own separator -- the one `NAMES()` uses,
 * so a quoted list and a list of names read alike.
 */
function quoted(args?: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!args) return args ?? null;
  let said: Record<string, unknown> | null = null;
  for (const [name, value] of Object.entries(args)) {
    if (!isQuoted(value)) continue;
    said ??= { ...args };
    said[name] = value.map((one) => spokenWords.t(one.code, one.args ?? null)).join(LIST_OUT);
  }
  return said ?? args;
}

/**
 * Fetch the words of a language and build them.
 *
 * Forgiving on purpose: a server too old to have `/public/i18n` -- or one that
 * answered with a 500 -- must not stop a login. The player gets an empty
 * language, every `t` falls back to its key, and the rest of the client, which
 * is still full of Russian written in place until wave IV, reads as before.
 */
export async function loadWords(locale: string, names: Names | null): Promise<Words> {
  const asked = locale || DEFAULT_LOCALE;
  try {
    //: Deferred on purpose, and the only such import here. Two reasons, and
    //: the first is the binding one: `api` reads `window.location` while it is
    //: being evaluated, and this module has to stay importable without a DOM,
    //: because `arrange`, `market` and `recipes` reach it for their sort order
    //: and those are tested in node. The second: `api` imports `compare` from
    //: here, so a static edge back would close a cycle.
    //:
    //: The build says the import "will not move module into another chunk",
    //: which is true and wanted: `api` belongs in the main chunk, and nothing
    //: here is trying to split it out.
    const api = await import("./api");
    return new Words(await api.words(asked), names);
  } catch (error) {
    console.warn(`words for ${asked} not loaded:`, error);
    return silence(asked);
  }
}
