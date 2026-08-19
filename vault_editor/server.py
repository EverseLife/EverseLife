"""The local server of the recipe editor.

Standard library only, on purpose: the tool has to start in a fresh clone with
nothing but `pyyaml` -- the same single dependency the vault's own build has.
It binds to the loopback address and to nothing else: it writes files, and a tool
that writes files has no business listening on the network.

    python vault_editor/server.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import ladder as model
import vaultfile as vault

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Session:
    """Everything a request needs, and one lock so two saves never overlap."""

    def __init__(self, vault_root: Path):
        self.vault = vault_root
        self.source = vault_root / "data" / "recipes.yaml"
        self.lock = threading.Lock()

    def open(self, text: str | None = None) -> tuple[vault.RecipesFile, model.Ladder]:
        file = vault.RecipesFile(self.source, text=text)
        derived, stale = model.load_derived(self.vault)
        ladder = model.Ladder(file, derived)
        ladder.stale = stale
        return file, ladder


# ------------------------------------------------------------------ handlers


def state(session: Session, _query: dict, _body: dict) -> dict:
    file, ladder = session.open()
    return {
        "vault": str(session.vault),
        "source": str(session.source),
        "stale": ladder.stale,
        "nodes": ladder.nodes(),
        "edges": ladder.edges(),
        "operations": [
            {
                "name": op["name"],
                "requires": op.get("requires") or [],
                "gives": op.get("gives") or [],
                "consumes": op.get("consumes") or [],
                "place": op.get("place"),
            }
            for op in ladder.operations
        ],
        "stations": ladder.stations(),
        "vocabulary": ladder.vocabulary(),
        "counts": {
            "recipes": len(ladder.recipes),
            "raw": len(ladder.raw),
            "operations": len(ladder.operations),
        },
        "undo": (backup.name if (backup := vault.last_backup()) else None),
    }


def recipe(session: Session, query: dict, _body: dict) -> dict:
    name = _need(query, "name")
    file, ladder = session.open()
    if name not in ladder.known_names():
        raise vault.VaultError(f"«{name}» нет в вольте")
    # Only recipes have a form. Raw material, operations and tool classes are
    # shown as they are: there are few of them, each line is explained by a
    # comment above it, and a form would only invite editing them blindly.
    authored = dict(ladder.recipes.get(name) or {})
    return {
        "name": name,
        "editable": name in ladder.recipes,
        "data": {k: v for k, v in authored.items() if k not in ("level", "section")},
        "level": authored.get("level"),
        "section": authored.get("section"),
        "source": file.source_of(name),
        "comment": file.comment_above(name),
        "references": model.references(name, ladder),
        "cost": ladder.raw_cost(name),
        "derived": ladder.derived_recipes.get(name),
    }


def cost(session: Session, query: dict, _body: dict) -> dict:
    name = _need(query, "name")
    _, ladder = session.open()
    quantity = float(query.get("quantity", ["1"])[0])
    return ladder.raw_cost(name, quantity)


def create(session: Session, _query: dict, body: dict) -> dict:
    data = _clean(body.get("data") or {})
    level = body.get("level")
    section = body.get("section") or None
    if level is None:
        raise vault.VaultError("не выбран уровень лестницы")
    with session.lock:
        file, ladder = session.open()
        model.validate(data, ladder)
        lines = file.insert(data, int(level), section)
        vault.save(
            session.source,
            lines,
            {"name": data["name"], "data": data},
            file.mtime,
            file.newline,
        )
    return {"saved": data["name"], "check": _check(session)}


def update(session: Session, query: dict, body: dict) -> dict:
    original = _need(query, "name")
    data = _clean(body.get("data") or {})
    rename_refs = bool(body.get("rename_refs"))
    with session.lock:
        file, ladder = session.open()
        if original not in ladder.recipes:
            raise vault.VaultError(f"рецепта «{original}» нет в файле")
        model.validate(data, ladder, original=original)

        renamed = data["name"] != original
        text = file.text
        if renamed and rename_refs:
            text = vault.rename_everywhere(file.text, original, data["name"])
            file = vault.RecipesFile(session.source, text=text, newline=file.newline)

        was = ladder.recipes[original]
        target = int(body["level"]) if body.get("level") is not None else was["level"]
        section = (body.get("section") if "section" in body else was["section"]) or None
        moved = (target, section) != (was["level"], was["section"])

        key = data["name"] if (renamed and rename_refs) else original
        if moved:
            file = vault.RecipesFile(
                session.source, text="\n".join(file.cut(key)), newline=file.newline
            )
            lines = file.insert(data, target, section)
        else:
            lines = file.replace(key, data)

        expect: dict[str, Any] = {"name": data["name"], "data": data}
        if renamed:
            expect["absent"] = [original]
        vault.save(
            session.source,
            lines,
            expect,
            file.mtime if not renamed else None,
            file.newline,
        )
    return {"saved": data["name"], "renamed": renamed, "check": _check(session)}


def delete(session: Session, query: dict, body: dict) -> dict:
    name = _need(query, "name")
    with session.lock:
        file, ladder = session.open()
        if name not in ladder.recipes:
            raise vault.VaultError(f"рецепта «{name}» нет в файле")
        lines = file.cut(name, with_comment=bool(body.get("with_comment")))
        vault.save(
            session.source,
            lines,
            {"name": name, "data": None},
            file.mtime,
            file.newline,
        )
    return {"deleted": name, "check": _check(session)}


def undo(session: Session, _query: dict, _body: dict) -> dict:
    with session.lock:
        restored = vault.undo(session.source)
    return {"restored": restored, "check": _check(session)}


def check(session: Session, _query: dict, _body: dict) -> dict:
    return _check(session)


def build(session: Session, _query: dict, _body: dict) -> dict:
    return _run([sys.executable, "tools/build.py"], session.vault)


def sync(session: Session, _query: dict, _body: dict) -> dict:
    """Build the vault and refresh the snapshot the engine reads.

    The editor does not do this itself: it runs `deploy/sync-vault.py`, the same
    script a person runs by hand and the same one CI would. One implementation,
    one behaviour -- on Windows, on Linux and inside the container, where the
    script arrives on a mounted volume.
    """
    script = vault.REPO / "deploy" / "sync-vault.py"
    if not script.exists():
        raise vault.VaultError(
            f"нет скрипта синхронизации: {script}. В контейнере подключите папку "
            "deploy репозитория — том `../deploy:/repo/deploy`."
        )
    return _run(
        [sys.executable, str(script), "--vault", str(session.vault)],
        vault.REPO,
    )


ROUTES = {
    ("GET", "/api/state"): state,
    ("GET", "/api/recipe"): recipe,
    ("GET", "/api/cost"): cost,
    ("POST", "/api/recipe"): create,
    ("PUT", "/api/recipe"): update,
    ("DELETE", "/api/recipe"): delete,
    ("POST", "/api/check"): check,
    ("POST", "/api/build"): build,
    ("POST", "/api/sync"): sync,
    ("POST", "/api/undo"): undo,
}


# ------------------------------------------------------------------- helpers


def _need(query: dict, key: str) -> str:
    values = query.get(key)
    if not values or not values[0]:
        raise vault.VaultError(f"не хватает параметра «{key}»")
    return values[0]


def _clean(data: dict) -> dict:
    """The authored fields, without the empty ones the form always sends."""
    out: dict[str, Any] = {}
    for key in vault.KEY_ORDER:
        if key not in data:
            continue
        value = data[key]
        if value in (None, "", [], {}) or (key in vault.BOOL_FIELDS and not value):
            continue
        if key in vault.NUMBER_FIELDS:
            value = float(value)
            value = int(value) if float(value).is_integer() else value
        if key in vault.MAP_FIELDS:
            value = {k: (int(v) if float(v).is_integer() else float(v)) for k, v in value.items()}
        out[key] = value
    if "name" not in out:
        raise vault.VaultError("у рецепта должно быть название")
    return out


def _check(session: Session) -> dict:
    return _run([sys.executable, "tools/build.py", "--check"], session.vault)


def _run(command: list[str], cwd: Path) -> dict:
    """Run a vault tool and bring its words back as they were printed.

    Windows would hand the child a cp1251 pipe and the Russian output would come
    back as question marks, so the child is told to speak UTF-8.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        done = subprocess.run(
            command, cwd=cwd, capture_output=True, env=env, timeout=300, check=False
        )
    except FileNotFoundError as error:
        raise vault.VaultError(f"не удалось запустить: {' '.join(command)} ({error})") from error
    text = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
    return {"command": " ".join(command), "code": done.returncode, "output": text}


# -------------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    session: Session

    protocol_version = "HTTP/1.1"
    server_version = "vault-editor"

    def do_GET(self) -> None:  # noqa: N802 -- the name is the stdlib's
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._api("GET", parsed)
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        self._api("POST", urlparse(self.path))

    def do_PUT(self) -> None:  # noqa: N802
        self._api("PUT", urlparse(self.path))

    def do_DELETE(self) -> None:  # noqa: N802
        self._api("DELETE", urlparse(self.path))

    def log_message(self, fmt: str, *args: Any) -> None:
        if "500" in str(args) or "400" in str(args):
            super().log_message(fmt, *args)

    # -- plumbing ----------------------------------------------------------

    def _api(self, method: str, parsed) -> None:
        route = ROUTES.get((method, parsed.path))
        if route is None:
            self._send(404, {"error": f"нет такого адреса: {method} {parsed.path}"})
            return
        try:
            body = self._body()
            payload = route(self.session, parse_qs(parsed.query), body)
            self._send(200, payload)
        except vault.VaultError as error:
            self._send(400, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 -- a dev tool reports, not dies
            import traceback

            self._send(500, {"error": f"{type(error).__name__}: {error}",
                             "trace": traceback.format_exc()})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / name).resolve()
        if not target.is_file() or STATIC.resolve() not in target.parents:
            self._send(404, {"error": f"нет файла: {name}"})
            return
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "text/plain"))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Редактор рецептов вольта OctoVerse")
    port = int(os.environ.get("OCTOVERSE_EDITOR_PORT", 8765))
    parser.add_argument("--port", type=int, default=port)
    parser.add_argument("--vault", default=None, help="путь к вольту гейм-дизайна")
    # Loopback by default: инструмент пишет в файлы. В контейнере петля своя, и
    # снаружи к ней не пробиться, поэтому образ поднимает сервер на 0.0.0.0 —
    # границей там служит проброс порта, а не адрес.
    parser.add_argument(
        "--host",
        default=os.environ.get("OCTOVERSE_EDITOR_HOST", "127.0.0.1"),
        help="адрес, на котором слушать (по умолчанию только петля)",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.vault:
        os.environ["OCTOVERSE_VAULT"] = args.vault
    try:
        root = vault.vault_root()
    except vault.VaultError as error:
        print(error, file=sys.stderr)
        return 1

    Handler.session = Session(root)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host  # noqa: S104
    url = f"http://{shown}:{args.port}/"
    print(f"Редактор рецептов: {url}")
    print(f"Вольт: {root}")
    print("Правится только data/recipes.yaml. Ctrl+C — выход.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nВыход.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
