import http.server
import json
import os

PORT = 8989
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
EVENTS_FILE = os.path.join(BASE_DIR, 'eventos.json')
TASKS_FILE  = os.path.join(BASE_DIR, 'tareas.json')
NOTES_FILE  = os.path.join(BASE_DIR, 'notas.json')   # apuntes por bloque


class Handler(http.server.BaseHTTPRequestHandler):

    # ─── GET ───────────────────────────────────────────────────
    def do_GET(self):
        if self.path in ('/', '/calendario.html'):
            self._serve_file('calendario.html', 'text/html')

        elif self.path == '/eventos':
            self._serve_json(EVENTS_FILE, default='[]')

        elif self.path == '/tareas':
            self._serve_json(TASKS_FILE, default='[]')

        elif self.path == '/notas':
            self._serve_json(NOTES_FILE, default='{}')

        elif self.path == '/ping':
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'pong')

        else:
            self.send_response(404)
            self.end_headers()

    # ─── POST ──────────────────────────────────────────────────
    def do_POST(self):
        if self.path == '/eventos':
            self._save_json(EVENTS_FILE)

        elif self.path == '/tareas':
            self._save_json(TASKS_FILE)

        elif self.path == '/notas':
            self._save_json(NOTES_FILE)

        else:
            self.send_response(404)
            self.end_headers()

    # ─── OPTIONS (CORS preflight) ──────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    # ─── HELPERS ───────────────────────────────────────────────
    def _serve_file(self, filename, content_type):
        path = os.path.join(BASE_DIR, filename)
        if not os.path.exists(path):
            self.send_response(404)
            self.end_headers()
            return
        with open(path, 'rb') as f:
            content = f.read()
        self.send_response(200)
        self.send_header('Content-Type',   content_type + '; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

    def _serve_json(self, filepath, default='[]'):
        """Devuelve el contenido de un archivo JSON (o el default si no existe)."""
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            content = default
        data = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type',             'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length',           len(data))
        self.end_headers()
        self.wfile.write(data)

    def _save_json(self, filepath):
        """Recibe un JSON en el body y lo persiste en disco."""
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try:
            data = json.loads(body)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.send_response(200)
            self.send_header('Content-Type',             'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, format, *args):
        pass  # silenciar logs en consola


print(f'✅ Servidor corriendo en http://localhost:{PORT}')
print('📅 Calendario disponible — Ctrl+C para apagar')
print('📋 Tareas disponibles en /tareas')
print('📝 Apuntes disponibles en /notas')

httpd = http.server.HTTPServer(('localhost', PORT), Handler)
httpd.serve_forever()
