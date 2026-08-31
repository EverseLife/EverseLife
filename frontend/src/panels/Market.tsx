// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Market: the node's order book (D-003, D-047, D-127).
 *
 * The separation all this was made for: **goods physically, disposing
 * remotely**. Loading and taking -- on foot, an order -- from anywhere,
 * buying -- standing here.
 *
 * The panel is two columns: on the left one chooses a position and places an
 * order on it, on the right stands the terminal -- the shelf the goods are
 * actually sold off. There is no copy of the pocket here any more (D-238): the
 * sidebar's inventory is the hands, it is on screen beside this panel, and a
 * stack drags from it onto the terminal and back.
 *
 * ## A position is any goods, not only a traded one
 *
 * The picker used to offer what had already been traded in this node plus what
 * the player was carrying -- a handful of names on a fresh market, and no way
 * at all to bid for something nobody had brought yet. That is backwards: a
 * market starts with somebody wanting what is not there. So the search runs
 * over the whole catalogue, and the short familiar list is what an empty
 * search shows.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import type { Book, Look, RecipeBook, Thing } from "../api";
import { Amount } from "../Amount";
import { chosen, tally } from "../amounts";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useSession } from "../actions";
import { DropZone } from "../DragMove";
import { grip, noDrag } from "../drag";
import { GoodsMark } from "../Glyph";
import { catalogue, floorOf, tierOf } from "../market";
import type { QualityTier } from "../market";

type Props = {
  look: Look;
  values: Record<string, any> | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Position = { goods: string; tier: string };

/** A quantity without lies: fractional is shown fractional, whole -- whole. */
const exactly = (qty: number) =>
  qty.toFixed(3).replace(/\.?0+$/, "") || "0";

/**
 * A sum of money without lies either, to the last minor unit.
 *
 * `tk` rounds to the coin's two decimals, which is right for a price in a
 * table and wrong for the total under an order: an order of one minor unit
 * would read "0 ₭" beside a live button, and a zero beside a live button is a
 * lie, not brevity.
 */
const coins = (minor: number) =>
  (minor / api.MONEY_SCALE).toFixed(4).replace(/\.?0+$/, "") || "0";

export function Market({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [positions, setPositions] = useState<Position[]>([]);
  //: Last deal per goods name in this node, minor units. Only deals get in --
  //: a name nobody has traded shows no price at all (D-002).
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [choice, setChoice] = useState<Position | null>(null);
  const [orderBook, setOrderBook] = useState<Book | null>(null);
  const [tiers, setTiers] = useState<QualityTier[]>([]);
  //: What the buyer will not go below (D-239). The tier button sets it to the
  //: band's start -- that is what pressing "хорошее" has always meant -- and
  //: the field lets the hand name any quality between the bands.
  const [floor, setFloor] = useState(0);
  //: Whether the floor in the field is the player's own. Until it is, it
  //: follows the tier, the way the price follows the book.
  const floorIsMine = useRef(false);
  const [query, setQuery] = useState("");
  //: The price step the book is read at, minor units; null -- let the server
  //: pick the finest that fits the depth. A choice made by hand is kept when
  //: the position changes: whoever went looking for the fine structure of one
  //: book is usually looking for it in the next.
  const [step, setStep] = useState<number | null>(null);
  //: The rungs the server accepts. A constant, read once with the tiers, not
  //: carried back with every book (D-225).
  const [steps, setSteps] = useState<number[]>([]);
  const [price, setPrice] = useState(3);
  const [volume, setVolume] = useState(1);
  //: Whether the price in the field is the player's own. Until it is, the
  //: field follows the position: opening a book and reading a price off it
  //: only to type it back in is work the panel can do itself.
  const priceIsMine = useRef(false);
  //: Other people's sell orders in this node -- what can be reserved.
  const [foreign, setForeign] = useState<
    { id: string; goods: string; tier: string; price: number; left: number }[]
  >([]);

  const node = look.node?.key;

  //: The book moves when the node trades (D-226, `market.*` to the room),
  //: not when the look object changes identity: one reread per market
  //: event, none for a swing at the face next door.
  const [edition, setEdition] = useState(0);
  useEffect(() => session.on("market.", () => setEdition((n) => n + 1)), [session]);

  useEffect(() => {
    void api.tiers().then((window) => {
      setTiers(window.tiers);
      setSteps(window.steps ?? []);
    });
  }, []);

  useEffect(() => {
    if (!node) return;
    void api.positions(node).then(({ positions, prices }) => {
      setPositions(positions);
      setPrices(prices ?? {});
      setChoice((previous) => previous ?? positions[0] ?? null);
    });
  }, [node, edition]);

  useEffect(() => {
    if (!node || !choice) return;
    void api.book(node, choice.goods, choice.tier, step).then(setOrderBook);
  }, [node, choice, edition, step]);

  //: The floor follows the tier until the hand names one of its own. Written
  //: as an effect rather than inside the tier button, because the first
  //: position is chosen by the panel itself -- and a floor left at zero there
  //: would buy "скверное" under a button that says "хорошее".
  useEffect(() => {
    if (!choice || tiers.length === 0 || floorIsMine.current) return;
    setFloor(floorOf(tiers, choice.tier));
  }, [choice, tiers]);

  useEffect(() => {
    void session
      .send("market.offers")
      .then((answer) => setForeign(answer.offers as typeof foreign))
      .catch(() => setForeign([]));
  }, [session, node, edition]);

  const terminal = look.stall ?? [];
  const inHands = look.inventory ?? [];
  //: What the two lists **are**, as one string each: `look` arrives anew every
  //: few seconds, and the same stacks must not rebuild the picker under a hand
  //: reaching for it.
  const carrying = inHands.map((t) => `${t.key ?? t.goods}|${t.tier}`).join(",");
  const shelved = terminal.map((t) => `${t.key ?? t.goods}|${t.tier}`).join(",");

  //: Book positions are goods plus quality tier: "ore, good" is a separate
  //: row, not a range (D-058). What an empty search offers: what trades here
  //: and what the player has to sell -- the two lists a hand reaches for.
  const near = useMemo(() => {
    const seen = new Map<string, Position>();
    for (const p of positions) seen.set(`${p.goods}|${p.tier}`, p);
    for (const t of [...inHands, ...terminal]) {
      if (t.quality == null) continue;
      //: The counter's name, not the item's: a written carrier is a position
      //: per recipe -- "Рецепт: Стекло" (D-209).
      const goods = t.key ?? t.goods;
      seen.set(`${goods}|${t.tier}`, { goods, tier: t.tier });
    }
    return [...seen.values()];
    //: Keyed by what the lists hold, not by the objects that hold them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, carrying, shelved]);

  const everything = useMemo(() => catalogue(book), [book]);
  const found = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    //: The whole catalogue, and the names one already deals in first: the
    //: search is for finding the unknown, not for losing the known.
    const mine = new Set(near.map((p) => p.goods));
    return everything
      .filter((name) => name.toLowerCase().includes(q))
      .sort((a, b) => Number(mine.has(b)) - Number(mine.has(a)));
  }, [query, everything, near]);

  const bestSell = orderBook?.asks[0] ?? null; // what they sell at: buy here
  const bestBuy = orderBook?.bids[0] ?? null; // what they take at: sell here

  //: A price to start from, so that the field is never a guess: the last deal
  //: if there was one, otherwise whichever side of the book exists.
  //: Divided out rather than run through `tk`, which rounds to the coin's two
  //: decimals: a standing bid of one minor unit came back as a price of zero,
  //: and the field then kept the zero into the next position.
  useEffect(() => {
    if (priceIsMine.current) return;
    const suggestion = orderBook?.last ?? bestSell?.price ?? bestBuy?.price ?? null;
    if (suggestion != null && suggestion > 0) setPrice(suggestion / api.MONEY_SCALE);
  }, [orderBook, bestSell, bestBuy]);

  const pick = (position: Position) => {
    //: A new position is a new price question; the field follows again.
    priceIsMine.current = false;
    //: New goods are a new trade entirely, and a floor named by hand was
    //: named for the goods it was named on. A tier switch within the same
    //: goods leaves it alone.
    if (position.goods !== choice?.goods) floorIsMine.current = false;
    setChoice(position);
  };

  const nameOf = (t: Thing) => t.key ?? t.goods;
  const onShelf = choice
    ? terminal
        .filter((t) => nameOf(t) === choice.goods && t.tier === choice.tier)
        .reduce((sum, t) => sum + t.amount, 0)
    : 0;
  const atHand = choice
    ? inHands
        .filter((t) => nameOf(t) === choice.goods && t.tier === choice.tier)
        .reduce((sum, t) => sum + t.amount, 0)
    : 0;

  const deal = (side: "market.buy" | "market.sell", price: number) =>
    act(() =>
      session.send(side, {
        ...(side === "market.sell" ? { node: node } : {}),
        goods: choice!.goods,
        //: A seller offers the lot they have, and stands in its tier. A buyer
        //: names a floor, and stands in the window that floor begins -- the
        //: server refuses the two if they disagree, so they are derived here
        //: from the one thing the hand set (D-239).
        tier: side === "market.buy" ? (tierOf(tiers, floor) ?? choice!.tier) : choice!.tier,
        ...(side === "market.buy" ? { min_quality: floor } : {}),
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
          {/* Choosing what to trade: search over the whole catalogue, and the
              quality tier beside it -- "ore, good" is its own book (D-058). */}
          <div className="row search">
            <input
              type="search"
              placeholder="найти товар"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="поиск товара"
            />
            <span className="note">
              {found
                ? `${found.length} из ${everything.length}`
                : "торгуется здесь и лежит у вас; ищите — найдётся любое"}
            </span>
          </div>

          <div className="market-pick">
            {(found ?? near.map((p) => p.goods)).length === 0 ? (
              <p className="note">
                {found ? "ничего не нашлось" : "здесь ещё ничем не торговали"}
              </p>
            ) : (
              <ul className="picks">
                {[...new Set(found ?? near.map((p) => p.goods))]
                  .slice(0, 40)
                  .map((goods) => (
                    <li key={goods}>
                      <button
                        className={choice?.goods === goods ? "" : "quiet"}
                        aria-pressed={choice?.goods === goods}
                        onClick={() =>
                          pick({
                            goods,
                            //: Keep the tier being looked at; a name picked
                            //: from a search has none of its own.
                            tier:
                              choice?.tier ??
                              near.find((p) => p.goods === goods)?.tier ??
                              tiers[2]?.name ??
                              "обычное",
                          })
                        }
                      >
                        <GoodsMark book={book} goods={goods} />
                        {goods}
                        {/* What it last went for here. No deal -- no number:
                            an invented price is the engine valuing goods. */}
                        {prices[goods] != null && (
                          <span className="note"> {api.tk(prices[goods])} ₭</span>
                        )}
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </div>

          {choice && tiers.length > 0 && (
            <div className="row tiers">
              <span className="note">качество</span>
              {tiers.map(({ name }) => (
                <button
                  key={name}
                  className={choice.tier === name ? "" : "quiet"}
                  aria-pressed={choice.tier === name}
                  onClick={() => pick({ goods: choice.goods, tier: name })}
                >
                  {name}
                </button>
              ))}
            </div>
          )}

          {choice && (
            <p className="sign">
              {choice.goods} · {choice.tier}
              <span className="note">
                {" "}
                в терминале {exactly(onShelf)} · в руках {exactly(atHand)}
              </span>
            </p>
          )}

          {/* Prices are written to the minor unit, and a book of rows a minor
              unit apart is a wall. Rows are glued into a step: the server
              picks the finest one that fits, and the hand can go finer. */}
          {orderBook && steps.length > 0 && (
            <div className="row tiers">
              <span className="note">шаг цены</span>
              <button
                className={step === null ? "" : "quiet"}
                aria-pressed={step === null}
                onClick={() => setStep(null)}
                title="шаг подбирает сервер: самый мелкий, при котором стакан помещается"
              >
                авто{step === null ? ` · ${coins(orderBook.step)}` : ""}
              </button>
              {steps.map((rung) => (
                <button
                  key={rung}
                  className={step === rung ? "" : "quiet"}
                  aria-pressed={step === rung}
                  onClick={() => setStep(rung)}
                >
                  {coins(rung)}
                </button>
              ))}
            </div>
          )}

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
                {/* A click on a rung puts its price in the field: reading a
                    number off the book to type it back in is work. */}
                {[...orderBook.asks].reverse().map((u) => (
                  <tr
                    key={`a${u.price}`}
                    className="pick"
                    onClick={() => {
                      setPrice(u.price / api.MONEY_SCALE);
                      priceIsMine.current = true;
                    }}
                  >
                    <td />
                    <td className="num">{coins(u.price)}</td>
                    <td className="num">{tally(choice!.goods, u.amount)}</td>
                  </tr>
                ))}
                {orderBook.bids.map((u) => (
                  <tr
                    key={`b${u.price}`}
                    className="pick"
                    onClick={() => {
                      setPrice(u.price / api.MONEY_SCALE);
                      priceIsMine.current = true;
                    }}
                  >
                    <td className="num">{tally(choice!.goods, u.amount)}</td>
                    <td className="num">{coins(u.price)}</td>
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

          {/* One order, one place: how much, at what price, and what it comes
              to. The two quick buttons underneath are the same order with the
              book's own best price already in it. */}
          <div className="order">
            <label>
              <span>сколько</span>
              <input
                type="number"
                step="0.1"
                min="0"
                value={volume}
                onChange={(e) => setVolume(Number(e.target.value))}
              />
            </label>
            {/* What the buyer will not go below (D-239). A lot better than
                asked is no loss, so the floor reaches the tiers above it. */}
            <label>
              <span>не хуже, качество</span>
              <input
                type="number"
                step="1"
                min={tiers[0]?.from ?? 0}
                max={tiers[tiers.length - 1]?.to ?? 100}
                value={floor}
                onChange={(e) => {
                  setFloor(Number(e.target.value));
                  floorIsMine.current = true;
                }}
              />
            </label>
            <label>
              <span>цена за единицу, ₭</span>
              <input
                type="number"
                step="0.1"
                min="0"
                value={price}
                onChange={(e) => {
                  setPrice(Number(e.target.value));
                  priceIsMine.current = true;
                }}
              />
            </label>
            <p className="order-total">
              итого <b className="num">{coins(api.minor(price) * volume)} ₭</b>
              <span className="note"> · налог платит продавец</span>
            </p>
            {tiers.length > 0 && (
              <p className="note">
                Покупка возьмёт «{tierOf(tiers, floor)}» и всё, что лучше;
                продажа идёт лотом ступени «{choice?.tier}».
              </p>
            )}
            <div className="row">
              <button
                onClick={() => deal("market.buy", api.minor(price))}
                disabled={busy || !choice || volume <= 0 || price <= 0}
                title="встать в стакан со своей ценой; что дешевле — купится сразу"
              >
                Купить
              </button>
              <button
                onClick={() => deal("market.sell", api.minor(price))}
                disabled={busy || !choice || volume <= 0 || price <= 0 || onShelf <= 0}
                title={
                  onShelf > 0
                    ? "встать в стакан со своей ценой; что дороже — продастся сразу"
                    : "продавать нечего: товар должен лежать в терминале"
                }
              >
                Продать
              </button>
            </div>
            <div className="row">
              <button
                className="quiet"
                onClick={() => deal("market.buy", bestSell!.price)}
                disabled={busy || !choice || !bestSell || volume <= 0}
                title="купить по лучшей цене продавцов"
              >
                По рынку купить{bestSell ? ` · ${api.tk(bestSell.price)} ₭` : ""}
              </button>
              <button
                className="quiet"
                onClick={() => deal("market.sell", bestBuy!.price)}
                disabled={busy || !choice || !bestBuy || volume <= 0 || onShelf <= 0}
                title="продать по лучшей цене покупателей; товар должен лежать в терминале"
              >
                По рынку продать{bestBuy ? ` · ${api.tk(bestBuy.price)} ₭` : ""}
              </button>
            </div>
            <p className="note">
              Остаток заявки встаёт ордером и ждёт. Покупают стоя здесь; свои
              ордера — в «торговле».
            </p>
          </div>

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
                  бронь: {reservation.goods} ·{" "}
                  {tally(reservation.goods, reservation.amount)} по{" "}
                  {api.tk(reservation.price)} ₭
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
          <h3>
            Терминал
            <Rule>
              Продаётся то, что в терминале; купленное забирается отсюда же. Клик по
              строке выбирает позицию. Перетащите сюда строку из инвентаря, чтобы
              выложить, и обратно в инвентарь — чтобы забрать.
            </Rule>
          </h3>
          {/* The drag pair (D-238): the sidebar's inventory is the other half
              of it. A stack dropped here is loaded, a row dragged out of here
              into the sidebar is taken back -- the same two commands the
              buttons send. */}
          <DropZone
            zone="terminal"
            accepts={["hands"]}
            disabled={busy}
            hint="перетащите сюда предмет из инвентаря, чтобы выложить"
            onMove={(stack, amount) =>
              //: The stack carries its own key and tier (grip below): the
              //: command needs no lookup that could miss after a reread.
              act(() =>
                session.send("market.load", {
                  goods: stack.key ?? stack.goods,
                  amount,
                  tier: stack.tier,
                }),
              )
            }
          >
            <Shelf
              things={terminal}
              book={book}
              choice={choice}
              mark={pick}
              busy={busy}
              take={(t, amount) =>
                act(() =>
                  session.send("market.take", {
                    goods: nameOf(t),
                    tier: t.tier,
                    amount,
                  }),
                )
              }
            />
          </DropZone>
        </div>
      </div>
    </section>
  );
}

/** What lies on the counter: rows to sell from, and a way to take them back. */
function Shelf({
  things,
  book,
  choice,
  mark,
  take,
  busy,
}: {
  things: Thing[];
  book: RecipeBook | null;
  choice: Position | null;
  mark: (p: Position) => void;
  take: (t: Thing, amount: number) => void;
  busy: boolean;
}) {
  //: How much of each stack to take back. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  if (things.length === 0) {
    return <p className="note">в терминале ничего вашего</p>;
  }
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
              {...grip({
                item: t.id,
                goods: t.goods,
                label: t.flavor ?? name,
                amount: t.amount,
                zone: "terminal",
                tier: t.tier,
                key: t.key ?? undefined,
              })}
            >
              <td>
                <GoodsMark book={book} goods={t.goods} />
                {t.flavor ?? name}
              </td>
              <td className="num">{tally(t.goods, t.amount)}</td>
              <td className="note">
                {t.quality == null ? "" : `${t.quality.toFixed(0)} · ${t.tier}`}
              </td>
              <td onClick={(e) => e.stopPropagation()} {...noDrag}>
                <Amount
                  goods={t.goods}
                  value={parts[t.id] ?? null}
                  max={t.amount}
                  onChange={(value) => setParts((was) => ({ ...was, [t.id]: value }))}
                />
              </td>
              <td>
                <button
                  className="quiet"
                  onClick={(e) => {
                    e.stopPropagation();
                    take(t, part);
                  }}
                  disabled={busy || part <= 0}
                >
                  Забрать
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
