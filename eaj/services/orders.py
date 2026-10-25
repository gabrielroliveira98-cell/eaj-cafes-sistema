"""Pedidos, pagamentos e ciclo de status.

Fluxo:
  aguardando_pagamento --(pagamento confirmado pelo admin)--> pago --> em_preparo --> enviado --> entregue
  qualquer status antes de "enviado" --> cancelado

Um pedido só vira "pago" quando a soma dos pagamentos confirmados cobre o total.
Não existe confirmação automática sem uma integração de pagamento real.
"""
import re
import secrets

from ..db import now, transaction
from . import BusinessError, audit, inventory

STATUS = {
    'aguardando_pagamento': 'Aguardando pagamento',
    'pago':                 'Pago',
    'em_preparo':           'Em preparo',
    'enviado':              'Enviado',
    'entregue':             'Entregue',
    'cancelado':            'Cancelado',
}
# Transições manuais. "pago" não aparece aqui: só é alcançado registrando pagamento.
TRANSITIONS = {
    'aguardando_pagamento': ['cancelado'],
    'pago':                 ['em_preparo', 'cancelado'],
    'em_preparo':           ['enviado', 'cancelado'],
    'enviado':              ['entregue'],
    'entregue':             [],
    'cancelado':            [],
}
REVENUE_STATUSES = ('pago', 'em_preparo', 'enviado', 'entregue')     # contam como venda
OPEN_STATUSES = ('aguardando_pagamento', 'pago', 'em_preparo', 'enviado')
PAYMENT_METHODS = {'pix': 'Pix', 'dinheiro': 'Dinheiro', 'transferencia': 'Transferência bancária',
                   'cartao': 'Cartão (maquininha)', 'outro': 'Outro'}
MAX_QTY = 50
UFS = ('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO').split()


def quote(db, raw_items):
    """Recalcula o carrinho no servidor. O navegador só informa produto e quantidade;
    preço, disponibilidade e totais vêm sempre do banco."""
    merged = {}
    for it in raw_items or []:
        try:
            pid, q = int(it.get('product_id')), int(it.get('quantity'))
        except (TypeError, ValueError, AttributeError):
            continue
        if q > 0:
            merged[pid] = merged.get(pid, 0) + q
    lines, problems, subtotal = [], [], 0
    for pid, q in merged.items():
        p = db.execute('SELECT * FROM products WHERE id = ?', (pid,)).fetchone()
        if p is None or not p['active']:
            problems.append('Um dos produtos do carrinho não está mais disponível e foi removido.')
            continue
        avail = inventory.available(p)
        if q > MAX_QTY:
            problems.append(f'O limite por pedido é de {MAX_QTY} unidades de cada produto.')
            q = MAX_QTY
        if avail <= 0:
            problems.append(f'{p["name"]} está esgotado no momento.')
            continue
        if q > avail:
            problems.append(f'Só há {avail} unidade(s) de {p["name"]} disponíveis. A quantidade foi ajustada.')
            q = avail
        line = q * p['price_cents']
        subtotal += line
        lines.append({'product_id': pid, 'name': p['name'], 'slug': p['slug'], 'image': p['image'],
                      'weight_grams': p['weight_grams'], 'quantity': q, 'available': avail,
                      'unit_price_cents': p['price_cents'], 'unit_cost_cents': p['unit_cost_cents'],
                      'line_total_cents': line})
    return {'lines': lines, 'subtotal_cents': subtotal, 'problems': problems}


def validate_customer(form):
    f = {k: (form.get(k) or '').strip() for k in ('name', 'email', 'phone', 'zip', 'street', 'number', 'complement',
                                                   'district', 'city', 'state', 'notes')}
    errors = {}
    if len(f['name']) < 3:
        errors['name'] = 'Informe seu nome completo.'
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', f['email']):
        errors['email'] = 'Informe um e-mail válido.'
    digits = re.sub(r'\D', '', f['phone'])
    if not 10 <= len(digits) <= 13:
        errors['phone'] = 'Informe um telefone com DDD.'
    zip_ = re.sub(r'\D', '', f['zip'])
    if len(zip_) != 8:
        errors['zip'] = 'Informe o CEP com 8 dígitos.'
    for k, msg in (('street', 'Informe a rua.'), ('number', 'Informe o número (ou "s/n").'),
                   ('district', 'Informe o bairro.'), ('city', 'Informe a cidade.')):
        if not f[k]:
            errors[k] = msg
    f['state'] = f['state'].upper()
    if f['state'] not in UFS:
        errors['state'] = 'Escolha o estado.'
    if not form.get('consent'):
        errors['consent'] = 'É preciso autorizar o uso dos dados para a entrega.'
    for k, n in (('name', 120), ('email', 160), ('street', 160), ('complement', 120), ('district', 120),
                 ('city', 120), ('notes', 600)):
        f[k] = f[k][:n]
    f['zip'] = f'{zip_[:5]}-{zip_[5:]}' if len(zip_) == 8 else zip_
    f['phone'] = f['phone'][:30]
    return f, errors


def create_order(db, raw_items, customer, shipping_method_id, user=None):
    with transaction(db):
        q = quote(db, raw_items)
        if q['problems']:
            raise BusinessError(' '.join(dict.fromkeys(q['problems'])) + ' Revise o carrinho e confirme novamente.')
        if not q['lines']:
            raise BusinessError('O carrinho está vazio.')
        ship = db.execute('SELECT * FROM shipping_methods WHERE id = ? AND active = 1', (shipping_method_id,)).fetchone()
        if ship is None:
            raise BusinessError('Escolha uma forma de entrega.')
        to_arrange = ship['price_cents'] is None
        shipping = 0 if to_arrange else ship['price_cents']
        t = now()
        cust = None
        if user is not None:
            cust = db.execute('SELECT * FROM customers WHERE user_id = ?', (user['id'],)).fetchone()
        if cust is None:
            cur = db.execute('INSERT INTO customers (user_id, name, email, phone, created_at) VALUES (?,?,?,?,?)',
                             (user['id'] if user is not None else None, customer['name'], customer['email'], customer['phone'], t))
            cust_id = cur.lastrowid
        else:
            cust_id = cust['id']
            db.execute('UPDATE customers SET name = ?, phone = ? WHERE id = ?', (customer['name'], customer['phone'], cust_id))
        cur = db.execute(
            'INSERT INTO orders (code, public_token, customer_id, status, subtotal_cents, shipping_cents, total_cents, '
            'shipping_method_id, shipping_label, shipping_to_arrange, customer_name, customer_email, customer_phone, '
            'ship_zip, ship_street, ship_number, ship_complement, ship_district, ship_city, ship_state, notes, created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('TMP-' + secrets.token_hex(8), secrets.token_urlsafe(24), cust_id, 'aguardando_pagamento', q['subtotal_cents'],
             shipping, q['subtotal_cents'] + shipping, ship['id'], ship['name'], 1 if to_arrange else 0,
             customer['name'], customer['email'], customer['phone'], customer['zip'], customer['street'], customer['number'],
             customer['complement'], customer['district'], customer['city'], customer['state'], customer['notes'], t))
        oid = cur.lastrowid
        code = f'EAJ-{t[:4]}-{oid:05d}'
        db.execute('UPDATE orders SET code = ? WHERE id = ?', (code, oid))
        for ln in q['lines']:
            db.execute('INSERT INTO order_items (order_id, product_id, product_name, weight_grams, quantity, unit_price_cents, '
                       'unit_cost_cents, line_total_cents) VALUES (?,?,?,?,?,?,?,?)',
                       (oid, ln['product_id'], ln['name'], ln['weight_grams'], ln['quantity'], ln['unit_price_cents'],
                        ln['unit_cost_cents'], ln['line_total_cents']))
        inventory.reserve_order(db, oid)
        db.execute('INSERT INTO order_events (order_id, from_status, to_status, note, user_id, created_at) VALUES (?,?,?,?,?,?)',
                   (oid, None, 'aguardando_pagamento', 'Pedido registrado pela loja', user['id'] if user is not None else None, t))
    return db.execute('SELECT * FROM orders WHERE id = ?', (oid,)).fetchone()


def get(db, order_id):
    return db.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()


def items(db, order_id):
    return db.execute('SELECT * FROM order_items WHERE order_id = ? ORDER BY id', (order_id,)).fetchall()


def paid_amount(db, order_id):
    return db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE order_id = ? AND status = 'confirmado'",
                      (order_id,)).fetchone()[0]


def _event(db, order_id, frm, to, note, user_id):
    db.execute('INSERT INTO order_events (order_id, from_status, to_status, note, user_id, created_at) VALUES (?,?,?,?,?,?)',
               (order_id, frm, to, note, user_id, now()))


def change_status(db, order_id, to, user_id, note='', refund_confirmed=False):
    with transaction(db):
        o = get(db, order_id)
        if o is None:
            raise BusinessError('Pedido não encontrado.')
        if to == o['status']:
            return o                                    # reenvio do mesmo status: nada acontece
        if to not in TRANSITIONS.get(o['status'], []):
            raise BusinessError(f'Não é possível mudar de "{STATUS[o["status"]]}" para "{STATUS.get(to, to)}".')
        t = now()
        if to == 'cancelado':
            paid = paid_amount(db, order_id)
            if paid > 0 and not refund_confirmed:
                raise BusinessError('Este pedido tem pagamento confirmado. Confirme que o reembolso foi feito ou combinado com o cliente antes de cancelar.')
            if not note.strip():
                raise BusinessError('Informe o motivo do cancelamento.')
            if inventory.order_state(db, order_id)['baixa_venda']:
                inventory.restore_order(db, order_id, user_id)
            else:
                inventory.release_order(db, order_id, user_id)
            if paid > 0:
                db.execute("UPDATE payments SET status = 'estornado', note = TRIM(note || ' Estornado no cancelamento.') "
                           "WHERE order_id = ? AND status = 'confirmado'", (order_id,))
            db.execute('UPDATE orders SET status = ?, cancelled_at = ?, cancel_reason = ? WHERE id = ?', (to, t, note.strip()[:300], order_id))
        else:
            col = {'enviado': 'shipped_at', 'entregue': 'delivered_at'}.get(to)
            if col:
                db.execute(f'UPDATE orders SET status = ?, {col} = ? WHERE id = ?', (to, t, order_id))
            else:
                db.execute('UPDATE orders SET status = ? WHERE id = ?', (to, order_id))
        _event(db, order_id, o['status'], to, note.strip(), user_id)
        audit.log('status_pedido', 'order', order_id, user_id=user_id, de=o['status'], para=to, nota=note.strip())
    return get(db, order_id)


def register_payment(db, order_id, method, amount_cents, paid_at, reference, note, user_id):
    if method not in PAYMENT_METHODS:
        raise BusinessError('Escolha a forma de pagamento.')
    if amount_cents <= 0:
        raise BusinessError('Informe o valor recebido.')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', paid_at or ''):
        raise BusinessError('Informe a data do pagamento.')
    with transaction(db):
        o = get(db, order_id)
        if o is None:
            raise BusinessError('Pedido não encontrado.')
        if o['status'] != 'aguardando_pagamento':
            raise BusinessError('Só é possível registrar pagamento em pedidos aguardando pagamento.')
        if o['shipping_to_arrange']:
            raise BusinessError('Defina o valor do frete antes de registrar o pagamento.')
        remaining = o['total_cents'] - paid_amount(db, order_id)
        if amount_cents > remaining:
            raise BusinessError(f'O valor é maior que o saldo em aberto do pedido ({remaining / 100:.2f}).'.replace('.', ','))
        t = now()
        stamp = f'{paid_at} {t[11:]}'
        db.execute('INSERT INTO payments (order_id, method, amount_cents, status, reference, paid_at, note, recorded_by, created_at) '
                   "VALUES (?,?,?,'confirmado',?,?,?,?,?)", (order_id, method, amount_cents, reference.strip()[:120], stamp,
                                                             note.strip()[:300], user_id, t))
        audit.log('registrar_pagamento', 'order', order_id, user_id=user_id, valor=amount_cents, forma=method, referencia=reference)
        if amount_cents == remaining:
            inventory.deduct_order(db, order_id, user_id)
            db.execute("UPDATE orders SET status = 'pago', paid_at = ? WHERE id = ?", (stamp, order_id))
            _event(db, order_id, 'aguardando_pagamento', 'pago', f'Pagamento confirmado ({PAYMENT_METHODS[method]})', user_id)
        else:
            _event(db, order_id, 'aguardando_pagamento', 'aguardando_pagamento',
                   f'Pagamento parcial de R$ {amount_cents / 100:.2f}'.replace('.', ','), user_id)
    return get(db, order_id)


def set_shipping(db, order_id, shipping_cents, user_id):
    with transaction(db):
        o = get(db, order_id)
        if o is None:
            raise BusinessError('Pedido não encontrado.')
        if o['status'] != 'aguardando_pagamento':
            raise BusinessError('O frete só pode ser alterado antes do pagamento.')
        if paid_amount(db, order_id):
            raise BusinessError('Já existe pagamento registrado neste pedido.')
        db.execute('UPDATE orders SET shipping_cents = ?, total_cents = subtotal_cents + ?, shipping_to_arrange = 0 WHERE id = ?',
                   (shipping_cents, shipping_cents, order_id))
        _event(db, order_id, o['status'], o['status'], f'Frete definido: R$ {shipping_cents / 100:.2f}'.replace('.', ','), user_id)
        audit.log('definir_frete', 'order', order_id, user_id=user_id, antes=o['shipping_cents'], depois=shipping_cents)


def lookup(db, code, email):
    return db.execute('SELECT * FROM orders WHERE code = ? AND customer_email = ? COLLATE NOCASE',
                      ((code or '').strip().upper(), (email or '').strip())).fetchone()
