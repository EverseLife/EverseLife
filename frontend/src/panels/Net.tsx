// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The Net: correspondence and channels (D-044, D-069, D-222).
 *
 * The sidebar's remote half of talking. The room's talk is below the world and
 * vanishes when you leave the room; this is kept, and it takes the road to
 * arrive: a letter is seen by its reader when `delivered_at` comes, and the
 * writer sees until then that it is still on the way. The delay is the
 * distance -- the same seconds as walking it -- so the tab never explains it:
 * "дойдёт через 6 мин" is the explanation.
 *
 * Four screens in one tab, and the list is the home: whom I write with, what I
 * read. Writing to somebody new starts from a name; a channel is found by name
 * or made. The city's channel is official and cannot be dropped by a citizen.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Channel,
  ChannelFound,
  Letter,
  Post,
  Session,
  Thread,
} from "../api";
import { when } from "../clock";
import { PersonName } from "../Name";
import { askProfile } from "../people";
import { Refusal, useActions } from "../actions";

type Props = {
  session: Session;
  /** The count from `look`: when it grows, the list is reread. */
  unread: number;
  /** Whom to open the correspondence with: "write" from somebody's card. */
  wanted: string | null;
  onWanted: () => void;
};

type View =
  | { kind: "list" }
  | { kind: "compose" }
  | { kind: "thread"; id: string; who: string }
  | { kind: "channel"; id: string }
  | { kind: "channels" };


export function Net({ session, unread, wanted, onWanted }: Props) {
  const [view, setView] = useState<View>({ kind: "list" });
  const [threads, setThreads] = useState<Thread[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);

  const reread = useCallback(async () => {
    try {
      const answer = await session.send("net.threads");
      setThreads(answer.threads as Thread[]);
      setChannels(answer.channels as Channel[]);
    } catch {
      /* the session rises by itself; the next reading will do */
    }
  }, [session]);

  useEffect(() => {
    void reread();
    return session.on("net.", () => void reread());
  }, [reread, unread, view.kind, session]);

  //: "Write" from somebody's card: the card has opened the thread already,
  //: asking again only finds it. The tab may not have been mounted when the
  //: card asked, so the name comes as a prop rather than an event.
  useEffect(() => {
    if (!wanted) return;
    void (async () => {
      const answer = await session.send("net.open", { name: wanted });
      setView({ kind: "thread", id: String(answer.thread), who: wanted });
      onWanted();
    })();
  }, [session, wanted, onWanted]);

  const back = () => setView({ kind: "list" });

  if (view.kind === "compose") {
    return (
      <Compose
        session={session}
        onBack={back}
        onOpen={(id, who) => setView({ kind: "thread", id, who })}
      />
    );
  }
  if (view.kind === "thread") {
    return <Talk session={session} id={view.id} who={view.who} onBack={back} />;
  }
  if (view.kind === "channel") {
    return <Feed session={session} id={view.id} onBack={back} onChanged={reread} />;
  }
  if (view.kind === "channels") {
    return (
      <Channels
        session={session}
        mine={channels}
        onBack={back}
        onOpen={(id) => setView({ kind: "channel", id })}
        onChanged={reread}
      />
    );
  }

  return (
    <div className="net">
      <div className="row">
        <button onClick={() => setView({ kind: "compose" })}>Написать</button>
        <button className="quiet" onClick={() => setView({ kind: "channels" })}>
          Каналы
        </button>
      </div>

      <h3>Переписка</h3>
      {threads.length === 0 && <p className="note">Переписок ещё нет.</p>}
      <ul className="net-list">
        {threads.map((thread) => (
          <li key={thread.id}>
            <button
              className="net-row"
              onClick={() => setView({ kind: "thread", id: thread.id, who: thread.who })}
            >
              <span className="net-head">
                <b>
                  {thread.who}
                  {thread.surname ? ` ${thread.surname}` : ""}
                </b>
                {thread.unread > 0 && <span className="tally alarm">{thread.unread}</span>}
                <span className="net-when">{thread.last_at ? when(thread.last_at) : ""}</span>
              </span>
              <span className="net-preview">{thread.preview ?? "пока ни слова"}</span>
            </button>
          </li>
        ))}
      </ul>

      {channels.length > 0 && (
        <>
          <h3>Каналы</h3>
          <ul className="net-list">
            {channels.map((channel) => (
              <li key={channel.id}>
                <button
                  className="net-row"
                  onClick={() => setView({ kind: "channel", id: channel.id })}
                >
                  <span className="net-head">
                    <b>{channel.name}</b>
                    {channel.official && <Official />}
                    {channel.unread > 0 && <span className="tally alarm">{channel.unread}</span>}
                    <span className="net-when">{channel.last_at ? when(channel.last_at) : ""}</span>
                  </span>
                  <span className="net-preview">{channel.official ? "город" : channel.by}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function Official() {
  return (
    <span className="official" title="официальный канал города">
      официальный
    </span>
  );
}

function Back({ onBack }: { onBack: () => void }) {
  return (
    <button className="quiet" onClick={onBack} aria-label="назад" title="к списку">
      ←
    </button>
  );
}

/** Whom to write: a name, with the Net's suggestions as it is typed. */
function Compose({
  session,
  onBack,
  onOpen,
}: {
  session: Session;
  onBack: () => void;
  onOpen: (id: string, who: string) => void;
}) {
  const acting = useActions();
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<{ name: string; surname: string }[]>([]);

  useEffect(() => {
    if (!query.trim()) {
      setPeople([]);
      return;
    }
    let alive = true;
    session
      .send("net.people", { query })
      .then((answer) => alive && setPeople(answer.people as typeof people))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [session, query]);

  const open = (name: string) =>
    acting.act(async () => {
      const answer = await session.send("net.open", { name });
      onOpen(String(answer.thread), String(answer.who));
    });

  return (
    <div className="net">
      <div className="row">
        <Back onBack={onBack} />
        <input
          className="chat-text"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && query.trim() && !acting.busy) void open(query.trim());
          }}
          placeholder="кому — имя"
        />
      </div>
      <ul className="net-list">
        {people.map((person) => (
          <li key={person.name}>
            <button className="net-row" onClick={() => void open(person.name)} disabled={acting.busy}>
              <span className="net-head">
                <b>
                  {person.name}
                  {person.surname ? ` ${person.surname}` : ""}
                </b>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <Refusal of={acting} />
    </div>
  );
}

/** One correspondence: the letters, and the road they are on. */
function Talk({
  session,
  id,
  who,
  onBack,
}: {
  session: Session;
  id: string;
  who: string;
  onBack: () => void;
}) {
  const acting = useActions();
  const [letters, setLetters] = useState<Letter[]>([]);
  const [text, setText] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const scroll = useRef<HTMLDivElement>(null);

  const read = useCallback(async () => {
    try {
      const answer = await session.send("net.read", { thread: id });
      setLetters(answer.letters as Letter[]);
      setNow(Date.now());
    } catch {
      /* the session rises by itself */
    }
  }, [session, id]);

  //: A letter says when it arrives (D-226, `net.letter`): nothing to poll.
  useEffect(() => {
    setLetters([]);
    void read();
    return session.on("net.", () => void read());
  }, [read, session]);

  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight });
  }, [letters.length]);

  const send = () =>
    acting.act(async () => {
      const cleaned = text.trim();
      if (!cleaned) return;
      await session.send("net.write", { thread: id, text: cleaned });
      setText("");
      await read();
    });

  return (
    <div className="net">
      <div className="row">
        <Back onBack={onBack} />
        <button className="link net-who" onClick={() => askProfile(who)} title="профиль">
          {who}
        </button>
      </div>
      <div className="chat-lines net-letters" ref={scroll}>
        {letters.length === 0 && <p className="note">Пока ни слова.</p>}
        {letters.map((letter) => {
          const onWay = letter.mine && new Date(letter.delivered_at).getTime() > now;
          return (
            <p key={letter.id} className={`line${letter.mine ? " mine" : ""}`}>
              {letter.mine ? (
                <b>вы:</b>
              ) : (
                <PersonName name={letter.who}>
                  <b>{letter.who}:</b>
                </PersonName>
              )}{" "}
              {letter.text}
              <span className="net-when">
                {onWay ? `в пути · дойдёт ${when(letter.delivered_at)}` : when(letter.delivered_at)}
              </span>
            </p>
          );
        })}
      </div>
      <div className="row chat-input">
        <input
          className="chat-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !acting.busy && text.trim()) void send();
          }}
          placeholder="написать…"
        />
        <button onClick={send} disabled={acting.busy || !text.trim()}>
          Отправить
        </button>
      </div>
      <Refusal of={acting} />
    </div>
  );
}

/** One channel: the posts that have reached this reader. */
function Feed({
  session,
  id,
  onBack,
  onChanged,
}: {
  session: Session;
  id: string;
  onBack: () => void;
  onChanged: () => Promise<void>;
}) {
  const acting = useActions();
  const [channel, setChannel] = useState<
    (Pick<Channel, "id" | "name" | "about" | "official" | "writable">) | null
  >(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [text, setText] = useState("");
  //: Whether the reader chose this channel: from the list, where it is known.
  const [mine, setMine] = useState<Channel | null>(null);

  const read = useCallback(async () => {
    try {
      const answer = await session.send("net.channel.read", { channel: id });
      setChannel(answer.channel as typeof channel);
      setPosts(answer.posts as Post[]);
      const list = (await session.send("net.threads")).channels as Channel[];
      setMine(list.find((c) => c.id === id) ?? null);
    } catch {
      /* the session rises by itself */
    }
  }, [session, id]);

  //: A post says when it reaches this reader (D-226, `net.post`).
  useEffect(() => {
    void read();
    return session.on("net.", () => void read());
  }, [read, session]);

  const publish = () =>
    acting.act(async () => {
      const cleaned = text.trim();
      if (!cleaned) return;
      await session.send("net.post", { channel: id, text: cleaned });
      setText("");
      await read();
    });

  const toggle = () =>
    acting.act(async () => {
      await session.send(mine ? "net.unsubscribe" : "net.subscribe", { channel: id });
      await read();
      await onChanged();
    });

  //: The owner and a citizen of this city do not unsubscribe: one wrote it,
  //: the other belongs to it.
  const mayLeave = channel !== null && !(mine?.implied ?? false) && !(channel.writable && !channel.official);

  return (
    <div className="net">
      <div className="row">
        <Back onBack={onBack} />
        <b className="net-who">{channel?.name ?? "…"}</b>
        {channel?.official && <Official />}
        {mayLeave && (
          <button className="quiet" onClick={toggle} disabled={acting.busy}>
            {mine ? "Отписаться" : "Подписаться"}
          </button>
        )}
      </div>
      {channel?.about && <p className="note">{channel.about}</p>}
      <div className="chat-lines net-letters">
        {posts.length === 0 && <p className="note">Пока ничего не опубликовано.</p>}
        {posts.map((post) => (
          <p key={post.id} className="line post">
            <PersonName name={post.who}>
              <b>{post.who}</b>
            </PersonName>
            <span className="net-when">{when(post.delivered_at)}</span>
            <br />
            {post.text}
          </p>
        ))}
      </div>
      {channel?.writable && (
        <div className="row chat-input">
          <textarea
            className="chat-text"
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="что сказать читателям…"
          />
          <button onClick={publish} disabled={acting.busy || !text.trim()}>
            Опубликовать
          </button>
        </div>
      )}
      <Refusal of={acting} />
    </div>
  );
}

/** Finding a channel by name, and starting one's own. */
function Channels({
  session,
  mine,
  onBack,
  onOpen,
  onChanged,
}: {
  session: Session;
  mine: Channel[];
  onBack: () => void;
  onOpen: (id: string) => void;
  onChanged: () => Promise<void>;
}) {
  const acting = useActions();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ChannelFound[]>([]);
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [making, setMaking] = useState(false);

  const search = useCallback(async () => {
    try {
      const answer = await session.send("net.channel.find", { query });
      setFound(answer.channels as ChannelFound[]);
    } catch {
      /* the session rises by itself */
    }
  }, [session, query]);

  useEffect(() => {
    void search();
  }, [search, mine.length]);

  const subscribe = (channel: ChannelFound) =>
    acting.act(async () => {
      await session.send(channel.subscribed ? "net.unsubscribe" : "net.subscribe", {
        channel: channel.id,
      });
      await search();
      await onChanged();
    });

  const create = () =>
    acting.act(async () => {
      const answer = await session.send("net.channel.create", { name, about });
      setName("");
      setAbout("");
      setMaking(false);
      await onChanged();
      onOpen(String(answer.channel));
    });

  return (
    <div className="net">
      <div className="row">
        <Back onBack={onBack} />
        <input
          className="chat-text"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="найти канал"
        />
        <button className="quiet" onClick={() => setMaking((v) => !v)}>
          Новый канал
        </button>
      </div>

      {making && (
        <div className="card flat">
          <label>
            <span>название</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
          </label>
          <label>
            <span>о чём</span>
            <textarea
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              rows={2}
              maxLength={300}
            />
          </label>
          <div className="row">
            <button onClick={create} disabled={acting.busy || !name.trim()}>
              Создать
            </button>
          </div>
        </div>
      )}

      <ul className="net-list">
        {found.map((channel) => (
          <li key={channel.id} className="net-found">
            <button className="net-row" onClick={() => onOpen(channel.id)}>
              <span className="net-head">
                <b>{channel.name}</b>
                {channel.official && <Official />}
              </span>
              <span className="net-preview">
                {channel.official ? "город" : channel.by}
                {channel.about ? ` · ${channel.about}` : ""}
              </span>
            </button>
            {!mine.find((c) => c.id === channel.id && (c.implied || (c.writable && !c.official))) && (
              <button className="quiet" onClick={() => void subscribe(channel)} disabled={acting.busy}>
                {channel.subscribed ? "отписаться" : "подписаться"}
              </button>
            )}
          </li>
        ))}
      </ul>
      {found.length === 0 && <p className="note">Ничего не найдено.</p>}
      <Refusal of={acting} />
    </div>
  );
}
