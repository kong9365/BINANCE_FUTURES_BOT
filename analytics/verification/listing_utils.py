"""신규 상장(onboardDate) 메타 — Binance exchangeInfo."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_onboard_dates(client) -> Dict[str, pd.Timestamp]:
    """USDT perpetual TRADING 심볼 → onboard UTC timestamp."""
    out: Dict[str, pd.Timestamp] = {}
    try:
        info = client.futures_exchange_info()
    except Exception as e:  # noqa: BLE001
        logger.error("[Listing] exchange_info 실패: %s", e)
        return out
    for s in info.get("symbols") or []:
        if s.get("status") != "TRADING":
            continue
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
            continue
        od = s.get("onboardDate")
        if not od:
            continue
        try:
            out[s["symbol"]] = pd.Timestamp(int(od), unit="ms", tz="UTC")
        except (TypeError, ValueError):
            continue
    return out


def protected_symbols() -> set[str]:
    from config.settings import PAIR_WHITELIST_CONFIG
    return set(PAIR_WHITELIST_CONFIG.protected_symbols)
