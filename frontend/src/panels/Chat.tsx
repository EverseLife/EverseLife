// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Live talk -- the location's bottom strip (D-043, D-050).
 *
 * Three message kinds, and the kind is mandatory: speech, action,
 * out-of-game. Styled differently -- without that roleplay is
 * indistinguishable from remarks, and metagame leaks into the in-game, up to the court.
 *
 * Circle management lives in a separate tab of the main window; here only
 * the talk itself -- and a mark if you speak in a circle rather than to the
 * whole room. There is no history: this is a conversation in a room, not correspondence.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatLine, Circle, Session } from "../api";
import { Refusal, useActions } from "../actions";
import { PersonName } from "../Name";

type Props = {
  session: Session;
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

export function Chat({ session, place }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [lines, setLines] = useState<ChatLine[]>([]);
  const [mine, setMine] = useState<Circle | null>(null);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<(typeof KINDS)[number]["value"]>("speech");
  const [quiet, setQuiet] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);

  const listen = useCallback(async () => {
    try {
      const answer = await session.send("chat.hear");
      setLines(answer.lines as ChatLine[]);
      setMine(((answer.circles as Circle[]) ?? []).find((c) => c.mine) ?? null);
    } catch {
      //: В пути слушать нечего — панель всё равно скрыта.
    }
  }, [session]);

  useEffect(() => {
    setLines([]);
    void listen();
    const timer = setInterval(() => void listen(), 4000);
    return () => clearInterval(timer);
  }, [listen, place]);

  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight });
  }, [lines.length]);

  const say = () =>
    act(async () => {
      const cleaned = text.trim();
      if (!cleaned) return;
      await session.send("chat.say", { text: cleaned, kind, quiet });
      setText("");
      await listen();
    });

  return (
    <section className="chat">
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
          : "Слышат все, кто здесь. Кружки — в соседнем табе."}
      </p>
    </section>
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
