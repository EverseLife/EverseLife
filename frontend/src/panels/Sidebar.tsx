/**
 * The left sidebar -- what works over the Net (D-050).
 *
 * The interface's organising principle is the same as the world's: **the
 * sidebar is remote, the main window is in-person**. Everything lying here is
 * available from anywhere, even from the road: account, jobs, own orders,
 * knowledge. The player absorbs the world's structure just by using the interface.
 *
 * From the vault's tabs those with mechanics behind them are assembled:
 * character, inventory, jobs, trade, knowledge, holdings. City governing left
 * here for the administration: authority is in-person (D-155). Bank and
 * obligations arrive with their systems (E3-E4).
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Look, Session } from "../api";
import { Doing } from "../Deadline";
import { Economy } from "./Economy";
import { Finance } from "./Finance";
import { Holdings } from "./Holdings";
import { Population } from "./Population";
import { Workshop } from "./Workshop";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The vault catalog -- for the "craft" tab: what is made by hand. */
  book?: any;
};

const TABS = [
  "персонаж",
  "инвентарь",
  "крафт",
  "дела",
  "торговля",
  //: Money is its own kind of business (D-190): the account, the statement,
  //: transfers and credit. It used to hide inside "хозяйство" behind meter
  //: bills, where nobody looks for a bank.
  "финансы",
  "знания",
  "хозяйство",
] as const;
//: State tabs: economy and population figures -- for those who govern. Shown
//: only to state officials; the same summary is visible in person in the node
//: with the administration (D-140, D-154).
const STATE_TABS = ["экономика", "население"] as const;
type Tab = (typeof TABS)[number] | (typeof STATE_TABS)[number];

export function Sidebar({ look, session, busy, act, book }: Props) {
  const [tab, setTab] = useState<Tab>("персонаж");

  //: A state office is at least one power in a city (D-155).
  const official = (look.city?.powers?.length ?? 0) > 0;
  const tabs: readonly Tab[] = official ? [...TABS, ...STATE_TABS] : TABS;
  const current: Tab = tabs.includes(tab) ? tab : "персонаж";

  return (
    <aside className="sidebar">
      <nav className="row tabs">
        {tabs.map((name) => (
          <button
            key={name}
            className={current === name ? "" : "quiet"}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      {current === "персонаж" && (
        <Character look={look} session={session} busy={busy} act={act} />
      )}
      {current === "инвентарь" && (
        <Inventory look={look} session={session} busy={busy} act={act} />
      )}
      {/* Ручной крафт живёт в сайдбаре: верёвку вьют там, где стоят, и станок
          этому месту не нужен. Запуск всё равно присутственный: в пути и во
          сне сервер откажет. */}
      {current === "крафт" && (
        <Workshop
          machine={null}
          book={book}
          look={look}
          act={act}
          session={session}
          busy={busy}
        />
      )}
      {current === "дела" && <Doings look={look} />}
      {current === "торговля" && (
        <Trade look={look} session={session} busy={busy} act={act} />
      )}
      {current === "знания" && <Knowledge look={look} />}
      {/* Хозяйство — деньги и документы, а не материя: счета за быт и ценные
          бумаги живут в Сети (D-116, D-149). */}
      {current === "финансы" && (
        <Finance look={look} session={session} busy={busy} act={act} />
      )}
      {/* Хозяйство — счета за быт, сеть и ценные бумаги: имущество, а не деньги. */}
      {current === "хозяйство" && (
        <Holdings look={look} session={session} busy={busy} act={act} />
      )}
      {current === "экономика" && (
        <Economy look={look} session={session} busy={busy} />
      )}
      {current === "население" && (
        <Population look={look} session={session} busy={busy} />
      )}

      <p className="note">
        Сайдбар — это Сеть: работает откуда угодно (D-044, D-050).
      </p>
    </aside>
  );
}

function Character({ look, session, busy, act }: Props) {
  const sleepingSince = look.body?.sleeping_since ?? null;
  const fed =
    look.body?.satiated_until != null &&
    new Date(look.body.satiated_until).getTime() > Date.now();
  const bed_ = (look.node?.stations ?? []).includes("Кровать");
  return (
    <div>
      <p className="sign">{look.identity}</p>
      <table>
        <tbody>
          <tr>
            <td>выносливость</td>
            <td className="num">{look.body?.stamina.toFixed(1) ?? "—"}</td>
          </tr>
          <tr>
            <td>счёт</td>
            <td className="num">{look.money} ₭</td>
          </tr>
          <tr>
            <td>сытость</td>
            <td className="num">{fed ? "сыт: расход ниже" : "—"}</td>
          </tr>
          <tr>
            <td>тело</td>
            <td className="num">
              {look.body === null
                ? "нет"
                : sleepingSince
                  ? "спит"
                  : look.survey
                    ? "в разведке"
                    : look.travel
                      ? "в пути"
                      : "здесь"}
            </td>
          </tr>
        </tbody>
      </table>

      {/* Привал: сон живёт при персонаже, но остаётся присутственным —
          ложатся там, где стоят, и в пути не ложатся (D-091). */}
      {look.body !== null && (
        <div className="row">
          {sleepingSince ? (
            <>
              <button onClick={() => act(() => session.send("rest.wake"))} disabled={busy}>
                Проснуться
              </button>
              <span className="note">
                спит уже <Slept since={sleepingSince} /> · начислится при пробуждении
              </span>
            </>
          ) : (
            <>
              <button
                onClick={() => act(() => session.send("rest.sleep"))}
                disabled={busy || Boolean(look.travel)}
              >
                {bed_ ? "Лечь в кровать" : "Лечь спать"}
              </button>
              <span className="note">
                {look.travel
                  ? "в пути не ложатся"
                  : bed_
                    ? "кровать здесь: сон быстрее"
                    : "кровати нет: сон медленнее"}
              </span>
            </>
          )}
        </div>
      )}

      <p className="note">
        Личность бессмертна, тело — расходник; выносливость возвращает сон
        (D-012, D-091).
      </p>
    </div>
  );
}

function Slept({ since }: { since: string }) {
  const [minutes, setMinutes] = useState(() => elapsedMinutes(since));
  useEffect(() => {
    const timer = setInterval(() => setMinutes(elapsedMinutes(since)), 10_000);
    return () => clearInterval(timer);
  }, [since]);
  if (minutes < 1) return <b>меньше минуты</b>;
  if (minutes < 60) return <b>{Math.floor(minutes)} мин</b>;
  return <b>{(minutes / 60).toFixed(1)} ч</b>;
}

const elapsedMinutes = (since: string) =>
  (Date.now() - new Date(since).getTime()) / 60_000;

function Inventory({ look, session, busy, act }: Props) {
  const carried = look.carry;
  return (
    <div>
      {/* Предел носимого — не украшение, а причина существования повозок:
          игрок обязан видеть его числом до того, как упрётся (D-146). */}
      {carried && (
        <>
          <p className="sign">
            в руках {carried.load.toFixed(1)} из {carried.capacity.toFixed(0)} кг
          </p>
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
                        className="quiet"
                        onClick={() =>
                          act(() => session.send("gear.unequip", { slot: slot }))
                        }
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
        </>
      )}

      <Things
        things={look.inventory}
        eat={(id) => act(() => session.send("food.eat", { item: id }))}
        equip={(id) => act(() => session.send("gear.equip", { item: id }))}
        busy={busy}
      />
      {look.stall && look.stall.length > 0 && (
        <>
          <h3>В терминале</h3>
          <Things things={look.stall} />
        </>
      )}
      <p className="note">
        Смотреть можно откуда угодно; есть — из рук, и в дороге тоже. Трогать
        остальное — только ногами (D-047).
      </p>
    </div>
  );
}

/** Everything running, one line each with its deadline bar.
 *
 * The main screen of an asynchronous game: what is going on and how long is
 * left. Every term in the world is drawn by the same element, so a batch, a
 * road and a reservation are read the same way and compared at a glance.
 */
function Doings({ look }: { look: Look }) {
  const empty = look.batches.length === 0 && !look.travel;
  return (
    <div>
      {look.travel && (
        <Doing
          what={`в пути: ${look.travel.final ?? look.travel.to}`}
          until={look.travel.arrives_at}
          since={look.travel.started_at}
          aside={look.travel.final ? "до следующего узла" : undefined}
        />
      )}
      {look.batches.map((job) => (
        <Doing
          key={job.id}
          what={
            job.work === "make"
              ? job.output
              : job.work === "repair"
                ? `починка: ${job.output}`
                : `переработка: ${job.output}`
          }
          until={job.ready_at}
          since={job.started_at}
          aside={job.work === "make" ? `качество ${job.quality.toFixed(0)}` : undefined}
        />
      ))}
      {empty && <p className="note">ничего не идёт</p>}
      <p className="note">
        Длительные действия идут сами, в том числе пока вы офлайн: их двигает
        мир, а не браузер.
      </p>
    </div>
  );
}

function Trade({ look, session, busy, act }: Props) {
  return (
    <div>
      {/* Бронь — единственный способ купить удалённо, и она с часами:
          не забрал в срок — задаток остаётся продавцу (D-047). */}
      {look.reservations.length > 0 && (
        <>
          <h3>Брони</h3>
          {look.reservations.map((reservation) => (
            <Doing
              key={reservation.id}
              what={`${reservation.goods}, ${reservation.tier}`}
              until={reservation.expires_at}
              since={reservation.placed_at}
              aside={
                `${reservation.amount} по ${api.tk(reservation.price)} ₭ · ` +
                `${reservation.node} · задаток ${api.tk(reservation.deposit)} ₭`
              }
            />
          ))}
          <p className="note">
            Забирают ногами: приезжайте в узел и выкупайте. Срок вышел — задаток
            остался продавцу, товар вернулся в стакан.
          </p>
        </>
      )}

      {look.orders.length === 0 ? (
        <p className="note">своих ордеров нет</p>
      ) : (
        look.orders.map((order) => (
          <div className="row" key={order.id}>
            <span>
              {order.side === "buy" ? "куплю" : "продам"} {order.goods}, {order.tier} ·{" "}
              {order.left} по {api.tk(order.price)} ₭
            </span>
            <button
              className="quiet"
              onClick={() => act(() => session.send("market.cancel", { order: order.id }))}
              disabled={busy}
            >
              Снять
            </button>
          </div>
        ))
      )}
      <p className="note">
        Ордером распоряжаются отсюда; товар лежит в терминале (D-047).
      </p>
    </div>
  );
}

function Knowledge({ look }: { look: Look }) {
  return (
    <div>
      {look.knows.length === 0 ? (
        <p className="note">пока ничего: рецепты берут в Библиотеке</p>
      ) : (
        look.knows.map((name) => <p key={name}>{name}</p>)
      )}
      <p className="note">
        Знание живёт в личности и не теряется ни смертью, ни судом (И8).
      </p>
    </div>
  );
}

function Things({
  things,
  eat,
  equip,
  busy,
}: {
  things: api.Thing[];
  eat?: (id: string) => void;
  equip?: (id: string) => void;
  busy?: boolean;
}) {
  if (things.length === 0) return <p className="note">пусто</p>;
  return (
    <table>
      <tbody>
        {things.map((thing) => (
          <tr key={thing.id}>
            <td>
              {thing.flavor ?? thing.goods}
              {thing.spoils_at && (
                <span className="note"> · {spoilAt(thing.spoils_at)}</span>
              )}
            </td>
            <td className="num">{thing.amount}</td>
            <td className="note">
              {thing.fineness !== null
                ? `проба ${thing.fineness}${thing.maker ? ` · клеймо ${thing.maker}` : ""}`
                : thing.vigor !== null
                  ? `${thing.variety ?? "сорт"} · сила ${thing.vigor.toFixed(0)}`
                  : thing.charge !== null
                    ? `заряд ${thing.charge.toFixed(0)}`
                    : thing.quality === null
                      ? ""
                      : `${thing.quality.toFixed(0)} · ${thing.tier}`}
              {thing.condition < 100 ? ` · сост. ${thing.condition.toFixed(0)}` : ""}
            </td>
            <td>
              {thing.food && eat && (
                <button className="quiet" onClick={() => eat(thing.id)} disabled={busy}>
                  Съесть
                </button>
              )}
              {thing.slot && equip && (
                <button className="quiet" onClick={() => equip(thing.id)} disabled={busy}>
                  Надеть
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function spoilAt(when: string): string {
  const hours = (new Date(when).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return "испортилось";
  if (hours < 24) return `испортится через ${Math.round(hours)} ч`;
  return `годно ${Math.round(hours / 24)} сут.`;
}


