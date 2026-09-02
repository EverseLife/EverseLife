// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The locale layer (D-251 wave III): rendering, the fallback, `NAME()` and
 *  the reading order the lists are sorted in. */

import { afterEach, describe, expect, it, vi } from "vitest";

//: `api` pulls in `host`, which reads `window.location` while it is being
//: evaluated, so the real
//: module cannot be loaded in node at all. `loadWords` reaches it through a
//: deferred import, and this stands in for it -- which is also the only way to
//: drive the branch that decides whether an older server can still be logged
//: into.
vi.mock("../api", () => ({ words: vi.fn() }));

import { words as fetchWords, type RecipeBook } from "../api";
import { orderGroups } from "../arrange";
import {
  DEFAULT_LOCALE,
  Words,
  collatorFor,
  compare,
  currentLocale,
  eventKey,
  forget,
  learn,
  MESSAGE_FUNCTIONS,
  loadWords,
  refusalText,
  spoken,
  t,
  type WordsBundle,
} from "../locale";
import { catalogue } from "../market";
import type { Names } from "../names";

/** A rename table with just enough in it for `NAME()` to have work to do. */
const NAMES = {
  //: `stone` sits in two domains on purpose: it is the collision the one
  //: function per namespace exists to keep apart.
  goods: { iron_ore: "Железная руда", clay: "Глина", stone: "Камень", beans: "Зерно" },
  classes: { pickaxe: "Кирка" },
  operations: { logging: "Рубка дерева" },
  slots: { back: "спина" },
  tiers: { fine: "отличное" },
  building_kinds: { stone: "каменный", wooden: "деревянный" },
  node_properties: { woods: "лес" },
  planets: { terra: "Терра", pyroxis: "Пироксис" },
  plants: { beans: "Бобы", spelt: "Полба" },
  virtual_stations: { hands: "Руки" },
  //: Code-laws are a domain of their own for the same reason: their ids are
  //: short and general -- `access`, `salary`, `toll` -- and one table shared
  //: with goods would one day answer with the wrong one.
  laws: { tax_trade: "Налог с продажи", toll: "Пошлина за проход" },
} as Names;

/** The shapes the server's own FTL uses: plain, `$arg`, `NAME()`, a selector. */
const FTL = `
storage-nothing-to-put = класть нечего
session-locale-unknown = такого языка нет: { $locale }
storage-relic = «{ NAME($goods) }» — наследие Предтеч
estate-unknown-kind = «{ KIND($kind) }» — не тип здания; строят из: { KINDS($kinds) }
ship-planet-has-no-orbit = у планеты { PLANET($planet) } нет орбитального узла
market-wrong-tier = ступень «{ TIER($tier) }» не та
gear-wrong-slot = слот «{ SLOT($slot) }» занят
attention-vote-law = голосование: { LAW($law) }
farm-wrong-seeds = «{ NAME($goods) }» — не семена культуры «{ CULTURE($culture) }»
energy-wrong-fuel = годится { NAMES($fuel) }
storage-mismatch = «{ NAME($goods) }» в «{ NAME($chest) }» не кладут: { $why ->
        [vessel] тара берёт только жидкость
       *[chest] жидкость держат в таре
    }
occupation-busy = тело занято: { $what } ({ $left })
doing-field-what = идёт разведка
time-left = ещё { $minutes } мин
city-needs = для города не хватает: { $lacks }
`;

const words = (locale: string, ftl: string, names: Names | null = NAMES) =>
  new Words({ locale, locales: [locale], ftl }, names);

const RU = words(DEFAULT_LOCALE, FTL);

//: The current language is a module-level cell, like `amounts.learn`: a test
//: that moves it puts it back, or the next test reads the wrong one.
afterEach(() => learn(RU));
learn(RU);

describe("t", () => {
  it("renders a message without arguments", () => {
    expect(t("storage-nothing-to-put")).toBe("класть нечего");
  });

  it("puts an argument in", () => {
    expect(t("session-locale-unknown", { locale: "kk" })).toBe("такого языка нет: kk");
  });

  it("picks a variant, and the default one for an unknown value", () => {
    const args = { goods: "clay", chest: "iron_ore" };
    expect(t("storage-mismatch", { ...args, why: "vessel" })).toBe(
      "«Глина» в «Железная руда» не кладут: тара берёт только жидкость",
    );
    expect(t("storage-mismatch", { ...args, why: "нечто" })).toBe(
      "«Глина» в «Железная руда» не кладут: жидкость держат в таре",
    );
  });

  it("leaves no isolation marks: the server sets none either", () => {
    //: FSI and PDI would make the same message differ between the two ends by
    //: characters nobody can see.
    expect(t("session-locale-unknown", { locale: "kk" })).not.toMatch(/[⁦-⁩]/);
  });

  it("falls back to the key rather than throwing", () => {
    expect(t("goods-not-enough")).toBe("goods-not-enough");
    expect(RU.has("goods-not-enough")).toBe(false);
    expect(RU.has("storage-relic")).toBe(true);
  });

  it("renders nothing but the key when the language failed to load", () => {
    learn(words("ru", ""));
    expect(t("storage-nothing-to-put")).toBe("storage-nothing-to-put");
  });
});

describe("NAME()", () => {
  it("turns an id into the word the player reads", () => {
    expect(t("storage-relic", { goods: "iron_ore" })).toBe("«Железная руда» — наследие Предтеч");
  });

  it("looks through the same domains as the server, in the same order", () => {
    //: `display_name` tries goods, virtual stations, classes, operations,
    //: node properties -- a class id must resolve, not fall through.
    expect(t("storage-relic", { goods: "pickaxe" })).toBe("«Кирка» — наследие Предтеч");
    expect(t("storage-relic", { goods: "hands" })).toBe("«Руки» — наследие Предтеч");
    expect(t("storage-relic", { goods: "woods" })).toBe("«лес» — наследие Предтеч");
  });

  it("shows the bare id when the table has no word for it", () => {
    expect(t("storage-relic", { goods: "unheard_of" })).toBe("«unheard_of» — наследие Предтеч");
  });

  it("says the key when there is no rename table at all", () => {
    learn(words("ru", FTL, null));
    expect(t("storage-relic", { goods: "iron_ore" })).toBe("«iron_ore» — наследие Предтеч");
  });
});

describe("the rest of the message functions", () => {
  it("gives each one its own domain", () => {
    expect(t("ship-planet-has-no-orbit", { planet: "terra" })).toBe(
      "у планеты Терра нет орбитального узла",
    );
    expect(t("market-wrong-tier", { tier: "fine" })).toBe("ступень «отличное» не та");
    expect(t("gear-wrong-slot", { slot: "back" })).toBe("слот «спина» занят");
    expect(t("attention-vote-law", { law: "tax_trade" })).toBe("голосование: Налог с продажи");
  });

  it("keeps the domains apart where an id means two things", () => {
    //: `stone` is «Камень» among goods and «каменный» among building kinds.
    //: One merged lookup would render one of the two wrong without saying so.
    expect(t("storage-relic", { goods: "stone" })).toBe("«Камень» — наследие Предтеч");
    expect(t("estate-unknown-kind", { kind: "stone", kinds: "wooden" })).toBe(
      "«каменный» — не тип здания; строят из: деревянный",
    );
  });

  it("keeps a culture apart from its produce", () => {
    //: `beans` is the grain among goods and the culture among plants: «Полба»
    //: is sown, «Зерно» is harvested.
    expect(t("farm-wrong-seeds", { goods: "beans", culture: "beans" })).toBe(
      "«Зерно» — не семена культуры «Бобы»",
    );
  });

  it("splits a list on the comma and joins it back in words", () => {
    expect(t("energy-wrong-fuel", { fuel: "clay,iron_ore" })).toBe("годится Глина, Железная руда");
    //: The server writes the list with spaces as often as without.
    expect(t("energy-wrong-fuel", { fuel: "clay, iron_ore ,stone" })).toBe(
      "годится Глина, Железная руда, Камень",
    );
    expect(t("energy-wrong-fuel", { fuel: "clay" })).toBe("годится Глина");
    //: An empty list is empty, not a stray separator.
    expect(t("energy-wrong-fuel", { fuel: "" })).toBe("годится ");
    expect(t("estate-unknown-kind", { kind: "wooden", kinds: "stone, wooden" })).toBe(
      "«деревянный» — не тип здания; строят из: каменный, деревянный",
    );
  });

  it("renders no message as a literal function call", () => {
    //: The shape of the defect this guards: an unregistered function is left
    //: in the output verbatim, and a refusal drawn from `code` would show it.
    for (const key of [
      "estate-unknown-kind",
      "ship-planet-has-no-orbit",
      "market-wrong-tier",
      "gear-wrong-slot",
      "farm-wrong-seeds",
      "energy-wrong-fuel",
      "attention-vote-law",
    ]) {
      expect(t(key, { kind: "stone", kinds: "wooden", planet: "terra", tier: "fine", slot: "back", goods: "beans", culture: "beans", fuel: "clay", law: "toll" })).not.toMatch(/\{\s*[A-Z]/);
    }
  });
});

describe("the current language", () => {
  it("is the one that was learnt", () => {
    expect(currentLocale()).toBe("ru");
    expect(spoken().locales).toEqual(["ru"]);
    learn(new Words({ locale: "sv", locales: ["ru", "sv"], ftl: "" }, NAMES));
    expect(currentLocale()).toBe("sv");
    expect(spoken().locales).toEqual(["ru", "sv"]);
  });

  it("keeps a language even when the server answered with nothing", () => {
    const empty = new Words({ locale: "", locales: [], ftl: "" }, null);
    expect(empty.locale).toBe(DEFAULT_LOCALE);
    expect(empty.locales).toEqual([DEFAULT_LOCALE]);
  });
});

describe("compare", () => {
  it("orders words the way the current language reads them", () => {
    //: Swedish puts «ä» after «z»; Russian, like most, treats it as an «a».
    //: The same pair must therefore come out in opposite orders -- which a
    //: hardcoded `localeCompare(a, b, "ru")` could never do.
    expect(compare("äpple", "zebra")).toBeLessThan(0);
    learn(new Words({ locale: "sv", locales: ["ru", "sv"], ftl: "" }, NAMES));
    expect(compare("äpple", "zebra")).toBeGreaterThan(0);
  });

  it("sorts group headers by the display word, not by the wire id", () => {
    expect(orderGroups(["Ящик", "Глина", "Железная руда"], "goods", [])).toEqual([
      "Глина",
      "Железная руда",
      "Ящик",
    ]);
  });

  it("sorts the market catalogue by the player's word for each id", () => {
    const book = {
      recipes: [{ id: "clay", name: "Глина" }],
      materials: [{ id: "iron_ore", name: "Железная руда" }],
      liquid: [],
    } as unknown as RecipeBook;
    //: Ids out, and their order follows «Глина» before «Железная руда» --
    //: the ASCII of the ids would have said the opposite.
    expect(catalogue(book, NAMES)).toEqual(["clay", "iron_ore"]);
    //: And the comparator can be handed in, which is what lets a memoised
    //: list re-sort the moment the language changes.
    expect(catalogue(book, NAMES, collatorFor("sv"))).toEqual(["clay", "iron_ore"]);
  });

  it("never throws on a language tag that cannot exist", () => {
    //: `new Intl.Collator("!!")` is a RangeError, and this runs in the middle
    //: of rendering a list.
    expect(() => collatorFor("not a tag at all")("a", "b")).not.toThrow();
    expect(collatorFor("!!")("a", "b")).toBeLessThan(0);
    learn(new Words({ locale: "!!", locales: ["!!"], ftl: "" }, null));
    expect(() => compare("a", "b")).not.toThrow();
  });
});

describe("the served FTL", () => {
  //: The check that would have caught five broken refusals: whatever the
  //: server's own message files call, this client must have registered. Read
  //: from the repository rather than from a fixture, because the drift being
  //: guarded against is exactly the one between those two directories. Through
  //: vite's glob rather than `node:fs`, so the app's type environment stays
  //: free of node's globals.
  const served = import.meta.glob("../../../backend/locales/ru/*.ftl", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  it("calls no function the client does not register", () => {
    expect(Object.keys(served).length, "no .ftl files found beside the backend").toBeGreaterThan(0);
    const called = new Set<string>();
    for (const source of Object.values(served)) {
      for (const line of source.split("\n")) {
        //: Comments name functions while explaining them; only what is in a
        //: message counts.
        if (line.trimStart().startsWith("#")) continue;
        for (const [, fn] of line.matchAll(/\b([A-Z][A-Z0-9_]*)\s*\(/g)) called.add(fn);
      }
    }
    expect(called.size).toBeGreaterThan(0);
    const missing = [...called].filter((fn) => !MESSAGE_FUNCTIONS.includes(fn));
    expect(missing, "message functions the server uses and the client does not have").toEqual([]);
  });
});

describe("eventKey", () => {
  //: The digest used to carry its own map of twenty-two lines, and the server
  //: is allowed to tell about twenty-three kinds -- the two the map lacked
  //: were an eruption and its warning, shown to the player as `plates.erupted`.
  //: Now the line comes from the locale, and the rule that finds it must be
  //: exactly the server's `i18n.event_key`.
  it("turns an event kind into the name of its message", () => {
    expect(eventKey("craft.finished")).toBe("event-craft-finished");
    //: An underscore is a legal Fluent identifier character and stays; only
    //: the dots move. Getting this wrong cost ten keys the first time.
    expect(eventKey("utility.cut_off")).toBe("event-utility-cut_off");
    expect(eventKey("market.order_expired")).toBe("event-market-order_expired");
  });

  it("finds a line for every kind the digest may carry", () => {
    //: Read out of the served file rather than listed here: the server's own
    //: test checks that file against its allowlist, so between the two the
    //: chain is closed -- allowlist to message to what the player reads.
    const lines = servedMessages();
    for (const kind of [
      "craft.finished",
      "plates.erupted",
      "plates.warned",
      "utility.cut_off",
      "market.reservation_lapsed",
    ]) {
      expect(lines, `no line for ${kind}`).toContain(eventKey(kind));
    }
  });
});

describe("a language the client has no files for yet", () => {
  //: The shape of wave V: the engine gains a language before the window does,
  //: because the engine's half is 755 messages and the window's is 1439. On
  //: that day `src/locales/en/` does not exist yet, and without a fallback the
  //: glob returns nothing and the **whole shell** renders as bare keys --
  //: neither English nor Russian, just `ui-summary-label` on the screen.
  it("falls back to the default language instead of going silent", () => {
    //: A language nobody has started -- not `en`, which now has its own files
    //: and would pass this by translating rather than by falling back.
    learn(new Words({ locale: "sv", locales: ["ru", "en", "sv"], ftl: "" }, NAMES));
    expect(spoken().locale).toBe("sv");
    expect(spoken().has("ui-summary-label")).toBe(true);
    expect(t("ui-summary-label")).toBe("Что произошло");
  });

  it("prefers the language's own word where it has one", () => {
    //: The other half of the same rule, and the reason the new language is
    //: loaded first: `addResource` keeps the first definition it sees, so a
    //: translated message must win over the untranslated one beneath it.
    learn(new Words({ locale: "en", locales: ["ru", "en"], ftl: "" }, NAMES));
    expect(t("ui-summary-label")).toBe("What happened");
  });

  it("loads a complete language without a wall of override warnings", () => {
    //: The fallback is trimmed to the messages the language does not say
    //: itself. Before that, a full English -- which parity *requires* to be
    //: full -- made every one of its messages a duplicate of the Russian
    //: beneath it: 1445 override warnings per login, and the warn channel is
    //: this module's only diagnostics.
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});
    learn(new Words({ locale: "en", locales: ["ru", "en"], ftl: "" }, NAMES));
    expect(t("ui-summary-label")).toBe("What happened");
    expect(warned).not.toHaveBeenCalled();
    warned.mockRestore();
  });

  it("does not load the fallback twice for the default language itself", () => {
    //: `addResource` keeps the first definition and reports the rest as
    //: errors. Loading Russian under Russian would make every one of its own
    //: 1439 messages a duplicate, and the console a wall of warnings.
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});
    learn(new Words({ locale: DEFAULT_LOCALE, locales: ["ru"], ftl: "" }, NAMES));
    expect(t("ui-summary-label")).toBe("Что произошло");
    expect(warned).not.toHaveBeenCalled();
    warned.mockRestore();
  });
});

describe("the client's own words", () => {
  //: The mirror of the server's completeness test. A key with no message is
  //: not a crash and not a wrong sentence -- it is the key itself on the
  //: screen, which nobody notices until a player does.
  const sources = import.meta.glob("../**/*.{ts,tsx}", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  /** Every key a `t("...")` names, with the file that names it. */
  function asked(): Map<string, string> {
    const found = new Map<string, string>();
    for (const [path, source] of Object.entries(sources)) {
      if (path.includes("/__tests__/")) continue;
      //: Both ways a key is named: `t("ui-...")` directly, and any bare `ui-`
      //: literal -- a panel may keep its keys in a map and hand them to `t`
      //: one at a time, which is a naming site all the same.
      for (const [, key] of source.matchAll(/\bt\(\s*"([a-z][a-z0-9-]*)"/g)) {
        if (!found.has(key)) found.set(key, path);
      }
      for (const [, key] of source.matchAll(/"(ui-[a-z0-9-]+)"/g)) {
        if (!found.has(key)) found.set(key, path);
      }
    }
    return found;
  }

  it("has a message for every key the client asks for", () => {
    const keys = asked();
    expect(keys.size, "no t(\"...\") calls found -- the scan went stale").toBeGreaterThan(0);
    const known = new Set(servedMessages());
    const missing = [...keys].filter(([key]) => !known.has(key));
    expect(missing, "keys the client asks for and no locale defines").toEqual([]);
  });

  it("writes no message nobody asks for", () => {
    //: Only our own `ui-` half: the served bundle is the engine's, and its
    //: own suite decides what belongs in it.
    const asked_ = asked();
    const ours = ownMessages().filter((key) => key.startsWith("ui-"));
    expect(ours.length).toBeGreaterThan(0);
    const orphans = ours.filter((key) => !asked_.has(key));
    expect(orphans, "messages written for nobody").toEqual([]);
  });
});

describe("refusalText", () => {
  it("draws the sentence from our own bundle when it knows the key", () => {
    //: The proof of the wave: the server rendered these words from this very
    //: file, and so did we.
    expect(refusalText("что-то пошло не так", "storage-relic", { goods: "iron_ore" })).toBe(
      "«Железная руда» — наследие Предтеч",
    );
  });

  it("keeps the server's words for a key we do not have", () => {
    //: A refusal site not converted yet, or an older bundle: nothing regresses.
    expect(refusalText("не хватает глины", "goods-not-enough", { short: 3 })).toBe(
      "не хватает глины",
    );
    expect(refusalText("не хватает глины")).toBe("не хватает глины");
  });

  it("says the messages a refusal quotes instead of passing them on", () => {
    //: The regression this block exists for. A refusal that quotes another
    //: one sends the quoted half as a **key**, so that each end says it in its
    //: own language -- and Fluent takes strings, numbers and dates, nothing
    //: else. Handed the array it printed the literal `{$what}` into the
    //: sentence and complained into an error list nobody reads. The player saw
    //: «тело занято: {$what}» in place of a sentence that had arrived correct,
    //: because a refusal is drawn from `code` in preference to the server's
    //: own words.
    expect(
      refusalText("тело занято: идёт разведка (ещё 12 мин)", "occupation-busy", {
        what: [{ code: "doing-field-what" }],
        left: [{ code: "time-left", args: { minutes: 12 } }],
      }),
    ).toBe("тело занято: идёт разведка (ещё 12 мин)");
  });

  it("strings several quoted messages the way the language strings a list", () => {
    //: What a city still lacks is as many messages as there are buildings, and
    //: the separator belongs to the language -- the same one `NAMES()` uses,
    //: so a quoted list and a list of names read alike.
    expect(
      refusalText("для города не хватает: биопринтер, рынок", "city-needs", {
        lacks: [
          { code: "storage-nothing-to-put" },
          { code: "session-locale-unknown", args: { locale: "kk" } },
        ],
      }),
    ).toBe("для города не хватает: класть нечего, такого языка нет: kk");
  });

  it("leaves an argument that is not a quoted message alone", () => {
    //: The shape test is deliberately narrow: only an array whose every entry
    //: has a string `code`. A plain value must reach Fluent untouched, or the
    //: fix for one refusal would break every other.
    expect(refusalText("х", "session-locale-unknown", { locale: "kk" })).toBe(
      "такого языка нет: kk",
    );
    //: An empty list still counts as quoted: it says nothing, and nothing is
    //: what it must render to. Letting it through to Fluent would print
    //: `{$what}` -- the very bug above, for the one case that looks harmless.
    expect(refusalText("сервер сказал так", "occupation-busy", { what: [], left: [] })).toBe(
      "тело занято:  ()",
    );
  });

  it("keeps the server's words when the names bundle failed to load", () => {
    //: The FTL and the names are two separate reads, and `App` swallows a
    //: failed one. Drawing the message from half a language would turn
    //: «Железная руда», which arrived correct, into `iron_ore`.
    learn(words("ru", FTL, null));
    expect(spoken().has("storage-relic")).toBe(true);
    expect(spoken().named).toBe(false);
    expect(refusalText("«Железная руда» — наследие Предтеч", "storage-relic", { goods: "iron_ore" })).toBe(
      "«Железная руда» — наследие Предтеч",
    );
  });
});

describe("loadWords", () => {
  //: What the fetch was asked for is captured by the stand-in itself rather
  //: than read off the spy's call history, so no test has to reset the spy
  //: between runs -- and none of these tests can be read wrong because an
  //: earlier one happened to ask for the same language.
  const asking = (answer: WordsBundle | Error) => {
    let asked: string | null = null;
    vi.mocked(fetchWords).mockImplementation(async (locale: string) => {
      asked = locale;
      if (answer instanceof Error) throw answer;
      return answer;
    });
    return () => asked;
  };

  it("asks for the language and speaks what came back", async () => {
    const asked = asking({ locale: "ru", locales: ["ru"], ftl: FTL });
    const loaded = await loadWords("ru", NAMES);
    expect(asked()).toBe("ru");
    expect(loaded.t("storage-nothing-to-put")).toBe("класть нечего");
    expect(loaded.t("storage-relic", { goods: "iron_ore" })).toBe(
      "«Железная руда» — наследие Предтеч",
    );
  });

  it("falls back to the default language when nobody named one", async () => {
    const asked = asking({ locale: DEFAULT_LOCALE, locales: [DEFAULT_LOCALE], ftl: "" });
    await loadWords("", NAMES);
    expect(asked()).toBe(DEFAULT_LOCALE);
  });

  it("lets the login through when the server has no /public/i18n", async () => {
    //: The branch that decides whether an older server can be played on at
    //: all: the read fails, the player gets an empty language, and the login
    //: carries on instead of dying on a missing endpoint.
    const asked = asking(new Error("/public/i18n/ru: 404"));
    const loaded = await loadWords("ru", NAMES);
    expect(asked()).toBe("ru");
    expect(loaded.locale).toBe("ru");
    expect(loaded.locales).toEqual(["ru"]);
    expect(loaded.t("storage-nothing-to-put")).toBe("storage-nothing-to-put");
  });
});

describe("forget", () => {
  it("leaves no trace of the last account's language", () => {
    learn(new Words({ locale: "sv", locales: ["ru", "sv"], ftl: FTL }, NAMES));
    forget();
    expect(currentLocale()).toBe(DEFAULT_LOCALE);
    expect(spoken().locales).toEqual([DEFAULT_LOCALE]);
    expect(t("storage-nothing-to-put")).toBe("storage-nothing-to-put");
  });
});

/** Every message name either side defines: the engine's and the client's own. */
function servedMessages(): string[] {
  const served = import.meta.glob("../../../backend/locales/ru/*.ftl", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;
  return [...messageNames(Object.values(served)), ...ownMessages()];
}
/** Message names the client's own locale files define. */
function ownMessages(): string[] {
  const own = import.meta.glob("../locales/*/*.ftl", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;
  return messageNames(Object.values(own));
}

/** Message names defined by any of these FTL sources. */
function messageNames(sources: string[]): string[] {
  const names: string[] = [];
  for (const source of sources) {
    for (const line of source.split("\n")) {
      const found = /^([a-zA-Z][a-zA-Z0-9_-]*)\s*=/.exec(line);
      if (found) names.push(found[1]);
    }
  }
  return names;
}
