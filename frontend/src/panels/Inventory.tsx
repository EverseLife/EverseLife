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
import type { Look, Session, Thing } from "../api";
import { Refusal, useActions } from "../actions";
import { Rule } from "../Rule";
import { Amount } from "../Amount";
import { chosen } from "../amounts";

type Props = { look: Look; session: Session };

/** Somebody standing in the same node: the only possible receiver. */
type Person = { body: string; name: string };

/** Which sub-question the open menu is asking. */
type Asking = null | { item: string; about: "menu" | "where" | "whom" };

export function Inventory({ look, session }: Props) {
  const acting = useActions();
  const { busy, act } = acting;
  const [asking, setAsking] = useState<Asking>(null);
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

      {things.length === 0 ? (
        <p className="note">В руках ничего нет.</p>
      ) : (
        <table className="goods">
          <tbody>
            {things.map((thing) => (
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
                          {chests.map((chest) => (
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
                <td>{thing.flavor ?? thing.goods}</td>
                <td className="num">{thing.amount}</td>
                <td className="note">{tells(thing)}</td>
                <td>
                  <Amount
                    value={parts[thing.id] ?? null}
                    max={thing.amount}
                    onChange={(value) =>
                      setParts((was) => ({ ...was, [thing.id]: value }))
                    }
                  />
                </td>
              </tr>
            ))}
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
                  <td>{thing.flavor ?? thing.goods}</td>
                  <td className="num">{thing.amount}</td>
                  <td className="note">{tells(thing)}</td>
                  <td />
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <Rule>
        Смотреть можно откуда угодно, есть — из рук и в дороге тоже, а трогать
        остальное только ногами. Передают из рук в руки: оба человека стоят в
        одном месте, и передача видна остальным — в разговоре появляется строка
        о ней. Полные руки посылку не примут: предел носимого чужой тоже.
      </Rule>
    </div>
  );
}

/** The one line that says what kind of thing this is. */
function tells(thing: Thing): string {
  const parts: string[] = [];
  if (thing.fineness !== null) {
    parts.push(`проба ${thing.fineness}`);
    if (thing.maker) parts.push(`клеймо ${thing.maker}`);
  } else if (thing.vigor !== null) {
    parts.push(`${thing.variety ?? "сорт"} · сила ${thing.vigor.toFixed(0)}`);
  } else if (thing.charge !== null) {
    parts.push(`заряд ${thing.charge.toFixed(0)}`);
  } else if (thing.quality !== null) {
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
