"""Autenticação, autorização, CSRF e limite de tentativas de login."""
import hmac
import secrets
import time
from collections import defaultdict
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, now

# Permissões internas. O perfil 'admin' tem todas; os demais recebem as que o administrador conceder.
PERMISSIONS = {
    'pedidos':    'Pedidos e pagamentos',
    'produtos':   'Produtos e preços',
    'estoque':    'Estoque',
    'financeiro': 'Financeiro',
    'socios':     'Sócios e distribuições',
    'frete':      'Formas de entrega',
    'bi':         'Painel de análise (BI)',
    'usuarios':   'Usuários e permissões',
    'auditoria':  'Histórico de alterações',
}
ROLES = {'admin': 'Administrador', 'analista': 'Analista', 'cliente': 'Cliente'}
DEFAULT_PERMS = {'admin': list(PERMISSIONS), 'analista': ['bi'], 'cliente': []}
MIN_PASSWORD = 10


def hash_password(pw):
    return generate_password_hash(pw)


def password_problem(pw):
    if len(pw or '') < MIN_PASSWORD:
        return f'A senha precisa ter pelo menos {MIN_PASSWORD} caracteres.'
    if pw.isdigit() or pw.isalpha():
        return 'Use letras e números na senha.'
    return None


# ---------- sessão ----------
def load_user():
    g.user = None
    uid = session.get('uid')
    if uid:
        row = get_db().execute('SELECT * FROM users WHERE id = ? AND active = 1', (uid,)).fetchone()
        if row is None or session.get('pwv') != row['password_hash'][-12:]:
            session.clear()           # usuário desativado ou senha trocada
        else:
            g.user = row


def login_user(user):
    session.clear()
    session.permanent = True
    session['uid'] = user['id']
    session['pwv'] = user['password_hash'][-12:]
    session['csrf'] = secrets.token_urlsafe(32)
    get_db().execute('UPDATE users SET last_login_at = ? WHERE id = ?', (now(), user['id']))


def logout_user():
    session.clear()


def user_perms(user):
    if user is None:
        return set()
    if user['role'] == 'admin':
        return set(PERMISSIONS)
    return {p for p in (user['permissions'] or '').split(',') if p in PERMISSIONS}


def has_perm(perm):
    return perm in user_perms(g.get('user'))


def is_staff(user=None):
    user = user if user is not None else g.get('user')
    return bool(user) and user['role'] in ('admin', 'analista')


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.get('user') is None:
            return redirect(url_for('account.login', next=request.full_path))
        return view(*a, **kw)
    return wrapped


def staff_required(perm=None):
    """Exige usuário interno (admin/analista) e, se informado, uma permissão."""
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            user = g.get('user')
            if user is None:
                return redirect(url_for('account.login', next=request.full_path))
            if not is_staff(user):
                abort(403)
            if perm and perm not in user_perms(user):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def first_allowed_endpoint(user):
    order = [('pedidos', 'admin.dashboard'), ('produtos', 'admin.dashboard'), ('estoque', 'admin.dashboard'),
             ('financeiro', 'admin.dashboard'), ('socios', 'admin.dashboard'), ('bi', 'bi.dashboard'),
             ('usuarios', 'admin.users'), ('auditoria', 'admin.audit'), ('frete', 'admin.shipping')]
    perms = user_perms(user)
    for p, ep in order:
        if p in perms:
            return ep
    return None


# ---------- CSRF ----------
def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def check_csrf():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    sent = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
    expected = session.get('csrf', '')
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description='Sessão expirada ou formulário inválido. Recarregue a página e tente de novo.')


# ---------- limite de tentativas ----------
_attempts = defaultdict(list)
MAX_ATTEMPTS, WINDOW = 8, 15 * 60


def login_blocked(key):
    t = time.time()
    _attempts[key] = [x for x in _attempts[key] if t - x < WINDOW]
    return len(_attempts[key]) >= MAX_ATTEMPTS


def register_failure(key):
    _attempts[key].append(time.time())


def clear_failures(key):
    _attempts.pop(key, None)


def authenticate(email, password):
    row = get_db().execute('SELECT * FROM users WHERE email = ? AND active = 1', ((email or '').strip(),)).fetchone()
    if row and check_password_hash(row['password_hash'], password or ''):
        return row
    if row is None:
        check_password_hash(hash_password('x'), password or '')   # tempo de resposta parecido
    return None


def safe_next(target, default):
    """Só permite redirecionar para caminhos internos."""
    if target and target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return default


def require(cond, message):
    if not cond:
        flash(message, 'error')
    return cond
