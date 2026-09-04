// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The counter itself: what lies on it, what goes onto it, what comes off.
 *
 * The market panel's right column, split out of `panels/Market.tsx` when it
 * passed 800 lines a second time (CLAUDE.md). The left column is the order --
 * a position, a price, a volume; this is the matter the order is written
 * about, and the three ways it moves:
 *
 * - **dragged**, the other half of the sidebar's inventory (D-238): a stack
 *   dropped here is loaded, a row dragged out is taken back;
 * - **poured**, for a liquid (D-255): it is never a stack in the hands -- it
 *   is inside a canister, and dragging the canister would sell the canister --
 *   so the tank has its own tap;
 * - **taken**, by the row's own button (`Shelf`).
 *
 * What did not move whole is said here rather than left to a refusal: a pour
 * stops at the room in the vessels, and a player reading a full row, a pressed
 * button and no refusal has no way to know why the canister is not full.
 *
 * Under the shelf stand one's own orders **in this node** -- the same rows the
 * sidebar's finance tab draws, and the same withdrawal. What lies on the
 * counter and what is promised off it is one question, and it used to be
 * answered in the other zone: a seller wondering why the take offers less than
 * the row shows had to leave the market to find their own order.
 */

import { useState } from "react";

import type { Loaded, Order, RecipeBook, Taken, Thing } from "../../api";
import { Amount } from "../../Amount";
import { chosen } from "../../amounts";
import type { Actions } from "../../actions";
import { DropZone } from "../../DragMove";
import { isLiquid } from "../../liquids";
import { t } from "../../locale";
import { exactly, type Position } from "../../market";
import type { Names } from "../../names";
import { Rule } from "../../Rule";
import type { Session } from "../../session";
import { Orders } from "./Orders";
import { Shelf } from "./Shelf";

//: Below this a difference between what was asked and what moved is the two
//: sides rounding, not a short pour: amounts travel as floats and the engine
//: keeps them as thousandths (`units.AMOUNT_SCALE`).
const CLOSE_ENOUGH = 0.0005;

export function Counter({
  things,
  book,
  names,
  choice,
  mark,
  free,
  orders,
  node,
  session,
  acting,
  wet,
  atHand,
}: {
  things: Thing[];
  book: RecipeBook | null;
  names: Names | null;
  choice: Position | null;
  mark: (p: Position) => void;
  free: (goods: string, tier: string) => number;
  /** One's own standing orders in this node -- what the shelf is pledged to. */
  orders: readonly Order[];
  /** The node this counter stands in: a walk to another city is another counter. */
  node: string | undefined;
  session: Session;
  acting: Actions;
  /** Whether the chosen position is poured rather than handed (D-230, D-255). */
  wet: boolean;
  /** How much of the chosen position the hands hold -- through vessels, if wet. */
  atHand: number;
}) {
  const { busy, act } = acting;
  //: How much of a liquid to pour into the tank. Its own field and not the
  //: order's volume: one says what to sell, the other what to bring, and a
  //: seller pours a canister and then lists half of it.
  const [pour, setPour] = useState<number | null>(null);
  //: What the counter said of a move that did not go whole. Not a refusal:
  //: the part that moved, moved. It was said about this position at this
  //: counter, and outlives neither -- the choice survives a walk to another
  //: city, so the node is in the key as well as the position.
  const [partly, setPartly] = useState<string | null>(null);
  const [about, setAbout] = useState<string | null>(null);
  const here = `${node}|${choice?.goods}|${choice?.tier}`;
  if (about !== here) {
    setAbout(here);
    if (partly) setPartly(null);
  }

  const nameOf = (one: Thing) => one.key ?? one.goods;

  return (
    <div>
      <h3>
        {t("ui-market-terminal")}
        <Rule>{t("ui-market-terminal-rule")}</Rule>
      </h3>
      {choice && wet && (
        <div className="row">
          <Amount goods={choice.goods} value={pour} max={atHand} onChange={setPour} />
          <button
            onClick={() =>
              act(async () => {
                setPartly(null);
                const asked = chosen(pour, atHand);
                //: The tier goes with the pour: without one the engine takes
                //: the worst stack first, and a seller standing on fine
                //: spirit would have poured out their awful one.
                const answer = await session.send<Loaded>("market.load", {
                  goods: choice.goods,
                  tier: choice.tier,
                  amount: asked,
                });
                if (asked - answer.loaded > CLOSE_ENOUGH) {
                  setPartly(
                    t("ui-market-poured-in-part", {
                      poured: exactly(answer.loaded),
                      left: exactly(asked - answer.loaded),
                    }),
                  );
                }
                setPour(null);
              })
            }
            disabled={busy || atHand <= 0}
            title={t("ui-market-pour-hint")}
          >
            {t("ui-market-pour")}
          </button>
        </div>
      )}
      {/* Said in the panel and not in a title on the dead button: a disabled
          button takes no pointer, so its tooltip reaches nobody. */}
      {choice && wet && atHand <= 0 && <p className="note">{t("ui-market-pour-none")}</p>}
      {partly && <p className="note">{partly}</p>}
      <DropZone
        zone="terminal"
        accepts={["hands"]}
        disabled={busy}
        hint={t("ui-market-terminal-drop")}
        onMove={(stack, amount) =>
          //: The stack carries its own key and tier (`grip` in the shelf): the
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
          things={things}
          book={book}
          names={names}
          choice={choice}
          mark={mark}
          busy={busy}
          free={free}
          take={(stack, amount) =>
            act(async () => {
              setPartly(null);
              const answer = await session.send<Taken>("market.take", {
                goods: nameOf(stack),
                tier: stack.tier,
                amount,
              });
              //: A liquid is poured into the vessels by their room, and what
              //: has no room waits in the tank (D-255).
              if (isLiquid(book, nameOf(stack)) && amount - answer.taken > CLOSE_ENOUGH) {
                setPartly(
                  t("ui-market-poured-part", {
                    poured: exactly(answer.taken),
                    left: exactly(amount - answer.taken),
                  }),
                );
              }
            })
          }
        />
      </DropZone>

      <h3>
        {t("ui-market-orders")}
        <Rule>{t("ui-market-orders-rule")}</Rule>
      </h3>
      <Orders
        orders={orders}
        none={t("ui-market-orders-none")}
        busy={busy}
        act={act}
      />
    </div>
  );
}
