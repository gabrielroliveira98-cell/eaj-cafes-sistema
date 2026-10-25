"""Controle de estoque.

Regra da EAJ:
  * Pedido registrado  -> RESERVA (o item sai do "disponível", mas continua no estoque físico).
  * Pagamento confirmado -> BAIXA definitiva (sai do físico e da reserva).
  * Cancelado antes do pagamento -> LIBERAÇÃO da reserva.
  * Cancelado depois do pagamento -> ESTORNO da baixa (volta ao físico).

Disponível = físico − reservado. Cada tipo de movimentação ligada a um pedido só pode
acontecer uma vez por produto (índice único no banco), então reenviar uma atualização
de status nunca duplica a baixa.
"""
from ..db import now
from . import BusinessError

MANUAL_KINDS = {
    'entrada':        ('Entrada de mercadoria', +1),
    'ajuste_entrada': ('Ajuste de inventário (entrada)', +1),
    'ajuste_saida':   ('Ajuste de inventário (saída)', -1),
    'perda':          ('Perda ou avaria', -1),
}
KIND_LABELS = {**{k: v[0] for k, v in MANUAL_KINDS.items()},
               'reserva': 'Reserva de pedido', 'liberacao': 'Reserva liberada',
               'baixa_venda': 'Baixa por venda', 'estorno_venda': 'Estorno de venda'}


def available(product):
    return product['stock_on_hand'] - product['stock_reserved']


def _apply(db, product_id, kind, d_on, d_res, order_id=None, note='', unit_cost=None, user_id=None):
    p = db.execute('SELECT name, stock_on_hand, stock_reserved FROM products WHERE id = ?', (product_id,)).fetchone()
    if p is None:
        raise BusinessError('Produto não encontrado.')
    on, res = p['stock_on_hand'] + d_on, p['stock_reserved'] + d_res
    if on < 0:
        raise BusinessError(f'Estoque insuficiente de {p["name"]}: há {p["stock_on_hand"]} unidade(s) em estoque.')
    if res < 0 or res > on:
        raise BusinessError(f'Estoque disponível insuficiente de {p["name"]}.')
    t = now()
    db.execute('UPDATE products SET stock_on_hand = ?, stock_reserved = ?, updated_at = ? WHERE id = ?', (on, res, t, product_id))
    db.execute('INSERT INTO stock_movements (product_id, kind, qty_on_hand_delta, qty_reserved_delta, on_hand_after, reserved_after, '
               'unit_cost_cents, order_id, note, user_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (product_id, kind, d_on, d_res, on, res, unit_cost, order_id, note, user_id, t))


def manual_movement(db, product_id, kind, quantity, note='', unit_cost=None, user_id=None):
    if kind not in MANUAL_KINDS:
        raise BusinessError('Tipo de movimentação inválido.')
    if not isinstance(quantity, int) or quantity <= 0:
        raise BusinessError('A quantidade precisa ser um número inteiro maior que zero.')
    sign = MANUAL_KINDS[kind][1]
    if sign < 0:
        p = db.execute('SELECT stock_on_hand, stock_reserved FROM products WHERE id = ?', (product_id,)).fetchone()
        if p and quantity > available(p):
            raise BusinessError(f'Só há {available(p)} unidade(s) disponíveis (as demais estão reservadas para pedidos).')
    _apply(db, product_id, kind, sign * quantity, 0, note=note, unit_cost=unit_cost, user_id=user_id)


def order_state(db, order_id):
    kinds = {r['kind'] for r in db.execute('SELECT DISTINCT kind FROM stock_movements WHERE order_id = ?', (order_id,))}
    return {k: k in kinds for k in ('reserva', 'liberacao', 'baixa_venda', 'estorno_venda')}


def _items(db, order_id):
    return db.execute('SELECT product_id, quantity FROM order_items WHERE order_id = ?', (order_id,)).fetchall()


def reserve_order(db, order_id, user_id=None):
    if order_state(db, order_id)['reserva']:
        return False
    for it in _items(db, order_id):
        _apply(db, it['product_id'], 'reserva', 0, it['quantity'], order_id=order_id, note='Pedido registrado', user_id=user_id)
    return True


def deduct_order(db, order_id, user_id=None):
    st = order_state(db, order_id)
    if st['baixa_venda']:
        return False
    if not st['reserva'] or st['liberacao']:
        raise BusinessError('O pedido não tem reserva de estoque ativa.')
    for it in _items(db, order_id):
        _apply(db, it['product_id'], 'baixa_venda', -it['quantity'], -it['quantity'], order_id=order_id,
               note='Pagamento confirmado', user_id=user_id)
    return True


def release_order(db, order_id, user_id=None):
    st = order_state(db, order_id)
    if not st['reserva'] or st['liberacao'] or st['baixa_venda']:
        return False
    for it in _items(db, order_id):
        _apply(db, it['product_id'], 'liberacao', 0, -it['quantity'], order_id=order_id, note='Pedido cancelado', user_id=user_id)
    return True


def restore_order(db, order_id, user_id=None):
    st = order_state(db, order_id)
    if not st['baixa_venda'] or st['estorno_venda']:
        return False
    for it in _items(db, order_id):
        _apply(db, it['product_id'], 'estorno_venda', it['quantity'], 0, order_id=order_id,
               note='Pedido cancelado após pagamento', user_id=user_id)
    return True
