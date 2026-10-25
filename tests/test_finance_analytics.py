from datetime import date

from eaj.services import BusinessError, analytics, finance, partners

from .base import AppTestCase

D1, D2 = date(2026, 10, 1), date(2026, 10, 31)


class FinanceAnalyticsTests(AppTestCase):
    def entry(self, cat_name, cents, comp='2026-10-10', status='realizado'):
        cid = self.db.execute('SELECT id FROM finance_categories WHERE name = ?', (cat_name,)).fetchone()['id']
        v, errors, _ = finance.validate(self.db, {'category_id': cid, 'description': cat_name, 'amount': f'{cents / 100:.2f}'.replace('.', ','),
                                                  'competence_date': comp, 'status': status,
                                                  'settled_date': comp if status == 'realizado' else ''})
        self.assertEqual(errors, {})
        return finance.create(self.db, v, self.admin)

    def sell(self, pid, qty, day='2026-10-15'):
        o = self.place([{'product_id': pid, 'quantity': qty}])
        return self.pay(o, when=day)

    def setUp(self):
        super().setUp()
        self.stock_in(self.p500, 50)
        self.stock_in(self.p1k, 50)
        self.set_cost(self.p500, 1805)
        self.set_cost(self.p1k, 3455)

    def test_finance_validation(self):
        cid = self.db.execute('SELECT id FROM finance_categories LIMIT 1').fetchone()['id']
        _, errors, _ = finance.validate(self.db, {'category_id': cid, 'description': 'x', 'amount': '0', 'competence_date': '',
                                                  'status': 'realizado', 'settled_date': ''})
        self.assertIn('description', errors)
        self.assertIn('amount', errors)
        self.assertIn('competence_date', errors)
        self.assertIn('settled_date', errors)

    def test_sales_ticket_and_cogs(self):
        self.sell(self.p500, 2)          # 60,00
        self.sell(self.p1k, 1)           # 60,00
        self.sell(self.p1k, 2)           # 120,00
        s = analytics.sales(self.db, D1, D2)
        self.assertEqual(s['gross_cents'], 24000)
        self.assertEqual(s['orders'], 3)
        self.assertEqual(s['units'], 5)
        self.assertEqual(s['avg_ticket_cents'], 8000)
        self.assertEqual(s['cogs_cents'], 2 * 1805 + 3 * 3455)

    def test_pending_orders_are_not_revenue(self):
        self.place([{'product_id': self.p500, 'quantity': 4}])
        self.assertEqual(analytics.sales(self.db, date(2000, 1, 1), date(2100, 1, 1))['gross_cents'], 0)

    def test_date_filter_uses_payment_date_inclusive(self):
        self.sell(self.p500, 1, '2026-10-01')
        self.sell(self.p500, 1, '2026-10-31')
        self.sell(self.p500, 1, '2026-11-01')
        self.assertEqual(analytics.sales(self.db, D1, D2)['orders'], 2)
        self.assertEqual(analytics.sales(self.db, D1, D1)['orders'], 1)

    def test_dre_separates_revenue_profit_and_result(self):
        self.sell(self.p1k, 10)                                      # receita 600,00 · CPV 345,50
        self.entry('Impostos', 3000)                                 # dedução
        self.entry('Marketing', 5000)                                # despesa operacional
        self.entry('Compra de café verde', 100000)                   # fora do resultado
        self.entry('Outras receitas', 1000)
        self.entry('Aluguel e despesas fixas', 2000, status='previsto')
        d = analytics.dre(self.db, D1, D2)
        self.assertEqual(d['gross_revenue'], 60000)
        self.assertEqual(d['deductions'], 3000)
        self.assertEqual(d['net_revenue'], 57000)
        self.assertEqual(d['cogs'], 34550)
        self.assertEqual(d['gross_profit'], 22450)
        self.assertEqual(d['opex'], 7000)                            # previstos entram pela competência
        self.assertEqual(d['operating_result'], 15450)
        self.assertEqual(d['net_result'], 16450)
        self.assertEqual(d['merch_purchases'], 100000)               # informado, mas não subtraído
        self.assertEqual(d['gross_margin_bp'], round(22450 * 10000 / 57000))

    def test_cancelled_entries_ignored(self):
        eid = self.entry('Marketing', 5000)
        v, _, _ = finance.validate(self.db, {'category_id': finance.get(self.db, eid)['category_id'], 'description': 'Marketing',
                                             'amount': '50', 'competence_date': '2026-10-10', 'status': 'cancelado'})
        finance.update(self.db, eid, v, self.admin)
        self.assertEqual(analytics.dre(self.db, D1, D2)['opex'], 0)

    def test_missing_cost_is_flagged(self):
        self.set_cost(self.p500, None)
        self.sell(self.p500, 3)
        d = analytics.dre(self.db, D1, D2)
        self.assertTrue(d['estimated'])
        self.assertEqual(d['sales']['units_without_cost'], 3)

    def test_product_filter_and_margin(self):
        self.sell(self.p500, 2)
        self.sell(self.p1k, 1)
        s = analytics.sales(self.db, D1, D2, product_id=self.p500)
        self.assertEqual((s['gross_cents'], s['units'], s['orders']), (6000, 2, 1))
        rows = {p['id']: p for p in analytics.by_product(self.db, D1, D2)}
        self.assertEqual(rows[self.p500]['margin_bp'], round((6000 - 3610) * 10000 / 6000))
        self.assertEqual(rows[self.p1k]['revenue'], 6000)

    def test_status_filter(self):
        o = self.sell(self.p500, 1)
        from eaj.services import orders
        orders.change_status(self.db, o['id'], 'em_preparo', self.admin)
        self.sell(self.p500, 1)
        self.assertEqual(analytics.sales(self.db, D1, D2, statuses=['em_preparo'])['orders'], 1)
        self.assertEqual(analytics.sales(self.db, D1, D2, statuses=['aguardando_pagamento'])['orders'], 0)

    def test_timeseries_buckets_sum_to_total(self):
        self.sell(self.p500, 1, '2026-10-02')
        self.sell(self.p1k, 2, '2026-10-20')
        for gran in ('dia', 'semana', 'mes'):
            ts = analytics.timeseries(self.db, D1, D2, gran)
            self.assertEqual(sum(r['revenue'] for r in ts), 15000, gran)
            self.assertEqual(sum(r['units'] for r in ts), 3, gran)
        self.assertEqual(len(analytics.timeseries(self.db, D1, D2, 'dia')), 31)

    def test_timeseries_separates_purchases(self):
        self.entry('Marketing', 5000)
        self.entry('Compra de café verde', 110000)
        self.entry('Outras receitas', 999)
        ts = analytics.timeseries(self.db, D1, D2, 'mes')
        self.assertEqual(ts[0]['expenses'], 5000)
        self.assertEqual(ts[0]['purchases'], 110000)
        self.assertEqual(ts[0]['purchases_paid'], 110000)

    def test_partner_split_uses_net_result_not_gross(self):
        self.sell(self.p1k, 10)                                      # receita 600,00
        self.entry('Marketing', 5450)                                # resultado = 600 − 345,50 − 54,50 = 200,00
        sim = partners.simulate(self.db, D1, D2, reserve_bp=2000)
        self.assertEqual(sim['dre']['net_result'], 20000)
        self.assertEqual(sim['reserve_cents'], 4000)
        self.assertEqual(sim['available_cents'], 16000)
        self.assertEqual([s['amount_cents'] for s in sim['shares']], [8000, 8000])
        self.assertNotEqual(sim['shares'][0]['amount_cents'], 60000 // 2)
        self.assertTrue(sim['can_approve'])
        did = partners.approve(self.db, sim, 'reunião', self.admin)
        self.assertEqual(partners.reserve_balance(self.db), 4000)
        again = partners.simulate(self.db, D1, D2, reserve_bp=2000)
        self.assertFalse(again['can_approve'])                       # período já distribuído
        with self.assertRaises(BusinessError):
            partners.approve(self.db, again, '', self.admin)
        partners.cancel(self.db, did, 'erro', self.admin)
        self.assertEqual(partners.reserve_balance(self.db), 0)

    def test_no_distribution_without_profit(self):
        self.sell(self.p500, 1)
        self.entry('Marketing', 50000)
        sim = partners.simulate(self.db, D1, D2, 0)
        self.assertLess(sim['dre']['net_result'], 0)
        self.assertEqual(sim['base_cents'], 0)
        self.assertFalse(sim['can_approve'])

    def test_base_above_available_blocked(self):
        self.sell(self.p1k, 10)
        sim = partners.simulate(self.db, D1, D2, 5000, approved_base_cents=10 ** 7)
        self.assertFalse(sim['can_approve'])

    def test_shares_must_sum_100(self):
        ids = [p['id'] for p in partners.list_partners(self.db)]
        with self.assertRaises(BusinessError):
            partners.update_shares(self.db, {ids[0]: 6000, ids[1]: 5000}, self.admin)
        partners.update_shares(self.db, {ids[0]: 6000, ids[1]: 4000}, self.admin)
