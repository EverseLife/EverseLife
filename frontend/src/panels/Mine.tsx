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
 */


import { useState } from "react";
import type { Look, Sight } from "../api";
import { solve, type PowSettings } from "../pow";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useNames, useSession } from "../actions";
import { classOf } from "../classes";
import { t } from "../locale";
import { goodsName } from "../names";

type Props = {
  look: Look;
  pow: PowSettings | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Mine({ look, pow }: Omit<Props, "busy" | "act">) {
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
      {!scene && (
        <>
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

          {scene.state === "active" ? (
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
          ) : (
            <p className="trouble">
              {t(scene.state === "collapsed" ? "ui-mine-collapsed" : "ui-mine-closed")}
            </p>
          )}
        </>
      )}
    </section>
  );
}
