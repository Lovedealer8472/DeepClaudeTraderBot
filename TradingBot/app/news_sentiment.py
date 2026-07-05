"""
Crypto news sentiment — Google News RSS + local Ollama LLM.

Fetches per-token headlines from Google News (free, no API key, 100 headlines/query).
Runs sentiment via Monolith Ollama (qwen3:8b) with keyword fallback.
Exposes per-symbol confluence modifiers for scoring.
"""

import asyncio
import hashlib
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from urllib.parse import quote


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
    Per-token crypto news sentiment using Google News RSS + local Ollama.
    Cache: 15-min TTL per symbol. Feed cache: 5-min TTL (100 headlines/symbol).
    """

    # Keyword fallback — fast, free, always available
    BULLISH_KEYWORDS = [
        "surge", "rally", "breakout", "bullish", "upgrade", "partnership",
        "launch", "adoption", "institutional", "etf", "approve", "record",
        "positive", "growth", "expansion", "accumulation", "support", "bounce",
        "reversal", "breakthrough", "listing", "integration", "mainnet",
        "retakes", "retaking", "gains", "climb", "soar", "spike", "rebound",
    ]
    BEARISH_KEYWORDS = [
        "crash", "hack", "exploit", "sec", "lawsuit", "ban", "regulate",
        "crackdown", "bearish", "downgrade", "selloff", "liquidation",
        "decline", "loss", "debt", "default", "delay", "suspend", "halt",
        "warning", "risk", "volatile", "uncertain", "fud", "dump", "plunge",
        "tumble", "slide", "drop", "slump", "fall", "correction",
    ]

    def __init__(self, use_ollama: bool = True, ollama_model: str = "qwen3:8b",
                 ollama_host: str = "http://192.168.50.26:11434"):
        self._cache: Dict[str, SentimentResult] = {}
        self._cache_ttl = 900  # 15 minutes
        self._use_ollama = use_ollama
        self._ollama_model = ollama_model
        self._ollama_host = ollama_host
        self._feed_cache: Dict[str, Tuple[float, List[str]]] = {}  # token → (timestamp, headlines)
        self._feed_ttl = 300  # 5 minutes — Google News rate limiting
        self._seen = set()  # Dedup hashes
        self._max_headlines = 20  # Cap headlines per token for LLM analysis

    async def get_sentiment(self, symbol: str) -> Optional[SentimentResult]:
        """Get sentiment for a symbol. Returns None if no headlines found."""
        token = symbol.split("/")[0] if "/" in symbol else symbol

        # Check cache
        if symbol in self._cache:
            cached = self._cache[symbol]
            if time.time() - cached.timestamp < self._cache_ttl:
                return cached

        # Fetch headlines
        headlines = await self._fetch_google_news(token)
        if not headlines:
            return None

        # Run sentiment
        if self._use_ollama:
            sentiment, confidence = await self._ollama_analyze(token, headlines)
        else:
            sentiment, confidence = self._keyword_analyze(headlines)

        result = SentimentResult(
            symbol=symbol,
            sentiment=sentiment,
            confidence=confidence,
            source_count=len(headlines),
            headlines=headlines[:5],
        )
        self._cache[symbol] = result
        return result

    async def get_confluence_modifier(self, symbol: str, side: str) -> int:
        """+10 if sentiment aligns with trade direction, -5 if opposes, 0 if neutral."""
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

    async def _fetch_google_news(self, token: str) -> List[str]:
        """Fetch headlines from Google News RSS for a token. Cached per token."""
        # Check feed cache
        if token in self._feed_cache:
            ts, headlines = self._feed_cache[token]
            if time.time() - ts < self._feed_ttl:
                return headlines

        try:
            query = quote(f"{token} cryptocurrency")
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&ceid=US:en"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

            def _fetch():
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.read()

            data = await asyncio.get_event_loop().run_in_executor(None, _fetch)
            root = ET.fromstring(data.decode("utf-8", errors="replace"))
            items = root.findall(".//item")

            headlines = []
            for item in items:
                title_elem = item.find("title")
                if title_elem is not None and title_elem.text:
                    title = title_elem.text.strip()
                    h = hashlib.md5(title.encode()).hexdigest()
                    if h not in self._seen:
                        self._seen.add(h)
                        headlines.append(title)
                if len(headlines) >= self._max_headlines:
                    break

            # Trim seen set
            if len(self._seen) > 10000:
                self._seen = set(list(self._seen)[-5000:])

            if headlines:
                self._feed_cache[token] = (time.time(), headlines)
            return headlines
        except Exception:
            # Return stale cache on error
            if token in self._feed_cache:
                _, headlines = self._feed_cache[token]
                return headlines
            return []

    def _keyword_analyze(self, headlines: List[str]) -> Tuple[str, float]:
        """Keyword-based sentiment. Fast fallback when Ollama is down."""
        if not headlines:
            return "NEUTRAL", 0.0
        text = " ".join(headlines).lower()
        bull = sum(1 for kw in self.BULLISH_KEYWORDS if kw in text)
        bear = sum(1 for kw in self.BEARISH_KEYWORDS if kw in text)
        total = bull + bear
        if total == 0:
            return "NEUTRAL", 0.0
        if bull > bear * 1.5:
            return "BULLISH", min(0.9, bull / max(total, 1))
        elif bear > bull * 1.5:
            return "BEARISH", min(0.9, bear / max(total, 1))
        return "NEUTRAL", 0.3

    async def _ollama_analyze(self, token: str, headlines: List[str]) -> Tuple[str, float]:
        """Ollama LLM analysis via Monolith. Returns (sentiment, confidence)."""
        if not headlines:
            return "NEUTRAL", 0.0

        prompt = f"""Analyze these crypto news headlines about {token}.
Classify the overall short-term price sentiment as exactly one word: BULLISH, BEARISH, or NEUTRAL.
Then give a confidence score from 0.0 to 1.0.

Headlines:
{chr(10).join(f'- {h}' for h in headlines[:10])}

Respond in JSON: {{"sentiment": "BULLISH|BEARISH|NEUTRAL", "confidence": 0.0-1.0}}"""

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._ollama_host}/api/generate",
                    json={"model": self._ollama_model, "prompt": prompt,
                          "stream": False, "format": "json"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        inner = json.loads(data.get("response", "{}"))
                        return inner.get("sentiment", "NEUTRAL"), float(
                            inner.get("confidence", 0.5))
        except Exception:
            pass
        return self._keyword_analyze(headlines)


# Singleton
_news_sentiment: Optional[NewsSentiment] = None


def get_news_sentiment(use_ollama: bool = True) -> NewsSentiment:
    global _news_sentiment
    if _news_sentiment is None:
        _news_sentiment = NewsSentiment(use_ollama=use_ollama)
    return _news_sentiment
