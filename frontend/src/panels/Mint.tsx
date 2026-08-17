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
import type { Look, Session, Thing } from "../api";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  look: Look;
  session: Session;
  values: Record<string, any> | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

const IRON = "Слиток железа";
const COINS: { coin: string; metal: string }[] = [
  { coin: "Золотая монета", metal: "Аффинированное золото" },
  { coin: "Серебряная монета", metal: "Аффинированное серебро" },
];

export function Mint({ look, session, values }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const canDo = COINS.filter((k) => look.knows.includes(k.coin));
  const [coin, setCoin] = useState(canDo[0]?.coin ?? COINS[0].coin);
  const chosen = COINS.find((k) => k.coin === coin) ?? COINS[0];
  const [qty, setQty] = useState(10);

  const fineness = Number(values?.["coin.default_fineness"] ?? 900);

  const inHands = useMemo(() => {
    const amount = (name: string) =>
      look.inventory
        .filter((t) => t.goods === name)
        .reduce((result, t) => result + t.amount, 0);
    return { metal: amount(chosen.metal), iron: amount(IRON) };
  }, [look.inventory, chosen.metal]);

  //: The coin's composition comes from the vault recipe: 0.9 refined + 0.1
  //: iron. The numbers are duplicated here only for the forecast before the
  //: click; the server spends by the recipe, and a mismatch honestly refuses
  //: rather than silently writing off something else.

  const metalNeeded = qty * 0.9;
  const ironNeeded = qty * 0.1;
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

      <p className="note">
        Уйдёт {metalNeeded.toFixed(1)} «{chosen.metal}» (в руках{" "}
        {inHands.metal.toFixed(1)}) и {ironNeeded.toFixed(1)} «{IRON}» (в руках{" "}
        {inHands.iron.toFixed(1)}). Лигатура — десятая часть железа: монета
        всегда 900-й пробы.
      </p>

      <button
        onClick={() =>
          act(() => session.send("coin.mint", { coin: coin, count: qty }))
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
          <h3>Кошелёк</h3>
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
          <Rule>            Переплавка вернёт аффинированный металл за вычетом угара; лигатура
            теряется — выковыривать её дороже самого железа.
          </Rule>
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
      <td className="num">{thing.amount}</td>
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
