// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The hearth: a pot by roles (D-119, D-128).
 *
 * A dish's composition is given not as a list but as roles: base, filler,
 * fat, seasoning. Into a role goes what was found. An unfilled role hurts
 * quality more than a bad product -- cheap fat is better than no fat.
 *
 * The combination decides the dish's **kind**, not its quality: "stew - beans,
 * vegetables" and "stew - turnip" are different dishes for the diet, though
 * the recipe is one. The pot is cooked whole -- a flow, not an order.
 */

import { useMemo, useState } from "react";
import type { Look } from "../api";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useSession } from "../actions";
import { TierPick } from "../Tier";

type Props = {
  look: Look;
  /** The vault catalog: a dish is a recipe with roles (D-119), not a name in a list. */
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

//: Roles are the keys of cook.role_weights; the order is constant so the form does not jump.
const ROLES = ["основа", "наполнитель", "жир", "приправа"] as const;

export function Kitchen({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  //: Dishes are the recipes with roles that the identity knows (D-119): the
  //: sign comes from the catalog, and a fourth dish in the vault is on the
  //: list without a client change.
  const withRoles = new Set<string>(
    (book?.recipes ?? []).filter((r) => r.roles).map((r) => r.name),
  );
  const dishes = look.knows.filter((name) => withRoles.has(name));
  const [dish, setDish] = useState(dishes[0] ?? "");
  const [filling, setFilling] = useState<Record<string, string>>({});
  //: Which quality of the product goes into each role (D-058): the good meat
  //: into the stew, the rest into the salting.
  const [tiers, setTiers] = useState<Record<string, string | null>>({});

  //: Products go into a role: what is edible is decided by data, not the client.
  const products = useMemo(
    () => [...new Set(look.inventory.filter((t) => t.ingredient).map((t) => t.goods))],
    [look.inventory],
  );

  const closed = ROLES.filter((role) => filling[role]).length;

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        Очаг
        <Rule>
          Пустая роль режет качество сильнее плохого продукта. Сочетание решает вид
          блюда — по видам считается разнообразие рациона. Нужна утварь в кармане:
          горшок или котёл.
        </Rule>
      </h2>
      {dishes.length === 0 ? (
        <p className="note">
          Ни одного блюда в личности: рецепты берут в Библиотеке.
        </p>
      ) : (
        <>
          <div className="row">
            <select value={dish} onChange={(e) => setDish(e.target.value)}>
              {dishes.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
            <span className="note">котёл варится целиком</span>
          </div>

          {ROLES.map((role) => (
            <div className="row" key={role}>
              <span className="role-name">{role}</span>
              <select
                value={filling[role] ?? ""}
                onChange={(e) => {
                  setFilling((f) => ({ ...f, [role]: e.target.value }));
                  setTiers((was) => ({ ...was, [role]: null }));
                }}
              >
                <option value="">— пусто —</option>
                {products.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
              {filling[role] && (
                <TierPick
                  things={look.inventory}
                  goods={filling[role]}
                  value={tiers[role]}
                  onChange={(tier) => setTiers((was) => ({ ...was, [role]: tier }))}
                />
              )}
            </div>
          ))}

          <button
            onClick={() =>
              act(async () => {
                const chosen = Object.fromEntries(
                  Object.entries(tiers).filter(([role, tier]) => tier && filling[role]),
                );
                await session.send("cook.pot", { output: dish, filling, tiers: chosen });
                setFilling({});
                setTiers({});
              })
            }
            disabled={busy || closed === 0}
          >
            Сварить котёл
          </button>
        </>
      )}
    </section>
  );
}
