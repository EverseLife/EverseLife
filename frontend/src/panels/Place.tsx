/**
 * The plot, the building and its furnishings (D-089, D-106, D-116, D-150).
 *
 * The windows are separated by meaning, not piled into one:
 *
 * - **Plot** -- only for an unowned node: a civic one is bought at a price by
 *   distance to the bioprinter (D-089), a wild one is taken. Buying issues a
 *   deed -- it is visible in the sidebar, in the "holdings" tab (D-116);
 * - **Building** -- for your own plot: build first, then furnish. Machines
 *   take area (`build.slots_per_area` m2 per place), so the house's area is capacity;
 * - **Machines** -- one works at them; the owner places and removes (D-150);
 * - **Furniture** -- a bed and a shelf: nobody works at them, they furnish the household.
 */

import { useState } from "react";
import * as api from "../api";
import type { Bench, Look, Session, Vehicle } from "../api";
import { Amount } from "../Amount";
import { chosen } from "../amounts";
import { when } from "../clock";
import { Farm } from "./Farm";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  book: any;
};

export function Place({ look, session, book }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const mine = Boolean(look.node?.mine);
  const wild = Boolean(look.node?.wild);
  const unowned = !look.node?.owner;
  //: The authority disposes of **civic** land, not any: a bought plot stands
  //: on city territory, but its owner is a person, and the engine refuses the
  //: authority just like a passer-by (`station.may_build`).
  const hasPower = Boolean(
    look.node?.city && !look.node?.owner && look.city?.powers.includes("laws"),
  );
  const price = look.node?.price ?? null;

  //: An empty node: the "Plot" window with purchase or taking. As soon as an
  //: owner appears -- or if the node is not for sale (city buildings, a vein)
  //: -- this window is gone: from then on "Building", "Machines", "Furniture" live.
  if (unowned && (wild || price !== null)) {
    return (
      <>
        <Refusal of={acting} />
        {/* Обоз стоит где угодно: у ничьего узла тоже грузят и распрягают. */}
        <Convoy look={look} session={session} busy={busy} act={act} />
        {/* Лес ничейного узла рубит любой пришедший (D-177). */}
        <Gather look={look} session={session} busy={busy} act={act} book={book} />
        {/* На ничьей земле лежащее свободно: брошенное в поле — находка (D-192). */}
        <Floor look={look} session={session} busy={busy} act={act} />
        {/* Строить за городом может всякий, хоть земля и остаётся ничьей
            (D-198): дом принадлежит построившему, земля под ним — никому. */}
        {wild && <House look={look} session={session} busy={busy} act={act} />}
        {/* Поле на ничьей земле тоже открыто: кто вспахал, тот и хозяйничает. */}
        {wild && look.node?.fertility ? (
          <Farm look={look} session={session} />
        ) : null}
        <section>
        <h2>Участок</h2>
        <p className="note">
          {look.node?.name} · {look.node?.area.toFixed(0)} м² · ничей
        </p>
        {wild ? (
          <p className="note">
            Земля за городом ничья и таковой остаётся: бумагу на владение
            выдаёт город, а здесь его нет. Работать и строить тут
            может всякий — поставленное принадлежит поставившему.
          </p>
        ) : price !== null ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.buy"))} disabled={busy}>
              Выкупить за {api.tk(price)} ₭
            </button>
            <span className="note">
              Цена от удалённости до биопринтера: деньги в казну,
              вам — бумага на землю.
            </span>
          </div>
        ) : (
          <p className="note">
            Городская земля, но цена не назначена: код-закон `land_price` пуст —
            город пока не продаёт.
          </p>
        )}
        </section>
      </>
    );
  }

  return (
    <>
      <Plot look={look} session={session} busy={busy} act={act} />
      {(mine || wild || hasPower) && (
        <House look={look} session={session} busy={busy} act={act} />
      )}
      {/* Земледелие стоит рядом с домом, а не только во вкладке: где владеешь
          землёй — там ею и распоряжаешься. Вне города поле открыто всякому
          (D-198), и окно там тоже к месту. */}
      {(mine || wild) && look.node?.fertility ? (
        <Farm look={look} session={session} />
      ) : null}
      <Gather look={look} session={session} busy={busy} act={act} book={book} />
      <Foundation look={look} session={session} busy={busy} act={act} />
      <Citizenship look={look} session={session} busy={busy} act={act} />
      <Convoy look={look} session={session} busy={busy} act={act} />
      <Equipment
        title="Станки"
        things={look.bench ?? []}
        kind="station"
        look={look}
        session={session}
        busy={busy}
        act={act}
        book={book}
        note="За станком работает один: пока идёт партия, второму он не отдаётся."
      />
      <Equipment
        title="Мебель"
        things={look.furniture ?? []}
        kind="furniture"
        look={look}
        session={session}
        busy={busy}
        act={act}
        book={book}
        note="Мебель обустраивает быт: кровать — сон быстрее, сундук — хранение. На ней не работают."
      />
      <Storages look={look} session={session} busy={busy} act={act} />
      <Floor look={look} session={session} busy={busy} act={act} />
    </>
  );
}

/** The floor of the place: what lies here and how much room is left (D-192).
 *
 * Putting a thing down is the first thing a person back from the mine does, and
 * until now there was nowhere to put it: only a chest, a hold or the market.
 * Cargo takes area, area is finite, and a chest saves it -- hence three honest
 * answers to "where do I keep this": build more, buy chests, haul away.
 */
function Floor({ look, session, busy, act }: Omit<Props, "book">) {
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const floor = look.floor;
  if (!floor) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  const room = floor.space;
  const inHands = look.inventory;
  const roofed = room.roofed > 0;

  return (
    <section>
      <h2>{roofed ? "В здании" : "На земле"}</h2>
      <p className="note">
        занято {room.used.toFixed(1)} из {room.area.toFixed(0)} м²
        {room.cargo_mass > 0 && ` · груза ${room.cargo_mass.toFixed(1)} кг`}
        {room.slots_used > 0 && ` · оборудования ${room.slots_used}`}
      </p>

      {floor.things.length > 0 ? (
        <table>
          <tbody>
            {floor.things.map((thing) => (
              <tr key={thing.id}>
                <td>{thing.flavor ?? thing.goods}</td>
                <td className="note">
                  {thing.amount.toFixed(1)} ·{" "}
                  {(thing.mass * thing.amount).toFixed(1)} кг
                </td>
                <td>
                  {floor.mine && (
                    <Amount
                      value={parts[thing.id] ?? null}
                      max={thing.amount}
                      onChange={(value) => setPart(thing.id, value)}
                    />
                  )}
                </td>
                <td>
                  {floor.mine && (
                    <button
                      className="quiet"
                      onClick={() =>
                        act(() =>
                          session.send("ground.pick", {
                            item: thing.id,
                            amount: chosen(parts[thing.id] ?? null, thing.amount),
                          }),
                        )
                      }
                      disabled={busy}
                      title="поднять в руки — сколько унесёте"
                    >
                      Поднять
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="note">пусто</p>
      )}

      {inHands.length > 0 && (
        <table>
          <tbody>
            {inHands.map((thing) => (
              <tr key={thing.id}>
                <td>{thing.goods}</td>
                <td className="note">
                  {thing.amount.toFixed(1)} ·{" "}
                  {(thing.mass * thing.amount).toFixed(1)} кг
                </td>
                <td>
                  <Amount
                    value={parts[thing.id] ?? null}
                    max={thing.amount}
                    onChange={(value) => setPart(thing.id, value)}
                  />
                </td>
                <td>
                  <button
                    className="quiet"
                    onClick={() =>
                      act(() =>
                        session.send("ground.drop", {
                          item: thing.id,
                          amount: chosen(parts[thing.id] ?? null, thing.amount),
                        }),
                      )
                    }
                    disabled={busy}
                  >
                    Положить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="note">
        {floor.mine
          ? "Лежащее занимает площадь; в сундуке — не занимает."
          : "Чужое место: смотреть можно, брать — нет."}
      </p>
    </section>
  );
}

/** Node storages: a chest, a shelf and everything with capacity in the vault.
 *
 * The chest itself is visible to anyone -- it stands in the room. Whoever
 * disposes of the node may open it: the owner, and on civic land the
 * authority (D-181). The limit is the same as for hands and hold -- kilograms.
 */
function Storages({ look, session, busy, act }: Omit<Props, "book">) {
  //: How much of a stack to move, per item. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const chests = look.storages ?? [];
  if (chests.length === 0) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  //: Everything in the hands makes sense to put away: nothing is weightless in this world.
  const inHands = look.inventory;

  return (
    <>
      {chests.map((chest) => (
        <section key={chest.id}>
          <h2>{chest.goods}</h2>
          <p className="note">
            занято {chest.mass.toFixed(1)} из {chest.capacity.toFixed(0)} кг
          </p>
          {!chest.mine ? (
            <p className="note">Чужое хранилище: что внутри — не ваше дело.</p>
          ) : (
            <>
              {chest.content.length > 0 && (
                <table>
                  <tbody>
                    {chest.content.map((thing) => (
                      <tr key={thing.id}>
                        <td>{thing.goods}</td>
                        <td className="note">{thing.amount.toFixed(1)}</td>
                        <td>
                          <Amount
                            value={parts[thing.id] ?? null}
                            max={thing.amount}
                            onChange={(value) => setPart(thing.id, value)}
                          />
                        </td>
                        <td>
                          <button
                            className="quiet"
                            onClick={() =>
                              act(() =>
                                session.send("storage.take", {
                                  storage: chest.id,
                                  item: thing.id,
                                  amount: chosen(parts[thing.id] ?? null, thing.amount),
                                }),
                              )
                            }
                            disabled={busy}
                            title="забрать в руки — сколько унесёте"
                          >
                            Забрать
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {chest.content.length === 0 && <p className="note">пусто</p>}
              {inHands.length > 0 && (
                <table>
                  <tbody>
                    {inHands.map((thing) => (
                      <tr key={thing.id}>
                        <td>{thing.goods}</td>
                        <td className="note">
                          {thing.amount.toFixed(1)} ·{" "}
                          {(thing.mass * thing.amount).toFixed(1)} кг
                        </td>
                        <td>
                          <Amount
                            value={parts[thing.id] ?? null}
                            max={thing.amount}
                            onChange={(value) => setPart(thing.id, value)}
                          />
                        </td>
                        <td>
                          <button
                            className="quiet"
                            onClick={() =>
                              act(() =>
                                session.send("storage.put", {
                                  storage: chest.id,
                                  item: thing.id,
                                  amount: chosen(parts[thing.id] ?? null, thing.amount),
                                }),
                              )
                            }
                            disabled={busy}
                          >
                            Положить
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <Rule>                Дом хранит то, что не увезти в руках; полный сундук не уносят.
              </Rule>
            </>
          )}
        </section>
      ))}
    </>
  );
}

/** An occupied plot: whose it is and what it is called (D-178).
 *
 * Ownership is a public fact: whoever enters sees the owner, whoever it is, a
 * person or a city. The name is given by whoever disposes of the land -- and
 * the map label changes, not the node key: deeds and edges reference the key.
 */
function Plot({ look, session, busy, act }: Omit<Props, "book">) {
  const node = look.node;
  const [name, setName] = useState("");
  if (!node || (!node.owner && !node.owner_city)) return null;

  const whose = node.mine
    ? "ваш участок"
    : node.owner
      ? `хозяин ${node.owner}`
      : `земля города ${node.owner_city}`;

  return (
    <section>
      <h2>Участок</h2>
      <p className="note">
        {node.name} · {node.area.toFixed(0)} м² · {whose}
        {node.gated && " · двор заперт"}
        {node.cut_off && " · отключён за неуплату"}
      </p>
      {node.may_name && (
        <div className="row">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={node.name}
            //: Repeats `runtime.LAND_NAME_LIMIT`: better to show the limit by
            //: the input field than to report it as a refusal after a click.
            maxLength={40}
            title="как называть это место"
          />
          <button
            onClick={() =>
              act(async () => {
                await session.send("land.rename", { name: name });
                setName("");
              })
            }
            disabled={busy || !name.trim() || name.trim() === node.name}
          >
            Переименовать
          </button>
          <span className="note">
            Имя увидят все на карте; ключ участка не меняется.
          </span>
        </div>
      )}
      {node.mine && <Gate look={look} session={session} busy={busy} act={act} />}
    </section>
  );
}

/** The gate of one's own yard: open or shut, and one roster (D-199).
 *
 * The list is deliberately single: with the yard open the named do not get in,
 * with it shut only they do. Two lists would need rules for contradictions
 * between them -- and every such rule is one more thing to learn.
 */
function Gate({ look, session, busy, act }: Omit<Props, "book">) {
  const node = look.node;
  const [who, setWho] = useState("");
  if (!node) return null;

  const roster = node.roster ?? [];
  const shut = Boolean(node.gated);

  return (
    <>
      <div className="row">
        <button
          onClick={() => act(() => session.send("gate.set", { closed: !shut }))}
          disabled={busy}
        >
          {shut ? "Открыть двор" : "Запереть двор"}
        </button>
        <span className="note">
          {shut
            ? "Заперт: входят только названные ниже. Хозяин — всегда."
            : "Открыт: входят все, кроме названных ниже."}
          {" Выйти можно всегда: захлопнуть двор при госте нельзя."}
        </span>
      </div>
      <div className="row">
        <input
          value={who}
          onChange={(e) => setWho(e.target.value)}
          placeholder="имя"
          title={shut ? "кого пускать" : "кого не пускать"}
        />
        <button
          onClick={() =>
            act(async () => {
              await session.send("gate.list", { who: who.trim() });
              setWho("");
            })
          }
          disabled={busy || !who.trim()}
        >
          {shut ? "Пускать" : "Не пускать"}
        </button>
        {roster.length > 0 && (
          <span className="note">
            {shut ? "Пускаем: " : "Не пускаем: "}
            {roster.map((name) => (
              <button
                key={name}
                onClick={() =>
                  act(() => session.send("gate.list", { who: name, strike: true }))
                }
                disabled={busy}
                title="убрать из списка"
              >
                {name} ✕
              </button>
            ))}
          </span>
        )}
      </div>
    </>
  );
}

/** The house: what stands, what is being raised, and what a new one costs.
 *
 * Storeys are the whole point of the window (D-125, D-145): the plot limits
 * the footprint, not the workshop -- a house grows upwards where the ground
 * does not grow sideways. The bill is shown **before** the work and against
 * what is in hand, so that "wood 12 of 30" is read at the plan and not
 * discovered at the click.
 */
function House({ look, session, busy, act }: Omit<Props, "book">) {
  const home = look.node?.building;
  const plot = look.node?.area ?? 0;
  const [area, setArea] = useState(20);
  const [floors, setFloors] = useState(1);
  const [bill, setBill] = useState<any>(null);
  if (!home) return null;

  const free = Math.max(0, plot - home.ground);
  const going = home.building ?? [];

  const count = async () => {
    setBill(await session.send("build.estimate", { area, floors, strength: 1 }));
  };

  const short = (bill?.materials ?? []).filter((m: any) => m.have < m.need);

  return (
    <section>
      <h2>Дом</h2>
      {home.area > 0 ? (
        <p>
          жилой площади <b>{home.area.toFixed(0)} м²</b> в{" "}
          <b>{home.floors}</b> эт. на {home.ground.toFixed(0)} м² земли · мест
          под оборудование{" "}
          <b>
            {home.used} из {home.slots}
          </b>
        </p>
      ) : (
        <p className="note">
          Дома нет — только двор. Станки и мебель ставят в дом: сначала строят.
        </p>
      )}

      {going.length > 0 && (
        <p className="note">
          Строится: {going.map((w) => `${w.area.toFixed(0)} м² в ${w.floors} эт.`).join(", ")}
          {" · готово "}
          {when(going[0].ready_at)}. Материалы уже в стене.
        </p>
      )}

      {/* Ничью землю за городом строит всякий пришедший (D-198): окно нужно и
          там, иначе правило есть, а руки к нему не приложить. */}
      {(look.node?.mine || look.node?.wild) && free > 0 && (
        <>
          <div className="row">
            <input
              type="number"
              min={1}
              max={Math.floor(free)}
              value={area}
              onChange={(e) => setArea(Number(e.target.value))}
              title="пятно застройки, м²"
            />
            <input
              type="number"
              min={1}
              value={floors}
              onChange={(e) => setFloors(Number(e.target.value))}
              title="этажей"
            />
            <button onClick={() => act(count)} disabled={busy || area <= 0}>
              Посчитать смету
            </button>
            <span className="note">
              {area} м² × {floors} эт. = {area * floors} м² жилой площади.
              Свободно {free.toFixed(0)} м² двора.
            </span>
          </div>

          {bill && (
            <>
              <table>
                <tbody>
                  {bill.materials.map((m: any) => (
                    <tr key={m.goods}>
                      <td>{m.goods}</td>
                      <td className={m.have < m.need ? "note" : undefined}>
                        {m.have.toFixed(1)} из {m.need.toFixed(1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row">
                <button
                  onClick={() =>
                    act(async () => {
                      await session.send("build.construct", {
                        area,
                        floors,
                        strength: 1,
                      });
                      setBill(null);
                    })
                  }
                  disabled={busy || short.length > 0 || area > free}
                >
                  Строить {area} м² в {floors} эт.
                </button>
                <span className="note">
                  {short.length > 0
                    ? `Не хватает: ${short.map((m: any) => m.goods).join(", ")}`
                    : `Работы на ${(bill.minutes / 60).toFixed(1)} ч; выше ${bill.max_floors} эт. дерево не держит.`}
                </span>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

/** Human-readable titles of place signs.
 *
 * The keys are the node properties themselves, and those come from the vault
 * in Russian -- they are game data, not identifiers. A key translated to
 * English silently stopped matching and the window showed the raw property.
 */
const PLACES: Record<string, string> = {
  лес: "Лес",
  камни: "Камни",
  луг: "Луг",
};

/** Place extraction (D-177): felling -- and future gathering -- without a machine.
 *
 * Shown where the node has a sign ("forest") and the land is own or unowned:
 * somebody else's forest belongs to its owner. The batch runs as ordinary
 * craft -- time and tool from the vault, the finished product is seen in "jobs".
 */
function Gather({ look, session, busy, act, book }: Props) {
  const [qty, setQty] = useState(10);
  const node = look.node;
  if (!node) return null;
  const available = node.mine || node.wild;
  const operations = (book?.operations ?? []).filter(
    (o: any) => o.place && (node.features ?? []).includes(o.place),
  );
  if (!available || operations.length === 0) return null;

  //: What satisfies the requirement: the item itself or any of the class ("Axe").
  const inHands = new Set(look.inventory.map((thing) => thing.goods));
  const hasMeans = (withWhat: string) =>
    inHands.has(withWhat) ||
    ((book?.tool_classes?.[withWhat] ?? []) as string[]).some((i) => inHands.has(i));

  //: One sign -- one window, even when several operations hang on it: in a
  //: forest you both fell trees and gather deadwood (D-196), and two sections
  //: titled "Лес" one under the other read as a bug.
  const bySign = new Map<string, any[]>();
  for (const operation of operations) {
    bySign.set(operation.place, [...(bySign.get(operation.place) ?? []), operation]);
  }

  return (
    <>
      {[...bySign].map(([sign, ways]) => (
        <section key={sign}>
          <h2>{PLACES[sign] ?? sign}</h2>
          <div className="row">
            <input
              type="number"
              min={1}
              value={qty}
              onChange={(e) => setQty(Number(e.target.value))}
              title="сколько добыть"
            />
            {ways.flatMap((operation: any) =>
              (operation.gives as string[]).map((exit) => {
                const needs = operation.requires as string[];
                const fits = needs.every(hasMeans);
                return (
                  <button
                    key={`${operation.name}:${exit}`}
                    onClick={() =>
                      act(() =>
                        session.send("craft.start", {
                          output: exit,
                          units: qty,
                          //: Wood comes both from felling and from deadwood --
                          //: the button says which one it is (D-196).
                          way: operation.name,
                        }),
                      )
                    }
                    disabled={busy || qty <= 0 || !fits}
                    title={
                      fits
                        ? needs.length > 0
                          ? `нужен ${needs.join(", ")}; готовое — в «делах»`
                          : "голыми руками, потому и дольше; готовое — в «делах»"
                        : `нужен: ${needs.join(", ")}`
                    }
                  >
                    {operation.name}: {exit}
                  </button>
                );
              }),
            )}
            <span className="note">
              Партия идёт временем, готовое забирается в «делах».
            </span>
          </div>
        </section>
      ))}
    </>
  );
}

/** Citizenship: one per person, entry by charter, exit with a delay (D-160).
 *
 * One joins in the administration -- where the city makes every decision
 * (D-155) -- so the window lives in the location, not the sidebar. The
 * admission order is always shown: "open", "by application" and "by
 * invitation" behave differently, and the person must understand what to expect.
 */
function Citizenship({ look, session, busy, act }: Omit<Props, "book">) {
  const city = look.city ?? null;
  const own = look.citizenship ?? null;
  //: Only in the administration: both joining and leaving are in-person (D-155).
  if (!city?.hall) return null;

  const order_: Record<string, string> = {
    open: "принимают свободно",
    application: "по заявке с одобрением",
    invite: "только по приглашению",
  };
  //: Citizenship taken as a print condition cannot be given up before the term (D-184).
  const linked = Boolean(
    own?.bound_until && new Date(own.bound_until) > new Date(),
  );

  return (
    <section>
      <h2>Гражданство</h2>
      {own ? (
        <p>
          состоите в <b>{own.city}</b>
          {own.leaving_at && <> · выходите: гражданство спадёт {when(own.leaving_at)}</>}
          {/* Обязательство, принятое при печати (D-184): срок виден заранее,
              а не открывается отказом при попытке выйти. */}
          {linked && <> · обязательство кончится {when(own.bound_until)}</>}
        </p>
      ) : (
        <p className="note">Вы нигде не состоите: гость платит пошлины, но не налоги.</p>
      )}

      {city?.hall && (
        <div className="row">
          {city.citizen ? (
            <span className="note">Это ваш город.</span>
          ) : city.requested ? (
            <span className="note">
              {city.admission === "invite"
                ? "Вас позвали: примите приглашение."
                : "Заявка подана — ждёт решения власти."}
            </span>
          ) : null}
          {!city.citizen && (
            <button
              onClick={() => act(() => session.send("city.join", {}))}
              disabled={busy || Boolean(own)}
              title={
                own
                  ? "гражданство одно на человека: сначала выйти из прежнего города"
                  : order_[city.admission]
              }
            >
              {city.requested && city.admission === "invite"
                ? "Принять приглашение"
                : "Вступить в граждане"}
            </button>
          )}
          <span className="note">{city.name}: {order_[city.admission]}</span>
        </div>
      )}

      {own && !own.leaving_at && (
        <div className="row">
          <button
            onClick={() => act(() => session.send("city.leave", {}))}
            disabled={busy || linked}
            title={
              linked
                ? "срок обязательства вы приняли, выбрав дверь этого города"
                : "заявление уходит по Сети"
            }
          >
            Выйти из гражданства
          </button>
          <span className="note">
            {linked
              ? "Обязательство печати держит до своего срока."
              : "Выход не мгновенен: гражданство спадёт по сроку."}
          </span>
        </div>
      )}
    </section>
  );
}

/** Founding a city: four buildings, not a coin (D-023, D-098, D-159).
 *
 * The window is shown only where founding is possible at all -- on your own
 * planet node outside a foreign city. The list of what is missing is visible
 * **before** the attempt: the entry threshold is buildings, and the person
 * must understand which ones exactly they lack.
 *
 * At founding the land goes to the city: from then on the authority hands it
 * out, not the yard owner (D-089), and that is said right here rather than found out later.
 */
function Foundation({ look, session, busy, act }: Omit<Props, "book">) {
  const ground = look.foundation ?? null;
  const [name, setName] = useState("");
  if (!ground) return null;

  const ready = ground.missing.length === 0;

  return (
    <section>
      <h2>Основание города</h2>
      <table>
        <tbody>
          {ground.needs.map((need) => (
            <tr key={need.role}>
              <td>{ground.missing.includes(need.role) ? "—" : "✓"}</td>
              <td>{need.role}</td>
              <td className="note">{need.any_of.join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="название города"
          disabled={!ready}
        />
        <button
          onClick={() => act(() => session.send("city.found", { name: name }))}
          disabled={busy || !ready || !name.trim()}
        >
          Основать город
        </button>
      </div>
      <p className="note">
        {ready
          ? "Земля отойдёт городу, основатель получит все полномочия."
          : "Порог входа — постройки, а не монета."}
      </p>
    </section>
  );
}

/** Convoy: what it is harnessed to, what it carries and what one can harness to here (D-157).
 *
 * Cargo rides **in the hold**, not in hands: that is the only way to carry
 * more than `inventory.carry_mass`. Moving from hands to hold and back is
 * in-person -- on the go the hold is closed.
 *
 * A separate window from machines on purpose: nobody stands at a wagon to
 * work, one harnesses to it, and these two actions must not be confused.
 */
function Convoy({ look, session, busy, act }: Omit<Props, "book">) {
  const convoy = look.convoy ?? null;
  const standing = (look.vehicles ?? []).filter((t) => !t.taken);
  //: How much of a stack to move, per item. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  if (!convoy && standing.length === 0) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));

  //: What in the hands has weight: no point loading the weightless, it rides anyway.
  const inHands = look.inventory.filter((thing) => thing.mass > 0);

  return (
    <section>
      <h2>Обоз</h2>
      {convoy ? (
        <>
          <p>
            впряжён: <b>{convoy.type_key}</b> · трюм{" "}
            <b>
              {convoy.mass.toFixed(1)} из {convoy.capacity.toFixed(0)} кг
            </b>{" "}
            · скорость ×{convoy.speed_k} · сост. {convoy.condition.toFixed(0)}
          </p>
          {convoy.cargo.length > 0 && (
            <table>
              <tbody>
                {convoy.cargo.map((thing) => (
                  <tr key={thing.id}>
                    <td>{thing.type_key}</td>
                    <td className="note">{thing.amount.toFixed(1)}</td>
                    <td>
                      <Amount
                        value={parts[thing.id] ?? null}
                        max={thing.amount}
                        onChange={(value) => setPart(thing.id, value)}
                      />
                    </td>
                    <td>
                      <button
                        className="quiet"
                        onClick={() =>
                          act(() =>
                            session.send("transport.unload", {
                              item: thing.id,
                              amount: chosen(parts[thing.id] ?? null, thing.amount),
                            }),
                          )
                        }
                        disabled={busy}
                        title="выгрузить в руки — сколько поместится"
                      >
                        Выгрузить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {inHands.length > 0 && (
            <table>
              <tbody>
                {inHands.map((thing) => (
                  <tr key={thing.id}>
                    <td>{thing.goods}</td>
                    <td className="note">
                      {thing.amount.toFixed(1)} ·{" "}
                      {(thing.mass * thing.amount).toFixed(1)} кг
                    </td>
                    <td>
                      <Amount
                        value={parts[thing.id] ?? null}
                        max={thing.amount}
                        onChange={(value) => setPart(thing.id, value)}
                      />
                    </td>
                    <td>
                      <button
                        className="quiet"
                        onClick={() =>
                          act(() =>
                            session.send("transport.load", {
                              item: thing.id,
                              amount: chosen(parts[thing.id] ?? null, thing.amount),
                            }),
                          )
                        }
                        disabled={busy}
                      >
                        Погрузить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row">
            <button
              onClick={() => act(() => session.send("transport.unharness"))}
              disabled={busy}
            >
              Распрячься
            </button>
            <span className="note">
              Обоз останется здесь с грузом; по бездорожью он не идёт.
            </span>
          </div>
        </>
      ) : (
        <div className="row">
          {standing.map((cart: Vehicle) => (
            <button
              key={cart.id}
              onClick={() =>
                act(() => session.send("transport.harness", { item: cart.id }))
              }
              disabled={busy}
              title={
                cart.capacity === null
                  ? "вольт не назвал грузоподъёмности"
                  : `${cart.capacity.toFixed(0)} кг · скорость ×${cart.speed_k}`
              }
            >
              Впрячься: {cart.goods}
            </button>
          ))}
          <span className="note">
            Груз едет в трюме, а не в руках.
          </span>
        </div>
      )}
    </section>
  );
}

/** The common equipment window: machines and furniture differ only by kind. */
function Equipment({
  title,
  things,
  kind,
  look,
  session,
  busy,
  act,
  book,
  note,
}: Props & {
  title: string;
  things: Bench[];
  kind: "station" | "furniture";
  note: string;
}) {
  const mine = Boolean(look.node?.mine);
  //: The owner places and removes, and on civic land the authority (`station.may_build`).
  //: In somebody else's house neither is entitled.
  const hasPower = Boolean(
    look.node?.city && !look.node?.owner && look.city?.powers.includes("laws"),
  );

  //: What from the hands can be placed here: the kind comes from vault data (D-090).
  const inHands = look.inventory.filter((thing) =>
    (book?.recipes ?? []).some(
      (r: any) => r.name === thing.goods && r.kind === kind,
    ),
  );

  //: The window is silent where there is nothing to say: no things in the node, none of yours in hand.
  if (things.length === 0 && !((mine || hasPower) && inHands.length > 0)) return null;

  const home = look.node?.building;
  const noRoom = home ? home.used >= home.slots : true;

  return (
    <section>
      <h2>{title}</h2>
      {things.length > 0 && (
        <table>
          <tbody>
            {things.map((thing) => (
              <tr key={thing.id}>
                <td>{thing.goods}</td>
                <td className="note">
                  {thing.quality === null ? "" : `качество ${thing.quality.toFixed(0)}`}
                  {thing.condition < 100 && ` · сост. ${thing.condition.toFixed(0)}`}
                </td>
                <td className="note">
                  {/* У аккумулятора состояние — это заряд, а не «занят»:
                      за ним не работают, он хранит энергию (D-179). */}
                  {thing.charge !== null
                    ? `заряд ${thing.charge.toFixed(0)} · заряжают в «хозяйстве»`
                    : kind === "station"
                      ? thing.busy
                        ? thing.mine
                          ? "занят вами"
                          : "занят"
                        : "свободен"
                      : ""}
                </td>
                <td>
                  {(mine || hasPower) && (
                    <button
                      className="quiet"
                      onClick={() =>
                        act(() => session.send("station.take", { item: thing.id }))
                      }
                      disabled={busy || thing.busy}
                      title="забрать в руки"
                    >
                      Забрать
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(mine || hasPower) && inHands.length > 0 && (
        <div className="row">
          {inHands.map((thing) => (
            <button
              key={thing.id}
              onClick={() => act(() => session.send("station.place", { item: thing.id }))}
              disabled={busy || noRoom}
              title={
                noRoom
                  ? "в здании нет места: стройте больше либо уносите лишнее"
                  : "поставить в здание"
              }
            >
              Поставить: {thing.goods}
            </button>
          ))}
          {noRoom && (
            <span className="note">в здании нет свободных мест</span>
          )}
        </div>
      )}
      <p className="note">{note}</p>
    </section>
  );
}
