"""
Feature cache pre-warmer — ensures AdvancedFeatures are available at startup.

Without pre-warming, advanced_features.py needs 200+ candles for HTF features.
First hour of trading operates with features=None, degrading signal quality.
This module fetches OHLCV for top-N volume symbols during startup to populate
the feature cache before the first scan cycle.
"""

import asyncio
import time
from typing import Optional, List


async def prewarm_feature_cache(
    feature_engine,
    exchange_wrapper,
    top_n: int = 20,
    max_concurrent: int = 5,
) -> int:
    """
    Pre-compute AdvancedFeatures for top-N symbols by 24h volume.

    Args:
        feature_engine: FeatureEngine instance
        exchange_wrapper: Exchange wrapper (for symbol list and OHLCV fetch)
        top_n: Number of top-volume symbols to pre-warm
        max_concurrent: Max concurrent OHLCV fetches

    Returns:
        Number of symbols successfully pre-warmed
    """
    logger = None
    try:
        from .logger import get_logger
        logger = get_logger("Prewarmer")
    except Exception:
        pass

    if feature_engine is None or exchange_wrapper is None:
        return 0

    # Get tickers sorted by 24h volume
    try:
        tickers = await exchange_wrapper.fetch_tickers()
    except Exception:
        if logger:
            logger.warning("[PREWARM] Could not fetch tickers — skipping")
        return 0

    # Sort by quote volume descending
    ranked = sorted(
        [(sym, float(t.get('quoteVolume', 0) or 0))
         for sym, t in tickers.items() if t.get('quoteVolume')],
        key=lambda x: x[1],
        reverse=True,
    )[:top_n]

    if not ranked:
        return 0

    symbols = [s for s, _ in ranked]
    if logger:
        logger.info(f"[PREWARM] Pre-computing features for {len(symbols)} top-volume symbols...")

    sem = asyncio.Semaphore(max_concurrent)
    warmed = 0
    start = time.time()

    async def warm_one(symbol: str):
        nonlocal warmed
        async with sem:
            try:
                await feature_engine.get_features(
                    symbol=symbol,
                    ltf_timeframe="1h",
                    htf_timeframe="4h",
                    ltf_limit=200,
                    htf_limit=200,
                )
                warmed += 1
            except Exception:
                pass

    await asyncio.gather(*[warm_one(s) for s in symbols], return_exceptions=True)

    elapsed = time.time() - start
    if logger:
        logger.info(f"[PREWARM] {warmed}/{len(symbols)} symbols warmed in {elapsed:.1f}s")

    return warmed
