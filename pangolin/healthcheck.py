#!/usr/bin/env python3
"""
Sidecar health checker dla HAProxy.
Sprawdza JEDNOCZEŚNIE: Pangolin HTTP API + YugabyteDB TCP.
Zwraca 200 tylko gdy oba są zdrowe.

Env vars:
  PANGOLIN_HOST  - hostname Pangolina (np. pangolin-1)
  YB_HOST        - IP YugabyteDB (np. 172.20.0.10)
  YB_PORT        - port YugabyteDB (domyślnie 5433)
"""
import http.server, subprocess, os, socket, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

PANGOLIN_HOST = os.environ["PANGOLIN_HOST"]
YB_HOST       = os.environ["YB_HOST"]
YB_PORT       = int(os.environ.get("YB_PORT", "5433"))

def check_pangolin() -> bool:
    r = subprocess.run(
        ["curl", "-sf", "--max-time", "3",
         f"http://{PANGOLIN_HOST}:3001/api/v1/"],
        capture_output=True
    )
    return r.returncode == 0

def check_yugabyte() -> bool:
    try:
        with socket.create_connection((YB_HOST, YB_PORT), timeout=3):
            return True
    except OSError:
        return False

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        p_ok = check_pangolin()
        y_ok = check_yugabyte()
        ok   = p_ok and y_ok
        code = 200 if ok else 503
        msg  = f"pangolin={'OK' if p_ok else 'FAIL'} yugabyte={'OK' if y_ok else 'FAIL'}"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())
        logging.info("%s %s → %d (%s)", PANGOLIN_HOST, YB_HOST, code, msg)

    def log_message(self, *_):
        pass  # cisza w logach HTTP

if __name__ == "__main__":
    logging.info("Health checker starting: pangolin=%s yb=%s:%d",
                 PANGOLIN_HOST, YB_HOST, YB_PORT)
    http.server.HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
