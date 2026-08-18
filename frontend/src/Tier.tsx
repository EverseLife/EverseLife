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
import { tierLabel, tiersOf } from "./tiers";

/**
 * The picker itself. Renders nothing when there is nothing to choose between:
 * one tier in the hands is not a choice, and a control that changes nothing
 * is noise.
 */
export function TierPick({
  things,
  goods,
  value,
  onChange,
  always,
}: {
  things: Thing[];
  goods: string;
  value: string | null | undefined;
  onChange: (tier: string | null) => void;
  /** Show even with a single tier: for lists where alignment matters. */
  always?: boolean;
}) {
  const stocks = tiersOf(things, goods);
  if (stocks.length === 0 || (stocks.length < 2 && !always)) return null;
  const current = value && stocks.some((s) => s.tier === value) ? value : "";
  return (
    <select
      value={current}
      onChange={(e) => onChange(e.target.value || null)}
      title={`какое качество «${goods}» пустить в дело`}
    >
      <option value="">любое (худшее первым)</option>
      {stocks.map((s) => (
        <option key={s.tier} value={s.tier}>
          {tierLabel(s)}
        </option>
      ))}
    </select>
  );
}
