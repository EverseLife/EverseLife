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
import { PersonName } from "../Name";

type Props = {
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The location key: changed -- the conversation started anew. */
  place: string;
};

const KINDS = [
  { value: "speech", label: "речь" },
  { value: "action", label: "действие" },
  { value: "ooc", label: "вне игры" },
] as const;

export function Chat({ place }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [lines, setLines] = useState<ChatLine[]>([]);
  const [circles, setCircles] = useState<Circle[]>([]);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<(typeof KINDS)[number]["value"]>("speech");
  const [quiet, setQuiet] = useState(false);
  //: The strip folds to one line (D-238): the talk gives the scene its
  //: height back when it is not being read.
  const [folded, setFolded] = useState(false);
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
        <span className="note">
          разговор
          {mine && ` · кружок «${mine.name ?? "без имени"}»`}
        </span>
        <button
          type="button"
          className="bare chat-fold"
          onClick={() => setFolded((was) => !was)}
          aria-expanded={!folded}
        >
          {folded ? "развернуть ▸" : "свернуть ▾"}
        </button>
      </div>
      {!folded && (
      <>
      <Refusal of={acting} />
      <div className="chat-lines" ref={scroll}>
        {lines.length === 0 && (
          <p className="note">Тихо. Разговор живёт, пока ты в комнате.</p>
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
            onClick={() => setKind(option.value)}
          >
            {option.label}
          </button>
        ))}
        <label className="note">
          <input
            type="checkbox"
            checked={quiet}
            onChange={(e) => setQuiet(e.target.checked)}
          />{" "}
          вполголоса
        </label>
        <input
          className="chat-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            //: The same fuse as on the button: busy or empty -- we do not send.
            if (e.key === "Enter" && !busy && text.trim()) void say();
          }}
          placeholder={
            kind === "speech" ? "сказать…" : kind === "action" ? "что делает персонаж…"
            : "не в мире…"
          }
        />
        <button onClick={say} disabled={busy || !text.trim()}>
          Сказать
        </button>
      </div>
      <p className="note">
        {mine
          ? `Вы в кружке «${mine.name ?? "без имени"}»: слышат участники, остальным долетают обрывки.`
          : "Слышат все, кто здесь. Собраться потише — кнопкой «кружки» слева."}
      </p>
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

  useEffect(() => {
    if (!open) return;
    //: A dialog owns the focus: the first control inside takes it.
    pop.current?.querySelector<HTMLElement>("input, button")?.focus();
    const onDown = (event: PointerEvent) => {
      if (anchor.current && !anchor.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        chip.current?.focus();
      }
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="hud-anchor" ref={anchor}>
      <button
        ref={chip}
        className="quiet"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="кому слышно сказанное; клик — подойти к кружку или собрать свой"
      >
        {mine ? `кружок «${mine.name ?? "без имени"}»` : "кружки"}
      </button>
      {open && (
        <div ref={pop} className="hud-pop up" role="dialog" aria-label="Кружки">
          {circles.length === 0 && (
            <p className="note">Никто не шепчется: весь разговор локации — общий.</p>
          )}
          {circles.map((circle) => (
            <div className="row circle-row" key={circle.id}>
              <span>
                <b>{circle.name ?? "кружок без имени"}</b>
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
                  отойти
                </button>
              ) : (
                <button
                  className="quiet"
                  onClick={() => move(() => session.send("chat.join", { circle: circle.id }))}
                  disabled={busy}
                >
                  подойти
                </button>
              )}
            </div>
          ))}
          {!mine && (
            <div className="row">
              <input
                value={name}
                placeholder="имя кружка (можно без)"
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
                Собрать
              </button>
            </div>
          )}
          <p className="note">
            Подошедшего к кружку видно всем; закрытых кружков нет. Пока вы в
            кружке, реплики слышат участники — с шансом утечки к чужим ушам.
          </p>
        </div>
      )}
    </span>
  );
}

function Line({ line }: { line: ChatLine }) {
  if (line.overheard) {
    return (
      <p className="line overheard">
        краем уха, из кружка «{line.source}»: <PersonName name={line.who} /> — «{line.text}»
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
        [вне игры] <PersonName name={line.who} />: {line.text}
      </p>
    );
  }
  return (
    <p className="line">
      <PersonName name={line.who}>
        <b>{line.who}:</b>
      </PersonName>{" "}
      {line.quiet ? <i>(вполголоса) {line.text}</i> : line.text}
    </p>
  );
}
