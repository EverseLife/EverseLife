// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What is in the hands, and what can be done with it.
 *
 * A table with one menu per row instead of a row of buttons per thing. The
 * reason is not tidiness: the same thing can be worn, put down, put in a chest
 * or handed to somebody, and four buttons on every line turned the inventory
 * into a wall of controls where the goods themselves were hard to find. A menu
 * puts the thing first and its verbs one tap away.
 *
 * Putting down lives here too, and only here. It used to be repeated in the
 * floor window, the chest window and the hold window -- three lists of the same
 * hands, three ways to do one thing. A thing is dropped from where it is held.
 *
 * Handing over is in person on both sides, and it **speaks in the room**: the
 * chat gets an action line. Property moving between two people is a fact the
 * others present can see, and a silent transfer would be a way to move it unseen.
 */

import { useEffect, useState } from "react";
import type { RecipeBook } from "../api";
import { stationsOf, varietyText, type Look, type Thing } from "../api";
import { Refusal, useActions, useBook, useNames, useSession } from "../actions";
import { flavorText, goodsName, slotName, tierName, type Names } from "../names";
import { t } from "../locale";
import { mayInstall } from "../building";
import { Rule } from "../Rule";
import { Amount } from "../Amount";
import { DropZone } from "../DragMove";
import { GoodsMark } from "../Glyph";
import { CHEST_ANY, chestOf, grip, noDrag } from "../drag";
import { chosen, tally } from "../amounts";
import { TERMINAL, classOf, firstOfClass, isGear } from "../classes";
import { fill, isVessel } from "../liquids";
import { whoIsHere, type Person } from "../people";
import {
  GROUPINGS,
  SORTINGS,
  arrange,
  groupKey,
  orderGroups,
  remember,
  remembered,
  summarize,
  type Grouping,
  type Sorting,
  type Summary,
} from "../arrange";

type Props = { look: Look };

/** Which sub-question the open menu is asking. */
type Asking = null | { item: string; about: "menu" | "where" | "whom" };

export function Inventory({ look }: Props) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  const acting = useActions();
  const { busy, act } = acting;
  const [asking, setAsking] = useState<Asking>(null);
  //: How the table is read: grouped by what, sorted by what. Remembered
  //: across reloads -- an axis chosen once is how this player thinks.
  const [group, setGroup] = useState<Grouping>(() => remembered().group);
  const [sort, setSort] = useState<Sorting>(() => remembered().sort);
  const [desc, setDesc] = useState<boolean>(() => remembered().desc);
  useEffect(() => remember({ group, sort, desc }), [group, sort, desc]);
  //: Which groups are open. Empty on purpose, and it starts empty again on a
  //: change of axis: grouping is asked for when the list has grown too long to
  //: read, and answering with the same long list unfolded answers nothing. The
  //: header says how much and how good, and that is enough to choose by.
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  useEffect(() => setOpened(new Set()), [group]);
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const [people, setPeople] = useState<Person[]>([]);

  //: Asked only when somebody is about to hand a thing over: what this list is
  //: for here is the set of possible receivers, and it must not be fetched for
  //: every open menu. Who stands in the room is a separate question and has an
  //: answer of its own now -- `panels/Here` names them in the talk's head, on
  //: the room's own events rather than on a poll. The rule the old note here
  //: stated -- never a list of names polled on every look -- still holds, and
  //: is what both places are written to.
  useEffect(() => {
    if (asking?.about !== "whom") return;
    void whoIsHere(session)
      .then(setPeople)
      .catch(() => setPeople([]));
  }, [session, asking?.about, look.node?.key]);

  const carried = look.carry;
  const chests = (look.storages ?? []).filter((chest) => chest.mine);
  //: Vessels take a pour, not a "put" (D-230): a canister goes into a chest
  //: like any thing, but what is in it goes into a tank by the hose.
  const tanks = chests.filter((chest) => isVessel(book, chest.goods));
  const boxes = chests.filter((chest) => !isVessel(book, chest.goods));
  const things = look.inventory;
  //: The floor is symmetric since D-204: whoever got in through the door puts
  //: down and takes up alike, on a city's floor as on their own. Only a body
  //: passing through a shut place -- inside without being let in -- has no
  //: floor at all, and `floor.open` is that one door (live check 2026-09-02:
  //: the window still asked for one's own land, and a guest in the capital
  //: could neither unload nor pick up what fell).
  const mayDropHere = Boolean(look.floor?.open);
  //: Whether a machine from the hands can be put up here at all (D-106, D-278):
  //: one's own building, a roof, a place left. One answer for the whole list.
  const standable = mayInstall(look);
  //: Which of the two surfaces this place actually has (D-244). A plot with no
  //: house has no floor; a house grown over the whole plot leaves no ground.
  //: Offering the half that is not there collects a refusal after the click.
  const roofed = (look.floor?.space.area ?? 0) > 0;
  const openGround = (look.ground?.space.area ?? 0) > 0;
  //: Whether a counter stands here at all: without one there is nothing to
  //: lay goods out on, and the market panel is not open either.
  const atTerminal = firstOfClass(book, stationsOf(look), TERMINAL) !== undefined;

  const part = (thing: Thing) => chosen(parts[thing.id] ?? null, thing.amount);
  const close = () => setAsking(null);

  //: One spelling of a row's name (D-251): the flavor by tokens, a written
  //: carrier as "носитель: рецепт", everything else by its display word.
  const label = (thing: Thing): string =>
    thing.flavor
      ? flavorText(names, thing.flavor)
      : thing.recipe
        ? `${goodsName(names, thing.goods)}: ${goodsName(names, thing.recipe)}`
        : goodsName(names, thing.goods);

  const send = (cmd: string, args: Record<string, unknown>) =>
    act(async () => {
      await session.send(cmd, args);
      close();
    });

  return (
    <div>
      {carried && (
        <p className="sign">
          {t("ui-inventory-carry", {
            load: carried.load.toFixed(1),
            capacity: carried.capacity.toFixed(0),
          })}
          <Rule>{t("ui-inventory-carry-rule")}</Rule>
        </p>
      )}

      {carried && carried.slots.length > 0 && (
        <div className="row">
          {carried.slots.map((slot) => {
            const worn = carried.equipped[slot];
            return (
              <span key={slot} className="note">
                {slotName(names, slot)}:{" "}
                {worn ? (
                  <>
                    {goodsName(names, worn.goods)}{" "}
                    <button
                      className="link"
                      onClick={() => act(() => session.send("gear.unequip", { slot }))}
                      disabled={busy}
                    >
                      {t("ui-inventory-unequip")}
                    </button>
                  </>
                ) : (
                  t("ui-inventory-slot-empty")
                )}
              </span>
            );
          })}
        </div>
      )}

      <Refusal of={acting} />

      {things.length > 1 && (
        <div className="row arrange">
          <select
            value={group}
            onChange={(e) => setGroup(e.target.value as Grouping)}
            title={t("ui-inventory-group")}
          >
            {GROUPINGS.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label}
              </option>
            ))}
          </select>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as Sorting)}
            title={t("ui-inventory-sort")}
          >
            {SORTINGS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
          <button
            className="quiet"
            onClick={() => setDesc(!desc)}
            title={desc ? t("ui-inventory-desc") : t("ui-inventory-asc")}
          >
            {desc ? "↓" : "↑"}
          </button>
        </div>
      )}

      {/* The whole list is a drop target (D-238): whatever lies on the open
          surface -- the floor, a chest, a hold, the terminal -- drags into
          the pocket. Each source keeps its own command, byte for byte the
          same one its buttons send. */}
      <DropZone
        zone="hands"
        accepts={["floor", "ground", CHEST_ANY, "hold", "terminal"]}
        disabled={busy}
        hint={t("ui-inventory-drop-hint")}
        onMove={(stack, amount) => {
          //: Both surfaces of a node are picked up by one command: what a hand
          //: reaches for is what it can see, and `ground.pick` does not ask
          //: which of the two it was lying on (D-244).
          if (stack.zone === "floor" || stack.zone === "ground") {
            void send("ground.pick", { item: stack.item, amount });
          } else if (stack.zone.startsWith("chest:")) {
            void send("storage.take", {
              storage: chestOf(stack.zone),
              item: stack.item,
              amount,
            });
          } else if (stack.zone === "hold") {
            void send("transport.unload", { item: stack.item, amount });
          } else if (stack.zone === "terminal") {
            void send("market.take", {
              goods: stack.key ?? stack.goods,
              tier: stack.tier,
              amount,
            });
          }
        }}
      >
      {things.length === 0 ? (
        <p className="note">{t("ui-inventory-empty")}</p>
      ) : (
        <table className="goods">
          <tbody>
            {sections(things, group, sort, desc, book, names).flatMap(({ title, rows, summary }) => [
              ...(title === null
                ? []
                : [
                    <tr key={`group:${title}`} className="group">
                      <td colSpan={4}>
                        <button
                          type="button"
                          className="bare fold"
                          aria-expanded={opened.has(title)}
                          onClick={() =>
                            setOpened((was) => {
                              const next = new Set(was);
                              if (!next.delete(title)) next.add(title);
                              return next;
                            })
                          }
                        >
                          <span className="mark" aria-hidden="true">
                            {opened.has(title) ? "▾" : "▸"}
                          </span>
                          <b>{title}</b>
                          <span className="note">{sums(summary, rows.length)}</span>
                        </button>
                      </td>
                    </tr>,
                  ]),
              ...(title !== null && !opened.has(title) ? [] : rows).map((thing) => (
              <tr
                key={thing.id}
                //: The sidebar's rows are drag sources too (D-238): the floor,
                //: a chest and a hold in the scene accept the "hands" zone, so
                //: a stack drags straight out of the pocket into the room.
                {...grip({
                  item: thing.id,
                  goods: thing.goods,
                  label: label(thing),
                  amount: thing.amount,
                  zone: "hands",
                  //: The market counts by name and quality tier, not by the
                  //: item's identity (D-058), so a stack dragged to a terminal
                  //: carries both -- and a written carrier carries the name the
                  //: counter knows it by (D-209).
                  tier: thing.tier,
                  key: thing.key ?? undefined,
                })}
              >
                <td>
                  {/* The class mark before the name (D-238): the name stays,
                      the glyph only lets the eye sort the column. */}
                  <GoodsMark book={book} goods={thing.goods} />
                  {label(thing)}
                  {/* A vessel shows its fill (D-230): the water is in the canister,
                      and nowhere else in the hands. */}
                  {thing.content !== undefined && (
                    <div className="note">{fill(book, names, thing)}</div>
                  )}
                </td>
                <td className="num">{tally(thing.goods, thing.amount)}</td>
                <td className="note">{tells(thing, names)}</td>
                <td className="handle">
                  <button
                    className="bare dots"
                    aria-label={t("ui-inventory-menu", {
                      goods: goodsName(names, thing.goods),
                    })}
                    aria-expanded={asking?.item === thing.id}
                    onClick={() =>
                      setAsking(
                        asking?.item === thing.id
                          ? null
                          : { item: thing.id, about: "menu" },
                      )
                    }
                  >
                    ⋯
                  </button>
                  {asking?.item === thing.id && (
                    //: The menu lives inside a draggable row: without `noDrag`
                    //: a swipe in the amount field -- or a sloppy click on a
                    //: verb -- starts a row drag instead.
                    <div className="menu" role="menu" {...noDrag}>
                      {asking.about === "menu" && (
                        <>
                          {/* How much of the stack the verbs below move: the
                              question lives with the verbs now, not as a
                              field in every row (D-238, the mockup's menu). */}
                          {thing.amount > 1 && (
                            <div className="menu-amount">
                              <span className="note">{t("ui-inventory-amount")}</span>
                              <Amount
                                goods={thing.goods}
                                value={parts[thing.id] ?? null}
                                max={thing.amount}
                                onChange={(value) =>
                                  setParts((was) => ({ ...was, [thing.id]: value }))
                                }
                              />
                            </div>
                          )}
                          {thing.slot && (
                            <button
                              role="menuitem"
                              onClick={() => send("gear.equip", { item: thing.id })}
                              disabled={busy}
                            >
                              {t("ui-inventory-equip")}
                            </button>
                          )}
                          {thing.food && (
                            <button
                              role="menuitem"
                              onClick={() => send("food.eat", { item: thing.id })}
                              disabled={busy}
                            >
                              {t("ui-inventory-eat")}
                            </button>
                          )}
                          {/* The warmer (D-231): a one-off handful of hours.
                              Shown by class rather than by name -- a second
                              warmer is data, like everything else. */}
                          {classOf(book, thing.goods) === "warmer" && (
                            <button
                              role="menuitem"
                              onClick={() => send("frost.warm", { item: thing.id })}
                              disabled={busy}
                              title={t("ui-inventory-warm-hint")}
                            >
                              {t("ui-inventory-warm")}
                            </button>
                          )}
                          {/* A knowledge carrier (D-209): read it into the identity --
                              the carrier stays -- or wipe it back into a blank. */}
                          {thing.recipe && (
                            <>
                              <button
                                role="menuitem"
                                onClick={() => send("carrier.read", { item: thing.id })}
                                disabled={busy || look.knows.includes(thing.recipe)}
                                title={
                                  look.knows.includes(thing.recipe)
                                    ? t("ui-inventory-copy-known")
                                    : t("ui-inventory-copy-hint")
                                }
                              >
                                {t("ui-inventory-copy")}
                              </button>
                              <button
                                role="menuitem"
                                onClick={() => send("carrier.wipe", { item: thing.id })}
                                disabled={busy}
                                title={t("ui-inventory-wipe-hint")}
                              >
                                {t("ui-inventory-wipe")}
                              </button>
                            </>
                          )}
                          {/* Putting up is not putting down (D-278): a machine
                              dropped on the floor is cargo; put up it takes a
                              place and works. Two moves, two buttons -- and the
                              two drop zones of the place say the same. */}
                          {standable && isGear(book, thing.goods) && (
                            <button
                              role="menuitem"
                              onClick={() => send("station.place", { item: thing.id })}
                              disabled={busy}
                              title={t("ui-inventory-install-hint")}
                            >
                              {t("ui-inventory-install")}
                            </button>
                          )}
                          <button
                            role="menuitem"
                            onClick={() => setAsking({ item: thing.id, about: "where" })}
                            disabled={busy}
                          >
                            {t("ui-inventory-put")}
                          </button>
                          <button
                            role="menuitem"
                            onClick={() => setAsking({ item: thing.id, about: "whom" })}
                            disabled={busy}
                          >
                            {t("ui-inventory-hand")}
                          </button>
                        </>
                      )}

                      {asking.about === "where" && (
                        <>
                          {/* The typed amount stays visible while choosing
                              where: the field is a step behind by now. */}
                          <p className="menu-ask">
                            {t("ui-inventory-where", {
                              amount: tally(thing.goods, part(thing)),
                            })}
                          </p>
                          {/* Two surfaces since D-244, and the menu names both:
                              the floor of the house and the ground beside it.
                              One button saying «На землю» while the engine put
                              the thing under the roof was a lie the menu told
                              on every built-up plot -- and the yard had no
                              keyboard path to it at all. */}
                          {mayDropHere ? (
                            <>
                              {roofed && (
                                <button
                                  role="menuitem"
                                  onClick={() =>
                                    send("ground.drop", {
                                      item: thing.id,
                                      amount: part(thing),
                                      indoors: true,
                                    })
                                  }
                                  disabled={busy}
                                >
                                  {t("ui-inventory-floor")}
                                </button>
                              )}
                              {openGround && (
                                <button
                                  role="menuitem"
                                  onClick={() =>
                                    send("ground.drop", {
                                      item: thing.id,
                                      amount: part(thing),
                                      indoors: false,
                                    })
                                  }
                                  disabled={busy}
                                >
                                  {t("ui-inventory-ground")}
                                </button>
                              )}
                            </>
                          ) : (
                            <p className="note">{t("ui-inventory-passing")}</p>
                          )}
                          {isVessel(book, thing.goods) &&
                            [
                              ...tanks.map((tank) => ({
                                id: tank.id,
                                goods: goodsName(names, tank.goods),
                              })),
                              ...things
                                .filter((other) => other.id !== thing.id && isVessel(book, other.goods))
                                .map((other) => ({
                                  id: other.id,
                                  goods: t("ui-inventory-in-hands", {
                                    goods: goodsName(names, other.goods),
                                  }),
                                })),
                            ].map((target) => (
                              <button
                                key={`pour:${target.id}`}
                                role="menuitem"
                                onClick={() =>
                                  send("liquid.pour", { from: thing.id, to: target.id })
                                }
                                disabled={busy || (thing.content ?? []).length === 0}
                                title={t("ui-inventory-pour-hint")}
                              >
                                {t("ui-inventory-pour", { target: target.goods.toLowerCase() })}
                              </button>
                            ))}
                          {boxes.map((chest) => (
                            <button
                              key={chest.id}
                              role="menuitem"
                              onClick={() =>
                                send("storage.put", {
                                  storage: chest.id,
                                  item: thing.id,
                                  amount: part(thing),
                                })
                              }
                              disabled={busy}
                            >
                              {t("ui-inventory-into", {
                                chest: goodsName(names, chest.goods).toLowerCase(),
                              })}
                            </button>
                          ))}
                          {/* Into the library, for good (D-068, D-209): only a written
                              carrier, only standing in one, and the name stays with it. */}
                          {thing.recipe && look.node?.shelf && (
                            <button
                              role="menuitem"
                              onClick={() => send("library.contribute", { item: thing.id })}
                              disabled={
                                busy ||
                                look.node.shelf.some((e) => e.recipe === thing.recipe)
                              }
                              title={
                                look.node.shelf.some((e) => e.recipe === thing.recipe)
                                  ? t("ui-inventory-contribute-there")
                                  : t("ui-inventory-contribute-hint")
                              }
                            >
                              {t("ui-inventory-contribute")}
                            </button>
                          )}
                          {look.convoy && (
                            <button
                              role="menuitem"
                              onClick={() =>
                                send("transport.load", {
                                  item: thing.id,
                                  amount: part(thing),
                                })
                              }
                              disabled={busy}
                            >
                              {t("ui-inventory-hold")}
                            </button>
                          )}
                          {/* Onto the counter, where a terminal stands (D-047):
                              only what lies in it is sold. The market panel
                              takes the same stack by drag, and this is the
                              path a keyboard and a finger have. */}
                          {atTerminal && (
                            <button
                              role="menuitem"
                              onClick={() =>
                                send("market.load", {
                                  goods: thing.key ?? thing.goods,
                                  tier: thing.tier,
                                  amount: part(thing),
                                })
                              }
                              disabled={busy}
                              title={t("ui-inventory-terminal-hint")}
                            >
                              {t("ui-inventory-terminal")}
                            </button>
                          )}
                          <button className="quiet" onClick={close}>
                            {t("ui-inventory-cancel")}
                          </button>
                        </>
                      )}

                      {asking.about === "whom" && (
                        <>
                          <p className="menu-ask">
                            {t("ui-inventory-whom", {
                              amount: tally(thing.goods, part(thing)),
                            })}
                          </p>
                          {people.length === 0 ? (
                            <p className="note">{t("ui-inventory-nobody")}</p>
                          ) : (
                            people.map((who) => (
                              <button
                                key={who.body}
                                role="menuitem"
                                onClick={() =>
                                  send("item.hand", {
                                    item: thing.id,
                                    to: who.body,
                                    amount: part(thing),
                                  })
                                }
                                disabled={busy}
                              >
                                {who.name}
                              </button>
                            ))
                          )}
                          <button className="quiet" onClick={close}>
                            {t("ui-inventory-cancel")}
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </td>
              </tr>
              )),
            ])}
          </tbody>
        </table>
      )}
      </DropZone>

      {look.stall && look.stall.length > 0 && (
        <>
          <h3>{t("ui-inventory-on-terminal")}</h3>
          <table className="goods">
            <tbody>
              {look.stall.map((thing) => (
                <tr key={thing.id}>
                  <td>
                    <GoodsMark book={book} goods={thing.goods} />
                    {label(thing)}
                  </td>
                  <td className="num">{tally(thing.goods, thing.amount)}</td>
                  <td className="note">{tells(thing, names)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/**
 * The table split into sections by the chosen axis, each sorted the chosen way.
 * One section without a title when there is no grouping.
 */
function sections(
  things: Thing[],
  group: Grouping,
  sort: Sorting,
  desc: boolean,
  book: RecipeBook | null,
  names: Names | null,
): { title: string | null; rows: Thing[]; summary: Summary }[] {
  const ordered = arrange(things, sort, desc, names);
  if (group === "none") return [{ title: null, rows: ordered, summary: summarize([]) }];
  const buckets = new Map<string, Thing[]>();
  for (const thing of ordered) {
    const key = groupKey(book, names, thing, group);
    buckets.set(key, [...(buckets.get(key) ?? []), thing]);
  }
  return orderGroups([...buckets.keys()], group, things, names).map((title) => {
    const rows = buckets.get(title) ?? [];
    return { title, rows, summary: summarize(rows) };
  });
}

/**
 * What a folded group says about itself: how much, how good, of how many
 * stacks and how heavy.
 *
 * The count of stacks stays because it is the one thing the fold hides: two
 * lots of ore at 12 and at 13 read as one line here, and the player must see
 * that the line covers two of them before deciding to open it.
 */
function sums(summary: Summary, stacks: number): string {
  const said: string[] = [];
  if (summary.goods != null) said.push(tally(summary.goods, summary.amount));
  if (summary.quality != null)
    said.push(t("ui-inventory-average", { quality: summary.quality.toFixed(0) }));
  said.push(positions(stacks));
  said.push(t("ui-inventory-mass", { mass: summary.mass.toFixed(1) }));
  return ` · ${said.join(" · ")}`;
}

/** "1 позиция", "2 позиции", "5 позиций" -- the count decides the word.
 *
 * The choosing is the message's, not this function's: which counts take which
 * word is a fact about a language, and a language that has one form for all of
 * them -- or six -- cannot be served by a rule written in `if`s here.
 *
 * The number goes twice: as a number, which is the only thing Fluent's plural
 * rules can look at, and as the digits to print. Printing `$count` itself would
 * hand it to the locale's number format, and a thousand stacks would read
 * "1 000 позиций" where every other figure in the row reads "1000".
 */
function positions(count: number): string {
  return t("ui-inventory-positions", { count, shown: String(count) });
}

/** The one line that says what kind of thing this is. */
function tells(thing: Thing, names: Names | null): string {
  const parts: string[] = [];
  if (thing.fineness != null) {
    parts.push(t("ui-inventory-fineness", { fineness: String(thing.fineness) }));
    if (thing.maker) parts.push(t("ui-inventory-maker", { maker: thing.maker }));
  } else if (thing.vigor != null) {
    parts.push(
      t("ui-inventory-vigor", {
        variety: varietyText(names, thing.variety) ?? t("ui-inventory-variety"),
        vigor: thing.vigor.toFixed(0),
      }),
    );
  } else if (thing.charge != null) {
    parts.push(t("ui-inventory-charge", { charge: thing.charge.toFixed(0) }));
  } else if (thing.quality != null) {
    parts.push(`${thing.quality.toFixed(0)} · ${tierName(names, thing.tier)}`);
  }
  if (thing.condition < 100)
    parts.push(t("ui-inventory-condition", { condition: thing.condition.toFixed(0) }));
  if (thing.spoils_at) parts.push(spoilAt(thing.spoils_at));
  return parts.join(" · ");
}

function spoilAt(when: string): string {
  const hours = (new Date(when).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return t("ui-inventory-spoiled");
  if (hours < 24) return t("ui-inventory-spoils", { hours: String(Math.round(hours)) });
  return t("ui-inventory-keeps", { days: String(Math.round(hours / 24)) });
}
