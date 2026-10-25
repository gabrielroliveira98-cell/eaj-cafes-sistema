"""Consultas analíticas. Todos os números saem do banco; nada é estimado sem aviso.

Definições usadas no sistema (iguais no painel administrativo, no BI e nos sócios):
  * Venda = pedido com pagamento confirmado (status pago, em preparo, enviado ou entregue).
    A data da venda é a data da confirmação do pagamento (orders.paid_at).
  * Receita bruta = total dos pedidos vendidos (produtos + frete cobrado).
  * Deduções = lançamentos da categoria "imposto" no período (competência).
  * Receita líquida = receita bruta − deduções.
  * CPV = quantidade vendida × custo unitário registrado no momento do pedido.
  * Lucro bruto = receita líquida − CPV.
  * Despesas operacionais = despesas fixas + variáveis + custos operacionais (competência, não canceladas).
  * Resultado operacional = lucro bruto − despesas operacionais.
  * Resultado líquido estimado = resultado operacional + outras receitas.
  * Compras de mercadoria (café verde, embalagens) aparecem no caixa, mas ficam FORA do resultado,
    porque o custo delas já entra pelo CPV quando o produto é vendido. Somar as duas seria contar duas vezes.
Isto é uma visão gerencial; não substitui a apuração contábil e tributária da empresa.
"""
from datetime import date, datetime, timedelta

from .orders import REVENUE_STATUSES, STATUS

OPEX_KINDS = ('despesa_fixa', 'despesa_variavel', 'custo_operacional')
KIND_LABELS = {'despesa_fixa': 'Despesa fixa', 'despesa_variavel': 'Despesa variável',
               'compra_mercadoria': 'Compra de mercadoria', 'custo_operacional': 'Custo operacional',
               'imposto': 'Imposto', 'outra_receita': 'Outra receita'}
GRANULARITIES = {'dia': 'Dia', 'semana': 'Semana', 'mes': 'Mês', 'ano': 'Ano'}


def parse_date(s, default):
    try:
        return datetime.strptime((s or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return default


def month_bounds(d=None):
    d = d or date.today()
    start = d.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def _in(statuses):
    return ','.join('?' * len(statuses))


def sale_statuses(filter_statuses=None):
    """Status que contam como venda, respeitando o filtro escolhido."""
    if not filter_statuses:
        return list(REVENUE_STATUSES)
    return [s for s in REVENUE_STATUSES if s in filter_statuses]


# ---------- vendas ----------
def sales(db, start, end, product_id=None, statuses=None):
    st = sale_statuses(statuses)
    out = {'gross_cents': 0, 'product_cents': 0, 'shipping_cents': 0, 'orders': 0, 'units': 0,
           'cogs_cents': 0, 'units_without_cost': 0, 'avg_ticket_cents': None}
    if not st:
        return out
    a, b = start.isoformat(), end.isoformat()
    if product_id:
        r = db.execute(
            f'SELECT COUNT(DISTINCT o.id) n, COALESCE(SUM(i.line_total_cents),0) rev, COALESCE(SUM(i.quantity),0) units, '
            f'COALESCE(SUM(CASE WHEN i.unit_cost_cents IS NOT NULL THEN i.quantity * i.unit_cost_cents END),0) cogs, '
            f'COALESCE(SUM(CASE WHEN i.unit_cost_cents IS NULL THEN i.quantity END),0) nocost '
            f'FROM orders o JOIN order_items i ON i.order_id = o.id '
            f'WHERE o.status IN ({_in(st)}) AND date(o.paid_at) BETWEEN ? AND ? AND i.product_id = ?',
            (*st, a, b, product_id)).fetchone()
        out.update(gross_cents=r['rev'], product_cents=r['rev'], orders=r['n'], units=r['units'],
                   cogs_cents=r['cogs'], units_without_cost=r['nocost'])
    else:
        r = db.execute(f'SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) gross, COALESCE(SUM(subtotal_cents),0) prod, '
                       f'COALESCE(SUM(shipping_cents),0) ship FROM orders '
                       f'WHERE status IN ({_in(st)}) AND date(paid_at) BETWEEN ? AND ?', (*st, a, b)).fetchone()
        i = db.execute(f'SELECT COALESCE(SUM(i.quantity),0) units, '
                       f'COALESCE(SUM(CASE WHEN i.unit_cost_cents IS NOT NULL THEN i.quantity * i.unit_cost_cents END),0) cogs, '
                       f'COALESCE(SUM(CASE WHEN i.unit_cost_cents IS NULL THEN i.quantity END),0) nocost '
                       f'FROM order_items i JOIN orders o ON o.id = i.order_id '
                       f'WHERE o.status IN ({_in(st)}) AND date(o.paid_at) BETWEEN ? AND ?', (*st, a, b)).fetchone()
        out.update(gross_cents=r['gross'], product_cents=r['prod'], shipping_cents=r['ship'], orders=r['n'],
                   units=i['units'], cogs_cents=i['cogs'], units_without_cost=i['nocost'])
    if out['orders']:
        out['avg_ticket_cents'] = round(out['gross_cents'] / out['orders'])
    return out


# ---------- lançamentos financeiros ----------
def finance_by_kind(db, start, end, category_id=None):
    sql = ('SELECT c.kind, e.status, COALESCE(SUM(e.amount_cents),0) v FROM finance_entries e '
           'JOIN finance_categories c ON c.id = e.category_id '
           "WHERE e.status <> 'cancelado' AND e.competence_date BETWEEN ? AND ?")
    params = [start.isoformat(), end.isoformat()]
    if category_id:
        sql += ' AND e.category_id = ?'
        params.append(category_id)
    out = {k: {'previsto': 0, 'realizado': 0, 'total': 0} for k in KIND_LABELS}
    for r in db.execute(sql + ' GROUP BY c.kind, e.status', params):
        out[r['kind']][r['status']] += r['v']
        out[r['kind']]['total'] += r['v']
    return out


def dre(db, start, end, product_id=None, statuses=None, category_id=None):
    s = sales(db, start, end, product_id, statuses)
    fk = finance_by_kind(db, start, end, category_id)
    # com filtro de produto, despesas gerais não são rateadas: o resultado fica no nível do produto
    general = not product_id
    deductions = fk['imposto']['total'] if general else 0
    opex = sum(fk[k]['total'] for k in OPEX_KINDS) if general else 0
    other = fk['outra_receita']['total'] if general else 0
    net_rev = s['gross_cents'] - deductions
    gross_profit = net_rev - s['cogs_cents']
    op_result = gross_profit - opex
    notes = []
    if s['units_without_cost']:
        notes.append(f'{s["units_without_cost"]} unidade(s) vendida(s) sem custo unitário cadastrado: o CPV e o lucro bruto estão subestimados.')
    if general and s['gross_cents'] and not fk['imposto']['total']:
        notes.append('Nenhum imposto registrado no período: a receita líquida é igual à bruta até que os impostos sejam lançados.')
    if product_id:
        notes.append('Com filtro de produto, despesas e impostos gerais não são distribuídos entre produtos; o resultado mostrado é o lucro bruto do produto.')
    return {'sales': s, 'finance': fk, 'gross_revenue': s['gross_cents'], 'deductions': deductions, 'net_revenue': net_rev,
            'cogs': s['cogs_cents'], 'gross_profit': gross_profit, 'opex': opex, 'operating_result': op_result,
            'other_income': other, 'net_result': op_result + other,
            'merch_purchases': fk['compra_mercadoria']['total'] if general else 0,
            'expenses_total': (opex + deductions + fk['compra_mercadoria']['total']) if general else 0,
            'gross_margin_bp': round(gross_profit * 10000 / net_rev) if net_rev else None,
            'estimated': bool(notes), 'notes': notes}


# ---------- séries temporais ----------
def bucket_key(d, gran):
    if gran == 'ano':
        return d.strftime('%Y')
    if gran == 'mes':
        return d.strftime('%Y-%m')
    if gran == 'semana':
        return (d - timedelta(days=d.weekday())).isoformat()       # segunda-feira da semana
    return d.isoformat()


def bucket_label(key, gran):
    months = 'jan fev mar abr mai jun jul ago set out nov dez'.split()
    if gran == 'ano':
        return key
    if gran == 'mes':
        return f'{months[int(key[5:7]) - 1]}/{key[2:4]}'
    d = date.fromisoformat(key)
    return (f'sem. {d.day:02d}/{d.month:02d}' if gran == 'semana' else f'{d.day:02d}/{d.month:02d}')


def buckets(start, end, gran):
    keys, d = [], start
    while d <= end:
        k = bucket_key(d, gran)
        if not keys or keys[-1] != k:
            keys.append(k)
        d += timedelta(days=1)
    return keys


def timeseries(db, start, end, gran='dia', product_id=None, statuses=None, category_id=None):
    keys = buckets(start, end, gran)
    # expenses = despesas operacionais + impostos (entram no resultado)
    # purchases = compras de mercadoria (só caixa; o custo entra pelo CPV)
    rows = {k: {'key': k, 'label': bucket_label(k, gran), 'revenue': 0, 'orders': 0, 'units': 0, 'cogs': 0,
                'expenses': 0, 'expenses_paid': 0, 'purchases': 0, 'purchases_paid': 0} for k in keys}
    st = sale_statuses(statuses)
    a, b = start.isoformat(), end.isoformat()
    if st:
        if product_id:
            q = db.execute(f'SELECT date(o.paid_at) d, COUNT(DISTINCT o.id) n, SUM(i.line_total_cents) rev, SUM(i.quantity) u, '
                           f'SUM(COALESCE(i.unit_cost_cents,0) * i.quantity) c FROM orders o JOIN order_items i ON i.order_id = o.id '
                           f'WHERE o.status IN ({_in(st)}) AND date(o.paid_at) BETWEEN ? AND ? AND i.product_id = ? GROUP BY d',
                           (*st, a, b, product_id))
        else:
            per_day = {r['d']: dict(r) for r in db.execute(
                f'SELECT date(paid_at) d, COUNT(*) n, SUM(total_cents) rev FROM orders '
                f'WHERE status IN ({_in(st)}) AND date(paid_at) BETWEEN ? AND ? GROUP BY d', (*st, a, b))}
            for r in db.execute(f'SELECT date(o.paid_at) d, SUM(i.quantity) u, SUM(COALESCE(i.unit_cost_cents,0) * i.quantity) c '
                                f'FROM order_items i JOIN orders o ON o.id = i.order_id '
                                f'WHERE o.status IN ({_in(st)}) AND date(o.paid_at) BETWEEN ? AND ? GROUP BY d', (*st, a, b)):
                per_day.setdefault(r['d'], {'d': r['d'], 'n': 0, 'rev': 0}).update(u=r['u'], c=r['c'])
            q = [{'d': d, 'n': v.get('n', 0), 'rev': v.get('rev', 0), 'u': v.get('u', 0), 'c': v.get('c', 0)} for d, v in per_day.items()]
        for r in q:
            k = bucket_key(date.fromisoformat(r['d']), gran)
            if k in rows:
                rows[k]['revenue'] += r['rev'] or 0
                rows[k]['orders'] += r['n'] or 0
                rows[k]['units'] += r['u'] or 0
                rows[k]['cogs'] += r['c'] or 0
    if not product_id:
        sql = ("SELECT e.competence_date d, e.status, c.kind = 'compra_mercadoria' AS purchase, SUM(e.amount_cents) v "
               'FROM finance_entries e JOIN finance_categories c ON c.id = e.category_id '
               "WHERE e.status <> 'cancelado' AND c.kind <> 'outra_receita' AND e.competence_date BETWEEN ? AND ?")
        params = [a, b]
        if category_id:
            sql += ' AND e.category_id = ?'
            params.append(category_id)
        for r in db.execute(sql + ' GROUP BY d, e.status, purchase', params):
            k = bucket_key(date.fromisoformat(r['d']), gran)
            if k in rows:
                col = 'purchases' if r['purchase'] else 'expenses'
                rows[k][col] += r['v']
                if r['status'] == 'realizado':
                    rows[k][col + '_paid'] += r['v']
    return [rows[k] for k in keys]


# ---------- por produto ----------
def by_product(db, start, end, statuses=None, product_id=None):
    st = sale_statuses(statuses)
    prods = db.execute('SELECT id, name, price_cents, unit_cost_cents FROM products ORDER BY sort_order, id').fetchall()
    data = {p['id']: {'id': p['id'], 'name': p['name'], 'revenue': 0, 'units': 0, 'cogs': 0, 'units_without_cost': 0,
                      'price_cents': p['price_cents'], 'unit_cost_cents': p['unit_cost_cents']} for p in prods
            if not product_id or p['id'] == product_id}
    if st:
        for r in db.execute(f'SELECT i.product_id pid, SUM(i.line_total_cents) rev, SUM(i.quantity) u, '
                            f'SUM(CASE WHEN i.unit_cost_cents IS NOT NULL THEN i.unit_cost_cents * i.quantity ELSE 0 END) c, '
                            f'SUM(CASE WHEN i.unit_cost_cents IS NULL THEN i.quantity ELSE 0 END) nc '
                            f'FROM order_items i JOIN orders o ON o.id = i.order_id '
                            f'WHERE o.status IN ({_in(st)}) AND date(o.paid_at) BETWEEN ? AND ? GROUP BY i.product_id',
                            (*st, start.isoformat(), end.isoformat())):
            if r['pid'] in data:
                d = data[r['pid']]
                d.update(revenue=r['rev'], units=r['u'], cogs=r['c'], units_without_cost=r['nc'])
    out = []
    for d in data.values():
        costed_rev = d['revenue'] - (d['units_without_cost'] * d['price_cents'] if d['units_without_cost'] else 0)
        d['gross_profit'] = d['revenue'] - d['cogs'] if not d['units_without_cost'] else None
        d['margin_bp'] = (round((costed_rev - d['cogs']) * 10000 / costed_rev) if costed_rev > 0 and d['units_without_cost'] < d['units'] else None)
        if d['unit_cost_cents'] is not None and d['price_cents']:
            d['catalog_margin_bp'] = round((d['price_cents'] - d['unit_cost_cents']) * 10000 / d['price_cents'])
        else:
            d['catalog_margin_bp'] = None
        out.append(d)
    return out


def status_distribution(db, start, end, statuses=None, product_id=None):
    sql = 'SELECT o.status, COUNT(DISTINCT o.id) n FROM orders o'
    params = []
    if product_id:
        sql += ' JOIN order_items i ON i.order_id = o.id AND i.product_id = ?'
        params.append(product_id)
    sql += ' WHERE date(o.created_at) BETWEEN ? AND ?'
    params += [start.isoformat(), end.isoformat()]
    if statuses:
        sql += f' AND o.status IN ({_in(statuses)})'
        params += list(statuses)
    counts = {r['status']: r['n'] for r in db.execute(sql + ' GROUP BY o.status', params)}
    return [{'status': s, 'label': STATUS[s], 'count': counts.get(s, 0)} for s in STATUS if not statuses or s in statuses]


def stock_evolution(db, start, end, gran='dia', product_id=None):
    keys = buckets(start, end, gran)
    prods = db.execute('SELECT id, name, stock_on_hand FROM products' + (' WHERE id = ?' if product_id else '') +
                       ' ORDER BY sort_order, id', (product_id,) if product_id else ()).fetchall()
    series = []
    for p in prods:
        before = db.execute('SELECT on_hand_after FROM stock_movements WHERE product_id = ? AND date(created_at) < ? ORDER BY id DESC LIMIT 1',
                            (p['id'], start.isoformat())).fetchone()
        level = before['on_hand_after'] if before else 0
        by_bucket = {}
        for r in db.execute('SELECT created_at, on_hand_after FROM stock_movements WHERE product_id = ? AND date(created_at) BETWEEN ? AND ? ORDER BY id',
                            (p['id'], start.isoformat(), end.isoformat())):
            by_bucket[bucket_key(date.fromisoformat(r['created_at'][:10]), gran)] = r['on_hand_after']
        vals = []
        for k in keys:
            level = by_bucket.get(k, level)
            vals.append(level)
        series.append({'id': p['id'], 'name': p['name'], 'values': vals})
    return {'labels': [bucket_label(k, gran) for k in keys], 'series': series}


def expenses_by_category(db, start, end, category_id=None):
    sql = ('SELECT c.id, c.name, c.kind, SUM(e.amount_cents) v, '
           "SUM(CASE WHEN e.status = 'realizado' THEN e.amount_cents ELSE 0 END) paid "
           'FROM finance_entries e JOIN finance_categories c ON c.id = e.category_id '
           "WHERE e.status <> 'cancelado' AND c.kind <> 'outra_receita' AND e.competence_date BETWEEN ? AND ?")
    params = [start.isoformat(), end.isoformat()]
    if category_id:
        sql += ' AND c.id = ?'
        params.append(category_id)
    return [dict(r) for r in db.execute(sql + ' GROUP BY c.id ORDER BY v DESC', params)]


def low_stock(db):
    return db.execute('SELECT *, stock_on_hand - stock_reserved AS available FROM products '
                      'WHERE active = 1 AND stock_on_hand - stock_reserved <= low_stock_threshold ORDER BY available').fetchall()


def weekday_orders(db, start, end, statuses=None):
    st = sale_statuses(statuses)
    names = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado']
    counts = [0] * 7
    if st:
        for r in db.execute(f"SELECT CAST(strftime('%w', paid_at) AS INTEGER) w, COUNT(*) n FROM orders "
                            f'WHERE status IN ({_in(st)}) AND date(paid_at) BETWEEN ? AND ? GROUP BY w',
                            (*st, start.isoformat(), end.isoformat())):
            counts[r['w']] = r['n']
    return [{'day': names[i], 'count': counts[i]} for i in range(7)]


# ---------- insights ----------
MIN_ORDERS_FOR_TRENDS = 5


def insights(db, start, end, statuses=None, product_id=None, category_id=None):
    """Separa fatos observados de recomendações. Não tira conclusões com poucos dados."""
    facts, alerts, recs = [], [], []
    cur = sales(db, start, end, product_id, statuses)
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev = sales(db, prev_end - timedelta(days=days - 1), prev_end, product_id, statuses)

    prods = [p for p in by_product(db, start, end, statuses, product_id) if p['units']]
    if prods:
        top_u = max(prods, key=lambda p: p['units'])
        top_r = max(prods, key=lambda p: p['revenue'])
        facts.append(f'Produto com maior volume: {top_u["name"]} ({top_u["units"]} unidade(s)).')
        if top_r['id'] != top_u['id']:
            facts.append(f'Produto com maior faturamento: {top_r["name"]}.')
        else:
            facts.append(f'{top_r["name"]} também lidera em faturamento.')

    if cur['orders'] >= MIN_ORDERS_FOR_TRENDS and prev['orders'] >= MIN_ORDERS_FOR_TRENDS and prev['gross_cents']:
        var = round((cur['gross_cents'] - prev['gross_cents']) * 10000 / prev['gross_cents'])
        sign = 'acima' if var >= 0 else 'abaixo'
        facts.append(f'Faturamento {abs(var) / 100:.1f}% {sign} do período anterior de mesma duração.'.replace('.', ',', 1))
        if var <= -2000:
            alerts.append('Queda de vendas de 20% ou mais em relação ao período anterior.')
    elif cur['orders'] or prev['orders']:
        facts.append(f'Ainda há poucas vendas para comparar períodos com segurança (mínimo de {MIN_ORDERS_FOR_TRENDS} pedidos pagos em cada período).')

    wd = weekday_orders(db, start, end, statuses)
    total_wd = sum(w['count'] for w in wd)
    if total_wd >= MIN_ORDERS_FOR_TRENDS:
        best = max(wd, key=lambda w: w['count'])
        facts.append(f'Dia da semana com mais pedidos pagos: {best["day"]} ({best["count"]} pedido(s)).')

    if not product_id:
        cats = expenses_by_category(db, start, end, category_id)
        tot = sum(c['v'] for c in cats)
        if tot:
            c = cats[0]
            facts.append(f'Maior categoria de despesa: {c["name"]} ({round(c["v"] * 100 / tot)}% dos lançamentos do período).')

    for p in low_stock(db):
        alerts.append(f'Estoque baixo: {p["name"]} com {p["available"]} unidade(s) disponível(is).')
    missing = db.execute('SELECT name FROM products WHERE active = 1 AND unit_cost_cents IS NULL').fetchall()
    if missing:
        recs.append('Cadastre o custo unitário de ' + ', '.join(m['name'] for m in missing) + ' para calcular margem e CPV.')
    pending = db.execute("SELECT COUNT(*) FROM orders WHERE status = 'aguardando_pagamento'").fetchone()[0]
    if pending:
        recs.append(f'{pending} pedido(s) aguardando pagamento: confirme ou cancele para liberar o estoque reservado.')
    if not cur['orders'] and not facts:
        facts.append('Nenhuma venda confirmada no período selecionado.')
    return {'facts': facts, 'alerts': alerts, 'recommendations': recs}
