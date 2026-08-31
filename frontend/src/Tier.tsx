// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Choosing **which quality** goes into the work (D-058, D-092).
 *
 * The engine takes the worst stack first unless told otherwise; this is where
 * it is told. One control for every place a thing is chosen by name -- a
 * recipe input, a laid-out composition, a pot role, coin metal, building
 * materials -- so that "quality" is one word and one look across the screen.
 *
 * The options are only the tiers that really lie in the hands for this thing,
 * with how much of each: a choice offered from thin air is a refusal waiting
 * to happen. "Любое" is the engine's own order -- worst first.
 */

import type { Thing } from "./api";
import { useNames } from "./actions";
import { t } from "./locale";
import { goodsName } from "./names";
import { tierLabel, tiersOf } from "./tiers";

/**
 * The picker itself. Always on the screen where a thing is chosen: the choice
 * of quality is part of every such place, and it must be seen even when the
 * hands hold one tier -- then the list says what that tier is -- or nothing at
 * all -- then it says so. `quiet` hides it instead when there is nothing to
 * choose between, for dense lists.
 */
export function TierPick({
  things,
  goods,
  value,
  onChange,
  quiet,
}: {
  things: Thing[];
  goods: string;
  value: string | null | undefined;
  onChange: (tier: string | null) => void;
  /** Hide when fewer than two tiers lie in the hands. */
  quiet?: boolean;
}) {
  const names = useNames();
  const stocks = tiersOf(things, goods);
  const word = goodsName(names, goods);
  if (quiet && stocks.length < 2) return null;
  if (stocks.length === 0) {
    return (
      <select disabled title={t("ui-tier-none-title", { goods: word })}>
        <option>{t("ui-tier-none")}</option>
      </select>
    );
  }
  const current = value && stocks.some((s) => s.tier === value) ? value : "";
  return (
    <select
      value={current}
      onChange={(e) => onChange(e.target.value || null)}
      title={t("ui-tier-title", { goods: word })}
    >
      <option value="">{t("ui-tier-any")}</option>
      {stocks.map((s) => (
        <option key={s.tier} value={s.tier}>
          {tierLabel(s, names)}
        </option>
      ))}
    </select>
  );
}
