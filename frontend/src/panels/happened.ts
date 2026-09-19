// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The detail beside a line of the return digest (`Summary`).
 *
 * Out of the panel so that it can be pinned without drawing one: the line is
 * a message of the world (`event-*`), the detail is whatever the payload
 * names -- a thing, a law, a place, a hull -- and each of those is an id the
 * window names in the reader's language, or a name that is the same in
 * every language.
 */

import { goodsName, lawName, type Names } from "../names";
import { nodeWord } from "./map/words";

/** One line of what happened, as `world.summary` sends it. */
export type Happened = { at: string; kind: string; payload: Record<string, unknown> };

/** Where the vault's own word for a biome is, should the names not have it. */
type Book = { constants?: Record<string, unknown> | null } | null | undefined;

/** A payload value that says something: a non-empty string, or nothing. */
function said(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** The one detail worth showing beside the line, if the payload has one. */
export function detail(row: Happened, names: Names | null, book: Book): string | null {
  const p = row.payload ?? {};
  //: These four keys carry goods ids (D-251) and go through the names.
  for (const key of ["output", "goods", "resource", "type_key"]) {
    const value = p[key];
    if (typeof value === "string" && value) return goodsName(names, value);
  }
  //: A law is an id too, and its own table names it: the line used to read
  //: «город изменил закон · tax_trade» to the very person who changed it.
  if (typeof p.law === "string" && p.law) return lawName(names, p.law);
  //: A node by its name -- and a find, which has none (D-321), by the keys
  //: of its ground (`facet.told_of`), named here in the reader's language by
  //: the rule the map names it by: the face first, the biome failing it. The
  //: journal used to carry the vault's word for the biome, which is Russian
  //: only, and an English reader got a Russian word at the end of the line.
  const node = said(p.node);
  if (node) return node;
  const facet = said(p.facet);
  const biome = said(p.biome);
  if (facet || biome) {
    return nodeWord({ facet, features: biome ? [biome] : [] }, book?.constants?.["biome.names"], names);
  }
  //: A person and a hull are already words: both are named by whoever made
  //: them, and there is no table to look either up in. `other` is the other
  //: hull of a meeting (D-289), `name` the hull itself.
  for (const key of ["to", "other", "name"]) {
    const value = p[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}
