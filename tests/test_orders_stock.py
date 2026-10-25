from eaj.services import BusinessError, catalog, inventory, orders

from .base import AppTestCase


class OrderAndStockTests(AppTestCase):
    def test_seed_has_only_official_products(self):
        rows = self.db.execute('SELECT name, price_cents, weight_grams FROM products ORDER BY sort_order').fetchall()
        self.assertEqual([(r['name'], r['price_cents'], r['weight_grams']) for r in rows],
                         [('EAJ Café Especial | 500 g', 3000, 500), ('EAJ Café Especial | 1 kg', 6000, 1000)])
        partners = self.db.execute('SELECT name, share_bp FROM partners').fetchall()
        self.assertEqual([(p['name'], p['share_bp']) for p in partners], [('Família', 5000), ('Parceiro investidor', 5000)])

    def test_quote_uses_server_prices_and_caps_stock(self):
        self.stock_in(self.p500, 3)
        self.stock_in(self.p1k, 10)
        q = orders.quote(self.db, [{'product_id': self.p500, 'quantity': 5, 'unit_price_cents': 1},
                                   {'product_id': self.p1k, 'quantity': 2}, {'product_id': self.p1k, 'quantity': 1}])
        lines = {l['product_id']: l for l in q['lines']}
        self.assertEqual(lines[self.p500]['quantity'], 3)                   # limitado ao estoque
        self.assertEqual(lines[self.p500]['unit_price_cents'], 3000)        # preço do banco, não do navegador
        self.assertEqual(lines[self.p1k]['quantity'], 3)                    # itens repetidos somados
        self.assertEqual(q['subtotal_cents'], 3 * 3000 + 3 * 6000)
        self.assertTrue(q['problems'])

    def test_order_totals_and_reservation(self):
        self.stock_in(self.p500, 10)
        self.stock_in(self.p1k, 5)
        o = self.place([{'product_id': self.p500, 'quantity': 2}, {'product_id': self.p1k, 'quantity': 1}])
        self.assertEqual(o['status'], 'aguardando_pagamento')
        self.assertEqual(o['subtotal_cents'], 12000)
        self.assertEqual(o['total_cents'], 12000)
        self.assertTrue(o['shipping_to_arrange'])
        self.assertRegex(o['code'], r'^EAJ-\d{4}-\d{5}$')
        p = self.product(self.p500)
        self.assertEqual((p['stock_on_hand'], p['stock_reserved']), (10, 2))   # reservado, não baixado
        self.assertEqual(inventory.available(p), 8)

    def test_cannot_sell_above_stock(self):
        self.stock_in(self.p500, 2)
        self.place([{'product_id': self.p500, 'quantity': 2}])
        with self.assertRaises(BusinessError):
            self.place([{'product_id': self.p500, 'quantity': 1}])           # tudo reservado
        with self.assertRaises(BusinessError):
            self.place([{'product_id': self.p1k, 'quantity': 1}])           # sem estoque

    def test_payment_deducts_stock_once(self):
        self.stock_in(self.p500, 5)
        o = self.place([{'product_id': self.p500, 'quantity': 2}])
        o = self.pay(o)
        self.assertEqual(o['status'], 'pago')
        self.assertIsNotNone(o['paid_at'])
        p = self.product(self.p500)
        self.assertEqual((p['stock_on_hand'], p['stock_reserved']), (3, 0))
        # repetir operações não duplica a baixa
        self.assertFalse(inventory.deduct_order(self.db, o['id']))
        orders.change_status(self.db, o['id'], 'pago', self.admin)
        with self.assertRaises(BusinessError):
            orders.register_payment(self.db, o['id'], 'pix', 100, '2026-01-01', '', '', self.admin)
        p = self.product(self.p500)
        self.assertEqual((p['stock_on_hand'], p['stock_reserved']), (3, 0))
        kinds = [r['kind'] for r in self.db.execute('SELECT kind FROM stock_movements WHERE order_id = ? ORDER BY id', (o['id'],))]
        self.assertEqual(kinds, ['reserva', 'baixa_venda'])

    def test_partial_payment_keeps_pending(self):
        self.stock_in(self.p1k, 2)
        o = self.place([{'product_id': self.p1k, 'quantity': 1}])
        orders.set_shipping(self.db, o['id'], 1500, self.admin)
        o = orders.get(self.db, o['id'])
        self.assertEqual(o['total_cents'], 7500)
        o2 = orders.register_payment(self.db, o['id'], 'pix', 5000, '2026-10-01', '', '', self.admin)
        self.assertEqual(o2['status'], 'aguardando_pagamento')
        with self.assertRaises(BusinessError):
            orders.register_payment(self.db, o['id'], 'pix', 2600, '2026-10-01', '', '', self.admin)   # acima do saldo
        o3 = orders.register_payment(self.db, o['id'], 'pix', 2500, '2026-10-01', '', '', self.admin)
        self.assertEqual(o3['status'], 'pago')

    def test_payment_requires_defined_shipping(self):
        self.stock_in(self.p1k, 1)
        o = self.place([{'product_id': self.p1k, 'quantity': 1}])
        with self.assertRaises(BusinessError):
            orders.register_payment(self.db, o['id'], 'pix', o['total_cents'], '2026-10-01', '', '', self.admin)

    def test_cancel_before_payment_releases(self):
        self.stock_in(self.p500, 4)
        o = self.place([{'product_id': self.p500, 'quantity': 3}])
        with self.assertRaises(BusinessError):
            orders.change_status(self.db, o['id'], 'cancelado', self.admin, '')          # motivo obrigatório
        orders.change_status(self.db, o['id'], 'cancelado', self.admin, 'Cliente desistiu')
        p = self.product(self.p500)
        self.assertEqual((p['stock_on_hand'], p['stock_reserved']), (4, 0))
        orders.change_status(self.db, o['id'], 'cancelado', self.admin, 'de novo')       # reenvio não faz nada
        self.assertEqual(self.product(self.p500)['stock_reserved'], 0)

    def test_cancel_after_payment_restores_and_requires_refund(self):
        self.stock_in(self.p500, 4)
        o = self.pay(self.place([{'product_id': self.p500, 'quantity': 3}]))
        with self.assertRaises(BusinessError):
            orders.change_status(self.db, o['id'], 'cancelado', self.admin, 'Avaria')
        orders.change_status(self.db, o['id'], 'cancelado', self.admin, 'Avaria', refund_confirmed=True)
        p = self.product(self.p500)
        self.assertEqual((p['stock_on_hand'], p['stock_reserved']), (4, 0))
        self.assertEqual(orders.paid_amount(self.db, o['id']), 0)                         # pagamento estornado

    def test_invalid_transitions(self):
        self.stock_in(self.p500, 4)
        o = self.place([{'product_id': self.p500, 'quantity': 1}])
        with self.assertRaises(BusinessError):
            orders.change_status(self.db, o['id'], 'enviado', self.admin)                 # sem pagamento
        o = self.pay(o)
        orders.change_status(self.db, o['id'], 'em_preparo', self.admin)
        orders.change_status(self.db, o['id'], 'enviado', self.admin)
        with self.assertRaises(BusinessError):
            orders.change_status(self.db, o['id'], 'cancelado', self.admin, 'tarde demais')
        o = orders.change_status(self.db, o['id'], 'entregue', self.admin)
        self.assertEqual(o['status'], 'entregue')

    def test_price_change_keeps_order_snapshot_and_history(self):
        self.stock_in(self.p500, 5)
        o = self.place([{'product_id': self.p500, 'quantity': 1}])
        p = self.product(self.p500)
        catalog.update(self.db, self.p500, {'name': p['name'], 'weight_grams': 500, 'description': '', 'active': '1',
                                            'low_stock_threshold': 0}, 3500, 1800, self.admin, 'Reajuste')
        item = orders.items(self.db, o['id'])[0]
        self.assertEqual(item['unit_price_cents'], 3000)
        self.assertEqual(self.product(self.p500)['price_cents'], 3500)
        hist = self.db.execute('SELECT price_cents, unit_cost_cents FROM price_history WHERE product_id = ? ORDER BY id', (self.p500,)).fetchall()
        self.assertEqual([(h['price_cents'], h['unit_cost_cents']) for h in hist], [(3000, None), (3500, 1800)])
        self.assertEqual(catalog.margin(self.product(self.p500)), (1700, 4857))

    def test_manual_exit_cannot_take_reserved(self):
        self.stock_in(self.p500, 3)
        self.place([{'product_id': self.p500, 'quantity': 2}])
        with self.assertRaises(BusinessError):
            inventory.manual_movement(self.db, self.p500, 'perda', 2, 'quebra')
        inventory.manual_movement(self.db, self.p500, 'perda', 1, 'quebra')
        p = self.product(self.p500)
        self.assertEqual((p['stock_on_hand'], p['stock_reserved']), (2, 2))
