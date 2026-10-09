"""Deterministic customer-facing confirmations from *verified* specialist data.

Never generate a bank balance in an LLM or frontend.  These messages use only
completed specialist results and fresh read-back data.
"""
from decimal import Decimal
import re


def money(value, language='en'):
    formatted = f'{Decimal(str(value)):,.2f}'
    if language == 'es':
        formatted = formatted.replace(',', '_').replace('.', ',').replace('_', '.')
    return f'R$ {formatted}'


def parse_brl_amount(message):
    """Parse BRL amounts, not unspecified numbers (IDs, dates, card digits)."""
    matches = re.findall(r'(?:R\s*\$|BRL)\s*([0-9][0-9.,]*)', message, re.I)
    if not matches or len(set(matches)) > 1:
        return None  # Multiple amounts require clarification; never guess which is the Pix.
    raw = matches[0].rstrip(',.')
    if '.' in raw and ',' in raw:
        decimal_separator = '.' if raw.rfind('.') > raw.rfind(',') else ','
        thousands_separator = ',' if decimal_separator == '.' else '.'
        raw = raw.replace(thousands_separator, '').replace(decimal_separator, '.')
    elif ',' in raw or '.' in raw:
        sep = ',' if ',' in raw else '.'
        parts = raw.split(sep)
        if len(parts[-1]) == 2 and len(parts) == 2:
            raw = parts[0] + '.' + parts[1]
        else:
            raw = ''.join(parts)
    try:
        amount = Decimal(raw)
        if amount > 0 and amount.as_tuple().exponent >= -2:
            return amount.quantize(Decimal('0.01'))
    except Exception:
        pass
    return None


def confirmed(action, outcome):
    allowed = {'execute_pix': 'completed', 'block_card': 'blocked', 'open_dispute': 'under_review'}
    return action in allowed and bool(outcome.get('id')) and outcome.get('status') == allowed[action]


def response(action, outcome, specialist_results, language='en'):
    """Return (customer_text, read_back_verified). No speculative read-back."""
    lang = 'es' if language == 'es' else 'en'
    by_role = {x.get('agent'): x for x in specialist_results}
    rid = outcome['id']
    if action == 'execute_pix':
        account = by_role.get('account', {}).get('data', {})
        wellness = by_role.get('wellness', {}).get('data', {})
        records = account.get('payments', [])
        payment = next((x for x in records if x.get('id') == rid and x.get('status') == 'completed'), None)
        verified = bool(payment and Decimal(str(payment['amount'])) == Decimal(str(outcome['amount'])))
        verified &= ('account' in account and 'projected' in wellness and
                     'obligations' in wellness and 'balance' in wellness)
        if verified:
            balance = Decimal(str(account['account']['balance']))
            obligations = Decimal(str(wellness['obligations']))
            projected = Decimal(str(wellness['projected']))
            verified = (balance == Decimal(str(wellness['balance'])) and projected == balance - obligations)
        if not verified:
            return ((f'✅ Pago Pix completado (referencia {rid}). No pude verificar el saldo actualizado; revisa tu cuenta.'
                     if lang == 'es' else
                     f'✅ Pix completed (reference {rid}). I could not verify the updated balance; check your account dashboard.'), False)
        beneficiaries = account.get('beneficiaries', [])
        name = next((b['name'] for b in beneficiaries if b['id'] == outcome.get('beneficiary_id')), None)
        name = name or ('el beneficiario verificado' if lang == 'es' else 'the verified beneficiary')
        amount = money(outcome['amount'], lang)
        balance_text = money(balance, lang)
        obligations_text = money(obligations, lang)
        projection_text = money(projected, lang)
        if lang == 'es':
            text = (f'✅ Tu pago Pix de {amount} a {name} se completó correctamente.\n\n'
                    f'Saldo actualizado: {balance_text}.\n'
                    f'Compromisos pendientes: {obligations_text}.\n'
                    f'Disponible proyectado después de esos compromisos: {projection_text}.\n\n'
                    f'Referencia: {rid}. Es una simulación; no se movieron fondos reales. [POL-FIN-001, v1.0]')
        else:
            text = (f'✅ Your Pix payment of {amount} to {name} was completed successfully.\n\n'
                    f'Updated checking balance: {balance_text}.\n'
                    f'Upcoming commitments: {obligations_text}.\n'
                    f'Projected available funds after those commitments: {projection_text}.\n\n'
                    f'Reference: {rid}. This is a simulation; no real funds moved. [POL-FIN-001, v1.0]')
        return text, True
    if action == 'block_card':
        card = by_role.get('card', {}).get('data', {}).get('card', {})
        verified = card.get('id') == rid and card.get('status') == 'blocked'
        end = outcome.get('last4', '••••')
        return ((f'✅ Tarjeta terminada en {end} bloqueada correctamente. Revisa la pestaña Tarjetas.'
                 if lang == 'es' else
                 f'✅ Card ending {end} is now blocked. You can verify it in Cards.'), verified)
    if action == 'open_dispute':
        disputes = by_role.get('dispute', {}).get('data', {}).get('disputes', [])
        verified = any(x.get('id') == rid and x.get('status') == 'under_review' for x in disputes)
        return ((f'✅ Tu reclamación {rid} está registrada y en revisión. No se ha abonado ningún reembolso automático.'
                 if lang == 'es' else
                 f'✅ Dispute {rid} is now registered and under review. No automatic refund has been issued.'), verified)
    raise ValueError('Unrecognized action')
