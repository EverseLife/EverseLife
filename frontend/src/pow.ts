/**
 * Плата устройства (D-110, D-112).
 *
 * Одна оценка Argon2id на сессию добычи. Работа фиксированная: не «сколько
 * успел», а ровно один проход. Мощность даёт доступ, но не преимущество —
 * быстрее посчитал, раньше начал; выход определяют решения в забое.
 *
 * Числа берутся из `/public/constants`, а не отсюда: сервер проверяет ответ
 * той же оценкой, и разойтись им нельзя (D-065).
 */

import { argon2id } from "hash-wasm";

const KIB_PER_MIB = 1024;
/** Длина ответа в байтах — величина протокола, а не баланса. */
const HASH_BYTES = 32;
const PARALLELISM = 1;

function bytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/** UUID аккаунта — те же 16 байт, что сервер кладёт в секрет оценки. */
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
 * Посчитать ответ. Считается заметное время и греет устройство — так и задумано:
 * это налог на масштаб, а не на игрока.
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
