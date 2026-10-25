import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import date

from flask import g

from eaj import create_app, seed
from eaj.db import connect, migrate, now
from eaj.security import DEFAULT_PERMS, hash_password
from eaj.services import inventory

PASSWORD = 'senha-teste-123'


class AppTestCase(unittest.TestCase):
    """Cada teste roda com um banco SQLite novo em uma pasta temporária."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='eaj-test-')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'teste', 'LIVE_RELOAD': False,
                               'DATABASE_PATH': os.path.join(self.tmp, 'test.sqlite3'),
                               'UPLOAD_DIR': os.path.join(self.tmp, 'uploads')})
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.db = connect(self.app.config['DATABASE_PATH'])
        g.db = self.db                    # serviços e auditoria usam a mesma conexão do teste
        migrate(self.db)
        seed.run(self.db)
        self.p500 = self.db.execute("SELECT * FROM products WHERE slug = 'eaj-cafe-especial-500g'").fetchone()['id']
        self.p1k = self.db.execute("SELECT * FROM products WHERE slug = 'eaj-cafe-especial-1kg'").fetchone()['id']
        self.ship = self.db.execute('SELECT id FROM shipping_methods').fetchone()['id']
        self.admin = self.make_user('admin@eaj.test', 'admin')
        self.analyst = self.make_user('analista@eaj.test', 'analista')
        self.client_user = self.make_user('cliente@eaj.test', 'cliente')

    def tearDown(self):
        self.ctx.pop()                    # fecha a conexão (teardown do app)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- utilitários ----------
    def make_user(self, email, role):
        cur = self.db.execute('INSERT INTO users (email, name, password_hash, role, permissions, created_at) VALUES (?,?,?,?,?,?)',
                              (email, email.split('@')[0].title(), hash_password(PASSWORD), role, ','.join(DEFAULT_PERMS[role]), now()))
        return cur.lastrowid

    def stock_in(self, pid, qty):
        inventory.manual_movement(self.db, pid, 'entrada', qty, 'teste')

    def set_cost(self, pid, cents):
        self.db.execute('UPDATE products SET unit_cost_cents = ? WHERE id = ?', (cents, pid))

    def product(self, pid):
        return self.db.execute('SELECT * FROM products WHERE id = ?', (pid,)).fetchone()

    def customer(self, **kw):
        c = {'name': 'Cliente Teste', 'email': 'cliente.teste@example.com', 'phone': '(35) 99999-0000', 'zip': '37965-000',
             'street': 'Rua A', 'number': '10', 'complement': '', 'district': 'Centro', 'city': 'Jacuí', 'state': 'MG', 'notes': ''}
        c.update(kw)
        return c

    def place(self, items, **kw):
        from eaj.services import orders
        return orders.create_order(self.db, items, self.customer(**kw), self.ship)

    def pay(self, order, when=None, amount=None):
        from eaj.services import orders
        if order['shipping_to_arrange']:
            orders.set_shipping(self.db, order['id'], 0, self.admin)
        o = orders.get(self.db, order['id'])
        return orders.register_payment(self.db, o['id'], 'pix', amount or o['total_cents'], when or date.today().isoformat(),
                                       'REF', '', self.admin)

    # ---------- HTTP ----------
    def http(self):
        return self.app.test_client()

    def csrf(self, client, path='/conta/entrar'):
        html = client.get(path).get_data(as_text=True)
        m = re.search(r'name="csrf-token" content="([^"]+)"', html) or re.search(r'name="csrf_token" value="([^"]+)"', html)
        return m.group(1)

    def login(self, client, email):
        token = self.csrf(client)
        r = client.post('/conta/entrar', data={'email': email, 'password': PASSWORD, 'csrf_token': token})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return self.csrf(client, '/')

    def api(self, client, path, **params):
        r = client.get(path, query_string=params, headers={'Accept': 'application/json'})
        self.assertEqual(r.status_code, 200)
        return json.loads(r.get_data(as_text=True))
