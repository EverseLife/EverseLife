// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The location and everything on it -- one panel per file under `place/`
 * (review 2026-08-23, wave 3); this file only re-exports them.
 */

export type { Props } from "./place/shared";
export { disposes } from "./place/shared";
export { Ground } from "./place/Ground";
export { Floor } from "./place/Floor";
export { Storages } from "./place/Storages";
export { Plot } from "./place/Plot";
export { Door } from "./place/Door";
export { House } from "./place/House";
export { Repair } from "./place/Repair";
export { Demolition } from "./place/Demolition";
export { PLACES } from "./place/shared";
export { gatherSigns } from "./place/shared";
export { Gather } from "./place/Gather";
export { Foundation } from "./place/Foundation";
export { Convoy } from "./place/Convoy";
export { placeable } from "./place/shared";
export { Equipment } from "./place/Equipment";
