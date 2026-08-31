// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The socket, which is the only place the player **acts**.
 *
 * There is and will be no convenient REST for "make a swing": it would turn
 * mining into a script (60-meta/01-anti-cheat). So one connection carries
 * everything a body does, and the protocol over it is boring on purpose --
 * one command, one reply, matched by number. Hence the queue below.
 *
 * It is two-way (D-226). Between the replies the server speaks on its own, and
 * an answer to a command is a confirmation rather than a state: what changed
 * arrives as an event, and whoever subscribed with `on()` hears it. That is
 * also why a broken session rises by itself instead of at the next command --
 * a closed socket is a deaf one, and waiting would lose whatever was said in
 * the meantime.
 *
 * Everything else here serves that. `Refused` is how the engine says no in
 * words the player can read, `Happening` is what the server says unasked, and
 * the two timeouts are the ways a socket fails quietly -- a break, and an
 * answer that never comes -- each of which would otherwise hang a button
 * forever.
 */
import { WS } from "./host";
import { refusalText, t } from "./locale";
import { PART_ANSWERS, PART_COMMANDS, type LiveLook, type Parts } from "./wire/look";
import type { Enrollment } from "./wire/person";

/**
 * The engine said no, in words the player can read.
 *
 * Since D-251 wave III a converted refusal site also names the **key** it was
 * rendered from and the arguments it was rendered with, so the client can draw
 * the same sentence out of the same FTL. Both are optional: the conversion runs
 * module by module, and a site that has not been converted sends only the
 * words. Kept on the error as well as rendered, because a panel that wants to
 * react to a particular refusal should match on the key, never on the text.
 */
export class Refused extends Error {
  readonly code?: string;
  readonly args?: Record<string, unknown>;

  constructor(said: string, code?: string, args?: Record<string, unknown>) {
    super(said);
    this.code = code;
    this.args = args;
  }
}

type Waiting = {
  resolve: (answer: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

/** Where the session token lives between page refreshes (D-187). */
const TOKEN_KEY = "everselife.token";

/**
 * What the server says on its own (D-226). `event` is the journal kind,
 * `touches` names the parts of the player's state it changed -- the client
 * rereads those whether or not it knows the kind. `seq` is the journal row:
 * the last one seen goes back to the server on reconnect, and the server
 * replays what was missed.
 */
export type Happening = {
  event: string;
  seq?: number;
  at?: string;
  touches: string[];
  /** Who did it, when it was not you. */
  who?: string;
  [key: string]: unknown;
};

export type Listener = (happening: Happening) => void;

/** How long the session waits before rising again after a break, and the cap. */
const REVIVE_DELAY_MS = 1000;

const REVIVE_DELAY_CAP_MS = 30_000;

/** How long a command waits for its answer. */
const ANSWER_TIMEOUT_MS = 30_000;

/** The client session. Holds the socket, the commands in flight, and the listeners.
 *
 * The socket is two-way (D-226): commands go out numbered and come back by
 * number; in between the server speaks on its own, and whoever subscribed
 * with `on()` hears it. A session never reads answers by order.
 *
 * The socket does not live forever: the server and proxies cut idle
 * connections. A broken session rises by itself -- on close it reconnects,
 * identifies by token and names the last event it saw, so nothing said in
 * the meantime is lost. A command that finds a dead socket first waits for
 * that rise, and only then goes.
 *
 * Identification is email and password (D-187). The password is entered
 * once: the server gives a token, it lives in `localStorage`, and by it the
 * session rises after F5 and after a break. Logging out of the account panel
 * revokes and forgets the token.
 */
export class Session {
  private socket: WebSocket | null = null;
  private pending = new Map<number, Waiting>();
  private ticket = 0;
  private reviving: Promise<void> | null = null;
  private reviveTimer: ReturnType<typeof setTimeout> | null = null;
  private reviveDelay = REVIVE_DELAY_MS;
  private listeners = new Map<string, Set<Listener>>();
  /** The last journal row heard: the `since` of the next `hello`. */
  private seq = 0;
  account = "";
  name = "";
  token = "";
  /**
   * The language of this account (D-251 wave III): said at the greeting, like
   * `admin`, because it cannot be derived from anything else the server sends
   * (D-225) and it decides which words the client loads. Empty until a
   * greeting has happened -- the caller falls back to `DEFAULT_LOCALE`.
   */
  locale = "";
  /**
   * The alpha's debug widget, if this copy opens it for this name (D-229).
   * Said once at the greeting rather than in every `look`: it cannot be
   * derived from anything else the server sends (D-225), and it does not
   * change while the session lasts.
   */
  admin = false;

  /** The token of the last login, if any: auto-login starts from it. */
  static remembered(): string {
    try {
      return localStorage.getItem(TOKEN_KEY) ?? "";
    } catch {
      return "";
    }
  }

  private remember(token: string): void {
    this.token = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* приватный режим: жетон живёт только в памяти */
    }
  }

  /**
   * Hear what the server says: one kind (`"knowledge.learned"`), a prefix
   * (`"market."`), or everything (`"*"`). Returns the way to stop hearing.
   */
  on(kind: string, listener: Listener): () => void {
    let set = this.listeners.get(kind);
    if (!set) {
      set = new Set();
      this.listeners.set(kind, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
    };
  }

  private hear(happening: Happening): void {
    if (typeof happening.seq === "number" && happening.seq > this.seq) this.seq = happening.seq;
    const heard = new Set<Listener>();
    for (const [kind, set] of this.listeners) {
      const fits =
        kind === "*" ||
        kind === happening.event ||
        (kind.endsWith(".") && happening.event.startsWith(kind));
      if (fits) set.forEach((listener) => heard.add(listener));
    }
    heard.forEach((listener) => {
      try {
        listener(happening);
      } catch (error) {
        console.error("listener failed on", happening.event, error);
      }
    });
  }

  /** Bring up the socket. Identification is a separate step: a newcomer has nothing to identify with yet. */
  private async connect(): Promise<void> {
    await this.close();
    const socket = new WebSocket(WS);
    this.socket = socket;

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error(t("ui-wire-no-answer")));
    });
    this.reviveDelay = REVIVE_DELAY_MS;

    socket.onmessage = (message) => {
      const parsed = JSON.parse(message.data);
      if (typeof parsed.event === "string") {
        this.hear({ touches: [], ...parsed });
        return;
      }
      const waiting = typeof parsed.id === "number" ? this.pending.get(parsed.id) : undefined;
      if (!waiting) {
        console.warn("answer to nobody", parsed);
        return;
      }
      this.pending.delete(parsed.id);
      const { id: _id, ...answer } = parsed;
      if (typeof answer.refused === "string") {
        //: The one place the locale layer is actually used in anger (D-251
        //: wave III): where our bundle knows the key, the sentence is drawn
        //: here, from the same file the server drew its own from. Everywhere
        //: else the server's words stand.
        const code = typeof answer.code === "string" ? answer.code : undefined;
        const args = (answer.args ?? undefined) as Record<string, unknown> | undefined;
        waiting.reject(new Refused(refusalText(answer.refused, code, args), code, args));
      } else {
        waiting.resolve(answer);
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.pending.forEach((w) => w.reject(new Error(t("ui-wire-session-closed"))));
      this.pending.clear();
      //: The server speaks first now (D-226): a closed socket is a deaf one,
      //: so it rises on its own and not at the next command.
      if (this.token) this.scheduleRevive();
    };
  }

  private scheduleRevive(): void {
    if (this.reviveTimer) return;
    this.reviveTimer = setTimeout(() => {
      this.reviveTimer = null;
      this.revive().catch(() => {
        this.reviveDelay = Math.min(this.reviveDelay * 2, REVIVE_DELAY_CAP_MS);
        if (this.token) this.scheduleRevive();
      });
    }, this.reviveDelay);
  }

  /** Login by email and password. */
  async open(email: string, password: string): Promise<Record<string, unknown>> {
    await this.connect();
    return this.greet("hello", { email, password });
  }

  /** Login with last time's token: F5 does not ask for the password. */
  async resume(token: string): Promise<Record<string, unknown>> {
    await this.connect();
    try {
      return await this.greet("hello", { token });
    } catch (error) {
      //: A revoked or expired token is forgotten at once -- otherwise every
      //: login would start with the same refusal.
      if (error instanceof Refused) this.remember("");
      throw error;
    }
  }

  /** Registration (D-187): there is no identity yet -- it is printed at the
   * chosen door (D-153, D-182). Four client steps go as one command. */
  async create(application: Enrollment): Promise<Record<string, unknown>> {
    await this.connect();
    return this.greet("join", { ...application });
  }

  /** Logout: the token is revoked and forgotten, the socket closed. */
  async logout(): Promise<void> {
    try {
      if (this.socket?.readyState === WebSocket.OPEN) await this.send("account.logout");
    } catch {
      /* отзыв — вежливость: забыть жетон важнее, чем дождаться ответа */
    }
    this.remember("");
    this.name = "";
    this.account = "";
    this.admin = false;
    this.locale = "";
    this.seq = 0;
    await this.close();
  }

  private async greet(
    cmd: string,
    args: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    //: `since` turns the stream of events on: the last row heard, 0 for "from now".
    const hello = await this.send(cmd, { ...args, since: this.seq });
    this.account = String(hello.account ?? "");
    this.name = String(hello.hello ?? "");
    this.admin = hello.admin === true;
    if (typeof hello.locale === "string") this.locale = hello.locale;
    if (typeof hello.token === "string") this.remember(hello.token);
    return hello;
  }

  async send(cmd: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      //: Nothing to identify with yet: there was no session, nothing to repair.
      if (!this.token) throw new Error(t("ui-wire-no-session"));
      await this.revive();
    }
    const socket = this.socket!;
    const id = ++this.ticket;
    return new Promise((resolve, reject) => {
      //: An answer that never comes must not hang a button forever.
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(t("ui-wire-timed-out")));
      }, ANSWER_TIMEOUT_MS);
      this.pending.set(id, {
        resolve: (answer) => {
          clearTimeout(timer);
          resolve(answer);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      socket.send(JSON.stringify({ id, cmd, ...args }));
    });
  }

  /** Bring a broken session back up. One rise for everyone who caught it.
   *
   * A refused token -- revoked, expired -- is not a broken socket: the
   * session is over, the token is forgotten, and `session.lost` tells the
   * screen to show the login instead of rising forever. */
  private revive(): Promise<void> {
    this.reviving ??= (async () => {
      try {
        await this.connect();
        await this.greet("hello", { token: this.token });
      } catch (error) {
        if (error instanceof Refused) {
          this.remember("");
          this.name = "";
          await this.close();
          this.hear({ event: "session.lost", touches: [], reason: error.message });
        }
        throw error;
      } finally {
        this.reviving = null;
      }
    })();
    return this.reviving;
  }

  /** The live part of what the player sees (D-226): body, place, pocket, the road. */
  async look(): Promise<LiveLook> {
    const answer = await this.send("look");
    return answer.look as LiveLook;
  }

  /** One of the slow parts, by name; the client keeps it until an event touches it. */
  async part<K extends keyof Parts>(name: K): Promise<Parts[K]> {
    const answer = await this.send(PART_COMMANDS[name]);
    return answer[PART_ANSWERS[name]] as Parts[K];
  }

  /** Every slow part at once: the first read, and the reread after `session.reread`. */
  async parts(): Promise<Parts> {
    const [knowledge, profile, orders, deeds, shelf] = await Promise.all([
      this.part("knowledge"),
      this.part("profile"),
      this.part("orders"),
      this.part("deeds"),
      this.part("shelf"),
    ]);
    return { knowledge, profile, orders, deeds, shelf };
  }

  async close(): Promise<void> {
    if (this.reviveTimer) {
      clearTimeout(this.reviveTimer);
      this.reviveTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }
}
