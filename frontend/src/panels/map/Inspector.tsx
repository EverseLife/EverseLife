// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { spell, type Look, type MapNode } from "../../api";
import { Deadline } from "../../Deadline";
import { Rule } from "../../Rule";
import { Refusal, useActions, useBook, useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { cityWord } from "../../planets";
import { Roads } from "./Roads";
import { LAYER_NAME, offworld } from "./model";
import { metresBetween } from "./scout";
import { radiusOf } from "./useGlobe";
import { nameWord, nodeWord, price, provinceWord } from "./words";

/**
 * The column beside the map: everything about the node you picked.
 *
 * It replaces three strips that used to live under the map and took 178px of
 * the 605px the scene had -- the map, which is the game's whole navigation
 * surface, was left with barely half the window. Worse, the strips spoke about
 * everything at once: every road from here, every exit, exploration. The column
 * speaks about one node, which is what a person looking at a map wants.
 *
 * Where you stand, the column offers entering and exploring. Anywhere else --
 * the road there, what it costs the body, and what the surface between here and
 * there is worth laying.
 *
 * Two things take the column over while they last, because while they last
 * nothing else can be done: a walk, and a scout's run (D-327). Both say the
 * same three things -- where to, how long is left, and the one button that
 * ends it -- and both hide "Вы здесь", which would otherwise name a node the
 * body is on its way out of.
 */
export function Inspector({
  look,
  picked,
  byKey,
  groups,
  walkTargets,
  onExpand,
  onEnter,
}: {
  look: Look;
  picked: string | null;
  byKey: Record<string, MapNode>;
  groups: Set<string>;
  walkTargets: Record<string, { key: string; seconds: number }>;
  onExpand: (node: MapNode) => void;
  onEnter: () => void;
}) {
  const session = useSession();
  const acting = useActions();
  const { busy, act } = acting;
  //: The vault's word for a biome (D-321): a found node has no name, and the
  //: column says its kind instead -- the same word the world's refusals use.
  const book = useBook();
  const biomes = book?.constants?.["biome.names"];
  //: The province's name (landscape plan, wave 3): the vault's word by id.
  const names = useNames();
  const here = look.node?.key ?? "";
  const ongoing = look.travel ?? null;

  //: On the road the column reports the road: nothing else can be done from it.
  if (ongoing) {
    return (
      <aside className="inspect">
        <h3>
          {t("ui-map-ongoing")}
          <Rule>{t("ui-map-ongoing-rule")}</Rule>
        </h3>
        <p className="sign">{nameWord(ongoing.final ?? ongoing.to)}</p>
        <p className="note">
          {ongoing.final
            ? t("ui-map-ongoing-leg", { to: nameWord(ongoing.to) })
            : t("ui-map-ongoing-direct")}
          {(ongoing.legs_left ?? 0) > 1 &&
            ` · ${t("ui-map-ongoing-left", { count: String(ongoing.legs_left! - 1) })}`}
        </p>
        <Deadline
          until={ongoing.arrives_at}
          since={ongoing.started_at}
          label={t("ui-map-transit-label")}
        />
        <div className="row">
          <button
            className="quiet"
            onClick={() => act(() => session.send("travel.cancel"))}
            disabled={busy}
          >
            {t("ui-map-turn-back")}
          </button>
        </div>
        <Refusal of={acting} />
      </aside>
    );
  }

  //: A run of the scout takes the column the same way (D-327): the far end is
  //: a place with no node yet, so it is said in metres rather than by name.
  const run = look.scouting ?? null;
  if (run) {
    const stand = byKey[here]?.place;
    const from = stand && "lat" in stand ? stand : null;
    const globe = radiusOf(book, byKey[here]?.planet ?? null);
    const metres = from && globe ? metresBetween(from, run.place, globe) : null;
    return (
      <aside className="inspect">
        <h3>
          {t("ui-map-scouting")}
          <Rule>{t("ui-map-scouting-rule")}</Rule>
        </h3>
        <p className="sign">
          {metres === null
            ? t("ui-map-scouting-away")
            : t("ui-map-scouting-far", { metres: Math.round(metres) })}
        </p>
        <Deadline
          until={run.arrives_at}
          since={run.started_at}
          label={t("ui-map-scouting-label")}
        />
        <div className="row">
          <button
            className="quiet"
            onClick={() => act(() => session.send("explore.stop"))}
            disabled={busy}
          >
            {t("ui-map-scouting-stop")}
          </button>
        </div>
        <Refusal of={acting} />
      </aside>
    );
  }

  const node = picked ? byKey[picked] : null;
  const mine =
    !node || node.key === here || walkTargets[node.key]?.key === here;

  //: Standing here: the way in, and the way out into the unknown.
  if (!node || mine) {
    return (
      <aside className="inspect">
        <h3>{t("ui-map-here")}</h3>
        <p className="sign">{look.node ? nodeWord(look.node, biomes) : ""}</p>
        {look.node && provinceWord(look.node, names) && (
          <p className="sign">{provinceWord(look.node, names)}</p>
        )}
        <div className="row">
          <button onClick={onEnter} disabled={busy}>
            {t("ui-map-enter")}
          </button>
        </div>
        <Refusal of={acting} />
      </aside>
    );
  }

  const step = walkTargets[node.key];
  const exit = (look.exits ?? []).find((path) => path.key === step?.key);
  const group = groups.has(node.key);
  //: One does not walk to a planet: the void has no edges, and the way there
  //: is a ship from a spaceport (D-201). The column says so instead of
  //: offering a step the server would refuse anyway. The same for any node
  //: of another planet: the surface is walked, the void is not.
  const sphere = Boolean(node.orbit);
  const off = offworld(byKey, here, node);
  const reachable = !sphere && !off && (group ? Boolean(step) : true);

  return (
    <aside className="inspect">
      <h3>
        {nodeWord(node, biomes)}
        <Rule>{t("ui-map-node-rule")}</Rule>
      </h3>
      {provinceWord(node, names) && <p className="sign">{provinceWord(node, names)}</p>}
      {node.drawn !== undefined && (
        //: A counter, not a measure: no thousands separator, as the clock does it.
        <p className="note">
          {t("ui-map-node-drawn", { day: String(node.drawn) })}
        </p>
      )}
      <p className="note">
        {node.aboard
          ? node.flight
            ? t("ui-map-node-ship-flight")
            : t("ui-map-node-ship-port")
          : node.layer === "city"
            ? cityWord(node.planet).within
            : LAYER_NAME[node.layer]
              ? t(LAYER_NAME[node.layer])
              : node.layer}
        {group && !node.aboard && !off
          ? ` · ${t("ui-map-node-expandable")}`
          : ""}
        {off && !sphere ? ` · ${t("ui-map-node-far")}` : ""}
      </p>
      {/* A passage is a term like any other, and it is shown the way every
          term in this world is shown. */}
      {node.flight && (
        <Deadline
          until={node.flight.arrives_at}
          since={node.flight.started_at}
          label={t("ui-map-flight-label")}
        />
      )}

      {exit ? (
        <table>
          <tbody>
            <tr>
              <td>{t("ui-map-road")}</td>
              <td className="num">{spell(exit.seconds)}</td>
            </tr>
            <tr>
              <td>{t("ui-map-road-price")}</td>
              <td className="num">{price(exit.stamina)}</td>
            </tr>
          </tbody>
        </table>
      ) : sphere ? (
        <p className="note">
          {node.deferred
            ? t("ui-map-planet-deferred")
            : node.planet !== byKey[here]?.planet
              ? t("ui-map-planet-other")
              : (look.node?.features ?? []).includes("aboard")
                ? //: Aboard a ship the cabin carries the planet's key too, and
                  //: "you stand on its surface" would be said to somebody in
                  //: the void above it.
                  t("ui-map-planet-ship")
                : t("ui-map-planet-own")}
        </p>
      ) : node.aboard ? (
        <p className="note">
          {node.flight ? t("ui-map-ship-flying") : t("ui-map-ship-gangway")}
        </p>
      ) : off ? (
        //: A place on another planet: no road crosses the void, and promising
        //: an auto-built route here would promise the server's refusal (D-201).
        <p className="note">{t("ui-map-node-offworld")}</p>
      ) : (
        <p className="note">{t("ui-map-node-far-walk")}</p>
      )}

      <div className="row">
        {reachable && (
          <button
            onClick={() =>
              act(() =>
                session.send("travel.go", { node: step?.key ?? node.key }),
              )
            }
            disabled={busy}
          >
            {t("ui-map-go")}
          </button>
        )}
        {/* A ship is not opened from space: its rooms are walked into by the
            gangway, and "expand" here would show somebody else's surface.
            Neither is another planet (D-240): its surface is not in the answer
            at all, and a button that opened an empty layer would promise a
            look nobody has -- one gets there by flying. */}
        {group && !node.aboard && !off && (
          <button
            className="quiet"
            onClick={() => onExpand(node)}
            disabled={busy}
          >
            {t("ui-map-expand")}
          </button>
        )}
      </div>
      <Roads look={look} busy={busy} act={act} only={node.key} />
      <Refusal of={acting} />
    </aside>
  );
}
