import os, sys, time, json, signal, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENTRY = os.environ.get("MATRIX_ENTRYPOINT", "main.py")
PORT = int(os.environ.get("PORT", "10000"))
ROOT = os.path.dirname(os.path.abspath(__file__))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            body = json.dumps({"status": "healthy", "project": os.path.basename(ROOT)}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *args):
        print("[health]", *args)

def serve():
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=serve, daemon=True).start()
script = os.path.abspath(os.path.join(ROOT, ENTRY))
if not script.startswith(ROOT + os.sep) or not os.path.isfile(script):
    raise SystemExit("Invalid MATRIX_ENTRYPOINT")
restarts = 0
while True:
    print("[MATRIX] Starting user process:", ENTRY, flush=True)
    proc = subprocess.Popen([sys.executable, "-u", script], cwd=ROOT, env=os.environ.copy())
    code = proc.wait()
    print("[MATRIX] User process exited with code", code, flush=True)
    if os.environ.get("MATRIX_AUTORESTART", "true").lower() != "true" or restarts >= 5:
        break
    restarts += 1
    time.sleep(min(5 * restarts, 30))
