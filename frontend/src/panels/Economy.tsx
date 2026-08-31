// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Economy -- a state tab of the sidebar (D-124, D-140).
 *
 * Visible only to state officials: the figures one governs by -- the
 * treasury, the city panel, laws in force and the world summary. Reading is
 * remote (D-140); **changing** laws from here is not allowed -- authority is
 * in-person and decides in the administration (D-155).
 */


import * as api from "../api";
import { useNames } from "../actions";
import { t } from "../locale";
import { goodsName } from "../names";
import { Panel } from "./Admin";
import { Rule } from "../Rule";
import type { StateView } from "./State";

export function Economy({ view }: { view: StateView }) {
  const names = useNames();
  const { city, panel, world } = view;
  const prices = Object.entries(world).filter(([k]) => k.startsWith("price."));

  return (
    <div>
      <p className="sign">
        {t("ui-city-treasury-sign", { city: city.name, treasury: api.tk(city.treasury) })}
      </p>
      <Panel panel={panel} />

      <h3>{t("ui-city-money")}</h3>
      <table>
        <tbody>
          <tr>
            <td>{t("ui-city-money-total")}</td>
            <td className="num">{(world["money.total"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>{t("ui-city-money-median")}</td>
            <td className="num">{(world["money.median"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>{t("ui-city-money-gini")}</td>
            <td className="num">{(world["money.gini"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>{t("ui-city-trades")}</td>
            <td className="num">{world["trades.count"] ?? 0}</td>
          </tr>
        </tbody>
      </table>

      {prices.length > 0 && (
        <>
          <h3>{t("ui-city-prices")}</h3>
          <table>
            <tbody>
              {prices.map(([key, price]) => (
                <tr key={key}>
                  <td>{goodsName(names, key.slice("price.".length))}</td>
                  <td className="num">{price.toFixed(2)} ₭</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>
        {t("ui-city-laws")}
        <Rule>{t("ui-city-laws-rule")}</Rule>
      </h3>
      <table>
        <tbody>
          {Object.entries(city.laws)
            .filter(([, law]) => law.value && law.value !== "нет")
            .map(([key, law]) => (
              <tr key={key}>
                <td title={law.note ?? ""}>{law.name}</td>
                <td className="num">
                  <b>{law.value}</b>
                  {law.unit && <span className="note"> {law.unit}</span>}
                </td>
                <td className="note">
                  {law.own ? t("ui-city-law-own") : t("ui-city-law-default")}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
