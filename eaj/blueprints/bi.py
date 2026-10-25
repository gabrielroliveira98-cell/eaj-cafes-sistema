"""Painel de análise de dados. Lê somente dados reais do banco; aplica os mesmos filtros a todos os indicadores."""
import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, jsonify, render_template, request

from ..db import get_db
from ..money import fmt_brl
from ..security import staff_required
from ..services import analytics, catalog, finance, inventory, orders

bp = Blueprint('bi', __name__, url_prefix='/bi')
MAX_DAYS = 3 * 366


def filters():
    t = date.today()
    start = analytics.parse_date(request.args.get('start'), t - timedelta(days=29))
    end = analytics.parse_date(request.args.get('end'), t)
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS)
    gran = request.args.get('gran') if request.args.get('gran') in analytics.GRANULARITIES else None
    if gran is None:
        days = (end - start).days
        gran = 'dia' if days <= 45 else ('semana' if days <= 180 else 'mes')
    try:
        product = int(request.args.get('product') or 0) or None
    except ValueError:
        product = None
    status = request.args.get('status') if request.args.get('status') in orders.STATUS else None
    try:
        category = int(request.args.get('category') or 0) or None
    except ValueError:
        category = None
    return {'start': start, 'end': end, 'gran': gran, 'product': product,
            'statuses': [status] if status else None, 'status': status or '', 'category': category}


def data_version(db):
    """Muda sempre que um pedido, pagamento, lançamento ou movimentação é criado ou alterado."""
    r = db.execute("SELECT (SELECT COALESCE(MAX(id),0) FROM order_events) || '.' || (SELECT COALESCE(MAX(id),0) FROM payments) || '.' || "
                   "(SELECT COALESCE(MAX(updated_at),'') FROM finance_entries) || '.' || (SELECT COUNT(*) FROM finance_entries) || '.' || "
                   "(SELECT COALESCE(MAX(id),0) FROM stock_movements) || '.' || (SELECT COALESCE(MAX(updated_at),'') FROM products)").fetchone()
    return r[0]


@bp.get('/')
@staff_required('bi')
def dashboard():
    db = get_db()
    return render_template('bi/dashboard.html', f=filters(), products=catalog.list_products(db), cats=finance.categories(db),
                           STATUS=orders.STATUS, GRANS=analytics.GRANULARITIES)


@bp.get('/api/dados')
@staff_required('bi')
def api_data():
    db = get_db()
    f = filters()
    a = (db, f['start'], f['end'])
    d = analytics.dre(*a, f['product'], f['statuses'], f['category'])
    stock = [{'id': p['id'], 'name': p['name'], 'on_hand': p['stock_on_hand'], 'reserved': p['stock_reserved'],
              'available': inventory.available(p), 'low': inventory.available(p) <= p['low_stock_threshold']}
             for p in catalog.list_products(db) if not f['product'] or p['id'] == f['product']]
    registered = db.execute('SELECT COUNT(DISTINCT o.id) FROM orders o' +
                            (' JOIN order_items i ON i.order_id = o.id AND i.product_id = ?' if f['product'] else '') +
                            ' WHERE date(o.created_at) BETWEEN ? AND ?' + (' AND o.status = ?' if f['status'] else ''),
                            tuple(x for x in ([f['product']] if f['product'] else []) + [f['start'].isoformat(), f['end'].isoformat()] +
                                  ([f['status']] if f['status'] else []))).fetchone()[0]
    return jsonify({
        'version': data_version(db),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'filters': {'start': f['start'].isoformat(), 'end': f['end'].isoformat(), 'gran': f['gran'], 'product': f['product'],
                    'status': f['status'], 'category': f['category']},
        'kpis': {
            'gross_revenue': d['gross_revenue'], 'net_revenue': d['net_revenue'], 'deductions': d['deductions'],
            'orders_paid': d['sales']['orders'], 'orders_registered': registered, 'units': d['sales']['units'],
            'avg_ticket': d['sales']['avg_ticket_cents'], 'cogs': d['cogs'], 'gross_profit': d['gross_profit'],
            'gross_margin_bp': d['gross_margin_bp'], 'expenses_total': d['expenses_total'], 'opex': d['opex'],
            'operating_result': d['operating_result'], 'net_result': d['net_result'], 'merch_purchases': d['merch_purchases'],
            'stock_available': sum(s['available'] for s in stock), 'estimated': d['estimated'], 'notes': d['notes'],
            'units_without_cost': d['sales']['units_without_cost'], 'product_filter': bool(f['product']),
        },
        'series': analytics.timeseries(*a, f['gran'], f['product'], f['statuses'], f['category']),
        'products': analytics.by_product(*a, f['statuses'], f['product']),
        'status': analytics.status_distribution(*a, f['statuses'], f['product']),
        'expenses_by_category': analytics.expenses_by_category(*a, f['category']) if not f['product'] else [],
        'stock_series': analytics.stock_evolution(*a, f['gran'], f['product']),
        'stock': stock,
        'insights': analytics.insights(*a, f['statuses'], f['product'], f['category']),
    })


@bp.get('/api/versao')
@staff_required('bi')
def api_version():
    return jsonify(version=data_version(get_db()))


@bp.get('/relatorio.csv')
@staff_required('bi')
def report_csv():
    db = get_db()
    f = filters()
    kind = request.args.get('tipo', 'vendas')
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    money = lambda c: fmt_brl(c).replace('R$ ', '') if c is not None else ''
    if kind == 'produtos':
        w.writerow(['Produto', 'Unidades vendidas', 'Faturamento (R$)', 'CPV (R$)', 'Lucro bruto (R$)', 'Margem bruta (%)', 'Unidades sem custo'])
        for p in analytics.by_product(db, f['start'], f['end'], f['statuses'], f['product']):
            w.writerow([p['name'], p['units'], money(p['revenue']), money(p['cogs']), money(p['gross_profit']),
                        '' if p['margin_bp'] is None else f"{p['margin_bp'] / 100:.1f}".replace('.', ','), p['units_without_cost']])
    elif kind == 'despesas':
        w.writerow(['Categoria', 'Tipo', 'Total no período (R$)', 'Realizado (R$)', 'Previsto (R$)'])
        for c in analytics.expenses_by_category(db, f['start'], f['end'], f['category']):
            w.writerow([c['name'], analytics.KIND_LABELS[c['kind']], money(c['v']), money(c['paid']), money(c['v'] - c['paid'])])
    else:
        kind = 'vendas'
        w.writerow(['Período', 'Pedidos pagos', 'Unidades', 'Faturamento (R$)', 'CPV (R$)', 'Despesas e impostos (R$)',
                    'Compras de mercadoria (R$)'])
        for r in analytics.timeseries(db, f['start'], f['end'], f['gran'], f['product'], f['statuses'], f['category']):
            w.writerow([r['key'], r['orders'], r['units'], money(r['revenue']), money(r['cogs']), money(r['expenses']),
                        money(r['purchases'])])
    name = f'eaj-{kind}-{f["start"].isoformat()}-a-{f["end"].isoformat()}.csv'
    return Response('﻿' + buf.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})
