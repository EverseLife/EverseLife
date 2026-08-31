// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The Library: what this one holds, with search and pages (D-053, D-068, D-076).
 *
 * Anyone may take: free, unconditional, without citizenship -- the Library
 * refuses nobody. Its only restriction is geographic: **it does not work
 * remotely**, so this table is visible only to whoever stands in it.
 *
 * A library holds what was put into it (D-068, D-209). The capital's shelf is
 * the base set; one a city built starts empty and fills as people bring
 * carriers -- from the inventory, «Положить… → В библиотеку». What is given
 * stays for good, and the giver's name stays with the recipe.
 *
 * The knowledge store grows, and without order turns into a dump of a
 * thousand recipes with names like "nail 2 final" -- in the game the Sage
 * watches over that. Here the same task is solved with what the client has:
 * search by name, machine and inputs plus pages.
 */

import { useEffect, useState } from "react";
import type { Recipe } from "../api";
import * as api from "../api";
import type { Look } from "../api";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useNames, useSession } from "../actions";
import { goodsName } from "../names";
import { t } from "../locale";

/** How many catalog rows to show at a time. A display quantity, not a game one. */
const PAGE = 8;

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Library({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  //: The book is the one loaded at login (`useBook`), not a second fetch.
  const book = useBook();
  const names = useNames();
  const [crops, setCrops] = useState<{ id: string; name: string }[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    void api.plants().then((p) => setCrops(p.plants));
  }, []);

  //: The shelf, not the vault: only what this library holds is on the table.
  //: The vault catalog supplies the details -- station, inputs, level -- and
  //: the shelf supplies the list and the contributors' names. Everything is
  //: keyed by the recipe id (D-251); the words come from the names bundle.
  const shelf = look.node?.shelf ?? [];
  const byId: Record<string, Recipe> = Object.fromEntries(
    (book?.recipes ?? []).map((r) => [r.id ?? r.name, r]),
  );
  const all = shelf.map((entry) => ({
    ...(byId[entry.recipe] ?? { name: entry.recipe, inputs: [], level: "?", station: null }),
    id: entry.recipe,
    contributor: entry.contributor,
  }));
  const query = search.trim().toLowerCase();
  //: The player types Russian, so the match runs over the display words.
  const found = all.filter(
    (recipe) =>
      !query ||
      goodsName(names, recipe.id).toLowerCase().includes(query) ||
      (recipe.station ? goodsName(names, recipe.station) : "").toLowerCase().includes(query) ||
      (recipe.contributor ?? "").toLowerCase().includes(query) ||
      recipe.inputs.some((entry: string) =>
        goodsName(names, entry).toLowerCase().includes(query),
      ),
  );

  const pages = Math.max(1, Math.ceil(found.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = found.slice(current * PAGE, current * PAGE + PAGE);
  const carriers = look.inventory.filter((thing) => thing.recipe);

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t("ui-library-title")}
        <Rule>{t("ui-library-rule")}</Rule>
      </h2>
      <div className="row">
        <input
          type="search"
          value={search}
          placeholder={t("ui-library-search")}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
        {/* The counts travel as the digits chosen here: handed over raw, Fluent
            would group them by the locale's own rules. */}
        <span className="note">
          {t("ui-library-found", { found: String(found.length), all: String(all.length) })}
        </span>
      </div>

      {all.length === 0 && <p className="note">{t("ui-library-shelf-empty")}</p>}

      <table className="catalog">
        <thead>
          <tr>
            <th>{t("ui-library-recipe")}</th>
            <th>{t("ui-library-level")}</th>
            <th>{t("ui-library-station")}</th>
            <th>{t("ui-library-inputs")}</th>
            <th>{t("ui-library-contribution")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {shown.map((recipe) => (
            <tr key={recipe.id}>
              <td>{goodsName(names, recipe.id)}</td>
              <td className="num">{recipe.level}</td>
              <td className="note">{recipe.station ? goodsName(names, recipe.station) : "—"}</td>
              <td className="note">
                {recipe.inputs.map((one: string) => goodsName(names, one)).join(", ") || "—"}
              </td>
              <td className="note">{recipe.contributor ?? t("ui-library-founding")}</td>
              <td>
                {look.knows.includes(recipe.id) ? (
                  <span className="note">{t("ui-library-known")}</span>
                ) : (
                  <button
                    className="quiet"
                    onClick={() =>
                      act(() => session.send("library.copy", { recipe: recipe.id }))
                    }
                    disabled={busy}
                  >
                    {t("ui-library-take")}
                  </button>
                )}
              </td>
            </tr>
          ))}
          {shown.length === 0 && all.length > 0 && (
            <tr>
              <td colSpan={6} className="note">
                {t("ui-library-none-found")}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="row">
        <button
          className="quiet"
          onClick={() => setPage(current - 1)}
          disabled={current === 0}
        >
          ←
        </button>
        <span className="note">
          {t("ui-library-page", { page: String(current + 1), pages: String(pages) })}
        </span>
        <button
          className="quiet"
          onClick={() => setPage(current + 1)}
          disabled={current >= pages - 1}
        >
          →
        </button>
      </div>

      {carriers.length > 0 && (
        <>
          <h3>{t("ui-library-carriers")}</h3>
          {carriers.map((thing) => {
            const there = shelf.some((entry) => entry.recipe === thing.recipe);
            return (
              <div className="row" key={thing.id}>
                <span>
                  {goodsName(names, thing.goods)}: {goodsName(names, thing.recipe ?? "")}
                </span>
                {there ? (
                  <span className="note">{t("ui-library-already")}</span>
                ) : (
                  <button
                    className="quiet"
                    onClick={() =>
                      act(() => session.send("library.contribute", { item: thing.id }))
                    }
                    disabled={busy}
                    title={t("ui-library-give-hint")}
                  >
                    {t("ui-library-give")}
                  </button>
                )}
              </div>
            );
          })}
        </>
      )}

      <h3>{t("ui-library-agrotech")}</h3>
      <div className="row">
        {crops.map((crop) => {
          const learned = (look.agrotech ?? []).includes(crop.id);
          return (
            <button
              key={crop.id}
              className="quiet"
              onClick={() =>
                act(() => session.send("breed.agrotech", { culture: crop.id }))
              }
              disabled={busy || learned}
              title={
                learned ? t("ui-library-agrotech-known") : t("ui-library-agrotech-hint")
              }
            >
              {crop.name}
              {learned ? " ✓" : ""}
            </button>
          );
        })}
      </div>
      <p className="note">{t("ui-library-agrotech-note")}</p>
    </section>
  );
}
