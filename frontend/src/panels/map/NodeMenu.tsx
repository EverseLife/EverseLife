// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { spell, type Look, type MapNode } from "../../api";
import { Refusal, useActions, useSession } from "../../actions";
import { t } from "../../locale";

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
  offworld,
  onExpand,
  onDone,
}: {
  at: { x: number; y: number };
  node: MapNode | undefined;
  look: Look;
  step?: { key: string; seconds: number };
  group: boolean;
  /** The node stands on another planet: walked-to never, flown-to only. */
  offworld: boolean;
  onExpand: () => void;
  onDone: () => void;
}) {
  const session = useSession();
  const acting = useActions();
  const { busy, act } = acting;
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
    !look.survey &&
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
      <p className="menu-ask">{node.name}</p>
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
      {group && !node.aboard && !offworld && (
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
