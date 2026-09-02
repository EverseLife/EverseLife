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

import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { Batch, Look } from "../api";
import { anyOfClass } from "../classes";
import { tally } from "../amounts";
import { busyWith, CRAFT, SLEEP } from "../busy";
import { Doing } from "../Deadline";
import { Glyph, GoodsMark } from "../Glyph";
import { Account } from "./Account";
import { Inventory } from "./Inventory";
import { Finance } from "./Finance";
import { Holdings } from "./Holdings";
import { State } from "./State";
import { Net } from "./Net";
import { Workshop } from "./Workshop";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useNames, useSession } from "../actions";
import { t } from "../locale";
import { goodsKeyName, goodsName, plantName, tierName } from "../names";
import { inputsOf, stationOf } from "../recipes";
import { folded as foldedPane, onSidebarTab, pendingSidebarTab, rememberFolded } from "../hud";
import { onThread } from "../people";

/** What the inner panels (Doings, Trade) take from the sidebar's own actions. */
type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/**
 * Seven tabs on an icon rail (D-238).
 *
 * The tabs used to be a wrapping row of "icon + word" and took up to three
 * rows before a single line of content. The rail is a fixed column of marks:
 * the word moved into the open panel's title and each button's tooltip, so
 * the brief's rule -- a mark never *instead* of the label -- is kept, only
 * the label moved. Office tabs stand below a hairline: they come and go with
 * the office, and the divide says so.
 *
 * They are joined the way a person thinks about them rather than the way the
 * engine is built: my account, what I am doing, what I own, what I know, what
 * I keep, and the state -- if the state is any of my business.
 */
//: `label` and `of` are message names, not words: this list is built once when
//: the module is first imported, and the language is learnt at the greeting,
//: long afterwards. `t` is called where a tab is drawn.
const TABS = [
  //: The account, not the character (D-238): the body's readings live in the
  //: header's instrument strip, and this tab manages the account alone.
  { id: "me", label: "ui-side-tab-me", icon: "me", of: "ui-side-tab-me-of" },
  //: Goods left "персонаж" for a tab of their own: the inventory is a table
  //: with a menu per row, and it does not share a screen with anything.
  { id: "goods", label: "ui-side-tab-goods", icon: "goods", of: "ui-side-tab-goods-of" },
  { id: "work", label: "ui-side-tab-work", icon: "work", of: "ui-side-tab-work-of" },
  { id: "money", label: "ui-side-tab-money", icon: "money", of: "ui-side-tab-money-of" },
  { id: "knows", label: "ui-side-tab-knows", icon: "knows", of: "ui-side-tab-knows-of" },
  { id: "estate", label: "ui-side-tab-estate", icon: "estate", of: "ui-side-tab-estate-of" },
  //: The Net (D-222): correspondence and channels. Remote by nature -- this
  //: is the one kind of talk that works from the road.
  { id: "net", label: "ui-side-tab-net", icon: "net", of: "ui-side-tab-net-of" },
] as const;
//: The state tab: figures for whoever governs. Shown only to office holders;
//: the same summary is visible in person in the node with the administration.
const STATE_TAB = {
  id: "state",
  label: "ui-side-tab-state",
  icon: "state",
  of: "ui-side-tab-state-of",
} as const;
type Tab = (typeof TABS)[number]["id"] | (typeof STATE_TAB)["id"];

export function Sidebar({ look, onLogout }: { look: Look; onLogout: () => void }) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [tab, setTab] = useState<Tab>("me");
  //: Folded: the rail alone, the panel hidden (brief, desktop layout). Any
  //: ask to open a tab -- a mark on the rail, the header's quick buttons,
  //: "write" from a card -- opens the panel too: nobody asks for a tab in
  //: order to look at a closed door. On a phone the sidebar is a page of
  //: its own and the fold is ignored by CSS, so the state needs no guard.
  const [folded, setFolded] = useState(() => foldedPane("sidebar"));
  const fold = useCallback((next: boolean) => {
    setFolded(next);
    rememberFolded("sidebar", next);
  }, []);
  const open = useCallback(
    (name: Tab) => {
      setTab(name);
      fold(false);
    },
    [fold],
  );
  //: "Write" from somebody's card lands in the Net tab, wherever it was asked from.
  const [wanted, setWanted] = useState<string | null>(null);
  useEffect(
    () =>
      onThread((name) => {
        setWanted(name);
        open("net");
      }),
    [open],
  );
  //: The header's quick buttons open tabs here too: the carried weight opens
  //: the inventory, the balance's popover links to finance (D-238). On a
  //: narrow screen the ask switches the zone that mounts this sidebar, so at
  //: dispatch time nobody was listening -- the pending ask is collected at
  //: mount, and consumed either way so a stale one never reapplies later.
  useEffect(() => {
    const known = (name: string) =>
      TABS.some((item) => item.id === name) || name === STATE_TAB.id;
    const asked = pendingSidebarTab();
    if (asked && known(asked)) open(asked as Tab);
    return onSidebarTab((name) => {
      pendingSidebarTab();
      if (known(name)) open(name as Tab);
    });
  }, [open]);
  const forgetWanted = useCallback(() => setWanted(null), []);

  //: A state office is at least one power in a city (D-155).
  const official = (look.city?.powers?.length ?? 0) > 0;
  const tabs = official ? [...TABS, STATE_TAB] : TABS;
  const current: Tab = tabs.some((item) => item.id === tab) ? tab : "me";

  //: A counter means "there is something here to look at", so only what can be
  //: waited on is counted: works under way, and money somebody else owes or holds.
  const counts: Partial<Record<Tab, number>> = {
    work: look.batches.length + (look.doings ?? []).filter((d) => d.kind !== "craft").length,
    money: look.orders.length + look.reservations.length,
    goods: look.inventory.length,
    net: look.net_unread ?? 0,
  };

  //: The nearest running term, drawn on the "активности" tab itself -- the
  //: taskbar trick: the work is visible without opening the tab. Only spans
  //: with a known start can be drawn as a share (same rule as `Deadline`).
  const running = useMemo(() => {
    const spans: { until: string; since: string }[] = [];
    if (look.travel?.arrives_at && look.travel.started_at) {
      spans.push({ until: look.travel.arrives_at, since: look.travel.started_at });
    }
    for (const job of look.batches) {
      if (job.state === "running" && job.ready_at && job.started_at) {
        spans.push({ until: job.ready_at, since: job.started_at });
      }
    }
    if (!spans.length) return null;
    return spans.reduce((a, b) =>
      new Date(a.until).getTime() <= new Date(b.until).getTime() ? a : b,
    );
  }, [look.travel, look.batches]);

  const mark = (item: {
    id: Tab;
    label: string;
    icon: Parameters<typeof Glyph>[0]["name"];
    of: string;
  }) => {
    const count = counts[item.id] ?? 0;
    const named = t(item.label);
    return (
      <button
        key={item.id}
        className={`bare rail-tab${current === item.id ? " on" : ""}`}
        aria-pressed={current === item.id}
        //: The glyph is aria-hidden and the tally is a bare number, so without
        //: this the button's accessible name would be "3": the label names the
        //: tab, with the count, and the tally stays visual. The count goes in
        //: as a string: the tally beside it is drawn raw.
        aria-label={
          count > 0
            ? t("ui-side-tab-counted", { tab: named, n: String(count) })
            : named
        }
        onClick={() => open(item.id)}
        title={t("ui-side-tab-title", { tab: named, about: t(item.of) })}
      >
        <Glyph name={item.icon} />
        {count > 0 && (
          <span className="tally" aria-hidden="true">
            {count}
          </span>
        )}
        {item.id === "work" && running && (
          <RailProgress until={running.until} since={running.since} />
        )}
      </button>
    );
  };

  return (
    <aside className={`sidebar${folded ? " folded" : ""}`}>
      {/* The rail: marks only, every tab pinned to the top. The word lives in
          the panel's title and in the tooltip, not nowhere (the brief's rule,
          moved rather than dropped). */}
      <nav className="rail" aria-label={t("ui-side-rail")}>
        {TABS.map(mark)}
        {official && (
          <>
            <span className="rail-line" aria-hidden="true" />
            {mark(STATE_TAB)}
          </>
        )}
        {/* The fold, at the foot of the rail: the panel goes, the marks stay,
            and a mark opens it again. Desktop only -- on a phone the rail
            lies across the top and there is nothing beside it to fold. */}
        <button
          className="bare rail-fold"
          aria-expanded={!folded}
          aria-controls="side-body"
          aria-label={t(folded ? "ui-side-unfold" : "ui-side-fold")}
          title={t(folded ? "ui-side-unfold" : "ui-side-fold")}
          onClick={() => fold(!folded)}
        >
          <Glyph name="fold" />
        </button>
      </nav>

      <div className="side-body" id="side-body">
        <Refusal of={acting} />
        <h3 className="side-title">{t(tabs.find((item) => item.id === current)!.label)}</h3>

        {current === "me" &&
          (look.profile ? (
            <Account profile={look.profile} onLogout={onLogout} />
          ) : (
            <p className="note">{t("ui-side-no-account")}</p>
          ))}
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
        {current === "state" && <State look={look} busy={busy} />}
      </div>
    </aside>
  );
}

/**
 * The share of a running term on the rail's tab, one-second beat like the
 * deadline bar it borrows the language of. Hidden from readers on purpose:
 * the tally and the tab's title carry the same news in words.
 */
function RailProgress({ until, since }: { until: string; since: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const ends = new Date(until).getTime();
  const starts = new Date(since).getTime();
  if (!(ends > starts)) return null;
  const share = Math.min(1, Math.max(0, (ends - now) / (ends - starts)));
  return (
    <span className="rail-progress" aria-hidden="true">
      <i style={{ width: `${(share * 100).toFixed(1)}%` }} />
    </span>
  );
}

function Slept({ since }: { since: string }) {
  const [minutes, setMinutes] = useState(() => elapsedMinutes(since));
  useEffect(() => {
    const timer = setInterval(() => setMinutes(elapsedMinutes(since)), 10_000);
    return () => clearInterval(timer);
  }, [since]);
  //: The figures go in as strings: they are read beside other raw ones, and
  //: the locale's number format would space out a four-digit count of minutes.
  if (minutes < 1) return <b>{t("ui-side-slept-under-minute")}</b>;
  if (minutes < 60) return <b>{t("ui-side-slept-minutes", { n: String(Math.floor(minutes)) })}</b>;
  return <b>{t("ui-side-slept-hours", { n: (minutes / 60).toFixed(1) })}</b>;
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
  const names = useNames();
  const doings = look.doings ?? [];
  const asleep = doings.some((d) => d.kind === "sleep");
  //: A bed is a thing class (D-215): the engine sleeps in any member of it.
  const bed_ = anyOfClass(book, api.stationsOf(look), "bed");
  //: What stands in the way of lying down: any occupation but sleep itself and
  //: a batch -- a batch freezes with the master and frees its machine (D-211).
  const cannotSleep = busyWith(look, [SLEEP, CRAFT]);
  //: Стопка кнопок «закончить»: у каждого занятия своя команда, и нет её
  //: только там, где прерывать нечего.
  const ends: Record<string, { cmd: string; label: string; why: string }> = {
    sleep: { cmd: "rest.wake", label: t("ui-side-end-sleep"), why: t("ui-side-end-sleep-why") },
    forage: { cmd: "forage.stop", label: t("ui-side-end-forage"), why: t("ui-side-end-forage-why") },
    field: { cmd: "explore.cancel", label: t("ui-side-end-field"), why: t("ui-side-end-field-why") },
    mine: { cmd: "mine.leave", label: t("ui-side-end-mine"), why: t("ui-side-end-mine-why") },
  };
  const empty = look.batches.length === 0 && doings.length === 0;
  const title = (job: Batch) =>
    job.work === "make"
      ? job.recipe
        ? t("ui-side-batch-make", {
            output: goodsName(names, job.output),
            recipe: goodsName(names, job.recipe),
          })
        : goodsName(names, job.output)
      : job.work === "repair"
        ? t("ui-side-batch-repair", { goods: goodsName(names, job.output) })
        : t("ui-side-batch-melt", { goods: goodsName(names, job.output) });
  //: A waiting batch says why in words (D-209): the reason decides what the
  //: player does next -- wait, walk back, or free a machine.
  const why = (job: Batch) =>
    job.waiting === "queued"
      ? t("ui-side-batch-queued")
      : job.waiting === "away"
        ? t("ui-side-batch-away", { node: job.node ?? "?" })
        : t("ui-side-batch-no-station");
  const left = (job: Batch) =>
    job.left_seconds == null
      ? ""
      : job.left_seconds < 60
        ? t("ui-side-batch-left-soon")
        : t("ui-side-batch-left", { n: (job.left_seconds / 60).toFixed(0) });
  //: The two halves used to be concatenated with a " · " written in the code;
  //: the separator is the message's now, so a language may punctuate its own way.
  const aside = (job: Batch) => {
    const rest = left(job);
    return rest ? t("ui-side-batch-aside", { why: why(job), left: rest }) : why(job);
  };
  const anyRunning = look.batches.some((job) => job.state === "running");
  return (
    <div>
      <h3>
        {t("ui-side-doings")}
        <Rule>{t("ui-side-doings-rule")}</Rule>
      </h3>
      {look.travel && (
        <Doing
          what={t("ui-side-travel", { to: look.travel.final ?? look.travel.to })}
          until={look.travel.arrives_at}
          since={look.travel.started_at}
          aside={look.travel.final ? t("ui-side-travel-next") : undefined}
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
          //: `d.title` and `d.what` arrive already written out, in the reader's
          //: own language: the engine names the occupation and its `i18n`
          //: renders it. Ours is only the colon between them.
          const said = t("ui-side-doing", { title: d.title, what: d.what });
          return d.until ? (
            <Doing key={d.kind} what={said} until={d.until}>
              {button}
            </Doing>
          ) : (
            <div className="doing" key={d.kind}>
              <span className="doing-what">{said}</span>
              {d.kind === "sleep" && look.body?.sleeping_since && (
                <span className="doing-aside note">
                  {/* Two messages rather than one: the counter between them is a
                      live component, and folding it into an argument would cost
                      the emphasis the duration is drawn with. */}
                  {t("ui-side-sleeping-for")} <Slept since={look.body.sleeping_since} />{" "}
                  {t("ui-side-sleeping-credited")}
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
            aside={
              job.work === "make"
                ? t("ui-side-batch-quality", { n: job.quality.toFixed(0) })
                : undefined
            }
          />
        ) : (
          <div className="doing" key={job.id}>
            <span className="doing-what">{title(job)}</span>
            <span className="doing-aside note">{aside(job)}</span>
            {job.waiting === "no_station" && !anyRunning && (
              <span className="doing-act">
                <button
                  className="quiet"
                  onClick={() => act(() => session.send("craft.resume"))}
                  disabled={busy}
                  title={t("ui-side-batch-resume-title")}
                >
                  {t("ui-side-batch-resume")}
                </button>
              </span>
            )}
          </div>
        ),
      )}
      {empty && <p className="note">{t("ui-side-doings-none")}</p>}

      {/* Привал стоит здесь же и последним: лечь спать — такое же занятие,
          как остальные, и начинают его там, где их заканчивают (D-211). */}
      {look.body != null && !asleep && (
        <div className="row">
          <button
            onClick={() => act(() => session.send("rest.sleep"))}
            disabled={busy || cannotSleep !== null}
            //: What forbids lying down arrives from `busyWith`, already written
            //: out; only the standing invitation is ours.
            title={cannotSleep ?? t("ui-side-sleep-title")}
          >
            {t("ui-side-sleep", { bed: String(Boolean(bed_)) })}
          </button>
          <span className="note">
            {cannotSleep ?? t("ui-side-sleep-note", { bed: String(Boolean(bed_)) })}
          </span>
        </div>
      )}
    </div>
  );
}

function Trade({ look, busy, act }: Props) {
  const session = useSession();
  const names = useNames();
  return (
    <div>
      {/* Бронь — единственный способ купить удалённо, и она с часами:
          не забрал в срок — задаток остаётся продавцу (D-047). */}
      {look.reservations.length > 0 && (
        <>
          <h3>
            {t("ui-side-reservations")}
            <Rule>{t("ui-side-reservations-rule")}</Rule>
          </h3>
          {look.reservations.map((reservation) => (
            <Doing
              key={reservation.id}
              what={t("ui-side-reservation", {
                goods: goodsKeyName(names, reservation.goods),
                tier: tierName(names, reservation.tier),
              })}
              until={reservation.expires_at}
              since={reservation.placed_at}
              //: The money is already spelled by `tk`, and the amount is drawn
              //: raw everywhere else in the row: both go in as strings.
              aside={t("ui-side-reservation-aside", {
                amount: String(reservation.amount),
                price: api.tk(reservation.price),
                node: reservation.node,
                deposit: api.tk(reservation.deposit),
              })}
            />
          ))}
        </>
      )}

      <h3>
        {t("ui-side-orders")}
        <Rule>{t("ui-side-orders-rule")}</Rule>
      </h3>
      {look.orders.length === 0 ? (
        <p className="note">{t("ui-side-orders-none")}</p>
      ) : (
        look.orders.map((order) => (
          <div className="row" key={order.id}>
            {/* `side` is the wire's own word, and the variant is keyed by it:
                a variant key is an identifier, never a sentence chosen here. */}
            <span>
              {t("ui-side-order", {
                side: order.side,
                goods: goodsKeyName(names, order.goods),
                //: A buy's floor named by hand stands beside the tier: the
                //: wire carries it only when the tier alone cannot say it
                //: (D-239, D-225), so a bare tier needs no suffix.
                tier:
                  order.min_quality != null
                    ? t("ui-market-order-floor", {
                        tier: tierName(names, order.tier),
                        floor: String(order.min_quality),
                      })
                    : tierName(names, order.tier),
                left: String(order.left),
                price: api.tk(order.price),
              })}
            </span>
            <button
              className="quiet"
              onClick={() => act(() => session.send("market.cancel", { order: order.id }))}
              disabled={busy}
            >
              {t("ui-side-order-cancel")}
            </button>
          </div>
        ))
      )}
    </div>
  );
}

function Knowledge({ look }: { look: Look }) {
  const book = useBook();
  const names = useNames();
  const discovered = new Set(look.discovered ?? []);
  //: One recipe open at a time: the eye toggles the row's details in place.
  const [shown, setShown] = useState<string | null>(null);
  const agrotech = look.agrotech ?? [];
  //: The details come from the book already loaded (D-225): the station and
  //: the inputs are the vault catalog's, nothing is asked over. The ladder
  //: step is not among them: the glossary bans "level", and the engine reads
  //: nothing off it -- the station and the inputs already say the same.
  const details = (id: string) =>
    (book?.recipes ?? []).find((recipe) => (recipe.id ?? recipe.name) === id);
  return (
    <div>
      <h3>
        {t("ui-side-recipes")}
        <Rule>{t("ui-side-recipes-rule")}</Rule>
      </h3>
      {look.knows.length === 0 ? (
        <p className="note">{t("ui-side-recipes-none")}</p>
      ) : (
        look.knows.map((name) => {
          const recipe = shown === name ? details(name) : undefined;
          //: `stationOf`, not the raw field: the vault writes `by_hand` into
          //: `station` for handwork, and only `canon` knows that word.
          const station = recipe ? stationOf(book, name) : null;
          const inputs = recipe ? inputsOf(book, name) : [];
          return (
            <div key={name}>
              <p>
                <GoodsMark book={book} goods={name} />
                {goodsName(names, name)}
                {discovered.has(name) && (
                  <span className="note" title={t("ui-side-recipe-discovered")}>
                    {" "}✦
                  </span>
                )}
                <button
                  className="bare peek"
                  aria-label={t("ui-side-recipe-details", { recipe: goodsName(names, name) })}
                  aria-expanded={shown === name}
                  title={t("ui-side-recipe-details", { recipe: goodsName(names, name) })}
                  onClick={() => setShown(shown === name ? null : name)}
                >
                  <Glyph name="eye" />
                </button>
              </p>
              {recipe && (
                <div className="note recipe-peek">
                  <div>
                    {station
                      ? t("ui-side-recipe-station", { station: goodsName(names, station) })
                      : t("ui-side-recipe-by-hand")}
                  </div>
                  {inputs.length > 0 && (
                    <div>
                      {t("ui-side-recipe-inputs", {
                        inputs: inputs
                          .map((input, at) => {
                            //: `inputsOf` resolves synonyms in order, while
                            //: `amounts` is keyed by the recipe's raw input
                            //: names -- so the raw name is asked too.
                            const amount =
                              recipe.amounts?.[input] ?? recipe.amounts?.[recipe.inputs[at]];
                            //: The quantity is a detail after the separator
                            //: (D-258): the name stays in the nominative.
                            return amount
                              ? `${goodsName(names, input)} · ${tally(input, amount)}`
                              : goodsName(names, input);
                          })
                          .join(", "),
                      })}
                    </div>
                  )}
                  <div>
                    {discovered.has(name)
                      ? t("ui-side-recipe-discovered")
                      : t("ui-side-recipe-source-learned")}
                  </div>
                  {/* The plaque (D-064, D-259): the first discoverer's name,
                      bound to the recipe forever. Founding recipes have none. */}
                  {look.pioneers?.[name] && (
                    <div>{t("ui-side-recipe-pioneer", { name: look.pioneers[name] })}</div>
                  )}
                </div>
              )}
            </div>
          );
        })
      )}

      {/* Agrotech beside the recipes (D-057): the second kind of knowledge the
          identity keeps. Taken in the Library; the tick there and this list
          are one and the same fact. */}
      <h3>
        {t("ui-side-agrotech")}
        <Rule>{t("ui-side-agrotech-rule")}</Rule>
      </h3>
      {agrotech.length === 0 ? (
        <p className="note">{t("ui-side-agrotech-none")}</p>
      ) : (
        agrotech.map((key) => (
          <p key={key}>
            <span className="goods-mark">
              <Glyph name="plant" />
            </span>
            {plantName(names, key)}
          </p>
        ))
      )}
    </div>
  );
}



