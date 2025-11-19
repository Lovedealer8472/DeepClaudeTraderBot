"""
Exchange Factory - Creates exchange instances based on configuration.
"""
from typing import Optional
from .base import ExchangeBase
from .binance_futures import BinanceFuturesExchange
from ..logger import get_logger


def create_exchange(config=None) -> BinanceFuturesExchange:
    """
    Create exchange instance based on configuration.
    
    Args:
        config: Configuration object with EXCHANGE setting (optional)
        
    Returns:
        BinanceFuturesExchange instance (only binance_futures supported)
        
    Raises:
        ValueError: If exchange type is not supported
    """
    logger = get_logger("ExchangeFactory")
    
    # Get exchange type from config (default to binance_futures)
    if config:
        exchange_type = getattr(config, 'EXCHANGE', 'binance_futures').lower()
    else:
        from ..config import EXCHANGE
        exchange_type = EXCHANGE.lower()
    
    logger.info(f"Creating exchange: {exchange_type}")
    
    if exchange_type == "binance_futures":
        return BinanceFuturesExchange()
    else:
        raise ValueError(f"Unsupported exchange: {exchange_type}. Only binance_futures is supported.")

