import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def _epoch_to_utc_iso(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).isoformat()


class DhanHistoryClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    async def fetch_intraday_ohlc(
        self,
        *,
        security_id: int,
        exchange_segment: str,
        instrument: str,
        interval_minutes: int,
        from_date: str,
        to_date: str,
        oi: bool = False,
        timeout_seconds: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """Fetch intraday candle data from Dhan and return a list of candles.

        Notes:
        - `from_date` / `to_date` must be strings in Dhan format: "YYYY-MM-DD HH:MM:SS".
        - Dhan limits large intraday fetches (commonly ~90 days per request).
        """
        url = "https://api.dhan.co/v2/charts/intraday"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.access_token,
        }
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": str(int(interval_minutes)),
            "oi": bool(oi),
            "fromDate": from_date,
            "toDate": to_date,
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Dhan intraday fetch failed: HTTP {resp.status_code} | {resp.text[:300]}")

        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Dhan intraday fetch unexpected response: {type(data)}")

        # Typical response: arrays for open/high/low/close/volume/timestamp
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        timestamps = data.get("timestamp") or []

        n = min(len(highs), len(lows), len(closes), len(timestamps))
        if n == 0:
            return []

        candles: List[Dict[str, Any]] = []
        for i in range(n):
            epoch = int(timestamps[i])
            candles.append(
                {
                    "timestamp": _epoch_to_utc_iso(epoch),
                    "epoch": epoch,
                    "open": float(opens[i]) if i < len(opens) and opens[i] is not None else None,
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                    "volume": None,
                }
            )

        return candles
