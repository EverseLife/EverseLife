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
import type { Look, Thing } from "../api";
import { Refusal, useActions, useBook, useSession } from "../actions";
import { Rule } from "../Rule";
import { Amount } from "../Amount";
import { chosen, tally } from "../amounts";
import { classOf } from "../classes";
import { fill, isVessel } from "../liquids";
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

/** Somebody standing in the same node: the only possible receiver. */
type Person = { body: string; name: string };

/** Which sub-question the open menu is asking. */
type Asking = null | { item: string; about: "menu" | "where" | "whom" };

export function Inventory({ look }: Props) {
  const session = useSession();
  const book = useBook();
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

  //: Who is here is asked for only when somebody is about to hand something
  //: over: a list of names polled on every look would be a presence tracker.
  useEffect(() => {
    if (asking?.about !== "whom") return;
    void session
      .send("people.here")
      .then((answer) => setPeople((answer.people as Person[]) ?? []))
      .catch(() => setPeople([]));
  }, [session, asking?.about, look.node?.key]);

  const carried = look.carry;
  const chests = (look.storages ?? []).filter((chest) => chest.mine);
  //: Vessels take a pour, not a "put" (D-230): a canister goes into a chest
  //: like any thing, but what is in it goes into a tank by the hose.
  const tanks = chests.filter((chest) => isVessel(book, chest.goods));
  const boxes = chests.filter((chest) => !isVessel(book, chest.goods));
  const things = look.inventory;
  //: The engine lets a thing be put down anywhere but only picked up where the
  //: node allows it -- so on somebody else's land dropping is one-way, and the
  //: thing stays for its owner. The interface does not offer a door that only
  //: opens outward: where it cannot be taken back, it is not offered.
  const mayDropHere = Boolean(look.floor?.mine);

  const part = (thing: Thing) => chosen(parts[thing.id] ?? null, thing.amount);
  const close = () => setAsking(null);

  const send = (cmd: string, args: Record<string, unknown>) =>
    act(async () => {
      await session.send(cmd, args);
      close();
    });

  return (
    <div>
      {carried && (
        <p className="sign">
          в руках {carried.load.toFixed(1)} из {carried.capacity.toFixed(0)} кг
          <Rule>
            Смотреть можно откуда угодно, есть — из рук и в дороге тоже, а трогать
            остальное только ногами. Передают из рук в руки: оба человека стоят в
            одном месте, и передача видна остальным — в разговоре появляется строка о
            ней. Полные руки посылку не примут: предел носимого чужой тоже.
          </Rule>
        </p>
      )}

      {carried && carried.slots.length > 0 && (
        <div className="row">
          {carried.slots.map((slot) => {
            const worn = carried.equipped[slot];
            return (
              <span key={slot} className="note">
                {slot}:{" "}
                {worn ? (
                  <>
                    {worn.goods}{" "}
                    <button
                      className="link"
                      onClick={() => act(() => session.send("gear.unequip", { slot }))}
                      disabled={busy}
                    >
                      снять
                    </button>
                  </>
                ) : (
                  "пусто"
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
            title="сгруппировать"
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
            title="упорядочить"
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
            title={desc ? "по убыванию — нажмите для возрастания" : "по возрастанию — нажмите для убывания"}
          >
            {desc ? "↓" : "↑"}
          </button>
        </div>
      )}

      {things.length === 0 ? (
        <p className="note">В руках ничего нет.</p>
      ) : (
        <table className="goods">
          <tbody>
            {sections(things, group, sort, desc, book).flatMap(({ title, rows, summary }) => [
              ...(title === null
                ? []
                : [
                    <tr key={`group:${title}`} className="group">
                      <td colSpan={5}>
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
              <tr key={thing.id}>
                <td className="handle">
                  <button
                    className="bare dots"
                    aria-label={`что можно с «${thing.goods}»`}
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
                    <div className="menu" role="menu">
                      {asking.about === "menu" && (
                        <>
                          {thing.slot && (
                            <button
                              role="menuitem"
                              onClick={() => send("gear.equip", { item: thing.id })}
                              disabled={busy}
                            >
                              Надеть
                            </button>
                          )}
                          {thing.food && (
                            <button
                              role="menuitem"
                              onClick={() => send("food.eat", { item: thing.id })}
                              disabled={busy}
                            >
                              Съесть
                            </button>
                          )}
                          {/* The warmer (D-231): a one-off handful of hours.
                              Shown by class rather than by name -- a second
                              warmer is data, like everything else. */}
                          {classOf(book, thing.goods) === "Грелка" && (
                            <button
                              role="menuitem"
                              onClick={() => send("frost.warm", { item: thing.id })}
                              disabled={busy}
                              title="сломать грелку: часы теплозапаса сразу, сверх потолка не копятся"
                            >
                              Согреться
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
                                    ? "этот рецепт уже в личности"
                                    : "скопировать рецепт в знания: стоит выносливости, носитель цел"
                                }
                              >
                                Скопировать в знания
                              </button>
                              <button
                                role="menuitem"
                                onClick={() => send("carrier.wipe", { item: thing.id })}
                                disabled={busy}
                                title="стереть запись: останется болванка"
                              >
                                Стереть
                              </button>
                            </>
                          )}
                          <button
                            role="menuitem"
                            onClick={() => setAsking({ item: thing.id, about: "where" })}
                            disabled={busy}
                          >
                            Положить…
                          </button>
                          <button
                            role="menuitem"
                            onClick={() => setAsking({ item: thing.id, about: "whom" })}
                            disabled={busy}
                          >
                            Передать…
                          </button>
                        </>
                      )}

                      {asking.about === "where" && (
                        <>
                          <p className="menu-ask">Куда положить</p>
                          {mayDropHere ? (
                            <button
                              role="menuitem"
                              onClick={() =>
                                send("ground.drop", {
                                  item: thing.id,
                                  amount: part(thing),
                                })
                              }
                              disabled={busy}
                            >
                              На землю
                            </button>
                          ) : (
                            <p className="note">
                              Земля чужая: положенное здесь достанется хозяину,
                              и обратно вы его не возьмёте.
                            </p>
                          )}
                          {isVessel(book, thing.goods) &&
                            [
                              ...tanks.map((tank) => ({ id: tank.id, goods: tank.goods })),
                              ...things
                                .filter((other) => other.id !== thing.id && isVessel(book, other.goods))
                                .map((other) => ({ id: other.id, goods: `${other.goods} в руках` })),
                            ].map((target) => (
                              <button
                                key={`pour:${target.id}`}
                                role="menuitem"
                                onClick={() =>
                                  send("liquid.pour", { from: thing.id, to: target.id })
                                }
                                disabled={busy || (thing.content ?? []).length === 0}
                                title="перелить всё, что внутри, сколько войдёт"
                              >
                                Перелить в {target.goods.toLowerCase()}
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
                              В {chest.goods.toLowerCase()}
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
                                  ? "этот рецепт здесь уже лежит"
                                  : "отдать в библиотеку навсегда: ваше имя останется при рецепте"
                              }
                            >
                              В библиотеку
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
                              В трюм
                            </button>
                          )}
                          <button className="quiet" onClick={close}>
                            Отмена
                          </button>
                        </>
                      )}

                      {asking.about === "whom" && (
                        <>
                          <p className="menu-ask">Кому передать</p>
                          {people.length === 0 ? (
                            <p className="note">
                              Здесь никого больше нет: передают из рук в руки.
                            </p>
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
                            Отмена
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </td>
                <td>
                  {thing.flavor ?? (thing.recipe ? `${thing.goods}: ${thing.recipe}` : thing.goods)}
                  {/* A vessel shows its fill (D-230): the water is in the canister,
                      and nowhere else in the hands. */}
                  {thing.content !== undefined && (
                    <div className="note">{fill(book, thing)}</div>
                  )}
                </td>
                <td className="num">{tally(thing.goods, thing.amount)}</td>
                <td className="note">{tells(thing)}</td>
                <td>
                  <Amount
                    goods={thing.goods}
                    value={parts[thing.id] ?? null}
                    max={thing.amount}
                    onChange={(value) =>
                      setParts((was) => ({ ...was, [thing.id]: value }))
                    }
                  />
                </td>
              </tr>
              )),
            ])}
          </tbody>
        </table>
      )}

      {look.stall && look.stall.length > 0 && (
        <>
          <h3>В терминале</h3>
          <table className="goods">
            <tbody>
              {look.stall.map((thing) => (
                <tr key={thing.id}>
                  <td />
                  <td>{thing.flavor ?? (thing.recipe ? `${thing.goods}: ${thing.recipe}` : thing.goods)}</td>
                  <td className="num">{tally(thing.goods, thing.amount)}</td>
                  <td className="note">{tells(thing)}</td>
                  <td />
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
): { title: string | null; rows: Thing[]; summary: Summary }[] {
  const ordered = arrange(things, sort, desc);
  if (group === "none") return [{ title: null, rows: ordered, summary: summarize([]) }];
  const buckets = new Map<string, Thing[]>();
  for (const thing of ordered) {
    const key = groupKey(book, thing, group);
    buckets.set(key, [...(buckets.get(key) ?? []), thing]);
  }
  return orderGroups([...buckets.keys()], group, things).map((title) => {
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
  if (summary.quality != null) said.push(`в среднем ${summary.quality.toFixed(0)}`);
  said.push(positions(stacks));
  said.push(`${summary.mass.toFixed(1)} кг`);
  return ` · ${said.join(" · ")}`;
}

/** "1 позиция", "2 позиции", "5 позиций" -- the count decides the word. */
function positions(count: number): string {
  const last = count % 10;
  const pair = count % 100;
  if (pair >= 11 && pair <= 14) return `${count} позиций`;
  if (last === 1) return `${count} позиция`;
  if (last >= 2 && last <= 4) return `${count} позиции`;
  return `${count} позиций`;
}

/** The one line that says what kind of thing this is. */
function tells(thing: Thing): string {
  const parts: string[] = [];
  if (thing.fineness != null) {
    parts.push(`проба ${thing.fineness}`);
    if (thing.maker) parts.push(`клеймо ${thing.maker}`);
  } else if (thing.vigor != null) {
    parts.push(`${thing.variety ?? "сорт"} · сила ${thing.vigor.toFixed(0)}`);
  } else if (thing.charge != null) {
    parts.push(`заряд ${thing.charge.toFixed(0)}`);
  } else if (thing.quality != null) {
    parts.push(`${thing.quality.toFixed(0)} · ${thing.tier}`);
  }
  if (thing.condition < 100) parts.push(`сост. ${thing.condition.toFixed(0)}`);
  if (thing.spoils_at) parts.push(spoilAt(thing.spoils_at));
  return parts.join(" · ");
}

function spoilAt(when: string): string {
  const hours = (new Date(when).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return "испортилось";
  if (hours < 24) return `испортится через ${Math.round(hours)} ч`;
  return `годно ${Math.round(hours / 24)} сут.`;
}
