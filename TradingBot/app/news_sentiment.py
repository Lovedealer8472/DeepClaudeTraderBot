"""
Crypto news sentiment — local LLM via Ollama or keyword fallback.

Fetches headlines from free RSS feeds / APIs, runs sentiment analysis,
and exposes a per-symbol sentiment score for confluence scoring.

Mode 1 (Ollama): llama3.2-3b running locally analyzes headlines
Mode 2 (fallback): keyword-based sentiment (free, no GPU needed)

Both modes output: BULLISH (+10pts), BEARISH (+10pts for shorts), NEUTRAL (0pts)
"""

import asyncio
import json
import re
import time
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from collections import deque


@dataclass
class SentimentResult:
    symbol: str
    sentiment: str  # "BULLISH", "BEARISH", "NEUTRAL"
    confidence: float  # 0.0 - 1.0
    source_count: int   # How many headlines contributed
    headlines: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class NewsSentiment:
    """
    Fetches crypto news and runs sentiment analysis.
    Caches results per symbol with a 15-minute TTL.
    """

    # Free RSS feeds — no API key required
    RSS_FEEDS = [
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/",
        "https://cryptopotato.com/feed/",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]

    # Simple keyword-based sentiment (fallback mode)
    BULLISH_KEYWORDS = [
        "surge", "rally", "breakout", "bullish", "upgrade", "partnership",
        "launch", "adoption", "institutional", "etf", "approve", "record",
        "positive", "growth", "expansion", "accumulation", "support", "bounce",
        "reversal", "breakthrough", "listing", "integration", "mainnet",
    ]
    BEARISH_KEYWORDS = [
        "crash", "hack", "exploit", "sec", "lawsuit", "ban", "regulate",
        "crackdown", "bearish", "downgrade", "selloff", "liquidation",
        "decline", "loss", "debt", "default", "delay", "suspend", "halt",
        "warning", "risk", "volatile", "uncertain", "fud",
    ]

    # Token name → common ticker mapping for headline matching
    TOKEN_MAP = {
        "bitcoin": "BTC/USDT",
        "btc": "BTC/USDT",
        "ethereum": "ETH/USDT",
        "eth": "ETH/USDT",
        "solana": "SOL/USDT",
        "sol": "SOL/USDT",
        "ripple": "XRP/USDT",
        "xrp": "XRP/USDT",
        "cardano": "ADA/USDT",
        "ada": "ADA/USDT",
        "avalanche": "AVAX/USDT",
        "avax": "AVAX/USDT",
        "polygon": "MATIC/USDT",
        "matic": "MATIC/USDT",
        "chainlink": "LINK/USDT",
        "link": "LINK/USDT",
        "near": "NEAR/USDT",
        "dogecoin": "DOGE/USDT",
        "doge": "DOGE/USDT",
        "litecoin": "LTC/USDT",
        "ltc": "LTC/USDT",
    }

    def __init__(self, use_ollama: bool = False, ollama_model: str = "llama3.2:3b"):
        self._cache: Dict[str, SentimentResult] = {}
        self._cache_ttl = 900  # 15 minutes
        self._use_ollama = use_ollama
        self._ollama_model = ollama_model
        self._headline_history: deque = deque(maxlen=500)  # Dedup seen headlines

    async def get_sentiment(self, symbol: str) -> Optional[SentimentResult]:
        """Get sentiment for a symbol. Returns None if no news found."""
        # Check cache
        if symbol in self._cache:
            cached = self._cache[symbol]
            if time.time() - cached.timestamp < self._cache_ttl:
                return cached

        # Fetch and analyze
        headlines = await self._fetch_headlines()
        relevant = self._filter_relevant(headlines, symbol)

        if not relevant:
            return None

        if self._use_ollama:
            sentiment, confidence = await self._ollama_analyze(symbol, relevant)
        else:
            sentiment, confidence = self._keyword_analyze(relevant)

        result = SentimentResult(
            symbol=symbol,
            sentiment=sentiment,
            confidence=confidence,
            source_count=len(relevant),
            headlines=relevant[:5],
        )
        self._cache[symbol] = result
        return result

    async def get_confluence_modifier(self, symbol: str, side: str) -> int:
        """
        Get confluence point modifier for a symbol+side.
        Returns: +10 if sentiment aligns with trade direction, -10 if opposes, 0 if neutral.
        """
        sentiment = await self.get_sentiment(symbol)
        if sentiment is None or sentiment.confidence < 0.5:
            return 0

        if sentiment.sentiment == "BULLISH" and side == "long":
            return 10
        elif sentiment.sentiment == "BEARISH" and side == "short":
            return 10
        elif sentiment.sentiment == "BULLISH" and side == "short":
            return -5
        elif sentiment.sentiment == "BEARISH" and side == "long":
            return -5
        return 0

    async def _fetch_headlines(self) -> List[str]:
        """Fetch headlines from RSS feeds. Returns deduplicated list."""
        try:
            import feedparser
        except ImportError:
            return []

        headlines = []
        for feed_url in self.RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:  # Top 10 per feed
                    title = entry.get("title", "")
                    if title and title not in self._headline_history:
                        self._headline_history.append(title)
                        headlines.append(title)
            except Exception:
                continue

        return headlines

    def _filter_relevant(self, headlines: List[str], symbol: str) -> List[str]:
        """Filter headlines mentioning this symbol or its token name."""
        # Extract token name from symbol (BTC/USDT → BTC, bitcoin)
        token = symbol.split("/")[0].lower() if "/" in symbol else symbol.lower()

        # Also check the reverse mapping
        aliases = [token]
        for name, ticker in self.TOKEN_MAP.items():
            if ticker == symbol or name == token:
                aliases.append(name)

        relevant = []
        for h in headlines:
            h_lower = h.lower()
            if any(alias in h_lower for alias in aliases):
                relevant.append(h)
            # Also catch general "crypto market" headlines for top tokens
            elif token in ("btc", "eth") and any(
                w in h_lower for w in ["crypto market", "bitcoin", "ethereum"]
            ):
                relevant.append(h)

        return relevant[:10]  # Max 10 headlines

    def _keyword_analyze(self, headlines: List[str]) -> Tuple[str, float]:
        """Simple keyword-based sentiment. Returns (sentiment, confidence)."""
        if not headlines:
            return "NEUTRAL", 0.0

        text = " ".join(headlines).lower()
        bull_count = sum(1 for kw in self.BULLISH_KEYWORDS if kw in text)
        bear_count = sum(1 for kw in self.BEARISH_KEYWORDS if kw in text)
        total = bull_count + bear_count

        if total == 0:
            return "NEUTRAL", 0.0

        if bull_count > bear_count * 1.5:
            confidence = min(0.9, bull_count / max(total, 1))
            return "BULLISH", confidence
        elif bear_count > bull_count * 1.5:
            confidence = min(0.9, bear_count / max(total, 1))
            return "BEARISH", confidence
        else:
            return "NEUTRAL", 0.3

    async def _ollama_analyze(self, symbol: str, headlines: List[str]) -> Tuple[str, float]:
        """
        Use local Ollama LLM to analyze headlines.
        Returns (sentiment, confidence).
        """
        if not headlines:
            return "NEUTRAL", 0.0

        prompt = f"""Analyze these crypto news headlines about {symbol}.
Classify the overall sentiment as exactly one word: BULLISH, BEARISH, or NEUTRAL.
Then give a confidence score from 0.0 to 1.0.

Headlines:
{chr(10).join(f'- {h}' for h in headlines[:10])}

Respond in JSON format: {{"sentiment": "BULLISH|BEARISH|NEUTRAL", "confidence": 0.0-1.0}}"""

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": self._ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        inner = json.loads(data.get("response", "{}"))
                        return inner.get("sentiment", "NEUTRAL"), float(
                            inner.get("confidence", 0.5)
                        )
        except Exception:
            pass

        # Fallback to keyword if Ollama unavailable
        return self._keyword_analyze(headlines)


# Singleton
_news_sentiment: Optional[NewsSentiment] = None


def get_news_sentiment(use_ollama: bool = False) -> NewsSentiment:
    global _news_sentiment
    if _news_sentiment is None:
        _news_sentiment = NewsSentiment(use_ollama=use_ollama)
    return _news_sentiment
