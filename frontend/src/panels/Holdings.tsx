// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Holdings: the city grid, batteries and household bills (D-071, D-135, D-149).
 *
 * The tab lives in the sidebar, not the location, for the same reason orders
 * live there: **this is money, not matter**. A node's bill comes once a
 * period, is paid from anywhere and depends on no place.
 *
 * The "holdings" section is shown only to those who have holdings: most do
 * not, and an empty table "your nodes: --" would be noise.
 *
 * Charging a battery is the only in-person action here, and it is named
 * in-person: the server refuses if there is no city around.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useEdition, useSession } from "../actions";
import * as api from "../api";
import type { DeedView, Holding, Look, Thing } from "../api";
import { Rule } from "../Rule";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Grid = { city: string; stored: number; tariff: number };

export function Holdings({ look, busy, act }: Props) {
  const session = useSession();
  //: Three states, not two: `undefined` is "not asked yet". Starting at `null`
  //: made the panel open with "there is no grid here" -- a statement about the
  //: world, printed before the world had been asked, and wrong wherever a grid
  //: does exist. The battery button read the same `null` and greyed itself out
  //: with "нет сети" on it.
  const [grid, setGrid] = useState<Grid | null | undefined>(undefined);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [deedMarket, setDeedMarket] = useState<DeedView[]>([]);
  //: Whether the last reading failed outright, and the tables below are older
  //: than they look.
  const [trouble, setTrouble] = useState(false);
  //: Which reading is the current one. The grid belongs to the city the body
  //: stands in, so walking out of the walls while the answers are in flight
  //: would otherwise settle the pool of the city just left.
  const asked = useRef(0);
  //: A battery is a machine (D-179): it is either in the hands or stands
  //: here. Both are charged the same, and there is no reason to keep two windows for that.
  const batteries: { id: string; goods: string; charge: number; where: string }[] = [
    ...look.inventory
      .filter((t: Thing) => t.charge != null)
      .map((t) => ({ id: t.id, goods: t.goods, charge: t.charge!, where: "в руках" })),
    ...(look.bench ?? [])
      .filter((machine) => machine.charge != null)
      .map((machine) => ({
        id: machine.id,
        goods: machine.goods,
        charge: machine.charge!,
        where: "стоит здесь",
      })),
  ];

  const reload = useCallback(async () => {
    const mine = ++asked.current;
    const current = () => mine === asked.current;
    try {
      const gridAnswer = await session.send("energy.grid");
      if (current()) setGrid((gridAnswer.grid as Grid | null) ?? null);
    } catch {
      //: `energy.grid` asks where the body stands, and in the cloud there is no
      //: body to ask about: it refuses rather than answering "no grid". That
      //: refusal is an answer -- there is no grid to be had from here -- and
      //: without catching it the panel waited for a reply that never came and
      //: sat on "Сеть опрашивается…" for the rest of the session.
      if (current()) setGrid(null);
    }
    try {
      const own = await session.send("utility.holdings");
      if (current()) setHoldings((own.holdings as Holding[]) ?? []);
      //: Deeds that can be bought: open contracts and those addressed to me.
      const deeds = await session.send("deed.market");
      if (current()) {
        setDeedMarket((deeds.deeds as DeedView[]) ?? []);
        setTrouble(false);
      }
    } catch {
      //: Neither of these two asks about a body, so they refuse for one reason
      //: only: the server did not answer at all. Left uncaught inside the
      //: `void reload()` of an effect, that is an unhandled rejection and a
      //: table quietly frozen on the last answer.
      if (current()) setTrouble(true);
    }
  }, [session]);
  //: Reread when the world says so (D-226), not on every look.
  const edition = useEdition("deed.", "land.", "building.");

  //: The node is a dependency of its own: the grid belongs to the city the body
  //: stands in, so walking out of the walls changes the answer while no edition
  //: does. Without it the panel kept showing the pool of the city just left,
  //: and offered a charge the server was about to refuse.
  const where = look.node?.key;
  useEffect(() => {
    void reload();
  }, [reload, edition, where]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  const debt = holdings.reduce((amount, node) => amount + node.debt, 0);

  return (
    <div>
      {trouble && (
        <p className="trouble">
          Сервер не ответил: то, что ниже, — прошлое чтение. Нажмите «обновить».
        </p>
      )}
      <h3>
        Городская сеть
        <Rule>
          Городские постройки содержит казна: энергия, которую они жгут, — расход
          города, а не посетителя.
        </Rule>
      </h3>
      {grid === undefined ? (
        <p className="note">Сеть опрашивается…</p>
      ) : grid ? (
        <p className="sign">
          {grid.city}: в пуле {grid.stored.toFixed(0)} · тариф {grid.tariff} ₭ за 100
        </p>
      ) : (
        <p className="note">
          Здесь городской сети нет: вне города работают от аккумулятора, и
          заряжают его в городе.
        </p>
      )}

      <h3>Аккумуляторы</h3>
      {batteries.length === 0 ? (
        <p className="note">
          Аккумулятора нет: энергия либо в пуле города, либо в аккумуляторе.
        </p>
      ) : (
        <table>
          <tbody>
            {batteries.map((battery) => (
              <tr key={battery.id}>
                <td>
                  {battery.goods}
                  <span className="note"> · {battery.where}</span>
                </td>
                <td className="num">{battery.charge.toFixed(0)}</td>
                <td>
                  <button
                    onClick={() => go(() => session.send("energy.charge", { item: battery.id }))}
                    disabled={busy || !grid || Boolean(look.travel)}
                    title={
                      grid
                        ? "залить доверху по тарифу"
                        : grid === undefined
                          ? "сеть ещё опрашивается"
                          : "здесь нет сети"
                    }
                  >
                    Зарядить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {holdings.length > 0 && (
        <>
          <h3>Владения и счета</h3>
          <table>
            <tbody>
              {holdings.map((node) => (
                <tr key={node.node}>
                  <td>
                    {node.name}
                    <span className="note"> · {node.area.toFixed(0)} м²</span>
                    {node.cut_off && <b> · отключён</b>}
                  </td>
                  <td className="num">
                    {node.grid
                      ? `${api.tk(node.cost_per_period)} ₭ / период`
                      : "нет сети"}
                  </td>
                  <td className="num">
                    {node.debt > 0 ? `долг ${api.tk(node.debt)} ₭` : "—"}
                  </td>
                  <td>
                    {node.debt > 0 && (
                      <button
                        onClick={() =>
                          go(() => session.send("utility.pay", { node: node.node }))
                        }
                        disabled={busy}
                      >
                        Оплатить
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Счёт считается с площади — свет, тепло, вентиляция. Не заплатил —
            узел отключён, и рабочие станции в нём стоят, пока долг не закрыт. Отобрать
            узел за долг движок не вправе: это решение суда.
            {debt > 0 && <> Сейчас долгов на {api.tk(debt)} ₭.</>}
          </p>
        </>
      )}

      <Deeds
        my={look.deeds ?? []}
        market={deedMarket}
        busy={busy}
        go={go}
      />
    </div>
  );
}

/** Deeds for plots: electronic documents of the Net (D-116).
 *
 * A deed is ownership documented: it lives with the identity, survives the
 * body and is sold by a sale contract -- to everyone or addressed. Money and
 * title pass in one deal, no escrow is needed. */

function Deeds({
  my,
  market,
  busy,
  go,
  }: {
  my: DeedView[];
  market: DeedView[];
  busy: boolean;
  go: (what: () => Promise<unknown>) => Promise<void>;
}) {
  const session = useSession();
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [toWhom, setToWhom] = useState<Record<string, string>>({});
  if (my.length === 0 && market.length === 0) return null;

  return (
    <>
      <h3>
        Ценные бумаги
        <Rule>
          Бумага — электронный документ: живёт в Сети, переживает тело и продаётся
          отсюда, хоть с дороги. Титул на участок переходит вместе с ней.
        </Rule>
      </h3>
      {my.length === 0 ? (
        <p className="note">
          Своих бумаг нет. Бумага появляется с участком: выкупили или заняли
          землю — владение оформлено документом.
        </p>
      ) : (
        <table>
          <tbody>
            {my.map((deed) => (
              <tr key={deed.id}>
                <td>
                  {deed.name ?? deed.node}
                  <span className="note">
                    {" "}
                    · {deed.area?.toFixed(0) ?? "?"} м²
                  </span>
                </td>
                <td className="note">
                  {deed.sale_price != null
                    ? `продаётся за ${api.tk(deed.sale_price)} ₭` +
                      (deed.sale_to ? ` · для ${deed.sale_to}` : "")
                    : "не продаётся"}
                </td>
                <td>
                  {deed.sale_price == null ? (
                    <span className="row">
                      <input
                        type="number"
                        min={0}
                        placeholder="цена, ₭"
                        value={prices[deed.id] ?? ""}
                        onChange={(e) =>
                          setPrices({ ...prices, [deed.id]: Number(e.target.value) })
                        }
                        title="цена договора, ₭"
                      />
                      <input
                        placeholder="кому (пусто — всем)"
                        value={toWhom[deed.id] ?? ""}
                        onChange={(e) =>
                          setToWhom({ ...toWhom, [deed.id]: e.target.value })
                        }
                      />
                      <button
                        className="quiet"
                        onClick={() =>
                          go(() =>
                            session.send("deed.offer", {
                              deed: deed.id,
                              price: api.minor(prices[deed.id] ?? 0),
                              to: (toWhom[deed.id] ?? "").trim() || undefined,
                            }),
                          )
                        }
                        disabled={busy || !(prices[deed.id] > 0)}
                      >
                        Продать
                      </button>
                    </span>
                  ) : (
                    <button
                      className="quiet"
                      onClick={() =>
                        go(() =>
                          session.send("deed.offer", { deed: deed.id, price: 0 }),
                        )
                      }
                      disabled={busy}
                    >
                      Снять с продажи
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {market.length > 0 && (
        <>
          <h3>Бумаги на продажу</h3>
          <table>
            <tbody>
              {market.map((deed) => (
                <tr key={deed.id}>
                  <td>
                    {deed.name ?? deed.node}
                    <span className="note">
                      {" "}
                      · {deed.area?.toFixed(0) ?? "?"} м² · у {deed.owner}
                    </span>
                  </td>
                  <td className="num">{api.tk(deed.sale_price ?? 0)} ₭</td>
                  <td>
                    <button
                      onClick={() =>
                        go(() => session.send("deed.buy", { deed: deed.id }))
                      }
                      disabled={busy}
                    >
                      Купить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
