// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The city's figures, read from anywhere, shown to whoever asks.
 *
 * This is not a part of the administration window; it merely appears in one of
 * its tabs. The panel is read **remotely** (D-140) and belongs equally to the
 * Economy tab of the sidebar, and it is the one thing here that grants nothing
 * and changes nothing -- it only counts. Hence its own file: two windows show
 * it, and neither of them owns it.
 *
 * The figures are common knowledge, because there is nothing to argue with the
 * authority about without them. The treasury by line item is the exception:
 * that section arrives only for those holding `dashboard`, and its absence is
 * a sentence rather than an empty table -- a closed ledger is a fact about the
 * city worth reading.
 */

import type { CityPanel } from "../../api";
import { groundName } from "../../grounds";
import { t } from "../../locale";
import { goodsName } from "../../names";
import { Rule } from "../../Rule";
import { useNames } from "../../actions";

/** The economic panel: the public snapshot plus the treasury for those with the right. */
export function Panel({ panel }: { panel: CityPanel | null }) {
  //: Called before the early returns: a hook must run on every render.
  const names = useNames();
  if (!panel) return <p className="note">{t("ui-admin-panel-none")}</p>;
  if (panel.blind) {
    return <p className="trouble">{t("ui-admin-panel-blind")}</p>;
  }
  //: A section may not arrive: the server may be older than the client, and
  //: the panel may not crash the whole screen over one missing summary line.
  const market = panel.market ?? { trades: 0, volume: 0, prices: {} };
  const people = panel.people ?? { here: 0, printed: 0 };
  const energy = panel.energy ?? {
    stored: 0,
    tariff: 0,
    spent_work: 0,
    spent_home: 0,
  };
  const work = panel.production ?? { mined: {}, harvested: 0, crafted: {} };
  const border = panel.trade ?? {
    imported: {},
    exported: {},
    trips_in: 0,
    trips_out: 0,
    duty_collected: 0,
  };
  const prices = Object.entries(market.prices ?? {});
  const goods = Object.entries(panel.goods ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12);

  return (
    <div>
      <p className="sign">
        {t("ui-admin-panel-sign", {
          hours: String(panel.window_hours),
          trades: String(market.trades),
          volume: market.volume.toFixed(2),
        })}
        <Rule>{t("ui-admin-panel-rule")}</Rule>
      </p>

      <h3>{t("ui-admin-panel-people")}</h3>
      <p>
        {t("ui-admin-panel-people-line", {
          here: String(people.here),
          printed: String(people.printed),
        })}
      </p>

      <h3>{t("ui-admin-panel-energy")}</h3>
      <p>
        {t("ui-admin-panel-energy-line", {
          stored: energy.stored.toFixed(0),
          tariff: String(energy.tariff),
          work: energy.spent_work.toFixed(0),
          home: energy.spent_home.toFixed(0),
        })}
      </p>

      <h3>{t("ui-admin-panel-border")}</h3>
      <p>
        {t("ui-admin-panel-border-line", {
          imported: weighed(border.imported, names),
          exported: weighed(border.exported, names),
        })}
      </p>
      <p className="note">
        {t("ui-admin-panel-trips", {
          in: String(border.trips_in),
          out: String(border.trips_out),
          duty: border.duty_collected.toFixed(2),
        })}
      </p>

      <h3>{t("ui-admin-panel-production")}</h3>
      <p>
        {t("ui-admin-panel-production-line", {
          mined: (work.mined?.["total"] ?? 0).toFixed(1),
          harvested: (work.harvested ?? 0).toFixed(1),
          crafted:
            Object.entries(work.crafted ?? {})
              .map(([name, qty]) => `${goodsName(names, name)} ${qty.toFixed(0)}`)
              .join(", ") || "—",
        })}
      </p>

      <h3>{t("ui-admin-panel-prices")}</h3>
      {prices.length === 0 ? (
        <p className="note">{t("ui-admin-panel-no-trades")}</p>
      ) : (
        <table>
          <tbody>
            {prices.map(([name, price]) => (
              <tr key={name}>
                <td>{goodsName(names, name)}</td>
                <td className="num">{price.toFixed(2)} ₭</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>{t("ui-admin-panel-goods")}</h3>
      <table>
        <tbody>
          {goods.map(([name, qty]) => (
            <tr key={name}>
              <td>{goodsName(names, name)}</td>
              <td className="num">{qty.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {panel.treasury ? (
        <>
          <h3>{t("ui-admin-panel-treasury")}</h3>
          <p>{t("ui-admin-panel-balance", { balance: panel.treasury.balance.toFixed(2) })}</p>
          {/* Lent out (D-283): a treasury emptied by lending and one eaten
              through are different positions, and the ruler has to tell them
              apart. The line is silent when nothing is out. */}
          {panel.treasury.lent > 0 && (
            <p>{t("ui-admin-panel-lent", { lent: panel.treasury.lent.toFixed(2) })}</p>
          )}
          <p className="note">
            {t("ui-admin-panel-collected", { lines: ledger(panel.treasury.collected) })}
          </p>
          <p className="note">
            {t("ui-admin-panel-spent", { lines: ledger(panel.treasury.spent) })}
          </p>
        </>
      ) : (
        <p className="note">{t("ui-admin-panel-treasury-closed")}</p>
      )}
    </div>
  );
}

/** Goods and their weight, in the player's words: what crossed the border. */
function weighed(rows: Record<string, number>, names: ReturnType<typeof useNames>): string {
  return (
    Object.entries(rows)
      .map(([id, kg]) =>
        t("ui-admin-panel-kg", { goods: goodsName(names, id), kg: kg.toFixed(1) }),
      )
      .join(", ") || "—"
  );
}

/** Treasury lines by their ground: what was collected, what was spent. */
function ledger(rows: Record<string, number>): string {
  return (
    Object.entries(rows)
      .map(([ground, qty]) =>
        t("ui-admin-panel-ledger-line", {
          ground: groundName(ground),
          amount: qty.toFixed(2),
        }),
      )
      .join(", ") || "—"
  );
}
