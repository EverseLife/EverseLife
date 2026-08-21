/**
 * The location and everything on it (D-089, D-106, D-116, D-150, D-204, D-205).
 *
 * The windows are cut by intent, not by where the code happened to grow, and
 * each stands on its own in the location's row (`Stand.tsx`):
 *
 * - **Участок** -- everything about the land itself: whose it is and what it is
 *   called, the door and the two lists (D-204), buying an empty plot, founding
 *   a city (D-159). Shut stops entry, never passage, so a neighbour is never
 *   cut off from their home;
 * - **Дом** -- build, then furnish: the walls and their demolition (D-205), and
 *   the machines and furniture that go into the house and take its slots
 *   (D-106, D-150). Working at somebody's machine is another matter: the
 *   machine has a row of its own;
 * - **На земле** -- storage, for everyone: the floor where whoever got in puts
 *   things down and picks them up (D-192, D-204), and the chests standing in
 *   the room (D-181). The door and the chest are the protection, not a rule;
 * - **Обоз** -- the wagon: harnessing, and the hold that carries what hands
 *   cannot (D-157);
 * - **Лес / Камни / Луг** -- extraction by the sign of the land (D-177), one
 *   row per sign, next to the other work of the place.
 *
 * Citizenship lives in the administration window (`Admin.tsx`): one joins a
 * city where the city makes its decisions (D-155, D-160). The former "Место"
 * window -- seven unrelated sections under one name -- is gone.
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Bench, Look, Session, Vehicle } from "../api";
import { Amount } from "../Amount";
import { chosen, tally } from "../amounts";
import { when } from "../clock";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";
import { TierPick } from "../Tier";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  book: any;
};

/** Whether the viewer disposes of this node: the holder, or the authority on civic land.
 *
 * Repeats `station.may_build` on the client: the same three cases, and the
 * windows of the location are shown by them.
 */
export function disposes(look: Look): boolean {
  const node = look.node;
  if (!node) return false;
  if (node.owner) return Boolean(node.mine);
  //: Nobody's land outside a city: work on it is open to everyone (D-198).
  if (node.wild) return true;
  return Boolean(look.city?.powers.includes("laws"));
}

/** Everything stored at the place: the floor and the chests, one window (D-181, D-192).
 *
 * The question the window answers is one -- "where do my things go here" -- and
 * the answers used to be scattered: the floor in a window of its own, the
 * chests among the sections of "Место". Now the floor comes first and the
 * chests follow: what lies takes area, what is chested does not, and seeing
 * both side by side is what makes that trade-off legible.
 *
 * The window is for everyone: whoever got in puts things down and picks them
 * up. What keeps a stranger's hands away is the shut door (D-204) and the
 * chest's own lock (D-181) -- not a rule against touching.
 */
export function Ground({ look, session }: Omit<Props, "busy" | "act" | "book">) {
  //: Own waiting and own refusal: a full yard must refuse this window, not the map.
  const acting = useActions();
  const { busy, act } = acting;
  if (!look.floor && (look.storages ?? []).length === 0) return null;

  return (
    <>
      <Refusal of={acting} />
      <Floor look={look} session={session} busy={busy} act={act} />
      <Storages look={look} session={session} busy={busy} act={act} />
    </>
  );
}

/** The floor itself: what lies here, and putting things on it (D-192, D-204).
 *
 * Putting a thing down is the first thing a person back from the mine does.
 * Cargo takes area, area is finite, and a chest saves it -- hence three honest
 * answers to "where do I keep this": build more, buy chests, haul away.
 *
 * A passer-by through a shut location is not inside, and for them the floor is
 * closed.
 */
function Floor({ look, session, busy, act }: Omit<Props, "book">) {
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const floor = look.floor;
  if (!floor) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  const room = floor.space;
  const roofed = room.roofed > 0;
  const open = floor.open !== false;
  //: Everything in the hands can be put down: nothing here is weightless, and
  //: the area budget is what says whether it fits.
  const inHands = look.inventory;

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
                  {tally(thing.goods, thing.amount)} ·{" "}
                  {(thing.mass * thing.amount).toFixed(1)} кг
                </td>
                <td>
                  {open && (
                    <Amount
                      goods={thing.goods}
                      value={parts[thing.id] ?? null}
                      max={thing.amount}
                      onChange={(value) => setPart(thing.id, value)}
                    />
                  )}
                </td>
                <td>
                  {open && (
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
                      title="взять в руки — сколько унесёте"
                    >
                      Взять
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

      {open && inHands.length > 0 && (
        <table>
          <tbody>
            {inHands.map((thing) => (
              <tr key={thing.id}>
                <td>{thing.goods}</td>
                <td className="note">
                  {tally(thing.goods, thing.amount)} ·{" "}
                  {(thing.mass * thing.amount).toFixed(1)} кг
                </td>
                <td>
                  <Amount
                    goods={thing.goods}
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
                    title="положить здесь — сколько поместится"
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
        {!open
          ? "Вы здесь проходом: чужая закрытая локация пола вам не отдаёт."
          : floor.mine
            ? "Лежащее занимает площадь; в сундуке — не занимает."
            : "Чужое место, но лежащее на земле берёт всякий, кого сюда пустили."}
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
          <h2>
            {chest.goods}
            <Rule>
              Дом хранит то, что не увезти в руках; полный сундук не уносят.
            </Rule>
          </h2>
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
                        <td className="note">{tally(thing.goods, thing.amount)}</td>
                        <td>
                          <Amount
                            goods={thing.goods}
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
                          {tally(thing.goods, thing.amount)} ·{" "}
                          {(thing.mass * thing.amount).toFixed(1)} кг
                        </td>
                        <td>
                          <Amount
                            goods={thing.goods}
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
            </>
          )}
        </section>
      ))}
    </>
  );
}

/** The plot: whose it is, what it is called, who gets in -- and how it changes hands.
 *
 * One window for everything about the land itself (D-178, D-204): ownership,
 * the name, the door, buying an empty plot and founding a city. These used to
 * live in two windows ("Локация" and half of "Место"), and the seam between
 * them ran through one question -- "what is this land and what may I do with
 * it" -- which no window answered whole.
 *
 * Ownership is a public fact: whoever enters sees the owner, a person or a
 * city, so the window is shown to guests too -- read-only. The name is given
 * by whoever disposes of the land, and the map label changes, not the node
 * key: deeds and edges reference the key. The door and the lists belong to the
 * holder alone: civic land is regulated by citizenship and duties, not by a
 * list of names.
 */
export function Plot({ look, session }: Omit<Props, "busy" | "act" | "book">) {
  //: This window's own waiting and its own refusal: shutting the door here must
  //: not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;
  const node = look.node;
  const [name, setName] = useState("");
  //: Handing a plot over is asked twice: the deed is cancelled by it, and the
  //: way back is a purchase at the price list.
  const [giving, setGiving] = useState(false);
  if (!node) return null;

  //: Same three cases as the old purchase window: nobody's city land with a
  //: price, and the wild beyond the walls. An owned node is never for sale here.
  const forSale = !node.owner && (Boolean(node.wild) || node.price !== null);
  const owned = Boolean(node.owner || node.owner_city);
  if (!forSale && !owned) return null;

  //: Who the meter is charged to (D-149). Ownership does not answer it by
  //: itself: a bought plot stays civic land, yet its bill is a person's.
  const upkeep =
    node.upkeep === "owner"
      ? "За электричество здесь платите вы: счёт идёт с площади раз в период."
      : node.upkeep === "city"
        ? `Узел содержит город${node.owner_city ? ` ${node.owner_city}` : ""}: энергия уходит из городского пула, деньгами счёт не выставляется.`
        : node.upkeep === "nobody"
          ? "Счётчика здесь нет: у узла нет хозяина, и выставлять счёт некому."
          : node.owner || node.owner_city
            ? "Городской сети здесь нет: счёта за электричество не бывает, работают от аккумулятора."
            : null;

  const whose = node.mine
    ? "ваш участок"
    : node.owner
      ? `хозяин ${node.owner}`
      : node.owner_city
        ? `земля города ${node.owner_city}`
        : "ничей";

  return (
    <>
    <section>
      <Refusal of={acting} />
      <h2>Участок</h2>
      <p className="note">
        {node.name} · {node.area.toFixed(0)} м² · {whose}
        {node.gated && " · закрыта для входа"}
        {node.cut_off && " · отключена за неуплату"}
      </p>
      {upkeep && <p className="note">{upkeep}</p>}
      {/* Only civic land is handed over: a ship's cabin is owned too, and there
          is no city under it to take it. */}
      {node.mine && node.owner_city && (
        giving ? (
          <div className="row">
            <button onClick={() => act(async () => {
              await session.send("land.cede");
              setGiving(false);
            })} disabled={busy}>
              Да, передать городу
            </button>
            <button onClick={() => setGiving(false)} disabled={busy}>
              Отмена
            </button>
            <span className="note">
              Бумага на землю погашается, участок станет городским. Вернуть его
              можно только выкупом по прейскуранту — как любой другой.
            </span>
          </div>
        ) : (
          <div className="row">
            <button onClick={() => setGiving(true)} disabled={busy}>
              Передать городу
            </button>
            <span className="note">
              Счётчик перейдёт на казну: городской узел жжёт энергию из пула, и
              деньгами за него никто не платит. Оборудование останется на месте,
              но распоряжаться им будет власть, а не вы.
            </span>
          </div>
        )
      )}
      {forSale &&
        (node.wild ? (
          <p className="note">
            Земля за городом ничья и таковой остаётся: бумагу на владение
            выдаёт город, а здесь его нет. Работать и строить тут
            может всякий — поставленное принадлежит поставившему.
          </p>
        ) : node.price !== null ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.buy"))} disabled={busy}>
              Выкупить за {api.tk(node.price)} ₭
            </button>
            <span className="note">
              Цена от удалённости до биопринтера: деньги в казну,
              вам — бумага на землю.
            </span>
          </div>
        ) : null)}
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
            Имя увидят все на карте; ключ локации не меняется.
          </span>
        </div>
      )}
      {node.mine && <Door look={look} session={session} busy={busy} act={act} />}
    </section>
    {/* Founding a city is the plot's fate, so the section stands here:
        the server offers it only where founding is possible at all. */}
    <Foundation look={look} session={session} busy={busy} act={act} />
    </>
  );
}

/** The door of one's own location: shut for entry, and two lists (D-204).
 *
 * Shutting stops **entry**, not passage: through a shut location one still
 * walks, so a neighbour whose home stands behind this one is never cut off from
 * it. The lists are two, and where they contradict each other the black one
 * wins -- one line to learn instead of a roster that flipped its meaning.
 */
function Door({ look, session, busy, act }: Omit<Props, "book">) {
  const node = look.node;
  //: One field per list: typing a name to let in and a name to keep out are
  //: different intentions, and a shared field would make them one slip apart.
  const [friend, setFriend] = useState("");
  const [foe, setFoe] = useState("");
  if (!node) return null;

  const allowed = node.allowed ?? [];
  const barred = node.barred ?? [];
  const shut = Boolean(node.gated);

  const strike = (name: string) => (
    <button
      key={name}
      onClick={() => act(() => session.send("gate.list", { who: name, strike: true }))}
      disabled={busy}
      title="убрать из списка"
    >
      {name} ✕
    </button>
  );

  const name = (who: string, allow: boolean, clear: () => void) =>
    act(async () => {
      await session.send("gate.list", { who: who, allowed: allow });
      clear();
    });

  return (
    <>
      <h3>
        Вход
        <Rule>
          Вошедший распоряжается тем, что лежит на земле: дверь и сундук — защита, а не
          правило «не бери».
        </Rule>
      </h3>
      <div className="row">
        <button
          onClick={() => act(() => session.send("gate.set", { closed: !shut }))}
          disabled={busy}
        >
          {shut ? "Открыть вход" : "Закрыть вход"}
        </button>
        <span className="note">
          {shut
            ? "Закрыта: входят хозяин и белый список."
            : "Открыта: входят все, кроме чёрного списка."}
          {" Пройти насквозь можно всегда — и выйти тоже: закрыть вход при госте нельзя."}
        </span>
      </div>

      <div className="row">
        <input
          value={friend}
          onChange={(e) => setFriend(e.target.value)}
          placeholder="имя"
          title="кого пускать в закрытую локацию"
        />
        <button
          onClick={() => name(friend.trim(), true, () => setFriend(""))}
          disabled={busy || !friend.trim()}
        >
          В белый список
        </button>
        {allowed.length > 0 ? (
          <span className="note">Пускаем: {allowed.map(strike)}</span>
        ) : (
          <span className="note">
            {shut ? "Пока никого: входите только вы." : "Пригодится, когда закроете вход."}
          </span>
        )}
      </div>

      <div className="row">
        <input
          value={foe}
          onChange={(e) => setFoe(e.target.value)}
          placeholder="имя"
          title="кого не пускать вовсе"
        />
        <button
          onClick={() => name(foe.trim(), false, () => setFoe(""))}
          disabled={busy || !foe.trim()}
        >
          В чёрный список
        </button>
        {barred.length > 0 ? (
          <span className="note">Не пускаем: {barred.map(strike)}</span>
        ) : (
          <span className="note">Чёрный список сильнее белого: названный тут не войдёт.</span>
        )}
      </div>
    </>
  );
}

/** The house: build it, take it apart -- and furnish it.
 *
 * Storeys are the point of the building part (D-125, D-145): the plot limits
 * the footprint, not the workshop -- a house grows upwards where the ground
 * does not grow sideways. The bill is shown **before** the work and against
 * what is in hand, so that "wood 12 of 30" is read at the plan and not
 * discovered at the click. Demolition (D-205) is shown the same way round:
 * the term, what comes back and what is in the way -- all before the button.
 *
 * Machines and furniture follow in the same window: both go **into the house**
 * and take its slots (D-106, D-150), so raising walls and filling them is one
 * story, not two windows. Working at a machine is another matter -- for that
 * the machine has a row of its own in the location.
 */
export function House({ look, session, book }: Omit<Props, "busy" | "act">) {
  //: Own waiting and own refusal: this window is a window of its own in the row.
  const acting = useActions();
  const { busy, act } = acting;
  const home = look.node?.building;
  const plot = look.node?.area ?? 0;
  const [area, setArea] = useState(20);
  const [floors, setFloors] = useState(1);
  //: Empty means "the plainest type there is" -- the engine decides which, so
  //: that the default lives in the vault and not in two places at once.
  const [kind, setKind] = useState<string>("");
  const [bill, setBill] = useState<any>(null);
  //: The shop window and the smallest footprint outlive the bill: the bill is
  //: cleared once the order is placed, and these two must not go with it --
  //: without them the picker would empty out and the minimum would read wrong
  //: exactly at the moment the player looks at what they have just started.
  const [shelf, setShelf] = useState<any[]>([]);
  const [least, setLeast] = useState(1);
  const take = (answer: any) => {
    setBill(answer);
    setShelf(answer?.kinds ?? []);
    setLeast(answer?.area_min ?? 1);
  };
  //: Which quality of each material goes into the wall (D-058).
  const [tiers, setTiers] = useState<Record<string, string | null>>({});
  //: The shop window of types comes back with the bill, so before the first
  //: estimate there is nothing to choose from. Hence one estimate on arrival:
  //: the type is chosen before the numbers, and the numbers must already be
  //: there. Above the early return on purpose -- a hook that sometimes does not
  //: run is a hook React counts wrong.
  const key = look.node?.key;
  const buildable = Boolean(look.node?.mine || look.node?.wild);
  useEffect(() => {
    if (!buildable) return;
    let dropped = false;
    void session
      .send("build.estimate", { area, floors, kind: kind || undefined })
      .then((first: any) => {
        if (!dropped) take(first);
      })
      .catch(() => undefined);
    return () => {
      dropped = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, buildable]);
  if (!home) return null;

  const going = home.building ?? [];
  //: Ground already promised to a site is ground taken (D-218): the engine
  //: counts it, and the window must count it the same way -- otherwise it
  //: offers metres the order will refuse.
  const started = going.reduce((sum, work) => sum + work.area, 0);
  const free = Math.max(0, plot - home.ground - started);

  const count = async () => {
    take(await session.send("build.estimate", { area, floors, kind: kind || undefined }));
  };
  const picked = kind || shelf[0]?.kind || "";

  const short = (bill?.materials ?? []).filter((m: any) => m.have < m.need);

  return (
    <>
    <section>
      <Refusal of={acting} />
      <h2>Дом</h2>
      {home.area > 0 ? (
        <p>
          жилой площади <b>{home.area.toFixed(0)} м²</b> в{" "}
          <b>{home.floors}</b> эт. на {home.ground.toFixed(0)} м² земли · мест
          под оборудование{" "}
          <b>
            {home.used} из {home.slots}
          </b>
          {home.kind && (
            <>
              {" · "}
              {home.kind}, состояние <b>{(home.condition ?? 0).toFixed(0)}%</b>
              {home.decay > 0 && ` (−${home.decay}% в сутки)`}
            </>
          )}
        </p>
      ) : (
        <p className="note">
          Дома нет — только двор. Рабочие станции и мебель ставят в дом: сначала строят.
        </p>
      )}

      {going.length > 0 && (
        <p className="note">
          Строится:{" "}
          {going
            .map(
              (w) =>
                `${w.area.toFixed(0)} м² в ${w.floors} эт.${w.kind ? ` (${w.kind})` : ""}`,
            )
            .join(", ")}
          {" · готово "}
          {when(going[0].ready_at)}. Материалы уже в стене.
        </p>
      )}

      {/* Ничью землю за городом строит всякий пришедший (D-198): окно нужно и
          там, иначе правило есть, а руки к нему не приложить. */}
      {(look.node?.mine || look.node?.wild) && free > 0 && (
        <>
          <div className="row">
            {/* Тип решает три вещи разом (D-218): состав, цену следующего этажа
                и скорость порчи. Числа показаны прямо в списке — выбор делают
                до сметы, и гадать о нём игрок не должен. */}
            <select
              value={picked}
              onChange={(e) => {
                setKind(e.target.value);
                setBill(null);
              }}
              title="тип здания"
            >
              {shelf.length === 0 && <option value="">…</option>}
              {shelf.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.kind} · этаж ×{k.growth} · порча {k.decay}%/сут
                </option>
              ))}
            </select>
            <input
              type="number"
              min={least}
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
            <button
              onClick={() => act(count)}
              disabled={busy || area < least || area > free}
            >
              Посчитать смету
            </button>
            <span className="note">
              {area} м² × {floors} эт. = {area * floors} м² жилой площади.
              Свободно {free.toFixed(0)} м² двора, меньше {least} м² не строится.
              {" "}Этажность не ограничена — за высоту платит смета.
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
                      <td>
                        {/* Which quality goes into the wall (D-058). */}
                        <TierPick
                          things={look.inventory}
                          goods={m.goods}
                          value={tiers[m.goods]}
                          onChange={(tier) => setTiers((was) => ({ ...was, [m.goods]: tier }))}
                        />
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
                        kind: picked || undefined,
                        tiers: Object.fromEntries(
                          Object.entries(tiers).filter(([, tier]) => tier),
                        ),
                      });
                      setBill(null);
                    })
                  }
                  disabled={busy || short.length > 0 || area > free || area < least}
                >
                  Строить {area} м² в {floors} эт.
                </button>
                <span className="note">
                  {short.length > 0
                    ? `Не хватает: ${short.map((m: any) => m.goods).join(", ")}`
                    : `Работы на ${(bill.minutes / 60).toFixed(1)} ч; ${bill.kind}.`}
                </span>
              </div>
            </>
          )}
        </>
      )}

      {/* Сносят там же, где строят: своё — и любую ничью землю за городом, где
          труд открыт всякому (D-198, D-205). Чужую городскую застройку
          разбирают по решению суда (D-095). */}
      {home.area > 0 && (look.node?.mine || look.node?.wild) && (
        <>
          <Repair look={look} session={session} busy={busy} act={act} />
          <Demolition look={look} session={session} busy={busy} act={act} />
        </>
      )}
    </section>
    <Equipment
      title="Рабочие станции"
      things={look.bench ?? []}
      kind="station"
      look={look}
      session={session}
      busy={busy}
      act={act}
      book={book}
      note="За рабочей станцией работает один: пока идёт партия, второму она не отдаётся."
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
    </>
  );
}

/** Mending one's own house: what it costs and how long it takes (D-218).
 *
 * A house stands at full strength right up to nothing and then falls, so the
 * only warning is the condition itself -- and it must be read here, next to the
 * button that answers it. The bill is asked for by the button, like the
 * building one: it is a question about a decision, and the decision is rare.
 */
function Repair({ look, session, busy, act }: Omit<Props, "book">) {
  const [plan, setPlan] = useState<any>(null);
  const home = look.node?.building;
  const worn = home?.condition ?? 100;
  const short = (plan?.materials ?? []).filter((m: any) => m.have < m.need);

  const count = async () => {
    setPlan(await session.send("build.repair_estimate"));
  };

  return (
    <>
      <div className="row">
        <button className="quiet" onClick={() => act(count)} disabled={busy || worn >= 100}>
          Посчитать ремонт
        </button>
        <span className="note">
          {worn >= 100
            ? "Дом целёхонек: чинить в нём нечего."
            : `Состояние ${worn.toFixed(0)}%. На нуле дом обрушится вместе с тем, что стоит во дворе.`}
        </span>
      </div>

      {plan && plan.materials.length > 0 && (
        <>
          <table>
            <tbody>
              {plan.materials.map((m: any) => (
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
                  await session.send("build.repair");
                  setPlan(null);
                })
              }
              disabled={busy || short.length > 0 || !plan.mine || plan.going}
            >
              Чинить
            </button>
            <span className="note">
              {plan.going
                ? "Ремонт уже идёт."
                : short.length > 0
                  ? `Не хватает: ${short.map((m: any) => m.goods).join(", ")}`
                  : `Работы на ${(plan.minutes / 60).toFixed(1)} ч; чинят тем же, чем построено.`}
            </span>
          </div>
        </>
      )}
    </>
  );
}

/** Demolishing one's own house: the term, the return and what is in the way (D-205).
 *
 * The estimate is asked for by the button, not on every poll: it is a question
 * about a decision, and the decision is rare. Everything that blocks the work is
 * shown as reasons -- the engine names them, the window does not guess.
 */
function Demolition({ look, session, busy, act }: Omit<Props, "book">) {
  const [plan, setPlan] = useState<any>(null);
  const going = (look.node?.building?.building ?? []).length > 0;

  const count = async () => {
    setPlan(await session.send("build.demolish_estimate"));
  };
  const blocking: string[] = plan?.blocking ?? [];

  return (
    <>
      <div className="row">
        <button className="quiet" onClick={() => act(count)} disabled={busy || going}>
          Посчитать снос
        </button>
        <span className="note">
          {going
            ? "Пока идёт стройка, сносить нечего: дождитесь её конца."
            : "Снос — работа: часть материалов вернётся, остальное сломается при разборе."}
        </span>
      </div>

      {plan && (
        <>
          {plan.back.length > 0 && (
            <table>
              <tbody>
                {plan.back.map((m: any) => (
                  <tr key={m.goods}>
                    <td>{m.goods}</td>
                    <td className="note">вернётся {tally(m.goods, m.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row">
            <button
              onClick={() =>
                act(async () => {
                  await session.send("build.demolish");
                  setPlan(null);
                })
              }
              disabled={busy || blocking.length > 0 || !plan.mine}
              title={
                blocking.length > 0
                  ? "двор пустеет до сноса, а не после"
                  : "работы идут временем, материалы придут в конце"
              }
            >
              Снести {plan.area.toFixed(0)} м²
            </button>
            <span className="note">
              {blocking.length > 0
                ? `Сначала: ${blocking.join("; ")}`
                : `Работы на ${(plan.minutes / 60).toFixed(1)} ч. Участок станет пустым.`}
            </span>
          </div>
        </>
      )}
    </>
  );
}

/** Human-readable titles of place signs.
 *
 * The keys are the node properties themselves, and those come from the vault
 * in Russian -- they are game data, not identifiers. A key translated to
 * English silently stopped matching and the window showed the raw property.
 */
export const PLACES: Record<string, string> = {
  лес: "Лес",
  камни: "Камни",
  луг: "Луг",
};

/** Signs of the land offering extraction to this viewer: one row per sign (D-177).
 *
 * The row (`Stand.tsx`) asks what stands here; a forest is as much a thing to
 * work at as a furnace, so each sign earns a row of its own instead of hiding
 * in a catch-all window. Somebody else's forest belongs to its owner: own and
 * nobody's land only.
 */
export function gatherSigns(look: Look, book: any): string[] {
  const node = look.node;
  if (!node || !(node.mine || node.wild)) return [];
  const signs: string[] = [];
  for (const operation of book?.operations ?? []) {
    const sign = operation.place;
    if (sign && (node.features ?? []).includes(sign) && !signs.includes(sign)) {
      signs.push(sign);
    }
  }
  return signs;
}

/** Place extraction (D-177): felling without a machine.
 *
 * One window per sign, opened from its row. The batch runs as ordinary
 * craft -- time and tool from the vault, the finished product is seen in "jobs".
 * What lies on the ground -- deadwood, stones, flax -- is not gathered
 * here: that is foraging on empty land, a window of its own (D-210).
 */
export function Gather({
  look,
  session,
  book,
  sign,
}: Omit<Props, "busy" | "act"> & { sign: string }) {
  //: Own waiting and own refusal: a window of its own in the row.
  const acting = useActions();
  const { busy, act } = acting;
  const [qty, setQty] = useState(10);
  const ways = (book?.operations ?? []).filter((o: any) => o.place === sign);
  if (ways.length === 0) return null;

  //: What satisfies the requirement: the item itself or any of the class ("Axe").
  const inHands = new Set(look.inventory.map((thing) => thing.goods));
  const hasMeans = (withWhat: string) =>
    inHands.has(withWhat) ||
    ((book?.tool_classes?.[withWhat] ?? []) as string[]).some((i) => inHands.has(i));

  return (
    <section>
      <Refusal of={acting} />
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
                      //: The button names the operation: one thing may
                      //: come from several ways (D-196).
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
          Партия идёт временем, готовое забирается в «делах». Валежник и
          прочее лежащее — в «Собирательстве».
        </span>
      </div>
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
 * A wagon standing in the node is an object of the node, so it has a row of
 * its own -- and a separate one from machines on purpose: nobody stands at a
 * wagon to work, one harnesses to it, and these two must not be confused.
 */
export function Convoy({ look, session }: Omit<Props, "busy" | "act" | "book">) {
  //: Own waiting and own refusal: a window of its own in the row.
  const acting = useActions();
  const { busy, act } = acting;
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
      <Refusal of={acting} />
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
                    <td className="note">{tally(thing.type_key, thing.amount)}</td>
                    <td>
                      <Amount
                        goods={thing.type_key}
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
                      {tally(thing.goods, thing.amount)} ·{" "}
                      {(thing.mass * thing.amount).toFixed(1)} кг
                    </td>
                    <td>
                      <Amount
                        goods={thing.goods}
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

/** What in the hands is equipment of this kind: the kind comes from vault data (D-090). */
function placeable(look: Look, book: any, kind: "station" | "furniture") {
  return look.inventory.filter((thing) =>
    (book?.recipes ?? []).some(
      (r: any) => r.name === thing.goods && r.kind === kind,
    ),
  );
}

/** The common equipment section: machines and furniture differ only by kind.
 *
 * Both stand among the sections of the "Дом" window and go silent where there
 * is nothing to say: nothing placed and nothing in hands to place is not worth
 * a header. The house summary above already counts the slots.
 */
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

  const inHands = placeable(look, book, kind);

  if (things.length === 0 && !((mine || hasPower) && inHands.length > 0)) {
    return null;
  }

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
                  {/* У аккумулятора состояние — это заряд, а не «занята»:
                      за ним не работают, он хранит энергию (D-179). */}
                  {thing.charge !== null
                    ? `заряд ${thing.charge.toFixed(0)} · заряжают в «хозяйстве»`
                    : kind === "station"
                      ? thing.busy
                        ? thing.mine
                          ? "занята вами"
                          : "занята"
                        : "свободна"
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
