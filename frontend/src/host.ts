// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where the server is.
 *
 * Four lines with a module to themselves, and the reason is the import graph
 * rather than their weight. This is the one place in the client that reads
 * `window.location` while it is being evaluated, so it is the one place that
 * cannot be loaded without a DOM -- `locale.ts` says as much where it defers
 * its own import. Both halves of the wire need the address: `api.ts` for the
 * public reads, `session.ts` for the socket. Were it to live in either of
 * them the other would have to import it back, and the two would close a
 * cycle over a string.
 */

//: The default server address is the same host the page was opened from.
//: Otherwise someone coming over the local network would look for the server
//: on their own phone: everyone has their own `localhost`. An explicit
//: `VITE_API` overrides this when the server is not nearby.
//:
//: In development the server lives on a separate port, in production behind
//: the same origin as the client, at the `/api` path: so the built image does
//: not know the production domain and suits anyone, and the socket gets
//: `wss://` without separate configuration.
export const HTTP =
  import.meta.env.VITE_API ??
  (import.meta.env.PROD
    ? `${window.location.origin}/api`
    : `${window.location.protocol}//${window.location.hostname}:8000`);
export const WS = HTTP.replace(/^http/, "ws") + "/session/ws";
