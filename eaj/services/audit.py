import json

from flask import g, has_request_context, request

from ..db import get_db, now


def log(action, entity, entity_id=None, user_id=None, **details):
    """Registra alterações administrativas e financeiras."""
    if user_id is None and has_request_context() and g.get('user') is not None:
        user_id = g.user['id']
    ip = (request.remote_addr or '') if has_request_context() else ''
    get_db().execute(
        'INSERT INTO audit_log (user_id, action, entity, entity_id, details, ip, created_at) VALUES (?,?,?,?,?,?,?)',
        (user_id, action, entity, entity_id, json.dumps(details, ensure_ascii=False, default=str), ip, now()))
