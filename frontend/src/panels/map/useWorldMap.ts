// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The map as the body sees it: the graph answered from where one stands
 * (D-240, D-319), the ships close enough to see laid over it (D-201), and its
 * nodes by key. Out of `GraphMap.tsx`, which is past the eight-hundred-line
 * bar already.
 */

import { useEffect, useMemo, useState } from "react";

import * as api from "../../api";
import type { Look, MapNode, WorldMap } from "../../api";
import { useSession } from "../../actions";
import { oneEach, withCityScene } from "./geo";

export function useWorldMap(look: Look): {
  /** Null until the first answer has come. */
  map: WorldMap | null;
  byKey: Record<string, MapNode>;
} {
  //: The map is answered from where the body stands (D-240), so the read
  //: carries the session's token: without it the server shows the sky alone.
  const session = useSession();

  const [world, setWorld] = useState<WorldMap | null>(null);
  const here = look.node?.key ?? "";
  //: The map opens by walking (D-319): what one sees changes with one's own
  //: node and the set of exits from it, so those are the reasons to reread.
  //: With their surface: a paving finished from here changes the map's own
  //: row of the way and -- since D-332 -- whose land the far node is, and
  //: the outline drawn round it; neither comes with `look`.
  const exits = (look.exits ?? []).map((path) => `${path.key}:${path.surface}`).join("|");
  useEffect(() => {
    //: Shared with the ship's console, which wants the same map from the same
    //: stand: one walk of the graph, not one per window (`standingMap`).
    void api.standingMap(session.token, `${here}|${exits}`).then(setWorld);
    //: The token is read inside and is the session's own for its whole life:
    //: it is not a reason to reread the map, and the reasons are listed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [here, exits]);
  //: Ships are not on the public map at all (D-201): from a distance a ship is
  //: a single hull on the space layer and nothing more. What is close enough
  //: to see arrives with `look` -- the ship moored at the pier one stands on,
  //: or the rooms of the one being stood in -- so a ship appears on walking up
  //: to it and is gone on walking away.
  //: Keyed by what the ships **are**, not by the object carrying them: `look`
  //: arrives anew every few seconds, and merging on its identity rebuilt the
  //: whole map -- and with it the layout and the simulation -- on every poll.
  const sighted = (look.ships?.nodes ?? []).map((node) => node.key).join("|");
  const map = useMemo<WorldMap | null>(() => {
    const seen = look.ships;
    if (!world) return world;
    //: The sighted rows last, so a hull the map has as a point of the sky is
    //: the pier's point here (`oneEach`).
    const nodes = oneEach([...world.nodes, ...(seen?.nodes ?? [])]);
    return {
      ...world,
      nodes: withCityScene(nodes),
      edges: [...world.edges, ...(seen?.edges ?? [])],
    };
    //: `look.ships` is read inside and keyed by `sighted` outside: the same
    //: keys mean the same ships, and the linter cannot be shown that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world, sighted]);
  const byKey = useMemo(() => {
    const out: Record<string, MapNode> = {};
    for (const node of map?.nodes ?? []) out[node.key] = node;
    return out;
  }, [map]);
  return { map, byKey };
}
