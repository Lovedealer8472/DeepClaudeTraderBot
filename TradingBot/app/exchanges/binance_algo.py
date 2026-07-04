"""
Binance USDⓈ-M Futures Algo Service client.

Post Dec-2025 migration: STOP_MARKET, TAKE_PROFIT_MARKET, STOP, TAKE_PROFIT,
and TRAILING_STOP_MARKET live on /fapi/v1/algoOrder (algoType=CONDITIONAL).
Old /fapi/v1/order returns -4120 STOP_ORDER_SWITCH_ALGO for these types.

This module provides payload builders, placement, query, cancel, and
reconciliation functions. All closePosition=true, no quantity, no reduceOnly.
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import uuid
import time


class AlgoStatus(str, Enum):
    """Binance Algo Service status lifecycle."""
    LOCAL_INTENT = "LOCAL_INTENT"      # Created locally, not yet submitted
    SUBMITTED = "SUBMITTED"            # REST call in flight
    NEW = "NEW"                        # Accepted by Algo Service
    CANCELED = "CANCELED"
    TRIGGERING = "TRIGGERING"
    TRIGGERED = "TRIGGERED"
    FINISHED = "FINISHED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    LOST = "LOST"                      # Local record not found on exchange


@dataclass
class AlgoOrder:
    """Typed representation of a Binance algo conditional order."""
    purpose: str  # "SL", "TP", "TRAIL"
    client_algo_id: str
    algo_id: Optional[str] = None
    symbol: str = ""
    side: str = ""                     # BUY or SELL
    order_type: str = ""               # STOP_MARKET, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET
    trigger_price: float = 0.0
    close_position: bool = True
    working_type: str = "MARK_PRICE"
    price_protect: bool = False
    callback_rate: Optional[float] = None  # For TRAILING_STOP_MARKET
    activate_price: Optional[float] = None
    status: AlgoStatus = AlgoStatus.LOCAL_INTENT
    actual_order_id: Optional[str] = None  # Set when TRIGGERED
    reject_reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# ═══════════════════════════════════════════
# PAYLOAD BUILDERS — pure functions, testable
# ═══════════════════════════════════════════

def build_close_position_sl(
    symbol: str,
    side: str,            # "SELL" to close long, "BUY" to close short
    trigger_price: float,
    working_type: str = "MARK_PRICE",
    price_protect: bool = False,
    client_algo_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a closePosition STOP_MARKET algo order payload.
    side="SELL" closes current long. side="BUY" closes current short.
    No quantity, no reduceOnly — Binance rejects them with closePosition=true.
    """
    if client_algo_id is None:
        client_algo_id = f"sl_{symbol.replace('/', '')}_{uuid.uuid4().hex[:8]}"
    return {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side.upper(),
        "type": "STOP_MARKET",
        "triggerPrice": round(trigger_price, _price_decimals(symbol)),
        "workingType": working_type,
        "closePosition": "true",
        "priceProtect": "true" if price_protect else "false",
        "newClientAlgoId": client_algo_id,
    }


def build_close_position_tp(
    symbol: str,
    side: str,
    trigger_price: float,
    working_type: str = "MARK_PRICE",
    price_protect: bool = False,
    client_algo_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a closePosition TAKE_PROFIT_MARKET algo order payload."""
    if client_algo_id is None:
        client_algo_id = f"tp_{symbol.replace('/', '')}_{uuid.uuid4().hex[:8]}"
    return {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side.upper(),
        "type": "TAKE_PROFIT_MARKET",
        "triggerPrice": round(trigger_price, _price_decimals(symbol)),
        "workingType": working_type,
        "closePosition": "true",
        "priceProtect": "true" if price_protect else "false",
        "newClientAlgoId": client_algo_id,
    }


def build_trailing_stop(
    symbol: str,
    side: str,
    quantity: float,
    callback_rate: float,   # 0.1 to 10.0 (percentage)
    activate_price: Optional[float] = None,
    working_type: str = "MARK_PRICE",
    client_algo_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a TRAILING_STOP_MARKET algo order payload.
    Server-side trailing — no bot intervention needed after placement.
    callback_rate: 0.1 = 0.1%, 1.0 = 1.0%, max 10.0.
    IMPORTANT: closePosition NOT supported on TRAILING_STOP_MARKET (returns -4136).
    Must use quantity + reduceOnly instead.
    """
    if client_algo_id is None:
        client_algo_id = f"trail_{symbol.replace('/', '')}_{uuid.uuid4().hex[:8]}"
    payload = {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side.upper(),
        "type": "TRAILING_STOP_MARKET",
        "callbackRate": round(callback_rate, 2),
        "workingType": working_type,
        "quantity": str(quantity),
        "reduceOnly": "true",
        "newClientAlgoId": client_algo_id,
    }
    if activate_price is not None:
        payload["activatePrice"] = round(activate_price, _price_decimals(symbol))
    return payload


# ═══════════════════════════════════════════
# EXCHANGE OPERATIONS
# ═══════════════════════════════════════════

async def place_algo_order(exchange, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Place a closePosition algo order via CCXT (auto-routes to /fapi/v1/algoOrder).
    CCXT ≥4.5.27 handles routing when stopPrice/triggerPrice is present.
    Call signature: exchange.create_order(symbol, type, side, amount, price, params)
    """
    try:
        symbol = payload["symbol"]
        order_type = payload["type"]
        side = payload["side"]
        # closePosition orders have no quantity — pass 0
        params = {
            "stopPrice": payload["triggerPrice"],
            "closePosition": True,
            "workingType": payload.get("workingType", "MARK_PRICE"),
            "priceProtect": payload.get("priceProtect", "false"),
        }
        if payload.get("newClientAlgoId"):
            params["clientOrderId"] = payload["newClientAlgoId"]
        return await exchange.create_order(symbol, order_type, side, 0, None, params)
    except Exception as e:
        err = str(e)
        if '4130' in err:
            return None  # Order already exists at same price — expected
        if '4120' in err:
            raise RuntimeError(
                "STOP_ORDER_SWITCH_ALGO: CCXT may be outdated (<4.5.27). "
                "Post-Dec 2025 migration requires Algo Service routing."
            ) from e
        raise


async def fetch_open_algo_orders(exchange, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """GET /fapi/v1/openAlgoOrders — returns open TP/SL and trailing algo orders."""
    params = {}
    if symbol:
        params["symbol"] = symbol
    try:
        return await exchange.fapiPrivateGetOpenAlgoOrders(params) or []
    except Exception:
        return []


async def fetch_algo_order(exchange, algo_id: Optional[str] = None,
                           client_algo_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """GET /fapi/v1/algoOrder — query a specific algo order by algoId or clientAlgoId."""
    params = {}
    if algo_id:
        params["algoId"] = algo_id
    if client_algo_id:
        params["clientAlgoId"] = client_algo_id
    try:
        return await exchange.fapiPrivateGetAlgoOrder(params)
    except Exception:
        return None


async def cancel_algo_order(exchange, algo_id: Optional[str] = None,
                            client_algo_id: Optional[str] = None) -> bool:
    """Cancel an algo order. Returns True if successful."""
    params = {}
    if algo_id:
        params["algoId"] = algo_id
    if client_algo_id:
        params["clientAlgoId"] = client_algo_id
    try:
        await exchange.fapiPrivateDeleteAlgoOrder(params)
        return True
    except Exception:
        return False


async def cancel_all_algo_orders(exchange, symbol: str) -> bool:
    """Cancel all algo orders for a symbol. Returns True if successful."""
    open_orders = await fetch_open_algo_orders(exchange, symbol)
    success = True
    for o in open_orders:
        aid = o.get("algoId")
        if aid:
            ok = await cancel_algo_order(exchange, algo_id=aid)
            if not ok:
                success = False
    return success


# ═══════════════════════════════════════════
# RECONCILIATION
# ═══════════════════════════════════════════

async def reconcile_algo_orders(
    exchange,
    expected_orders: List[AlgoOrder],
) -> Dict[str, AlgoOrder]:
    """
    Reconcile local AlgoOrder records against exchange truth.
    Returns a dict of {client_algo_id: AlgoOrder} with status updated from exchange.
    """
    result = {}
    for ao in expected_orders:
        exchange_order = await fetch_algo_order(exchange, client_algo_id=ao.client_algo_id)
        if exchange_order:
            ao.algo_id = exchange_order.get("algoId")
            ao.status = AlgoStatus(exchange_order.get("algoStatus", "NEW").upper())
            ao.actual_order_id = exchange_order.get("actualOrderId")
            ao.reject_reason = exchange_order.get("rejectReason")
            ao.updated_at = time.time()
        else:
            # Not found on exchange — mark as LOST if it was previously SUBMITTED or NEW
            if ao.status in (AlgoStatus.SUBMITTED, AlgoStatus.NEW):
                ao.status = AlgoStatus.LOST
                ao.updated_at = time.time()
        result[ao.client_algo_id] = ao
    return result


async def startup_algo_reconciliation(exchange, symbols: List[str]) -> Dict[str, List[AlgoOrder]]:
    """
    Full startup reconciliation: fetch open algo orders and positions.
    Returns {symbol: [AlgoOrder, ...]} for all symbols with open protection.
    """
    result = {}
    open_algos = await fetch_open_algo_orders(exchange)
    for o in open_algos:
        sym = o.get("symbol", "")
        if symbols and sym not in symbols:
            continue
        ao = AlgoOrder(
            purpose=_infer_purpose(o),
            client_algo_id=o.get("clientAlgoId", ""),
            algo_id=o.get("algoId"),
            symbol=sym,
            side=o.get("side", ""),
            order_type=o.get("type", ""),
            trigger_price=float(o.get("triggerPrice", 0)),
            close_position=o.get("closePosition") == "true",
            status=AlgoStatus(o.get("algoStatus", "NEW").upper()),
            actual_order_id=o.get("actualOrderId"),
        )
        result.setdefault(sym, []).append(ao)
    return result


# ═══════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════

def _price_decimals(symbol: str) -> int:
    """Estimate price precision from symbol. Default 4 decimals for USDT-M perps."""
    # In production, get this from exchange.markets[symbol]['precision']['price']
    return 4


def _infer_purpose(order: Dict[str, Any]) -> str:
    """Infer purpose from algo order type and side."""
    t = order.get("type", "")
    if "TRAILING" in t:
        return "TRAIL"
    if "TAKE_PROFIT" in t:
        return "TP"
    if "STOP" in t:
        return "SL"
    return "UNKNOWN"


def close_side(position_side: str) -> str:
    """Return the close side for a position. Long → SELL, Short → BUY."""
    return "SELL" if position_side.lower() == "long" else "BUY"
