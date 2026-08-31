// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The word on waking (D-182).
 *
 * There are no NPCs, no quests, and nobody else to explain to the printed who
 * they are and why the world is empty. This is the only place where the game
 * speaks about itself -- and therefore it is strictly bounded: **not a single
 * lore secret**. Here only what the printed already knows about themselves.
 * What happened to the Forerunners, whose sample the machine prints and what
 * lies under Aurora's ice is not our business: lore is given out in fragments
 * and is mined (10-world/01).
 *
 * The second half is the first three steps. Not a quest and not a checked
 * chain but an answer to "what to do" -- the very path by which a newcomer
 * pays for themselves in a couple of hours (60-meta/00).
 *
 * The window closes with a button and opens again from the header: what was
 * read once must not become unavailable, and what was not read -- mandatory.
 */


import { t } from "../locale";

type Props = {
  onClose: () => void;
};

export function Intro({ onClose }: Props) {
  return (
    <div className="veil" role="dialog" aria-modal="true" aria-label={t("ui-intro-title")}>
      <section className="intro">
        <h2>{t("ui-intro-title")}</h2>

        <p>{t("ui-intro-forerunners")}</p>
        <p>{t("ui-intro-machine")}</p>
        <p>{t("ui-intro-legacy")}</p>

        <h3>{t("ui-intro-start")}</h3>
        <ol>
          <li>{t("ui-intro-step-look")}</li>
          <li>{t("ui-intro-step-recipe")}</li>
          <li>{t("ui-intro-step-sell")}</li>
        </ol>

        <div className="row">
          <button onClick={onClose}>{t("ui-intro-go")}</button>
          <span className="note">{t("ui-intro-again")}</span>
        </div>
      </section>
    </div>
  );
}
