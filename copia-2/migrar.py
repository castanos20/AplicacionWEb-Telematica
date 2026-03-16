"""
Migración: importa eventos.json, tareas.json y notas.json al usuario 'jose' en calendario.db
Ejecutar desde la carpeta donde están los archivos:
    python3 migrar.py
"""
import sqlite3, json, os, sys

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_FILE    = os.path.join(BASE_DIR, 'calendario.db')
USERNAME   = 'jose'

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def load_json(filename, default):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        print(f'  ⚠️  No se encontró {filename}, se omite.')
        return default
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

# ── Buscar el user_id de 'jose' ──────────────────────────────────────
with get_db() as conn:
    row = conn.execute("SELECT id FROM usuarios WHERE username = ?", (USERNAME,)).fetchone()

if not row:
    print(f'❌  El usuario "{USERNAME}" no existe en la base de datos.')
    print('    Regístrate primero en localhost:8989 y vuelve a ejecutar este script.')
    sys.exit(1)

user_id = row['id']
print(f'✅  Usuario "{USERNAME}" encontrado (id={user_id})')

# ── Migrar eventos ───────────────────────────────────────────────────
eventos = load_json('eventos.json', [])
if eventos:
    with get_db() as conn:
        conn.execute("DELETE FROM eventos WHERE user_id = ?", (user_id,))
        for ev in eventos:
            conn.execute("""
                INSERT OR REPLACE INTO eventos
                    (id, user_id, name, location, recurring, date, day_of_week, start, end, color)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ev.get('id'),
                user_id,
                ev.get('name', ''),
                ev.get('location', ''),
                1 if ev.get('recurring') else 0,
                ev.get('date'),
                ev.get('dayOfWeek'),
                ev.get('start', ''),
                ev.get('end', ''),
                ev.get('color', 'orange'),
            ))
    print(f'📅  {len(eventos)} eventos migrados.')

# ── Migrar tareas ────────────────────────────────────────────────────
tareas = load_json('tareas.json', [])
if tareas:
    with get_db() as conn:
        conn.execute("DELETE FROM tareas WHERE user_id = ?", (user_id,))
        for t in tareas:
            conn.execute("""
                INSERT OR REPLACE INTO tareas
                    (id, user_id, title, description, status, priority, date, hour, event_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.get('id'),
                user_id,
                t.get('title', ''),
                t.get('description', ''),
                t.get('status', 'pending'),
                t.get('priority', 'medium'),
                t.get('date'),
                t.get('hour'),
                t.get('eventId'),
                t.get('createdAt'),
            ))
    print(f'✅  {len(tareas)} tareas migradas.')

# ── Migrar notas ─────────────────────────────────────────────────────
notas = load_json('notas.json', {})
if notas:
    with get_db() as conn:
        conn.execute("DELETE FROM notas WHERE user_id = ?", (user_id,))
        for key, content in notas.items():
            if content and content.strip():
                conn.execute(
                    "INSERT OR REPLACE INTO notas (user_id, key, content) VALUES (?, ?, ?)",
                    (user_id, key, content)
                )
    print(f'📝  {len(notas)} notas migradas.')

print('\n🎉  Migración completada. Ya puedes iniciar sesión como "jose".')
