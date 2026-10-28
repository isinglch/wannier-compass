"""Loopback-only, read-only event viewer. No workflow control or arbitrary file API."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlsplit

from .live import public_event, tool_summary
from .viewer_plots import trial_results, read_plot


class RunReader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._tool_indexes = {}
        self._index_lock = threading.RLock()

    @staticmethod
    def _tool_key(event):
        return json.dumps(
            [event.get(k) for k in ("role", "tool", "arguments", "seconds")],
            sort_keys=True,
        )

    def _tool_index(self, identifier):
        run = self.run(identifier)
        path = self.safe_file(run, "journal.jsonl")
        with self._index_lock:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            index = self._tool_indexes.get(identifier)
            if (
                index is None
                or index["identity"] != identity
                or stat.st_size < index["cursor"]
            ):
                index = {"identity": identity, "cursor": 0, "by_id": {}, "by_key": {}}
                self._tool_indexes[identifier] = index
            with path.open("rb") as stream:
                stream.seek(index["cursor"])
                while True:
                    start = stream.tell()
                    raw = stream.readline()
                    if not raw.endswith(b"\n"):
                        break
                    index["cursor"] = stream.tell()
                    try:
                        event = json.loads(raw)
                        if event.get("kind") != "tool":
                            continue
                        key = self._tool_key(event)
                        record_id = event["event_sha256"]
                        index["by_id"][record_id] = {
                            "offset": start,
                            "summary": tool_summary(event["result"]),
                        }
                        index["by_key"].setdefault(key, []).append(record_id)
                    except (ValueError, KeyError, TypeError):
                        continue
            return index

    def tool_result(self, identifier, record_id):
        if not re.fullmatch(r"[a-f0-9]{64}", record_id):
            raise ValueError("Invalid tool record identifier")
        index = self._tool_index(identifier)
        entry = index["by_id"].get(record_id)
        if entry is None:
            raise ValueError("Tool record not found")
        path = self.safe_file(self.run(identifier), "journal.jsonl")
        with path.open("rb") as stream:
            stream.seek(entry["offset"])
            event = json.loads(stream.readline())
        if event.get("kind") != "tool" or event.get("event_sha256") != record_id:
            raise ValueError("Tool record changed")
        return {k: event[k] for k in ("role", "tool", "arguments", "result")}

    def _enrich_tool(self, identifier, event):
        index = self._tool_index(identifier)
        record_id = event.get("tool_record_id")
        if not record_id:
            matches = index["by_key"].get(self._tool_key(event), [])
            # Old feeds omitted references. Never guess among ambiguous records.
            record_id = matches[0] if len(matches) == 1 else None
        entry = index["by_id"].get(record_id)
        if entry is None:
            return {**event, "tool_result_available": False}
        return {
            **event,
            "tool_record_id": record_id,
            "result": entry["summary"],
            "tool_result_available": True,
        }

    def run(self, identifier):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("Invalid run identifier")
        path = self.root / identifier
        if path.is_symlink() or path.resolve().parent != self.root or not path.is_dir():
            raise ValueError("Run not found")
        return path

    @staticmethod
    def safe_file(run, relative):
        path = run / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(run.resolve()) or path.is_symlink():
            raise ValueError("Record escapes run")
        return path

    def runs(self):
        rows = []
        if not self.root.is_dir():
            return rows
        for path in sorted(self.root.iterdir(), reverse=True):
            if (
                path.is_symlink()
                or not path.is_dir()
                or not (path / "journal.jsonl").is_file()
            ):
                continue
            try:
                final = self.safe_file(path, "final-report.json")
                report = json.loads(final.read_text()) if final.exists() else {}
                rows.append(
                    {
                        "id": path.name,
                        "stop": report.get("stop"),
                        "role_steps": report.get("role_steps"),
                        "trial_attempts": report.get("trial_attempts"),
                    }
                )
            except (ValueError, OSError):
                continue
        return rows

    def trials(self, identifier):
        return trial_results(self.run(identifier))

    def trial_plot(self, identifier, trial, plot, extension, checksum=None):
        return read_plot(self.run(identifier), trial, plot, extension, checksum)

    def events(self, identifier, offset=0, *, limit=256):
        run = self.run(identifier)
        live = self.safe_file(run, "events.jsonl")
        path = live if live.exists() else self.safe_file(run, "journal.jsonl")
        if not path.exists():
            return [], offset
        rows = []
        with path.open("rb") as stream:
            if offset < 0 or offset > path.stat().st_size:
                raise ValueError("Invalid event cursor")
            stream.seek(offset)
            for _ in range(limit):
                start = stream.tell()
                raw = stream.readline()
                if not raw.endswith(b"\n"):
                    stream.seek(start)
                    break
                try:
                    event = json.loads(raw)
                    if path != live:
                        response = None
                        if event.get("exchange"):
                            exchange = self.safe_file(run, event["exchange"])
                            response = json.loads(exchange.read_text()).get("response")
                        value = public_event(event["kind"], event, response)
                        event = (
                            {
                                "ts_utc": event["ts_utc"],
                                **value,
                                **(
                                    {"tool_record_id": event["event_sha256"]}
                                    if event["kind"] == "tool"
                                    else {}
                                ),
                            }
                            if value
                            else None
                        )
                    if event:
                        if event["kind"] == "tool":
                            event = self._enrich_tool(identifier, event)
                        rows.append({"cursor": stream.tell(), "event": event})
                except (ValueError, KeyError, OSError, TypeError):
                    continue
            return rows, stream.tell()


def make_server(root, port=8765):
    reader = RunReader(root)
    assets = Path(__file__).parent / "web"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *unused):
            pass

        def send(self, body, content_type="application/json; charset=utf-8", code=200):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            started = False
            # Same-origin UI only; reject DNS rebinding and cross-site fetches.
            host = urlsplit("http://" + self.headers.get("Host", "")).hostname
            origin = self.headers.get("Origin")
            if host not in ("127.0.0.1", "localhost") or (
                origin and origin != "http://" + self.headers.get("Host")
            ):
                self.send(b'{"error":"Local origin required"}', code=403)
                return
            try:
                if parsed.path in ("/", "/app.js", "/style.css"):
                    name, mime = {
                        "/": ("index.html", "text/html; charset=utf-8"),
                        "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                        "/style.css": ("style.css", "text/css; charset=utf-8"),
                    }[parsed.path]
                    self.send((assets / name).read_bytes(), mime)
                elif parsed.path == "/api/runs":
                    self.send(json.dumps(reader.runs(), ensure_ascii=False).encode())
                elif parsed.path == "/api/tool-result":
                    query = parse_qs(parsed.query)
                    value = reader.tool_result(
                        query.get("run", [""])[0], query.get("record", [""])[0]
                    )
                    self.send(json.dumps(value, ensure_ascii=False).encode())
                elif parsed.path == "/api/trials":
                    query = parse_qs(parsed.query)
                    self.send(
                        json.dumps(
                            reader.trials(query.get("run", [""])[0]), ensure_ascii=False
                        ).encode()
                    )
                elif parsed.path == "/api/trial-plot":
                    query = parse_qs(parsed.query)
                    body, mime, _, _ = reader.trial_plot(
                        query.get("run", [""])[0],
                        query.get("trial", [""])[0],
                        query.get("plot", [""])[0],
                        query.get("format", ["png"])[0],
                        query.get("sha256", [None])[0],
                    )
                    self.send(body, mime)
                elif parsed.path == "/api/events":
                    query = parse_qs(parsed.query)
                    identifier = query.get("run", [""])[0]
                    offset = int(
                        self.headers.get("Last-Event-ID")
                        or query.get("after", ["0"])[0]
                    )
                    reader.events(
                        identifier, offset, limit=0
                    )  # Validate before SSE headers.
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    started = True
                    while not self.server.stopping.is_set():
                        rows, offset = reader.events(identifier, offset)
                        for row in rows:
                            body = json.dumps(row["event"], ensure_ascii=False)
                            self.wfile.write(
                                f"id: {row['cursor']}\ndata: {body}\n\n".encode()
                            )
                        # Cursor also advances over ignored historical audit rows.
                        self.wfile.write(
                            f"id: {offset}\nevent: cursor\ndata: {{}}\n\n".encode()
                        )
                        self.wfile.flush()
                        self.server.stopping.wait(0.25)
                else:
                    self.send(b'{"error":"Not found"}', code=404)
            except (ValueError, OSError, KeyError):
                if not started:
                    self.send(b'{"error":"Record unavailable"}', code=404)
                else:
                    self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.stopping = threading.Event()
    return server


def serve(root, port):
    server = make_server(root, port)
    print(
        json.dumps(
            {
                "viewer_url": f"http://127.0.0.1:{server.server_port}",
                "runs_root": str(Path(root).resolve()),
                "read_only": True,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()
