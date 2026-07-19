"""IndustryService — 個股產業別分類（上市 TWSE + 上櫃 TPEX，免登入）。

來源：
- 上市：TWSE OpenAPI opendata t187ap03_L（公司代號 / 產業別）。
- 上櫃：TPEX OpenAPI mopsfin_t187ap03_O（SecuritiesCompanyCode / SecuritiesIndustryCode）。

回傳 {stock_code: industry_code}。兩市場產業代碼體系不同，故「同類股」分組
需搭配市場別（見 turnover_monitor_service）。結果模組級快取，程式生命週期抓一次。
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

_TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_HEADERS = {"User-Agent": "Mozilla/5.0", "accept": "application/json"}
_TIMEOUT = 15

_cache: dict[str, str] | None = None


def _fetch(url: str, code_key: str, ind_key: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        for row in r.json():
            code = str(row.get(code_key, "")).strip()
            ind = str(row.get(ind_key, "")).strip()
            if code and ind:
                out[code] = ind
    except Exception as e:  # noqa: BLE001
        log.warning("industry fetch failed (%s): %s", url, e)
    return out


def get_industry_map(force: bool = False) -> dict[str, str]:
    """{stock_code: industry_code}，合併上市+上櫃。模組級快取。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    m: dict[str, str] = {}
    m.update(_fetch(_TWSE_URL, "公司代號", "產業別"))
    m.update(_fetch(_TPEX_URL, "SecuritiesCompanyCode", "SecuritiesIndustryCode"))
    _cache = m
    return m
