"""Painel administrativo. Toda rota exige usuário interno e a permissão correspondente."""
import re
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..db import get_db, now, today, transaction
from ..money import MoneyError, parse_money, parse_percent_bp
from ..security import DEFAULT_PERMS, PERMISSIONS, ROLES, hash_password, password_problem, staff_required, user_perms
from ..services import BusinessError, analytics, audit, catalog, finance, inventory, orders, partners, settings

bp = Blueprint('admin', __name__, url_prefix='/admin')
PER_PAGE = 25


def _page():
    try:
        return max(1, int(request.args.get('page', 1)))
    except ValueError:
        return 1


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _date(v):
    return v if v and re.match(r'^\d{4}-\d{2}-\d{2}$', v) else ''


def _back(default):
    return redirect(request.referrer or default)


# ---------- dashboard ----------
@bp.get('/')
@staff_required()
def dashboard():
    if not (user_perms(g.user) & {'pedidos', 'produtos', 'estoque', 'financeiro'}):
        return redirect(url_for('bi.dashboard') if 'bi' in user_perms(g.user) else url_for('shop.index'))
    db = get_db()
    t = date.today()
    m0, m1 = analytics.month_bounds(t)
    day = analytics.sales(db, t, t)
    month = analytics.dre(db, m0, m1)
    counts = {r['status']: r['n'] for r in db.execute('SELECT status, COUNT(*) n FROM orders GROUP BY status')}
    month_orders = db.execute('SELECT COUNT(*) FROM orders WHERE date(created_at) BETWEEN ? AND ?', (m0.isoformat(), m1.isoformat())).fetchone()[0]
    recent = db.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 8').fetchall()
    return render_template('admin/dashboard.html', day=day, month=month, counts=counts, month_orders=month_orders,
                           pending=sum(counts.get(s, 0) for s in orders.OPEN_STATUSES), low=analytics.low_stock(db),
                           recent=recent, STATUS=orders.STATUS, m0=m0, m1=m1, today=t,
                           missing_cost=db.execute('SELECT COUNT(*) FROM products WHERE active = 1 AND unit_cost_cents IS NULL').fetchone()[0])


# ---------- pedidos ----------
@bp.get('/pedidos')
@staff_required('pedidos')
def order_list():
    db = get_db()
    f = {'status': request.args.get('status', ''), 'start': _date(request.args.get('start')),
         'end': _date(request.args.get('end')), 'q': (request.args.get('q') or '').strip()[:80]}
    where, params = ['1=1'], []
    if f['status'] in orders.STATUS:
        where.append('status = ?'); params.append(f['status'])
    elif f['status'] == 'abertos':
        where.append(f"status IN ({','.join('?' * len(orders.OPEN_STATUSES))})"); params += list(orders.OPEN_STATUSES)
    if f['start']:
        where.append('date(created_at) >= ?'); params.append(f['start'])
    if f['end']:
        where.append('date(created_at) <= ?'); params.append(f['end'])
    if f['q']:
        where.append('(code LIKE ? OR customer_name LIKE ? OR customer_email LIKE ?)'); params += [f'%{f["q"]}%'] * 3
    w = ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM orders WHERE {w}', params).fetchone()[0]
    page = _page()
    rows = db.execute(f'SELECT * FROM orders WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?', (*params, PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
    sums = db.execute(f'SELECT COALESCE(SUM(total_cents),0) FROM orders WHERE {w}', params).fetchone()[0]
    return render_template('admin/orders.html', rows=rows, f=f, total=total, page=page, pages=max(1, -(-total // PER_PAGE)),
                           STATUS=orders.STATUS, sums=sums)


@bp.get('/pedidos/<int:oid>')
@staff_required('pedidos')
def order_detail(oid):
    db = get_db()
    o = orders.get(db, oid)
    if o is None:
        abort(404)
    events = db.execute('SELECT e.*, u.name AS user_name FROM order_events e LEFT JOIN users u ON u.id = e.user_id '
                        'WHERE e.order_id = ? ORDER BY e.id', (oid,)).fetchall()
    pays = db.execute('SELECT p.*, u.name AS user_name FROM payments p LEFT JOIN users u ON u.id = p.recorded_by '
                      'WHERE p.order_id = ? ORDER BY p.id', (oid,)).fetchall()
    movs = db.execute('SELECT m.*, p.name AS product FROM stock_movements m JOIN products p ON p.id = m.product_id '
                      'WHERE m.order_id = ? ORDER BY m.id', (oid,)).fetchall()
    paid = orders.paid_amount(db, oid)
    return render_template('admin/order_detail.html', o=o, items=orders.items(db, oid), events=events, pays=pays, movs=movs,
                           paid=paid, remaining=o['total_cents'] - paid, STATUS=orders.STATUS, next_steps=orders.TRANSITIONS[o['status']],
                           METHODS=orders.PAYMENT_METHODS, KIND_LABELS=inventory.KIND_LABELS, today=today())


@bp.post('/pedidos/<int:oid>/status')
@staff_required('pedidos')
def order_status(oid):
    try:
        orders.change_status(get_db(), oid, request.form.get('to', ''), g.user['id'], request.form.get('note', ''),
                             refund_confirmed=bool(request.form.get('refund_confirmed')))
        flash('Status do pedido atualizado.', 'ok')
    except BusinessError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.order_detail', oid=oid))


@bp.post('/pedidos/<int:oid>/pagamento')
@staff_required('pedidos')
def order_payment(oid):
    try:
        amount = parse_money(request.form.get('amount'), allow_zero=False)
        o = orders.register_payment(get_db(), oid, request.form.get('method', ''), amount, request.form.get('paid_at', ''),
                                    request.form.get('reference', ''), request.form.get('note', ''), g.user['id'])
        flash('Pagamento registrado. Pedido confirmado e estoque baixado.' if o['status'] == 'pago'
              else 'Pagamento parcial registrado. O pedido continua aguardando o restante.', 'ok')
    except (BusinessError, MoneyError) as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.order_detail', oid=oid))


@bp.post('/pedidos/<int:oid>/frete')
@staff_required('pedidos')
def order_shipping(oid):
    try:
        orders.set_shipping(get_db(), oid, parse_money(request.form.get('shipping')), g.user['id'])
        flash('Frete definido. O total do pedido foi atualizado.', 'ok')
    except (BusinessError, MoneyError) as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.order_detail', oid=oid))


# ---------- produtos ----------
@bp.get('/produtos')
@staff_required('produtos')
def products():
    db = get_db()
    rows = catalog.list_products(db)
    margins = {p['id']: catalog.margin(p) for p in rows}
    return render_template('admin/products.html', rows=rows, margins=margins, available=inventory.available)


def _product_form(p=None, errors=None, form=None):
    db = get_db()
    history = db.execute('SELECT h.*, u.name AS user_name FROM price_history h LEFT JOIN users u ON u.id = h.changed_by '
                         'WHERE h.product_id = ? ORDER BY h.id DESC', (p['id'],)).fetchall() if p else []
    return render_template('admin/product_form.html', p=dict(p) if p else None, errors=errors or {}, form=form, history=history,
                           margin=catalog.margin(p) if p else (None, None))


def _parse_product_money(form):
    errors = {}
    try:
        price = parse_money(form.get('price'))
    except MoneyError as e:
        price, errors['price'] = None, str(e)
    cost = None
    if (form.get('unit_cost') or '').strip():
        try:
            cost = parse_money(form.get('unit_cost'))
        except MoneyError as e:
            errors['unit_cost'] = str(e)
    return price, cost, errors


@bp.route('/produtos/novo', methods=['GET', 'POST'])
@staff_required('produtos')
def product_new():
    if request.method == 'POST':
        price, cost, errors = _parse_product_money(request.form)
        if not errors:
            try:
                with transaction():
                    pid = catalog.create(get_db(), request.form, price, cost, g.user['id'])
                    if request.files.get('image') and request.files['image'].filename:
                        catalog.save_image(get_db(), pid, request.files['image'], current_app.config['UPLOAD_DIR'])
                flash('Produto cadastrado. Lembre-se de registrar a entrada de estoque.', 'ok')
                return redirect(url_for('admin.product_edit', pid=pid))
            except BusinessError as e:
                errors['form'] = str(e)
        return _product_form(None, errors, request.form), 422
    return _product_form()


@bp.route('/produtos/<int:pid>', methods=['GET', 'POST'])
@staff_required('produtos')
def product_edit(pid):
    db = get_db()
    p = catalog.get(db, pid)
    if p is None:
        abort(404)
    if request.method == 'POST':
        price, cost, errors = _parse_product_money(request.form)
        if not errors:
            try:
                with transaction():
                    catalog.update(db, pid, request.form, price, cost, g.user['id'], request.form.get('change_note', '').strip())
                    if request.files.get('image') and request.files['image'].filename:
                        catalog.save_image(db, pid, request.files['image'], current_app.config['UPLOAD_DIR'])
                flash('Produto atualizado.', 'ok')
                return redirect(url_for('admin.product_edit', pid=pid))
            except BusinessError as e:
                errors['form'] = str(e)
        return _product_form(p, errors, request.form), 422
    return _product_form(p)


# ---------- estoque ----------
@bp.get('/estoque')
@staff_required('estoque')
def stock():
    db = get_db()
    pf = _int(request.args.get('product'))
    kf = request.args.get('kind', '')
    where, params = ['1=1'], []
    if pf:
        where.append('m.product_id = ?'); params.append(pf)
    if kf in inventory.KIND_LABELS:
        where.append('m.kind = ?'); params.append(kf)
    w = ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM stock_movements m WHERE {w}', params).fetchone()[0]
    page = _page()
    movs = db.execute(f'SELECT m.*, p.name AS product, o.code AS order_code, u.name AS user_name FROM stock_movements m '
                      f'JOIN products p ON p.id = m.product_id LEFT JOIN orders o ON o.id = m.order_id LEFT JOIN users u ON u.id = m.user_id '
                      f'WHERE {w} ORDER BY m.id DESC LIMIT ? OFFSET ?', (*params, PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
    return render_template('admin/stock.html', products=catalog.list_products(db), movs=movs, total=total, page=page,
                           pages=max(1, -(-total // PER_PAGE)), pf=pf, kf=kf, KIND_LABELS=inventory.KIND_LABELS,
                           MANUAL=inventory.MANUAL_KINDS, available=inventory.available)


@bp.post('/estoque/movimento')
@staff_required('estoque')
def stock_move():
    db = get_db()
    try:
        qty = _int(request.form.get('quantity'), -1)
        cost = parse_money(request.form.get('unit_cost')) if (request.form.get('unit_cost') or '').strip() else None
        note = (request.form.get('note') or '').strip()[:300]
        kind = request.form.get('kind', '')
        if kind != 'entrada' and not note:
            raise BusinessError('Explique o motivo do ajuste ou da perda.')
        with transaction(db):
            inventory.manual_movement(db, _int(request.form.get('product_id')), kind, qty, note, cost, g.user['id'])
            audit.log('movimentar_estoque', 'product', _int(request.form.get('product_id')), tipo=kind, quantidade=qty, nota=note)
        flash('Movimentação registrada.', 'ok')
    except (BusinessError, MoneyError) as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.stock'))


# ---------- financeiro ----------
@bp.get('/financeiro')
@staff_required('financeiro')
def finance_list():
    db = get_db()
    m0, m1 = analytics.month_bounds()
    f = {'start': _date(request.args.get('start')) or m0.isoformat(), 'end': _date(request.args.get('end')) or m1.isoformat(),
         'category': _int(request.args.get('category')), 'status': request.args.get('status', ''),
         'kind': request.args.get('kind', ''), 'q': (request.args.get('q') or '').strip()[:80]}
    res = finance.search(db, f['start'], f['end'], f['category'], f['status'], f['kind'], f['q'], _page(), PER_PAGE)
    s, e = date.fromisoformat(f['start']), date.fromisoformat(f['end'])
    return render_template('admin/finance.html', res=res, f=f, cats=finance.categories(db), STATUS=finance.STATUS,
                           KINDS=analytics.KIND_LABELS, dre=analytics.dre(db, s, e))


def _finance_form(entry=None, form=None, errors=None):
    db = get_db()
    return render_template('admin/finance_form.html', entry=dict(entry) if entry else None, form=form or {}, errors=errors or {},
                           cats=finance.categories(db, active_only=True), STATUS=finance.STATUS, KINDS=analytics.KIND_LABELS,
                           today=today())


@bp.route('/financeiro/novo', methods=['GET', 'POST'])
@staff_required('financeiro')
def finance_new():
    if request.method == 'POST':
        db = get_db()
        v, errors, _cat = finance.validate(db, request.form)
        if not errors:
            finance.create(db, v, g.user['id'])
            flash('Lançamento registrado.', 'ok')
            return redirect(url_for('admin.finance_list', start=v['competence_date'][:8] + '01'))
        return _finance_form(None, request.form, errors), 422
    return _finance_form()


@bp.route('/financeiro/<int:eid>', methods=['GET', 'POST'])
@staff_required('financeiro')
def finance_edit(eid):
    db = get_db()
    entry = finance.get(db, eid)
    if entry is None:
        abort(404)
    if request.method == 'POST':
        v, errors, _cat = finance.validate(db, request.form)
        if not errors:
            finance.update(db, eid, v, g.user['id'])
            flash('Lançamento atualizado.', 'ok')
            return redirect(url_for('admin.finance_list'))
        return _finance_form(entry, request.form, errors), 422
    return _finance_form(entry)


@bp.post('/financeiro/<int:eid>/excluir')
@staff_required('financeiro')
def finance_delete(eid):
    if request.form.get('confirm') != 'EXCLUIR':
        flash('Para excluir, digite EXCLUIR no campo de confirmação.', 'error')
        return redirect(url_for('admin.finance_edit', eid=eid))
    try:
        finance.delete(get_db(), eid, g.user['id'])
        flash('Lançamento excluído. O registro completo foi guardado no histórico de alterações.', 'ok')
    except BusinessError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.finance_list'))


@bp.post('/financeiro/categorias')
@staff_required('financeiro')
def finance_category():
    db = get_db()
    try:
        if request.form.get('toggle'):
            cid = _int(request.form.get('toggle'))
            db.execute('UPDATE finance_categories SET active = 1 - active WHERE id = ?', (cid,))
            audit.log('alternar_categoria', 'finance_category', cid)
            flash('Categoria atualizada.', 'ok')
        else:
            finance.create_category(db, request.form.get('name'), request.form.get('kind'), g.user['id'])
            flash('Categoria criada.', 'ok')
    except BusinessError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.finance_categories'))


@bp.get('/financeiro/categorias')
@staff_required('financeiro')
def finance_categories():
    db = get_db()
    rows = db.execute('SELECT c.*, (SELECT COUNT(*) FROM finance_entries e WHERE e.category_id = c.id) AS uses '
                      'FROM finance_categories c ORDER BY c.kind, c.name').fetchall()
    return render_template('admin/finance_categories.html', rows=rows, KINDS=analytics.KIND_LABELS)


# ---------- sócios ----------
@bp.get('/socios')
@staff_required('socios')
def partners_view():
    db = get_db()
    m0, m1 = analytics.month_bounds()
    s = analytics.parse_date(request.args.get('start'), m0)
    e = analytics.parse_date(request.args.get('end'), m1)
    if e < s:
        s, e = e, s
    errors = {}
    try:
        reserve_bp = parse_percent_bp(request.args['reserve']) if request.args.get('reserve') else int(settings.get('reserve_bp') or 0)
    except MoneyError as ex:
        reserve_bp, errors['reserve'] = int(settings.get('reserve_bp') or 0), str(ex)
    base = None
    if (request.args.get('base') or '').strip():
        try:
            base = parse_money(request.args.get('base'))
        except MoneyError as ex:
            errors['base'] = str(ex)
    sim = partners.simulate(db, s, e, reserve_bp, base)
    dists = db.execute('SELECT d.*, u.name AS user_name FROM distributions d LEFT JOIN users u ON u.id = d.approved_by ORDER BY d.id DESC').fetchall()
    shares = {d['id']: db.execute('SELECT * FROM distribution_shares WHERE distribution_id = ?', (d['id'],)).fetchall() for d in dists}
    reserves = db.execute('SELECT r.*, u.name AS user_name FROM cash_reserves r LEFT JOIN users u ON u.id = r.created_by ORDER BY r.id DESC LIMIT 20').fetchall()
    return render_template('admin/partners.html', sim=sim, partners=partners.list_partners(db), dists=dists, shares=shares,
                           reserves=reserves, balance=partners.reserve_balance(db), errors=errors,
                           base_input=request.args.get('base', ''), saved_reserve_bp=int(settings.get('reserve_bp') or 0))


@bp.post('/socios/participacao')
@staff_required('socios')
def partners_shares():
    db = get_db()
    try:
        shares = {p['id']: parse_percent_bp(request.form.get(f'share_{p["id"]}')) for p in partners.list_partners(db)}
        partners.update_shares(db, shares, g.user['id'])
        flash('Participação societária atualizada.', 'ok')
    except (BusinessError, MoneyError) as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.partners_view'))


@bp.post('/socios/reserva-padrao')
@staff_required('socios')
def partners_reserve_default():
    try:
        bp_ = parse_percent_bp(request.form.get('reserve'))
        old = settings.get('reserve_bp')
        settings.put('reserve_bp', bp_)
        audit.log('alterar_reserva_padrao', 'settings', None, antes=old, depois=bp_)
        flash('Percentual padrão de reserva salvo.', 'ok')
    except MoneyError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.partners_view'))


@bp.post('/socios/aprovar')
@staff_required('socios')
def partners_approve():
    db = get_db()
    try:
        s, e = date.fromisoformat(request.form['start']), date.fromisoformat(request.form['end'])
        reserve_bp = parse_percent_bp(request.form.get('reserve'))
        base = parse_money(request.form.get('base'))
        sim = partners.simulate(db, s, e, reserve_bp, base)
        if request.form.get('confirm') != '1':
            raise BusinessError('Marque a confirmação de que os sócios aprovaram esta distribuição.')
        partners.approve(db, sim, request.form.get('notes', ''), g.user['id'])
        flash('Distribuição aprovada e registrada. Nenhuma transferência foi feita pelo sistema.', 'ok')
    except (BusinessError, MoneyError, KeyError, ValueError) as ex:
        flash(str(ex) if not isinstance(ex, (KeyError, ValueError)) else 'Dados da simulação inválidos.', 'error')
    return redirect(url_for('admin.partners_view', start=request.form.get('start'), end=request.form.get('end'),
                            reserve=request.form.get('reserve'), base=request.form.get('base')))


@bp.post('/socios/<int:did>/cancelar')
@staff_required('socios')
def partners_cancel(did):
    try:
        partners.cancel(get_db(), did, request.form.get('reason', ''), g.user['id'])
        flash('Distribuição cancelada.', 'ok')
    except BusinessError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.partners_view'))


@bp.post('/socios/reserva-uso')
@staff_required('socios')
def partners_reserve_use():
    try:
        partners.register_reserve_use(get_db(), parse_money(request.form.get('amount'), allow_zero=False),
                                      request.form.get('note', ''), g.user['id'])
        flash('Uso da reserva registrado.', 'ok')
    except (BusinessError, MoneyError) as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.partners_view'))


# ---------- frete ----------
@bp.route('/frete', methods=['GET', 'POST'])
@staff_required('frete')
def shipping():
    db = get_db()
    if request.method == 'POST':
        try:
            name = (request.form.get('name') or '').strip()[:80]
            if len(name) < 3:
                raise BusinessError('Informe o nome da forma de entrega.')
            arrange = bool(request.form.get('to_arrange'))
            price = None if arrange else parse_money(request.form.get('price'))
            desc = (request.form.get('description') or '').strip()[:200]
            sid = _int(request.form.get('id'))
            active = 1 if request.form.get('active') else 0
            if sid:
                db.execute('UPDATE shipping_methods SET name = ?, description = ?, price_cents = ?, active = ? WHERE id = ?',
                           (name, desc, price, active, sid))
                audit.log('editar_frete', 'shipping_method', sid, nome=name, preco=price, ativo=active)
            else:
                cur = db.execute('INSERT INTO shipping_methods (name, description, price_cents, active, sort_order) VALUES (?,?,?,?,?)',
                                 (name, desc, price, active, (db.execute('SELECT COALESCE(MAX(sort_order),0) FROM shipping_methods').fetchone()[0] + 1)))
                audit.log('criar_frete', 'shipping_method', cur.lastrowid, nome=name, preco=price)
            flash('Forma de entrega salva.', 'ok')
        except (BusinessError, MoneyError) as e:
            flash(str(e), 'error')
        return redirect(url_for('admin.shipping'))
    return render_template('admin/shipping.html', rows=db.execute('SELECT * FROM shipping_methods ORDER BY sort_order, id').fetchall())


# ---------- usuários ----------
@bp.get('/usuarios')
@staff_required('usuarios')
def users():
    db = get_db()
    role = request.args.get('role', 'equipe')
    if role == 'cliente':
        rows = db.execute("SELECT * FROM users WHERE role = 'cliente' ORDER BY id DESC LIMIT 200").fetchall()
    else:
        rows = db.execute("SELECT * FROM users WHERE role <> 'cliente' ORDER BY role, name").fetchall()
    return render_template('admin/users.html', rows=rows, role=role, PERMISSIONS=PERMISSIONS, ROLES=ROLES)


def _admins_left(db, exclude_id):
    return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id <> ?", (exclude_id,)).fetchone()[0]


@bp.post('/usuarios')
@staff_required('usuarios')
def user_create():
    db = get_db()
    email = (request.form.get('email') or '').strip()
    name = (request.form.get('name') or '').strip()
    role = request.form.get('role', 'analista')
    pw = request.form.get('password') or ''
    try:
        if role not in ('admin', 'analista'):
            raise BusinessError('Escolha o perfil.')
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            raise BusinessError('E-mail inválido.')
        if len(name) < 2:
            raise BusinessError('Informe o nome.')
        problem = password_problem(pw)
        if problem:
            raise BusinessError(problem)
        if db.execute('SELECT 1 FROM users WHERE email = ?', (email,)).fetchone():
            raise BusinessError('Já existe um usuário com esse e-mail.')
        perms = [p for p in request.form.getlist('perms') if p in PERMISSIONS] or DEFAULT_PERMS[role]
        cur = db.execute('INSERT INTO users (email, name, password_hash, role, permissions, created_at) VALUES (?,?,?,?,?,?)',
                         (email, name, hash_password(pw), role, ','.join(perms), now()))
        audit.log('criar_usuario', 'user', cur.lastrowid, email=email, perfil=role, permissoes=perms)
        flash('Usuário criado. Informe a senha a ele por um canal seguro e peça para trocá-la.', 'ok')
    except BusinessError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.users'))


@bp.post('/usuarios/<int:uid>')
@staff_required('usuarios')
def user_update(uid):
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
    if u is None:
        abort(404)
    try:
        if u['role'] == 'cliente':
            active = 1 if request.form.get('active') else 0
            db.execute('UPDATE users SET active = ? WHERE id = ?', (active, uid))
            audit.log('alterar_cliente', 'user', uid, ativo=active)
        else:
            role = request.form.get('role', u['role'])
            if role not in ('admin', 'analista'):
                raise BusinessError('Perfil inválido.')
            active = 1 if request.form.get('active') else 0
            if (role != 'admin' or not active) and u['role'] == 'admin' and _admins_left(db, uid) == 0:
                raise BusinessError('É preciso manter pelo menos um administrador ativo.')
            if uid == g.user['id'] and not active:
                raise BusinessError('Você não pode desativar o próprio usuário.')
            perms = [p for p in request.form.getlist('perms') if p in PERMISSIONS]
            db.execute('UPDATE users SET role = ?, permissions = ?, active = ? WHERE id = ?', (role, ','.join(perms), active, uid))
            audit.log('alterar_usuario', 'user', uid, antes={'perfil': u['role'], 'permissoes': u['permissions'], 'ativo': u['active']},
                      depois={'perfil': role, 'permissoes': ','.join(perms), 'ativo': active})
        flash('Usuário atualizado.', 'ok')
    except BusinessError as e:
        flash(str(e), 'error')
    return redirect(url_for('admin.users', role='cliente' if u['role'] == 'cliente' else 'equipe'))


@bp.post('/usuarios/<int:uid>/senha')
@staff_required('usuarios')
def user_password(uid):
    db = get_db()
    pw = request.form.get('password') or ''
    problem = password_problem(pw)
    if problem:
        flash(problem, 'error')
    elif db.execute('SELECT 1 FROM users WHERE id = ?', (uid,)).fetchone() is None:
        abort(404)
    else:
        db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(pw), uid))
        audit.log('redefinir_senha', 'user', uid)
        flash('Senha redefinida. As sessões abertas desse usuário foram encerradas.', 'ok')
    return redirect(url_for('admin.users'))


@bp.route('/minha-senha', methods=['GET', 'POST'])
@staff_required()
def my_password():
    from werkzeug.security import check_password_hash
    if request.method == 'POST':
        db = get_db()
        if not check_password_hash(g.user['password_hash'], request.form.get('current') or ''):
            flash('Senha atual incorreta.', 'error')
        elif request.form.get('password') != request.form.get('password2'):
            flash('As senhas novas não conferem.', 'error')
        elif password_problem(request.form.get('password')):
            flash(password_problem(request.form.get('password')), 'error')
        else:
            from ..security import login_user
            db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(request.form['password']), g.user['id']))
            audit.log('trocar_propria_senha', 'user', g.user['id'])
            login_user(db.execute('SELECT * FROM users WHERE id = ?', (g.user['id'],)).fetchone())
            flash('Senha alterada.', 'ok')
            return redirect(url_for('admin.dashboard'))
    return render_template('admin/my_password.html')


# ---------- auditoria e configurações ----------
@bp.get('/auditoria', endpoint='audit')
@staff_required('auditoria')
def audit_view():
    db = get_db()
    page = _page()
    total = db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
    rows = db.execute('SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT ? OFFSET ?',
                      (50, (page - 1) * 50)).fetchall()
    return render_template('admin/audit.html', rows=rows, page=page, pages=max(1, -(-total // 50)), total=total)


@bp.route('/configuracoes', methods=['GET', 'POST'])
@staff_required('usuarios')
def store_settings():
    if request.method == 'POST':
        changes = {}
        for k in ('store_whatsapp', 'store_email', 'payment_instructions'):
            v = (request.form.get(k) or '').strip()[:600]
            if v != settings.get(k):
                changes[k] = v
                settings.put(k, v)
        if changes:
            audit.log('alterar_configuracoes', 'settings', None, **changes)
        flash('Configurações salvas.', 'ok')
        return redirect(url_for('admin.store_settings'))
    return render_template('admin/settings.html', s=settings.all_settings(),
                           payment_provider=current_app.config['PAYMENT_PROVIDER'])
