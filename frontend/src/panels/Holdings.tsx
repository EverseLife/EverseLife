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
 *
 * The one exception to "this is money, not matter" is the way home: a holding
 * is a place as well as a bill, and the row that names it is where one decides
 * to go there. The button only sends `travel.go` -- the route builds itself
 * (D-045) -- and the one refusal it answers itself is "the nodes share no
 * edge", which belongs beside the row rather than in the panel's shared strip:
 * it is a fact about that holding and no other.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useEdition, useNames, useSession } from "../actions";
import { t } from "../locale";
import { goodsName } from "../names";
import * as api from "../api";
import type { DeedView, Holding, Look, Thing } from "../api";
import { Refused } from "../api";
import { Rule } from "../Rule";
import { NumberField } from "../NumberField";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Grid = { city: string; stored: number; tariff: number };

export function Holdings({ look, busy, act }: Props) {
  const session = useSession();
  const names = useNames();
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
      .filter((held: Thing) => held.charge != null)
      .map((thing) => ({
        id: thing.id,
        goods: thing.goods,
        charge: thing.charge!,
        where: t("ui-holdings-in-hands"),
      })),
    ...(look.bench ?? [])
      .filter((machine) => machine.charge != null)
      .map((machine) => ({
        id: machine.id,
        goods: machine.goods,
        charge: machine.charge!,
        where: t("ui-holdings-here"),
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
  //: The pool line too: a charge, the tick's own production or the alpha's
  //: print into the pool all move the number this panel shows -- that one
  //: kind, not every print of a thing.
  const edition = useEdition("deed.", "land.", "building.", "energy.", "alpha.energized");

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

  //: Which holding the graph has no way to. Kept by node key rather than as a
  //: flag: two rows may be asked in turn, and the answer belongs to the one it
  //: was given about.
  const [noWay, setNoWay] = useState<string | null>(null);
  //: The answer was about where the body stood, not about the row: a road laid
  //: since, or a step onto the other side of a river, and it is stale. Cleared
  //: with every reread rather than only before the next attempt -- otherwise
  //: it sat under the row for the rest of the session.
  useEffect(() => setNoWay(null), [where, edition]);
  const walk = (node: string) =>
    go(async () => {
      setNoWay(null);
      try {
        await session.send("travel.go", { node });
      } catch (error) {
        //: Matched on the key, never on the words (`session.Refused`). Only
        //: the one on foot: with a convoy harnessed the same key means the
        //: trackless ground will not take it, and the way out is to unhitch --
        //: which the engine's own sentence says and this row's does not. Every
        //: other refusal -- already on the road, no strength, confinement --
        //: goes to the panel's shared strip in the engine's words: it is about
        //: the body, not about the holding.
        const foot =
          error instanceof Refused &&
          error.code === "travel-no-route" &&
          error.args?.how === "foot";
        if (foot) {
          setNoWay(node);
          return;
        }
        throw error;
      }
    });

  const debt = holdings.reduce((amount, node) => amount + node.debt, 0);

  return (
    <div>
      {trouble && (
        <p className="trouble">{t("ui-holdings-stale")}</p>
      )}
      <h3>
        {t("ui-holdings-grid")}
        <Rule>{t("ui-holdings-grid-rule")}</Rule>
      </h3>
      {grid === undefined ? (
        <p className="note">{t("ui-holdings-grid-asking")}</p>
      ) : grid ? (
        <p className="sign">
          {t("ui-holdings-grid-pool", {
            city: grid.city,
            stored: grid.stored.toFixed(0),
            tariff: grid.tariff,
          })}
        </p>
      ) : (
        <p className="note">{t("ui-holdings-grid-none")}</p>
      )}

      <h3>{t("ui-holdings-batteries")}</h3>
      {batteries.length === 0 ? (
        <p className="note">{t("ui-holdings-batteries-none")}</p>
      ) : (
        <table>
          <tbody>
            {batteries.map((battery) => (
              <tr key={battery.id}>
                <td>
                  {goodsName(names, battery.goods)}
                  <span className="note"> · {battery.where}</span>
                </td>
                <td className="num">{battery.charge.toFixed(0)}</td>
                <td>
                  <button
                    onClick={() => go(() => session.send("energy.charge", { item: battery.id }))}
                    disabled={busy || !grid || Boolean(look.travel)}
                    title={
                      grid
                        ? t("ui-holdings-charge-hint")
                        : grid === undefined
                          ? t("ui-holdings-charge-asking")
                          : t("ui-holdings-charge-no-grid")
                    }
                  >
                    {t("ui-holdings-charge")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {holdings.length > 0 && (
        <>
          <h3>{t("ui-holdings-title")}</h3>
          <table>
            <tbody>
              {holdings.map((node) => (
                <tr key={node.node}>
                  <td>
                    {node.name}
                    <span className="note">
                      {" "}
                      {t("ui-holdings-area", { area: node.area.toFixed(0) })}
                    </span>
                    {node.cut_off && <b> {t("ui-holdings-cut-off")}</b>}
                  </td>
                  <td className="num">
                    {node.grid
                      ? t("ui-holdings-per-period", {
                          cost: api.tk(node.cost_per_period),
                        })
                      : t("ui-holdings-no-grid")}
                  </td>
                  <td className="num">
                    {node.debt > 0
                      ? t("ui-holdings-debt", { amount: api.tk(node.debt) })
                      : "—"}
                  </td>
                  <td>
                    {node.debt > 0 && (
                      <button
                        onClick={() =>
                          go(() => session.send("utility.pay", { node: node.node }))
                        }
                        disabled={busy}
                      >
                        {t("ui-holdings-pay")}
                      </button>
                    )}
                    {/* Standing in it, one is home already: a button that can
                        only be refused is not an offer. */}
                    {where === node.node ? (
                      <span className="note">{t("ui-holdings-home-here")}</span>
                    ) : (
                      <button
                        className="quiet"
                        onClick={() => walk(node.node)}
                        disabled={busy || Boolean(look.travel)}
                        title={t("ui-holdings-home-hint")}
                      >
                        {t("ui-holdings-home")}
                      </button>
                    )}
                    {noWay === node.node && (
                      <p className="reason">{t("ui-holdings-no-way")}</p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            {t("ui-holdings-bill-rule")}
            {debt > 0 && t("ui-holdings-debt-total", { amount: api.tk(debt) })}
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
        {t("ui-holdings-deeds")}
        <Rule>{t("ui-holdings-deeds-rule")}</Rule>
      </h3>
      {my.length === 0 ? (
        <p className="note">{t("ui-holdings-deeds-none")}</p>
      ) : (
        <table>
          <tbody>
            {my.map((deed) => (
              <tr key={deed.id}>
                <td>
                  {deed.name ?? deed.node}
                  <span className="note">
                    {" "}
                    {t("ui-holdings-deed-area", { area: deed.area?.toFixed(0) ?? "?" })}
                  </span>
                </td>
                <td className="note">
                  {deed.sale_price != null
                    ? t("ui-holdings-deed-sale", { price: api.tk(deed.sale_price) }) +
                      (deed.sale_to
                        ? t("ui-holdings-deed-sale-to", { who: deed.sale_to })
                        : "")
                    : t("ui-holdings-deed-not-sold")}
                </td>
                <td>
                  {deed.sale_price == null ? (
                    <span className="row">
                      <NumberField
                        min={0}
                        placeholder={t("ui-holdings-price")}
                        //: Nothing typed yet is an empty box, not a zero: the
                        //: placeholder is what says what the box is for.
                        value={prices[deed.id] ?? null}
                        //: An emptied box is no price at all, and the sale
                        //: button is shut until there is one.
                        onChange={(typed) => setPrices({ ...prices, [deed.id]: typed ?? 0 })}
                        title={t("ui-holdings-price-hint")}
                      />
                      <input
                        placeholder={t("ui-holdings-to-whom")}
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
                        {t("ui-holdings-sell")}
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
                      {t("ui-holdings-unsell")}
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
          <h3>{t("ui-holdings-market")}</h3>
          <table>
            <tbody>
              {market.map((deed) => (
                <tr key={deed.id}>
                  <td>
                    {deed.name ?? deed.node}
                    <span className="note">
                      {" "}
                      {t("ui-holdings-deed-market-area", {
                        area: deed.area?.toFixed(0) ?? "?",
                        owner: deed.owner,
                      })}
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
                      {t("ui-holdings-buy")}
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
