// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The player is not a reader of our design log.
 *
 * Decision codes -- `D-143`, `D-055` -- are how the vault and the engine talk
 * to each other, and comments in this repository keep them deliberately. But a
 * code inside a line the player reads is a promise of a document they will
 * never see, and there were 253 of them.
 *
 * So the rule is one-sided: codes live in comments, never in copy. Without a
 * check they come back within a month, because every new panel is written by
 * somebody who has the decision open in front of them.
 *
 * Comments are stripped first, and only what is left -- JSX text, string
 * literals, `title` and `placeholder` -- is searched.
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

/** Files whose whole purpose is to name decisions rather than show text. */
const SKIP = new Set([]);

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

const problems = [];
let scanned = 0;

for (const path of sources(root)) {
  const shown = relative(join(here, ".."), path).replace(/\\/g, "/");
  if (SKIP.has(shown)) continue;
  scanned++;
  const bare = withoutComments(readFileSync(path, "utf8"));
  bare.split("\n").forEach((line, i) => {
    for (const hit of line.matchAll(CODE)) {
      problems.push(`${shown}:${i + 1}: ${hit[0]} in copy -- ${line.trim().slice(0, 72)}`);
    }
  });
}

if (problems.length > 0) {
  console.error(`copy check failed: ${problems.length} decision code(s) in player-facing text\n`);
  for (const line of problems) console.error(`  ${line}`);
  console.error(
    "\nCodes belong in comments, where they explain the code to us. In a line the" +
      "\nplayer reads they promise a document that player will never be given.",
  );
  process.exit(1);
}

console.log(`copy fine: no decision codes in player-facing text across ${scanned} files`);
