// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The device fee (D-110, D-112).
 *
 * One Argon2id estimate per mining session. The work is fixed: not "as much
 * as you managed" but exactly one pass. Power gives access but not advantage
 * -- computed faster, started earlier; yield is determined by decisions at the face.
 *
 * The numbers come from `/public/constants`, not from here: the server checks
 * the answer with the same estimate, and they may not diverge (D-065).
 */

import { argon2id } from "hash-wasm";

const KIB_PER_MIB = 1024;
/** The answer length in bytes -- a protocol quantity, not a balance one. */
const HASH_BYTES = 32;
const PARALLELISM = 1;

function bytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/** The account UUID -- the same 16 bytes the server puts into the estimate's secret. */
function accountBytes(uuid: string): Uint8Array {
  return bytes(uuid.replace(/-/g, ""));
}

export type PowSettings = { iterations: number; memoryMib: number };

export function powSettings(values: Record<string, any>): PowSettings {
  return {
    iterations: Number(values["pow.argon_iterations"]),
    memoryMib: Number(values["pow.memory_per_session"]),
  };
}

/**
 * Compute the answer. It takes noticeable time and heats the device -- as
 * intended: this is a tax on scale, not on the player.
 */

export async function solve(
  account: string,
  nonceHex: string,
  settings: PowSettings,
): Promise<string> {
  return argon2id({
    password: accountBytes(account),
    salt: bytes(nonceHex),
    parallelism: PARALLELISM,
    iterations: settings.iterations,
    memorySize: settings.memoryMib * KIB_PER_MIB,
    hashLength: HASH_BYTES,
    outputType: "hex",
  });
}
