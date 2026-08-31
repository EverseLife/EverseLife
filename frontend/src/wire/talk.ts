// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What people say to each other, in the three ways the world allows it.
 *
 * Out loud in the room, where only whoever stands there hears it (D-043); in
 * writing to one person, kept and carried by the road (D-222); and to a
 * subscription, which is a city or a person publishing to whoever asked.
 *
 * They are one file because they are one distinction: everything here has an
 * author, an addressee and a moment of arrival, and the shapes differ mostly
 * in how wide the addressee is.
 */

/** A remark as heard by someone standing in the location (D-043, D-050). */
export type ChatLine = {
  id: string;
  who: string;
  kind: "speech" | "action" | "ooc";
  quiet: boolean;
  text: string;
  overheard: boolean;
  source?: string;
  at: string;
};

/** A circle: membership visible, content not. */
export type Circle = { id: string; name?: string; members: string[]; mine: boolean };

/** The Net (D-222): correspondence kept, arriving by the road. */
export type Thread = {
  id: string;
  /** The other party. */
  who: string;
  surname: string;
  last_at?: string;
  /** The last letter the reader can already see. */
  preview?: string;
  unread: number;
};

export type Letter = {
  id: string;
  who: string;
  mine: boolean;
  text: string;
  sent_at: string;
  /** When it reaches the reader: for one's own, "on the way" until then. */
  delivered_at: string;
};

export type Channel = {
  id: string;
  name: string;
  about: string;
  /** The city's: marked as official. */
  official: boolean;
  /** The reader writes here. */
  writable: boolean;
  /** Implied by citizenship: cannot be dropped. */
  implied: boolean;
  /** Who writes it: the author's name, or the city's. */
  by: string;
  last_at?: string;
  unread: number;
};

/** A channel found by search: subscribed or not. */
export type ChannelFound = Pick<Channel, "id" | "name" | "about" | "official" | "by"> & {
  subscribed: boolean;
};

export type Post = { id: string; who: string; text: string; at: string; delivered_at: string };
