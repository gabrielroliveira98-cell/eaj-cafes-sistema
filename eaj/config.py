import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def _bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ('1', 'true', 'sim', 'yes', 'on')


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    DATABASE_PATH = str(BASE_DIR / os.environ.get('DATABASE_PATH', 'instance/eaj.sqlite3'))
    FRAMES_SOURCE = str(BASE_DIR / os.environ.get('FRAMES_SOURCE', 'FRAMES VIDEO'))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = _bool('SESSION_COOKIE_SECURE')
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8           # 8 horas

    MAX_CONTENT_LENGTH = int(float(os.environ.get('UPLOAD_MAX_MB', '4')) * 1024 * 1024)
    UPLOAD_DIR = str(BASE_DIR / 'eaj' / 'static' / 'uploads')

    # Integração de pagamento: vazio = nenhuma integração configurada.
    PAYMENT_PROVIDER = os.environ.get('PAYMENT_PROVIDER', '').strip()

    # Recarrega o navegador quando arquivos mudam (somente em desenvolvimento).
    LIVE_RELOAD = _bool('LIVE_RELOAD')

    TESTING = False
