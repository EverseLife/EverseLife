// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The header as an instrument strip (D-238).
 *
 * Left of the divide: the wordmark, then the figures a player keeps glancing
 * at -- the balance and the planet's clock. The balance is a button, not a
 * caption: it opens a quick transfer on the spot. The frequent action lives
 * one click from the number; the full functionality stays in its own tab.
 *
 * The carried weight is not here. It stood beside the balance and opened the
 * inventory, but the inventory is a tab in the sidebar that says the same
 * figure at its top -- and the strip is for what one glances at from anywhere,
 * not for a second door into a room one is already standing in.
 *
 * Right of the divide: the body's readings -- stamina, satiety, warmth --
 * and the service row (summary, intro, refresh, sources). Account controls
 * left the header for the sidebar's "аккаунт" tab: nothing here manages
 * the account any more.
 *
 * The current node's name is not repeated here -- the scene names where you
 * are. The header only says what overrides everything: on the road, in the
 * field, asleep, and the ground about to shake.
 */

import { useEffect, useRef, useState } from "react";
import type { Frost as FrostState, Look } from "../api";
import { Refusal, useActions, useSession } from "../actions";
import { hands, stamp, worldTime } from "../clock";
import { Deadline } from "../Deadline";
import { Glyph } from "../Glyph";
import { askSidebarTab } from "../hud";
import { Logo } from "../Logo";
import { VIEWS, type View } from "../views";
import { reserveNow } from "../warmth";

//: Where the source of this build lives -- AGPL §13 asks for the source of
//: *this* version, and the repository's head is not it. `VITE_RELEASE` is
//: baked in by the image build (`Dockerfile`, CI passes `github.sha`); without
//: it -- a hand build, a dev server -- the repository is the honest answer.
const REPOSITORY = "https://github.com/EverseLife/EverseLife";
const SOURCE_URL = import.meta.env.VITE_RELEASE
  ? `${REPOSITORY}/tree/${import.meta.env.VITE_RELEASE}`
  : REPOSITORY;

type Props = {
  look: Look;
  waiting: number;
  narrow: boolean;
  onSummary: () => void;
  onIntro: () => void;
  onRefresh: () => void;
  /** The scene tabs; absent when there is no scene to switch (no body). */
  view?: View;
  onView?: (view: View) => void;
};

export function TopBar({ look, waiting, narrow, onSummary, onIntro, onRefresh, view, onView }: Props) {
  const embodied = look.body != null;
  const ongoing = Boolean(look.travel);
  const exploring = Boolean(look.survey);
  const asleep = Boolean(look.body?.sleeping_since);
  const away = ongoing || exploring;
  const fed =
    look.body?.satiated_until != null &&
    new Date(look.body.satiated_until).getTime() > Date.now();

  return (
    <header>
      <span className="brand" title="Everse.Life">
        <Logo height={26} />
      </span>

      <MoneyQuick money={look.money} />

      {look.clock && <WorldClock clock={look.clock} />}

      {!embodied && <span className="note">в облаке</span>}
      {(away || asleep) && (
        <span className="note">
          {ongoing
            ? `в пути: ${look.travel!.final ?? look.travel!.to}`
            : exploring
              ? "в разведке"
              : ""}
          {asleep ? (away ? " · спит" : "спит") : ""}
        </span>
      )}

      {/* The ground here is about to shake (D-197, P6). In the header rather
          than a place tab: the window exists to be escaped in time, and the
          clock to the tremor must be in sight from any tab. */}
      {look.node?.shaking_at && (
        <span className="trouble-inline" title="извержение: лежащее на земле сгорит, дороги перечертит, а дорога, порвавшаяся под идущим, убивает вместе с сумкой. Постройки целы: мир не стирает построенное">
          земля тронется через <Deadline until={look.node.shaking_at} label="извержение" size="row" />
        </span>
      )}

      {/* The body's readings, always on: they used to hide in a sidebar tab,
          two clicks from the player whose body was freezing. */}
      {embodied && (
        <span className="vitals">
          <span className="vital" title="выносливость: тратится трудом, возвращается сном">
            <Glyph name="stamina" />
            <b className="num">{look.body!.stamina.toFixed(1)}</b>
          </span>
          <span
            className={`vital${fed ? "" : " dim"}`}
            title={fed ? "сыт: расход выносливости ниже" : "не ел: обычный расход"}
          >
            <Glyph name="satiety" />
            <b className="num">{fed ? "сыт" : "—"}</b>
          </span>
          {/* Warmth is shown only where cold exists (D-231): on Terra there is
              no reading at all rather than an empty one. */}
          {look.frost && <Warmth frost={look.frost} />}
        </span>
      )}

      {/* On a phone the bar at the bottom chooses the zone, and a second set
          of the same choices in the header would only take the row. */}
      {!narrow && view && onView && (
        <nav className="row tabs">
          {VIEWS.map((option) => (
            <button
              key={option.id}
              className={view === option.id ? "" : "quiet"}
              aria-current={view === option.id || undefined}
              onClick={() => onView(option.id)}
              //: In-person tabs are unavailable en route and while exploring --
              //: you are not in the node (D-107, D-152).
              disabled={option.id !== "map" && away}
            >
              {option.label}
            </button>
          ))}
        </nav>
      )}

      <button
        className="quiet"
        onClick={onSummary}
        title="что произошло, пока вас не было"
      >
        сводка
        {waiting > 0 && <span className="tally alarm">{waiting}</span>}
      </button>
      {/* The intro stays within reach: once read it must not become
          unreachable, and unread it must not become mandatory (D-182). */}
      <button className="quiet" onClick={onIntro} title="кто вы и с чего начать">
        ?
      </button>
      <button className="quiet" onClick={onRefresh}>
        обновить
      </button>
      {/* The sources of this version. AGPL §13: whoever plays over the
          network must be offered them, not sent to a README. The
          machine-readable answer to the same question is `/public/source`. */}
      <a
        className="quiet"
        href={SOURCE_URL}
        target="_blank"
        rel="noopener noreferrer"
        title="исходный код этой версии"
      >
        исходники
      </a>
    </header>
  );
}

/**
 * The balance, and the transfer one click under it.
 *
 * The full statement and the bank stay in the "финансы" tab; this popover
 * carries the one financial action done many times a day. Same command, same
 * refusals -- shown in place.
 */
function MoneyQuick({ money }: { money: Look["money"] }) {
  const session = useSession();
  const acting = useActions();
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState(10);
  const [memo, setMemo] = useState("");
  const anchor = useRef<HTMLSpanElement | null>(null);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const first = useRef<HTMLInputElement | null>(null);

  //: A dialog owns the focus: opening moves it to the first field, closing by
  //: Escape hands it back to the button that opened it. Any press outside
  //: closes it too -- the popover convention of the client (`NodeMenu`, hints).
  useEffect(() => {
    if (!open) return;
    first.current?.focus();
    const onDown = (event: PointerEvent) => {
      if (anchor.current && !anchor.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        toggle.current?.focus();
      }
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const transfer = () =>
    acting.act(async () => {
      await session.send("finance.transfer", { to, amount, memo });
      setMemo("");
      setOpen(false);
    });

  return (
    <span className="hud-anchor" ref={anchor}>
      <button
        ref={toggle}
        className="bare hud"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        title="счёт — быстрый перевод"
      >
        <Glyph name="money" />
        <b className="num">{money} ₭</b>
      </button>
      {open && (
        <div className="hud-pop" role="dialog" aria-label="Быстрый перевод">
          <Refusal of={acting} />
          <div className="form">
            <label>
              <span>кому</span>
              <input
                ref={first}
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="имя личности"
              />
            </label>
            <label>
              <span>сколько, ₭</span>
              <input
                type="number"
                min={0}
                step="0.01"
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value))}
              />
            </label>
            <label>
              <span>за что</span>
              <input
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                placeholder="видно получателю и суду"
                maxLength={140}
              />
            </label>
            <button
              onClick={() => void transfer()}
              disabled={acting.busy || !to.trim() || amount <= 0}
            >
              Перевести
            </button>
            <button
              className="link"
              onClick={() => {
                setOpen(false);
                askSidebarTab("money");
              }}
            >
              выписка и кредит — в «финансах»
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

/** Local clock of the planet in the header (D-029).
 *
 * A Terran day is 38 hours, so the hands drift against the player's own clock
 * -- that drift is the point: the world lives by its own time, not by the
 * time zone of whoever is looking.
 */
function WorldClock({ clock }: { clock: NonNullable<Look["clock"]> }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    //: A world minute is a real minute: half-minute ticking is enough for the
    //: hands never to lag behind by a visible amount.
    const timer = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="clock" title={`местное время: ${stamp(clock, now)}`}>
      {/* One size for the whole reading: the day is part of the time, not a
          footnote to it. */}
      {hands(clock, now)} · сутки {worldTime(clock, now).day}
    </span>
  );
}

/**
 * The heat reserve, counted by the client (D-226, D-231).
 *
 * The server names the stamp and the rate once; the hand is drawn here, the
 * same way the planet's clock is (`warmth.ts`). Asking the server for the
 * hours every second would be a poll, and the number would still be stale
 * between two answers. The effect hangs on the **values**, not on the object:
 * a `look` that changed nothing about the cold must not restart the beat.
 */
function Warmth({ frost }: { frost: FrostState }) {
  const [hours, setHours] = useState(() => reserveNow(frost));
  useEffect(() => {
    setHours(reserveNow(frost));
    const timer = setInterval(() => setHours(reserveNow(frost)), 10_000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frost.at, frost.hours, frost.per_hour, frost.max, frost.warm]);
  const word = frost.climate === "пекло" ? "прохлада" : "тепло";
  if (hours <= 0) {
    return (
      <span
        className="vital low"
        title={`${word}: замёрзшее тело жжёт выносливость просто на времени и тратит на работу больше обычного; кончится — смерть`}
      >
        <Glyph name="warmth" />
        <b className="num">замёрз</b>
      </span>
    );
  }
  return (
    <span
      className="vital"
      title={
        frost.warm
          ? `${word}: узел обогрет, запас восполняется`
          : `${word}: узел холодный, запас тает`
      }
    >
      <Glyph name="warmth" />
      <b className="num">
        {hours.toFixed(1)} ч {frost.warm ? "↑" : "↓"}
      </b>
    </span>
  );
}
