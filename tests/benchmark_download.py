r"""Local per-connection throttled benchmark; no external hosting traffic.

Run: .venv\Scripts\python.exe tests/benchmark_download.py
This measures parallel transfer potential, not real hosting service performance.
"""

import hashlib
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downloader.aria2_http_engine import Aria2HttpDownloadTask
from downloader.engine import DownloadTask

DATA = bytes(range(256)) * (64 * 1024)  # 16 MiB


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        start, end = 0, len(DATA) - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                self.send_error(400)
                return
            start = int(match[1])
            if match[2]:
                end = min(end, int(match[2]))
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Connection", "close")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", 'attachment; filename="payload.bin"')
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.end_headers()
        try:
            for offset in range(start, end + 1, 64 * 1024):
                self.wfile.write(DATA[offset : min(offset + 64 * 1024, end + 1)])
                self.wfile.flush()
                time.sleep(0.01)
        except (ConnectionError, OSError):
            pass

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    expected = hashlib.sha256(DATA).digest()
    try:
        with tempfile.TemporaryDirectory() as folder:
            url = f"http://127.0.0.1:{server.server_port}/payload.bin"
            for name, task in (
                (
                    "standard",
                    DownloadTask(url, folder, "payload.bin", partial_id="standard"),
                ),
                (
                    "aria2-8",
                    Aria2HttpDownloadTask(
                        url, folder, "payload.bin", partial_id="parallel", split=8
                    ),
                ),
            ):
                started = time.perf_counter()
                result = task.run()
                elapsed = time.perf_counter() - started
                if not result["success"]:
                    raise RuntimeError(result)
                actual = Path(result["file_path"]).read_bytes()
                assert hashlib.sha256(actual).digest() == expected, "Output mismatch"
                print(
                    f"{name}: {elapsed:.3f}s, {len(actual) / elapsed / 1048576:.2f} MiB/s; SHA-256 verified"
                )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
