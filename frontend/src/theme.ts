/**
 * The planet's theme (D-074, D-080).
 *
 * The interface reports **where you are**, not who you are: the base tone is a
 * property of the planet under your feet, not of your line. A Nymph living on
 * Terra sees the Terran theme -- she is in a foreign world, and the world says
 * so without a word of explanation.
 *
 * Everything this costs is one attribute on `<html>`: the values live in
 * `theme.css`, and markup, layout, density and element positions never change
 * with the theme. Somebody who flies to Aquatica does not relearn the screen.
 *
 * The switch happens on arrival, in one step and without animation (brief
 * section 3): a slow cross-fade of a whole interface reads as a fault.
 */

/** Planets of the world plus the void: `Planet` in `models/world.py` and a ship. */
const KNOWN = ["terra", "aquatica", "pyroxis", "aurora", "void"] as const;
export type Skin = (typeof KNOWN)[number];

const FALLBACK: Skin = "terra";

/** The last applied skin: repainting with the same value is not a change. */
let current: Skin | null = null;

/**
 * Point the token layer at a planet.
 *
 * The name comes from the server as `look.clock.planet` -- the node's own
 * field, so it is right for a location as much as for a city. An unknown name
 * is not an error worth a blank screen: an unpainted interface is worse than a
 * Terran one, so we fall back and say so in the console.
 */
export function wearPlanet(
  planet: string | null | undefined,
  aboard = false,
): void {
  //: A ship is not a planet but a subgraph, and its nodes keep the planet of
  //: their home port: the length of a day and the wear of gear are counted from
  //: it, and neither has a meaning of its own on board. So the look is taken
  //: from a node property instead, and the theme changes on the one edge where
  //: the connectors meet -- a step aboard, not an arrival in orbit.
  const skin = aboard ? "void" : pick(planet);
  if (skin === current) return;
  current = skin;
  document.documentElement.dataset.planet = skin;
}

function pick(planet: string | null | undefined): Skin {
  //: No planet means no ground under the feet: a ship, and the void's theme --
  //: the darkest and poorest in colour of them all.
  if (!planet) return "void";
  const name = planet.toLowerCase();
  if ((KNOWN as readonly string[]).includes(name)) return name as Skin;
  console.warn(`unknown planet "${planet}": falling back to ${FALLBACK}`);
  return FALLBACK;
}
