from __future__ import annotations

from typing import Dict, Any, Tuple, Optional
from .config import load_config


class MissingCredentials(Exception):
    pass


def get_lighter_signer_params() -> Tuple[str, int, int]:
    """Return (private_key, account_index, api_key_index) from env/config.

    Raises MissingCredentials if any value is missing.
    """
    cfg = load_config()
    auth = cfg.get("auth", {}).get("lighter", {})
    pk = auth.get("private_key")
    ai = auth.get("account_index")
    ki = auth.get("api_key_index")
    if not pk or ai is None or ki is None:
        raise MissingCredentials("Missing Lighter credentials: set LIGHTER_API_KEY_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX in environment/.env")
    return str(pk), int(ai), int(ki)


def get_aster_futures_signer() -> Tuple[str, str, str]:
    """Return (user, signer, ecdsa_private_key) for Aster futures.

    Raises MissingCredentials if any value is missing.
    """
    cfg = load_config()
    auth = cfg.get("auth", {}).get("aster", {})
    user = auth.get("user")
    signer = auth.get("signer")
    pk = auth.get("ecdsa_private_key")
    if not user or not signer or not pk:
        raise MissingCredentials("Missing Aster futures credentials: set ASTER_USER, ASTER_SIGNER, ASTER_ECDSA_PRIVATE_KEY in environment/.env")
    return str(user), str(signer), str(pk)

