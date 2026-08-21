# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Load test: how many simultaneous players this server holds.

Off-the-shelf tools (k6, wrk, ab) measure the wrong surface here. The player
does not live on HTTP: `/public/*` is a read-only catalog, and the whole game
goes through one WebSocket where one command is answered by exactly one reply
(`src/api/session.py`). So the client is written here, and it plays what the
browser client plays.

## What the load is actually made of

The client polls `look` every 5 seconds, and every 2 while something is
running (`frontend/src/App.tsx`). One `look` is the player's whole picture --
profile, money, knowledge, orders, reservations, batches, body, node,
inventory, veins, ships -- a few dozen queries in a single transaction. A
connected player who does nothing at all therefore costs 0.2-0.5 look/s, and
it is that, not actions, the server spends itself on. Hence the default
profile: connect, and poll.

The second cost is the device fee (D-110): one Argon2id pass of
`pow.memory_per_session` MiB per mining session, which the server verifies by
computing the same thing. `--pow-per-minute` exercises exactly that path.
A `mine.start` refused for want of a pickaxe has **already** cost the server
the whole verification -- that refusal is the measurement, not a failure.

## What it does not do

It does not go to production. `prepare` registers real accounts, and the world
is eternal with no wipes (D-007): load-test characters printed at a door stay
in it forever. The target is a copy of the production compose; against a
non-local host `prepare` refuses unless `--allow-remote` is passed.

## How to run it

    # a) accounts for the test -- once per target world
    .venv/Scripts/python.exe tools/loadtest.py prepare --players 300

    # b) the staircase itself
    .venv/Scripts/python.exe tools/loadtest.py run --steps 25,50,100,200,300

The generator competes for the same processor as everything else, so for real
numbers run it from another machine (this file plus the `backend/` venv), and
keep `--pow-workers` low: every concurrent solve costs the generator the same
memory the server pays.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

import websockets

#: Run as a plain script from anywhere -- the estimate is taken from the engine
#: itself (`src/engine/pow.py`), and duplicating that computation here would
#: mean the load test could measure a fee the server does not charge.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.constants import Constants  # noqa: E402
from src.engine.pow import solve  # noqa: E402

#: The idle client's poll (`frontend/src/App.tsx`). While something is running
#: it polls every 2 seconds -- pass `--look-period 2` for a world where
#: everyone is busy at once.
LOOK_PERIOD = 5.0

#: Long enough to pass `check_password`, and no secret: these accounts exist
#: only in a test world.
PASSWORD = "loadtest-password"

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "host.docker.internal")


def _fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as answer:
        return json.loads(answer.read())


def _quantile(values: list[float], share: float) -> float:
    """Nearest-rank quantile. `statistics.quantiles` interpolates and needs
    more than one point; a step with two samples still has to report."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(share * len(ordered) + 0.5))))
    return ordered[rank - 1]


class Tally:
    """Everything one step of the staircase measured."""

    def __init__(self) -> None:
        self.timings: dict[str, list[float]] = {}
        self.refusals: Counter[str] = Counter()
        self.failures: Counter[str] = Counter()
        self.opened = time.perf_counter()

    def add(self, command: str, seconds: float, refusal: str | None) -> None:
        self.timings.setdefault(command, []).append(seconds)
        if refusal is not None:
            self.refusals[f"{command}: {refusal}"] += 1

    def fail(self, reason: str) -> None:
        self.failures[reason] += 1

    @property
    def calls(self) -> int:
        return sum(len(values) for values in self.timings.values())

    def lines(self) -> list[str]:
        elapsed = max(time.perf_counter() - self.opened, 1e-9)
        rows = []
        for command, values in sorted(self.timings.items()):
            rows.append(
                f"    {command:<16} n={len(values):<6} "
                f"{len(values) / elapsed:6.1f}/s  "
                f"p50 {_quantile(values, 0.50) * 1000:7.1f}ms  "
                f"p95 {_quantile(values, 0.95) * 1000:8.1f}ms  "
                f"p99 {_quantile(values, 0.99) * 1000:8.1f}ms  "
                f"max {max(values) * 1000:8.1f}ms"
            )
        for reason, count in self.refusals.most_common(5):
            rows.append(f"    refused  {count:<5} {reason}")
        for reason, count in self.failures.most_common(5):
            rows.append(f"    FAILED   {count:<5} {reason}")
        return rows


class Box:
    """The tally the running players write into. A step swaps it, and every
    player picks the new one up on its next call."""

    def __init__(self) -> None:
        self.tally = Tally()


async def _call(socket, box: Box, message: dict) -> dict:
    started = time.perf_counter()
    await socket.send(json.dumps(message))
    reply = json.loads(await socket.recv())
    box.tally.add(message["cmd"], time.perf_counter() - started, reply.get("refused"))
    return reply


async def _pay_device_fee(
    socket, box: Box, account: dict, constants: Constants, gate: asyncio.Semaphore
) -> None:
    """The heaviest path there is: challenge, Argon2id, verification.

    A refusal after the verification is expected -- a printed body has no
    pickaxe (D-215) and mining will not start. The server has paid for the
    estimate by then, and that is what is being measured.
    """
    issued = await _call(socket, box, {"cmd": "pow.challenge"})
    if "refused" in issued:
        return
    async with gate:
        answer = await asyncio.to_thread(
            solve, constants, uuid.UUID(account["account"]), bytes.fromhex(issued["nonce"])
        )
    await _call(
        socket,
        box,
        {
            "cmd": "mine.start",
            "challenge": issued["challenge"],
            "answer": answer.hex(),
            #: Whatever vein the last `look` saw. Without one the command is
            #: refused after the verification -- the cost is already paid.
            "vein": account.get("vein") or str(uuid.UUID(int=0)),
        },
    )


async def _player(
    target: str,
    account: dict,
    box: Box,
    stop: asyncio.Event,
    constants: Constants,
    look_period: float,
    pow_per_minute: float,
    gate: asyncio.Semaphore,
    origin: str | None,
) -> None:
    """One connected player: hello by token, then the client's own polling."""
    try:
        async with websockets.connect(
            target, open_timeout=60, ping_interval=20, ping_timeout=60, origin=origin
        ) as socket:
            greeting = await _call(socket, box, {"cmd": "hello", "token": account["token"]})
            if "refused" in greeting:
                box.tally.fail(f"hello: {greeting['refused']}")
                return
            next_fee = time.perf_counter() + (
                random.uniform(0, 60.0 / pow_per_minute) if pow_per_minute > 0 else 0.0
            )
            while not stop.is_set():
                #: Jitter, otherwise every player polls in the same instant and
                #: the measured server is one nobody has.
                await asyncio.sleep(look_period * random.uniform(0.75, 1.25))
                seen = await _call(socket, box, {"cmd": "look"})
                veins = seen.get("look", {}).get("veins") or []
                if veins:
                    account["vein"] = veins[0]["id"]
                if pow_per_minute > 0 and time.perf_counter() >= next_fee:
                    next_fee = time.perf_counter() + 60.0 / pow_per_minute
                    await _pay_device_fee(socket, box, account, constants, gate)
    except asyncio.CancelledError:
        raise
    except Exception as trouble:  # noqa: BLE001 -- a dead player is a data point, not a crash
        box.tally.fail(f"{type(trouble).__name__}: {trouble}"[:90])


async def prepare(args: argparse.Namespace) -> int:
    """Register the accounts the run will play. This writes to the world."""
    host = args.http.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host not in LOCAL_HOSTS and not args.allow_remote:
        print(
            f"{host} is not local, and `prepare` prints bodies into a world that is never "
            "wiped (D-007). Point it at a copy, or pass --allow-remote if this is one."
        )
        return 2

    doors = _fetch(f"{args.http}/public/doors")["doors"]
    if not doors:
        print("no doors in this world: nowhere to print")
        return 2
    #: The Forerunners' printer by default: it belongs to nobody, so three
    #: hundred test characters do not drain a city treasury of its grants (D-182).
    door = args.door or next((d["node"] for d in doors if d["precursor"]), doors[0]["node"])
    stamp = uuid.uuid4().hex[:6]
    accounts: list[dict] = []
    gate = asyncio.Semaphore(args.concurrency)

    async def one(number: int) -> None:
        email = f"load-{stamp}-{number}@loadtest.example"
        async with gate, websockets.connect(args.ws, open_timeout=60, origin=args.origin) as socket:
            await socket.send(
                json.dumps(
                    {
                        "cmd": "join",
                        "email": email,
                        "password": PASSWORD,
                        "name": f"Нагрузка {stamp} {number}",
                        "line": "human",
                        "node": door,
                    }
                )
            )
            reply = json.loads(await socket.recv())
            if "refused" in reply:
                print(f"  #{number} refused: {reply['refused']}")
                return
            accounts.append(
                {
                    "email": email,
                    "password": PASSWORD,
                    "token": reply["token"],
                    "account": reply["account"],
                    "name": reply["hello"],
                }
            )

    started = time.perf_counter()
    await asyncio.gather(*(one(number) for number in range(args.players)))
    Path(args.accounts).write_text(json.dumps(accounts, ensure_ascii=False, indent=1), "utf-8")
    print(
        f"registered {len(accounts)}/{args.players} at door {door!r} "
        f"in {time.perf_counter() - started:.1f}s -> {args.accounts}"
    )
    return 0


async def run(args: argparse.Namespace) -> int:
    accounts = json.loads(Path(args.accounts).read_text("utf-8"))
    constants = Constants(_fetch(f"{args.http}/public/constants")["values"], source=args.http)
    steps = [int(step) for step in args.steps.split(",")]
    if max(steps) > len(accounts):
        print(f"{max(steps)} players asked for, {len(accounts)} accounts prepared")
        return 2

    box, stop = Box(), asyncio.Event()
    gate = asyncio.Semaphore(args.pow_workers)
    running: list[asyncio.Task] = []
    verdict = 0
    print(
        f"target {args.ws}\n"
        f"profile: look every {args.look_period}s"
        + (f", device fee {args.pow_per_minute}/min per player" if args.pow_per_minute else "")
    )
    try:
        for step in steps:
            while len(running) < step:
                running.append(
                    asyncio.create_task(
                        _player(
                            args.ws,
                            accounts[len(running)],
                            box,
                            stop,
                            constants,
                            args.look_period,
                            args.pow_per_minute,
                            gate,
                            args.origin,
                        )
                    )
                )
            #: Connecting and the first `look` are a burst nobody experiences in
            #: life; the step is measured only after it has settled.
            await asyncio.sleep(args.warmup)
            box.tally = Tally()
            await asyncio.sleep(args.hold)
            tally, box.tally = box.tally, Tally()

            alive = sum(1 for task in running if not task.done())
            print(f"\n{step} players ({alive} alive, {tally.calls} calls)")
            for line in tally.lines():
                print(line)

            looks = tally.timings.get("look", [])
            p95 = _quantile(looks, 0.95) * 1000
            broken = sum(tally.failures.values())
            if p95 > args.budget_ms or alive < step * 0.98 or broken:
                print(
                    f"\n  ceiling: at {step} players look p95 = {p95:.0f}ms "
                    f"(budget {args.budget_ms:.0f}ms), {alive}/{step} alive, {broken} dropped."
                )
                verdict = 1
                break
        else:
            print(f"\n  no ceiling found: {max(steps)} players held within the budget.")
    finally:
        stop.set()
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
    return verdict


def main() -> int:
    #: Refusals arrive from the server in Russian, and the Windows console
    #: codepage turns them into noise -- the report is unreadable exactly where
    #: it says why the load broke.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--http", default="http://localhost:8000", help="the server's HTTP root")
    parser.add_argument("--ws", default=None, help="session socket, derived from --http by default")
    parser.add_argument("--accounts", default="loadtest-accounts.json")
    parser.add_argument("--origin", default=None, help="Origin header, if the target checks it")
    commands = parser.add_subparsers(dest="command", required=True)

    born = commands.add_parser("prepare", help="register the accounts (writes to the world)")
    born.add_argument("--players", type=int, default=100)
    born.add_argument("--door", default=None, help="node key; the Forerunners' printer by default")
    born.add_argument("--concurrency", type=int, default=8)
    born.add_argument("--allow-remote", action="store_true")

    play = commands.add_parser("run", help="the staircase")
    play.add_argument("--steps", default="25,50,100,200")
    play.add_argument(
        "--warmup", type=float, default=15.0, help="seconds before a step is measured"
    )
    play.add_argument("--hold", type=float, default=60.0, help="seconds a step is measured for")
    play.add_argument("--look-period", type=float, default=LOOK_PERIOD)
    play.add_argument(
        "--pow-per-minute", type=float, default=0.0, help="device fees per player per minute"
    )
    play.add_argument(
        "--pow-workers", type=int, default=2, help="concurrent solves on this machine"
    )
    play.add_argument(
        "--budget-ms", type=float, default=500.0, help="the look p95 still called good"
    )

    args = parser.parse_args()
    if args.ws is None:
        derived = args.http.replace("http://", "ws://").replace("https://", "wss://")
        args.ws = f"{derived.rstrip('/')}/session/ws"
    return asyncio.run(prepare(args) if args.command == "prepare" else run(args))


if __name__ == "__main__":
    raise SystemExit(main())
