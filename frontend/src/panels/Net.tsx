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
  CityVote,
  Letter,
  Post,
  Thread,
} from "../api";
import { when } from "../clock";
import { t } from "../locale";
import { PersonName } from "../Name";
import { askProfile } from "../people";
import { Refusal, useActions, useNames, useSession } from "../actions";
import { Deadline } from "../Deadline";
import { PollAnswer, PollSubject } from "./Poll";
import { pollTally } from "../polls";

type Props = {
  /** The count from `look`: when it grows, the list is reread. */
  unread: number;
  /** Whom to open the correspondence with: "write" from somebody's card. */
  wanted: string | null;
  onWanted: () => void;
};

/** The city's events the ballot box moves on. */
const POLL_EVENTS = [
  "city.vote_opened",
  "city.vote_cast",
  "city.vote_nominated",
  "city.vote_closed",
];

type View =
  | { kind: "list" }
  | { kind: "compose" }
  | { kind: "thread"; id: string; who: string }
  | { kind: "channel"; id: string }
  | { kind: "channels" };


export function Net({ unread, wanted, onWanted }: Props) {
  const session = useSession();
  const [view, setView] = useState<View>({ kind: "list" });
  const [threads, setThreads] = useState<Thread[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [polls, setPolls] = useState<CityVote[]>([]);

  const reread = useCallback(async () => {
    try {
      const answer = await session.send("net.threads");
      setThreads(answer.threads as Thread[]);
      setChannels(answer.channels as Channel[]);
      setPolls(answer.votes as CityVote[]);
    } catch {
      /* the session rises by itself; the next reading will do */
    }
  }, [session]);

  useEffect(() => {
    void reread();
    const stops = [
      session.on("net.", () => void reread()),
      //: A poll is the city's affair and arrives with the city's events rather
      //: than the Net's: convened, stood in, cast in, counted. The tally moves
      //: under a reader who is only watching, and the list moves with it
      //: (D-226). Named one by one because `on` takes a **kind or a prefix
      //: ending in a dot**: «city.vote_» is neither, and matched nothing at
      //: all -- a poll convened while the tab was open never showed up in it.
      ...POLL_EVENTS.map((kind) => session.on(kind, () => void reread())),
    ];
    return () => stops.forEach((stop) => stop());
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
        onBack={back}
        onOpen={(id, who) => setView({ kind: "thread", id, who })}
      />
    );
  }
  if (view.kind === "thread") {
    return <Talk id={view.id} who={view.who} onBack={back} />;
  }
  if (view.kind === "channel") {
    return <Feed id={view.id} onBack={back} onChanged={reread} />;
  }
  if (view.kind === "channels") {
    return (
      <Channels
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
        <button onClick={() => setView({ kind: "compose" })}>{t("ui-net-write")}</button>
        <button className="quiet" onClick={() => setView({ kind: "channels" })}>
          {t("ui-net-channels")}
        </button>
      </div>

      <Polls polls={polls} onAnswered={reread} />

      <h3>{t("ui-net-threads")}</h3>
      {threads.length === 0 && <p className="note">{t("ui-net-threads-none")}</p>}
      <ul className="net-list">
        {threads.map((thread) => (
          <li key={thread.id}>
            <button
              className="bare net-row"
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
              <span className="net-preview">{thread.preview ?? t("ui-net-thread-empty")}</span>
            </button>
          </li>
        ))}
      </ul>

      {channels.length > 0 && (
        <>
          <h3>{t("ui-net-channels")}</h3>
          <ul className="net-list">
            {channels.map((channel) => (
              <li key={channel.id}>
                <button
                  className="bare net-row"
                  onClick={() => setView({ kind: "channel", id: channel.id })}
                >
                  <span className="net-head">
                    <b>{channel.name}</b>
                    {channel.official && <Official />}
                    {channel.unread > 0 && <span className="tally alarm">{channel.unread}</span>}
                    <span className="net-when">{channel.last_at ? when(channel.last_at) : ""}</span>
                  </span>
                  <span className="net-preview">
                    {channel.official ? t("ui-net-city") : channel.by}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** The city's polls, where a citizen answers them (D-161).
 *
 * Here rather than only in the administration because a vote is participation
 * and not governing: the ballot travels to whoever has a voice, wherever they
 * stand, and the citizen who never opens the administration is exactly the one
 * the poll had to reach. The section appears only while a poll is running --
 * an empty heading in an inbox is noise -- and an answered one stays in it
 * until the deadline: a mind may be changed while the box is open.
 */
function Polls({ polls, onAnswered }: { polls: CityVote[]; onAnswered: () => Promise<void> }) {
  const names = useNames();
  const acting = useActions();
  //: The refusal outlives the row it came from: a poll closed between the
  //: drawing and the press disappears on the reread, and with the section
  //: gone the player would see the line vanish and never learn why.
  if (polls.length === 0 && !acting.trouble) return null;
  const answer = (what: () => Promise<unknown>) =>
    acting.act(async () => {
      await what();
      await onAnswered();
    });
  return (
    <>
      <h3>{t("ui-net-votes")}</h3>
      <ul className="net-list">
        {polls.map((poll) => (
          <li key={poll.id}>
            <div className="net-row net-poll">
              <span className="net-head">
                <b>
                  <PollSubject poll={poll} names={names} />
                </b>
              </span>
              <span className="net-preview">{pollTally(poll)}</span>
              <span className="row">
                <PollAnswer poll={poll} go={answer} busy={acting.busy} />
                <Deadline until={poll.closes_at} label={t("ui-net-votes")} size="row" />
              </span>
            </div>
          </li>
        ))}
      </ul>
      <Refusal of={acting} />
    </>
  );
}

function Official() {
  return (
    <span className="official" title={t("ui-net-official-title")}>
      {t("ui-net-official")}
    </span>
  );
}

function Back({ onBack }: { onBack: () => void }) {
  return (
    <button
      className="quiet"
      onClick={onBack}
      aria-label={t("ui-net-back")}
      title={t("ui-net-back-title")}
    >
      ←
    </button>
  );
}

/** Whom to write: a name, with the Net's suggestions as it is typed. */
function Compose({
  onBack,
  onOpen,
}: {
  onBack: () => void;
  onOpen: (id: string, who: string) => void;
}) {
  const session = useSession();
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
          placeholder={t("ui-net-to-whom")}
        />
      </div>
      <ul className="net-list">
        {people.map((person) => (
          <li key={person.name}>
            <button className="bare net-row" onClick={() => void open(person.name)} disabled={acting.busy}>
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
  id,
  who,
  onBack,
}: {
  id: string;
  who: string;
  onBack: () => void;
}) {
  const session = useSession();
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
        <button className="link net-who" onClick={() => askProfile(who)} title={t("ui-net-profile")}>
          {who}
        </button>
      </div>
      <div className="chat-lines net-letters" ref={scroll}>
        {letters.length === 0 && <p className="note">{t("ui-net-letters-none")}</p>}
        {letters.map((letter) => {
          const onWay = letter.mine && new Date(letter.delivered_at).getTime() > now;
          return (
            <p key={letter.id} className={`line${letter.mine ? " mine" : ""}`}>
              {letter.mine ? (
                <b>{t("ui-net-you")}</b>
              ) : (
                <PersonName name={letter.who}>
                  <b>{letter.who}:</b>
                </PersonName>
              )}{" "}
              {letter.text}
              <span className="net-when">
                {onWay
                  ? t("ui-net-on-way", { when: when(letter.delivered_at) })
                  : when(letter.delivered_at)}
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
          placeholder={t("ui-net-letter-hint")}
        />
        <button onClick={send} disabled={acting.busy || !text.trim()}>
          {t("ui-net-send")}
        </button>
      </div>
      <Refusal of={acting} />
    </div>
  );
}

/** One channel: the posts that have reached this reader. */
function Feed({
  id,
  onBack,
  onChanged,
}: {
  id: string;
  onBack: () => void;
  onChanged: () => Promise<void>;
}) {
  const session = useSession();
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
            {mine ? t("ui-net-unsubscribe") : t("ui-net-subscribe")}
          </button>
        )}
      </div>
      {channel?.about && <p className="note">{channel.about}</p>}
      <div className="chat-lines net-letters">
        {posts.length === 0 && <p className="note">{t("ui-net-posts-none")}</p>}
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
            placeholder={t("ui-net-post-hint")}
          />
          <button onClick={publish} disabled={acting.busy || !text.trim()}>
            {t("ui-net-publish")}
          </button>
        </div>
      )}
      <Refusal of={acting} />
    </div>
  );
}

/** Finding a channel by name, and starting one's own. */
function Channels({
  mine,
  onBack,
  onOpen,
  onChanged,
}: {
  mine: Channel[];
  onBack: () => void;
  onOpen: (id: string) => void;
  onChanged: () => Promise<void>;
}) {
  const session = useSession();
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
          placeholder={t("ui-net-find-channel")}
        />
        <button className="quiet" onClick={() => setMaking((v) => !v)}>
          {t("ui-net-new-channel")}
        </button>
      </div>

      {making && (
        <div className="card flat">
          <label>
            <span>{t("ui-net-channel-name")}</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
          </label>
          <label>
            <span>{t("ui-net-channel-about")}</span>
            <textarea
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              rows={2}
              maxLength={300}
            />
          </label>
          <div className="row">
            <button onClick={create} disabled={acting.busy || !name.trim()}>
              {t("ui-net-channel-create")}
            </button>
          </div>
        </div>
      )}

      <ul className="net-list">
        {found.map((channel) => (
          <li key={channel.id} className="net-found">
            <button className="bare net-row" onClick={() => onOpen(channel.id)}>
              <span className="net-head">
                <b>{channel.name}</b>
                {channel.official && <Official />}
              </span>
              <span className="net-preview">
                {channel.official ? t("ui-net-city") : channel.by}
                {channel.about ? ` · ${channel.about}` : ""}
              </span>
            </button>
            {!mine.find((c) => c.id === channel.id && (c.implied || (c.writable && !c.official))) && (
              <button className="quiet" onClick={() => void subscribe(channel)} disabled={acting.busy}>
                {channel.subscribed ? t("ui-net-unsubscribe-quiet") : t("ui-net-subscribe-quiet")}
              </button>
            )}
          </li>
        ))}
      </ul>
      {found.length === 0 && <p className="note">{t("ui-net-nothing-found")}</p>}
      <Refusal of={acting} />
    </div>
  );
}
