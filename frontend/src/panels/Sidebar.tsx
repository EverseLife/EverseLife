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

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Look, Session } from "../api";
import { Economy } from "./Economy";
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
      {current === "хозяйство" && (
        <>
          <Holdings look={look} session={session} busy={busy} act={act} />
          {/* Банк — тоже Сеть: кредит берут и гасят откуда угодно (D-167). */}
          <Bank session={session} busy={busy} act={act} />
        </>
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

function Doings({ look }: { look: Look }) {
  const empty = look.batches.length === 0 && !look.travel;
  return (
    <div>
      {look.travel && (
        <p>
          в пути: {look.travel.final ?? look.travel.to} · придём{" "}
          {left(look.travel.arrives_at)}
          {look.travel.final ? " в следующий узел" : ""}
        </p>
      )}
      {look.batches.map((job) => (
        <p key={job.id}>
          {job.work === "make" ? job.output
            : job.work === "repair" ? `починка: ${job.output}`
            : `переработка: ${job.output}`}{" "}
          · {left(job.ready_at)}
        </p>
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
            <p key={reservation.id}>
              {reservation.goods}, {reservation.tier} · {reservation.amount} по {api.tk(reservation.price)} ₭ ·{" "}
              {reservation.node} · задаток {api.tk(reservation.deposit)} ₭ ·{" "}
              <b>забрать {left(reservation.expires_at)}</b>
            </p>
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

function left(when: string): string {
  const seconds = Math.round((new Date(when).getTime() - Date.now()) / 1000);
  if (seconds <= 0) return "вот-вот";
  return `через ${api.spell(seconds)}`;
}

/** Bank: rate, own loans, credit and repayment (D-030, D-087, D-167).
 *
 * The rate is shown together with an explanation of where it came from: the
 * algorithm must be not only deterministic but readable -- otherwise there is
 * nothing to argue monetary policy with. Reserve and circulating supply are
 * public for the same reason.
 */
function Bank({
  session,
  busy,
  act,
}: {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
}) {
  const [bank, setBank] = useState<any>(null);
  const [qty, setQty] = useState(50);

  const refresh = useCallback(async () => {
    try {
      setBank(await session.send("bank.view"));
    } catch {
      setBank(null);
    }
  }, [session]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!bank) return null;
  const go = (what: () => Promise<unknown>) => act(async () => {
    await what();
    await refresh();
  });

  return (
    <section>
      <h2>Банк</h2>
      <p>
        ключевая ставка <b>{Number(bank.rate).toFixed(2)}%</b>
        {bank.why && <span className="note"> · {bank.why}</span>}
      </p>
      <Council session={session} busy={busy} act={act} />
      <p className="note">
        в обороте {api.tk(bank.circulating)} ₭ · в резерве {api.tk(bank.reserve)} ₭
      </p>
      <p>
        ваш лимит <b>{api.tk(bank.limit)} ₭</b>
        <span className="note"> · {bank.limit_why}</span>
      </p>

      {(bank.loans ?? []).length > 0 && (
        <table>
          <tbody>
            {bank.loans.map((loan: any) => (
              <tr key={loan.id}>
                <td>
                  {api.tk(loan.outstanding)} ₭
                  <span className="note"> из {api.tk(loan.principal)} ₭ под {Number(loan.rate).toFixed(1)}%</span>
                </td>
                <td>
                  <button
                    onClick={() => go(() => session.send("bank.repay", { loan: loan.id }))}
                    disabled={busy}
                  >
                    Погасить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row">
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          title="сколько занять, ₭"
        />
        <button
          onClick={() => go(() => session.send("bank.borrow", { amount: qty }))}
          disabled={busy || qty <= 0}
        >
          Взять кредит
        </button>
        <span className="note">
          Залога нет: лимит выдаёт труд (D-173). Занимает ваш город со своей
          маржой (D-175).
        </span>
      </div>
    </section>
  );
}

/** The Council of cities and the rate (D-087, D-172).
 *
 * While there are fewer cities with an administration than the threshold, the
 * algorithm computes the rate, and this window just says how many are left.
 * After the threshold -- the city's vote in the corridor around the
 * recommendation: the Council argues with the algorithm, not replaces it.
 */

function Council({
  session,
  busy,
  act,
}: {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
}) {
  const [council, setCouncil] = useState<any>(null);
  const [rate, setRate] = useState<number | null>(null);

  useEffect(() => {
    void session.send("bank.council").then(setCouncil).catch(() => setCouncil(null));
  }, [session]);

  if (!council) return null;
  if (!council.council_decides) {
    return (
      <p className="note">
        {council.locked_until
          ? `ставка возвращена алгоритму до ${new Date(council.locked_until).toLocaleString()}: инфляция за тревожной чертой`
          : `ставку считает алгоритм: городов с администрацией ${council.cities_with_hall} из ${council.handover_at}, дальше решает Совет городов`}
      </p>
    );
  }

  const desired_ = rate ?? Number(council.advised);
  return (
    <div className="row">
      <input
        type="number"
        step={0.5}
        min={Number(council.advised) - Number(council.corridor)}
        max={Number(council.advised) + Number(council.corridor)}
        value={desired_}
        onChange={(e) => setRate(Number(e.target.value))}
        title={`коридор ±${council.corridor} вокруг рекомендации ${Number(council.advised).toFixed(2)}%`}
      />
      <button
        onClick={() => act(() => session.send("bank.council_rate", { rate: desired_ }))}
        disabled={busy}
      >
        Голос города за ставку
      </button>
      <span className="note">
        алгоритм советует {Number(council.advised).toFixed(2)}% · коридор ±
        {council.corridor} · голос подаёт держатель права «законы» (D-172)
      </span>
    </div>
  );
}
