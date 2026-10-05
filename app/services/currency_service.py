"""Currency conversion service — live FX rates via Frankfurter API."""

import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory TTL cache for exchange rates
# ---------------------------------------------------------------------------

_rate_cache: dict[str, tuple[float, float]] = {}  # key -> (rate, timestamp)
_CACHE_TTL = 300  # 5 minutes


def _cache_key(base: str, target: str) -> str:
    return f"{base.upper()}:{target.upper()}"


# ---------------------------------------------------------------------------
# Currency symbol mapping
# ---------------------------------------------------------------------------

CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CAD": "C$",
    "AUD": "A$",
    "CHF": "CHF",
    "CNY": "¥",
    "KRW": "₩",
    "SGD": "S$",
    "AED": "AED",
}

FALLBACK_SYMBOL = ""

# Currencies the Frankfurter (ECB) API can convert — anything else cannot be
# used as a preferred currency or converted from.
SUPPORTED_CURRENCIES: frozenset[str] = frozenset({
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW", "MXN", "MYR",
    "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD",
    "ZAR",
})


class CurrencyConversionError(Exception):
    """Raised when an exchange rate cannot be obtained."""


def is_supported_currency(code: str) -> bool:
    """True if the code is a currency we can convert to/from."""
    return code.upper() in SUPPORTED_CURRENCIES


def get_currency_symbol(code: str) -> str:
    """Return the symbol for a currency code, or the code itself as fallback."""
    return CURRENCY_SYMBOLS.get(code.upper(), code.upper())


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CurrencyService:
    """Converts between currencies using the Frankfurter API."""

    def __init__(self, api_url: str | None = None) -> None:
        self.api_url = api_url or settings.frankfurter_api_url

    async def get_rate(self, base: str, target: str) -> float:
        """Fetch the live exchange rate from base to target currency.

        Uses an in-memory cache with a 5-minute TTL to reduce API calls.
        Raises CurrencyConversionError if the rate cannot be fetched.
        """
        base = base.upper()
        target = target.upper()

        if base == target:
            return 1.0

        if base not in SUPPORTED_CURRENCIES or target not in SUPPORTED_CURRENCIES:
            raise CurrencyConversionError(f"Unsupported currency pair {base}→{target}")

        key = _cache_key(base, target)
        now = time.time()

        # Check cache
        if key in _rate_cache:
            rate, ts = _rate_cache[key]
            if now - ts < _CACHE_TTL:
                logger.info("FX rate %s→%s = %s (cache hit)", base, target, rate)
                return rate

        # Fetch from API
        url = f"{self.api_url}/latest?base={base}&symbols={target}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                rate = float(data["rates"][target])

                # Cache the result
                _rate_cache[key] = (rate, now)
                logger.info(
                    "FX rate %s→%s = %s (fetched in %.0f ms)",
                    base, target, rate, (time.perf_counter() - started) * 1000,
                )
                return rate

        except Exception as exc:  # noqa: BLE001 — normalise transport/parse failures
            logger.warning(
                "Failed to fetch FX rate for %s→%s: %s", base, target, type(exc).__name__
            )
            raise CurrencyConversionError(f"No FX rate for {base}→{target}") from exc

    async def convert(
        self, amount: float, from_currency: str, to_currency: str
    ) -> tuple[float, float]:
        """Convert an amount between currencies.

        Returns:
            Tuple of (converted_amount, rate_used).
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return (amount, 1.0)

        rate = await self.get_rate(from_currency, to_currency)
        converted = round(amount * rate, 2)
        return (converted, rate)


# Shared instance
currency_service = CurrencyService()
