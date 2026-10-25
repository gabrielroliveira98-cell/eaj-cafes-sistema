"""Fluxos completos pela interface HTTP: permissões, CSRF, checkout e atualização do painel."""
import json
import re

from .base import AppTestCase


class HttpTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.stock_in(self.p500, 20)
        self.stock_in(self.p1k, 20)
        self.set_cost(self.p500, 1805)

    def test_public_pages(self):
        c = self.http()
        for path in ('/', '/produto/eaj-cafe-especial-500g', '/checkout', '/pedido/consultar', '/conta/entrar', '/conta/cadastro'):
            self.assertEqual(c.get(path).status_code, 200, path)
        self.assertEqual(c.get('/produto/nao-existe').status_code, 404)

    def test_public_never_exposes_internal_data(self):
        c = self.http()
        html = c.get('/').get_data(as_text=True)
        self.assertNotIn('unit_cost', html)
        self.assertNotIn('18,05', html)
        token = self.csrf(c, '/')
        r = c.post('/api/carrinho', json={'items': [{'product_id': self.p500, 'quantity': 1}]}, headers={'X-CSRF-Token': token})
        self.assertNotIn('unit_cost_cents', r.get_data(as_text=True))

    def test_private_routes_require_login_and_permission(self):
        anon = self.http()
        for path in ('/admin/', '/admin/pedidos', '/admin/financeiro', '/admin/socios', '/bi/', '/bi/api/dados'):
            self.assertEqual(anon.get(path).status_code, 302, path)
        cli = self.http()
        self.login(cli, 'cliente@eaj.test')
        for path in ('/admin/', '/admin/pedidos', '/bi/', '/bi/api/dados'):
            self.assertEqual(cli.get(path).status_code, 403, path)
        ana = self.http()
        self.login(ana, 'analista@eaj.test')
        self.assertEqual(ana.get('/bi/').status_code, 200)
        self.assertEqual(ana.get('/bi/api/dados').status_code, 200)
        for path in ('/admin/pedidos', '/admin/financeiro', '/admin/produtos', '/admin/usuarios', '/admin/socios'):
            self.assertEqual(ana.get(path).status_code, 403, path)

    def test_admin_can_grant_permissions(self):
        adm = self.http()
        token = self.login(adm, 'admin@eaj.test')
        r = adm.post(f'/admin/usuarios/{self.analyst}', data={'role': 'analista', 'active': '1', 'perms': ['bi', 'pedidos'], 'csrf_token': token})
        self.assertEqual(r.status_code, 302)
        ana = self.http()
        self.login(ana, 'analista@eaj.test')
        self.assertEqual(ana.get('/admin/pedidos').status_code, 200)
        self.assertEqual(ana.get('/admin/financeiro').status_code, 403)

    def test_csrf_required(self):
        c = self.http()
        self.assertEqual(c.post('/api/carrinho', json={'items': []}).status_code, 400)
        self.assertEqual(c.post('/checkout', data={}).status_code, 400)
        adm = self.http()
        self.login(adm, 'admin@eaj.test')
        self.assertEqual(adm.post('/admin/estoque/movimento', data={'product_id': self.p500, 'kind': 'entrada', 'quantity': 5}).status_code, 400)

    def test_login_rejects_bad_password(self):
        c = self.http()
        token = self.csrf(c)
        r = c.post('/conta/entrar', data={'email': 'admin@eaj.test', 'password': 'errada', 'csrf_token': token})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(c.get('/admin/').status_code, 302)

    def checkout(self, c, items, **over):
        token = self.csrf(c, '/checkout')
        data = {'csrf_token': token, 'items': json.dumps(items), 'shipping_method': self.ship, 'consent': '1',
                'name': 'Maria Teste', 'email': 'maria@example.com', 'phone': '35 99999 1111', 'zip': '37965000',
                'street': 'Rua das Flores', 'number': '12', 'district': 'Centro', 'city': 'Jacuí', 'state': 'MG'}
        data.update(over)
        return c.post('/checkout', data=data)

    def test_checkout_flow_and_dashboard_update(self):
        adm = self.http()
        self.login(adm, 'admin@eaj.test')
        before = self.api(adm, '/bi/api/dados')
        self.assertEqual(before['kpis']['gross_revenue'], 0)

        shop = self.http()
        r = self.checkout(shop, [{'product_id': self.p500, 'quantity': 2, 'unit_price_cents': 1}])
        self.assertEqual(r.status_code, 302)
        loc = r.headers['Location']
        page = shop.get(loc).get_data(as_text=True)
        self.assertIn('O pagamento ainda não foi realizado', page)
        code = re.search(r'EAJ-\d{4}-\d{5}', loc).group(0)
        oid = self.db.execute('SELECT id, total_cents FROM orders WHERE code = ?', (code,)).fetchone()
        self.assertEqual(oid['total_cents'], 6000)                    # preço do servidor, mesmo com valor adulterado

        mid = self.api(adm, '/bi/api/dados')
        self.assertEqual(mid['kpis']['gross_revenue'], 0)               # aguardando pagamento não é venda
        self.assertEqual(mid['kpis']['orders_registered'], 1)
        self.assertNotEqual(mid['version'], before['version'])

        token = self.csrf(adm, f'/admin/pedidos/{oid["id"]}')
        adm.post(f'/admin/pedidos/{oid["id"]}/frete', data={'shipping': '0', 'csrf_token': token})
        r = adm.post(f'/admin/pedidos/{oid["id"]}/pagamento', data={'method': 'pix', 'amount': '60,00', 'paid_at': self.today(),
                                                                    'reference': 'E2E', 'csrf_token': token})
        self.assertEqual(r.status_code, 302)
        after = self.api(adm, '/bi/api/dados')
        self.assertEqual(after['kpis']['gross_revenue'], 6000)
        self.assertEqual(after['kpis']['units'], 2)
        self.assertEqual(after['kpis']['avg_ticket'], 6000)
        self.assertEqual(after['kpis']['cogs'], 3610)
        self.assertEqual(sum(r['revenue'] for r in after['series']), 6000)
        self.assertEqual(self.product(self.p500)['stock_on_hand'], 18)

        # consulta pública exige número + e-mail corretos
        token = self.csrf(shop, '/pedido/consultar')
        bad = shop.post('/pedido/consultar', data={'code': code, 'email': 'outra@example.com', 'csrf_token': token})
        self.assertEqual(bad.status_code, 200)
        ok = shop.post('/pedido/consultar', data={'code': code, 'email': 'maria@example.com', 'csrf_token': token})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(self.http().get(f'/pedido/{code}').status_code, 404)   # sem token, sem acesso

    def test_checkout_validation(self):
        r = self.checkout(self.http(), [{'product_id': self.p500, 'quantity': 1}], email='invalido', consent='')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)

    def test_checkout_blocks_oversell(self):
        r = self.checkout(self.http(), [{'product_id': self.p1k, 'quantity': 21}])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)

    def test_customer_history(self):
        cli = self.http()
        self.login(cli, 'cliente@eaj.test')
        r = self.checkout(cli, [{'product_id': self.p1k, 'quantity': 1}], email='qualquer@example.com')
        self.assertEqual(r.status_code, 302)
        html = cli.get('/conta/pedidos').get_data(as_text=True)
        self.assertRegex(html, r'EAJ-\d{4}-\d{5}')
        row = self.db.execute('SELECT customer_email FROM orders').fetchone()
        self.assertEqual(row['customer_email'], 'cliente@eaj.test')       # pedido fica na conta do cliente

    def test_bi_csv_export(self):
        adm = self.http()
        self.login(adm, 'admin@eaj.test')
        for tipo in ('vendas', 'produtos', 'despesas'):
            r = adm.get('/bi/relatorio.csv', query_string={'tipo': tipo})
            self.assertEqual(r.status_code, 200)
            self.assertIn('text/csv', r.headers['Content-Type'])

    def test_admin_pages_render(self):
        adm = self.http()
        self.login(adm, 'admin@eaj.test')
        o = self.pay(self.place([{'product_id': self.p500, 'quantity': 1}]))
        for path in ('/admin/', '/admin/pedidos', f'/admin/pedidos/{o["id"]}', '/admin/produtos', f'/admin/produtos/{self.p500}',
                     '/admin/produtos/novo', '/admin/estoque', '/admin/financeiro', '/admin/financeiro/novo', '/admin/financeiro/categorias',
                     '/admin/socios', '/admin/frete', '/admin/usuarios', '/admin/usuarios?role=cliente', '/admin/auditoria',
                     '/admin/configuracoes', '/admin/minha-senha', '/bi/'):
            self.assertEqual(adm.get(path).status_code, 200, path)

    @staticmethod
    def today():
        from datetime import date
        return date.today().isoformat()
