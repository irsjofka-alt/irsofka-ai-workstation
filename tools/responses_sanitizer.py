#!/usr/bin/env python3
"""Buang pesan assistant kosong dari request Responses API sebelum ke Nvidia.

Cline (provider openai-native) berbicara lewat OpenAI Responses API. Ketika
model menambahkan teks kosong (mis. "\\n") pada balasan yang berisi tool call,
Cline mengirim item:

    {"role": "assistant", "content": [{"type": "output_text", "text": "\n"}]}

di antara `function_call` dan `function_call_output`. Endpoint Nvidia menolak
seluruh request dengan `data did not match any variant of untagged enum
InputParam`. Proxy ini hanya membuang item assistant yang isinya kosong; sisa
body, header, dan stream diteruskan apa adanya. Kunci tidak dibaca atau dicatat.
"""

import argparse
import http.client
import http.server
import json
import os
import ssl
import sys
import time
import urllib.parse

DEFAULT_UPSTREAM = "https://integrate.api.nvidia.com"
# Instruksi "maksimal N tool call" bisa diabaikan model. Setelah N panggilan
# terlihat di riwayat, proxy mencabut seluruh tool agar model wajib menyimpulkan.
TOOL_BUDGET = int(os.environ.get("CLINE_TOOL_BUDGET", "24"))


def blank_assistant(item) -> bool:
    if not isinstance(item, dict) or item.get("role") != "assistant":
        return False
    content = item.get("content")
    if isinstance(content, str):
        return not content.strip()
    if not isinstance(content, list):
        return False
    texts = []
    for part in content:
        if not isinstance(part, dict):
            return False
        kind = part.get("type")
        if kind in ("output_text", "text", "input_text"):
            texts.append(part.get("text") or "")
        elif kind == "reasoning":
            # Item reasoning bukan teks jawaban; biarkan.
            continue
        else:
            return False
    return bool(texts) and not "".join(texts).strip()


# Tool yang hasilnya bisa melampaui context model (search_codebase mengembalikan
# 100 hasil; satu run tercatat 2,94 juta token untuk context 1M). Tool ini
# dibuang dari daftar, sehingga model tidak bisa memanggilnya dan harus memakai
# run_commands (mis. grep dengan head) yang keluarannya bisa dibatasi.
BLOCKED_TOOLS = {"search_codebase"}


def sanitize(body: bytes) -> tuple:
    try:
        data = json.loads(body)
    except Exception:
        return body, 0
    removed = 0
    items = data.get("input")
    used = 0
    if isinstance(items, list):
        used = sum(1 for item in items if isinstance(item, dict) and item.get("type") == "function_call")
        kept = [item for item in items if not blank_assistant(item)]
        removed += len(items) - len(kept)
        data["input"] = kept
    over_budget = used >= TOOL_BUDGET
    tools = data.get("tools")
    if isinstance(tools, list):
        kept_tools = []
        for tool in tools:
            name = tool.get("name") if isinstance(tool, dict) else None
            if name is None and isinstance(tool, dict):
                name = (tool.get("function") or {}).get("name")
            if name in BLOCKED_TOOLS:
                continue
            if over_budget:
                # Budget habis: cabut semua tool supaya model menyimpulkan,
                # bukan terus menjelajah sampai run gagal.
                continue
            kept_tools.append(tool)
        removed += len(tools) - len(kept_tools)
        data["tools"] = kept_tools
    if over_budget:
        data["tool_choice"] = "none"
        sys.stderr.write(f"[sanitizer] budget {used}/{TOOL_BUDGET} tercapai; tool dicabut\n")
    if not removed and not over_budget:
        return body, 0
    return json.dumps(data).encode(), removed


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = DEFAULT_UPSTREAM
    verbose = False

    def log_message(self, fmt, *args):
        # Sengaja tanpa body; hanya status/permintaan.
        if self.verbose:
            sys.stderr.write("[sanitizer] " + (fmt % args) + "\n")

    def do_GET(self):
        self.forward(None)

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        self.forward(body)

    def forward(self, body):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if body and path.endswith("/responses"):
            body, removed = sanitize(body)
            if removed and self.verbose:
                sys.stderr.write(f"[sanitizer] membuang {removed} pesan assistant kosong\n")

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length", "connection")
        }
        headers["connection"] = "close"
        # Nvidia kadang memutus TLS di tengah request besar. Request model tidak
        # punya efek samping di sisi API, jadi percobaan ulang aman selama
        # respons belum mulai dikirim ke klien.
        response = None
        last_error = None
        for attempt in range(3):
            connection = http.client.HTTPSConnection(
                urllib.parse.urlsplit(self.upstream).netloc,
                timeout=900,
                context=ssl.create_default_context(),
            )
            try:
                connection.request(self.command, path, body=body, headers=headers)
                response = connection.getresponse()
            except (http.client.HTTPException, ssl.SSLError, OSError) as error:
                last_error = error
                connection.close()
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                continue
            if response.status == 429 and attempt < 2:
                # Cline membuat banyak panggilan di dalam satu proses; batas
                # 40 RPM Nvidia bisa tersentuh. Beri jeda, bukan langsung gagal.
                response.read()
                connection.close()
                response = None
                time.sleep(5 * (attempt + 1))
                continue
            break
        if response is None:
            self.send_error(502, f"upstream: {last_error}")
            return

        self.send_response(response.status)
        length = response.getheader("content-length")
        for key, value in response.getheaders():
            if key.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(key, value)
        if length is not None:
            self.send_header("content-length", length)
        else:
            self.send_header("transfer-encoding", "chunked")
        self.send_header("connection", "close")
        self.end_headers()

        try:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                if length is None:
                    self.wfile.write(f"{len(chunk):x}\r\n".encode())
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            if length is None:
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            connection.close()
            self.close_connection = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    Handler.upstream = args.upstream.rstrip("/")
    Handler.verbose = args.verbose
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write(f"[sanitizer] 127.0.0.1:{args.port} -> {Handler.upstream}\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
