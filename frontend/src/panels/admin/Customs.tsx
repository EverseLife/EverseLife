// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The two duties, which are laws the table of laws cannot edit.
 *
 * Every other law in this window is a single value in a single input. A duty
 * is a map -- goods to rate and duty-free norm -- carried over the wire as a
 * JSON string, so it needs a table, a picker, and a parser forgiving enough to
 * survive the older shape where the value was one number for everything. That
 * editor and the parser that feeds it are one thing: neither has a use without
 * the other, and both exist only because the value is not a scalar.
 */

import { useState } from "react";
import { t } from "../../locale";
import { goodsName } from "../../names";
import { useBook, useNames } from "../../actions";
import { NumberField } from "../../NumberField";

/** Duty: goods, rate and duty-free norm (D-123).
 *
 * A rate without a norm hits everyone alike, and the first to suffer is the
 * resident with a sack of turnips. So the row here is always three parts, not one. */
export function Customs({
  law,
  name,
  value,
  goods,
  busy,
  apply,
}: {
  law: string;
  name: string;
  value: string | null;
  goods: string[];
  busy: boolean;
  apply: (value: unknown) => void;
}) {
  const names = useNames();
  const book = useBook();
  const parsed = parse(value);
  const [item, setItem] = useState("");
  const [rate, setRate] = useState(10);
  const [norm, setNorm] = useState(30);

  //: The player types the Russian word; the law is keyed by the id (D-251).
  //: The synonyms map carries every Russian spelling, and an id passes as is.
  const add = () =>
    apply({
      ...parsed,
      [book?.synonyms?.[item.trim()] ?? item.trim()]: { rate: rate, free: norm },
    });
  const remove = (which: string) => {
    const without = { ...parsed };
    delete without[which];
    apply(without);
  };

  return (
    <div>
      <h3>{name}</h3>
      {Object.keys(parsed).length === 0 ? (
        <p className="note">{t("ui-admin-customs-open")}</p>
      ) : (
        <table>
          <tbody>
            {Object.entries(parsed).map(([which, condition]) => (
              <tr key={which}>
                <td>{goodsName(names, which)}</td>
                <td className="num">{condition.rate}%</td>
                <td className="note">
                  {t("ui-admin-customs-free", { free: String(condition.free) })}
                </td>
                <td>
                  <button className="quiet" onClick={() => remove(which)} disabled={busy}>
                    {t("ui-admin-customs-drop")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <input
          list={`goods-${law}`}
          placeholder={t("ui-admin-customs-goods")}
          value={item}
          onChange={(e) => setItem(e.target.value)}
        />
        <datalist id={`goods-${law}`}>
          {goods.map((name) => (
            //: Offered in the player's words; `add` resolves back to the id.
            <option key={name} value={goodsName(names, name)} />
          ))}
        </datalist>
        <NumberField
          value={rate}
          onChange={(typed) => setRate(typed ?? 0)}
          title={t("ui-admin-customs-rate-title")}
        />
        <NumberField
          value={norm}
          onChange={(typed) => setNorm(typed ?? 0)}
          title={t("ui-admin-customs-free-title")}
        />
        <button onClick={add} disabled={busy || !item.trim() || rate <= 0}>
          {t("ui-admin-customs-add")}
        </button>
      </div>
    </div>
  );
}

/** A map-law's value comes as a JSON string: parse without crashing. */
function parse(value: string | null): Record<string, { rate: number; free: number }> {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, { rate: number; free: number }>;
    }
  } catch {
    //: Старое значение числом — это ставка на всё, и её показывает таблица
    //: законов выше. Здесь редактируются прицельные строки.
  }
  return {};
}
