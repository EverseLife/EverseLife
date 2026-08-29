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
import { useSession } from "../actions";
import * as api from "../api";
import { when } from "../clock";
import { Rule } from "../Rule";

type Props = {
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

const CITY_KINDS: Record<string, string> = {
  building_repair: "ремонт постройки",
  building_build: "стройка",
  fuel_delivery: "подвоз топлива",
};

export function CityWorks({ busy, act }: Props) {
  const session = useSession();
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

  return (
    <>
      <h3>
        Госзаказ
        <Rule>
          Город называет работу и свою цену за нетрудовое — материалы, топливо; фонд
          работ доплачивает долю трудового тарифа. Деньги откладываются при вывеске:
          пустая казна или пустой фонд откажут сразу. Заказ — это и лицензия: пока он
          висит, чинить и строить на участке города может любой.
        </Rule>
      </h3>

      {orders.length > 0 && (
        <table>
          <tbody>
            {orders.map((order) => (
              <tr key={order.id}>
                <td className="note">
                  {CITY_KINDS[order.kind] ?? order.kind} · {order.node ?? "—"} ·{" "}
                  {api.tk(order.tariff)} ₭ · {when(order.posted_at)}
                </td>
                <td>
                  <button
                    className="quiet"
                    onClick={() => go(() => session.send("city.works_cancel", { order: order.id }))}
                    disabled={busy}
                  >
                    Отозвать
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="form">
        <label>
          <span>участок (ключ узла)</span>
          <input value={node} onChange={(e) => setNode(e.target.value)} />
        </label>
        <label>
          <span>предложение города, ₭ — за материалы или топливо</span>
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
          Заказать ремонт
        </button>
      </div>

      <div className="form">
        <label>
          <span>тип дома</span>
          <input value={buildKind} onChange={(e) => setBuildKind(e.target.value)} />
        </label>
        <label>
          <span>пятно, м²</span>
          <input
            type="number"
            min={1}
            value={footprint}
            onChange={(e) => setFootprint(Number(e.target.value))}
          />
        </label>
        <label>
          <span>этажей</span>
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
                kind: buildKind,
                footprint,
                floors,
                offer,
              }),
            )
          }
          disabled={busy || !node || !buildKind}
        >
          Заказать стройку
        </button>
      </div>

      <div className="form">
        <label>
          <span>топливо</span>
          <input value={fuel} onChange={(e) => setFuel(e.target.value)} />
        </label>
        <label>
          <span>сколько единиц</span>
          <input
            type="number"
            min={1}
            value={fuelAmount}
            onChange={(e) => setFuelAmount(Number(e.target.value))}
          />
        </label>
        <label>
          <span>цена за единицу, ₭</span>
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
                fuel,
                amount: fuelAmount,
                price: fuelPrice,
              }),
            )
          }
          disabled={busy || !node || !fuel}
        >
          Заказать подвоз
        </button>
      </div>

      <h3>
        Кредит казне
        <Rule>
          Казна занимает у ЦБ на общественные работы: по ключевой, без маржи и без
          надбавок, на общей кредитной линии города — той же, что несёт займы граждан.
        </Rule>
      </h3>
      {loans && (
        <p className="note">
          линия: занято {api.tk(loans.line.occupied)} ₭ из {api.tk(loans.line.permitted)} ₭
        </p>
      )}
      {loans &&
        (loans.loans ?? []).map((loan) => (
          <div className="row" key={loan.id}>
            <span className="note">
              осталось {api.tk(loan.outstanding)} ₭ из {api.tk(loan.principal)} ₭ под{" "}
              {Number(loan.rate).toFixed(2)}% · взят {when(loan.taken_at)}
            </span>
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.loan_repay", { loan: loan.id }))}
              disabled={busy}
            >
              Погасить из казны
            </button>
          </div>
        ))}
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
          Занять у ЦБ ₭
        </button>
      </div>
    </>
  );
}
