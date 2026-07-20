"""WarrantService — 個股權證多空（認購/牛證 = 多，認售/熊證 = 空）。

- 權證→標的：TWSE OpenAPI t187ap37_L（權證代號 → 標的證券/指數 名稱），模組級快取。
- 每日成交：TWSE MI_INDEX 四個權證類別：
    0999  認購權證(不含牛證)  → 多
    0999C 牛證                → 多
    0999P 認售權證(不含熊證)  → 空
    0999B 熊證                → 空
  以「成交金額」加總，得每個標的多/空方金額。

註：台股權證結構性偏認購，多數個股「多方佔比」偏高；翻空(空方佔比高)相對罕見、
更具警示意義。回傳含 long_share 供顯示梯度。純 I/O，以標的「名稱」對應個股。
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

_BASIC_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap37_L"
_MI_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 30

# 類別 → 多(L)/空(S)
_CATS = {"0999": "L", "0999C": "L", "0999P": "S", "0999B": "S"}

_underlying_cache: dict[str, str] | None = None
_flows_cache: dict[str, dict] = {}          # date → {name: [long, short]}


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def get_underlying_map(force: bool = False) -> dict[str, str]:
    """{權證代號: 標的名稱}，模組級快取。"""
    global _underlying_cache
    if _underlying_cache is not None and not force:
        return _underlying_cache
    out: dict[str, str] = {}
    try:
        r = requests.get(_BASIC_URL,
                         headers={**_HEADERS, "accept": "application/json"},
                         timeout=_TIMEOUT)
        for row in r.json():
            code = str(row.get("權證代號", "")).strip()
            u = str(row.get("標的證券/指數", "")).strip()
            if code and u:
                out[code] = u
    except Exception as e:  # noqa: BLE001
        log.warning("warrant basic fetch failed: %s", e)
    _underlying_cache = out
    return out


def _fetch_category(date: str, typ: str) -> list[tuple[str, float]]:
    """回某類別當日 [(權證代號, 成交金額), ...]。date 為 yyyymmdd。"""
    try:
        d = requests.get(_MI_URL, params={"response": "json", "date": date,
                                          "type": typ},
                        headers=_HEADERS, timeout=_TIMEOUT).json()
    except Exception as e:  # noqa: BLE001
        log.warning("warrant MI_INDEX %s failed: %s", typ, e)
        return []
    tbl = None
    for t in d.get("tables", []):
        if any("證券代號" in str(x) for x in (t.get("fields") or [])):
            tbl = t
            break
    if not tbl:
        return []
    out = []
    for row in tbl.get("data", []):
        if len(row) > 5:
            out.append((row[1], _num(row[5])))   # 證券代號 / 成交金額
    return out


def warrant_flows(date: str) -> dict[str, list]:
    """回 {標的名稱: [多方金額, 空方金額]}。同日快取。date 為 yyyymmdd。"""
    if date in _flows_cache:
        return _flows_cache[date]
    umap = get_underlying_map()
    agg: dict[str, list] = {}
    for typ, side in _CATS.items():
        idx = 0 if side == "L" else 1
        for code, val in _fetch_category(date, typ):
            if val <= 0:
                continue
            u = umap.get(code)
            if not u:
                continue
            cell = agg.setdefault(u, [0.0, 0.0])
            cell[idx] += val
    _flows_cache[date] = agg
    return agg


def _classify(flows: dict) -> dict[str, dict]:
    """{key: [long, short]} → {key: {long, short, long_share, bias}}。"""
    out: dict[str, dict] = {}
    for key, (lng, sht) in flows.items():
        tot = lng + sht
        if tot <= 0:
            continue
        share = lng / tot * 100.0
        bias = "多" if share >= 55 else "空" if share <= 45 else "中性"
        out[key] = {"long": lng, "short": sht,
                    "long_share": round(share, 1), "bias": bias}
    return out


def bias_by_name(date: str) -> dict[str, dict]:
    """上市：{標的名稱: {long, short, long_share, bias}}。"""
    return _classify(warrant_flows(date))


# --- TPEX（上櫃）：日報直接含標的代碼，依代碼對應 -----------------------

_TPEX_URLS = [
    "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts",       # 認購售
    "https://www.tpex.org.tw/openapi/v1/tpex_warrant_wcb_daily_quts",   # 牛熊證
]
_tpex_flows_cache: dict[str, list] | None = None


def _tpex_side(name: str) -> int | None:
    """由權證名稱判方向：購/牛 → 多(0)、售/熊 → 空(1)。"""
    if "購" in name or "牛" in name:
        return 0
    if "售" in name or "熊" in name:
        return 1
    return None


def bias_by_code_tpex() -> dict[str, dict]:
    """上櫃：{標的代碼: {long, short, long_share, bias}}。TPEX OpenAPI 供最新交易日。"""
    global _tpex_flows_cache
    if _tpex_flows_cache is None:
        agg: dict[str, list] = {}
        for url in _TPEX_URLS:
            try:
                rows = requests.get(url, headers={**_HEADERS,
                                    "accept": "application/json"},
                                   timeout=_TIMEOUT).json()
            except Exception as e:  # noqa: BLE001
                log.warning("tpex warrant fetch failed: %s", e)
                continue
            for r in rows:
                code = str(r.get("UnderlyingStockCode", "")).strip()
                side = _tpex_side(str(r.get("Name", "")))
                val = _num(r.get("TradeValue"))
                if not code or side is None or val <= 0:
                    continue
                cell = agg.setdefault(code, [0.0, 0.0])
                cell[side] += val
        _tpex_flows_cache = agg
    return _classify(_tpex_flows_cache)
