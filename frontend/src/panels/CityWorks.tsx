// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * City orders on the works board, and the treasury as a borrower (D-248).
 *
 * The city names what it wants done -- mending its plot, raising a house,
 * hauling fuel to a station -- and what it pays for the non-labour part; the
 * fund adds its share of the labour tariff. Both are escrowed at posting, so
 * the refusal comes here, not at the payout. The forms are deliberately
 * plain: the redesign (D-238) will reshape these screens, and the engine
 * checks every field anyway.
 */

import { useCallback, useEffect, useState } from "react";
import { useBook, useNames, useSession } from "../actions";
import * as api from "../api";
import { when } from "../clock";
import { t } from "../locale";
import { Rule } from "../Rule";

type Props = {
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The capital does not borrow from itself (D-175): it prints (D-270). */
  capital?: boolean;
};

/** The kinds of order this board posts, each by the message that names it. */
const CITY_KINDS: Record<string, string> = {
  building_repair: "ui-city-works-repair",
  building_build: "ui-city-works-build",
  fuel_delivery: "ui-city-works-fuel",
};

export function CityWorks({ busy, act, capital = false }: Props) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  const [orders, setOrders] = useState<api.WorksOrder[]>([]);
  const [loans, setLoans] = useState<api.CityLoans | null>(null);
  const [node, setNode] = useState("");
  const [offer, setOffer] = useState(0);
  const [buildKind, setBuildKind] = useState("");
  const [footprint, setFootprint] = useState(10);
  const [floors, setFloors] = useState(1);
  const [fuel, setFuel] = useState("");
  const [fuelAmount, setFuelAmount] = useState(10);
  const [fuelPrice, setFuelPrice] = useState(1);
  const [borrow, setBorrow] = useState(50);

  const refresh = useCallback(async () => {
    try {
      const board = (await session.send("works.board", {})) as api.WorksBoard;
      setOrders((board.orders ?? []).filter((row) => row.kind in CITY_KINDS));
      setLoans((await session.send("city.loans", {})) as api.CityLoans);
    } catch {
      setOrders([]);
      setLoans(null);
    }
  }, [session]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await refresh();
    });

  //: The player types the Russian word; the command carries the id (D-251).
  //: Goods resolve through the book's synonyms, a building kind through the
  //: reverse of the renames table; an unknown word travels as typed and the
  //: engine's own resolve() has the last word.
  const goodsId = (word: string) => book?.synonyms?.[word.trim()] ?? word.trim();
  const kindId = (word: string) => {
    const typed = word.trim();
    const hit = Object.entries(names?.building_kinds ?? {}).find(
      ([, ru]) => ru.toLowerCase() === typed.toLowerCase(),
    );
    return hit?.[0] ?? typed;
  };

  return (
    <>
      <h3>
        {t("ui-city-works-title")}
        <Rule>{t("ui-city-works-rule")}</Rule>
      </h3>

      {orders.length > 0 && (
        <table>
          <tbody>
            {orders.map((order) => (
              <tr key={order.id}>
                <td className="note">
                  {order.kind in CITY_KINDS ? t(CITY_KINDS[order.kind]) : order.kind} ·{" "}
                  {order.node ?? "—"} · {api.tk(order.tariff)} ₭ · {when(order.posted_at)}
                </td>
                <td>
                  <button
                    className="quiet"
                    onClick={() => go(() => session.send("city.works_cancel", { order: order.id }))}
                    disabled={busy}
                  >
                    {t("ui-city-works-cancel")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="form">
        <label>
          <span>{t("ui-city-works-node")}</span>
          <input value={node} onChange={(e) => setNode(e.target.value)} />
        </label>
        <label>
          <span>{t("ui-city-works-offer")}</span>
          <input
            type="number"
            min={0}
            value={offer}
            onChange={(e) => setOffer(Number(e.target.value))}
          />
        </label>
        <button
          onClick={() => go(() => session.send("city.works_repair", { node, offer }))}
          disabled={busy || !node}
        >
          {t("ui-city-works-order-repair")}
        </button>
      </div>

      <div className="form">
        <label>
          <span>{t("ui-city-works-kind")}</span>
          <input value={buildKind} onChange={(e) => setBuildKind(e.target.value)} />
        </label>
        <label>
          <span>{t("ui-city-works-footprint")}</span>
          <input
            type="number"
            min={1}
            value={footprint}
            onChange={(e) => setFootprint(Number(e.target.value))}
          />
        </label>
        <label>
          <span>{t("ui-city-works-floors")}</span>
          <input
            type="number"
            min={1}
            value={floors}
            onChange={(e) => setFloors(Number(e.target.value))}
          />
        </label>
        <button
          onClick={() =>
            go(() =>
              session.send("city.works_build", {
                node,
                kind: kindId(buildKind),
                footprint,
                floors,
                offer,
              }),
            )
          }
          disabled={busy || !node || !buildKind}
        >
          {t("ui-city-works-order-build")}
        </button>
      </div>

      <div className="form">
        <label>
          <span>{t("ui-city-works-fuel-label")}</span>
          <input value={fuel} onChange={(e) => setFuel(e.target.value)} />
        </label>
        <label>
          <span>{t("ui-city-works-amount")}</span>
          <input
            type="number"
            min={1}
            value={fuelAmount}
            onChange={(e) => setFuelAmount(Number(e.target.value))}
          />
        </label>
        <label>
          <span>{t("ui-city-works-price")}</span>
          <input
            type="number"
            min={0}
            step={0.1}
            value={fuelPrice}
            onChange={(e) => setFuelPrice(Number(e.target.value))}
          />
        </label>
        <button
          onClick={() =>
            go(() =>
              session.send("city.works_fuel", {
                node,
                fuel: goodsId(fuel),
                amount: fuelAmount,
                price: fuelPrice,
              }),
            )
          }
          disabled={busy || !node || !fuel}
        >
          {t("ui-city-works-order-fuel")}
        </button>
      </div>

      <h3>
        {t("ui-city-loan-title")}
        <Rule>{t("ui-city-loan-rule")}</Rule>
      </h3>
      {loans && (
        <p className="note">
          {t("ui-city-loan-line", {
            occupied: api.tk(loans.line.occupied),
            permitted: api.tk(loans.line.permitted),
          })}
        </p>
      )}
      {loans &&
        (loans.loans ?? []).map((loan) => (
          <div className="row" key={loan.id}>
            <span className="note">
              {t("ui-city-loan-row", {
                outstanding: api.tk(loan.outstanding),
                principal: api.tk(loan.principal),
                rate: Number(loan.rate).toFixed(2),
                taken: when(loan.taken_at),
              })}
            </span>
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.loan_repay", { loan: loan.id }))}
              disabled={busy}
            >
              {t("ui-city-loan-repay")}
            </button>
          </div>
        ))}
      {!capital && (
        <div className="row">
          <input
            type="number"
            min={1}
            value={borrow}
            onChange={(e) => setBorrow(Number(e.target.value))}
          />
          <button
            onClick={() => go(() => session.send("city.borrow", { amount: borrow }))}
            disabled={busy || borrow <= 0}
          >
            {t("ui-city-borrow")}
          </button>
        </div>
      )}
    </>
  );
}
