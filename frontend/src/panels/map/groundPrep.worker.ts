// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The worker that makes the rasters ready for the GPU (`groundPrep.ts`),
 * off the page's thread. One job in, one answer out: the levels come back
 * handed over, not copied, and a job that throws answers with its reason.
 */

import type { RasterPassport } from "../../api";
import { buffersOf, prepare, type GroundRasters, type Prepared } from "./groundPrep";

export type Job = { id: number; passport: RasterPassport; rasters: GroundRasters };
export type Answer = { id: number; prepared: Prepared } | { id: number; failed: string };

//: The worker's own scope, named by what is used of it: the project's
//: types are the page's (`DOM`), and the worker's library beside them would
//: declare the globals twice.
type Scope = {
  onmessage: ((event: MessageEvent<Job>) => void) | null;
  postMessage(message: Answer, transfer: Transferable[]): void;
};
const scope = self as unknown as Scope;

scope.onmessage = ({ data: { id, passport, rasters } }) => {
  try {
    const prepared = prepare(passport, rasters);
    scope.postMessage({ id, prepared }, buffersOf(prepared));
  } catch (why) {
    scope.postMessage({ id, failed: String(why) }, []);
  }
};
