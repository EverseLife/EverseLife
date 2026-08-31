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
import { Refusal, useActions, useBook, useCompare, useNames, useSession } from "../actions";
import { DropZone } from "../DragMove";
import { grip, noDrag } from "../drag";
import { GoodsMark } from "../Glyph";
import { catalogue, coins, exactly, floorOf, tierOf } from "../market";
import type { QualityTier } from "../market";
import { flavorText, goodsKeyName, goodsName, tierName, type Names } from "../names";
import { t } from "../locale";

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
  const names = useNames();
  const order = useCompare();
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
    void api.book(node, choice.goods, choice.tier, step).then((answer) => {
      if (!current) return;
      if (answer.type_key !== choice.goods || answer.tier !== choice.tier) return;
      setOrderBook(answer);
    });
    return () => {
      current = false;
    };
  }, [node, choice, edition, step]);

  //: The floor follows the tier until the hand names one of its own. Written
  //: as an effect rather than inside the tier button, because the first
  //: position is chosen by the panel itself -- and a floor left at zero there
  //: would buy the worst tier under a button that names a better one.
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
  const carrying = inHands.map((one) => `${one.key ?? one.goods}|${one.tier}`).join(",");
  const shelved = terminal.map((one) => `${one.key ?? one.goods}|${one.tier}`).join(",");

  //: Book positions are goods plus quality tier: "ore, good" is a separate
  //: row, not a range (D-058). What an empty search offers: what trades here
  //: and what the player has to sell -- the two lists a hand reaches for.
  const near = useMemo(() => {
    const seen = new Map<string, Position>();
    for (const p of positions) seen.set(`${p.goods}|${p.tier}`, p);
    for (const stack of [...inHands, ...terminal]) {
      //: Everything held, quality or none: a coin and a seed have a tier as
      //: much as an ore does (the engine's lowest one), and the old list left
      //: them out -- so what a player was carrying could not be offered.
      //: The counter's name, not the item's: a written carrier is a position
      //: per recipe -- "Рецепт: Стекло" (D-209).
      const goods = stack.key ?? stack.goods;
      seen.set(`${goods}|${stack.tier}`, { goods, tier: stack.tier });
    }
    return [...seen.values()];
    //: Keyed by what the lists hold, not by the objects that hold them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, carrying, shelved]);

  //: The comparator carries the language: a switch changes its identity and
  //: the catalogue is laid out again in the new reading order.
  const everything = useMemo(() => catalogue(book, names, order), [book, names, order]);
  const found = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    //: The whole catalogue, and the names one already deals in first: the
    //: search is for finding the unknown, not for losing the known. The player
    //: types the Russian word (D-251), so the match runs over the display name
    //: -- and over the id itself, for whoever pastes one.
    const mine = new Set(near.map((p) => p.goods));
    return everything
      .filter(
        (id) => goodsName(names, id).toLowerCase().includes(q) || id.toLowerCase().includes(q),
      )
      .sort((a, b) => Number(mine.has(b)) - Number(mine.has(a)));
  }, [query, everything, near, names]);

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
    //: New goods are a new trade entirely, and a floor named by hand was
    //: named for the goods it was named on. A tier switch within the same
    //: goods leaves it alone.
    if (position.goods !== choice?.goods) floorIsMine.current = false;
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
    return here[0] ?? choice?.tier ?? tiers[2]?.name ?? "common";
  };

  /** A rung of the book becomes the price in the field: reading a number off
   *  the screen to type it back in is work the panel can do. */
  const takePrice = (minor: number) => {
    setPrice(minor / api.MONEY_SCALE);
    priceIsMine.current = true;
  };

  //: Whether this position is counted in pieces rather than measured (D-212).
  const whole = choice ? counted(choice.goods) : false;

  const nameOf = (one: Thing) => one.key ?? one.goods;
  const onShelf = choice
    ? terminal
        .filter((one) => nameOf(one) === choice.goods && one.tier === choice.tier)
        .reduce((sum, one) => sum + one.amount, 0)
    : 0;
  const atHand = choice
    ? inHands
        .filter((one) => nameOf(one) === choice.goods && one.tier === choice.tier)
        .reduce((sum, one) => sum + one.amount, 0)
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
      <h2>{t("ui-market-title")}</h2>
      <div className="market-grid">
        <div>
          {/* Choosing what to trade: search over the whole catalogue, and the
              quality tier beside it -- "ore, good" is its own book (D-058). */}
          <div className="row search">
            <input
              type="search"
              placeholder={t("ui-market-search")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label={t("ui-market-search-label")}
            />
            {/* The counts travel as the digits chosen here: handed over raw,
                Fluent would group them by the locale's own rules, and the
                catalogue is long enough for that to show. */}
            <span className="note">
              {found
                ? t("ui-market-found", {
                    found: String(found.length),
                    all: String(everything.length),
                  })
                : t("ui-market-hint")}
            </span>
          </div>

          <div className="market-pick">
            {(found ?? near.map((p) => p.goods)).length === 0 ? (
              <p className="note">
                {found ? t("ui-market-none-found") : t("ui-market-none-traded")}
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
                      {goodsKeyName(names, goods)}
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
              <span className="note">{t("ui-market-quality")}</span>
              {tiers.map(({ name }) => (
                <button
                  key={name}
                  className={choice.tier === name ? "" : "quiet"}
                  aria-pressed={choice.tier === name}
                  onClick={() => pick({ goods: choice.goods, tier: name })}
                >
                  {tierName(names, name)}
                </button>
              ))}
            </div>
          )}

          {choice && (
            <p className="sign">
              {goodsKeyName(names, choice.goods)} · {tierName(names, choice.tier)}
              <span className="note">
                {" "}
                {t("ui-market-stock", { shelf: exactly(onShelf), hand: exactly(atHand) })}
              </span>
            </p>
          )}

          {/* Prices are written to the minor unit, and a book of rows a minor
              unit apart is a wall. Rows are glued into a step: the server
              picks the finest one that fits, and the hand can go finer. */}
          {liveBook && steps.length > 0 && (
            <div className="row tiers">
              <span className="note">{t("ui-market-step")}</span>
              <button
                className={step === null ? "" : "quiet"}
                aria-pressed={step === null}
                onClick={() => setStep(null)}
                title={t("ui-market-step-auto-title")}
              >
                {t("ui-market-step-auto")}
                {step === null ? ` · ${coins(liveBook.step)}` : ""}
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

          {liveBook && (liveBook.asks.length > 0 || liveBook.bids.length > 0) ? (
            <table className="book">
              <thead>
                <tr>
                  <th>{t("ui-market-bids")}</th>
                  <th>{t("ui-market-price")}</th>
                  <th>{t("ui-market-asks")}</th>
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
                    aria-label={t("ui-market-rung", { price: coins(u.price) })}
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
                    aria-label={t("ui-market-rung", { price: coins(u.price) })}
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
            <p className="note">{t("ui-market-book-empty")}</p>
          )}
          {liveBook?.last != null && (
            <p className="note">{t("ui-market-last", { price: api.tk(liveBook.last) })}</p>
          )}

          {/* One order, one place: how much, at what price, and what it comes
              to. The two quick buttons underneath are the same order with the
              book's own best price already in it. */}
          <div className="order">
            <label>
              <span>
                {choice
                  ? t("ui-market-volume-unit", { unit: unit(choice.goods) })
                  : t("ui-market-volume")}
              </span>
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
            {/* What the buyer will not go below (D-239). A lot better than
                asked is no loss, so the floor reaches the tiers above it. */}
            <label>
              <span>{t("ui-market-floor")}</span>
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
              <span>{t("ui-market-price-each")}</span>
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
              {t("ui-market-total")} <b className="num">{coins(api.minor(price) * volume)} ₭</b>
              <span className="note"> · {t("ui-market-tax")}</span>
            </p>
            {tiers.length > 0 && (
              <p className="note">
                {t("ui-market-floor-rule", {
                  floor: tierName(names, tierOf(tiers, floor) ?? ""),
                  tier: tierName(names, choice?.tier ?? ""),
                })}
              </p>
            )}
            <div className="row">
              <button
                onClick={() => deal("market.buy", api.minor(price))}
                disabled={busy || !choice || volume <= 0 || price <= 0}
                title={t("ui-market-buy-hint")}
              >
                {t("ui-market-buy")}
              </button>
              <button
                onClick={() => deal("market.sell", api.minor(price))}
                disabled={busy || !choice || volume <= 0 || price <= 0 || onShelf <= 0}
                title={onShelf > 0 ? t("ui-market-sell-hint") : t("ui-market-sell-none")}
              >
                {t("ui-market-sell")}
              </button>
            </div>
            <div className="row">
              <button
                className="quiet"
                onClick={() => deal("market.buy", bestSell!.price)}
                disabled={busy || !choice || !bestSell || volume <= 0}
                title={t("ui-market-buy-best-hint")}
              >
                {bestSell
                  ? t("ui-market-buy-best-at", { price: api.tk(bestSell.price) })
                  : t("ui-market-buy-best")}
              </button>
              <button
                className="quiet"
                onClick={() => deal("market.sell", bestBuy!.price)}
                disabled={busy || !choice || !bestBuy || volume <= 0 || onShelf <= 0}
                title={t("ui-market-sell-best-hint")}
              >
                {bestBuy
                  ? t("ui-market-sell-best-at", { price: api.tk(bestBuy.price) })
                  : t("ui-market-sell-best")}
              </button>
            </div>
            <p className="note">{t("ui-market-rest")}</p>
          </div>

          {/* Бронь — единственное исключение из «купить только стоя здесь»:
              купец, собираясь в дорогу, резервирует партию задатком (D-047). */}
          {foreign.length > 0 && (
            <>
              <h3>
                {t("ui-market-reserve-title")}
                <Rule>{t("ui-market-reserve-rule")}</Rule>
              </h3>
              <table>
                <tbody>
                  {foreign.map((offer) => (
                    <tr key={offer.id}>
                      <td>
                        {goodsKeyName(names, offer.goods)}, {tierName(names, offer.tier)}
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
                          title={t("ui-market-reserve-hint")}
                        >
                          {t("ui-market-reserve")}
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
                  {t("ui-market-reservation", {
                    goods: goodsKeyName(names, reservation.goods),
                    amount: tally(reservation.goods, reservation.amount),
                    price: api.tk(reservation.price),
                  })}
                </span>
                <button
                  onClick={() =>
                    act(() =>
                      session.send("market.redeem", { reservation: reservation.id }),
                    )
                  }
                  disabled={busy}
                >
                  {t("ui-market-redeem")}
                </button>
              </div>
            ))}
        </div>

        <div>
          <h3>
            {t("ui-market-terminal")}
            <Rule>{t("ui-market-terminal-rule")}</Rule>
          </h3>
          {/* The drag pair (D-238): the sidebar's inventory is the other half
              of it. A stack dropped here is loaded, a row dragged out of here
              into the sidebar is taken back -- the same two commands the
              buttons send. */}
          <DropZone
            zone="terminal"
            accepts={["hands"]}
            disabled={busy}
            hint={t("ui-market-terminal-drop")}
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
              names={names}
              choice={choice}
              mark={pick}
              busy={busy}
              take={(stack, amount) =>
                act(() =>
                  session.send("market.take", {
                    goods: nameOf(stack),
                    tier: stack.tier,
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
  names,
  choice,
  mark,
  take,
  busy,
}: {
  things: Thing[];
  book: RecipeBook | null;
  names: Names | null;
  choice: Position | null;
  mark: (p: Position) => void;
  take: (stack: Thing, amount: number) => void;
  busy: boolean;
}) {
  //: How much of each stack to take back. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  if (things.length === 0) {
    return <p className="note">{t("ui-market-terminal-empty")}</p>;
  }
  return (
    <table>
      <tbody>
        {/* The row is `stack`, not `t`: `t` is the locale's now, and a stack
            shadowing it would turn a label into a call on a thing. */}
        {things.map((stack) => {
          const name = stack.key ?? stack.goods;
          const shown = stack.flavor ? flavorText(names, stack.flavor) : goodsKeyName(names, name);
          const selected = choice?.goods === name && choice?.tier === stack.tier;
          const part = chosen(parts[stack.id] ?? null, stack.amount);
          return (
            <tr
              key={stack.id}
              className={`pick ${selected ? "picked" : ""}`}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={t("ui-market-row", { goods: shown, tier: tierName(names, stack.tier) })}
              onClick={() => mark({ goods: name, tier: stack.tier })}
              onKeyDown={(e) => onEnter(e, () => mark({ goods: name, tier: stack.tier }))}
              {...grip({
                item: stack.id,
                goods: stack.goods,
                label: shown,
                amount: stack.amount,
                zone: "terminal",
                tier: stack.tier,
                key: stack.key ?? undefined,
              })}
            >
              <td>
                <GoodsMark book={book} goods={stack.goods} />
                {shown}
              </td>
              <td className="num">{tally(stack.goods, stack.amount)}</td>
              <td className="note">
                {stack.quality == null
                  ? ""
                  : `${stack.quality.toFixed(0)} · ${tierName(names, stack.tier)}`}
              </td>
              <td onClick={(e) => e.stopPropagation()} {...noDrag}>
                <Amount
                  goods={stack.goods}
                  value={parts[stack.id] ?? null}
                  max={stack.amount}
                  onChange={(value) => setParts((was) => ({ ...was, [stack.id]: value }))}
                />
              </td>
              <td>
                <button
                  className="quiet"
                  onClick={(e) => {
                    e.stopPropagation();
                    take(stack, part);
                  }}
                  disabled={busy || part <= 0}
                >
                  {t("ui-market-take")}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
