import re
import secrets
from pathlib import Path

from ..db import now
from . import BusinessError, audit

ALLOWED_IMAGE = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}


def list_products(db, active_only=False):
    sql = 'SELECT * FROM products' + (' WHERE active = 1' if active_only else '') + ' ORDER BY sort_order, id'
    return db.execute(sql).fetchall()


def get(db, product_id):
    return db.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()


def by_slug(db, slug):
    return db.execute('SELECT * FROM products WHERE slug = ? AND active = 1', (slug,)).fetchone()


def slugify(text):
    t = text.lower()
    for a, b in (('áàâãä', 'a'), ('éèêë', 'e'), ('íìîï', 'i'), ('óòôõö', 'o'), ('úùûü', 'u'), ('ç', 'c')):
        t = re.sub(f'[{a}]', b, t)
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-') or 'produto'


def validate(data):
    name = (data.get('name') or '').strip()
    if len(name) < 3:
        raise BusinessError('Informe o nome do produto.')
    try:
        grams = int(data.get('weight_grams') or 0)
    except ValueError:
        grams = 0
    if grams <= 0:
        raise BusinessError('Informe o peso em gramas (número inteiro maior que zero).')
    try:
        low = int(data.get('low_stock_threshold') or 0)
    except ValueError:
        raise BusinessError('O alerta de estoque baixo precisa ser um número inteiro.')
    if low < 0:
        raise BusinessError('O alerta de estoque baixo não pode ser negativo.')
    return name, grams, low


def create(db, data, price_cents, cost_cents, user_id):
    name, grams, low = validate(data)
    slug = base = slugify(name)
    n = 2
    while db.execute('SELECT 1 FROM products WHERE slug = ?', (slug,)).fetchone():
        slug, n = f'{base}-{n}', n + 1
    t = now()
    order = (db.execute('SELECT MAX(sort_order) FROM products').fetchone()[0] or 0) + 1
    cur = db.execute('INSERT INTO products (slug, name, weight_grams, description, price_cents, unit_cost_cents, '
                     'low_stock_threshold, image, active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (slug, name, grams, (data.get('description') or '').strip(), price_cents, cost_cents, low,
                      'img/produto-eaj.jpg', 1 if data.get('active') else 0, order, t, t))
    pid = cur.lastrowid
    db.execute('INSERT INTO price_history (product_id, price_cents, unit_cost_cents, changed_by, changed_at, note) VALUES (?,?,?,?,?,?)',
               (pid, price_cents, cost_cents, user_id, t, 'Cadastro do produto'))
    audit.log('criar_produto', 'product', pid, name=name, preco=price_cents, custo=cost_cents)
    return pid


def update(db, product_id, data, price_cents, cost_cents, user_id, note=''):
    p = get(db, product_id)
    if p is None:
        raise BusinessError('Produto não encontrado.')
    name, grams, low = validate(data)
    t = now()
    db.execute('UPDATE products SET name = ?, weight_grams = ?, description = ?, price_cents = ?, unit_cost_cents = ?, '
               'low_stock_threshold = ?, active = ?, updated_at = ? WHERE id = ?',
               (name, grams, (data.get('description') or '').strip(), price_cents, cost_cents, low,
                1 if data.get('active') else 0, t, product_id))
    if price_cents != p['price_cents'] or cost_cents != p['unit_cost_cents']:
        db.execute('INSERT INTO price_history (product_id, price_cents, unit_cost_cents, changed_by, changed_at, note) VALUES (?,?,?,?,?,?)',
                   (product_id, price_cents, cost_cents, user_id, t, note or 'Alteração de preço ou custo'))
    audit.log('editar_produto', 'product', product_id,
              antes={'preco': p['price_cents'], 'custo': p['unit_cost_cents'], 'ativo': p['active']},
              depois={'preco': price_cents, 'custo': cost_cents, 'ativo': 1 if data.get('active') else 0})


def save_image(db, product_id, file_storage, upload_dir):
    """Valida o conteúdo real da imagem (não só a extensão) e grava com nome aleatório."""
    from PIL import Image, UnidentifiedImageError
    if not file_storage or not file_storage.filename:
        return None
    try:
        img = Image.open(file_storage.stream)
        img.verify()
        fmt = img.format
    except (UnidentifiedImageError, OSError):
        raise BusinessError('O arquivo enviado não é uma imagem válida (use JPG, PNG ou WEBP).')
    if fmt not in ALLOWED_IMAGE:
        raise BusinessError('Formato não aceito. Use JPG, PNG ou WEBP.')
    file_storage.stream.seek(0)
    img = Image.open(file_storage.stream).convert('RGB')
    img.thumbnail((1400, 1400))
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    name = f'produto-{product_id}-{secrets.token_hex(6)}.jpg'
    img.save(Path(upload_dir) / name, 'JPEG', quality=86, optimize=True, progressive=True)
    db.execute('UPDATE products SET image = ?, updated_at = ? WHERE id = ?', (f'uploads/{name}', now(), product_id))
    audit.log('trocar_imagem_produto', 'product', product_id, arquivo=name)
    return name


def margin(product):
    """Margem bruta estimada por unidade: preço − custo unitário cadastrado."""
    if product['unit_cost_cents'] is None:
        return None, None
    m = product['price_cents'] - product['unit_cost_cents']
    pct = None if not product['price_cents'] else round(m * 10000 / product['price_cents'])
    return m, pct
