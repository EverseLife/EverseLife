/**
 * Монетный станок: чеканка и переплавка (D-016, D-086).
 *
 * Монета — предмет, а не счёт: она лежит в кармане, гибнет с телом и ходит
 * там, где нет терминала. Проба одна на весь мир — 900‰: состав задан
 * рецептом (0.9 аффинированного металла и 0.1 слитка железа лигатурой), и
 * механики занижения не существует — монета всегда содержит то, что обещает.
 */

import { useMemo, useState } from "react";
import type { Look, Session, Thing } from "../api";

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

export function Mint({ look, session, values, busy, act }: Props) {
  const умеет = COINS.filter((к) => look.knows.includes(к.coin));
  const [монета, setМонета] = useState(умеет[0]?.coin ?? COINS[0].coin);
  const выбранная = COINS.find((к) => к.coin === монета) ?? COINS[0];
  const [сколько, setСколько] = useState(10);

  const проба = Number(values?.["coin.default_fineness"] ?? 900);

  const в_руках = useMemo(() => {
    const сумма = (имя: string) =>
      look.inventory
        .filter((т) => т.goods === имя)
        .reduce((итог, т) => итог + т.amount, 0);
    return { металл: сумма(выбранная.metal), железо: сумма(IRON) };
  }, [look.inventory, выбранная.metal]);

  //: Состав монеты — из рецепта вольта: 0.9 аффинажа + 0.1 железа. Числа
  //: продублированы здесь только для прогноза до нажатия; тратит сервер по
  //: рецепту, и рассинхрон честно откажет, а не молча спишет другое.
  const надо_металла = сколько * 0.9;
  const надо_железа = сколько * 0.1;
  const хватает =
    надо_металла <= в_руках.металл && надо_железа <= в_руках.железо;

  const кошелёк = look.inventory.filter((т) => т.fineness != null);

  if (умеет.length === 0) {
    return (
      <section>
        <h2>Монетный станок</h2>
        <p className="note">
          Чеканить нечего: рецепт монеты берут в Библиотеке (D-053). Монета —
          предмет, и делается она как всякий предмет, только своей дверью.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h2>Монетный станок</h2>

      <div className="row">
        <select value={монета} onChange={(e) => setМонета(e.target.value)}>
          {умеет.map((к) => (
            <option key={к.coin}>{к.coin}</option>
          ))}
        </select>
        <input
          type="number"
          min="1"
          step="1"
          value={сколько}
          onChange={(e) => setСколько(Number(e.target.value))}
          title="сколько монет"
        />
        <span className="note">проба {проба} ‰ — одна на весь мир</span>
      </div>

      <p className="note">
        Уйдёт {надо_металла.toFixed(1)} «{выбранная.metal}» (в руках{" "}
        {в_руках.металл.toFixed(1)}) и {надо_железа.toFixed(1)} «{IRON}» (в руках{" "}
        {в_руках.железо.toFixed(1)}). Лигатура — десятая часть железа: монета
        всегда 900-й пробы.
      </p>

      <button
        onClick={() =>
          act(() => session.send("coin.mint", { coin: монета, count: сколько }))
        }
        disabled={busy || !хватает || сколько <= 0}
      >
        Чеканить
      </button>
      {!хватает && (
        <p className="note">металла или железа не хватает: партия не начнётся</p>
      )}

      {кошелёк.length > 0 && (
        <>
          <h3>Кошелёк</h3>
          <table>
            <tbody>
              {кошелёк.map((монета) => (
                <Строка
                  key={монета.id}
                  вещь={монета}
                  busy={busy}
                  плавить={(сколько) =>
                    act(() =>
                      session.send("coin.melt", { item: монета.id, count: сколько }),
                    )
                  }
                />
              ))}
            </tbody>
          </table>
          <p className="note">
            Переплавка вернёт аффинированный металл за вычетом угара; лигатура
            теряется — выковыривать её дороже самого железа.
          </p>
        </>
      )}
    </section>
  );
}

function Строка({
  вещь,
  busy,
  плавить,
}: {
  вещь: Thing;
  busy: boolean;
  плавить: (сколько: number) => void;
}) {
  return (
    <tr>
      <td>{вещь.goods}</td>
      <td className="num">{вещь.amount}</td>
      <td className="note">
        проба {вещь.fineness}
        {вещь.maker ? ` · клеймо ${вещь.maker}` : ""}
      </td>
      <td>
        <button className="quiet" onClick={() => плавить(вещь.amount)} disabled={busy}>
          Переплавить
        </button>
      </td>
    </tr>
  );
}
