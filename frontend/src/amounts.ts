/** Helpers for the quantity field (`Amount.tsx`). Kept apart from the component
 *  so hot reload keeps working: a module of components exports components. */

/** How much to move: what was typed, or the whole stack if nothing was. */
export function chosen(value: number | null, whole: number): number {
  return value === null ? whole : Math.min(value, whole);
}
