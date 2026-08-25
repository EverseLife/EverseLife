// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import * as api from "../../api";
import { Refusal, useActions, useSession } from "../../actions";
import type { Props } from "./shared";
import { Door } from "./Door";
import { Foundation } from "./Foundation";


/** The plot: whose it is, what it is called, who gets in -- and how it changes hands.
 *
 * One window for everything about the land itself (D-178, D-204): ownership,
 * the name, the door, buying an empty plot and founding a city. These used to
 * live in two windows ("Локация" and half of "Место"), and the seam between
 * them ran through one question -- "what is this land and what may I do with
 * it" -- which no window answered whole.
 *
 * Ownership is a public fact: whoever enters sees the owner, a person or a
 * city, so the window is shown to guests too -- read-only. The name is given
 * by whoever disposes of the land, and the map label changes, not the node
 * key: deeds and edges reference the key. The door and the lists belong to the
 * holder alone: civic land is regulated by citizenship and duties, not by a
 * list of names.
 */
/**
 * Marking a strip out of the land one holds.
 *
 * It lives here rather than in "Земледелие" because it is a thing done to
 * **land**: the window of farming is about the cycle -- ploughing, sowing, the
 * daily round, the harvest -- and that cycle has nowhere to happen until a
 * strip exists. So the land gives birth to the plot, and the plot brings its
 * own window with it.
 */
function Marking({ look, busy, act }: Props) {
  const session = useSession();
  const [name, setName] = useState("");
  const [metres, setMetres] = useState(100);
  const marked = look.node?.plots ?? 0;
  //: Nothing grows in the open ground of a climate (D-231): the server refuses,
  //: and the window says so before the refusal rather than after it.
  const climate = look.frost?.climate ?? null;

  if ((look.node?.fertility ?? 0) <= 0) return null;
  return (
    <div className="pocket">
      <h3>Делянки</h3>
      {climate ? (
        <p className="note">
          Здесь {climate}: в открытом грунте ничего не растёт, и обогрев узла
          этого не меняет. Еда сюда приходит кораблём.
        </p>
      ) : (
        <>
          <div className="row">
            <input
              value={name}
              placeholder="имя делянки"
              onChange={(e) => setName(e.target.value)}
            />
            <input
              type="number"
              value={metres}
              onChange={(e) => setMetres(Number(e.target.value))}
              title="площадь, м²"
            />
            <button
              onClick={() =>
                act(async () => {
                  await session.send("farm.mark", { name, area: metres });
                  setName("");
                })
              }
              disabled={busy}
            >
              Разметить
            </button>
          </div>
          <p className="note">
            {marked > 0
              ? `Размечено делянок: ${marked}. Работа с ними — в окне «Земледелие».`
              : "Размеченная делянка откроет окно «Земледелие»: вспашка, посев, уход и уборка."}
          </p>
        </>
      )}
    </div>
  );
}


export function Plot({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This window's own waiting and its own refusal: shutting the door here must
  //: not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;
  const node = look.node;
  const [name, setName] = useState("");
  //: Handing a plot over is asked twice: the deed is cancelled by it, and the
  //: way back is a purchase at the price list.
  const [giving, setGiving] = useState(false);
  if (!node) return null;

  //: Same three cases as the old purchase window: nobody's city land with a
  //: price, and the wild beyond the walls. An owned node is never for sale here.
  const forSale = !node.owner && (api.isWild(node) || node.price !== undefined);
  const owned = Boolean(node.owner || node.owner_city);
  if (!forSale && !owned) return null;

  //: Who the meter is charged to (D-149). Ownership does not answer it by
  //: itself: a bought plot stays civic land, yet its bill is a person's.
  const upkeep =
    node.upkeep === "owner"
      ? "За электричество здесь платите вы: счёт идёт с площади раз в период."
      : node.upkeep === "city"
        ? `Узел содержит город${node.owner_city ? ` ${node.owner_city}` : ""}: энергия уходит из городского пула, деньгами счёт не выставляется.`
        : node.upkeep === "nobody"
          ? "Счётчика здесь нет: у узла нет хозяина, и выставлять счёт некому."
          : node.owner || node.owner_city
            ? "Городской сети здесь нет: счёта за электричество не бывает, работают от аккумулятора."
            : null;

  //: Во что обходится держать участок сутки (D-127, D-220). Стоит рядом с
  //: ценой выкупа не для симметрии: ставка убывает с каждым узлом от
  //: биопринтера, поэтому центр дорог дважды — и купить, и держать, — и вторую
  //: половину счёта покупатель обязан видеть до того, как заплатит первую.
  const tax = node.tax > 0
    ? `Земельный налог: ${api.tk(node.tax)} ₭ в сутки со всей площади участка — застроен он или нет.`
      + ` Чем дальше от биопринтера, тем ставка ниже.`
    : null;

  const mine = api.isMine(look);
  const whose = mine
    ? "ваш участок"
    : node.owner
      ? `хозяин ${node.owner}`
      : node.owner_city
        ? `земля города ${node.owner_city}`
        : "ничей";

  return (
    <>
    <section>
      <Refusal of={acting} />
      <h2>Участок</h2>
      <p className="note">
        {node.name} · {node.area.toFixed(0)} м² · {whose}
        {node.gated && " · закрыта для входа"}
        {node.cut_off && " · отключена за неуплату"}
      </p>
      {tax && <p className="note">{tax}</p>}
      {upkeep && <p className="note">{upkeep}</p>}
      {/* Only civic land is handed over: a ship's cabin is owned too, and there
          is no city under it to take it. */}
      {mine && node.owner_city && (
        giving ? (
          <div className="row">
            <button onClick={() => act(async () => {
              await session.send("land.cede");
              setGiving(false);
            })} disabled={busy}>
              Да, передать городу
            </button>
            <button onClick={() => setGiving(false)} disabled={busy}>
              Отмена
            </button>
            <span className="note">
              Бумага на землю погашается, участок станет городским. Вернуть его
              можно только выкупом по прейскуранту — как любой другой.
            </span>
          </div>
        ) : (
          <div className="row">
            <button onClick={() => setGiving(true)} disabled={busy}>
              Передать городу
            </button>
            <span className="note">
              Счётчик перейдёт на казну: городской узел жжёт энергию из пула, и
              деньгами за него никто не платит. Оборудование останется на месте,
              но распоряжаться им будет власть, а не вы.
            </span>
          </div>
        )
      )}
      {forSale &&
        (api.isWild(node) ? (
          <p className="note">
            Земля за городом ничья и таковой остаётся: бумагу на владение
            выдаёт город, а здесь его нет. Работать и строить тут
            может всякий — поставленное принадлежит поставившему.
          </p>
        ) : node.price !== undefined ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.buy"))} disabled={busy}>
              Выкупить за {api.tk(node.price)} ₭
            </button>
            <span className="note">
              Цена от удалённости до биопринтера: деньги в казну,
              вам — бумага на землю.
            </span>
          </div>
        ) : null)}
      {node.may_name && (
        <div className="row">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={node.name}
            //: Repeats `runtime.LAND_NAME_LIMIT`: better to show the limit by
            //: the input field than to report it as a refusal after a click.
            maxLength={40}
            title="как называть это место"
          />
          <button
            onClick={() =>
              act(async () => {
                await session.send("land.rename", { name: name });
                setName("");
              })
            }
            disabled={busy || !name.trim() || name.trim() === node.name}
          >
            Переименовать
          </button>
          <span className="note">
            Имя увидят все на карте; ключ локации не меняется.
          </span>
        </div>
      )}
      {mine && <Door look={look} busy={busy} act={act} />}
      {mine && <Marking look={look} busy={busy} act={act} />}
    </section>
    {/* Founding a city is the plot's fate, so the section stands here:
        the server offers it only where founding is possible at all. */}
    <Foundation look={look} busy={busy} act={act} />
    </>
  );
}
