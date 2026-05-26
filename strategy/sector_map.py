"""
strategy/sector_map.py
=====================================================================
심볼 → 섹터 정적 매핑 (CCS-Lite v1.1 kill gate 7 데이터 소스).

목적:
  동일 섹터(MEME/AI/L2/DeFi/RWA/INFRA/...) 종목을 *이미* 보유 중이면 같은 섹터
  신규 진입을 차단해 단일 내러티브 동조 손실을 방지한다(2024년 MEME·AI 동반
  급락 교훈). 외부 API/DB 의존이 늘면 자유도와 장애점이 늘어나므로 **정적 dict**
  + 정기 수동 갱신 가이드만 유지한다.

설계:
  - 순수 데이터(상수 dict) + 두 헬퍼만 — 외부 의존 0, I/O 0.
  - 미등록 심볼은 `None`(섹터 미상) — 호출자가 보수적으로 *동일 섹터 미판정*
    (= 차단 안 함)으로 처리하든, 별도 'UNKNOWN' 버킷으로 묶든 선택 가능.
  - 등록 기준: Binance USDT-M Perp 상장 + CoinGecko/CoinMarketCap 카테고리
    + 운영자가 트레이딩 시 묶음으로 보는 그룹. 정확성보다 *동조 위험 분류*가 목적.

갱신 가이드(분기 1회):
  1) CoinGecko `coins/categories` 의 상위 100 카테고리 훑기.
  2) Binance Futures 신규 상장 페어(`/fapi/v1/exchangeInfo`) 중 미등록 추가.
  3) 카테고리 모호 시 `OTHER`(거시·기반 자산) 또는 None(미분류) 사용.
=====================================================================
"""

from __future__ import annotations

from typing import Dict, Optional


# ── 섹터 카테고리 상수 ──────────────────────────────────────────────
# 운영자 분류 직관 + CoinGecko 주요 카테고리 정합.
SECTOR_MEME = "MEME"           # 1000PEPE, DOGE, SHIB, FLOKI, WIF, BONK
SECTOR_AI = "AI"               # FET, AGIX, RNDR, TAO, WLD
SECTOR_L1 = "L1"               # SOL, AVAX, NEAR, APT, SUI, ATOM, ADA, DOT, TRX, BNB
SECTOR_L2 = "L2"               # ARB, OP, MATIC, STRK, MANTA, IMX
SECTOR_DEFI = "DEFI"           # UNI, AAVE, MKR, SNX, CRV, LDO, GMX, JUP, SUSHI, COMP
SECTOR_RWA = "RWA"             # ONDO, POLYX, OM (Mantra), USTC...
SECTOR_INFRA = "INFRA"         # LINK(오라클이지만 INFRA로 분류), GRT, RPL, ENS, MKR(중복), JTO
SECTOR_ORACLE = "ORACLE"       # LINK 단독 분류 가능 — 여기선 ORACLE 우선
SECTOR_PAYMENT = "PAYMENT"     # XRP, XLM, BCH, LTC, ALGO
SECTOR_PRIVACY = "PRIVACY"     # XMR, ZEC, DASH (Binance 상장은 일부)
SECTOR_GAMING = "GAMING"       # AXS, SAND, MANA, GALA, ENJ, ILV
SECTOR_STORAGE = "STORAGE"     # FIL, AR, STX
SECTOR_EXCHANGE = "EXCHANGE"   # BNB(중복 — L1 우선), CAKE, GMT(거래량 토큰 등)
SECTOR_BTC = "BTC"             # BTC, WBTC(없지만)
SECTOR_ETH = "ETH"             # ETH, STETH(없지만)
SECTOR_DEPIN = "DEPIN"         # IO, HNT, MOBILE
SECTOR_OTHER = "OTHER"         # 분류 모호 — 매크로/기반/명확한 그룹 외

# ── 정적 매핑 (USDT 페어 키) ────────────────────────────────────────
# 우선순위: 1) 트레이더 동조 직관 2) CoinGecko 첫 카테고리.
# 같은 종목이 여러 그룹 후보면 *가장 강한 내러티브* 1개 선택.
_SYMBOL_TO_SECTOR: Dict[str, str] = {
    # ── BTC / ETH (단독 카테고리) ──
    "BTCUSDT": SECTOR_BTC,
    "ETHUSDT": SECTOR_ETH,

    # ── L1 (메인넷 기반 자산) ──
    "SOLUSDT": SECTOR_L1,
    "AVAXUSDT": SECTOR_L1,
    "NEARUSDT": SECTOR_L1,
    "APTUSDT": SECTOR_L1,
    "SUIUSDT": SECTOR_L1,
    "ATOMUSDT": SECTOR_L1,
    "ADAUSDT": SECTOR_L1,
    "DOTUSDT": SECTOR_L1,
    "TRXUSDT": SECTOR_L1,
    "BNBUSDT": SECTOR_L1,
    "TONUSDT": SECTOR_L1,
    "SEIUSDT": SECTOR_L1,
    "INJUSDT": SECTOR_L1,
    "TIAUSDT": SECTOR_L1,
    "FTMUSDT": SECTOR_L1,
    "EGLDUSDT": SECTOR_L1,
    "ICPUSDT": SECTOR_L1,
    "FLOWUSDT": SECTOR_L1,
    "KLAYUSDT": SECTOR_L1,
    "HBARUSDT": SECTOR_L1,
    "VETUSDT": SECTOR_L1,
    "ETCUSDT": SECTOR_L1,

    # ── L2 / 스케일링 ──
    "ARBUSDT": SECTOR_L2,
    "OPUSDT": SECTOR_L2,
    "MATICUSDT": SECTOR_L2,
    "STRKUSDT": SECTOR_L2,
    "MANTAUSDT": SECTOR_L2,
    "IMXUSDT": SECTOR_L2,
    "METISUSDT": SECTOR_L2,
    "BLASTUSDT": SECTOR_L2,
    "ZKUSDT": SECTOR_L2,
    "ZROUSDT": SECTOR_L2,

    # ── MEME ──
    "DOGEUSDT": SECTOR_MEME,
    "SHIBUSDT": SECTOR_MEME,
    "1000PEPEUSDT": SECTOR_MEME,
    "PEPEUSDT": SECTOR_MEME,
    "1000FLOKIUSDT": SECTOR_MEME,
    "FLOKIUSDT": SECTOR_MEME,
    "WIFUSDT": SECTOR_MEME,
    "BONKUSDT": SECTOR_MEME,
    "1000BONKUSDT": SECTOR_MEME,
    "BOMEUSDT": SECTOR_MEME,
    "MEMEUSDT": SECTOR_MEME,
    "DOGSUSDT": SECTOR_MEME,
    "NEIROUSDT": SECTOR_MEME,
    "1000SATSUSDT": SECTOR_MEME,
    "POPCATUSDT": SECTOR_MEME,
    "MEWUSDT": SECTOR_MEME,
    "MOODENGUSDT": SECTOR_MEME,
    "BRETTUSDT": SECTOR_MEME,
    "TURBOUSDT": SECTOR_MEME,
    "PNUTUSDT": SECTOR_MEME,

    # ── AI ──
    "FETUSDT": SECTOR_AI,
    "AGIXUSDT": SECTOR_AI,
    "RNDRUSDT": SECTOR_AI,
    "RENDERUSDT": SECTOR_AI,
    "TAOUSDT": SECTOR_AI,
    "WLDUSDT": SECTOR_AI,
    "OCEANUSDT": SECTOR_AI,
    "ARKMUSDT": SECTOR_AI,
    "GRTUSDT": SECTOR_AI,  # The Graph(인덱싱이지만 AI 내러티브로 동조)
    "IOUSDT": SECTOR_AI,   # io.net
    "PHAUSDT": SECTOR_AI,
    "NMRUSDT": SECTOR_AI,
    "AIUSDT": SECTOR_AI,
    "AKTUSDT": SECTOR_AI,

    # ── DeFi ──
    "UNIUSDT": SECTOR_DEFI,
    "AAVEUSDT": SECTOR_DEFI,
    "MKRUSDT": SECTOR_DEFI,
    "SNXUSDT": SECTOR_DEFI,
    "CRVUSDT": SECTOR_DEFI,
    "LDOUSDT": SECTOR_DEFI,
    "GMXUSDT": SECTOR_DEFI,
    "JUPUSDT": SECTOR_DEFI,
    "SUSHIUSDT": SECTOR_DEFI,
    "COMPUSDT": SECTOR_DEFI,
    "1INCHUSDT": SECTOR_DEFI,
    "DYDXUSDT": SECTOR_DEFI,
    "PENDLEUSDT": SECTOR_DEFI,
    "ENAUSDT": SECTOR_DEFI,
    "ETHFIUSDT": SECTOR_DEFI,
    "EIGENUSDT": SECTOR_DEFI,
    "RPLUSDT": SECTOR_DEFI,  # Rocket Pool

    # ── RWA ──
    "ONDOUSDT": SECTOR_RWA,
    "POLYXUSDT": SECTOR_RWA,
    "OMUSDT": SECTOR_RWA,
    "TRUUSDT": SECTOR_RWA,

    # ── Oracle ──
    "LINKUSDT": SECTOR_ORACLE,
    "PYTHUSDT": SECTOR_ORACLE,
    "APIUSDT": SECTOR_ORACLE,

    # ── Payment ──
    "XRPUSDT": SECTOR_PAYMENT,
    "XLMUSDT": SECTOR_PAYMENT,
    "BCHUSDT": SECTOR_PAYMENT,
    "LTCUSDT": SECTOR_PAYMENT,
    "ALGOUSDT": SECTOR_PAYMENT,

    # ── Privacy ──
    "XMRUSDT": SECTOR_PRIVACY,
    "ZECUSDT": SECTOR_PRIVACY,
    "DASHUSDT": SECTOR_PRIVACY,

    # ── Gaming ──
    "AXSUSDT": SECTOR_GAMING,
    "SANDUSDT": SECTOR_GAMING,
    "MANAUSDT": SECTOR_GAMING,
    "GALAUSDT": SECTOR_GAMING,
    "ENJUSDT": SECTOR_GAMING,
    "ILVUSDT": SECTOR_GAMING,
    "BIGTIMEUSDT": SECTOR_GAMING,
    "PIXELUSDT": SECTOR_GAMING,
    "PORTALUSDT": SECTOR_GAMING,
    "MAVIAUSDT": SECTOR_GAMING,

    # ── Storage ──
    "FILUSDT": SECTOR_STORAGE,
    "ARUSDT": SECTOR_STORAGE,
    "STXUSDT": SECTOR_STORAGE,

    # ── Exchange / Infra ──
    "CAKEUSDT": SECTOR_EXCHANGE,
    "GMTUSDT": SECTOR_EXCHANGE,

    # ── DePIN ──
    "HNTUSDT": SECTOR_DEPIN,
    "MOBILEUSDT": SECTOR_DEPIN,

    # ── 기타(분류 모호하지만 등록은 됨) ──
    "INJUSDT": SECTOR_L1,  # 중복 보장(보호종목)
}


def get_sector(symbol: str) -> Optional[str]:
    """심볼 → 섹터. 미등록이면 None.

    Args:
        symbol: Binance USDT 페어(대문자, 예 "SOLUSDT").

    Returns:
        섹터 문자열 또는 None.
    """
    if not symbol:
        return None
    return _SYMBOL_TO_SECTOR.get(symbol.upper())


def same_sector(a: str, b: str) -> bool:
    """두 심볼이 동일 섹터인지. 어느 한쪽이라도 미등록이면 False(차단 안 함).

    의도: 미등록 종목은 보수적으로 *판정 불가* → 차단하지 않음. 운영자가 후속에
    sector_map 에 등록할 때 자연스럽게 활성화된다.
    """
    if not a or not b:
        return False
    sa = get_sector(a)
    sb = get_sector(b)
    if sa is None or sb is None:
        return False
    return sa == sb


def is_any_held_same_sector(candidate: str, held_symbols) -> bool:
    """후보 심볼과 동일 섹터인 보유 종목이 하나라도 있으면 True.

    Args:
        candidate: 진입 후보 심볼.
        held_symbols: 현재 보유 종목 iterable(set/list/tuple).

    Returns:
        bool. held_symbols 가 비었거나 후보 미등록이면 False.
    """
    if not candidate or not held_symbols:
        return False
    cand_sector = get_sector(candidate)
    if cand_sector is None:
        return False
    for h in held_symbols:
        if h == candidate:
            continue
        if get_sector(h) == cand_sector:
            return True
    return False
