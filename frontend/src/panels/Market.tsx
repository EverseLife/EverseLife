/**
 * Market: the node's order book (D-003, D-047, D-127).
 *
 * The separation all this was made for: **goods physically, disposing
 * remotely**. Loading and taking -- on foot, an order -- from anywhere,
 * buying -- standing here.
 *
 * The layout is two columns across the whole scene: the book and deals on the
 * left, own goods on the right. A quick deal hits the book's best price; a
 * custom price is for those ready to wait. A click on a goods row selects the
 * position in the book.
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Book, Look, Session, Thing } from "../api";
import { Amount } from "../Amount";
import { chosen } from "../amounts";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  look: Look;
  session: Session;
  values: Record<string, any> | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Position = { goods: string; tier: string };

/** A quantity without lies: fractional is shown fractional, whole -- whole. */
const exactly = (qty: number) =>
  qty.toFixed(3).replace(/\.?0+$/, "") || "0";

export function Market({ look, session }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [positions, setPositions] = useState<Position[]>([]);
  const [choice, setChoice] = useState<Position | null>(null);
  const [orderBook, setOrderBook] = useState<Book | null>(null);
  const [price, setPrice] = useState(3);
  const [volume, setVolume] = useState(1);
  //: Other people's sell orders in this node -- what can be reserved.
  const [foreign, setForeign] = useState<
    { id: string; goods: string; tier: string; price: number; left: number }[]
  >([]);

  const node = look.node?.key;

  useEffect(() => {
    if (!node) return;
    void api.positions(node).then(({ positions }) => {
      setPositions(positions);
      setChoice((previous) => previous ?? positions[0] ?? null);
    });
  }, [node, look]);

  useEffect(() => {
    if (!node || !choice) return;
    void api.book(node, choice.goods, choice.tier).then(setOrderBook);
  }, [node, choice, look]);

  useEffect(() => {
    void session
      .send("market.offers")
      .then((answer) => setForeign(answer.offers as typeof foreign))
      .catch(() => setForeign([]));
  }, [session, look]);

  //: Book positions are goods plus quality tier: "ore, good" is a separate
  //: row, not a range (D-058). Own goods are added to the traded ones: one
  //: may also sell what has not been traded here yet.
  const pocket = look.inventory ?? [];
  const terminal = look.stall ?? [];
  const all: Position[] = [
    ...positions,
    ...[...pocket, ...terminal]
      .filter((t) => t.quality !== null)
      //: The counter's name, not the item's: a written carrier is a position
      //: per recipe -- "Рецепт: Стекло" (D-209).
      .map((t) => ({ goods: t.key ?? t.goods, tier: t.tier })),
  ].filter(
    (p, i, everything) => everything.findIndex((d) => d.goods === p.goods && d.tier === p.tier) === i,
  );

  const bestSell = orderBook?.asks[0] ?? null; // what they sell at: buy here
  const bestBuy = orderBook?.bids[0] ?? null; // what they take at: sell here


  const deal = (side: "market.buy" | "market.sell", price: number) =>
    act(() =>
      session.send(side, {
        ...(side === "market.sell" ? { node: node } : {}),
        goods: choice!.goods,
        tier: choice!.tier,
        price,
        amount: volume,
      }),
    );

  return (
    <section className="wide">
      <Refusal of={acting} />
      <h2>Рынок</h2>
      <div className="market-grid">
        <div>
          <div className="row">
            <select
              value={choice ? `${choice.goods}|${choice.tier}` : ""}
              onChange={(e) => {
                const [goods, tier] = e.target.value.split("|");
                setChoice({ goods, tier });
              }}
            >
              {all.map((p) => (
                <option key={`${p.goods}|${p.tier}`} value={`${p.goods}|${p.tier}`}>
                  {p.goods}, {p.tier}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="1"
              min="1"
              value={volume}
              onChange={(e) => setVolume(Number(e.target.value))}
              title="объём сделки"
            />
          </div>

          {orderBook && (orderBook.asks.length > 0 || orderBook.bids.length > 0) ? (
            <table className="book">
              <thead>
                <tr>
                  <th>покупают</th>
                  <th>цена ₭</th>
                  <th>продают</th>
                </tr>
              </thead>
              <tbody>
                {[...orderBook.asks].reverse().map((u) => (
                  <tr key={`a${u.price}`}>
                    <td />
                    <td className="num">{api.tk(u.price)}</td>
                    <td className="num">{u.amount}</td>
                  </tr>
                ))}
                {orderBook.bids.map((u) => (
                  <tr key={`b${u.price}`}>
                    <td className="num">{u.amount}</td>
                    <td className="num">{api.tk(u.price)}</td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="note">по этой позиции стакан пуст: цену назначает первый</p>
          )}
          {orderBook?.last != null && (
            <p className="note">последняя сделка: {api.tk(orderBook.last)} ₭</p>
          )}

          <div className="row">
            <button
              onClick={() => deal("market.buy", bestSell!.price)}
              disabled={busy || !choice || !bestSell}
              title="купить по лучшей цене продавцов"
            >
              Быстро купить{bestSell ? ` · ${api.tk(bestSell.price)} ₭` : ""}
            </button>
            <button
              onClick={() => deal("market.sell", bestBuy!.price)}
              disabled={busy || !choice || !bestBuy}
              title="продать по лучшей цене покупателей; товар должен лежать в терминале"
            >
              Быстро продать{bestBuy ? ` · ${api.tk(bestBuy.price)} ₭` : ""}
            </button>
          </div>
          <p className="note">
            Быстрая сделка бьёт по лучшей цене; остаток встаёт ордером.
          </p>

          <h3>Своя цена</h3>
          <div className="row">
            <input
              type="number"
              step="0.1"
              min="0"
              value={price}
              onChange={(e) => setPrice(Number(e.target.value))}
              title="цена за единицу, ₭"
            />
            <button
              className="quiet"
              onClick={() => deal("market.buy", api.minor(price))}
              disabled={busy || !choice}
            >
              Купить
            </button>
            <button
              className="quiet"
              onClick={() => deal("market.sell", api.minor(price))}
              disabled={busy || !choice}
            >
              Продать
            </button>
          </div>
          <p className="note">
            Покупают стоя здесь; свои ордера — в «торговле». Налог платит
            продавец.
          </p>

          {/* Бронь — единственное исключение из «купить только стоя здесь»:
              купец, собираясь в дорогу, резервирует партию задатком (D-047). */}
          {foreign.length > 0 && (
            <>
              <h3>
                Забронировать
                <Rule>
                  Бронируют издалека, забирают ногами; не забрал в срок — задаток у
                  продавца.
                </Rule>
              </h3>
              <table>
                <tbody>
                  {foreign.map((offer) => (
                    <tr key={offer.id}>
                      <td>
                        {offer.goods}, {offer.tier}
                      </td>
                      <td className="num">{api.tk(offer.price)} ₭</td>
                      {/* Дробный остаток нельзя округлять до нуля: «0» рядом с
                          живой кнопкой — обман, а не краткость. */}
                      <td className="num">{exactly(offer.left)}</td>
                      <td>
                        <button
                          className="quiet"
                          onClick={() =>
                            act(() =>
                              session.send("market.reserve", {
                                order: offer.id,
                                amount: Math.min(volume, offer.left),
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
            </>
          )}

          {/* Свои брони в этом узле выкупаются здесь же. */}
          {look.reservations
            .filter((reservation) => reservation.node_key === node)
            .map((reservation) => (
              <div className="row" key={reservation.id}>
                <span>
                  бронь: {reservation.goods} · {reservation.amount} по {api.tk(reservation.price)} ₭
                </span>
                <button
                  onClick={() =>
                    act(() =>
                      session.send("market.redeem", { reservation: reservation.id }),
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
          <Own
            things={pocket}
            choice={choice}
            mark={setChoice}
            button="Загрузить"
            action={(t, amount) =>
              //: The row is a stack of one quality: what is loaded is that
              //: tier, not the worst of the name (D-058).
              act(() =>
                session.send("market.load", { goods: t.key ?? t.goods, amount, tier: t.tier }),
              )
            }
            busy={busy}
            empty="в кармане пусто"
          />

          <h3>
            Терминал
            <Rule>
              Продаётся то, что в терминале; купленное забирается отсюда же . Клик по
              строке выбирает позицию.
            </Rule>
          </h3>
          <Own
            things={terminal}
            choice={choice}
            mark={setChoice}
            button="Забрать"
            action={(t, amount) =>
              act(() =>
                session.send("market.take", {
                  goods: t.key ?? t.goods,
                  tier: t.tier,
                  amount,
                }),
              )
            }
            busy={busy}
            empty="в терминале ничего вашего"
          />
        </div>
      </div>
    </section>
  );
}

function Own({
  things,
  choice,
  mark,
  button,
  action,
  busy,
  empty,
}: {
  things: Thing[];
  choice: Position | null;
  mark: (p: Position) => void;
  button: string;
  action: (t: Thing, amount: number) => void;
  busy: boolean;
  empty: string;
}) {
  //: How much of each stack to move. Empty means the whole of it: selling
  //: everything is the common case, selling part of it is the one that used
  //: to be impossible (D-047).
  const [parts, setParts] = useState<Record<string, number | null>>({});
  if (things.length === 0) return <p className="note">{empty}</p>;
  return (
    <table>
      <tbody>
        {things.map((t) => {
          const name = t.key ?? t.goods;
          const selected = choice?.goods === name && choice?.tier === t.tier;
          const part = chosen(parts[t.id] ?? null, t.amount);
          return (
            <tr
              key={t.id}
              className={`pick ${selected ? "picked" : ""}`}
              onClick={() => mark({ goods: name, tier: t.tier })}
            >
              <td>{t.flavor ?? name}</td>
              <td className="num">{t.amount}</td>
              <td className="note">
                {t.quality === null ? "" : `${t.quality.toFixed(0)} · ${t.tier}`}
              </td>
              <td onClick={(e) => e.stopPropagation()}>
                <Amount
                  value={parts[t.id] ?? null}
                  max={t.amount}
                  onChange={(value) =>
                    setParts((was) => ({ ...was, [t.id]: value }))
                  }
                />
              </td>
              <td>
                <button
                  className="quiet"
                  onClick={(e) => {
                    e.stopPropagation();
                    action(t, part);
                  }}
                  disabled={busy || part <= 0}
                >
                  {button}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
