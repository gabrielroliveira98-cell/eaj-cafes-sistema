from ..db import get_db

# Valores padrão. Nenhum deles é um dado comercial inventado: os campos de contato ficam vazios
# até a empresa preencher, e a reserva começa em 0% até ser definida.
DEFAULTS = {
    'store_whatsapp': '',
    'store_email': '',
    'payment_instructions': '',
    'reserve_bp': '0',
}


def get(key):
    row = get_db().execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else DEFAULTS.get(key, '')


def all_settings():
    out = dict(DEFAULTS)
    out.update({r['key']: r['value'] for r in get_db().execute('SELECT key, value FROM settings')})
    return out


def put(key, value):
    get_db().execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                     (key, str(value)))
