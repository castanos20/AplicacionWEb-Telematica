import http.server
import json
import os
import hashlib
import secrets
import time
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse

PORT     = 8989
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE  = os.path.join(BASE_DIR, 'calendario.db')

# ── Configuración de correo ──────────────────────────────────────────
SMTP_USER = 'krate.arroz@gmail.com'
SMTP_PASS = 'xfnx vkjv kwtk mztp'
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587
RESET_TTL = 60 * 30   # 30 minutos

sessions     = {}
reset_tokens = {}   # { token: { "user_id": int, "expires": float } }
SESSION_TTL  = 60 * 60 * 24 * 7

# ════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ════════════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT    NOT NULL UNIQUE,
            password   TEXT    NOT NULL,
            salt       TEXT    NOT NULL,
            email      TEXT    DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS eventos (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            name        TEXT    NOT NULL,
            location    TEXT    DEFAULT '',
            recurring   INTEGER DEFAULT 0,
            date        TEXT,
            day_of_week INTEGER,
            start       TEXT    NOT NULL,
            end         TEXT    NOT NULL,
            color       TEXT    DEFAULT 'orange'
        );
        CREATE TABLE IF NOT EXISTS tareas (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            title       TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            status      TEXT    DEFAULT 'pending',
            priority    TEXT    DEFAULT 'medium',
            date        TEXT,
            hour        INTEGER,
            event_id    INTEGER,
            created_at  INTEGER
        );
        CREATE TABLE IF NOT EXISTS notas (
            user_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            key      TEXT    NOT NULL,
            content  TEXT    NOT NULL,
            PRIMARY KEY (user_id, key)
        );
        CREATE TABLE IF NOT EXISTS eventos_manuales (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            title       TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            date        TEXT,
            hour        INTEGER,
            end_hour    INTEGER,
            color       TEXT    DEFAULT 'blue',
            event_id    INTEGER,
            created_at  INTEGER
        );
        """)
        # Migraciones seguras
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN email TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN is_admin INTEGER DEFAULT 0")
        except Exception:
            pass
        # Migración: recrear eventos_manuales si hour tiene NOT NULL (schema viejo)
        try:
            cols = conn.execute("PRAGMA table_info(eventos_manuales)").fetchall()
            hour_col = next((c for c in cols if c[1] == 'hour'), None)
            if hour_col and hour_col[3] == 1:  # notnull == 1
                conn.executescript("""
                    ALTER TABLE eventos_manuales RENAME TO eventos_manuales_old;
                    CREATE TABLE eventos_manuales (
                        id          INTEGER PRIMARY KEY,
                        user_id     INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                        title       TEXT    NOT NULL,
                        description TEXT    DEFAULT '',
                        date        TEXT,
                        hour        INTEGER,
                        end_hour    INTEGER,
                        color       TEXT    DEFAULT 'blue',
                        event_id    INTEGER,
                        created_at  INTEGER
                    );
                    INSERT INTO eventos_manuales SELECT * FROM eventos_manuales_old;
                    DROP TABLE eventos_manuales_old;
                """)
                print("✅  Migración eventos_manuales completada")
        except Exception as e:
            print(f"⚠️   Migración eventos_manuales: {e}")

# ════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════

def hash_password(password, salt):
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()

def purge_expired_sessions():
    now = time.time()
    for t in [t for t, s in sessions.items() if s['expires'] < now]:
        del sessions[t]

def purge_expired_resets():
    now = time.time()
    for t in [t for t, s in reset_tokens.items() if s['expires'] < now]:
        del reset_tokens[t]

def get_session(token):
    purge_expired_sessions()
    s = sessions.get(token)
    return s if (s and s['expires'] > time.time()) else None

def extract_token(handler):
    for part in handler.headers.get('Cookie', '').split(';'):
        part = part.strip()
        if part.startswith('session='):
            return part[len('session='):]
    auth = handler.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return None

def count_users():
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]

def _is_admin(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT is_admin FROM usuarios WHERE id=?", (user_id,)).fetchone()
        return bool(row and row['is_admin'])

def send_reset_email(to_email, username, token):
    """Envía el correo con el link de recuperación."""
    reset_link = f'http://localhost:{PORT}/reset.html?token={token}'
    subject    = '🔑 Recuperación de contraseña — Horario'
    body_html  = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;background:#0d1117;color:#e6edf3;border-radius:12px;padding:32px">
      <h2 style="color:#3dd68c;margin-top:0">📅 Horario</h2>
      <p>Hola <strong>{username}</strong>,</p>
      <p>Recibimos una solicitud para restablecer tu contraseña.</p>
      <p style="margin:24px 0">
        <a href="{reset_link}"
           style="background:#3dd68c;color:#0d1117;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700">
          Restablecer contraseña
        </a>
      </p>
      <p style="color:#8b949e;font-size:13px">Este enlace expira en 30 minutos.<br>
      Si no solicitaste esto, ignora este correo.</p>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f'Horario <{SMTP_USER}>'
    msg['To']      = to_email
    msg.attach(MIMEText(body_html, 'html'))

    print(f'📧  Conectando a {SMTP_HOST}:{SMTP_PORT}...')
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, to_email, msg.as_string())
    print(f'✅  Correo enviado a {to_email}')

# ════════════════════════════════════════════════════════════════════
#  SERIALIZACIÓN DB → JSON
# ════════════════════════════════════════════════════════════════════

def rows_to_eventos(rows):
    result = []
    for r in rows:
        ev = {'id': r['id'], 'name': r['name'], 'location': r['location'] or '',
              'recurring': bool(r['recurring']), 'start': r['start'], 'end': r['end'], 'color': r['color']}
        if r['recurring']:
            ev['dayOfWeek'] = r['day_of_week']
        else:
            ev['date'] = r['date']
        result.append(ev)
    return result

def rows_to_tareas(rows):
    return [{'id': r['id'], 'title': r['title'], 'description': r['description'] or '',
             'status': r['status'], 'priority': r['priority'], 'date': r['date'],
             'hour': r['hour'], 'eventId': r['event_id'], 'createdAt': r['created_at']} for r in rows]

def rows_to_notas(rows):
    return {r['key']: r['content'] for r in rows}

def rows_to_manuales(rows):
    return [{'id': r['id'], 'title': r['title'], 'description': r['description'] or '',
             'date': r['date'], 'hour': r['hour'], 'endHour': r['end_hour'],
             'color': r['color'] or 'blue', 'eventId': r['event_id'],
             'createdAt': r['created_at']} for r in rows]

def save_eventos(user_id, data):
    with get_db() as conn:
        conn.execute("DELETE FROM eventos WHERE user_id = ?", (user_id,))
        for ev in data:
            conn.execute(
                "INSERT INTO eventos (id,user_id,name,location,recurring,date,day_of_week,start,end,color) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ev.get('id') or int(time.time()*1000), user_id, ev.get('name',''), ev.get('location',''),
                 1 if ev.get('recurring') else 0, ev.get('date'), ev.get('dayOfWeek'),
                 ev.get('start',''), ev.get('end',''), ev.get('color','orange')))

def save_tareas(user_id, data):
    with get_db() as conn:
        conn.execute("DELETE FROM tareas WHERE user_id = ?", (user_id,))
        for t in data:
            conn.execute(
                "INSERT INTO tareas (id,user_id,title,description,status,priority,date,hour,event_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t.get('id') or int(time.time()*1000), user_id, t.get('title',''), t.get('description',''),
                 t.get('status','pending'), t.get('priority','medium'), t.get('date'),
                 t.get('hour'), t.get('eventId'), t.get('createdAt') or int(time.time()*1000)))

def save_notas(user_id, data):
    with get_db() as conn:
        conn.execute("DELETE FROM notas WHERE user_id = ?", (user_id,))
        for key, content in data.items():
            if content and content.strip():
                conn.execute("INSERT INTO notas (user_id,key,content) VALUES (?,?,?)", (user_id, key, content))

def save_manuales(user_id, data):
    with get_db() as conn:
        conn.execute("DELETE FROM eventos_manuales WHERE user_id = ?", (user_id,))
        for m in data:
            conn.execute(
                "INSERT INTO eventos_manuales (id,user_id,title,description,date,hour,end_hour,color,event_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (m.get('id') or int(time.time()*1000), user_id, m.get('title',''), m.get('description',''),
                 m.get('date',''), m.get('hour', 0), m.get('endHour'),
                 m.get('color','blue'), m.get('eventId'), m.get('createdAt') or int(time.time()*1000)))


# ════════════════════════════════════════════════════════════════════
#  HANDLER HTTP
# ════════════════════════════════════════════════════════════════════

class Handler(http.server.BaseHTTPRequestHandler):

    def _cors(self):
        origin = self.headers.get('Origin', '*')
        self.send_header('Access-Control-Allow-Origin',      origin)
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Methods',     'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',     'Content-Type, Authorization')

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ('/', '/calendario.html'):
            self._serve_file('calendario.html', 'text/html'); return

        if path == '/reset.html':
            self._serve_file('reset.html', 'text/html'); return

        if path == '/admin':
            self._serve_file('admin.html', 'text/html'); return

        if path == '/admin/usuarios':
            session = get_session(extract_token(self))
            if not session or not _is_admin(session['user_id']):
                self._json_err(403, 'Acceso denegado'); return
            with get_db() as conn:
                rows = conn.execute('SELECT id, username, COALESCE(email,\'\') as email, created_at, COALESCE(is_admin,0) as is_admin FROM usuarios ORDER BY created_at').fetchall()
            users = [{'id': r['id'], 'username': r['username'], 'email': r['email'] or '', 'created_at': r['created_at'], 'is_admin': bool(r['is_admin'])} for r in rows]
            self._json_ok(users); return

        if path == '/ping':
            self.send_response(200); self._cors(); self.end_headers()
            self.wfile.write(b'pong'); return

        session = get_session(extract_token(self))

        if path == '/me':
            if session: self._json_ok({'ok': True, 'user': session['username'], 'is_admin': _is_admin(session['user_id'])})
            else:        self._json_err(401, 'No autenticado')
            return

        if not session:
            self._json_err(401, 'No autenticado'); return

        uid = session['user_id']

        if path == '/eventos':
            with get_db() as conn:
                rows = conn.execute("SELECT * FROM eventos WHERE user_id=?", (uid,)).fetchall()
            self._json_ok(rows_to_eventos(rows)); return

        if path == '/tareas':
            with get_db() as conn:
                rows = conn.execute("SELECT * FROM tareas WHERE user_id=?", (uid,)).fetchall()
            self._json_ok(rows_to_tareas(rows)); return

        if path == '/notas':
            with get_db() as conn:
                rows = conn.execute("SELECT * FROM notas WHERE user_id=?", (uid,)).fetchall()
            self._json_ok(rows_to_notas(rows)); return

        if path == '/manuales':
            with get_db() as conn:
                rows = conn.execute("SELECT * FROM eventos_manuales WHERE user_id=?", (uid,)).fetchall()
            self._json_ok(rows_to_manuales(rows)); return

        self.send_response(404); self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        # ── Login ────────────────────────────────────────────────────
        if path == '/login':
            body = self._read_body()
            try:
                data = json.loads(body)
                username = data.get('username','').strip().lower()
                password = data.get('password','')
            except: self._json_err(400, 'JSON inválido'); return

            with get_db() as conn:
                user = conn.execute("SELECT * FROM usuarios WHERE username=?", (username,)).fetchone()

            if not user or hash_password(password, user['salt']) != user['password']:
                self._json_err(401, 'Usuario o contraseña incorrectos'); return

            token = secrets.token_hex(32)
            sessions[token] = {'user_id': user['id'], 'username': user['username'],
                                'expires': time.time() + SESSION_TTL}
            self.send_response(200); self._cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', f'session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True, 'token': token, 'user': user['username'], 'is_admin': _is_admin(user['id'])}).encode())
            return

        # ── Logout ───────────────────────────────────────────────────
        if path == '/logout':
            token = extract_token(self)
            if token and token in sessions: del sessions[token]
            self.send_response(200); self._cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', 'session=; Path=/; Max-Age=0')
            self.end_headers(); self.wfile.write(b'{"ok":true}'); return

        # ── Registro ─────────────────────────────────────────────────
        if path == '/register':
            body = self._read_body()
            try:
                data     = json.loads(body)
                username = data.get('username','').strip().lower()
                password = data.get('password','')
                email    = data.get('email','').strip().lower()
            except: self._json_err(400, 'JSON inválido'); return

            if not username or not password:
                self._json_err(400, 'Usuario y contraseña requeridos'); return
            if len(username) < 3:
                self._json_err(400, 'El usuario debe tener al menos 3 caracteres'); return
            if len(password) < 4:
                self._json_err(400, 'La contraseña debe tener al menos 4 caracteres'); return

            with get_db() as conn:
                if conn.execute("SELECT id FROM usuarios WHERE username=?", (username,)).fetchone():
                    self._json_err(409, 'El usuario ya existe'); return
                if email and conn.execute("SELECT id FROM usuarios WHERE email=?", (email,)).fetchone():
                    self._json_err(409, 'Ese correo ya está registrado'); return
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO usuarios (username,password,salt,email,created_at) VALUES (?,?,?,?,?)",
                             (username, hash_password(password, salt), salt, email, int(time.time()*1000)))

            self._json_ok({'ok': True, 'user': username}); return

        # ── Olvidé mi contraseña ──────────────────────────────────────
        if path == '/forgot':
            body = self._read_body()
            try:
                data  = json.loads(body)
                email = data.get('email','').strip().lower()
            except: self._json_err(400, 'JSON inválido'); return

            with get_db() as conn:
                user = conn.execute("SELECT * FROM usuarios WHERE email=?", (email,)).fetchone()

            # Siempre responde OK para no revelar si el correo está registrado
            if not user:
                self._json_ok({'ok': True, 'msg': 'Si ese correo está registrado, recibirás un email.'}); return

            purge_expired_resets()
            token = secrets.token_hex(32)
            reset_tokens[token] = {'user_id': user['id'], 'expires': time.time() + RESET_TTL}

            try:
                send_reset_email(user['email'], user['username'], token)
                print(f'📧 Reset enviado a {user["email"]} para {user["username"]}')
            except Exception as e:
                print(f'❌ Error enviando correo: {e}')
                self._json_err(500, 'Error al enviar el correo'); return

            self._json_ok({'ok': True, 'msg': 'Si el usuario existe y tiene correo, recibirás un email.'}); return

        # ── Reset de contraseña ───────────────────────────────────────
        if path == '/reset':
            body = self._read_body()
            try:
                data         = json.loads(body)
                token        = data.get('token','')
                new_password = data.get('password','')
            except: self._json_err(400, 'JSON inválido'); return

            purge_expired_resets()
            rt = reset_tokens.get(token)
            if not rt:
                self._json_err(400, 'El enlace es inválido o ya expiró'); return
            if len(new_password) < 4:
                self._json_err(400, 'La contraseña debe tener al menos 4 caracteres'); return

            uid  = rt['user_id']
            salt = secrets.token_hex(16)
            with get_db() as conn:
                conn.execute("UPDATE usuarios SET password=?, salt=? WHERE id=?",
                             (hash_password(new_password, salt), salt, uid))

            del reset_tokens[token]
            self._json_ok({'ok': True}); return

        # ── Admin: eliminar usuario ──────────────────────────────────
        if path == '/admin/delete':
            session = get_session(extract_token(self))
            if not session or not _is_admin(session['user_id']):
                self._json_err(403, 'Acceso denegado'); return
            body = self._read_body()
            try: data = json.loads(body)
            except: self._json_err(400, 'JSON inválido'); return
            uid_del = data.get('user_id')
            if not uid_del:
                self._json_err(400, 'user_id requerido'); return
            with get_db() as conn:
                user = conn.execute('SELECT username FROM usuarios WHERE id=?', (uid_del,)).fetchone()
                if not user:
                    self._json_err(404, 'Usuario no encontrado'); return
                if _is_admin(uid_del):
                    self._json_err(400, 'No puedes eliminar a un administrador'); return
                conn.execute('DELETE FROM usuarios WHERE id=?', (uid_del,))
            # Invalidar sesiones del usuario eliminado
            for t, s in list(sessions.items()):
                if s['user_id'] == uid_del:
                    del sessions[t]
            print(f'🗑️  Admin eliminó usuario id={uid_del} ({user["username"]})')
            self._json_ok({'ok': True}); return

        # ── Admin: resetear contraseña ───────────────────────────────
        if path == '/admin/reset-password':
            session = get_session(extract_token(self))
            if not session or not _is_admin(session['user_id']):
                self._json_err(403, 'Acceso denegado'); return
            body = self._read_body()
            try: data = json.loads(body)
            except: self._json_err(400, 'JSON inválido'); return
            uid_rst  = data.get('user_id')
            new_pass = data.get('password', '').strip()
            if not uid_rst or not new_pass:
                self._json_err(400, 'user_id y password requeridos'); return
            if len(new_pass) < 4:
                self._json_err(400, 'Mínimo 4 caracteres'); return
            salt = secrets.token_hex(16)
            with get_db() as conn:
                user = conn.execute('SELECT username FROM usuarios WHERE id=?', (uid_rst,)).fetchone()
                if not user:
                    self._json_err(404, 'Usuario no encontrado'); return
                conn.execute('UPDATE usuarios SET password=?, salt=? WHERE id=?',
                             (hash_password(new_pass, salt), salt, uid_rst))
            # Invalidar sesiones del usuario reseteado
            for t, s in list(sessions.items()):
                if s['user_id'] == uid_rst:
                    del sessions[t]
            print(f'🔑  Admin reseteó contraseña de {user["username"]}')
            self._json_ok({'ok': True}); return

        # ── Rutas autenticadas ────────────────────────────────────────
        session = get_session(extract_token(self))
        if not session:
            self._json_err(401, 'No autenticado'); return

        uid  = session['user_id']
        body = self._read_body()
        try: data = json.loads(body)
        except: self._json_err(400, 'JSON inválido'); return

        if path == '/perfil':
            new_username = data.get('username','').strip().lower()
            new_email    = data.get('email','').strip().lower()
            new_password = data.get('password','').strip()
            cur_password = data.get('current_password','')

            # Verificar contraseña actual
            with get_db() as conn:
                user = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
            if not user or hash_password(cur_password, user['salt']) != user['password']:
                self._json_err(401, 'Contraseña actual incorrecta'); return

            # Validaciones
            if new_username and len(new_username) < 3:
                self._json_err(400, 'El usuario debe tener al menos 3 caracteres'); return
            if new_password and len(new_password) < 4:
                self._json_err(400, 'La nueva contraseña debe tener al menos 4 caracteres'); return

            with get_db() as conn:
                if new_username and new_username != user['username']:
                    if conn.execute("SELECT id FROM usuarios WHERE username=? AND id!=?", (new_username, uid)).fetchone():
                        self._json_err(409, 'Ese nombre de usuario ya existe'); return
                if new_email and new_email != (user['email'] or ''):
                    if conn.execute("SELECT id FROM usuarios WHERE email=? AND id!=?", (new_email, uid)).fetchone():
                        self._json_err(409, 'Ese correo ya está registrado'); return

                # Aplicar cambios
                final_username = new_username or user['username']
                final_email    = new_email    if new_email    else (user['email'] or '')
                if new_password:
                    salt = secrets.token_hex(16)
                    conn.execute("UPDATE usuarios SET username=?, email=?, password=?, salt=? WHERE id=?",
                                 (final_username, final_email, hash_password(new_password, salt), salt, uid))
                else:
                    conn.execute("UPDATE usuarios SET username=?, email=? WHERE id=?",
                                 (final_username, final_email, uid))

            # Actualizar sesión en memoria
            for s in sessions.values():
                if s['user_id'] == uid:
                    s['username'] = final_username

            print(f'✏️  Usuario id={uid} actualizó su perfil → {final_username}')
            self._json_ok({'ok': True, 'user': final_username, 'is_admin': _is_admin(uid)}); return

        if path == '/eventos': save_eventos(uid, data); self._json_ok({'ok': True}); return
        if path == '/tareas':  save_tareas(uid, data);  self._json_ok({'ok': True}); return
        if path == '/notas':   save_notas(uid, data);   self._json_ok({'ok': True}); return
        if path == '/manuales': save_manuales(uid, data); self._json_ok({'ok': True}); return

        self.send_response(404); self.end_headers()

    def _read_body(self):
        return self.rfile.read(int(self.headers.get('Content-Length', 0)))

    def _serve_file(self, filename, content_type):
        fpath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(fpath):
            self.send_response(404); self.end_headers(); return
        with open(fpath, 'rb') as f: content = f.read()
        self.send_response(200)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.end_headers(); self.wfile.write(content)

    def _json_ok(self, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200); self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(raw))
        self.end_headers(); self.wfile.write(raw)

    def _json_err(self, code, msg):
        raw = json.dumps({'ok': False, 'error': msg}).encode()
        self.send_response(code); self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(raw))
        self.end_headers(); self.wfile.write(raw)

    def log_message(self, fmt, *args):
        pass


init_db()
n = count_users()
print(f'✅ Servidor multi-usuario corriendo en http://0.0.0.0:{PORT}')
print(f'👥 Usuarios registrados: {n}{"  ← crea el primero en /register" if n == 0 else ""}')
print(f'🗄️  Base de datos: {DB_FILE}')
print('📅 Calendario — Ctrl+C para apagar')

httpd = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
httpd.serve_forever()
