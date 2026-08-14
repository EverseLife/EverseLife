/**
 * Рынок: стакан узла (D-003, D-047, D-127).
 *
 * Разделение, ради которого всё и сделано: **товар физически, распоряжение
 * удалённо**. Загрузить и забрать — ногами, ордер — откуда угодно, купить —
 * стоя здесь.
 *
 * Компоновка — два столбца во всю ширину сцены: слева стакан и сделки, справа
 * свой товар. Быстрая сделка бьёт по лучшей цене стакана; своя цена — для
 * тех, кто готов ждать. Клик по строке товара выбирает позицию в стакане.
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Book, Look, Session, Thing } from "../api";

type Props = {
  look: Look;
  session: Session;
  values: Record<string, any> | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Позиция = { goods: string; tier: string };

/** Количество без вранья: дробное показывается дробным, целое — целым. */
const ровно = (сколько: number) =>
  сколько.toFixed(3).replace(/\.?0+$/, "") || "0";

export function Market({ look, session, busy, act }: Props) {
  const [позиции, setПозиции] = useState<Позиция[]>([]);
  const [выбор, setВыбор] = useState<Позиция | null>(null);
  const [стакан, setСтакан] = useState<Book | null>(null);
  const [цена, setЦена] = useState(3);
  const [объём, setОбъём] = useState(1);
  //: Чужие заявки на продажу в этом узле — то, что можно забронировать.
  const [чужие, setЧужие] = useState<
    { id: string; goods: string; tier: string; price: number; left: number }[]
  >([]);

  const узел = look.node?.key;

  useEffect(() => {
    if (!узел) return;
    void api.positions(узел).then(({ positions }) => {
      setПозиции(positions);
      setВыбор((прежний) => прежний ?? positions[0] ?? null);
    });
  }, [узел, look]);

  useEffect(() => {
    if (!узел || !выбор) return;
    void api.book(узел, выбор.goods, выбор.tier).then(setСтакан);
  }, [узел, выбор, look]);

  useEffect(() => {
    void session
      .send("market.offers")
      .then((ответ) => setЧужие(ответ.offers as typeof чужие))
      .catch(() => setЧужие([]));
  }, [session, look]);

  //: Позиции стакана — товар плюс ступень качества: «руда, хорошая» это
  //: отдельная строка, а не диапазон (D-058). К торгуемым добавляется своё:
  //: продавать можно и то, чем тут ещё не торговали.
  const карман = look.inventory ?? [];
  const терминал = look.stall ?? [];
  const все: Позиция[] = [
    ...позиции,
    ...[...карман, ...терминал]
      .filter((т) => т.quality !== null)
      .map((т) => ({ goods: т.goods, tier: т.tier })),
  ].filter(
    (п, i, всё) => всё.findIndex((д) => д.goods === п.goods && д.tier === п.tier) === i,
  );

  const лучшая_продажа = стакан?.asks[0] ?? null; // почём продают: тут покупают
  const лучшая_покупка = стакан?.bids[0] ?? null; // почём берут: тут продают

  const сделка = (side: "market.buy" | "market.sell", price: number) =>
    act(() =>
      session.send(side, {
        ...(side === "market.sell" ? { node: узел } : {}),
        goods: выбор!.goods,
        tier: выбор!.tier,
        price,
        amount: объём,
      }),
    );

  return (
    <section className="wide">
      <h2>Рынок</h2>
      <div className="market-grid">
        <div>
          <div className="row">
            <select
              value={выбор ? `${выбор.goods}|${выбор.tier}` : ""}
              onChange={(e) => {
                const [goods, tier] = e.target.value.split("|");
                setВыбор({ goods, tier });
              }}
            >
              {все.map((п) => (
                <option key={`${п.goods}|${п.tier}`} value={`${п.goods}|${п.tier}`}>
                  {п.goods}, {п.tier}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="1"
              min="1"
              value={объём}
              onChange={(e) => setОбъём(Number(e.target.value))}
              title="объём сделки"
            />
          </div>

          {стакан && (стакан.asks.length > 0 || стакан.bids.length > 0) ? (
            <table className="book">
              <thead>
                <tr>
                  <th>покупают</th>
                  <th>цена ₭</th>
                  <th>продают</th>
                </tr>
              </thead>
              <tbody>
                {[...стакан.asks].reverse().map((у) => (
                  <tr key={`a${у.price}`}>
                    <td />
                    <td className="num">{api.tk(у.price)}</td>
                    <td className="num">{у.amount}</td>
                  </tr>
                ))}
                {стакан.bids.map((у) => (
                  <tr key={`b${у.price}`}>
                    <td className="num">{у.amount}</td>
                    <td className="num">{api.tk(у.price)}</td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="note">по этой позиции стакан пуст: цену назначает первый</p>
          )}
          {стакан?.last != null && (
            <p className="note">последняя сделка: {api.tk(стакан.last)} ₭</p>
          )}

          <div className="row">
            <button
              onClick={() => сделка("market.buy", лучшая_продажа!.price)}
              disabled={busy || !выбор || !лучшая_продажа}
              title="купить по лучшей цене продавцов"
            >
              Быстро купить{лучшая_продажа ? ` · ${api.tk(лучшая_продажа.price)} ₭` : ""}
            </button>
            <button
              onClick={() => сделка("market.sell", лучшая_покупка!.price)}
              disabled={busy || !выбор || !лучшая_покупка}
              title="продать по лучшей цене покупателей; товар должен лежать в терминале"
            >
              Быстро продать{лучшая_покупка ? ` · ${api.tk(лучшая_покупка.price)} ₭` : ""}
            </button>
          </div>
          <p className="note">
            Быстрая сделка бьёт по лучшей цене стакана. Не хватило объёма по этой
            цене — остаток встаёт ордером и ждёт.
          </p>

          <h3>Своя цена</h3>
          <div className="row">
            <input
              type="number"
              step="0.1"
              min="0"
              value={цена}
              onChange={(e) => setЦена(Number(e.target.value))}
              title="цена за единицу, ₭"
            />
            <button
              className="quiet"
              onClick={() => сделка("market.buy", api.minor(цена))}
              disabled={busy || !выбор}
            >
              Купить
            </button>
            <button
              className="quiet"
              onClick={() => сделка("market.sell", api.minor(цена))}
              disabled={busy || !выбор}
            >
              Продать
            </button>
          </div>
          <p className="note">
            Купить можно только стоя здесь; ордером распоряжаются откуда угодно —
            свои ордера в сайдбаре, в «торговле». Налог с продажи платит продавец.
          </p>

          {/* Бронь — единственное исключение из «купить только стоя здесь»:
              купец, собираясь в дорогу, резервирует партию задатком (D-047). */}
          {чужие.length > 0 && (
            <>
              <h3>Забронировать</h3>
              <table>
                <tbody>
                  {чужие.map((предложение) => (
                    <tr key={предложение.id}>
                      <td>
                        {предложение.goods}, {предложение.tier}
                      </td>
                      <td className="num">{api.tk(предложение.price)} ₭</td>
                      {/* Дробный остаток нельзя округлять до нуля: «0» рядом с
                          живой кнопкой — обман, а не краткость. */}
                      <td className="num">{ровно(предложение.left)}</td>
                      <td>
                        <button
                          className="quiet"
                          onClick={() =>
                            act(() =>
                              session.send("market.reserve", {
                                order: предложение.id,
                                amount: Math.min(объём, предложение.left),
                              }),
                            )
                          }
                          disabled={busy}
                          title="внести задаток и забрать до срока"
                        >
                          Бронь
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="note">
                Бронируют издалека, забирают ногами: задаток вносится сразу, а
                срок идёт. Не забрал — задаток остался продавцу, товар вернулся
                в стакан.
              </p>
            </>
          )}

          {/* Свои брони в этом узле выкупаются здесь же. */}
          {look.reservations
            .filter((бронь) => бронь.node_key === узел)
            .map((бронь) => (
              <div className="row" key={бронь.id}>
                <span>
                  бронь: {бронь.goods} · {бронь.amount} по {api.tk(бронь.price)} ₭
                </span>
                <button
                  onClick={() =>
                    act(() =>
                      session.send("market.redeem", { reservation: бронь.id }),
                    )
                  }
                  disabled={busy}
                >
                  Выкупить
                </button>
              </div>
            ))}
        </div>

        <div>
          <h3>Карман</h3>
          <Свои
            things={карман}
            выбор={выбор}
            пометить={setВыбор}
            кнопка="Загрузить"
            действие={(т) =>
              act(() => session.send("market.load", { goods: т.goods, amount: т.amount }))
            }
            busy={busy}
            пусто="в кармане пусто"
          />

          <h3>Терминал</h3>
          <Свои
            things={терминал}
            выбор={выбор}
            пометить={setВыбор}
            кнопка="Забрать"
            действие={(т) =>
              act(() =>
                session.send("market.take", {
                  goods: т.goods,
                  tier: т.tier,
                  amount: т.amount,
                }),
              )
            }
            busy={busy}
            пусто="в терминале ничего вашего"
          />
          <p className="note">
            Продаётся то, что лежит в терминале: сначала «Загрузить», потом
            продавать. Купленное появляется здесь же — забирается ногами (D-047).
            Клик по строке выбирает позицию в стакане.
          </p>
        </div>
      </div>
    </section>
  );
}

function Свои({
  things,
  выбор,
  пометить,
  кнопка,
  действие,
  busy,
  пусто,
}: {
  things: Thing[];
  выбор: Позиция | null;
  пометить: (п: Позиция) => void;
  кнопка: string;
  действие: (т: Thing) => void;
  busy: boolean;
  пусто: string;
}) {
  if (things.length === 0) return <p className="note">{пусто}</p>;
  return (
    <table>
      <tbody>
        {things.map((т) => {
          const выбрано = выбор?.goods === т.goods && выбор?.tier === т.tier;
          return (
            <tr
              key={т.id}
              className={`pick ${выбрано ? "picked" : ""}`}
              onClick={() => пометить({ goods: т.goods, tier: т.tier })}
            >
              <td>{т.flavor ?? т.goods}</td>
              <td className="num">{т.amount}</td>
              <td className="note">
                {т.quality === null ? "" : `${т.quality.toFixed(0)} · ${т.tier}`}
              </td>
              <td>
                <button
                  className="quiet"
                  onClick={(e) => {
                    e.stopPropagation();
                    действие(т);
                  }}
                  disabled={busy}
                >
                  {кнопка}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
