// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Which track a place plays, a volume that outlives a reload, and the
 *  player's own moves: switching off and on, changing a track mid-fade, a
 *  file that is not there, a browser that refuses to play (D-333). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_VOLUME,
  FADE,
  TRACKS,
  gainFor,
  getMusic,
  peekMusicForTests,
  playFor,
  resetMusicForTests,
  setVolume,
  trackFor,
  trackUrl,
} from "../music";

//: The tests run in node, where there is no storage: a map stands in for it.
function storage(): Storage {
  const box = new Map<string, string>();
  return {
    getItem: (key: string) => box.get(key) ?? null,
    setItem: (key: string, value: string) => void box.set(key, value),
    removeItem: (key: string) => void box.delete(key),
    clear: () => box.clear(),
    key: (at: number) => [...box.keys()][at] ?? null,
    get length() {
      return box.size;
    },
  };
}

/** What the browser's audio element would do, counted. */
class FakeAudio {
  static made: FakeAudio[] = [];
  /** Whether `play` refuses, as a browser does before a gesture. */
  static refusing = false;
  loop = false;
  preload = "";
  paused = true;
  /** Times the source was set: setting it again restarts a download. */
  sets = 0;
  plays = 0;
  private source = "";
  private readonly handlers = new Map<string, (() => void)[]>();
  constructor() {
    FakeAudio.made.push(this);
  }
  get src(): string {
    return this.source;
  }
  set src(value: string) {
    this.source = value;
    this.sets += 1;
  }
  play(): Promise<void> {
    this.plays += 1;
    if (FakeAudio.refusing) return Promise.reject(new Error("no gesture"));
    this.paused = false;
    return Promise.resolve();
  }
  pause(): void {
    this.paused = true;
  }
  addEventListener(kind: string, handler: () => void): void {
    this.handlers.set(kind, [...(this.handlers.get(kind) ?? []), handler]);
  }
  fire(kind: string): void {
    for (const handler of this.handlers.get(kind) ?? []) handler();
  }
}

/** The gain node: the last level a ramp was told to reach. */
const gain = { level: 0 };

class FakeContext {
  currentTime = 0;
  destination = {};
  createGain() {
    return {
      gain: {
        value: 0,
        cancelScheduledValues: () => undefined,
        setValueAtTime: () => undefined,
        linearRampToValueAtTime: (level: number) => {
          gain.level = level;
        },
      },
      connect: () => undefined,
    };
  }
  createMediaElementSource() {
    return { connect: () => undefined };
  }
  resume(): Promise<void> {
    return Promise.resolve();
  }
}

/** Let the promises of `play()` settle. */
async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

const ms = FADE * 1000;

describe("which track a place plays", () => {
  it("gives a planet its own", () => {
    expect(trackFor("terra", false)).toBe("terra");
    expect(trackFor("aurora", false)).toBe("aurora");
    expect(trackFor("pyroxis", true)).toBe("pyroxis");
  });

  it("aboard, follows whether the hull is off its pier", () => {
    expect(trackFor("void", true)).toBe("space-transit");
    expect(trackFor("void", false)).toBe("space-orbit");
  });

  it("names a file under /music for every track", () => {
    for (const track of TRACKS) expect(trackUrl(track)).toBe(`/music/${track}.m4a`);
  });
});

describe("the volume setting", () => {
  beforeEach(() => vi.stubGlobal("localStorage", storage()));
  afterEach(() => {
    vi.unstubAllGlobals();
    resetMusicForTests();
  });

  it("opens half way", () => {
    resetMusicForTests();
    expect(getMusic()).toEqual({ volume: DEFAULT_VOLUME });
  });

  it("comes back as it was left", () => {
    resetMusicForTests();
    setVolume(0.3);
    resetMusicForTests();
    expect(getMusic()).toEqual({ volume: 0.3 });
  });

  it("keeps the slider within its ends and ignores a box that lies", () => {
    resetMusicForTests();
    setVolume(4);
    expect(getMusic().volume).toBe(1);
    localStorage.setItem("everselife.music.volume", "loud");
    resetMusicForTests();
    expect(getMusic().volume).toBe(DEFAULT_VOLUME);
  });

  it("turns the old switch into nought on the slider, once", () => {
    localStorage.setItem("everselife.music.volume", "0.7");
    localStorage.setItem("everselife.music.muted", "1");
    resetMusicForTests();
    expect(getMusic().volume).toBe(0);
    expect(localStorage.getItem("everselife.music.muted")).toBeNull();
    expect(localStorage.getItem("everselife.music.volume")).toBe("0");
  });

  it("gives the ear the square of the slider, and nought at nought", () => {
    expect(gainFor({ volume: 0.5 })).toBe(0.25);
    expect(gainFor({ volume: 1 })).toBe(1);
    expect(gainFor({ volume: 0 })).toBe(0);
  });

  it("does nothing without a browser to play in", () => {
    resetMusicForTests();
    //: Node has no window and no AudioContext: asking for a place only remembers.
    expect(() => playFor("terra", false)).not.toThrow();
    expect(peekMusicForTests().born).toBe(false);
  });
});

describe("the player", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", storage());
    vi.stubGlobal("Audio", FakeAudio);
    vi.stubGlobal("AudioContext", FakeContext);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    FakeAudio.made = [];
    FakeAudio.refusing = false;
    gain.level = 0;
    resetMusicForTests();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    resetMusicForTests();
  });

  /** A place asked for, then the slider touched: that gesture births the player. */
  async function born(track = "terra" as const): Promise<FakeAudio> {
    playFor(track, false);
    expect(peekMusicForTests().born).toBe(false);
    //: Not the default: the same value again is no change and no gesture.
    setVolume(0.6);
    await settle();
    expect(peekMusicForTests().born).toBe(true);
    expect(FakeAudio.made).toHaveLength(1);
    return FakeAudio.made[0];
  }

  it("is born on the slider's gesture and plays the place's track", async () => {
    const audio = await born();
    expect(audio.loop).toBe(true);
    expect(audio.src).toBe("/music/terra.m4a");
    expect(audio.plays).toBe(1);
    expect(audio.paused).toBe(false);
    expect(gain.level).toBeCloseTo(0.36);
  });

  it("at nought, fades and then pauses; raised again, plays again", async () => {
    const audio = await born();
    setVolume(0);
    expect(gain.level).toBe(0);
    expect(audio.paused).toBe(false);
    vi.advanceTimersByTime(ms + 10);
    expect(audio.paused).toBe(true);
    setVolume(0.6);
    await settle();
    expect(audio.paused).toBe(false);
    expect(gain.level).toBeCloseTo(0.36);
  });

  it("raised within the tail of switching off, keeps playing", async () => {
    const audio = await born();
    setVolume(0);
    vi.advanceTimersByTime(ms / 2);
    setVolume(0.6);
    vi.advanceTimersByTime(ms * 2);
    expect(audio.paused).toBe(false);
    expect(gain.level).toBeCloseTo(0.36);
  });

  it("changes the track after one fade, however many looks arrive meanwhile", async () => {
    const audio = await born();
    playFor("aurora", false);
    expect(audio.src).toBe("/music/terra.m4a");
    expect(gain.level).toBe(0);
    //: Looks keep coming during the fade; none of them restarts it.
    vi.advanceTimersByTime(ms / 2);
    playFor("aurora", false);
    vi.advanceTimersByTime(ms / 2 - 10);
    playFor("aurora", false);
    expect(audio.src).toBe("/music/terra.m4a");
    vi.advanceTimersByTime(20);
    expect(audio.src).toBe("/music/aurora.m4a");
    expect(FakeAudio.made).toHaveLength(1);
    expect(gain.level).toBeCloseTo(0.36);
  });

  it("asks once for a file that is not there", async () => {
    const audio = await born();
    audio.fire("error");
    const sets = audio.sets;
    const plays = audio.plays;
    playFor("terra", false);
    playFor("terra", false);
    setVolume(0.7);
    expect(audio.sets).toBe(sets);
    expect(audio.plays).toBe(plays);
    //: Another track is still worth asking for.
    playFor("aurora", false);
    vi.advanceTimersByTime(ms + 10);
    expect(audio.src).toBe("/music/aurora.m4a");
  });

  it("asks again after a refusal, without restarting the download", async () => {
    playFor("terra", false);
    FakeAudio.refusing = true;
    setVolume(0.6);
    await settle();
    const audio = FakeAudio.made[0];
    expect(audio.plays).toBe(1);
    expect(audio.paused).toBe(true);
    FakeAudio.refusing = false;
    playFor("terra", false);
    await settle();
    expect(audio.plays).toBe(2);
    expect(audio.sets).toBe(1);
    expect(audio.paused).toBe(false);
  });
});
