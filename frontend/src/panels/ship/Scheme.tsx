// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * «Схема корабля»: the hull's lines as a picture, and the place they are
 * edited (D-288, D-340; owner, 2026-09-13).
 *
 * Opened at the bridge. Lanes are the compartments in laying order; machines
 * stand left, vessels right; every line runs from a machine's port to a
 * vessel in the tone of the port's liquid. The picture is computed from
 * `line.view` and nothing about it is stored (`lanes.layout`).
 *
 * What the owner does here, and nowhere else so fully:
 *
 * * **draws a line** -- drags from a port's dot to a vessel, or presses the
 *   dot and then the vessel. A vessel that holds another liquid takes no
 *   drop: the engine would never pour into it (D-288, one liquid per vessel);
 * * **orders and cuts** -- a port pressed, or a line pressed, opens the port
 *   below the drawing: its vessels in line order, moved up and down, taken
 *   off one by one or all at once;
 * * **names a vessel** -- a vessel pressed with no port chosen opens its
 *   name: «Кислород, левый борт» instead of «Кислородный баллон».
 *
 * A crew member reads the same picture without the handles: the engine takes
 * these orders from the owner at a console only (`_commanded_by`).
 *
 * Reread when the world says the hull changed (D-226): a line drawn, a
 * vessel named or poured into, a machine put up, an automat standing still.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Look } from "../../api";
import { Refusal, useActions, useEdition, useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { goodsName, type Names } from "../../names";
import { Rule } from "../../Rule";
import { STALL_POWER, suits, type Feed, type FeedPort, type FeedVessel } from "../../wire/lines";
import { curve } from "../curve";
import { layout, moved, portKey, toneOfVessel, withVessel, without } from "./lanes";
import { spelt } from "./model";

/** How far a press may wander before it is a drag rather than a press, px. */
const PRESS_SLOP = 4;

type Chosen = { machine: string; port: string };
type Drag = Chosen & { x0: number; y0: number; x: number; y: number };

/** The word for which way a port runs. */
const WAY: Record<FeedPort["way"], string> = {
  in: "ui-ship-scheme-way-in",
  out: "ui-ship-scheme-way-out",
  vent: "ui-ship-scheme-way-vent",
};

/** What that way means for the order of the line. */
const WAY_NOTE: Record<FeedPort["way"], string> = {
  in: "ui-ship-scheme-way-in-note",
  out: "ui-ship-scheme-way-out-note",
  vent: "ui-ship-scheme-way-vent-note",
};

export function Scheme({ look }: { look: Look }) {
  const session = useSession();
  const names = useNames();
  const acting = useActions();
  const { busy, act } = acting;
  const edition = useEdition("line.", "station.", "storage.", "ship.");
  const [feed, setFeed] = useState<Feed | null>(null);
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [naming, setNaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const field = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setFeed(await session.send<Feed>("line.view", {}));
    } catch {
      setFeed(null);
    }
  }, [session]);
  useEffect(() => {
    void load();
  }, [load, edition, look.node?.key]);

  const picture = useMemo(() => (feed ? layout(feed) : null), [feed]);

  const portOf = (which: Chosen | null): FeedPort | undefined =>
    which === null
      ? undefined
      : feed?.machines
          .find((one) => one.item === which.machine)
          ?.ports.find((one) => one.port === which.port);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await load();
    });
  const plumb = (which: Chosen, vessels: string[]) =>
    go(() =>
      session.send("line.set", {
        ship: feed?.ship,
        machine: which.machine,
        port: which.port,
        vessels,
      }),
    );
  const rename = (vessel: string, title: string) =>
    go(() => session.send("line.name", { ship: feed?.ship, vessel, name: title }));

  /** Put a vessel on a port's line, if the engine would pour or draw through it. */
  const join = (which: Chosen, vessel: FeedVessel) => {
    const port = portOf(which);
    if (!port || !suits(port, vessel)) return;
    if (!port.lines.includes(vessel.item)) void plumb(which, withVessel(port, vessel.item));
  };

  //: The drag lives on the window while it lasts: the pointer leaves the dot
  //: at once, and a vessel card is where it ends.
  useEffect(() => {
    if (drag === null) return;
    const where = (event: PointerEvent) => {
      const box = field.current?.getBoundingClientRect();
      return box ? { x: event.clientX - box.left, y: event.clientY - box.top } : null;
    };
    const move = (event: PointerEvent) => {
      const at = where(event);
      if (at) setDrag((was) => (was ? { ...was, ...at } : was));
    };
    const up = (event: PointerEvent) => {
      const at = where(event);
      const pressed =
        at === null || Math.hypot(at.x - drag.x0, at.y - drag.y0) <= PRESS_SLOP;
      if (pressed) {
        setChosen((was) =>
          was?.machine === drag.machine && was.port === drag.port
            ? null
            : { machine: drag.machine, port: drag.port },
        );
      } else {
        const target = document
          .elementFromPoint(event.clientX, event.clientY)
          ?.closest<HTMLElement>("[data-vessel]")?.dataset.vessel;
        const vessel = feed?.vessels.find((one) => one.item === target);
        if (vessel) join(drag, vessel);
      }
      setDrag(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- the drag's own start is its key
  }, [drag?.machine, drag?.port, drag?.x0, drag?.y0]);

  if (feed === null || picture === null) return null;
  const mine = feed.yours;
  const active = drag ?? chosen;
  const activePort = portOf(active);
  const nameOf = (vessel: FeedVessel) => vessel.name ?? goodsName(names, vessel.goods);

  return (
    <section className="scheme-window">
      <Refusal of={acting} />
      <h2>
        {t("ui-ship-scheme")}
        <Rule>{t("ui-ship-scheme-rule")}</Rule>
      </h2>
      {feed.machines.length === 0 ? (
        <p className="note">{t("ui-ship-scheme-none")}</p>
      ) : (
        <p className="note">{mine ? t("ui-ship-scheme-hint") : t("ui-ship-scheme-read-only")}</p>
      )}

      <div className="scheme">
        <div
          ref={field}
          className="scheme-field"
          style={{ width: picture.width, height: picture.height }}
        >
          {picture.lanes.map((lane) => (
            <div
              key={lane.node}
              className="scheme-lane"
              style={{ top: lane.y, height: lane.h, width: picture.width }}
            >
              <span className="note">{lane.name}</span>
            </div>
          ))}

          <svg className="scheme-lines" width={picture.width} height={picture.height}>
            {picture.lines.map((line) => {
              const lit =
                active?.machine === line.machine && active.port === line.port ? " lit" : "";
              return (
                <g key={line.key} className={`scheme-line-pair tone-${line.tone}${lit}`}>
                  {/* The wide invisible twin is the hit area: a hairline is not a button. */}
                  <path
                    className="scheme-line-hit"
                    d={line.d}
                    onClick={() => setChosen({ machine: line.machine, port: line.port })}
                  >
                    <title>{t("ui-ship-scheme-line-pick")}</title>
                  </path>
                  <path className="scheme-line" d={line.d} />
                  <text className="scheme-rank" x={line.end.x - 6} y={line.end.y - 4}>
                    {line.rank + 1}
                  </text>
                </g>
              );
            })}
            {drag !== null && picture.ports.get(portKey(drag.machine, drag.port)) && (
              <path
                className={`scheme-line drawing tone-${drag.port}`}
                d={curve(
                  picture.ports.get(portKey(drag.machine, drag.port))!.x,
                  picture.ports.get(portKey(drag.machine, drag.port))!.y,
                  drag.x,
                  drag.y,
                )}
              />
            )}
          </svg>

          {feed.machines.map((machine) => {
            const spot = picture.machines.get(machine.item);
            if (!spot) return null;
            return (
              <div
                key={machine.item}
                className="scheme-card machine"
                style={{ left: spot.x, top: spot.y, width: spot.w, height: spot.h }}
              >
                <p className="sign">{goodsName(names, machine.goods)}</p>
                {machine.ports.map((port) => {
                  const on = active?.machine === machine.item && active.port === port.port;
                  return (
                    <div key={port.port} className={"scheme-port-row" + (on ? " on" : "")}>
                      <span>
                        {t(WAY[port.way])} ·{" "}
                        {port.liquids.map((one) => goodsName(names, one)).join(", ")}
                      </span>
                      <button
                        className={`scheme-port tone-${port.port}`}
                        aria-label={t("ui-ship-scheme-port", {
                          goods: goodsName(names, port.liquids[0] ?? port.port),
                        })}
                        disabled={busy || !mine}
                        onPointerDown={(event) => {
                          const box = field.current?.getBoundingClientRect();
                          if (!box) return;
                          event.preventDefault();
                          const x = event.clientX - box.left;
                          const y = event.clientY - box.top;
                          setDrag({ machine: machine.item, port: port.port, x0: x, y0: y, x, y });
                        }}
                      />
                    </div>
                  );
                })}
                {machine.stall && (
                  //: The card is narrow and the reason is the one line worth
                  //: reading whole: the full words ride on the title.
                  <p className="scheme-stall" title={stallWords(machine.stall, machine.ports, names)}>
                    {stallWords(machine.stall, machine.ports, names)}
                  </p>
                )}
              </div>
            );
          })}

          {feed.vessels.map((vessel) => {
            const spot = picture.vessels.get(vessel.item);
            if (!spot) return null;
            const takes = activePort ? suits(activePort, vessel) : null;
            const state = takes === null ? "" : takes ? " can-take" : " cannot";
            return (
              <button
                key={vessel.item}
                data-vessel={vessel.item}
                className={`bare scheme-card vessel tone-${toneOfVessel(feed, vessel)}${state}`}
                style={{ left: spot.x, top: spot.y, width: spot.w, height: spot.h }}
                disabled={busy || !mine}
                onClick={() => {
                  if (chosen) {
                    join(chosen, vessel);
                    return;
                  }
                  setName(vessel.name ?? "");
                  setNaming(vessel.item);
                }}
              >
                <span className="sign">{nameOf(vessel)}</span>
                {vessel.name && <span className="note">{goodsName(names, vessel.goods)}</span>}
                <span className="note">
                  {vessel.holds.length === 0
                    ? t("ui-ship-feed-empty")
                    : vessel.holds
                        .map((held) => `${goodsName(names, held.goods)} ${spelt(held.amount)}`)
                        .join(", ")}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {mine && chosen !== null && activePort && (
        <div className="scheme-panel">
          <h3>
            {goodsName(names, feed.machines.find((one) => one.item === chosen.machine)?.goods ?? "")}{" "}
            · {t(WAY[activePort.way])} ·{" "}
            {activePort.liquids.map((one) => goodsName(names, one)).join(", ")}
          </h3>
          <p className="note">{t(WAY_NOTE[activePort.way])}</p>
          {activePort.lines.length === 0 ? (
            <p className="note">{t("ui-ship-scheme-no-line", { way: activePort.way })}</p>
          ) : (
            <ol className="scheme-order">
              {activePort.lines.map((id, at) => {
                const vessel = feed.vessels.find((one) => one.item === id);
                if (!vessel) return null;
                return (
                  <li key={id}>
                    {nameOf(vessel)} · {vessel.node_name}{" "}
                    <button
                      className="quiet"
                      disabled={busy || at === 0}
                      onClick={() => void plumb(chosen, moved(activePort, id, -1))}
                    >
                      {t("ui-ship-feed-up")}
                    </button>
                    <button
                      className="quiet"
                      disabled={busy || at === activePort.lines.length - 1}
                      onClick={() => void plumb(chosen, moved(activePort, id, 1))}
                    >
                      {t("ui-ship-scheme-down")}
                    </button>
                    <button
                      className="quiet"
                      disabled={busy}
                      onClick={() => void plumb(chosen, without(activePort, id))}
                    >
                      {t("ui-ship-scheme-unline")}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
          <p>
            {activePort.lines.length > 0 && (
              <button className="quiet" disabled={busy} onClick={() => void plumb(chosen, [])}>
                {t("ui-ship-feed-reset")}
              </button>
            )}{" "}
            <button className="quiet" onClick={() => setChosen(null)}>
              {t("ui-ship-scheme-done")}
            </button>
          </p>
        </div>
      )}

      {mine && chosen === null && naming !== null && (
        <div className="scheme-panel">
          <label>
            {t("ui-ship-scheme-name-label")}{" "}
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>{" "}
          <button
            disabled={busy || !name.trim()}
            onClick={() => {
              void rename(naming, name);
              setNaming(null);
            }}
          >
            {t("ui-ship-name-set")}
          </button>{" "}
          {feed.vessels.find((one) => one.item === naming)?.name && (
            <button
              className="quiet"
              disabled={busy}
              onClick={() => {
                void rename(naming, "");
                setNaming(null);
              }}
            >
              {t("ui-ship-scheme-name-clear")}
            </button>
          )}{" "}
          <button className="quiet" onClick={() => setNaming(null)}>
            {t("ui-ship-cancel")}
          </button>
        </div>
      )}
    </section>
  );
}

/** Why an automat stands, in words: the cells, or the port that stopped it. */
function stallWords(
  stall: string,
  ports: FeedPort[],
  names: Names | null,
): string {
  if (stall === STALL_POWER) return t("ui-ship-scheme-stall-power");
  const port = ports.find((one) => one.port === stall);
  const goods = goodsName(names, port?.liquids[0] ?? stall);
  return port?.way === "in"
    ? t("ui-ship-scheme-stall-dry", { goods })
    : t("ui-ship-scheme-stall-full", { goods });
}
