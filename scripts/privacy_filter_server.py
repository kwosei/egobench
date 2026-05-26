from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from egobench.config import PrivacyCfg
from egobench.privacy import make_redactor


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve EgoBench privacy redaction locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8755)
    parser.add_argument("--model", default="openai/privacy-filter")
    parser.add_argument("--score-threshold", type=float, default=0.5)
    parser.add_argument("--replacement", default="[{label}]")
    args = parser.parse_args()

    redactor = make_redactor(
        PrivacyCfg(
            enabled=True,
            backend="transformers",
            model=args.model,
            score_threshold=args.score_threshold,
            replacement=args.replacement,
        )
    )
    server = ThreadingHTTPServer((args.host, args.port), _handler(redactor))
    print(f"Serving privacy redaction on http://{args.host}:{args.port}/redact")
    server.serve_forever()


def _handler(redactor: Any) -> type[BaseHTTPRequestHandler]:
    class PrivacyFilterHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.send_error(404)
                return
            self._send_json({"ok": True})

        def do_POST(self) -> None:
            if self.path != "/redact":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                text = payload["text"]
                if not isinstance(text, str):
                    raise ValueError("`text` must be a string.")
                result = redactor.redact(text)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(
                {
                    "redacted_text": result.text,
                    "spans": [asdict(span) for span in result.spans],
                }
            )

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return PrivacyFilterHandler


if __name__ == "__main__":
    main()
