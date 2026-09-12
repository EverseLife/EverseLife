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
 * and the service row (the summary and the music). Account controls left
 * the header for the sidebar's "аккаунт" tab, and so did the intro and the
 * sources: nothing here manages the account any more.
 *
 * The current node's name is not repeated here -- the scene names where you
 * are. The header only says what overrides everything: on the road, in the
 * field, asleep, and the ground about to shake.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Air, Frost as FrostState, Look } from "../api";
import { Refusal, useActions, useSession } from "../actions";
import { hands, stamp, worldTime } from "../clock";
import { Deadline } from "../Deadline";
import { Glyph } from "../Glyph";
import { askSidebarTab } from "../hud";
import { t } from "../locale";
import { Logo } from "../Logo";
import { setVolume, useMusic } from "../music";
import { term } from "./map/orbits";
import { usePopover } from "../popover";
import { VIEWS, type View } from "../views";
import { leftNow, reserveNow } from "../warmth";
import { NumberField } from "../NumberField";

type Props = {
  look: Look;
  waiting: number;
  narrow: boolean;
  onSummary: () => void;
  /** The scene tabs; absent when there is no scene to switch (no body). */
  view?: View;
  onView?: (view: View) => void;
};

export function TopBar({ look, waiting, narrow, onSummary, view, onView }: Props) {
  const embodied = look.body != null;
  const ongoing = Boolean(look.travel);
  const asleep = Boolean(look.body?.sleeping_since);
  const away = ongoing;
  const fed =
    look.body?.satiated_until != null &&
    new Date(look.body.satiated_until).getTime() > Date.now();
  //: Where the body is, when that overrides everything else. Empty exactly
  //: when `away` is false, which is what lets sleep be joined onto it.
  const where = ongoing ? t("ui-top-travel", { to: look.travel!.final ?? look.travel!.to }) : "";

  return (
    <header>
      <span className="brand" title="everse.life">
        <Logo height={26} />
      </span>

      <MoneyQuick money={look.money} />

      {look.clock && <WorldClock clock={look.clock} />}

      {!embodied && <span className="note">{t("ui-top-cloud")}</span>}
      {(away || asleep) && (
        <span className="note">
          {asleep ? (away ? t("ui-top-away-asleep", { where }) : t("ui-top-asleep")) : where}
        </span>
      )}

      {/* The ground here is about to shake (D-197, P6). In the header rather
          than a place tab: the window exists to be escaped in time, and the
          clock to the tremor must be in sight from any tab. */}
      {look.node?.shaking_at && (
        <span className="trouble-inline" title={t("ui-top-shaking-title")}>
          {t("ui-top-shaking")}{" "}
          <Deadline until={look.node.shaking_at} label={t("ui-top-eruption")} size="row" />
        </span>
      )}

      {/* The body's readings, always on: they used to hide in a sidebar tab,
          two clicks from the player whose body was freezing. */}
      {embodied && (
        <span className="vitals">
          <span className="vital" title={t("ui-top-stamina")}>
            <Glyph name="stamina" />
            <b className="num">{look.body!.stamina.toFixed(1)}</b>
          </span>
          <span
            className={`vital${fed ? "" : " dim"}`}
            title={t("ui-top-satiety-title", { fed: String(fed) })}
          >
            <Glyph name="satiety" />
            <b className="num">{t("ui-top-satiety", { fed: String(fed) })}</b>
          </span>
          {/* Warmth is shown only where cold exists (D-231): on Terra there is
              no reading at all rather than an empty one. */}
          {look.frost && <Warmth frost={look.frost} />}
          {/* The air stands beside the cold because it is the same kind of
              thing: a scale that only exists where the planet says so, and one
              that kills when it runs out (D-233). */}
          {look.air && <Breath air={look.air} />}
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

      {/* The service row: the summary and the music on a desktop, one button
          on a phone. The strip there is 375px wide, and framed boxes took two
          lines of it -- a third of the screen went to the header before the
          first line of the world. Behind the overflow they are the same two,
          and the summary's count rides the button so that "something is
          waiting" is still read without opening it. The intro, the refresh
          and the sources left the strip (owner, 2026-09-12): the intro and
          the sources live in the account tab, the refresh is gone -- the
          server speaks (D-226). */}
      {narrow ? (
        <More waiting={waiting} onSummary={onSummary} />
      ) : (
        <>
          <button
            className="quiet"
            onClick={onSummary}
            title={t("ui-top-summary-title")}
          >
            {t("ui-top-summary")}
            {waiting > 0 && <span className="tally alarm">{waiting}</span>}
          </button>
          <MusicQuick />
        </>
      )}
    </header>
  );
}

/** The slider alone: nought is off, and the mark says so (D-333). */
function MusicSlider() {
  const music = useMusic();
  return (
    <label className="slider">
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={Math.round(music.volume * 100)}
        aria-label={t("ui-top-music-volume")}
        onChange={(e) => setVolume(Number(e.target.value) / 100)}
      />
    </label>
  );
}

/**
 * The music, one click from anywhere (D-333).
 *
 * A framed note in the strip, like the summary beside it; under it the
 * slider and nothing else. The setting is the browser's (D-298), and the
 * slider's left end is the switch: nought is off.
 */
function MusicQuick() {
  const music = useMusic();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLSpanElement | null>(null);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  usePopover({ open, close, anchor, toggle, pop });
  const off = music.volume === 0;

  return (
    <span className="hud-anchor" ref={anchor}>
      <button
        ref={toggle}
        className="quiet"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        aria-label={t("ui-top-music")}
        title={off ? t("ui-top-music-off-title") : t("ui-top-music")}
      >
        <Glyph name={off ? "music-off" : "music"} />
      </button>
      {open && (
        <div ref={pop} className="hud-pop end" role="dialog" aria-label={t("ui-top-music")}>
          <MusicSlider />
        </div>
      )}
    </span>
  );
}

/**
 * The phone's overflow: the service row behind one mark (brief section 9).
 *
 * A menu, not a second toolbar -- the same layer the inventory's row handle
 * opens, hanging off the strip's right edge. The music is a line of it with
 * the slider in place: a second layer under a menu is one tap too many.
 */
function More({ waiting, onSummary }: Pick<Props, "waiting" | "onSummary">) {
  const music = useMusic();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLSpanElement | null>(null);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  usePopover({ open, close, anchor, toggle, pop });
  //: Choosing unmounts the layer, and the focus with it: it goes back to the
  //: button it came from, as it does on Escape -- a keyboard must never be
  //: left pointing at nothing.
  const pick = (what: () => void) => () => {
    setOpen(false);
    toggle.current?.focus();
    what();
  };

  return (
    <span className="hud-anchor more" ref={anchor}>
      <button
        ref={toggle}
        className="bare hud"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={t("ui-top-more")}
        title={t("ui-top-more")}
      >
        <Glyph name="more" />
        {waiting > 0 && <span className="tally alarm">{waiting}</span>}
      </button>
      {open && (
        <div ref={pop} className="menu hud-menu" role="menu" aria-label={t("ui-top-more")}>
          <button role="menuitem" onClick={pick(onSummary)} title={t("ui-top-summary-title")}>
            {t("ui-top-summary")}
            {waiting > 0 && <span className="tally alarm">{waiting}</span>}
          </button>
          {/* Not a menu item: the slider inside has its own keys, and a
              role on the wrapper would promise a screen reader a line that
              is not one. The arrows reach it through `usePopover`. */}
          <div className="menu-music">
            <Glyph name={music.volume === 0 ? "music-off" : "music"} />
            <MusicSlider />
          </div>
        </div>
      )}
    </span>
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
  //: The form, not the whole layer: the focus goes to the first field, and a
  //: refusal standing above the form must not take it instead.
  const form = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  usePopover({ open, close, anchor, toggle, pop: form });

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
        title={t("ui-top-money-title")}
      >
        <Glyph name="money" />
        {/* The balance arrives from the wire already spelled (`tk`), so it goes
            in as the string it is rather than through the locale's numbers. */}
        <b className="num">{t("ui-top-money", { money: String(money) })}</b>
      </button>
      {open && (
        <div className="hud-pop" role="dialog" aria-label={t("ui-top-transfer")}>
          <Refusal of={acting} />
          <div className="form" ref={form}>
            <label>
              <span>{t("ui-top-transfer-to")}</span>
              <input
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder={t("ui-top-transfer-to-hint")}
              />
            </label>
            <label>
              <span>{t("ui-top-transfer-amount")}</span>
              <NumberField
                min={0}
                step="0.01"
                value={amount}
                onChange={(typed) => setAmount(typed ?? 0)}
              />
            </label>
            <label>
              <span>{t("ui-top-transfer-memo")}</span>
              <input
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                placeholder={t("ui-top-transfer-memo-hint")}
                maxLength={140}
              />
            </label>
            <button
              onClick={() => void transfer()}
              disabled={acting.busy || !to.trim() || amount <= 0}
            >
              {t("ui-top-transfer-send")}
            </button>
            <button
              className="link"
              onClick={() => {
                setOpen(false);
                askSidebarTab("money");
              }}
            >
              {t("ui-top-transfer-more")}
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
    <span className="clock" title={t("ui-top-clock-title", { stamp: stamp(clock, now) })}>
      {/* One size for the whole reading: the day is part of the time, not a
          footnote to it. The day goes in as a string: it is a counter, and the
          thousandth day must not read as "1 000". */}
      {t("ui-top-clock", {
        hands: hands(clock, now),
        day: String(worldTime(clock, now).day),
      })}
    </span>
  );
}

/**
 * The air, counted by the client exactly as the cold is (D-226, D-233).
 *
 * Units rather than hours, because that is what the reserve is: a liquid in a
 * cylinder or in the ship's tanks. The rate turns it into hours, and the rate
 * is the server's -- what the hull makes against what its crew breathes.
 */
function Breath({ air }: { air: Air }) {
  const [units, setUnits] = useState(() => leftNow(air));
  useEffect(() => {
    setUnits(leftNow(air));
    const timer = setInterval(() => setUnits(leftNow(air)), 10_000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [air.at, air.units, air.per_hour, air.where, air.suit]);
  //: A bagful of cylinders and no suit is the one case where the number is not
  //: the answer: nothing connects the body to them, and it is suffocating with
  //: a full bag (D-234).
  if (air.where === "suit" && !air.suit) {
    return (
      <span className="vital low" title={t("ui-top-air-no-suit-title")}>
        <Glyph name="warmth" />
        <b className="num">{t("ui-top-air-no-suit")}</b>
      </span>
    );
  }
  if (units <= 0) {
    return (
      <span className="vital low" title={t("ui-top-air-out-title")}>
        <Glyph name="warmth" />
        <b className="num">{t("ui-top-air-out")}</b>
      </span>
    );
  }
  const hours = air.per_hour < 0 ? units / -air.per_hour : null;
  return (
    <span
      className="vital"
      //: `where` is the wire's own word for where the air comes from; the
      //: variant is keyed by it rather than by a sentence chosen here.
      title={t("ui-top-air-title", { aboard: String(air.where === "aboard") })}
    >
      <Glyph name="warmth" />
      <b className="num">
        {hours == null
          ? t("ui-top-air-units", { n: units.toFixed(0) })
          //: The same term the ship's card prints (`ui-ship-air-burn`): one
          //: unit and one rounding, or the same reserve read "30.0 ч" in the
          //: bar and "1.3 сут" in the window a click away.
          : t("ui-top-air-left", { term: term(hours) })}
      </b>
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
  //: The scale is one; the word for it is not. On a hot planet the reserve is
  //: coolness, and the same three sentences say so.
  const word = t("ui-top-warmth-word", { heat: String(frost.climate === "heat") });
  if (hours <= 0) {
    return (
      <span className="vital low" title={t("ui-top-warmth-frozen-title", { word })}>
        <Glyph name="warmth" />
        <b className="num">{t("ui-top-warmth-frozen")}</b>
      </span>
    );
  }
  return (
    <span
      className="vital"
      title={
        frost.warm
          ? t("ui-top-warmth-warm-title", { word })
          : t("ui-top-warmth-cold-title", { word })
      }
    >
      <Glyph name="warmth" />
      <b className="num">
        {t("ui-top-warmth-hours", { warm: String(frost.warm), n: hours.toFixed(1) })}
      </b>
    </span>
  );
}
