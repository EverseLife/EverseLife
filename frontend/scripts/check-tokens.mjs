/**
 * Contrast check over the token layer (D-055, D-077).
 *
 * The brief demands not lower than 4.5:1 for every text/background pair, in
 * every theme and at every lightness -- and demands the check be automatic:
 * "the number of checks grows with the number of planets". Five planets in two
 * lights is 150 pairs, and nobody verifies that by eye. So it runs in CI, and a
 * failure breaks the build rather than reaching a player.
 *
 * It also catches the likelier bug: a planet that forgot a token. A missing
 * `--d-400` does not throw in CSS -- it silently inherits somebody else's
 * value, and the theme looks almost right.
 *
 * No dependencies on purpose: the check must survive a lockfile update.
 *
 *   node scripts/check-tokens.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "src", "theme.css"), "utf8");

/** Every step and role a palette must define. A planet missing one is a bug. */
const STEPS = ["950", "900", "850", "800", "700", "600", "400", "200", "50"];
const ROLES = ["accent", "warning", "danger", "success"];
const NAMES = [...STEPS, ...ROLES];

/**
 * Text/surface pairs that actually occur on screen.
 *
 * `--base-800` is in the list because it is the ground of input fields and the
 * selected row -- the darkest surface muted text ever lands on, and exactly
 * where the vault's own values failed.
 */
const PAIRS = [
  ["primary text", "50", "900"],
  ["primary text", "50", "850"],
  ["secondary text", "200", "900"],
  ["secondary text", "200", "850"],
  ["muted text", "400", "900"],
  ["muted text", "400", "850"],
  ["muted text", "400", "800"],
  ["accent (a person)", "accent", "900"],
  ["accent (a person)", "accent", "850"],
  ["warning", "warning", "900"],
  ["warning", "warning", "850"],
  ["danger", "danger", "900"],
  ["danger", "danger", "850"],
  ["success", "success", "900"],
  ["success", "success", "850"],
];

const FLOOR = 4.5;

/** Palettes, by the block that declares them: `:root[data-planet="..."]`. */
function palettes(css) {
  const found = new Map();
  //: Terra is declared together with the bare `:root` -- it is the default.
  const blocks = css.matchAll(/(:root[^{]*)\{([^}]*)\}/g);
  for (const [, selector, body] of blocks) {
    //: A literal hex, not a mention: the block that chooses between the sets
    //: also names `--d-*`, and matching it would overwrite Terra with nothing.
    if (!/--[dl]-[a-z0-9]+\s*:\s*#/.test(body)) continue;
    const planet = selector.match(/data-planet="([a-z]+)"/)?.[1] ?? "terra";
    const light = { dark: {}, light: {} };
    for (const [, which, name, value] of body.matchAll(
      /--([dl])-([a-z0-9]+)\s*:\s*(#[0-9A-Fa-f]{6})/g,
    )) {
      light[which === "d" ? "dark" : "light"][name] = value;
    }
    found.set(planet, light);
  }
  return found;
}

const channel = (v) => {
  v /= 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};

const luminance = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
};

const contrast = (a, b) => {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};

const sets = palettes(source);
const problems = [];
let checked = 0;
let worst = { ratio: Infinity, where: "" };

if (sets.size === 0) {
  problems.push("no palettes found in theme.css: has the file moved?");
}

for (const [planet, themes] of sets) {
  for (const [light, tokens] of Object.entries(themes)) {
    const where = `${planet}/${light}`;

    for (const name of NAMES) {
      if (!tokens[name]) problems.push(`${where}: token --${name} is not defined`);
    }
    if (NAMES.some((name) => !tokens[name])) continue;

    for (const [label, front, back] of PAIRS) {
      const ratio = contrast(tokens[front], tokens[back]);
      checked++;
      if (ratio < worst.ratio) worst = { ratio, where: `${where} ${label}` };
      if (ratio < FLOOR) {
        problems.push(
          `${where}: ${label} ${tokens[front]} on --base-${back} ${tokens[back]}` +
            ` = ${ratio.toFixed(2)}:1, below ${FLOOR}`,
        );
      }
    }
  }
}

const themes = [...sets.keys()].join(", ");
if (problems.length > 0) {
  console.error(`token check failed: ${problems.length} problem(s)\n`);
  for (const line of problems) console.error(`  ${line}`);
  console.error(
    "\nA colour is not a taste here: the floor of 4.5:1 is a vault requirement" +
      " (D-077), and a semantic colour nobody can read stops being a language.",
  );
  process.exit(1);
}

console.log(
  `tokens fine: ${checked} pairs over ${sets.size} planets (${themes})` +
    ` in two lights, worst ${worst.ratio.toFixed(2)}:1 at ${worst.where}`,
);
