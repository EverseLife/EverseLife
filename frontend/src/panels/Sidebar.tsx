/**
 * Левый сайдбар — то, что работает через Сеть (D-050).
 *
 * Организующий принцип интерфейса — тот же, что у мира: **сайдбар — удалённое,
 * основное окно — присутственное**. Всё, что здесь лежит, доступно откуда
 * угодно, хоть с дороги: счёт, дела, свои ордера, знания. Игрок усваивает
 * устройство мира, просто пользуясь интерфейсом.
 *
 * Из табов вольта собраны те, за которыми уже есть механика: персонаж,
 * инвентарь, дела, торговля, знания, хозяйство. Управление городом отсюда
 * ушло в администрацию: власть присутственна (D-155). Банк и обязательства
 * приедут со своими системами (Э3–Э4).
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
  /** Справочник вольта — для вкладки «крафт»: что делается руками. */
  книга?: any;
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
//: Государственные вкладки: цифры экономики и населения — для тех, кто
//: правит. Показываются только гос.должностям; та же сводка присутственно
//: видна в узле с администрацией (D-140, D-154).
const STATE_TABS = ["экономика", "население"] as const;
type Tab = (typeof TABS)[number] | (typeof STATE_TABS)[number];

export function Sidebar({ look, session, busy, act, книга }: Props) {
  const [tab, setTab] = useState<Tab>("персонаж");

  //: Гос.должность — это хоть одно полномочие в городе (D-155).
  const чиновник = (look.city?.powers?.length ?? 0) > 0;
  const вкладки: readonly Tab[] = чиновник ? [...TABS, ...STATE_TABS] : TABS;
  const текущая: Tab = вкладки.includes(tab) ? tab : "персонаж";

  return (
    <aside className="sidebar">
      <nav className="row tabs">
        {вкладки.map((name) => (
          <button
            key={name}
            className={текущая === name ? "" : "quiet"}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      {текущая === "персонаж" && (
        <Character look={look} session={session} busy={busy} act={act} />
      )}
      {текущая === "инвентарь" && (
        <Inventory look={look} session={session} busy={busy} act={act} />
      )}
      {/* Ручной крафт живёт в сайдбаре: верёвку вьют там, где стоят, и станок
          этому месту не нужен. Запуск всё равно присутственный: в пути и во
          сне сервер откажет. */}
      {текущая === "крафт" && (
        <Workshop
          станок={null}
          книга={книга}
          look={look}
          act={act}
          session={session}
          busy={busy}
        />
      )}
      {текущая === "дела" && <Doings look={look} />}
      {текущая === "торговля" && (
        <Trade look={look} session={session} busy={busy} act={act} />
      )}
      {текущая === "знания" && <Knowledge look={look} />}
      {/* Хозяйство — деньги и документы, а не материя: счета за быт и ценные
          бумаги живут в Сети (D-116, D-149). */}
      {текущая === "хозяйство" && (
        <>
          <Holdings look={look} session={session} busy={busy} act={act} />
          {/* Банк — тоже Сеть: кредит берут и гасят откуда угодно (D-167). */}
          <Bank session={session} busy={busy} act={act} />
        </>
      )}
      {текущая === "экономика" && (
        <Economy look={look} session={session} busy={busy} />
      )}
      {текущая === "население" && (
        <Population look={look} session={session} busy={busy} />
      )}

      <p className="note">
        Сайдбар — это Сеть: работает откуда угодно (D-044, D-050).
      </p>
    </aside>
  );
}

function Character({ look, session, busy, act }: Props) {
  const спит_с = look.body?.sleeping_since ?? null;
  const сыт =
    look.body?.satiated_until != null &&
    new Date(look.body.satiated_until).getTime() > Date.now();
  const кровать = (look.node?.stations ?? []).includes("Кровать");
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
            <td className="num">{сыт ? "сыт: расход ниже" : "—"}</td>
          </tr>
          <tr>
            <td>тело</td>
            <td className="num">
              {look.body === null
                ? "нет"
                : спит_с
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
          {спит_с ? (
            <>
              <button onClick={() => act(() => session.send("rest.wake"))} disabled={busy}>
                Проснуться
              </button>
              <span className="note">
                спит уже <Slept since={спит_с} /> · начислится при пробуждении
              </span>
            </>
          ) : (
            <>
              <button
                onClick={() => act(() => session.send("rest.sleep"))}
                disabled={busy || Boolean(look.travel)}
              >
                {кровать ? "Лечь в кровать" : "Лечь спать"}
              </button>
              <span className="note">
                {look.travel
                  ? "в пути не ложатся"
                  : кровать
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
  const [минут, setМинут] = useState(() => elapsedMinutes(since));
  useEffect(() => {
    const timer = setInterval(() => setМинут(elapsedMinutes(since)), 10_000);
    return () => clearInterval(timer);
  }, [since]);
  if (минут < 1) return <b>меньше минуты</b>;
  if (минут < 60) return <b>{Math.floor(минут)} мин</b>;
  return <b>{(минут / 60).toFixed(1)} ч</b>;
}

const elapsedMinutes = (since: string) =>
  (Date.now() - new Date(since).getTime()) / 60_000;

function Inventory({ look, session, busy, act }: Props) {
  const носимое = look.carry;
  return (
    <div>
      {/* Предел носимого — не украшение, а причина существования повозок:
          игрок обязан видеть его числом до того, как упрётся (D-146). */}
      {носимое && (
        <>
          <p className="sign">
            в руках {носимое.load.toFixed(1)} из {носимое.capacity.toFixed(0)} кг
          </p>
          <div className="row">
            {носимое.slots.map((слот) => {
              const надето = носимое.equipped[слот];
              return (
                <span key={слот} className="note">
                  {слот}:{" "}
                  {надето ? (
                    <>
                      {надето.goods}{" "}
                      <button
                        className="quiet"
                        onClick={() =>
                          act(() => session.send("gear.unequip", { slot: слот }))
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
  const пусто = look.batches.length === 0 && !look.travel;
  return (
    <div>
      {look.travel && (
        <p>
          в пути: {look.travel.final ?? look.travel.to} · придём{" "}
          {осталось(look.travel.arrives_at)}
          {look.travel.final ? " в следующий узел" : ""}
        </p>
      )}
      {look.batches.map((дело) => (
        <p key={дело.id}>
          {дело.work === "make" ? дело.output
            : дело.work === "repair" ? `починка: ${дело.output}`
            : `переработка: ${дело.output}`}{" "}
          · {осталось(дело.ready_at)}
        </p>
      ))}
      {пусто && <p className="note">ничего не идёт</p>}
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
          {look.reservations.map((бронь) => (
            <p key={бронь.id}>
              {бронь.goods}, {бронь.tier} · {бронь.amount} по {api.tk(бронь.price)} ₭ ·{" "}
              {бронь.node} · задаток {api.tk(бронь.deposit)} ₭ ·{" "}
              <b>забрать {осталось(бронь.expires_at)}</b>
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
        look.orders.map((ордер) => (
          <div className="row" key={ордер.id}>
            <span>
              {ордер.side === "buy" ? "куплю" : "продам"} {ордер.goods}, {ордер.tier} ·{" "}
              {ордер.left} по {api.tk(ордер.price)} ₭
            </span>
            <button
              className="quiet"
              onClick={() => act(() => session.send("market.cancel", { order: ордер.id }))}
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
        look.knows.map((имя) => <p key={имя}>{имя}</p>)
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
                <span className="note"> · {срокПорчи(thing.spoils_at)}</span>
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

function срокПорчи(когда: string): string {
  const часов = (new Date(когда).getTime() - Date.now()) / 3_600_000;
  if (часов <= 0) return "испортилось";
  if (часов < 24) return `испортится через ${Math.round(часов)} ч`;
  return `годно ${Math.round(часов / 24)} сут.`;
}

function осталось(когда: string): string {
  const секунды = Math.round((new Date(когда).getTime() - Date.now()) / 1000);
  if (секунды <= 0) return "вот-вот";
  return `через ${api.spell(секунды)}`;
}

/** Банк: ставка, свои займы, кредит и погашение (D-030, D-087, D-167).
 *
 * Ставка показана вместе с объяснением, откуда она взялась: алгоритм обязан
 * быть не только детерминированным, но и читаемым — иначе спорить с денежной
 * политикой нечем. Резерв и оборотная масса публичны по той же причине.
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
  const [банк, setБанк] = useState<any>(null);
  const [сколько, setСколько] = useState(50);

  const обновить = useCallback(async () => {
    try {
      setБанк(await session.send("bank.view"));
    } catch {
      setБанк(null);
    }
  }, [session]);
  useEffect(() => {
    void обновить();
  }, [обновить]);

  if (!банк) return null;
  const го = (what: () => Promise<unknown>) => act(async () => {
    await what();
    await обновить();
  });

  return (
    <section>
      <h2>Банк</h2>
      <p>
        ключевая ставка <b>{Number(банк.rate).toFixed(2)}%</b>
        {банк.why && <span className="note"> · {банк.why}</span>}
      </p>
      <Council session={session} busy={busy} act={act} />
      <p className="note">
        в обороте {api.tk(банк.circulating)} ₭ · в резерве {api.tk(банк.reserve)} ₭
      </p>
      <p>
        ваш лимит <b>{api.tk(банк.limit)} ₭</b>
        <span className="note"> · {банк.limit_why}</span>
      </p>

      {(банк.loans ?? []).length > 0 && (
        <table>
          <tbody>
            {банк.loans.map((заём: any) => (
              <tr key={заём.id}>
                <td>
                  {api.tk(заём.outstanding)} ₭
                  <span className="note"> из {api.tk(заём.principal)} ₭ под {Number(заём.rate).toFixed(1)}%</span>
                </td>
                <td>
                  <button
                    onClick={() => го(() => session.send("bank.repay", { loan: заём.id }))}
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
          value={сколько}
          onChange={(e) => setСколько(Number(e.target.value))}
          title="сколько занять, ₭"
        />
        <button
          onClick={() => го(() => session.send("bank.borrow", { amount: сколько }))}
          disabled={busy || сколько <= 0}
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

/** Совет городов и ставка (D-087, D-172).
 *
 * Пока городов с администрацией меньше порога, ставку считает алгоритм, и это
 * окно просто говорит, сколько осталось. После порога — голос города в
 * коридоре вокруг рекомендации: Совет спорит с алгоритмом, а не заменяет его.
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
  const [совет, setСовет] = useState<any>(null);
  const [ставка, setСтавка] = useState<number | null>(null);

  useEffect(() => {
    void session.send("bank.council").then(setСовет).catch(() => setСовет(null));
  }, [session]);

  if (!совет) return null;
  if (!совет.council_decides) {
    return (
      <p className="note">
        {совет.locked_until
          ? `ставка возвращена алгоритму до ${new Date(совет.locked_until).toLocaleString()}: инфляция за тревожной чертой`
          : `ставку считает алгоритм: городов с администрацией ${совет.cities_with_hall} из ${совет.handover_at}, дальше решает Совет городов`}
      </p>
    );
  }

  const желаемая = ставка ?? Number(совет.advised);
  return (
    <div className="row">
      <input
        type="number"
        step={0.5}
        min={Number(совет.advised) - Number(совет.corridor)}
        max={Number(совет.advised) + Number(совет.corridor)}
        value={желаемая}
        onChange={(e) => setСтавка(Number(e.target.value))}
        title={`коридор ±${совет.corridor} вокруг рекомендации ${Number(совет.advised).toFixed(2)}%`}
      />
      <button
        onClick={() => act(() => session.send("bank.council_rate", { rate: желаемая }))}
        disabled={busy}
      >
        Голос города за ставку
      </button>
      <span className="note">
        алгоритм советует {Number(совет.advised).toFixed(2)}% · коридор ±
        {совет.corridor} · голос подаёт держатель права «законы» (D-172)
      </span>
    </div>
  );
}
