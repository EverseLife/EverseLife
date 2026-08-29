// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { Refusal, useActions } from "../../actions";
import type { Props, Surface } from "./shared";
import { Floor } from "./Floor";
import { Storages } from "./Storages";


/** Everything stored at the place: the floor and the chests, one window (D-181, D-192).
 *
 * The question the window answers is one -- "where do my things go here" -- and
 * the answers used to be scattered: the floor in a window of its own, the
 * chests among the sections of "Место". Now the floor comes first and the
 * chests follow: what lies takes area, what is chested does not, and seeing
 * both side by side is what makes that trade-off legible.
 *
 * The window is for everyone: whoever got in puts things down and picks them
 * up. What keeps a stranger's hands away is the shut door (D-204) and the
 * chest's own lock (D-181) -- not a rule against touching.
 */
export function Ground({
  look,
  where = "floor",
}: Omit<Props, "busy" | "act"> & { where?: Surface }) {
  //: Own waiting and own refusal: a full yard must refuse this window, not the map.
  const acting = useActions();
  const { busy, act } = acting;
  //: Where there is a house, its floor owns the chests; where there is none,
  //: the ground does.
  const roofed = (look.floor?.space.area ?? 0) > 0;

  return (
    <>
      <Refusal of={acting} />
      <Floor look={look} busy={busy} act={act} where={where} />
      {/* The chests, shown by whichever window owns the surface they stand on.
          A chest is furniture and goes into a building (D-106) -- but a plot
          with no building has no floor at all, and then the chests standing on
          it belong to the ground. Shown by exactly one of the two windows, or
          a canister put down on a wild node would be on no screen anywhere. */}
      {(where === "floor") === roofed && <Storages look={look} busy={busy} act={act} />}
    </>
  );
}
