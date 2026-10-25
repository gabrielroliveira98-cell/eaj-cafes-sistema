"""Dados iniciais oficiais. Só cria o que ainda não existe, então pode rodar mais de uma vez."""
from .db import now, transaction

PRODUCTS = [
    # slug, nome, gramas, preço em centavos, ordem
    ('eaj-cafe-especial-500g', 'EAJ Café Especial | 500 g', 500, 3000, 1),
    ('eaj-cafe-especial-1kg',  'EAJ Café Especial | 1 kg', 1000, 6000, 2),
]
PRODUCT_IMAGE = 'img/produto-eaj.jpg'

# Categorias são apenas a estrutura de classificação; nenhum valor é lançado.
CATEGORIES = [
    ('Aluguel e despesas fixas', 'despesa_fixa'),
    ('Despesas variáveis', 'despesa_variavel'),
    ('Compra de café verde', 'compra_mercadoria'),
    ('Compra de embalagens', 'compra_mercadoria'),
    ('Torrefação', 'custo_operacional'),
    ('Transporte e entregas', 'custo_operacional'),
    ('Máquinas e equipamentos', 'custo_operacional'),
    ('Taxas de pagamento', 'despesa_variavel'),
    ('Marketing', 'despesa_variavel'),
    ('Impostos', 'imposto'),
    ('Outras receitas', 'outra_receita'),
]

PARTNERS = [('Família', 5000), ('Parceiro investidor', 5000)]

SHIPPING = [('Entrega a combinar',
             'O valor e a forma de entrega são combinados com a EAJ depois do pedido.', None, 1)]


def run(db):
    t = now()
    with transaction(db):
        for slug, name, grams, price, order in PRODUCTS:
            if db.execute('SELECT 1 FROM products WHERE slug = ?', (slug,)).fetchone():
                continue
            desc = f'Café especial EAJ em embalagem de {"1 kg" if grams == 1000 else f"{grams} g"}.'
            cur = db.execute(
                'INSERT INTO products (slug, name, weight_grams, description, price_cents, unit_cost_cents, image, sort_order, created_at, updated_at) '
                'VALUES (?,?,?,?,?,NULL,?,?,?,?)', (slug, name, grams, desc, price, PRODUCT_IMAGE, order, t, t))
            db.execute('INSERT INTO price_history (product_id, price_cents, unit_cost_cents, changed_at, note) VALUES (?,?,NULL,?,?)',
                       (cur.lastrowid, price, t, 'Cadastro inicial'))
        for name, kind in CATEGORIES:
            db.execute('INSERT OR IGNORE INTO finance_categories (name, kind) VALUES (?, ?)', (name, kind))
        if not db.execute('SELECT 1 FROM partners').fetchone():
            db.executemany('INSERT INTO partners (name, share_bp) VALUES (?, ?)', PARTNERS)
        if not db.execute('SELECT 1 FROM shipping_methods').fetchone():
            db.executemany('INSERT INTO shipping_methods (name, description, price_cents, sort_order) VALUES (?,?,?,?)', SHIPPING)
