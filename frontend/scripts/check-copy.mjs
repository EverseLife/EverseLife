// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Two rules about the text the player reads.
 *
 * **No decision codes.** `D-143`, `D-055` are how the vault and the engine talk
 * to each other, and comments in this repository keep them deliberately. But a
 * code inside a line the player reads is a promise of a document they will
 * never see, and there were 253 of them.
 *
 * **No Russian outside the locales** (D-251 wave IV). A sentence written in the
 * code that produces it cannot be read in a second language, and wave IV moved
 * some 1200 of them into `src/locales/*.ftl`. Without a check they come back
 * within a month: writing `<h3>Рецепты</h3>` is one keystroke and calling
 * `t("ui-...")` is several, and nobody is going to remember which is required
 * on a Friday.
 *
 * Both rules are one-sided: comments are stripped first, and only what is left
 * -- JSX text, string literals, `title`, `placeholder`, `aria-label` -- is
 * searched. A Russian word in a comment is fine and often better than English.
 *
 * The exceptions to the second rule are listed below, each with its reason,
 * and the list is where a disputed line is settled -- not in an argument with
 * the check. Almost all of them are one thing: a Russian string that is a
 * **wire value**, compared against or sent to the server, and therefore data
 * rather than copy.
 *
 *   node scripts/check-copy.mjs
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "src");

/** A vault decision: `D-` and three digits. */
const CODE = /\bD-\d{3}\b/g;

/** A Cyrillic letter -- the shape a sentence written in place has. */
const RUSSIAN = /[Ѐ-ӿ]/;

/** Files whose whole purpose is to name decisions rather than show text. */
const SKIP = new Set([]);

/**
 * Where Russian is not copy, and why. Keyed by file, then by the exact text.
 *
 * A line is excused only for the file it was excused in: the same word may be
 * a wire value here and a heading there. Add to this list rather than arguing
 * with the check -- and if a reason cannot be written in one clause, the line
 * is probably copy after all.
 */
const ALLOWED = new Map(
  Object.entries({
    "src/panels/Admin.tsx": {
      'memo: "выплата",':
        "the ground written into the ledger memo, sent to the server and kept" +
        " as the payer's own words; the statement shows it back verbatim",
    },
    "src/panels/admin/Court.tsx": {
      'job.verdict && job.verdict !== "отказано"':
        "a verdict value from the wire. It is Russian in the engine too, and" +
        " making it an id is a server change with a migration, not a string move",
    },
    "src/panels/Doors.tsx": {
      '(d.precursor && "предтеч".includes(q)),':
        "a search keyword matched against what the player typed. Known defect:" +
        " the Forerunners' door is findable only by a Russian query",
    },
    "src/panels/Economy.tsx": {
      '.filter(([, law]) => law.value && law.value !== "нет")':
        "a code-law's value from the wire, written by the city; same shape as" +
        " the verdict above and the same server-side change to fix",
    },
    "src/panels/Kitchen.tsx": {
      'const ROLES = ["основа", "наполнитель", "жир", "приправа"] as const;':
        "the keys of `cook.role_weights` in the vault -- wire value and label" +
        " at once. Splitting them means giving the roles ids in the vault",
    },
  }).map(([file, entries]) => [file, new Map(Object.entries(entries))]),
);

function sources(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.(tsx|ts)$/.test(name)) out.push(path);
  }
  return out;
}

/**
 * Blank out comments, keeping the line and column count intact so the reported
 * position still points at the real place in the file.
 *
 * Not a parser, and it does not need to be: it only has to avoid reporting a
 * code that sits in a comment. The three shapes that occur here are `//`,
 * `//:` and `/* ... *\/`; a `//` inside a string literal is the one false
 * positive it could produce, and a URL with a decision code in it does not exist.
 */
function withoutComments(source) {
  let out = "";
  let inBlock = false;
  for (const line of source.split("\n")) {
    let kept = line;
    if (inBlock) {
      const close = kept.indexOf("*/");
      if (close === -1) {
        out += " ".repeat(kept.length) + "\n";
        continue;
      }
      kept = " ".repeat(close + 2) + kept.slice(close + 2);
      inBlock = false;
    }
    for (;;) {
      const open = kept.indexOf("/*");
      if (open === -1) break;
      const close = kept.indexOf("*/", open + 2);
      if (close === -1) {
        kept = kept.slice(0, open) + " ".repeat(kept.length - open);
        inBlock = true;
        break;
      }
      kept = kept.slice(0, open) + " ".repeat(close + 2 - open) + kept.slice(close + 2);
    }
    const line_ = kept.indexOf("//");
    if (line_ !== -1) kept = kept.slice(0, line_) + " ".repeat(kept.length - line_);
    //: A JSX comment block: `{/* ... */}` is already handled above, but a bare
    //: `*` continuation line of a doc comment is not -- it was blanked with the block.
    out += kept + "\n";
  }
  return out;
}

/**
 * The Russian on this line, as one piece of text to be excused or reported.
 *
 * The whole line, trimmed -- not the individual words. An exception has to
 * name something a person can recognise and grep for, and half a template
 * literal is neither.
 */
function russianOf(line) {
  return RUSSIAN.test(line) ? line.trim() : null;
}

const codes = [];
const written = [];
let scanned = 0;

for (const path of sources(root)) {
  const shown = relative(join(here, ".."), path).replace(/\\/g, "/");
  if (SKIP.has(shown)) continue;
  //: The locales are where the sentences are supposed to be, and the tests
  //: assert the exact wording on purpose -- both would fail the second rule
  //: for doing their job.
  if (shown.includes("/locales/") || shown.includes("/__tests__/")) continue;
  scanned++;
  const excused = ALLOWED.get(shown) ?? new Map();
  const bare = withoutComments(readFileSync(path, "utf8"));
  bare.split("\n").forEach((line, i) => {
    for (const hit of line.matchAll(CODE)) {
      codes.push(`${shown}:${i + 1}: ${hit[0]} in copy -- ${line.trim().slice(0, 72)}`);
    }
    const said = russianOf(line);
    if (said && !excused.has(said)) {
      written.push(`${shown}:${i + 1}: ${said.slice(0, 92)}`);
    }
  });
}

if (codes.length > 0) {
  console.error(`copy check failed: ${codes.length} decision code(s) in player-facing text\n`);
  for (const line of codes) console.error(`  ${line}`);
  console.error(
    "\nCodes belong in comments, where they explain the code to us. In a line the" +
      "\nplayer reads they promise a document that player will never be given.",
  );
  process.exit(1);
}

if (written.length > 0) {
  console.error(`copy check failed: ${written.length} line(s) of Russian outside the locales\n`);
  for (const line of written) console.error(`  ${line}`);
  console.error(
    "\nA sentence written in the code that produces it cannot be read in a second" +
      "\nlanguage (D-251). Move it to src/locales/<lang>/*.ftl and draw it with" +
      "\nt(\"ui-...\"). If the string is a wire value rather than copy -- compared" +
      "\nagainst or sent to the server -- add it to ALLOWED at the top of this file" +
      "\nwith the reason, which is where such a line is settled.",
  );
  process.exit(1);
}

console.log(
  `copy fine: no decision codes and no Russian outside the locales across ${scanned} files`,
);
