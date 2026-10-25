"""Valores monetários em centavos inteiros e percentuais em pontos-base.

Nenhum cálculo financeiro usa float: a conversão de texto passa por Decimal
e os arredondamentos usam ROUND_HALF_UP explicitamente.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class MoneyError(ValueError):
    pass


def parse_money(text, allow_zero=True):
    """'30' | '30,00' | '1.234,56' | 'R$ 30,5' -> centavos (int)."""
    if text is None:
        raise MoneyError('Informe um valor.')
    s = str(text).strip().replace('R$', '').replace(' ', '')
    if not s:
        raise MoneyError('Informe um valor.')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise MoneyError('Valor inválido. Use o formato 1.234,56.')
    if not d.is_finite():
        raise MoneyError('Valor inválido.')
    if d < 0:
        raise MoneyError('O valor não pode ser negativo.')
    cents = int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if cents == 0 and not allow_zero:
        raise MoneyError('O valor precisa ser maior que zero.')
    return cents


def parse_percent_bp(text):
    """'50' | '12,5' -> pontos-base (5000 | 1250)."""
    s = str(text or '').strip().replace('%', '').replace(',', '.')
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise MoneyError('Percentual inválido.')
    if not d.is_finite() or d < 0 or d > 100:
        raise MoneyError('O percentual precisa estar entre 0 e 100.')
    return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def apply_bp(cents, bp):
    """Aplica um percentual em pontos-base, arredondando ao centavo."""
    return int((Decimal(cents) * Decimal(bp) / Decimal(10000)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def split_by_shares(total_cents, shares_bp):
    """Divide um valor entre participantes sem perder centavos.
    O resto do arredondamento vai para quem tem a maior fração descartada."""
    if not shares_bp:
        return []
    raw = [Decimal(total_cents) * Decimal(bp) / Decimal(10000) for bp in shares_bp]
    floors = [int(r) for r in raw]
    target = apply_bp(total_cents, sum(shares_bp))
    rest = target - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:max(0, rest)]:
        floors[i] += 1
    return floors


def fmt_brl(cents, signed=False):
    if cents is None:
        return '—'
    neg = cents < 0
    c = abs(int(cents))
    reais, cent = divmod(c, 100)
    s = f'{reais:,}'.replace(',', '.') + f',{cent:02d}'
    sign = '−' if neg else ('+' if signed and cents > 0 else '')
    return f'{sign}R$ {s}'


def fmt_input(cents):
    """Formato para preencher campos de formulário: 1234,56"""
    if cents is None:
        return ''
    reais, cent = divmod(int(cents), 100)
    return f'{reais},{cent:02d}'


def fmt_bp(bp, decimals=1):
    if bp is None:
        return '—'
    v = Decimal(bp) / Decimal(100)
    s = f'{v:.{decimals}f}'.replace('.', ',')
    if decimals and s.endswith(',' + '0' * decimals):
        s = s.split(',')[0]
    return s + '%'


def fmt_bp_input(bp):
    """Pontos-base para campo de formulário: 5000 -> '50', 1250 -> '12,5'."""
    v = Decimal(int(bp or 0)) / Decimal(100)
    s = f'{v:.2f}'.rstrip('0').rstrip('.')
    return s.replace('.', ',')


def ratio_bp(part, whole):
    """part/whole em pontos-base (None quando whole == 0)."""
    if not whole:
        return None
    return int((Decimal(part) * 10000 / Decimal(whole)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
