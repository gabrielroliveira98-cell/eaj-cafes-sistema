"""Loja pública. Nenhuma rota daqui expõe custos, despesas ou dados de outros clientes."""
import hmac
import json
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from ..db import get_db
from ..services import BusinessError, catalog, inventory, orders, settings

bp = Blueprint('shop', __name__)

STEPS = ['aguardando_pagamento', 'pago', 'em_preparo', 'enviado', 'entregue']


def frames_manifest():
    path = Path(current_app.static_folder) / 'frames' / 'manifest.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'count': 0, 'lg': [], 'sm': []}


def public_product(p):
    """Somente os campos que o cliente pode ver."""
    avail = inventory.available(p)
    return {'id': p['id'], 'slug': p['slug'], 'name': p['name'], 'weight_grams': p['weight_grams'],
            'description': p['description'], 'price_cents': p['price_cents'], 'image': p['image'],
            'available': avail, 'low': 0 < avail <= p['low_stock_threshold']}


@bp.get('/')
def index():
    db = get_db()
    products = [public_product(p) for p in catalog.list_products(db, active_only=True)]
    return render_template('shop/index.html', products=products, frames=frames_manifest(),
                           payment_configured=bool(current_app.config['PAYMENT_PROVIDER']))


@bp.get('/produto/<slug>')
def product(slug):
    p = catalog.by_slug(get_db(), slug)
    if p is None:
        abort(404)
    return render_template('shop/product.html', p=public_product(p))


@bp.post('/api/carrinho')
def api_cart():
    data = request.get_json(silent=True) or {}
    q = orders.quote(get_db(), data.get('items'))
    for ln in q['lines']:
        ln.pop('unit_cost_cents', None)          # custo é informação interna
    return jsonify(q)


@bp.get('/checkout')
def checkout():
    db = get_db()
    ships = db.execute('SELECT * FROM shipping_methods WHERE active = 1 ORDER BY sort_order, id').fetchall()
    prefill = {}
    if g.user is not None:
        c = db.execute('SELECT * FROM customers WHERE user_id = ?', (g.user['id'],)).fetchone()
        prefill = {'name': c['name'] if c else g.user['name'], 'email': g.user['email'], 'phone': c['phone'] if c else ''}
    return render_template('shop/checkout.html', ships=ships, form=prefill, errors={}, ufs=orders.UFS,
                           payment_configured=bool(current_app.config['PAYMENT_PROVIDER']))


@bp.post('/checkout')
def checkout_post():
    db = get_db()
    ships = db.execute('SELECT * FROM shipping_methods WHERE active = 1 ORDER BY sort_order, id').fetchall()
    customer, errors = orders.validate_customer(request.form)
    try:
        items = json.loads(request.form.get('items') or '[]')
    except ValueError:
        items = []
    if not items:
        errors['items'] = 'Seu carrinho está vazio.'
    try:
        ship_id = int(request.form.get('shipping_method') or 0)
    except ValueError:
        ship_id = 0
    if not ship_id:
        errors['shipping_method'] = 'Escolha uma forma de entrega.'
    if not errors:
        if g.user is not None and g.user['role'] == 'cliente':
            customer['email'] = g.user['email']          # pedido fica no histórico da conta
        try:
            o = orders.create_order(db, items, customer, ship_id, g.user if g.user is not None and g.user['role'] == 'cliente' else None)
        except BusinessError as e:
            errors['items'] = str(e)
        else:
            return redirect(url_for('shop.order', code=o['code'], t=o['public_token'], novo=1))
    flash('Revise os campos destacados.', 'error')
    return render_template('shop/checkout.html', ships=ships, form=request.form, errors=errors, ufs=orders.UFS,
                           payment_configured=bool(current_app.config['PAYMENT_PROVIDER'])), 422


def _can_view(o):
    token = request.args.get('t', '')
    if token and hmac.compare_digest(token, o['public_token']):
        return True
    u = g.user
    if u is not None and u['role'] in ('admin', 'analista'):
        return False                                  # equipe usa o painel, não a página do cliente
    if u is not None:
        c = get_db().execute('SELECT id FROM customers WHERE user_id = ?', (u['id'],)).fetchone()
        return c is not None and c['id'] == o['customer_id']
    return False


@bp.get('/pedido/<code>')
def order(code):
    db = get_db()
    o = db.execute('SELECT * FROM orders WHERE code = ?', (code,)).fetchone()
    if o is None or not _can_view(o):
        abort(404)
    events = db.execute("SELECT to_status, created_at FROM order_events WHERE order_id = ? AND (from_status IS NULL OR from_status <> to_status) "
                        "ORDER BY id", (o['id'],)).fetchall()
    reached = {e['to_status']: e['created_at'] for e in events}
    return render_template('shop/order.html', o=o, items=orders.items(db, o['id']), STATUS=orders.STATUS, steps=STEPS,
                           reached=reached, is_new=request.args.get('novo') == '1', store=settings.all_settings(),
                           paid=orders.paid_amount(db, o['id']),
                           payment_configured=bool(current_app.config['PAYMENT_PROVIDER']))


@bp.route('/pedido/consultar', methods=['GET', 'POST'])
def lookup():
    if request.method == 'POST':
        o = orders.lookup(get_db(), request.form.get('code'), request.form.get('email'))
        if o is not None:
            return redirect(url_for('shop.order', code=o['code'], t=o['public_token']))
        flash('Não encontramos um pedido com esse número e e-mail. Confira os dados e tente de novo.', 'error')
    return render_template('shop/lookup.html', form=request.form)
