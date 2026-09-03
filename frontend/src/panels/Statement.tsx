// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The statement: the account's postings a page at a time, each row opening
 * in place (D-190).
 *
 * A balance without a history is a number nobody can argue with, and a
 * history cut off at the latest fifty is one that cannot be argued with for
 * long. The pages turn by the id of the last row read: the journal grows at
 * the top, and an offset would slide under the reader.
 *
 * The eye on a row asks the server for what the row cannot show -- every
 * side of the operation, the deal a sale settled, the order a deposit was
 * frozen under and what it bought. A buyer's own statement never carries
 * the deal: the money left for the escrow first, and the settlement is the
 * escrow's, so the deposit row is where a purchase is read.
 */

import { useEffect, useState } from "react";
import { useNames, useSession } from "../actions";
import type { Look } from "../api";
import { tally } from "../amounts";
import { when } from "../clock";
import { Glyph } from "../Glyph";
import { groundName } from "../grounds";
import { t } from "../locale";
import { groundGlyph } from "../marks";
import { goodsKeyName, tierName } from "../names";

/** One line of the statement, as the server sends it. */
type Entry = {
  /** The row's own number: the page turns by it and the row opens by it. */
  id: number;
  at: string;
  reason: string;
  amount: number;
  money: string;
  incoming: boolean;
  memo: Record<string, string>;
  /** The other side's own name, when it has one: a person, or a city. */
  with: string | null;
  /**
   * What kind of side it is, when it is not a person -- `genesis`,
   * `bank_reserve`, `works_fund`. The server sends the enum and the word for
   * it comes out of the locale (D-251): it used to send «резерв банка» ready
   * made, and the two members nobody had written a word for arrived as their
   * own code in the middle of the statement.
   */
  side: string | null;
};

/** One leg of the operation a row belongs to. The reader's own leg carries
 * the reader's name, and `look.identity` is that name (D-225). */
type Side = Pick<Entry, "with" | "side" | "money" | "incoming">;

/**
 * The deal a `trade` row settled. What the buyer paid is the escrow's leg
 * among the sides, and the seller is the reader: a `trade` row stands on no
 * account but the seller's (D-225).
 */
type Deal = {
  goods: string;
  tier: string;
  amount: number;
  price: string;
  tax: string;
  fee: string;
  buyer: string | null;
  market: string | null;
  reserved: boolean;
};

/** One deal settled against the order a deposit was frozen under. */
type Fill = { with: string | null; amount: number; price: string; at: string };

/** The order behind an `escrow_hold` or `escrow_release` row. What it has
 * bought is the sum of its fills (D-225). */
type Held = {
  goods: string;
  tier: string;
  amount: number;
  price: string;
  market: string | null;
  fills: Fill[];
};

/** What the eye opens: the sides, and the deal or the order when there is one. */
type Opened = { sides: Side[]; deal: Deal | null; order: Held | null };

type Names = ReturnType<typeof useNames>;

/**
 * Who stood on the other side of the posting, as a person reads it.
 *
 * A person is named and there is nothing to translate. An institution is not:
 * the server sends what kind of side it was and the word comes from the
 * locale, so the same row reads «резерв банка» to one player and "the reserve"
 * to another. A city treasury is both at once -- a kind, with a name in it.
 */
function counterparty(entry: Pick<Entry, "with" | "side">): string {
  if (!entry.side) return entry.with ?? "—";
  return t(`ledger-side-${entry.side}`, {
    //: A variant key is an identifier, never a boolean: the flag says whether
    //: there is a name to put in, and the name travels beside it.
    named: entry.with ? "true" : "false",
    name: entry.with ?? "",
  });
}

/** The sign and the sum, the way the row shows them. */
function signed(incoming: boolean, money: string): string {
  return `${incoming ? "+" : "−"}${money} ₭`;
}

export function Statement({ look }: { look: Look }) {
  const session = useSession();
  const names = useNames();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [more, setMore] = useState(false);
  //: The ids the pages were turned at, in order: the page shown stands under
  //: the last of them, and "newer" is a step back along the same path.
  const [turned, setTurned] = useState<number[]>([]);
  //: One row open at a time, like a recipe in the sidebar. What it opened
  //: into is asked over when it opens and again when the account moves: the
  //: fills of an order grow after its deposit was written.
  const [shown, setShown] = useState<number | null>(null);
  //: Kept with the id it answers for: a row just opened shows as waiting
  //: until its own answer lands, not the previous row's.
  const [opened, setOpened] = useState<{ id: number; what: Opened | null } | null>(null);

  const before = turned.length > 0 ? turned[turned.length - 1] : undefined;

  useEffect(() => {
    let live = true;
    session
      .send("finance.statement", before === undefined ? {} : { before })
      .then((answer) => {
        if (!live) return;
        setEntries((answer.entries as Entry[]) ?? []);
        setMore(Boolean(answer.more));
      })
      .catch(() => {
        if (!live) return;
        setEntries([]);
        setMore(false);
      });
    return () => {
      live = false;
    };
  }, [session, before, look.money]);

  useEffect(() => {
    if (shown === null) return;
    let live = true;
    session
      .send("finance.posting", { entry: shown })
      .then((answer) => {
        if (live) setOpened({ id: shown, what: answer as unknown as Opened });
      })
      .catch(() => {
        if (live) setOpened({ id: shown, what: null });
      });
    return () => {
      live = false;
    };
  }, [session, shown, look.money]);

  const turn = (to: number[]) => {
    setTurned(to);
    setShown(null);
  };

  return (
    <>
      <h3>{t("ui-finance-statement")}</h3>
      {entries.length === 0 ? (
        <p className="note">{t("ui-finance-none")}</p>
      ) : (
        <div className="facts">
          {entries.map((entry) => {
            const ground = groundName(entry.reason);
            return (
              <div className="fact" key={entry.id}>
                <span className="fact-name">
                  <span className="goods-mark">
                    <Glyph name={groundGlyph(entry.reason)} />
                  </span>
                  {ground}
                  <button
                    className="bare peek"
                    aria-label={t("ui-finance-peek", { ground })}
                    aria-expanded={shown === entry.id}
                    title={t("ui-finance-peek", { ground })}
                    onClick={() => setShown(shown === entry.id ? null : entry.id)}
                  >
                    <Glyph name="eye" />
                  </button>
                </span>
                <span className="fact-val">{signed(entry.incoming, entry.money)}</span>
                <p className="note">
                  {counterparty(entry)}
                  {entry.memo?.ground ? ` · ${entry.memo.ground}` : ""}
                  {` · ${when(entry.at)}`}
                </p>
                {shown === entry.id && (
                  <div className="note fact-peek">
                    <Details
                      entry={entry}
                      opened={opened?.id === entry.id ? opened.what : undefined}
                      me={look.identity}
                      names={names}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {(turned.length > 0 || more) && (
        <div className="row">
          <button
            className="quiet"
            disabled={turned.length === 0}
            onClick={() => turn(turned.slice(0, -1))}
          >
            {t("ui-finance-newer")}
          </button>
          <button
            className="quiet"
            disabled={!more}
            onClick={() => turn([...turned, entries[entries.length - 1].id])}
          >
            {t("ui-finance-older")}
          </button>
        </div>
      )}
    </>
  );
}

function Details({
  entry,
  opened,
  me,
  names,
}: {
  entry: Entry;
  opened: Opened | null | undefined;
  me: string;
  names: Names;
}) {
  if (opened === undefined) return <div>{t("ui-finance-peek-wait")}</div>;
  if (opened === null) return <div>{t("ui-finance-peek-none")}</div>;
  return (
    <>
      {opened.sides.map((side, at) => (
        <div key={at}>
          {side.with === me ? t("ui-finance-side-me") : counterparty(side)}
          {` · ${signed(side.incoming, side.money)}`}
        </div>
      ))}
      {entry.memo?.ground && <div>{t("ui-finance-ground", { ground: entry.memo.ground })}</div>}
      {opened.deal && <DealLines deal={opened.deal} sides={opened.sides} names={names} />}
      {opened.order && <HeldLines held={opened.order} names={names} />}
    </>
  );
}

function DealLines({ deal, sides, names }: { deal: Deal; sides: Side[]; names: Names }) {
  //: What the buyer paid is what left the escrow: the one leg of a
  //: settlement that gives rather than receives (D-225).
  const cost = sides.find((side) => !side.incoming)?.money ?? "0";
  return (
    <>
      <div>
        {t("ui-finance-deal-goods", {
          goods: goodsKeyName(names, deal.goods),
          tier: tierName(names, deal.tier),
          amount: tally(deal.goods, deal.amount),
        })}
      </div>
      <div>{t("ui-finance-deal-price", { price: deal.price, cost })}</div>
      {deal.buyer && <div>{t("ui-finance-deal-buyer", { name: deal.buyer })}</div>}
      {deal.market && <div>{t("ui-finance-node", { node: deal.market })}</div>}
      <div>{t("ui-finance-deal-charges", { tax: deal.tax, fee: deal.fee })}</div>
      {deal.reserved && <div>{t("ui-finance-deal-reserved")}</div>}
    </>
  );
}

function HeldLines({ held, names }: { held: Held; names: Names }) {
  //: What was bought is what the fills add up to: the wire carries the deals
  //: and not their sum (D-225).
  const bought = held.fills.reduce((sum, fill) => sum + fill.amount, 0);
  return (
    <>
      <div>
        {t("ui-finance-order", {
          goods: goodsKeyName(names, held.goods),
          tier: tierName(names, held.tier),
          amount: tally(held.goods, held.amount),
          price: held.price,
        })}
      </div>
      {held.market && <div>{t("ui-finance-node", { node: held.market })}</div>}
      <div>
        {t("ui-finance-order-filled", {
          filled: tally(held.goods, bought),
          amount: tally(held.goods, held.amount),
        })}
      </div>
      {held.fills.map((fill, at) => (
        <div key={at}>
          {t("ui-finance-fill", {
            name: fill.with ?? "—",
            amount: tally(held.goods, fill.amount),
            price: fill.price,
            when: when(fill.at),
          })}
        </div>
      ))}
    </>
  );
}
