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
import { goodsName, lawName, lawNote, lawOption, lawUnit } from "../names";
import { NOBODY } from "../wire/city";
import { Panel } from "./admin/Panel";
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
            //: A law switched off is not a rule to read: «nobody» is the key
            //: for that in every law that is a choice. It used to be the
            //: Russian word «нет» compared here -- a value the wire happened
            //: to carry, and the one allowlisted in `check-copy` for this file.
            .filter(([, law]) => law.value && law.value !== NOBODY)
            .map(([key, law]) => (
              <tr key={key}>
                <td title={lawNote(names, key) ?? ""}>{lawName(names, key)}</td>
                <td className="num">
                  <b>{lawOption(names, key, law.value ?? "")}</b>
                  {lawUnit(names, key) && (
                    <span className="note"> {lawUnit(names, key)}</span>
                  )}
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
