// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

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
import type { Batch, Look } from "../api";
import { anyOfClass } from "../classes";
import { busyWith, CRAFT, SLEEP } from "../busy";
import { Doing } from "../Deadline";
import { Glyph } from "../Glyph";
import { Inventory } from "./Inventory";
import { Economy } from "./Economy";
import { Finance } from "./Finance";
import { Holdings } from "./Holdings";
import { Net } from "./Net";
import { Population } from "./Population";
import { Workshop } from "./Workshop";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useSession } from "../actions";
import { onThread } from "../people";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The vault catalog -- for the "craft" tab: what is made by hand. */
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
  { id: "me", label: "персонаж", icon: "me", of: "персонаж: состояние и счёт" },
  //: Goods left "персонаж" for a tab of their own: the inventory is a table
  //: with a menu per row, and it does not share a screen with anything.
  { id: "goods", label: "инвентарь", icon: "goods", of: "что в руках и что надето" },
  { id: "work", label: "активности", icon: "work", of: "что идёт, чем это закончить, и что можно сделать руками" },
  { id: "money", label: "финансы", icon: "money", of: "счёт, выписка, кредит, свои ордера" },
  { id: "knows", label: "знания", icon: "knows", of: "известные рецепты" },
  { id: "estate", label: "хозяйство", icon: "estate", of: "сеть, счета за быт, бумаги" },
  //: The Net (D-222): correspondence and channels. Remote by nature -- this
  //: is the one kind of talk that works from the road.
  { id: "net", label: "сеть", icon: "net", of: "переписка и каналы" },
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

export function Sidebar({ look }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [tab, setTab] = useState<Tab>("me");
  //: "Write" from somebody's card lands in the Net tab, wherever it was asked from.
  const [wanted, setWanted] = useState<string | null>(null);
  useEffect(
    () =>
      onThread((name) => {
        setWanted(name);
        setTab("net");
      }),
    [],
  );
  const forgetWanted = useCallback(() => setWanted(null), []);

  //: A state office is at least one power in a city (D-155).
  const official = (look.city?.powers?.length ?? 0) > 0;
  const tabs = official ? [...TABS, STATE_TAB] : TABS;
  const current: Tab = tabs.some((t) => t.id === tab) ? tab : "me";

  //: A counter means "there is something here to look at", so only what can be
  //: waited on is counted: works under way, and money somebody else owes or holds.
  const counts: Partial<Record<Tab, number>> = {
    work: look.batches.length + (look.doings ?? []).filter((d) => d.kind !== "craft").length,
    money: look.orders.length + look.reservations.length,
    goods: look.inventory.length,
    net: look.net_unread ?? 0,
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

      {current === "me" && <Character look={look} />}
      {current === "goods" && <Inventory look={look} />}
      {/* Ручной крафт живёт в сайдбаре: верёвку вьют там, где стоят, и рабочая
          станция этому месту не нужна. Запуск всё равно присутственный: в пути
          и во сне сервер откажет. */}
      {current === "work" && (
        <>
          <Doings look={look} busy={busy} act={act} />
          <Workshop machine={null} look={look} />
        </>
      )}
      {current === "knows" && <Knowledge look={look} />}
      {/* Хозяйство — деньги и документы, а не материя: счета за быт и ценные
          бумаги живут в Сети (D-116, D-149). */}
      {current === "money" && (
        <>
          <Finance look={look} busy={busy} act={act} />
          <Trade look={look} busy={busy} act={act} />
        </>
      )}
      {/* Хозяйство — счета за быт, сеть и ценные бумаги: имущество, а не деньги. */}
      {current === "estate" && (
        <Holdings look={look} busy={busy} act={act} />
      )}
      {current === "net" && (
        <Net
          unread={look.net_unread ?? 0}
          wanted={wanted}
          onWanted={forgetWanted}
        />
      )}
      {current === "state" && (
        <>
          <Economy look={look} busy={busy} />
          <Population look={look} busy={busy} />
        </>
      )}
    </aside>
  );
}

function Character({ look }: Pick<Props, "look">) {
  const sleepingSince = look.body?.sleeping_since ?? null;
  const fed =
    look.body?.satiated_until != null &&
    new Date(look.body.satiated_until).getTime() > Date.now();
  return (
    <div>
      <p className="sign">
        {look.identity}
        <Rule>
          Личность бессмертна, тело — расходник; выносливость возвращает сон.
        </Rule>
      </p>
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
              {look.body == null
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

      {/* Привал переехал в «дела» (D-211): сон — такое же занятие, как поиск и
          вспашка, и начинают их в одном месте, а не по разным окнам. */}
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

/**
 * Everything the body is at, and the one place it is stopped (D-211).
 *
 * Before this the sleep button lived by the character, a search was ended in
 * the window it was started from, and a run in the field on the map -- so
 * "what am I even doing" was a hunt through the interface. The server names
 * every occupation (`look.doings`); the list draws them in one column, each
 * with what ends it where anything can.
 *
 * The road and the plough have no button on purpose: a road is walked to its
 * end, and a plough is not thrown half-way. Batches keep their own rows below
 * -- they carry a queue, a reason for waiting and a quality, and none of that
 * fits one line.
 */
function Doings({ look, busy, act }: Props) {
  const session = useSession();
  const book = useBook();
  const doings = look.doings ?? [];
  const asleep = doings.some((d) => d.kind === "sleep");
  //: A bed is a thing class (D-215): the engine sleeps in any member of it.
  const bed_ = anyOfClass(book, api.stationsOf(look), "Кровать");
  //: What stands in the way of lying down: any occupation but sleep itself and
  //: a batch -- a batch freezes with the master and frees its machine (D-211).
  const cannotSleep = busyWith(look, [SLEEP, CRAFT]);
  //: Стопка кнопок «закончить»: у каждого занятия своя команда, и нет её
  //: только там, где прерывать нечего.
  const ends: Record<string, { cmd: string; label: string; why: string }> = {
    sleep: { cmd: "rest.wake", label: "Проснуться", why: "выносливость начислится при пробуждении" },
    forage: { cmd: "forage.stop", label: "Закончить", why: "потраченные силы не вернутся" },
    field: { cmd: "explore.cancel", label: "Вернуться", why: "заход прервётся, находки не будет" },
    mine: { cmd: "mine.leave", label: "Выйти из забоя", why: "добытое уйдёт в руки" },
  };
  const empty = look.batches.length === 0 && doings.length === 0;
  const title = (job: Batch) =>
    job.work === "make"
      ? job.recipe
        ? `${job.output}: ${job.recipe}`
        : job.output
      : job.work === "repair"
        ? `починка: ${job.output}`
        : `переработка: ${job.output}`;
  //: A waiting batch says why in words (D-209): the reason decides what the
  //: player does next -- wait, walk back, or free a machine.
  const why = (job: Batch) =>
    job.waiting === "queued"
      ? "в очереди"
      : job.waiting === "away"
        ? `замерла: вернитесь в «${job.node ?? "?"}»`
        : "ждёт свободной станции";
  const left = (job: Batch) =>
    job.left_seconds == null
      ? ""
      : job.left_seconds < 60
        ? " · меньше минуты работы"
        : ` · ещё ${(job.left_seconds / 60).toFixed(0)} мин работы`;
  const anyRunning = look.batches.some((job) => job.state === "running");
  return (
    <div>
      <h3>
        Дела
        <Rule>
          Тело делает одно дело за раз: спит, ищет, пашет, разведывает, идёт или
          работает у станции. Всё, что идёт, видно здесь, и здесь же
          заканчивается — искать окно, из которого дело начато, не нужно. Дорога
          идёт сама, в том числе пока вы офлайн. Партия идёт, только пока вы
          стоите у станции: ушли или легли спать — замерла, вернулись —
          продолжилась. У одного человека идёт одна работа, остальные ждут
          очереди в порядке запуска.
        </Rule>
      </h3>
      {look.travel && (
        <Doing
          what={`в пути: ${look.travel.final ?? look.travel.to}`}
          until={look.travel.arrives_at}
          since={look.travel.started_at}
          aside={look.travel.final ? "до следующего узла" : undefined}
        />
      )}

      {/* Занятия тела: сон, поиск, вспашка, разведка, забой. Дорога уже
          показана выше со своей целью, партии — ниже со своей очередью. */}
      {doings
        .filter((d) => d.kind !== "road" && d.kind !== "craft")
        .map((d) => {
          const end = ends[d.kind];
          const button = end && (
            <button
              className="quiet"
              onClick={() => act(() => session.send(end.cmd))}
              disabled={busy}
              title={end.why}
            >
              {end.label}
            </button>
          );
          return d.until ? (
            <Doing key={d.kind} what={`${d.title}: ${d.what}`} until={d.until}>
              {button}
            </Doing>
          ) : (
            <div className="doing" key={d.kind}>
              <span className="doing-what">
                {d.title}: {d.what}
              </span>
              {d.kind === "sleep" && look.body?.sleeping_since && (
                <span className="doing-aside note">
                  спит уже <Slept since={look.body.sleeping_since} /> · начислится
                  при пробуждении
                </span>
              )}
              {button && <span className="doing-act">{button}</span>}
            </div>
          );
        })}
      {look.batches.map((job) =>
        job.state === "running" && job.ready_at ? (
          <Doing
            key={job.id}
            what={title(job)}
            until={job.ready_at}
            since={job.started_at}
            aside={job.work === "make" ? `качество ${job.quality.toFixed(0)}` : undefined}
          />
        ) : (
          <div className="doing" key={job.id}>
            <span className="doing-what">{title(job)}</span>
            <span className="doing-aside note">
              {why(job)}
              {left(job)}
            </span>
            {job.waiting === "no_station" && !anyRunning && (
              <span className="doing-act">
                <button
                  className="quiet"
                  onClick={() => act(() => session.send("craft.resume"))}
                  disabled={busy}
                  title="станция освободилась — продолжить"
                >
                  Продолжить
                </button>
              </span>
            )}
          </div>
        ),
      )}
      {empty && <p className="note">ничего не идёт</p>}

      {/* Привал стоит здесь же и последним: лечь спать — такое же занятие,
          как остальные, и начинают его там, где их заканчивают (D-211). */}
      {look.body != null && !asleep && (
        <div className="row">
          <button
            onClick={() => act(() => session.send("rest.sleep"))}
            disabled={busy || cannotSleep !== null}
            title={cannotSleep ?? "лечь там, где стоите: выносливость начислится при пробуждении"}
          >
            {bed_ ? "Лечь в кровать" : "Лечь спать"}
          </button>
          <span className="note">
            {cannotSleep
              ? cannotSleep
              : bed_
                ? "кровать здесь: сон быстрее"
                : "кровати нет: сон медленнее"}
          </span>
        </div>
      )}
    </div>
  );
}

function Trade({ look, busy, act }: Omit<Props, "book">) {
  const session = useSession();
  return (
    <div>
      {/* Бронь — единственный способ купить удалённо, и она с часами:
          не забрал в срок — задаток остаётся продавцу (D-047). */}
      {look.reservations.length > 0 && (
        <>
          <h3>
            Брони
            <Rule>
              Забирают ногами: приезжайте в узел и выкупайте. Срок вышел — задаток
              остался продавцу, товар вернулся в стакан.
            </Rule>
          </h3>
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
        </>
      )}

      <h3>
        Ордера
        <Rule>
          Ордером распоряжаются отсюда; товар лежит в терминале.
        </Rule>
      </h3>
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
    </div>
  );
}

function Knowledge({ look }: { look: Look }) {
  const discovered = new Set(look.discovered ?? []);
  return (
    <div>
      <h3>
        Рецепты
        <Rule>
          Знание живёт в личности и не теряется ни смертью, ни судом (И8). Берут в
          Библиотеке, читают с носителя «Рецепт» или открывают сами — у станции, без
          рецепта. Своё открытие помечено ✦.
        </Rule>
      </h3>
      {look.knows.length === 0 ? (
        <p className="note">
          пока ничего: рецепты берут в Библиотеке, читают с носителя или открывают сами
        </p>
      ) : (
        look.knows.map((name) => (
          <p key={name}>
            {name}
            {discovered.has(name) && (
              <span className="note" title="открыт вами: первооткрыватель">
                {" "}✦
              </span>
            )}
          </p>
        ))
      )}
    </div>
  );
}



