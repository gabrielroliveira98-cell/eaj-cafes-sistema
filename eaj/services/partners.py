"""Participação societária e distribuição de resultados.

A participação (por exemplo 50% / 50%) se aplica ao VALOR APROVADO PARA DISTRIBUIÇÃO,
nunca à receita bruta. O caminho é sempre:
  resultado líquido estimado − reserva de caixa = valor disponível → base aprovada → divisão entre sócios.
Nenhuma transferência bancária é feita pelo sistema: a aprovação apenas registra a decisão.
"""
from ..db import now, transaction
from ..money import apply_bp, split_by_shares
from . import BusinessError, analytics, audit


def list_partners(db, active_only=True):
    return db.execute('SELECT * FROM partners' + (' WHERE active = 1' if active_only else '') + ' ORDER BY id').fetchall()


def update_shares(db, shares, user_id):
    """shares: {partner_id: share_bp}. A soma precisa ser exatamente 100%."""
    if sum(shares.values()) != 10000:
        raise BusinessError('A soma das participações precisa ser exatamente 100%.')
    with transaction(db):
        old = {p['id']: p['share_bp'] for p in list_partners(db)}
        for pid, bp in shares.items():
            if pid not in old:
                raise BusinessError('Participante não encontrado.')
            db.execute('UPDATE partners SET share_bp = ? WHERE id = ?', (bp, pid))
        audit.log('alterar_participacao', 'partners', None, user_id=user_id, antes=old, depois=shares)


def reserve_balance(db):
    r = db.execute("SELECT COALESCE(SUM(CASE WHEN kind = 'constituicao' THEN amount_cents ELSE -amount_cents END),0) FROM cash_reserves").fetchone()
    return r[0]


def simulate(db, start, end, reserve_bp, approved_base_cents=None):
    d = analytics.dre(db, start, end)
    net = d['net_result']
    positive = max(0, net)
    reserve = apply_bp(positive, reserve_bp)
    available = positive - reserve
    base = available if approved_base_cents is None else approved_base_cents
    warnings = list(d['notes'])
    if net <= 0:
        warnings.append('O resultado líquido estimado do período não é positivo: não há valor a distribuir.')
    if approved_base_cents is not None and approved_base_cents > available:
        warnings.append('A base informada é maior que o valor disponível depois da reserva. Reduza a base ou a reserva.')
    partners = list_partners(db)
    amounts = split_by_shares(max(0, base), [p['share_bp'] for p in partners])
    shares = [{'partner_id': p['id'], 'name': p['name'], 'share_bp': p['share_bp'], 'amount_cents': a}
              for p, a in zip(partners, amounts)]
    overlap = db.execute("SELECT id, period_start, period_end FROM distributions WHERE status = 'aprovada' "
                         'AND period_start <= ? AND period_end >= ?', (end.isoformat(), start.isoformat())).fetchone()
    if overlap:
        warnings.append(f'Já existe uma distribuição aprovada que cobre parte deste período '
                        f'({overlap["period_start"]} a {overlap["period_end"]}). Aprovar de novo distribuiria o mesmo resultado duas vezes.')
    return {'dre': d, 'start': start, 'end': end, 'reserve_bp': reserve_bp, 'reserve_cents': reserve,
            'available_cents': available, 'base_cents': max(0, base), 'shares': shares, 'warnings': warnings,
            'overlap': overlap, 'can_approve': net > 0 and 0 < base <= available and not overlap
                                                and sum(p['share_bp'] for p in partners) == 10000}


def approve(db, sim, notes, user_id):
    if not sim['can_approve']:
        raise BusinessError('Esta simulação não pode ser aprovada. Revise os avisos.')
    d = sim['dre']
    t = now()
    with transaction(db):
        cur = db.execute(
            'INSERT INTO distributions (period_start, period_end, gross_revenue_cents, deductions_cents, cogs_cents, gross_profit_cents, '
            'operating_expenses_cents, other_income_cents, net_result_cents, reserve_bp, reserve_cents, approved_base_cents, status, '
            "notes, approved_by, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'aprovada',?,?,?)",
            (sim['start'].isoformat(), sim['end'].isoformat(), d['gross_revenue'], d['deductions'], d['cogs'], d['gross_profit'],
             d['opex'], d['other_income'], d['net_result'], sim['reserve_bp'], sim['reserve_cents'], sim['base_cents'],
             notes.strip()[:600], user_id, t))
        did = cur.lastrowid
        for s in sim['shares']:
            db.execute('INSERT INTO distribution_shares (distribution_id, partner_id, partner_name, share_bp, amount_cents) VALUES (?,?,?,?,?)',
                       (did, s['partner_id'], s['name'], s['share_bp'], s['amount_cents']))
        if sim['reserve_cents'] > 0:
            db.execute("INSERT INTO cash_reserves (kind, amount_cents, distribution_id, note, created_by, created_at) VALUES ('constituicao',?,?,?,?,?)",
                       (sim['reserve_cents'], did, 'Reserva da distribuição aprovada', user_id, t))
        audit.log('aprovar_distribuicao', 'distribution', did, user_id=user_id, periodo=[sim['start'], sim['end']],
                  resultado=d['net_result'], reserva=sim['reserve_cents'], base=sim['base_cents'],
                  divisao={s['name']: s['amount_cents'] for s in sim['shares']})
    return did


def cancel(db, distribution_id, reason, user_id):
    if not (reason or '').strip():
        raise BusinessError('Informe o motivo do cancelamento.')
    with transaction(db):
        d = db.execute('SELECT * FROM distributions WHERE id = ?', (distribution_id,)).fetchone()
        if d is None or d['status'] != 'aprovada':
            raise BusinessError('Distribuição não encontrada ou já cancelada.')
        db.execute("UPDATE distributions SET status = 'cancelada', cancelled_at = ?, notes = TRIM(notes || ' | Cancelada: ' || ?) WHERE id = ?",
                   (now(), reason.strip()[:300], distribution_id))
        if d['reserve_cents'] > 0:
            db.execute("INSERT INTO cash_reserves (kind, amount_cents, distribution_id, note, created_by, created_at) VALUES ('estorno',?,?,?,?,?)",
                       (d['reserve_cents'], distribution_id, 'Estorno da reserva por cancelamento da distribuição', user_id, now()))
        audit.log('cancelar_distribuicao', 'distribution', distribution_id, user_id=user_id, motivo=reason.strip())


def register_reserve_use(db, amount_cents, note, user_id):
    if amount_cents <= 0:
        raise BusinessError('Informe o valor.')
    if amount_cents > reserve_balance(db):
        raise BusinessError('O valor é maior que o saldo da reserva.')
    if not (note or '').strip():
        raise BusinessError('Descreva o uso da reserva.')
    db.execute("INSERT INTO cash_reserves (kind, amount_cents, note, created_by, created_at) VALUES ('uso',?,?,?,?)",
               (amount_cents, note.strip()[:300], user_id, now()))
    audit.log('usar_reserva', 'cash_reserve', None, user_id=user_id, valor=amount_cents, nota=note.strip())
