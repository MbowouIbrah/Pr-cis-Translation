"""Limitation de debit et cle d'API du frontend.

Isole dans son propre module parce que TOUTES les routes en dependent : le
laisser dans la fabrique d'application obligeait chaque routeur a importer
l'application elle-meme -- exactement le cycle qu'on cherche a eviter.

`limiter` vaut None si slowapi n'est pas installe : `rate_limit_decorator`
rend alors un decorateur neutre, et rien d'autre ne change.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.config import FRONTEND_API_KEY, note


try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address)
except ImportError:
    limiter = None
    note(logging.WARNING, "Slowapi absent : limitation de débit désactivée.")

def rate_limit_decorator(limit_str: str):
    if limiter:
        return limiter.limit(limit_str)
    return lambda f: f


def verify_api_key(x_api_key: str = None):
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")
    if x_api_key != FRONTEND_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid X-API-Key")
