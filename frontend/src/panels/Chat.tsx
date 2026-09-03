// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Live talk -- the location's bottom strip (D-043, D-050).
 *
 * Three message kinds, and the kind is mandatory: speech, action,
 * out-of-game. Styled differently -- without that roleplay is
 * indistinguishable from remarks, and metagame leaks into the in-game, up to the court.
 *
 * Circles live here now (D-238): they only decide who hears what is said,
 * and that choice belongs beside the saying, not on a scene tab of its own.
 * The chip by the input names the current channel; the popover under it
 * joins, leaves and gathers. There is no history: this is a conversation in
 * a room, not correspondence.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatLine, Circle } from "../api";
import { Refusal, useActions, useSession } from "../actions";
import { Glyph } from "../Glyph";
import { folded as foldedPane, rememberFolded } from "../hud";
import { Here } from "./Here";
import { t } from "../locale";
import { useNarrow } from "../narrow";
import { PersonName } from "../Name";
import { usePopover } from "../popover";

type Props = {
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The location key: changed -- the conversation started anew. */
  place: string;
};

/**
 * The three kinds of line, each by the message that names it.
 *
 * `value` goes on the wire and stays as it is; `label` is a key, drawn where
 * the button is drawn -- a `t` here would freeze the language at import.
 */
const KINDS = [
  { value: "speech", label: "ui-chat-kind-speech" },
  { value: "action", label: "ui-chat-kind-action" },
  { value: "ooc", label: "ui-chat-kind-ooc" },
] as const;

type Kind = (typeof KINDS)[number]["value"];

/** The placeholder over the input, by the kind of line being written. */
const SAY_HINTS: Record<Kind, string> = {
  speech: "ui-chat-say-speech",
  action: "ui-chat-say-action",
  ooc: "ui-chat-say-ooc",
};

/** A circle's name where one message wants both halves of "named or not". */
function circleName(circle: Circle | null): Record<string, string> {
  return { named: String(circle?.name != null), name: circle?.name ?? "" };
}

export function Chat({ place }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [lines, setLines] = useState<ChatLine[]>([]);
  const [circles, setCircles] = useState<Circle[]>([]);
  const [text, setText] = useState("");
  //: Sticky between two lines said in a row, and no further: both go out with
  //: `chat.say` and both have consequences in the world. Out of character is
  //: the mark D-050 pays a whole message type for, so that a week later nobody
  //: has to guess what the character said and what the person did; speaking
  //: under one's breath multiplies the chance of leaking to the next circle
  //: (D-043). A choice made in the last session must not speak for a sentence
  //: typed in this one, so neither is written down (`kept.ts`, the list of
  //: what is never kept).
  const [kind, setKind] = useState<Kind>("speech");
  const [quiet, setQuiet] = useState(false);
  //: The strip folds to one line (D-238): the talk gives the scene its
  //: height back when it is not being read. The fold is remembered, like
  //: the sidebar's: a strip that springs back on every reload nags.
  const [shut, setShut] = useState(() => foldedPane("chat"));
  const fold = (next: boolean) => {
    setShut(next);
    rememberFolded("chat", next);
  };
  //: On a phone the talk is not a strip under the scene but a section of its
  //: own, chosen from the bar at the bottom (`shell.css`, brief section 9).
  //: There is no scene above it to give height back to, so folding it would
  //: leave the section empty but for the line that folded it -- and the way
  //: out of the talk is the bar, not a button inside it. The remembered fold
  //: is left alone: it belongs to the desktop's strip, and a phone must not
  //: unfold the strip the desktop was left with.
  const narrow = useNarrow();
  const folded = shut && !narrow;
  const scroll = useRef<HTMLDivElement>(null);

  const listen = useCallback(async () => {
    try {
      const answer = await session.send("chat.hear");
      setLines(answer.lines as ChatLine[]);
      setCircles((answer.circles as Circle[]) ?? []);
    } catch {
      //: В пути слушать нечего — панель всё равно скрыта.
    }
  }, [session]);
  //: Only who stands with whom. `chat.hear` serves the delivery buffer of the
  //: last half hour, and replacing `lines` with it would wipe what the room
  //: accumulated through `chat.said` -- so a circle forming across the room
  //: must not touch the talk at all.
  const listenCircles = useCallback(async () => {
    try {
      const answer = await session.send("chat.hear");
      setCircles((answer.circles as Circle[]) ?? []);
    } catch {
      //: В пути слушать нечего — панель всё равно скрыта.
    }
  }, [session]);
  const mine = circles.find((circle) => circle.mine) ?? null;

  //: The line itself rides with `chat.said` (D-226, wave 2): it is added
  //: here, once by `id` -- a circle member may get it twice, whole and as a
  //: leak. `chat.hear` reads the room whole on entry and after a break; who
  //: stands with whom is told by the room too (`chat.circled`).
  useEffect(() => {
    setLines([]);
    setCircles([]);
    void listen();
    const saidOff = session.on("chat.said", (happening) => {
      const line = happening.line as ChatLine | undefined;
      if (!line) {
        void listen();
        return;
      }
      setLines((known) =>
        known.some((l) => l.id === line.id)
          ? known
          : [...known, { ...line, overheard: Boolean(line.overheard) }],
      );
    });
    const circledOff = session.on("chat.circled", () => void listenCircles());
    return () => {
      saidOff();
      circledOff();
    };
  }, [listen, listenCircles, place, session]);

  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight });
  }, [lines.length]);

  const say = () =>
    act(async () => {
      const cleaned = text.trim();
      if (!cleaned) return;
      await session.send("chat.say", { text: cleaned, kind, quiet });
      //: The line comes back as `chat.said`; nothing to reread.
      setText("");
    });

  return (
    <section className="chat">
      <div className="chat-head">
        <span className="note chat-head-what">
          {t("ui-chat-head")}
          {mine && t("ui-chat-head-circle", circleName(mine))}
        </span>
        {/* Who else stands in the room, in the head rather than in the lines:
            presence is a fact about the place, and the talk is where one looks
            for the people in it. */}
        <Here place={place} />
        {!narrow && (
          <button
            type="button"
            className="bare chat-fold"
            onClick={() => fold(!folded)}
            aria-expanded={!folded}
          >
            {folded ? t("ui-chat-unfold") : t("ui-chat-fold")}
          </button>
        )}
      </div>
      {!folded && (
      <>
      <Refusal of={acting} />
      <div className="chat-lines" ref={scroll}>
        {lines.length === 0 && (
          <p className="note">{t("ui-chat-silent")}</p>
        )}
        {lines.map((line) => (
          <Line key={line.id} line={line} />
        ))}
      </div>

      <div className="row chat-input">
        <CircleChip circles={circles} busy={busy} act={act} onChanged={listenCircles} />
        {KINDS.map((option) => (
          <button
            key={option.value}
            className={kind === option.value ? "" : "quiet"}
            //: The kind chosen is a choice, not an action (D-238): the slab
            //: belongs to the verb beside it, and a reader is told which of
            //: the three is on.
            aria-pressed={kind === option.value}
            onClick={() => setKind(option.value)}
          >
            {t(option.label)}
          </button>
        ))}
        {/* Half a voice is a choice like the three kinds beside it, and wears
            the same grammar (D-238): a slab that stays down while it is on,
            `aria-pressed` for whoever is not looking at the slab. A tick in a
            box was the one control in the strip shaped unlike its neighbours,
            and the smallest thing to hit in it. The glyph changes with the
            state the way the map's tether does -- two rings of voice or one. */}
        <button
          className={`chat-voice${quiet ? "" : " quiet"}`}
          aria-pressed={quiet}
          onClick={() => setQuiet(!quiet)}
        >
          <Glyph name={quiet ? "whisper" : "voice"} />
          {t("ui-chat-quiet-toggle")}
        </button>
        <input
          className="chat-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            //: The same fuse as on the button: busy or empty -- we do not send.
            if (e.key === "Enter" && !busy && text.trim()) void say();
          }}
          placeholder={t(SAY_HINTS[kind])}
        />
        <button className="chat-send" onClick={say} disabled={busy || !text.trim()}>
          {t("ui-chat-say")}
        </button>
      </div>
      {/* Only a circle needs a footnote: the common talk is the default,
          and a line saying so under every message was noise. */}
      {mine && <p className="note">{t("ui-chat-note-circle", circleName(mine))}</p>}
      </>
      )}
    </section>
  );
}

/**
 * The channel by the input (D-238): who will hear what you are about to say,
 * and the door to change that. The popover carries what the "кружки" scene
 * tab used to: join, step away, gather. Groups are visible, their content is
 * not -- walking up to a circle is seen by everybody (D-043).
 */
function CircleChip({
  circles,
  busy,
  act,
  onChanged,
}: {
  circles: Circle[];
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** Reread the room after an own move: dissolving one's last circle does
   *  not always come back as `chat.circled`, and the chip must not stale. */
  onChanged: () => Promise<void>;
}) {
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const anchor = useRef<HTMLSpanElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const chip = useRef<HTMLButtonElement | null>(null);
  const mine = circles.find((circle) => circle.mine) ?? null;
  const move = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await onChanged();
    });

  const close = useCallback(() => setOpen(false), []);
  usePopover({ open, close, anchor, toggle: chip, pop });

  return (
    <span className="hud-anchor" ref={anchor}>
      <button
        ref={chip}
        className="quiet"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={t("ui-chat-chip-title")}
      >
        {mine ? t("ui-chat-chip-circle", circleName(mine)) : t("ui-chat-chip-none")}
      </button>
      {open && (
        <div ref={pop} className="hud-pop up" role="dialog" aria-label={t("ui-chat-circles-label")}>
          {circles.length === 0 && <p className="note">{t("ui-chat-circles-none")}</p>}
          {circles.map((circle) => (
            <div className="row circle-row" key={circle.id}>
              <span>
                <b>{t("ui-chat-circle-title", circleName(circle))}</b>
                <span className="note">
                  {" "}
                  ·{" "}
                  {circle.members.map((member, i) => (
                    <span key={member}>
                      {i > 0 && ", "}
                      <PersonName name={member} />
                    </span>
                  ))}
                </span>
              </span>
              {circle.mine ? (
                <button
                  className="quiet"
                  onClick={() => move(() => session.send("chat.leave"))}
                  disabled={busy}
                >
                  {t("ui-chat-leave")}
                </button>
              ) : (
                <button
                  className="quiet"
                  onClick={() => move(() => session.send("chat.join", { circle: circle.id }))}
                  disabled={busy}
                >
                  {t("ui-chat-join")}
                </button>
              )}
            </div>
          ))}
          {!mine && (
            <div className="row">
              <input
                value={name}
                placeholder={t("ui-chat-gather-name")}
                onChange={(e) => setName(e.target.value)}
              />
              <button
                onClick={() =>
                  move(async () => {
                    await session.send("chat.gather", { name: name.trim() || undefined });
                    setName("");
                  })
                }
                disabled={busy}
              >
                {t("ui-chat-gather")}
              </button>
            </div>
          )}
          <p className="note">{t("ui-chat-circles-rule")}</p>
        </div>
      )}
    </span>
  );
}

function Line({ line }: { line: ChatLine }) {
  //: The name in the middle of an overheard scrap is right-clickable, so that
  //: sentence is drawn in two halves with `PersonName` between them.
  if (line.overheard) {
    return (
      <p className="line overheard">
        {t("ui-chat-overheard", { source: line.source ?? "" })}{" "}
        <PersonName name={line.who} /> {t("ui-chat-overheard-said", { text: line.text })}
      </p>
    );
  }
  if (line.kind === "action") {
    return (
      <p className="line action">
        * <PersonName name={line.who} /> {line.text}
      </p>
    );
  }
  if (line.kind === "ooc") {
    return (
      <p className="line ooc">
        {t("ui-chat-ooc")} <PersonName name={line.who} />: {line.text}
      </p>
    );
  }
  return (
    <p className="line">
      <PersonName name={line.who}>
        <b>{line.who}:</b>
      </PersonName>{" "}
      {line.quiet ? <i>{t("ui-chat-quiet-line", { text: line.text })}</i> : line.text}
    </p>
  );
}
