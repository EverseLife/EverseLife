// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Suing and sentencing: the part of the administration that judges people
 * rather than governs a city.
 *
 * It stands apart from the window for the same reason a courthouse does. The
 * claim is a sentence nobody parses, the sanctions come as a list from the
 * vault, and the whole exchange runs on `city.sue` and `city.judge` without
 * touching a law, a lot or the treasury.
 *
 * The picker and the reading back of a passed sentence stay together here:
 * they are one list read in two directions, and prising them apart is exactly
 * how a decided case ends up printing the engine's key in a row where the line
 * below offers the word.
 */

import { useState } from "react";
import type { CourtCase, SanctionKind } from "../../api";
import { t } from "../../locale";
import { useSession } from "../../actions";

/** The city court: cases and verdicts (D-095, D-117, D-166).
 *
 * The case card shows the plaintiff, the defendant and the substance in
 * words: examining it is the judge's work, not the engine's. Sanctions are
 * listed from the vault, and unenforceable ones are marked honestly -- a
 * verdict without enforcement is worse than refusing a verdict.
 */

/**
 * A sanction in the player's words. The names come with the list the picker is
 * built from, so a passed sentence is read the same way it was chosen: without
 * this the decided case printed the engine's key -- `приговор: fine` -- in a
 * row where the very next line offered «Штраф».
 */
function named(sanctions: SanctionKind[], kind: string | null | undefined): string {
  return sanctions.find((one) => one.id === kind)?.name ?? kind ?? "—";
}

export function Court({
  jobs,
  sanctions,
  penalColonies,
  can,
  go,
  busy,
}: {
  jobs: CourtCase[];
  sanctions: SanctionKind[];
  penalColonies: { key: string; name: string }[];
  can: boolean;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const session = useSession();
  const [toWhom, setToWhom] = useState("");
  const [essence, setEssence] = useState("");
  const [sanction, setSanction] = useState("fine");
  const [qty, setQty] = useState(10);
  const [penalColony, setPenalColony] = useState("");
  const open = jobs.filter((job) => job.state === "open");
  if (jobs.length === 0 && !can) return null;

  return (
    <>
      <h3>{t("ui-admin-court")}</h3>
      {jobs.length > 0 && (
        <table>
          <tbody>
            {jobs.slice(0, 8).map((job) => (
              <tr key={job.id}>
                <td>
                  {job.plaintiff} → {job.defendant}
                  <span className="note"> · {job.claim}</span>
                </td>
                <td className="note">
                  {job.state === "open"
                    ? t("ui-admin-case-open")
                    : job.state === "judged"
                      ? t("ui-admin-case-judged", { sanction: named(sanctions, job.verdict) })
                      : /* A dismissal carries the judge's own words when there
                           are any, and the engine's own "отказано" when there
                           are none -- and repeating that after the colon read
                           "отказано: отказано". The comparison is against what
                           the engine writes into the row, so it stays a literal:
                           it is a wire value, not a line to read. */
                        job.verdict && job.verdict !== "отказано"
                        ? t("ui-admin-case-dismissed-why", { why: job.verdict })
                        : t("ui-admin-case-dismissed")}
                </td>
                <td>
                  {can && job.state === "open" && (
                    <>
                      <select
                        value={sanction}
                        onChange={(e) => setSanction(e.target.value)}
                      >
                        {sanctions.map((kind) => (
                          <option key={kind.id} value={kind.id} disabled={!kind.enforced}>
                            {kind.name}
                            {kind.enforced ? "" : ` ${t("ui-admin-sanction-unenforced")}`}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        min={0}
                        value={qty}
                        onChange={(e) => setQty(Number(e.target.value))}
                        title={t("ui-admin-fine-title")}
                      />
                      {/* Куда сажать — решает суд (D-176): каторга одна —
                          очевидно, несколько — судья называет которую. */}
                      {sanction === "prison" && penalColonies.length > 1 && (
                        <select
                          value={penalColony}
                          onChange={(e) => setPenalColony(e.target.value)}
                          title={t("ui-admin-prison-title")}
                        >
                          <option value="">{t("ui-admin-prison-pick")}</option>
                          {penalColonies.map((node) => (
                            <option key={node.key} value={node.key}>
                              {node.name}
                            </option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={() =>
                          go(() =>
                            session.send("city.judge", {
                              case: job.id,
                              sanction: sanction,
                              amount: qty,
                              days: qty,
                              ...(sanction === "prison" && penalColony
                                ? { prison: penalColony }
                                : {}),
                            }),
                          )
                        }
                        disabled={busy || (sanction === "prison" && penalColonies.length > 1 && !penalColony)}
                      >
                        {t("ui-admin-verdict")}
                      </button>
                      <button
                        className="quiet"
                        onClick={() =>
                          go(() => session.send("city.judge", { case: job.id }))
                        }
                        disabled={busy}
                      >
                        {t("ui-admin-dismiss")}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <input
          value={toWhom}
          onChange={(e) => setToWhom(e.target.value)}
          placeholder={t("ui-admin-sue-whom")}
        />
        <input
          value={essence}
          onChange={(e) => setEssence(e.target.value)}
          placeholder={t("ui-admin-sue-claim")}
        />
        <button
          onClick={() =>
            go(() => session.send("city.sue", { who: toWhom, claim: essence }))
          }
          disabled={busy || !toWhom.trim() || !essence.trim()}
        >
          {t("ui-admin-sue")}
        </button>
        <span className="note">{t("ui-admin-sue-note")}</span>
      </div>
      {open.length > 0 && !can && (
        <p className="note">{t("ui-admin-court-queue", { count: String(open.length) })}</p>
      )}
    </>
  );
}
