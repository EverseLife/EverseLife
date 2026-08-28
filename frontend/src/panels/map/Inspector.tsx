// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { spell, type Look, type MapNode } from "../../api";
import { Deadline } from "../../Deadline";
import { Rule } from "../../Rule";
import { Refusal, useActions, useSession } from "../../actions";
import { cityWord } from "../../planets";
import { Roads } from "./Roads";
import { Search } from "./Search";
import { LAYER_NAME, offworld, type LayerId } from "./model";
import { price } from "./words";

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
 */
export function Inspector({
  look,
  picked,
  byKey,
  groups,
  walkTargets,
  layer,
  onExpand,
  onEnter,
}: {
  look: Look;
  picked: string | null;
  byKey: Record<string, MapNode>;
  groups: Set<string>;
  walkTargets: Record<string, { key: string; seconds: number }>;
  /** Which map is open: the search offers what **this** layer can grow (D-237). */
  layer: LayerId;
  onExpand: (node: MapNode) => void;
  onEnter: () => void;
}) {
  const session = useSession();
  const acting = useActions();
  const { busy, act } = acting;
  const here = look.node?.key ?? "";
  const ongoing = look.travel ?? null;

  //: On the road the column reports the road: nothing else can be done from it.
  if (ongoing) {
    return (
      <aside className="inspect">
        <h3>
          В пути
          <Rule>
            Пока идёшь, тебя нет нигде: добыча, крафт, погрузка и покупка закрыты, а
            счёт и ордера работают. Повернуть назад можно в любой момент — вернёшься
            туда, откуда вышел, а потраченное не вернётся.
          </Rule>
        </h3>
        <p className="sign">{ongoing.final ?? ongoing.to}</p>
        <p className="note">
          {ongoing.final ? `сейчас — отрезок до «${ongoing.to}»` : "прямой переход"}
          {(ongoing.legs_left ?? 0) > 1 && ` · впереди ещё ${ongoing.legs_left! - 1} узл.`}
        </p>
        <Deadline until={ongoing.arrives_at} since={ongoing.started_at} label="переход" />
        <div className="row">
          <button
            className="quiet"
            onClick={() => act(() => session.send("travel.cancel"))}
            disabled={busy}
          >
            Повернуть назад
          </button>
        </div>
        <Refusal of={acting} />
      </aside>
    );
  }

  const node = picked ? byKey[picked] : null;
  const mine = !node || node.key === here || walkTargets[node.key]?.key === here;

  //: Standing here: the way in, and the way out into the unknown.
  if (!node || mine) {
    return (
      <aside className="inspect">
        <h3>Вы здесь</h3>
        <p className="sign">{look.node?.name}</p>
        {!look.survey && (
          <div className="row">
            <button onClick={onEnter} disabled={busy}>
              Войти
            </button>
          </div>
        )}
        <Search look={look} busy={busy} act={act} layer={layer} />
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
  const reachable = !look.survey && !sphere && !off && (group ? Boolean(step) : true);

  return (
    <aside className="inspect">
      <h3>
        {node.name}
        <Rule>
          Идти можно в любой узел на карте: маршрут строится сам по времени с учётом
          покрытия, каждый отрезок — отдельное задание, и приход сам выводит в
          следующий. По прямой не ходят: нет ребра — нет пути. Карта показывает два
          шага вокруг вас — дальний узел откроется, когда вы к нему приблизитесь.
        </Rule>
      </h3>
      <p className="note">
        {node.aboard
          ? node.flight
            ? "корабль · в рейсе"
            : "корабль · у космодрома"
          : node.layer === "city"
            ? cityWord(node.planet).within
            : (LAYER_NAME[node.layer] ?? node.layer)}
        {group && !node.aboard && !off ? " · есть что раскрыть" : ""}
        {off && !sphere ? " · другая планета: смотреть отсюда нечего" : ""}
      </p>
      {/* A passage is a term like any other, and it is shown the way every
          term in this world is shown. */}
      {node.flight && (
        <Deadline
          until={node.flight.arrives_at}
          since={node.flight.started_at}
          label="рейс"
        />
      )}

      {exit ? (
        <table>
          <tbody>
            <tr>
              <td>дорога</td>
              <td className="num">{spell(exit.seconds)}</td>
            </tr>
            <tr>
              <td>стоит тела</td>
              <td className="num">{price(exit.stamina)}</td>
            </tr>
          </tbody>
        </table>
      ) : sphere ? (
        <p className="note">
          {node.deferred
            ? "Планета вне альфы: её ещё нет в мире, и попасть на неё нельзя."
            : node.planet !== byKey[here]?.planet
              ? "Другая планета. Пешком туда пути нет: только кораблём с космодрома."
              : (look.node?.features ?? []).includes("борт")
                ? //: Aboard a ship the cabin carries the planet's key too, and
                  //: "you stand on its surface" would be said to somebody in
                  //: the void above it.
                  "Планета вашего корабля."
                : "Ваша планета: вы стоите на её поверхности."}
        </p>
      ) : node.aboard ? (
        <p className="note">
          {node.flight
            ? "Корабль в рейсе: трапа нет, пока он не причалит."
            : "На борт заходят ногами, по трапу с космодрома."}
        </p>
      ) : off ? (
        //: A place on another planet: no road crosses the void, and promising
        //: an auto-built route here would promise the server's refusal (D-201).
        <p className="note">
          Это другая планета: пешком туда пути нет, только кораблём с космодрома.
        </p>
      ) : (
        <p className="note">
          Соседним не является: маршрут построится сам, по проходимым рёбрам.
        </p>
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
            Идти
          </button>
        )}
        {/* A ship is not opened from space: its rooms are walked into by the
            gangway, and "expand" here would show somebody else's surface.
            Neither is another planet (D-240): its surface is not in the answer
            at all, and a button that opened an empty layer would promise a
            look nobody has -- one gets there by flying. */}
        {group && !node.aboard && !off && (
          <button className="quiet" onClick={() => onExpand(node)} disabled={busy}>
            Раскрыть
          </button>
        )}
      </div>
      {look.survey && (
        <p className="reason">Разведчик в поле: тело недоступно, как во сне.</p>
      )}

      <Roads look={look} busy={busy} act={act} only={node.name} />
      <Refusal of={acting} />
    </aside>
  );
}

