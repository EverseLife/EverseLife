// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Background music (D-333, `50-interface/10-sound`).
 *
 * One track per place, and the place is the one the theme already reads
 * (`theme.ts`, D-074, D-080): the planet under the feet, or the void when
 * aboard a ship. The void has two tracks -- a hull off its pier, under way
 * or adrift, and a hull moored, in orbit or at a pier -- and `look.ships.
 * underway` tells them apart: a room aboard shows no pier and no orbit, so
 * the client cannot tell from anything else it is sent (D-225).
 *
 * The switch follows `look`, exactly as the theme does: the server speaks,
 * the screen rereads, and the music changes with the screen (D-226). The only
 * timers in this file are the tails of a fade, and they schedule sound, not
 * data.
 *
 * The volume is a view setting under D-298: it lives in `localStorage`,
 * belongs to the browser rather than the account, and survives a logout --
 * `forgetKept` never sees the `everselife.music.` prefix. One number, and
 * nought is off: the slider's left end is the switch (owner, 2026-09-12).
 *
 * A browser will not play a sound before the person has touched the page.
 * So nothing is created until the first pointer or key after `playFor` has
 * been asked for a track, and a person who has the music off never gets an
 * audio element at all.
 */

import { useSyncExternalStore } from "react";

import { readKept, writeKept } from "./kept";
import type { Skin } from "./theme";

/** The tracks: file names under `public/music/`, one per place. */
export const TRACKS = [
  "terra",
  "aurora",
  "pyroxis",
  "aquatica",
  "space-transit",
  "space-orbit",
] as const;
export type Track = (typeof TRACKS)[number];

/**
 * Which track a place plays.
 *
 * A planet plays its own. Aboard, the hull's state decides: off its pier --
 * under way or adrift -- is the transit, moored anywhere -- an orbit, a pier
 * -- is the orbit track. A ship standing at a Terran spaceport is still
 * aboard, and the theme already says "void" there; the music says the same.
 */
export function trackFor(skin: Skin, underway: boolean): Track {
  if (skin === "void") return underway ? "space-transit" : "space-orbit";
  return skin;
}

/** Where a track's file is served from: `public/music/<track>.m4a`. */
export function trackUrl(track: Track): string {
  return `/music/${track}.m4a`;
}

const VOLUME_KEY = "everselife.music.volume";
/** The key of the struck switch, cleared when found (see `readSetting`). */
const MUTED_KEY = "everselife.music.muted";
/** Half way, on by default: the owner's call. */
export const DEFAULT_VOLUME = 0.5;

export type MusicSetting = {
  /** 0..1, the slider's position; nought is off. */
  volume: number;
};

const listeners = new Set<() => void>();
let setting: MusicSetting = readSetting();

function readSetting(): MusicSetting {
  const raw = readKept(VOLUME_KEY);
  const parsed = raw === null ? NaN : Number(raw);
  let volume = Number.isFinite(parsed) ? clamp(parsed) : DEFAULT_VOLUME;
  //: For one evening (2026-09-12) the switch was a key of its own. A
  //: browser that still holds it keeps the silence it chose -- as nought
  //: on the slider -- and the key goes.
  if (readKept(MUTED_KEY) !== null) {
    if (readKept(MUTED_KEY) === "1") volume = 0;
    writeKept(MUTED_KEY, null);
    writeKept(VOLUME_KEY, String(volume));
  }
  return { volume };
}

function clamp(value: number): number {
  return Math.min(1, Math.max(0, value));
}

export function getMusic(): MusicSetting {
  return setting;
}

/** Move the slider. Nought switches the music off, anything above it on. */
export function setVolume(volume: number): void {
  const next = { volume: clamp(volume) };
  if (next.volume === setting.volume) return;
  setting = next;
  writeKept(VOLUME_KEY, String(next.volume));
  changed();
}

export function subscribeMusic(notify: () => void): () => void {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

/** The setting, for whoever renders it. */
export function useMusic(): MusicSetting {
  return useSyncExternalStore(subscribeMusic, getMusic, getMusic);
}

/**
 * What the ear gets for a slider position. A linear slider feels wrong --
 * the loud half is all the same and the quiet half does everything -- so the
 * gain is the square of the position.
 */
export function gainFor({ volume }: MusicSetting): number {
  return volume * volume;
}

function changed(): void {
  for (const notify of [...listeners]) notify();
  //: The slider is a gesture itself: a browser that refused to play before
  //: one lets the player be born right here. `wake` applies the setting to
  //: a player it makes; an existing one is told.
  if (player) player.apply();
  else wake();
}

/* ---------------------------------------------------------------------- */

/** How long a track takes to leave, and the next one to arrive, in seconds. */
export const FADE = 1.2;

/**
 * The one player of the page.
 *
 * One `<audio loop>` fed through a gain node: the loop is the browser's own,
 * the gain is what the slider moves and what a track change ramps. Changing
 * a track is a fade to silence, a new source, a fade back up; switching off
 * is a fade to silence and a pause, so that a loop nobody hears is not
 * decoded for hours.
 *
 * Two timers, never both: `swap` is the tail of a track change, `resting`
 * the tail of switching off. Each is cleared by whatever supersedes it.
 */
class Player {
  private readonly context: AudioContext;
  private readonly gain: GainNode;
  private element: HTMLAudioElement | null = null;
  /** The track in the element, or nothing while it failed or was refused. */
  private playing: Track | null = null;
  /** The track a running fade-out is about to hand over to. */
  private pending: Track | null = null;
  /** Tracks whose file did not load: asked once per page, not once per look. */
  private readonly failed = new Set<Track>();
  private swap: ReturnType<typeof setTimeout> | null = null;
  private resting: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.context = new AudioContext();
    this.gain = this.context.createGain();
    this.gain.gain.value = 0;
    this.gain.connect(this.context.destination);
  }

  /** Bring the sound to what the setting and the place say, now. */
  apply(): void {
    const level = gainFor(setting);
    if (level === 0) {
      this.rest();
      return;
    }
    //: Switched back on within the tail of switching off: the pause must
    //: not land on a track that is being raised.
    if (this.resting) {
      clearTimeout(this.resting);
      this.resting = null;
    }
    if (wanted && this.playing !== wanted && !this.failed.has(wanted)) {
      this.change(wanted);
      return;
    }
    if (!this.element || this.playing === null) return;
    //: iOS suspends the context when the page goes to the background, and a
    //: paused element stays paused after `rest`: both are woken here.
    void this.context.resume();
    if (this.element.paused) void this.element.play().catch(() => undefined);
    this.ramp(level);
  }

  /** Off: fade to silence, then stop decoding a loop nobody hears. */
  private rest(): void {
    if (this.swap) {
      clearTimeout(this.swap);
      this.swap = null;
      this.pending = null;
    }
    if (this.resting || !this.element || this.element.paused) return;
    this.ramp(0);
    //: The tail of the fade: a sound timer, not a data one (D-226).
    this.resting = setTimeout(() => {
      this.resting = null;
      this.element?.pause();
    }, FADE * 1000);
  }

  /** Play the track the place asks for, fading between two if one is on. */
  private change(track: Track): void {
    //: Already on its way: every look during the fade asks for the same
    //: thing, and restarting the timer each time would never let it fire.
    if (this.pending === track) return;
    if (this.swap) clearTimeout(this.swap);
    //: Nothing to fade out yet: start straight away, ramping in.
    if (!this.element || this.playing === null) {
      this.swap = null;
      this.pending = null;
      this.start(track);
      return;
    }
    this.pending = track;
    this.ramp(0);
    //: The tail of the fade: a sound timer, not a data one (D-226).
    this.swap = setTimeout(() => {
      this.swap = null;
      this.pending = null;
      this.start(track);
    }, FADE * 1000);
  }

  private start(track: Track): void {
    if (!this.element) {
      const element = new Audio();
      element.loop = true;
      element.preload = "auto";
      //: A missing file is a track not yet delivered, not a fault worth a
      //: request every look: it stays silent until the page is reloaded.
      element.addEventListener("error", () => {
        if (this.playing) this.failed.add(this.playing);
        this.playing = null;
      });
      this.context.createMediaElementSource(element).connect(this.gain);
      this.element = element;
    }
    this.playing = track;
    //: Setting the same source again restarts a six-minute download: only
    //: a different track touches it.
    const url = trackUrl(track);
    if (this.element.src !== url && !this.element.src.endsWith(url)) this.element.src = url;
    void this.context.resume();
    void this.element.play().catch(() => {
      //: Refused before a gesture, or while the tab is muted by the browser:
      //: the next gesture or look asks again, with the source left as it is.
      this.playing = null;
    });
    this.ramp(gainFor(setting));
  }

  private ramp(level: number): void {
    const at = this.context.currentTime;
    this.gain.gain.cancelScheduledValues(at);
    this.gain.gain.setValueAtTime(this.gain.gain.value, at);
    this.gain.gain.linearRampToValueAtTime(level, at + FADE);
  }
}

let player: Player | null = null;
/** The track the place asks for; the player follows it when it exists. */
let wanted: Track | null = null;
let armed = false;

/**
 * Tell the music where the player stands. Called on every look, like the
 * theme; before the first gesture it only remembers.
 */
export function playFor(skin: Skin, underway: boolean): void {
  wanted = trackFor(skin, underway);
  if (player) {
    player.apply();
    return;
  }
  arm();
}

/** The first pointer or key after login is what lets the browser play. */
function arm(): void {
  if (armed || typeof window === "undefined") return;
  armed = true;
  const once = () => {
    window.removeEventListener("pointerdown", once);
    window.removeEventListener("keydown", once);
    armed = false;
    //: Nothing to play yet, or the music is off: the next gesture after the
    //: place or the setting changes is the one that counts.
    if (!wake()) arm();
  };
  window.addEventListener("pointerdown", once);
  window.addEventListener("keydown", once);
}

/** Make the player, if there is something to play and a way to play it. */
function wake(): boolean {
  if (player) return true;
  if (!wanted || gainFor(setting) === 0 || typeof AudioContext === "undefined") return false;
  player = new Player();
  player.apply();
  return true;
}

/** For tests: forget the player, the place and whoever was listening. */
export function resetMusicForTests(): void {
  player = null;
  wanted = null;
  armed = false;
  listeners.clear();
  setting = readSetting();
}

/** For tests: is there a player. */
export function peekMusicForTests(): { born: boolean } {
  return { born: player !== null };
}
