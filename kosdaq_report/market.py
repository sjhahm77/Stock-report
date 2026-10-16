"""시세 데이터 소스 선택: KRX Open API → (실패 시) 공공데이터포털 순서로 시도한다."""
from __future__ import annotations

from datetime import date

import pandas as pd

from .datagokr import DataGoKrAuthError, DataGoKrClient, DataGoKrError
from .krx import KrxAuthError, KrxClient, KrxError, StockInfo


class MarketDataError(RuntimeError):
    pass


class MarketData:
    def __init__(self, krx_key: str = "", datagokr_key: str = ""):
        self.sources: list[tuple[str, object]] = []
        if krx_key.strip():
            self.sources.append(("KRX Open API", KrxClient(krx_key)))
        if datagokr_key.strip():
            self.sources.append(("공공데이터포털", DataGoKrClient(datagokr_key)))
        if not self.sources:
            raise MarketDataError("KRX API 키 또는 공공데이터포털 인증키 중 하나는 있어야 합니다.")
        self.used: str = ""

    def _call(self, method: str, *args):
        errors = []
        for name, client in list(self.sources):
            try:
                result = getattr(client, method)(*args)
            except (KrxAuthError, DataGoKrAuthError) as e:
                # 권한이 없는 소스는 이후 호출에서 아예 건너뛴다 (매번 수백 건씩 실패 호출하지 않도록)
                self.sources = [s for s in self.sources if s[0] != name]
                errors.append(f"[{name}] {e}")
                continue
            except (KrxError, DataGoKrError) as e:
                errors.append(f"[{name}] {e}")
                continue
            # 빈 결과면 다음 소스도 시도 (예: KRX 는 되지만 해당 종목 데이터가 없을 때)
            if isinstance(result, (pd.DataFrame, pd.Series, list)) and len(result) == 0 and name != self.sources[-1][0]:
                errors.append(f"[{name}] 데이터 없음")
                continue
            self.used = name
            return result
        raise MarketDataError(" / ".join(errors))

    def listing(self) -> list[StockInfo]:
        return self._call("listing")

    def price_history(self, code: str, start: date, end: date | None = None) -> pd.DataFrame:
        return self._call("price_history", code, start, end)

    def kosdaq_index(self, start: date, end: date | None = None) -> pd.Series:
        return self._call("kosdaq_index", start, end)
