// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The face: "Roof" (D-143).
 *
 * Three buttons and a pace lever. Roof stability is never shown -- it is not
 * in the reply at all, and the sign lies by `mine.sign_noise` and **does not
 * change until you swing**: otherwise the average of readings would yield the hidden number.
 *
 * The session opens only after the device fee, and the browser computes it.
 *
 * How the bad ending gets said here at all is in `../mining`: `look` carries
 * the open face and nothing else, so a collapse arrives as an event.
 */


import { useEffect, useState } from "react";
import type { Look, Sight } from "../api";
import { solve, type PowSettings } from "../pow";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useNames, useSession } from "../actions";
import { classOf } from "../classes";
import { caveIn, rubbleOut, type CaveIn, type Rubble } from "../mining";
import { t } from "../locale";
import { goodsName } from "../names";

type Props = {
  look: Look;
  values: Record<string, any> | null;
  pow: PowSettings | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Mine({ look, values, pow }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [computing, setComputing] = useState(false);
  const scene = look.mining as Sight | null | undefined;
  const vein = look.veins?.[0];
  //: The rock kills by a count, not by a coin (D-294), and a body that has
  //: spent its grace looks no different from a fresh one. Shown only when the
  //: next cave-in is the fatal one -- so the sentence names no number and
  //: stays true whatever the vault sets the count to. A server that has not
  //: sent the constants yet says nothing rather than guessing.
  const spared = values?.["mine.collapses_survived"];
  const doomed =
    typeof spared === "number" && (look.body?.cave_ins ?? 0) >= spared;

  //: The roof came down, and `look` will never say so: it carries the open
  //: session and a collapsed one is closed. The window hears it instead --
  //: see `../mining` for why the event and not the reply to the swing.
  //:
  //: State of this component, and that is enough: a working collapses only
  //: under one's own swing (`engine/mining/face`), so the panel is open and
  //: mounted when the news arrives. Walking away or reloading loses it, and
  //: that is the right price -- the return summary tells it again.
  const [buried, setBuried] = useState<CaveIn | null>(null);
  useEffect(
    () =>
      session.on("mining.collapsed", (happening) => {
        const mine = caveIn(happening);
        if (mine) setBuried(mine);
      }),
    [session],
  );
  //: A face opened again is the collapse read and done with: the notice
  //: belongs to the walk back in, not to the next shift.
  const digging = Boolean(scene);
  useEffect(() => {
    if (digging) setBuried(null);
  }, [digging]);
  //: A new face is a new working: whatever the last one said about its
  //: rubble belongs to it and not to this one.
  const face = scene?.session;
  useEffect(() => {
    setRubble(null);
  }, [face]);

  //: A caved-in working is dug out before it is worked (D-301), and the swing
  //: that does it brings nothing back. Nothing in `look` says so -- the roof
  //: is the one number the player never sees, and the sign speaks of the roof
  //: rather than of the rubble over it -- so the shovelling is told, the way
  //: the collapse is. `cleared` on the last one, and then the working is a
  //: working again.
  const [rubble, setRubble] = useState<Rubble | null>(null);
  useEffect(
    () =>
      session.on("mining.rubble", (happening) => {
        const dug = rubbleOut(happening);
        if (dug) setRubble(dug);
      }),
    [session],
  );
  //: And it goes when the swinging turns back into mining: a swing that
  //: brought ore, or a roof that came down, says the shovelling is over
  //: better than any timer. Without this the last line of a clearing --
  //: "the rubble is cleared" -- would hang over every shift after it, on
  //: this face and on the next, because the panel never unmounts.
  useEffect(
    () => session.on("mining.swing", () => setRubble(null)),
    [session],
  );
  useEffect(
    () => session.on("mining.collapsed", () => setRubble(null)),
    [session],
  );

  const start = () =>
    act(async () => {
      //: The button is dead without a vein, so this is a guard rather than a
      //: path -- but `act` shows what it catches, so what it throws is copy.
      if (!vein || !pow) throw new Error(t("ui-mine-no-vein-here"));
      setComputing(true);
      try {
        const challenge = await session.send("pow.challenge");
        const answer = await solve(session.account, String(challenge.nonce), pow);
        await session.send("mine.start", {
          challenge: challenge.challenge,
          answer: answer,
          vein: vein.id,
          //: The tool is found by its class (D-215), never by a substring of a name.
          tool: look.inventory.find((thing) => classOf(book, thing.goods) === "pickaxe")?.id,
        });
      } finally {
        setComputing(false);
      }
    });

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t("ui-mine-title")}
        <Rule>{t("ui-mine-rule")}</Rule>
      </h2>
      {doomed && <p className="trouble">{t("ui-mine-last-cave-in")}</p>}
      {scene && rubble && (
        <p className="note" role="status">
          {t(rubble.cleared ? "ui-mine-rubble-out" : "ui-mine-rubble")}
        </p>
      )}
      {!scene && (
        <>
          {buried && (
            /* One alert, both lines: the sentence and the number it is about
               are announced together, or the screen reader hears half of it. */
            <div role="alert">
              <p className="trouble">
                {t("ui-mine-collapsed")}{" "}
                <button
                  className="link"
                  onClick={() => setBuried(null)}
                  aria-label={t("ui-refusal-dismiss")}
                >
                  ×
                </button>
              </p>
              {/* A roof down on the first swing buried nothing: the sentence
                  above says it whole, and "0.000" under it says less. */}
              {buried.lost > 0 && (
                <p className="note">
                  {t("ui-mine-collapsed-lost", { lost: buried.lost.toFixed(3) })}
                </p>
              )}
            </div>
          )}
          <p className="note">
            {vein
              ? t("ui-mine-vein", {
                  goods: goodsName(names, vein.resource),
                  richness: vein.richness.toFixed(0),
                })
              : t("ui-mine-no-vein")}
          </p>
          <button onClick={start} disabled={busy || computing || !vein}>
            {t(computing ? "ui-mine-computing" : "ui-mine-start")}
          </button>
          <p className="note">
            {/* A setting the server has not sent yet leaves a hole, exactly as
                the interpolation did before: `String(undefined)` would put the
                word "undefined" in front of the player. */}
            {t("ui-mine-pow", {
              memory: pow?.memoryMib?.toString() ?? "",
              rounds: pow?.iterations?.toString() ?? "",
            })}
          </p>
        </>
      )}

      {scene && (
        <>
          <p className="sign">{scene.sign}</p>
          <table>
            <tbody>
              <tr>
                <td>{t("ui-mine-mined")}</td>
                <td className="num">{scene.mined.toFixed(3)}</td>
              </tr>
              <tr>
                <td>{t("ui-mine-swings")}</td>
                <td className="num">{scene.swings}</td>
              </tr>
              <tr>
                <td>{t("ui-mine-timbers")}</td>
                <td className="num">{scene.timbers}</td>
              </tr>
            </tbody>
          </table>

          {/* `look` sends the open face and only that, so there is no closed
              one to draw here: the end of a session is the notice above. */}
          <div className="row">
            <button onClick={() => act(() => session.send("mine.swing"))} disabled={busy}>
              {t("ui-mine-swing")}
            </button>
            <button onClick={() => act(() => session.send("mine.timber"))} disabled={busy}>
              {t("ui-mine-timber")}
            </button>
            <button onClick={() => act(() => session.send("mine.leave"))} disabled={busy}>
              {t("ui-mine-leave")}
            </button>
            <button
              className="quiet"
              onClick={() =>
                act(() =>
                  session.send("mine.pace", {
                    pace: scene.pace === "fast" ? "steady" : "fast",
                  }),
                )
              }
              disabled={busy}
            >
              {t("ui-mine-pace", { fast: String(scene.pace === "fast") })}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
