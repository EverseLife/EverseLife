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
 * actually sold off. There is no copy of the pocket here any more (D-238):
 * the sidebar's inventory is the hands, and a stack drags from it onto the
 * terminal and back. Where the two are not on screen together -- a narrow
 * screen puts the sidebar and the scene in different zones -- the way through
 * is the row menu's "В терминал" and the "Забрать" button here.
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
import { chosen, counted, tally, unit } from "../amounts";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useSession } from "../actions";
import { DropZone } from "../DragMove";
import { grip, noDrag } from "../drag";
import { GoodsMark } from "../Glyph";
import { catalogue, coins, exactly } from "../market";

//: The panel keeps its own waiting and its own refusal, so it takes neither.
type Props = { look: Look };

type Position = { goods: string; tier: string };

/**
 * A row that acts on a click acts on Enter and Space too.
 *
 * The rows here are `<tr>` rather than buttons because a button cannot hold a
 * table row, and the alternative -- a button inside every cell -- would put
 * three controls where the eye reads one line. So the row carries the button's
 * manners instead: a role, a stop on the tab ring, and these two keys.
 */
const onEnter = (event: React.KeyboardEvent, act: () => void) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  act();
};

export function Market({ look }: Props) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [positions, setPositions] = useState<Position[]>([]);
  const [choice, setChoice] = useState<Position | null>(null);
  const [orderBook, setOrderBook] = useState<Book | null>(null);
  const [tiers, setTiers] = useState<string[]>([]);
  const [query, setQuery] = useState("");
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
    void api.tiers().then(({ tiers }) => setTiers(tiers.map((t) => t.name)));
  }, []);

  useEffect(() => {
    if (!node) return;
    void api.positions(node).then(({ positions }) => {
      setPositions(positions);
      setChoice((previous) => previous ?? positions[0] ?? null);
    });
  }, [node, edition]);

  //: The book belongs to a position, and the answer must prove it does before
  //: it is shown. Without that a click on a new name left the old book on
  //: screen for as long as the fetch took -- and "по рынку" is one click away
  //: from it, so an order for the new goods went out at the old goods' price.
  //: Cleared first for the same reason: an empty book disables those buttons,
  //: a stale one does not.
  useEffect(() => {
    if (!node || !choice) return;
    let current = true;
    setOrderBook(null);
    void api.book(node, choice.goods, choice.tier).then((answer) => {
      if (!current) return;
      if (answer.type_key !== choice.goods || answer.tier !== choice.tier) return;
      setOrderBook(answer);
    });
    return () => {
      current = false;
    };
  }, [node, choice, edition]);

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
      //: Everything held, quality or none: a coin and a seed have a tier as
      //: much as an ore does (the engine's lowest one), and the old list left
      //: them out -- so what a player was carrying could not be offered.
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

  /**
   * The book, but only if it is this position's book.
   *
   * Clearing it in the effect is not enough on its own: the click that changes
   * the position renders once with the new name and the old book still in
   * hand, and in that frame "по рынку" would send an order for the new goods
   * at the old goods' price. Asked of the data instead of the order of events,
   * the stale book is invisible from the same render the choice changes in.
   */
  const liveBook =
    orderBook && orderBook.type_key === choice?.goods && orderBook.tier === choice?.tier
      ? orderBook
      : null;
  const bestSell = liveBook?.asks[0] ?? null; // what they sell at: buy here
  const bestBuy = liveBook?.bids[0] ?? null; // what they take at: sell here

  //: A price to start from, so that the field is never a guess: the last deal
  //: if there was one, otherwise whichever side of the book exists.
  //: Divided out rather than run through `tk`, which rounds to the coin's two
  //: decimals: a standing bid of one minor unit came back as a price of zero,
  //: and the field then kept the zero into the next position.
  useEffect(() => {
    if (priceIsMine.current) return;
    const suggestion = liveBook?.last ?? bestSell?.price ?? bestBuy?.price ?? null;
    if (suggestion != null && suggestion > 0) setPrice(suggestion / api.MONEY_SCALE);
  }, [liveBook, bestSell, bestBuy]);

  const pick = (position: Position) => {
    //: A new position is a new price question, and the field follows again --
    //: but only when the position really changed. Clicking the row one is
    //: already on (the terminal's rows pick too) must not wipe a typed price.
    if (choice?.goods !== position.goods || choice?.tier !== position.tier) {
      priceIsMine.current = false;
    }
    setChoice(position);
  };

  /**
   * The tier to open a name at.
   *
   * What trades here wins: looking at "ore, excellent" and then searching out
   * bread must not land on "bread, excellent" -- the books are matched by tier
   * exactly (D-058), and an order in a tier nobody deals in would stand for
   * ever. The tier being looked at is kept only when this name is traded in it.
   */
  const tierFor = (goods: string): string => {
    const here = near.filter((p) => p.goods === goods).map((p) => p.tier);
    if (choice && here.includes(choice.tier)) return choice.tier;
    return here[0] ?? choice?.tier ?? tiers[2] ?? "обычное";
  };

  /** A rung of the book becomes the price in the field: reading a number off
   *  the screen to type it back in is work the panel can do. */
  const takePrice = (minor: number) => {
    setPrice(minor / api.MONEY_SCALE);
    priceIsMine.current = true;
  };

  //: Whether this position is counted in pieces rather than measured (D-212).
  const whole = choice ? counted(choice.goods) : false;

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
              /* Every match, not the first forty: the box scrolls, and a list
                 silently cut at forty is a list that does not contain what the
                 search says it found. */
              <ul className="picks">
                {[...new Set(found ?? near.map((p) => p.goods))].map((goods) => (
                  <li key={goods}>
                    <button
                      className={choice?.goods === goods ? "" : "quiet"}
                      aria-pressed={choice?.goods === goods}
                      onClick={() => pick({ goods, tier: tierFor(goods) })}
                    >
                      <GoodsMark book={book} goods={goods} />
                      {goods}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {choice && tiers.length > 0 && (
            <div className="row tiers">
              <span className="note">качество</span>
              {tiers.map((tier) => (
                <button
                  key={tier}
                  className={choice.tier === tier ? "" : "quiet"}
                  aria-pressed={choice.tier === tier}
                  onClick={() => pick({ goods: choice.goods, tier })}
                >
                  {tier}
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

          {liveBook && (liveBook.asks.length > 0 || liveBook.bids.length > 0) ? (
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
                {[...liveBook.asks].reverse().map((u) => (
                  <tr
                    key={`a${u.price}`}
                    className="pick"
                    role="button"
                    tabIndex={0}
                    aria-label={`цена ${coins(u.price)} за единицу`}
                    onClick={() => takePrice(u.price)}
                    onKeyDown={(e) => onEnter(e, () => takePrice(u.price))}
                  >
                    <td />
                    <td className="num">{coins(u.price)}</td>
                    <td className="num">{tally(choice!.goods, u.amount)}</td>
                  </tr>
                ))}
                {liveBook.bids.map((u) => (
                  <tr
                    key={`b${u.price}`}
                    className="pick"
                    role="button"
                    tabIndex={0}
                    aria-label={`цена ${coins(u.price)} за единицу`}
                    onClick={() => takePrice(u.price)}
                    onKeyDown={(e) => onEnter(e, () => takePrice(u.price))}
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
          {liveBook?.last != null && (
            <p className="note">последняя сделка: {api.tk(liveBook.last)} ₭</p>
          )}

          {/* One order, one place: how much, at what price, and what it comes
              to. The two quick buttons underneath are the same order with the
              book's own best price already in it. */}
          <div className="order">
            <label>
              <span>сколько{choice ? `, ${unit(choice.goods)}` : ""}</span>
              {/* The field obeys the thing (D-212): a counted one steps and
                  rounds to whole pieces -- the engine refuses a half loaf, and
                  a field must not offer what cannot be done. */}
              <input
                type="number"
                step={whole ? 1 : "any"}
                min="0"
                value={volume}
                onChange={(e) => {
                  const typed = Number(e.target.value);
                  if (!Number.isFinite(typed)) return;
                  const held = Math.max(0, typed);
                  setVolume(whole ? Math.floor(held) : held);
                }}
              />
            </label>
            <label>
              <span>цена за единицу, ₭</span>
              {/* Money steps by the coin's own hundredth; the field still takes
                  a typed price down to the last minor unit, because the book
                  has rungs there. */}
              <input
                type="number"
                step="0.01"
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
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={`позиция ${name}, ${t.tier}`}
              onClick={() => mark({ goods: name, tier: t.tier })}
              onKeyDown={(e) => onEnter(e, () => mark({ goods: name, tier: t.tier }))}
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
