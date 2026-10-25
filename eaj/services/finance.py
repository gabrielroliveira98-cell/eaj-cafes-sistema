"""Lançamentos financeiros: despesas, compras, impostos e outras receitas.

Cada lançamento tem data de competência (a que período pertence) e, quando realizado,
data de pagamento/recebimento. "Previsto" e "realizado" ficam separados em todos os relatórios.
"""
import re

from ..db import now, transaction
from ..money import MoneyError, parse_money
from . import BusinessError, audit
from .analytics import KIND_LABELS

STATUS = {'previsto': 'Previsto', 'realizado': 'Realizado', 'cancelado': 'Cancelado'}
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def categories(db, active_only=False):
    return db.execute('SELECT * FROM finance_categories' + (' WHERE active = 1' if active_only else '') +
                      ' ORDER BY kind, name').fetchall()


def validate(db, form):
    errors, v = {}, {}
    try:
        v['category_id'] = int(form.get('category_id') or 0)
    except ValueError:
        v['category_id'] = 0
    cat = db.execute('SELECT * FROM finance_categories WHERE id = ?', (v['category_id'],)).fetchone()
    if cat is None:
        errors['category_id'] = 'Escolha a categoria.'
    v['description'] = (form.get('description') or '').strip()[:200]
    if len(v['description']) < 3:
        errors['description'] = 'Descreva o lançamento.'
    try:
        v['amount_cents'] = parse_money(form.get('amount'), allow_zero=False)
    except MoneyError as e:
        errors['amount'] = str(e)
    v['competence_date'] = (form.get('competence_date') or '').strip()
    if not DATE_RE.match(v['competence_date']):
        errors['competence_date'] = 'Informe a data de competência.'
    v['due_date'] = (form.get('due_date') or '').strip() or None
    if v['due_date'] and not DATE_RE.match(v['due_date']):
        errors['due_date'] = 'Data inválida.'
    v['status'] = form.get('status') if form.get('status') in STATUS else 'previsto'
    v['settled_date'] = (form.get('settled_date') or '').strip() or None
    if v['status'] == 'realizado' and not v['settled_date']:
        errors['settled_date'] = 'Informe a data em que foi pago ou recebido.'
    if v['settled_date'] and not DATE_RE.match(v['settled_date']):
        errors['settled_date'] = 'Data inválida.'
    if v['status'] != 'realizado':
        v['settled_date'] = None
    v['notes'] = (form.get('notes') or '').strip()[:600]
    return v, errors, cat


def create(db, v, user_id):
    t = now()
    with transaction(db):
        cur = db.execute('INSERT INTO finance_entries (category_id, description, amount_cents, competence_date, due_date, settled_date, '
                         'status, notes, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (v['category_id'], v['description'], v['amount_cents'], v['competence_date'], v['due_date'],
                          v['settled_date'], v['status'], v['notes'], user_id, t, t))
        audit.log('criar_lancamento', 'finance_entry', cur.lastrowid, user_id=user_id, **v)
    return cur.lastrowid


def get(db, entry_id):
    return db.execute('SELECT e.*, c.name AS category, c.kind FROM finance_entries e JOIN finance_categories c ON c.id = e.category_id '
                      'WHERE e.id = ?', (entry_id,)).fetchone()


def update(db, entry_id, v, user_id):
    with transaction(db):
        old = get(db, entry_id)
        if old is None:
            raise BusinessError('Lançamento não encontrado.')
        db.execute('UPDATE finance_entries SET category_id = ?, description = ?, amount_cents = ?, competence_date = ?, due_date = ?, '
                   'settled_date = ?, status = ?, notes = ?, updated_at = ? WHERE id = ?',
                   (v['category_id'], v['description'], v['amount_cents'], v['competence_date'], v['due_date'],
                    v['settled_date'], v['status'], v['notes'], now(), entry_id))
        audit.log('editar_lancamento', 'finance_entry', entry_id, user_id=user_id,
                  antes={k: old[k] for k in ('category_id', 'description', 'amount_cents', 'competence_date', 'status', 'settled_date')},
                  depois={k: v[k] for k in ('category_id', 'description', 'amount_cents', 'competence_date', 'status', 'settled_date')})


def delete(db, entry_id, user_id):
    with transaction(db):
        old = get(db, entry_id)
        if old is None:
            raise BusinessError('Lançamento não encontrado.')
        db.execute('DELETE FROM finance_entries WHERE id = ?', (entry_id,))
        audit.log('excluir_lancamento', 'finance_entry', entry_id, user_id=user_id,
                  registro={k: old[k] for k in old.keys()})


def search(db, start=None, end=None, category_id=None, status=None, kind=None, q='', page=1, per_page=25):
    where, params = ['1=1'], []
    if start:
        where.append('e.competence_date >= ?'); params.append(start)
    if end:
        where.append('e.competence_date <= ?'); params.append(end)
    if category_id:
        where.append('e.category_id = ?'); params.append(category_id)
    if status in STATUS:
        where.append('e.status = ?'); params.append(status)
    if kind in KIND_LABELS:
        where.append('c.kind = ?'); params.append(kind)
    if q:
        where.append('e.description LIKE ?'); params.append(f'%{q}%')
    w = ' AND '.join(where)
    base = f'FROM finance_entries e JOIN finance_categories c ON c.id = e.category_id WHERE {w}'
    total = db.execute(f'SELECT COUNT(*) {base}', params).fetchone()[0]
    sums = db.execute(f"SELECT COALESCE(SUM(CASE WHEN e.status='previsto' THEN e.amount_cents END),0) prev, "
                      f"COALESCE(SUM(CASE WHEN e.status='realizado' THEN e.amount_cents END),0) real {base} "
                      f"AND c.kind <> 'outra_receita'", params).fetchone()
    income = db.execute(f"SELECT COALESCE(SUM(CASE WHEN e.status <> 'cancelado' THEN e.amount_cents END),0) {base} "
                        f"AND c.kind = 'outra_receita'", params).fetchone()[0]
    rows = db.execute(f'SELECT e.*, c.name AS category, c.kind {base} ORDER BY e.competence_date DESC, e.id DESC LIMIT ? OFFSET ?',
                      (*params, per_page, (page - 1) * per_page)).fetchall()
    return {'rows': rows, 'total': total, 'pages': max(1, -(-total // per_page)), 'page': page,
            'sum_previsto': sums['prev'], 'sum_realizado': sums['real'], 'sum_income': income}


def create_category(db, name, kind, user_id):
    name = (name or '').strip()[:80]
    if len(name) < 2:
        raise BusinessError('Informe o nome da categoria.')
    if kind not in KIND_LABELS:
        raise BusinessError('Escolha o tipo da categoria.')
    if db.execute('SELECT 1 FROM finance_categories WHERE name = ?', (name,)).fetchone():
        raise BusinessError('Já existe uma categoria com esse nome.')
    cur = db.execute('INSERT INTO finance_categories (name, kind) VALUES (?, ?)', (name, kind))
    audit.log('criar_categoria', 'finance_category', cur.lastrowid, user_id=user_id, name=name, kind=kind)
