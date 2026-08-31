// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Belonging to a city, on its own: joining, the term one is held by, leaving.
 *
 * The rest of the window asks "what may I do in this city" and answers out of
 * the rights the visitor holds. This asks "am I of it at all", and answers the
 * same for the ruler and for a stranger who walked in off the road -- it reads
 * `look`, not the survey, so it draws even where there is no city view to
 * draw. Hence its own waiting and its own refusal: an application in flight
 * has no business greying out the court and the votes.
 */

import type { Look } from "../../api";
import { when } from "../../clock";
import { t } from "../../locale";
import { Refusal, useActions, useSession } from "../../actions";

/** Citizenship: one per person, entry by charter, exit with a delay (D-160).
 *
 * One joins in the administration -- where the city makes every decision
 * (D-155) -- so the section stands in this window, first: for a visitor the
 * question "may I belong here" comes before the machinery of the power. The
 * admission order is always shown: "open", "by application" and "by
 * invitation" behave differently, and the person must understand what to expect.
 */
export function Citizenship({ look }: { look: Look }) {
  const session = useSession();
  //: Own waiting and own refusal: joining must not grey out the court and the votes.
  const acting = useActions();
  const { busy, act } = acting;
  const city = look.city ?? null;
  const own = look.citizenship ?? null;
  //: Only in the administration: both joining and leaving are in-person (D-155).
  //: The window itself is opened by the hall on the bench; here only the city is needed.
  if (!city) return null;

  //: The admission order, each by the message that says what to expect.
  const order_: Record<string, string> = {
    open: "ui-admin-admission-open",
    application: "ui-admin-admission-application",
    invite: "ui-admin-admission-invite",
  };
  const admission = t(order_[city.admission] ?? city.admission);
  //: Citizenship taken as a print condition cannot be given up before the term (D-184).
  const linked = Boolean(
    own?.bound_until && new Date(own.bound_until) > new Date(),
  );

  return (
    <section>
      <Refusal of={acting} />
      <h2>{t("ui-admin-citizenship")}</h2>
      {own ? (
        <p>
          {t("ui-admin-citizenship-in")} <b>{own.city}</b>
          {own.leaving_at && (
            <> · {t("ui-admin-citizenship-leaving", { when: when(own.leaving_at) })}</>
          )}
          {/* Обязательство, принятое при печати (D-184): срок виден заранее,
              а не открывается отказом при попытке выйти. */}
          {linked && (
            <> · {t("ui-admin-citizenship-bound", { when: when(own.bound_until) })}</>
          )}
        </p>
      ) : (
        <p className="note">{t("ui-admin-citizenship-none")}</p>
      )}

      <div className="row">
        {city.citizen ? (
          <span className="note">{t("ui-admin-your-city")}</span>
        ) : city.requested ? (
          <span className="note">
            {city.admission === "invite" ? t("ui-admin-invited") : t("ui-admin-applied")}
          </span>
        ) : null}
        {!city.citizen && (
          <button
            onClick={() => act(() => session.send("city.join", {}))}
            disabled={busy || Boolean(own)}
            title={own ? t("ui-admin-join-blocked") : admission}
          >
            {city.requested && city.admission === "invite"
              ? t("ui-admin-accept-invite")
              : t("ui-admin-join")}
          </button>
        )}
        <span className="note">
          {t("ui-admin-admission-line", { city: city.name, order: admission })}
        </span>
      </div>

      {own && !own.leaving_at && (
        <div className="row">
          <button
            onClick={() => act(() => session.send("city.leave", {}))}
            disabled={busy || linked}
            title={linked ? t("ui-admin-leave-bound-title") : t("ui-admin-leave-title")}
          >
            {t("ui-admin-leave")}
          </button>
          <span className="note">
            {linked ? t("ui-admin-leave-bound-note") : t("ui-admin-leave-note")}
          </span>
        </div>
      )}
    </section>
  );
}
