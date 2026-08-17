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
import { Glyph } from "../Glyph";
import { Inventory } from "./Inventory";
import { Economy } from "./Economy";
import { Finance } from "./Finance";
import { Holdings } from "./Holdings";
import { Population } from "./Population";
import { Workshop } from "./Workshop";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The vault catalog -- for the "craft" tab: what is made by hand. */
  book?: any;
};

/**
 * Six tabs, not ten.
 *
 * The vault kept this open as a question -- "nine is a lot, candidates for
 * joining are inventory+character and bank+trade" -- and the measurement
 * settles it: eight labels wrapped into three rows and took 111px, 18% of the
 * sidebar's height, before a single line of content. An official had ten.
 *
 * They are joined the way a person thinks about them rather than the way the
 * engine is built: what I am, what I am doing, what I own, what I know, what I
 * keep, and the state -- if the state is any of my business.
 */
const TABS = [
  { id: "me", label: "персонаж", icon: "me", of: "персонаж: состояние, сон, счёт" },
  //: Goods left "персонаж" for a tab of their own: the inventory is a table
  //: with a menu per row, and it does not share a screen with anything.
  { id: "goods", label: "инвентарь", icon: "goods", of: "что в руках и что надето" },
  { id: "work", label: "активности", icon: "work", of: "что идёт и что можно сделать руками" },
  { id: "money", label: "финансы", icon: "money", of: "счёт, выписка, кредит, свои ордера" },
  { id: "knows", label: "знания", icon: "knows", of: "известные рецепты" },
  { id: "estate", label: "хозяйство", icon: "estate", of: "сеть, счета за быт, бумаги" },
] as const;
//: The state tab: figures for whoever governs. Shown only to office holders;
//: the same summary is visible in person in the node with the administration.
const STATE_TAB = {
  id: "state",
  label: "город",
  icon: "state",
  of: "экономика и население",
} as const;
type Tab = (typeof TABS)[number]["id"] | (typeof STATE_TAB)["id"];

export function Sidebar({ look, session, book }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [tab, setTab] = useState<Tab>("me");

  //: A state office is at least one power in a city (D-155).
  const official = (look.city?.powers?.length ?? 0) > 0;
  const tabs = official ? [...TABS, STATE_TAB] : TABS;
  const current: Tab = tabs.some((t) => t.id === tab) ? tab : "me";

  //: A counter means "there is something here to look at", so only what can be
  //: waited on is counted: works under way, and money somebody else owes or holds.
  const counts: Partial<Record<Tab, number>> = {
    work: look.batches.length + (look.travel ? 1 : 0),
    money: look.orders.length + look.reservations.length,
    goods: look.inventory.length,
  };

  return (
    <aside className="sidebar">
      <Refusal of={acting} />
      <nav className="row tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={current === t.id ? "" : "quiet"}
            onClick={() => setTab(t.id)}
            title={t.of}
          >
            <Glyph name={t.icon} />
            {t.label}
            {(counts[t.id] ?? 0) > 0 && <span className="tally">{counts[t.id]}</span>}
          </button>
        ))}
      </nav>

      {current === "me" && (
        <Character look={look} session={session} busy={busy} act={act} />
      )}
      {current === "goods" && <Inventory look={look} session={session} />}
      {/* Ручной крафт живёт в сайдбаре: верёвку вьют там, где стоят, и рабочая
          станция этому месту не нужна. Запуск всё равно присутственный: в пути
          и во сне сервер откажет. */}
      {current === "work" && (
        <>
          <Doings look={look} />
          <Workshop machine={null} book={book} look={look} session={session} />
        </>
      )}
      {current === "knows" && <Knowledge look={look} />}
      {/* Хозяйство — деньги и документы, а не материя: счета за быт и ценные
          бумаги живут в Сети (D-116, D-149). */}
      {current === "money" && (
        <>
          <Finance look={look} session={session} busy={busy} act={act} />
          <Trade look={look} session={session} busy={busy} act={act} />
        </>
      )}
      {/* Хозяйство — счета за быт, сеть и ценные бумаги: имущество, а не деньги. */}
      {current === "estate" && (
        <Holdings look={look} session={session} busy={busy} act={act} />
      )}
      {current === "state" && (
        <>
          <Economy look={look} session={session} busy={busy} />
          <Population look={look} session={session} busy={busy} />
        </>
      )}
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

      <Rule>        Личность бессмертна, тело — расходник; выносливость возвращает сон.
      </Rule>
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
      <Rule>        Длительные действия идут сами, в том числе пока вы офлайн: их двигает
        мир, а не браузер.
      </Rule>
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
          <Rule>            Забирают ногами: приезжайте в узел и выкупайте. Срок вышел — задаток
            остался продавцу, товар вернулся в стакан.
          </Rule>
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
      <Rule>        Ордером распоряжаются отсюда; товар лежит в терминале.
      </Rule>
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
      <Rule>        Знание живёт в личности и не теряется ни смертью, ни судом (И8).
      </Rule>
    </div>
  );
}



