"""Login (clientes e equipe), cadastro de clientes e histórico de pedidos."""
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..db import get_db, now, transaction
from ..security import (authenticate, clear_failures, first_allowed_endpoint, hash_password, is_staff, login_blocked,
                        login_required, login_user, logout_user, password_problem, register_failure, safe_next)
from ..services import orders

bp = Blueprint('account', __name__, url_prefix='/conta')


@bp.route('/entrar', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        key = f'{request.remote_addr}|{email.lower()}'
        if login_blocked(key):
            flash('Muitas tentativas. Aguarde 15 minutos e tente novamente.', 'error')
            return render_template('account/login.html', email=email), 429
        user = authenticate(email, request.form.get('password'))
        if user is None:
            register_failure(key)
            flash('E-mail ou senha incorretos.', 'error')
            return render_template('account/login.html', email=email), 401
        clear_failures(key)
        login_user(user)
        if is_staff(user):
            ep = first_allowed_endpoint(user)
            default = url_for(ep) if ep else url_for('shop.index')
        else:
            default = url_for('account.my_orders')
        return redirect(safe_next(request.args.get('next'), default))
    return render_template('account/login.html', email='')


@bp.post('/sair')
def logout():
    logout_user()
    flash('Você saiu da sua conta.', 'ok')
    return redirect(url_for('shop.index'))


@bp.route('/cadastro', methods=['GET', 'POST'])
def register():
    form, errors = {}, {}
    if request.method == 'POST':
        form = {k: (request.form.get(k) or '').strip() for k in ('name', 'email', 'phone')}
        pw = request.form.get('password') or ''
        if len(form['name']) < 3:
            errors['name'] = 'Informe seu nome.'
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', form['email']):
            errors['email'] = 'Informe um e-mail válido.'
        if not 10 <= len(re.sub(r'\D', '', form['phone'])) <= 13:
            errors['phone'] = 'Informe um telefone com DDD.'
        problem = password_problem(pw)
        if problem:
            errors['password'] = problem
        elif pw != request.form.get('password2'):
            errors['password2'] = 'As senhas não conferem.'
        db = get_db()
        if not errors and db.execute('SELECT 1 FROM users WHERE email = ?', (form['email'],)).fetchone():
            errors['email'] = 'Já existe uma conta com esse e-mail. Entre ou use outro e-mail.'
        if not errors:
            t = now()
            with transaction(db):
                cur = db.execute("INSERT INTO users (email, name, password_hash, role, permissions, created_at) VALUES (?,?,?,'cliente','',?)",
                                 (form['email'], form['name'], hash_password(pw), t))
                uid = cur.lastrowid
                # pedidos feitos antes com o mesmo e-mail, sem conta, passam para o histórico
                guest = db.execute('SELECT id FROM customers WHERE user_id IS NULL AND email = ? ORDER BY id LIMIT 1', (form['email'],)).fetchone()
                if guest:
                    db.execute('UPDATE customers SET user_id = ?, name = ?, phone = ? WHERE id = ?', (uid, form['name'], form['phone'], guest['id']))
                    db.execute('UPDATE orders SET customer_id = ? WHERE customer_id IN (SELECT id FROM customers WHERE user_id IS NULL AND email = ?)',
                               (guest['id'], form['email']))
                else:
                    db.execute('INSERT INTO customers (user_id, name, email, phone, created_at) VALUES (?,?,?,?,?)',
                               (uid, form['name'], form['email'], form['phone'], t))
            login_user(db.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone())
            flash('Conta criada. Seus pedidos ficam reunidos aqui.', 'ok')
            return redirect(url_for('account.my_orders'))
        flash('Revise os campos destacados.', 'error')
    return render_template('account/register.html', form=form, errors=errors)


@bp.get('/pedidos')
@login_required
def my_orders():
    if is_staff(g.user):
        return redirect(url_for(first_allowed_endpoint(g.user) or 'shop.index'))
    db = get_db()
    rows = db.execute('SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.user_id = ? ORDER BY o.id DESC',
                      (g.user['id'],)).fetchall()
    return render_template('account/orders.html', rows=rows, STATUS=orders.STATUS)
