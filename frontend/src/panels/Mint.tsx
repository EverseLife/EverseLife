// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The mint press: minting and melting (D-016, D-086).
 *
 * A coin is an item, not an account: it lies in the pocket, perishes with the
 * body and circulates where there is no terminal. One fineness for the whole
 * world -- 900 per mille: the composition is set by the recipe (0.9 refined
 * metal and 0.1 iron ingot as alloy), and there is no debasement mechanic --
 * a coin always contains what it promises.
 */

import { useMemo, useState } from "react";
import type { RecipeBook } from "../api";
import type { Look, Thing } from "../api";
import { tally } from "../amounts";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useSession } from "../actions";
import { TierPick } from "../Tier";

type Props = {
  look: Look;
  /** The vault catalog: the coins and what goes under the die come from it. */
  values: Record<string, any> | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Coin = {
  coin: string;
  /** The refined metal: the input the coin is mostly made of. */
  metal: string;
  /** The alloy: the other input, a tenth of iron. */
  alloy: string;
  metalPerCoin: number;
  alloyPerCoin: number;
};

/**
 * The coins and their composition, read off the vault (D-086, D-090): a money
 * recipe is a coin, its heavier input is the refined metal, the lighter one
 * the alloy. A third coin or a changed fineness is data, not a client change.
 */
function coinsOf(book: RecipeBook | null): Coin[] {
  return ((book?.recipes ?? []) as any[])
    .filter((r) => r.kind === "money")
    .map((r) => {
      const parts = Object.entries(r.amounts ?? {}) as [string, number][];
      parts.sort((a, b) => b[1] - a[1]);
      const [metal, metalPerCoin] = parts[0] ?? ["", 0];
      const [alloy, alloyPerCoin] = parts[1] ?? ["", 0];
      return { coin: r.name as string, metal, alloy, metalPerCoin, alloyPerCoin };
    });
}

export function Mint({ look, values }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const COINS = useMemo(() => coinsOf(book), [book]);
  const canDo = COINS.filter((k) => look.knows.includes(k.coin));
  const [coin, setCoin] = useState(canDo[0]?.coin ?? COINS[0]?.coin ?? "");
  const chosen: Coin = COINS.find((k) => k.coin === coin) ?? COINS[0] ?? {
    coin: "", metal: "", alloy: "", metalPerCoin: 0, alloyPerCoin: 0,
  };
  const IRON = chosen.alloy;
  const [qty, setQty] = useState(10);
  //: Which quality of metal and of iron goes under the die (D-058).
  const [tiers, setTiers] = useState<Record<string, string | null>>({});

  const fineness = Number(values?.["coin.default_fineness"] ?? 900);

  const inHands = useMemo(() => {
    const amount = (name: string) =>
      look.inventory
        .filter((t) => t.goods === name)
        .reduce((result, t) => result + t.amount, 0);
    return { metal: amount(chosen.metal), iron: amount(IRON) };
  }, [look.inventory, chosen.metal, IRON]);

  //: The coin's composition comes from the vault recipe: the forecast before
  //: the click is computed from the same amounts the server spends by.
  const metalNeeded = qty * chosen.metalPerCoin;
  const ironNeeded = qty * chosen.alloyPerCoin;
  const enough =
    metalNeeded <= inHands.metal && ironNeeded <= inHands.iron;

  const purse = look.inventory.filter((t) => t.fineness != null);

  if (canDo.length === 0) {
    return (
      <section>
        <Refusal of={acting} />
        <h2>Монетная станция</h2>
        <p className="note">
          Чеканить нечего: рецепт монеты берут в Библиотеке. Монета —
          предмет, и делается она как всякий предмет, только своей дверью.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h2>Монетная станция</h2>

      <div className="row">
        <select value={coin} onChange={(e) => setCoin(e.target.value)}>
          {canDo.map((k) => (
            <option key={k.coin}>{k.coin}</option>
          ))}
        </select>
        <input
          type="number"
          min="1"
          step="1"
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          title="сколько монет"
        />
        <span className="note">проба {fineness} ‰ — одна на весь мир</span>
      </div>
      {[chosen.metal, IRON].map((goods) => (
        <div className="row" key={goods}>
          <TierPick
            things={look.inventory}
            goods={goods}
            value={tiers[goods]}
            onChange={(tier) => setTiers((was) => ({ ...was, [goods]: tier }))}
          />
        </div>
      ))}

      <p className="note">
        Уйдёт {metalNeeded.toFixed(1)} «{chosen.metal}» (в руках{" "}
        {inHands.metal.toFixed(1)}) и {ironNeeded.toFixed(1)} «{IRON}» (в руках{" "}
        {inHands.iron.toFixed(1)}). Лигатура — десятая часть железа: монета
        всегда 900-й пробы.
      </p>

      <button
        onClick={() =>
          act(() =>
            session.send("coin.mint", {
              coin: coin,
              count: qty,
              tiers: Object.fromEntries(
                Object.entries(tiers).filter(([, tier]) => tier),
              ),
            }),
          )
        }
        disabled={busy || !enough || qty <= 0}
      >
        Чеканить
      </button>
      {!enough && (
        <p className="note">металла или железа не хватает: партия не начнётся</p>
      )}

      {purse.length > 0 && (
        <>
          <h3>
            Кошелёк
            <Rule>
              Переплавка вернёт аффинированный металл за вычетом угара; лигатура
              теряется — выковыривать её дороже самого железа.
            </Rule>
          </h3>
          <table>
            <tbody>
              {purse.map((coin) => (
                <Row
                  key={coin.id}
                  thing={coin}
                  busy={busy}
                  melt={(qty) =>
                    act(() =>
                      session.send("coin.melt", { item: coin.id, count: qty }),
                    )
                  }
                />
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Row({
  thing,
  busy,
  melt,
}: {
  thing: Thing;
  busy: boolean;
  melt: (qty: number) => void;
}) {
  return (
    <tr>
      <td>{thing.goods}</td>
      <td className="num">{tally(thing.goods, thing.amount)}</td>
      <td className="note">
        проба {thing.fineness}
        {thing.maker ? ` · клеймо ${thing.maker}` : ""}
      </td>
      <td>
        <button className="quiet" onClick={() => melt(thing.amount)} disabled={busy}>
          Переплавить
        </button>
      </td>
    </tr>
  );
}
