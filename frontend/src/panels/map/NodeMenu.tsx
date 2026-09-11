// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { spell, type Look, type MapNode } from "../../api";
import { Refusal, useActions, useBook, useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { markWord, nodeWord } from "./words";

/** The right-click menu on a node: go there, or open it up.
 *
 * Fixed to the pointer rather than to the node: the frame pans and zooms under
 * the hand, and a menu pinned to a node would crawl out from under it.
 */
export function NodeMenu({
  at,
  node,
  look,
  step,
  group,
  opens,
  offworld,
  onExpand,
  onDone,
}: {
  at: { x: number; y: number };
  node: MapNode | undefined;
  look: Look;
  step?: { key: string; seconds: number };
  group: boolean;
  /** The group is still shut: only then is there a city to open (D-330) --
   *  with its streets already drawn one is standing in the middle of it. */
  opens: boolean;
  /** The node stands on another planet: walked-to never, flown-to only. */
  offworld: boolean;
  onExpand: () => void;
  onDone: () => void;
}) {
  const session = useSession();
  const acting = useActions();
  const { busy, act } = acting;
  //: The vault's word for a biome (D-321): a found node has no name, and the
  //: column says its kind instead -- the same word the world's refusals use.
  const biomes = useBook()?.constants?.["biome.names"];
  //: The facet names a find (landscape plan wave 7); the table is the same
  //: one the inspector reads.
  const names = useNames();
  if (!node) return null;

  //: On the road the body stands in no node at all (D-107): `look.node` still
  //: names the node one walked out of, and a menu on it used to answer "Вы
  //: здесь." right above "Пока идёшь, никуда не выйти." -- two opposite things
  //: about the same moment.
  const here = !look.travel && node.key === (look.node?.key ?? "");
  //: Same rule as in the column: a planet is flown to, not walked to (D-201),
  //: and so is any place on one -- the surface is walked, the void is not.
  const may =
    !look.travel &&
    !here &&
    !node.orbit &&
    !offworld &&
    (group ? Boolean(step) : true);

  return (
    <div
      className="node-menu"
      role="menu"
      style={{ left: at.x, top: at.y }}
      //: The window-wide listener shuts the menu; a click inside it must not.
      onPointerDown={(e) => e.stopPropagation()}
    >
      {/* The city's name while the city is one mark on the map, the node's
          own word otherwise (D-330): the menu and the circle it was opened
          from must not disagree about what was clicked. */}
      <p className="menu-ask">{markWord(node, opens, nodeWord(node, biomes, names))}</p>
      {may && (
        <button
          role="menuitem"
          onClick={() =>
            void act(async () => {
              await session.send("travel.go", { node: step?.key ?? node.key });
              onDone();
            })
          }
          disabled={busy}
        >
          {t("ui-map-go")}
          {step ? ` · ${spell(step.seconds)}` : ""}
        </button>
      )}
      {/* Another planet is not opened either (D-240): one flies there. */}
      {opens && !node.aboard && !offworld && (
        <button role="menuitem" className="quiet" onClick={onExpand} disabled={busy}>
          {t("ui-map-expand")}
        </button>
      )}
      {here && <p className="note">{t("ui-map-menu-here")}</p>}
      {look.travel && <p className="note">{t("ui-map-menu-walking")}</p>}
      <Refusal of={acting} />
    </div>
  );
}
