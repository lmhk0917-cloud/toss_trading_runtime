"""Minimal Toss Invest Open API client.

Official docs:
- https://developers.tossinvest.com/docs
- https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from . import config
    from .security import sanitize_payload
except ImportError:  # pragma: no cover - direct script execution fallback
    import config
    from security import sanitize_payload


class TossInvestClientError(Exception):
    pass


class TossInvestClient(object):
    def __init__(self, client_id=None, client_secret=None, account_seq=None, base_url=None, opener=None):
        self.client_id = client_id or os.environ.get(config.CLIENT_ID_ENV)
        self.client_secret = client_secret or os.environ.get(config.CLIENT_SECRET_ENV)
        self.account_seq = account_seq or os.environ.get(config.ACCOUNT_SEQ_ENV)
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self._access_token = None
        self._token_expires_at = 0
        self.min_request_interval_sec = float(os.environ.get("TOSSINVEST_MIN_REQUEST_INTERVAL_SEC", "0.35"))
        self._last_request_at = 0.0

    def validate_config(self):
        missing = []
        if not self.client_id:
            missing.append(config.CLIENT_ID_ENV)
        if not self.client_secret:
            missing.append(config.CLIENT_SECRET_ENV)
        return missing

    def issue_token(self):
        missing = self.validate_config()
        if missing:
            raise TossInvestClientError("missing environment variables: {}".format(", ".join(missing)))

        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        response = self._request_raw(
            "POST",
            "/oauth2/token",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=False,
        )
        token = response.get("access_token")
        if not token:
            raise TossInvestClientError("token response did not include access_token")
        self._access_token = token
        self._token_expires_at = time.time() + int(response.get("expires_in") or 0) - 60
        return response

    def get_accounts(self):
        return self._request_json("GET", "/api/v1/accounts")

    def get_prices(self, symbols):
        if isinstance(symbols, (list, tuple)):
            symbols = ",".join([str(item).upper() for item in symbols])
        return self._request_json("GET", "/api/v1/prices", query={"symbols": symbols})

    def get_candles(self, symbol, interval="1m", count=100, before=None, adjusted=True):
        query = {
            "symbol": str(symbol).upper(),
            "interval": interval,
            "count": int(count),
            "adjusted": "true" if adjusted else "false",
        }
        if before:
            query["before"] = before
        return self._request_json("GET", "/api/v1/candles", query=query)

    def get_stocks(self, symbols):
        if isinstance(symbols, (list, tuple)):
            symbols = ",".join([str(item).upper() for item in symbols])
        return self._request_json("GET", "/api/v1/stocks", query={"symbols": symbols})

    def get_stock_warnings(self, symbol):
        return self._request_json("GET", "/api/v1/stocks/{}/warnings".format(str(symbol).upper()))

    def get_us_market_calendar(self):
        return self._request_json("GET", "/api/v1/market-calendar/US")

    def get_kr_market_calendar(self):
        return self._request_json("GET", "/api/v1/market-calendar/KR")

    def get_exchange_rate(self, base_currency="USD", quote_currency="KRW"):
        return self._request_json(
            "GET",
            "/api/v1/exchange-rate",
            query={"baseCurrency": base_currency, "quoteCurrency": quote_currency},
        )

    def get_holdings(self, account_seq=None):
        return self._request_json("GET", "/api/v1/holdings", account_seq=account_seq or self.account_seq)

    def get_buying_power(self, currency="USD", account_seq=None):
        return self._request_json(
            "GET",
            "/api/v1/buying-power",
            query={"currency": currency},
            account_seq=account_seq or self.account_seq,
        )

    def create_order(self, order, account_seq=None):
        raise TossInvestClientError("create_order is disabled in this scaffold; use TossOrderSafetyGate first")

    def _request_json(self, method, path, query=None, payload=None, account_seq=None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if account_seq:
            headers["X-Tossinvest-Account"] = account_seq
        return self._request_raw(method, path, query=query, body=body, headers=headers)

    def _request_raw(self, method, path, query=None, body=None, headers=None, auth=True):
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = "Bearer {}".format(self._get_access_token())
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        safe = sanitize_payload({"method": method, "path": path, "query": query, "headers": headers})
        delay = 1.0
        for attempt in range(3):
            self._respect_request_interval()
            try:
                with self.opener(request, timeout=20) as response:
                    data = response.read()
                    if not data:
                        return {}
                    return json.loads(data.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    retry_after = exc.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else delay
                    time.sleep(max(wait, self.min_request_interval_sec))
                    delay *= 2
                    continue
                raise TossInvestClientError("request failed {}: HTTP Error {}: {}".format(safe, exc.code, exc.reason))
            except Exception as exc:
                raise TossInvestClientError("request failed {}: {}".format(safe, exc))
        raise TossInvestClientError("request failed {}: retry exhausted".format(safe))

    def _get_access_token(self):
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        return self.issue_token().get("access_token")

    def _respect_request_interval(self):
        now = time.time()
        elapsed = now - self._last_request_at
        if elapsed < self.min_request_interval_sec:
            time.sleep(self.min_request_interval_sec - elapsed)
        self._last_request_at = time.time()
