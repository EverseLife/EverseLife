/**
 * Живое общение — нижняя полоса локации (D-043, D-050).
 *
 * Три типа сообщений, и тип обязателен: речь, действие, вне игры. Оформлены
 * по-разному — без этого отыгрыш неотличим от реплик, а метагейм протекает в
 * игровое, вплоть до суда.
 *
 * Управление кружками живёт отдельным табом основного окна; здесь только сам
 * разговор — и пометка, если вы говорите в кружке, а не на всю комнату.
 * Истории нет: это разговор в комнате, а не переписка.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatLine, Circle, Session } from "../api";

type Props = {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** Ключ локации: сменилась — разговор начался заново. */
  place: string;
};

const KINDS = [
  { value: "speech", label: "речь" },
  { value: "action", label: "действие" },
  { value: "ooc", label: "вне игры" },
] as const;

export function Chat({ session, busy, act, place }: Props) {
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
            //: Тот же предохранитель, что у кнопки: занято или пусто — не шлём.
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
        краем уха, из кружка «{line.source}»: {line.who} — «{line.text}»
      </p>
    );
  }
  if (line.kind === "action") {
    return (
      <p className="line action">
        * {line.who} {line.text}
      </p>
    );
  }
  if (line.kind === "ooc") {
    return (
      <p className="line ooc">
        [вне игры] {line.who}: {line.text}
      </p>
    );
  }
  return (
    <p className="line">
      <b>{line.who}:</b> {line.quiet ? <i>(вполголоса) {line.text}</i> : line.text}
    </p>
  );
}
