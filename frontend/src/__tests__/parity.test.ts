// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Every language says the same things, with the same values in them (D-251 V).
 *
 * Translating a message file is not translating a document: the sentence
 * carries machinery, and the machinery has to survive the crossing. Three
 * things can be lost, and none of them raises anything at render time --
 *
 * - a **message**. Fluent falls back to nothing and the key reaches the eye;
 * - an **argument**. `{ $left }` dropped from the English line means the
 *   deadline is simply not said, and the sentence still reads perfectly well;
 * - a **function**. `NAME($goods)` written as `{ $goods }` prints `iron_ore`
 *   in the middle of the sentence -- the defect wave IV was spent removing.
 *
 * So: parity against the default language, per message. Wording is nobody's
 * business here. The engine's half of the same check is
 * `backend/tools/check_locales.py`; this is the window's.
 *
 * Variant **keys** are compared only where they are identifiers the code
 * selects on -- `[true]`, `[named]`. A plural category is the language's own
 * business: Russian counts in three forms and English in two, and demanding
 * parity there would be demanding a mistranslation.
 */

import { describe, expect, it } from "vitest";

const FILES = import.meta.glob("../locales/*/*.ftl", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** The language every other one is measured against: the one the game is written in. */
const DEFAULT_LOCALE = "ru";

/** A variant key that names a plural category belongs to the language. */
const PLURAL = new Set(["zero", "one", "two", "few", "many", "other"]);

/**
 * One file at a time, while a translation is in flight.
 *
 * `PARITY_ONLY=city.ftl npx vitest run src/__tests__/parity.test.ts` narrows
 * to a single pair. Whole-language parity is meaningless until every file
 * exists, and waiting until then to find a dropped argument is waiting too
 * long. CI sets nothing and compares everything.
 */
const ONLY =
  (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env
    ?.PARITY_ONLY ?? "";

type Shape = { args: Set<string>; functions: Set<string>; variants: Set<string> };

/** Every message of one language: id -> its body, however many files it is in. */
function messages(locale: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const [path, source] of Object.entries(FILES)) {
    if (!path.includes(`/locales/${locale}/`)) continue;
    if (ONLY && !path.endsWith(`/${ONLY}`)) continue;
    let id: string | null = null;
    let body: string[] = [];
    const flush = () => {
      if (id) found.set(id, body.join("\n"));
      id = null;
      body = [];
    };
    for (const line of source.split("\n")) {
      const starts = /^([a-zA-Z][a-zA-Z0-9_-]*)\s*=(.*)$/.exec(line);
      if (starts) {
        flush();
        id = starts[1];
        body = [starts[2]];
      } else if (id !== null && /^\s+\S/.test(line)) {
        body.push(line);
      } else if (id !== null && line.trim() === "") {
        //: A blank line inside a select is legal and carries nothing.
      } else {
        flush();
      }
    }
    flush();
  }
  return found;
}

/** What a message interpolates, calls and selects on -- ignoring its words. */
function shape(body: string): Shape {
  const args = new Set<string>();
  const functions = new Set<string>();
  const variants = new Set<string>();
  for (const [, name] of body.matchAll(/\$([a-zA-Z][a-zA-Z0-9_]*)/g)) args.add(name);
  for (const [, name] of body.matchAll(/\b([A-Z][A-Z0-9_]*)\s*\(/g)) functions.add(name);
  for (const [, key] of body.matchAll(/\*?\[\s*([a-zA-Z][a-zA-Z0-9_-]*)\s*\]/g)) {
    if (!PLURAL.has(key)) variants.add(key);
  }
  return { args, functions, variants };
}

const missing = (mine: Set<string>, yours: Set<string>) =>
  [...mine].filter((one) => !yours.has(one)).sort();

describe("the languages say the same things", () => {
  const locales = [
    ...new Set(
      Object.keys(FILES)
        .map((path) => /\/locales\/([^/]+)\//.exec(path)?.[1])
        .filter((name): name is string => Boolean(name)),
    ),
  ].filter((name) => name !== DEFAULT_LOCALE);

  const ours = messages(DEFAULT_LOCALE);

  it("has a default language to measure against", () => {
    expect(ours.size).toBeGreaterThan(0);
  });

  for (const locale of locales) {
    describe(locale, () => {
      const theirs = messages(locale);

      it("knows every message the default language knows, and no others", () => {
        expect(missing(new Set(ours.keys()), new Set(theirs.keys()))).toEqual([]);
        expect(missing(new Set(theirs.keys()), new Set(ours.keys()))).toEqual([]);
      });

      it("keeps every argument, function and enum branch", () => {
        const lost: string[] = [];
        for (const [id, body] of ours) {
          const other = theirs.get(id);
          if (other === undefined) continue;
          const mine = shape(body);
          const yours = shape(other);
          for (const what of ["args", "functions", "variants"] as const) {
            for (const one of missing(mine[what], yours[what])) lost.push(`${id}: lost ${one}`);
            for (const one of missing(yours[what], mine[what])) lost.push(`${id}: gained ${one}`);
          }
        }
        expect(lost).toEqual([]);
      });
    });
  }
});
