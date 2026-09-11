// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Which names the map writes (`map/labels`): the ones that fit, in the order
 * they are wanted. Pure, so pinned here apart from the drawing.
 */

import { describe, expect, it } from "vitest";

import { DOOR_EM, HULL_EM, LABEL_EM, legible, type Named } from "../panels/map/labels";

const name = (key: string, x: number, y: number, text: string, rank = 3): Named => ({
  key,
  x,
  y,
  text,
  em: LABEL_EM,
  rank,
});

describe("the names the map has room for", () => {
  it("writes what does not run into anything", () => {
    const kept = legible([
      name("a", 0, 0, "Рынок"),
      //: Two hundred units away: nothing of one reaches the other.
      name("b", 200, 0, "Библиотека"),
    ]);
    expect([...kept].sort()).toEqual(["a", "b"]);
  });

  it("leaves off the name that would be written over one already placed", () => {
    //: The capital's own case, which was drawn as «Ядро: Принтер
    //: ПредтечРынок». The distance is measured in **ems** and not in units
    //: on purpose: the size of a name is tuned by eye (`LABEL_EM` went from
    //: eight to six on 2026-09-11), and a fixture written in units stops
    //: overlapping at the first such tuning and passes on saying nothing.
    const apart = 3 * LABEL_EM;
    const kept = legible([
      name("core", 0, 0, "Ядро: Принтер Предтеч"),
      name("market", apart, 0, "Рынок"),
    ]);
    expect([...kept]).toEqual(["core"]);
    //: And far enough apart both are written: the rule is "what does not
    //: fit", not "the second name".
    expect([...legible([
      name("core", 0, 0, "Ядро: Принтер Предтеч"),
      name("market", 12 * LABEL_EM, 0, "Рынок"),
    ])].sort()).toEqual(["core", "market"]);
  });

  it("keeps the one underfoot, then the hull, then the city, then what a step reaches", () => {
    //: Stepped in half-ems, so the five span about one name's own width --
    //: a crowd whatever the size is tuned to.
    const step = LABEL_EM / 2;
    const crowd = [
      name("far", 0, 0, "Дальний", 4),
      name("near", step, 0, "Соседний", 3),
      name("city", 2 * step, 0, "Город", 2),
      //: A hull is the same diamond as every other and has nothing but its
      //: name; moored among a domed city's flats it is lost without it.
      name("hull", 3 * step, 0, "Заря", 1),
      name("here", 4 * step, 0, "Здесь", 0),
    ];
    //: All five sit within one name's width of each other: only the first
    //: wanted survives, and it is the one the player is standing in.
    expect([...legible(crowd)]).toEqual(["here"]);
    //: The order of the list does not decide it -- the rank does.
    expect([...legible([...crowd].reverse())]).toEqual(["here"]);
  });

  it("settles a tie by the key, so the same map drops the same name", () => {
    const pair = [name("b", 0, 0, "Первый"), name("a", 10, 0, "Второй")];
    expect([...legible(pair)]).toEqual(["a"]);
    expect([...legible([...pair].reverse())]).toEqual(["a"]);
  });

  it("says nothing about a node with no name", () => {
    //: A find has no name (D-321), and an empty label takes no room.
    const kept = legible([name("find", 0, 0, ""), name("near", 4, 0, "Рынок")]);
    expect([...kept]).toEqual(["near"]);
  });

  it("gives way with a door's caption before it gives way with a name", () => {
    //: «КОСМОДРОМ» under the pier and «Заря» under the hull moored beside it
    //: sit at the same height and ran into each other. The caption says what
    //: a node is; the name says which one it is, and that is worth more.
    const kept = legible([
      { key: "port door", x: 0, y: 30, text: "космодром", em: DOOR_EM, door: true, rank: 5 },
      { key: "hull", x: 20, y: 30, text: "Заря", em: HULL_EM, rank: 1 },
    ]);
    expect([...kept]).toEqual(["hull"]);
    //: With room for both, both are written.
    const roomy = legible([
      { key: "port door", x: 0, y: 30, text: "космодром", em: DOOR_EM, door: true, rank: 5 },
      { key: "hull", x: 200, y: 30, text: "Заря", em: HULL_EM, rank: 1 },
    ]);
    expect([...roomy].sort()).toEqual(["hull", "port door"]);
  });

  it("gives a hull its own size, and its name hangs clear of the pier's", () => {
    const kept = legible([
      name("pier", 0, -9, "Космодром"),
      //: A hull's name is set larger and hangs below it (`.node.ship`).
      { key: "hull", x: 0, y: 21, text: "Заря", em: HULL_EM, rank: 3 },
    ]);
    expect([...kept].sort()).toEqual(["hull", "pier"]);
  });
});
