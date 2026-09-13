// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The hull's plumbing as a short list (D-288): which vessels each machine
 * drinks from and pours into.
 *
 * One section per port of every machine aboard that runs a liquid -- the
 * fuel of an engine, the oxygen of the life support, the water, oxygen and
 * hydrogen of the air machine, the oxygen of the hydroponics (D-340) -- and
 * under it every installed vessel of the hull that holds that liquid or
 * nothing yet, each with the room it stands in and the owner's name for it.
 * Nothing ticked is a port that reaches nothing (D-288 as amended
 * 2026-09-04); ticking makes the line of the ticked, and the order of
 * ticking is the order of use or of filling; «выше» moves one up. The full
 * picture, and the naming, is the ship's scheme (`Scheme`); this list stays
 * for the quick edit at the console.
 *
 * The picture is the server's (`line.view`) and is reread when the world
 * says the hull changed (D-226): a line drawn, a vessel put up or poured
 * into, a machine taken down.
 */

import { useCallback, useEffect, useState } from "react";
import { useEdition, useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { goodsName } from "../../names";
import { suits, type Feed as Plumbing, type FeedMachine, type FeedPort, type FeedVessel } from "../../wire/lines";
import type { Vessel } from "./model";

export function Feed({
  vessel,
  busy,
  plumb,
}: {
  vessel: Vessel;
  busy: boolean;
  /** Draw one port's lines: the vessels in order, or none for nothing. */
  plumb: (machine: string, port: string, vessels: string[]) => Promise<unknown>;
}) {
  const session = useSession();
  const edition = useEdition("line.", "station.", "storage.", "ship.");
  const [feed, setFeed] = useState<Plumbing | null>(null);

  const load = useCallback(async () => {
    try {
      setFeed(await session.send<Plumbing>("line.view", { ship: vessel.ship }));
    } catch {
      setFeed(null);
    }
  }, [session, vessel.ship]);

  useEffect(() => {
    void load();
  }, [load, edition]);

  if (feed === null) return null;
  return (
    <div className="feed">
      <h3>{t("ui-ship-feed")}</h3>
      {feed.machines.length === 0 ? (
        <p className="note">{t("ui-ship-feed-none")}</p>
      ) : (
        <>
          <p className="note">{t("ui-ship-feed-hint")}</p>
          {feed.machines.map((machine) =>
            machine.ports.map((port) => (
              <Port
                key={`${machine.item}:${port.port}`}
                vessels={feed.vessels}
                machine={machine}
                port={port}
                busy={busy}
                draw={(chosen) => plumb(machine.item, port.port, chosen)}
              />
            )),
          )}
        </>
      )}
    </div>
  );
}

/** The word for which way a port runs (D-340). */
const WAY: Record<FeedPort["way"], string> = {
  in: "ui-ship-scheme-way-in",
  out: "ui-ship-scheme-way-out",
  vent: "ui-ship-scheme-way-vent",
};

function Port({
  vessels,
  machine,
  port,
  busy,
  draw,
}: {
  vessels: FeedVessel[];
  machine: FeedMachine;
  port: FeedPort;
  busy: boolean;
  draw: (chosen: string[]) => Promise<unknown>;
}) {
  const names = useNames();
  const any = port.lines.length === 0;
  //: The ticked first, in their order, then the rest by room: the list reads
  //: as the line does.
  const rows = [
    ...port.lines
      .map((id) => vessels.find((one) => one.item === id))
      .filter((one): one is FeedVessel => one !== undefined),
    ...vessels.filter((one) => !port.lines.includes(one.item) && suits(port, one)),
  ];
  const toggle = (id: string) =>
    draw(port.lines.includes(id) ? port.lines.filter((one) => one !== id) : [...port.lines, id]);
  const up = (id: string) => {
    const at = port.lines.indexOf(id);
    if (at <= 0) return;
    const next = [...port.lines];
    [next[at - 1], next[at]] = [next[at], next[at - 1]];
    void draw(next);
  };
  return (
    <div className="feed-port">
      <p>
        <b>{goodsName(names, machine.goods)}</b> · {machine.node_name} · {t(WAY[port.way])} ·{" "}
        <span className="note">{port.liquids.map((one) => goodsName(names, one)).join(", ")}</span>
        {any ? (
          <span className="note"> · {t("ui-ship-scheme-no-line", { way: port.way })}</span>
        ) : (
          <>
            {" "}
            <button className="quiet" disabled={busy} onClick={() => void draw([])}>
              {t("ui-ship-feed-reset")}
            </button>
          </>
        )}
      </p>
      {rows.length === 0 ? (
        <p className="note">{t("ui-ship-feed-no-vessels")}</p>
      ) : (
        <ul className="feed-vessels">
          {rows.map((one) => {
            const at = port.lines.indexOf(one.item);
            return (
              <li key={one.item}>
                <label>
                  <input
                    type="checkbox"
                    checked={at >= 0}
                    disabled={busy}
                    onChange={() => void toggle(one.item)}
                  />{" "}
                  {one.name ?? goodsName(names, one.goods)} · {one.node_name} ·{" "}
                  <span className="note">
                    {one.holds.length === 0
                      ? t("ui-ship-feed-empty")
                      : one.holds
                          .map((held) => `${goodsName(names, held.goods)} ${held.amount.toFixed(0)}`)
                          .join(", ")}
                  </span>
                </label>
                {at > 0 && (
                  <button className="quiet" disabled={busy} onClick={() => up(one.item)}>
                    {t("ui-ship-feed-up")}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
