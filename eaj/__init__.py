"""EAJ Cafés Especiais — loja, gestão e análise de dados em uma única aplicação."""
import os
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

from . import db as dbmod
from .config import Config
from .money import fmt_bp, fmt_bp_input, fmt_brl, fmt_input
from .security import PERMISSIONS, ROLES, check_csrf, csrf_token, has_perm, is_staff, load_user


def create_app(overrides=None):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    if not app.config['SECRET_KEY']:
        raise RuntimeError('SECRET_KEY não definida. Rode "python manage.py setup" para criar o arquivo .env.')

    app.teardown_appcontext(dbmod.close_db)

    from .blueprints import account, admin, bi, shop
    app.register_blueprint(shop.bp)
    app.register_blueprint(account.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(bi.bp)

    @app.before_request
    def _before():
        load_user()
        check_csrf()

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        if request.path.startswith(('/admin', '/bi', '/conta')):
            resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.context_processor
    def _ctx():
        return dict(csrf_token=csrf_token, has_perm=has_perm, is_staff=is_staff,
                    PERMISSIONS=PERMISSIONS, ROLES=ROLES, live_reload=app.config['LIVE_RELOAD'],
                    current_user=g.get('user'))

    app.jinja_env.filters.update(brl=fmt_brl, money_input=fmt_input, pct=fmt_bp, pct_input=fmt_bp_input,
                                 date_br=_date_br, datetime_br=_datetime_br, grams=_grams)

    for code in (400, 403, 404, 405, 413, 500):
        app.register_error_handler(code, _error_handler(code))

    if app.config['LIVE_RELOAD']:
        _register_live_reload(app)
    return app


def _date_br(s):
    if not s:
        return '—'
    s = str(s)
    return f'{s[8:10]}/{s[5:7]}/{s[0:4]}'


def _datetime_br(s):
    if not s:
        return '—'
    s = str(s)
    return f'{s[8:10]}/{s[5:7]}/{s[0:4]} {s[11:16]}'


def _grams(g_):
    return '1 kg' if g_ == 1000 else (f'{g_ / 1000:g} kg'.replace('.', ',') if g_ >= 1000 else f'{g_} g')


ERROR_TITLES = {400: 'Requisição inválida', 403: 'Acesso não permitido', 404: 'Página não encontrada',
                405: 'Ação não permitida', 413: 'Arquivo grande demais', 500: 'Erro inesperado'}


def _error_handler(code):
    def handler(err):
        desc = getattr(err, 'description', '') if code != 500 else 'Algo deu errado do nosso lado. Tente novamente em instantes.'
        if request.path.startswith('/api/') or request.path.startswith('/bi/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify(error=ERROR_TITLES[code], detail=desc), code
        return render_template('error.html', code=code, title=ERROR_TITLES[code], detail=desc), code
    return handler


def _register_live_reload(app):
    root = Path(app.root_path)

    @app.get('/__dev/stamp')
    def _stamp():
        latest = 0.0
        for dirpath, _dirs, files in os.walk(root):
            if 'uploads' in dirpath or '__pycache__' in dirpath:
                continue
            for f in files:
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(dirpath, f)))
                except OSError:
                    pass
        return str(latest)
