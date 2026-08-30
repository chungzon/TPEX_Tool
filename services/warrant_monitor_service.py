"""隔日沖監控 — 權證即時買賣壓彙總（判斷自營避險是否鬆動）。

概念：自營商賣出認購權證後買標的避險、賣出認售權證後空/賣標的避險。盤中若
- 認購被倒賣（認購內盤賣壓）→ 自營買回認購、賣出避險股 → 標的賣壓；
- 認售被買進（認售外盤買盤）→ 自營賣股避險 → 標的賣壓。
故「自營賣壓」≈ (認售淨買) − (認購淨買) = put_net − call_net，越大越可能自營賣股。

本模組：
- 標的→權證清單對應（上市依名稱、上櫃依代碼；含認購/認售分類）。模組級快取。
- WarrantAgg：逐檔標的彙總其權證即時內外盤買賣量與加權漲跌（純計算）。

對應表需 requests I/O（首次載入 TWSE 4 萬筆較慢，之後快取）；即時逐筆由 viewmodel
以 Shioaji 訂閱後餵入 WarrantAgg。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

_TWSE_BASIC = "https://openapi.twse.com.tw/v1/opendata/t187ap37_L"
_TPEX_URLS = [
    "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts",
    "https://www.tpex.org.tw/openapi/v1/tpex_warrant_wcb_daily_quts",
]
_HEADERS = {"User-Agent": "Mozilla/5.0", "accept": "application/json"}
_TIMEOUT = 30

# 標的名稱 → [(權證代號, side)]（上市）；標的代碼 → [(權證代號, side, tradevol)]（上櫃）
_twse_map: dict[str, list] | None = None
_tpex_map: dict[str, list] | None = None


def warrant_side(text: str) -> str | None:
    """由權證類型/簡稱判方向：購/牛 → 'call'（認購）、售/熊 → 'put'（認售）。"""
    t = str(text or "")
    if "購" in t or "牛" in t:
        return "call"
    if "售" in t or "熊" in t:
        return "put"
    return None


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _build_twse() -> dict[str, list]:
    global _twse_map
    if _twse_map is not None:
        return _twse_map
    m: dict[str, list] = {}
    try:
        rows = requests.get(_TWSE_BASIC, headers=_HEADERS, timeout=_TIMEOUT).json()
        for r in rows:
            wc = str(r.get("權證代號", "")).strip()
            und = str(r.get("標的證券/指數", "")).strip()
            side = warrant_side(r.get("權證類型", ""))
            if wc and und and side:
                m.setdefault(und, []).append((wc, side, 0.0))
    except Exception as e:  # noqa: BLE001
        log.warning("warrant TWSE basic fetch failed: %s", e)
    _twse_map = m
    return m


def _build_tpex() -> dict[str, list]:
    global _tpex_map
    if _tpex_map is not None:
        return _tpex_map
    m: dict[str, list] = {}
    for url in _TPEX_URLS:
        try:
            rows = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT).json()
        except Exception as e:  # noqa: BLE001
            log.warning("warrant TPEX fetch failed: %s", e)
            continue
        for r in rows:
            wc = str(r.get("Code", "")).strip()
            uc = str(r.get("UnderlyingStockCode", "")).strip()
            side = warrant_side(r.get("Name", ""))
            if wc and uc and side:
                m.setdefault(uc, []).append((wc, side, _num(r.get("TradeVol."))))
    _tpex_map = m
    return m


def warrants_for(stock_code: str, stock_name: str = "") -> list[tuple]:
    """回某標的的權證清單 [(權證代號, side, 參考成交量)]。

    上櫃：依標的代碼（TPEX 日報含成交量，可排序）；上市：依標的名稱（無量，量填0）。
    先試上櫃代碼命中，否則用上市名稱。
    """
    tp = _build_tpex().get(stock_code)
    if tp:
        return list(tp)
    return list(_build_twse().get(stock_name, []))


_twse_turnover_cache: dict[str, dict] = {}


def _twse_turnover(date: str) -> dict[str, float]:
    """{權證代號: 當日成交金額}（上市權證排序用）。date 為 yyyymmdd。同日快取。"""
    if date in _twse_turnover_cache:
        return _twse_turnover_cache[date]
    from services import warrant_service as ws
    m: dict[str, float] = {}
    for typ in ("0999", "0999C", "0999P", "0999B"):
        try:
            for code, val in ws._fetch_category(date, typ):
                m[code] = m.get(code, 0.0) + _num(val)
        except Exception as e:  # noqa: BLE001
            log.warning("twse warrant turnover %s failed: %s", typ, e)
    _twse_turnover_cache[date] = m
    return m


def pick_warrants(stock_code: str, stock_name: str = "",
                  date: str | None = None, k: int = 6) -> list[tuple]:
    """挑該標的「成交最活躍」前 k 檔權證 [(權證代號, side)]。

    上櫃用 TPEX 日報成交量；上市用 TWSE MI_INDEX 成交金額（需 date=yyyymmdd）。
    量來源缺漏時退回原順序前 k 檔。
    """
    cands = warrants_for(stock_code, stock_name)      # [(wc, side, vol)]
    if not cands:
        return []
    if all((c[2] or 0) == 0 for c in cands) and date:      # 上市：補成交金額
        tmap = _twse_turnover(date)
        cands = [(c[0], c[1], tmap.get(c[0], 0.0)) for c in cands]
    cands.sort(key=lambda c: c[2] or 0, reverse=True)
    return [(c[0], c[1]) for c in cands[:k]]


def preload_maps() -> None:
    """預先建立對應表（背景執行緒呼叫，避免首次點擊卡頓）。"""
    _build_tpex()
    _build_twse()


@dataclass
class WarrantAgg:
    """單一標的的權證即時彙總。ticks 由 viewmodel 依權證代號餵入。"""
    code: str
    name: str = ""
    call_buy: int = 0        # 認購外盤(買)量 張
    call_sell: int = 0       # 認購內盤(賣)量 張
    put_buy: int = 0         # 認售外盤(買)量 張
    put_sell: int = 0        # 認售內盤(賣)量 張
    # 每檔權證：{wcode: {'side','ref','last','vol'}} 供加權漲跌
    warrants: dict = field(default_factory=dict)

    def add_warrant(self, wcode: str, side: str, ref: float, last: float) -> None:
        self.warrants[wcode] = {"side": side, "ref": ref or 0.0,
                                "last": last or ref or 0.0, "vol": 0}

    def on_tick(self, wcode: str, tick: dict) -> None:
        w = self.warrants.get(wcode)
        if w is None:
            return
        vol = int(tick.get("volume") or 0)
        price = tick.get("close") or tick.get("price")
        if price:
            w["last"] = float(price)
        if vol <= 0:
            return
        w["vol"] += vol
        tt = tick.get("tick_type", 0)      # 1 外盤(買) / 2 內盤(賣)
        if w["side"] == "call":
            if tt == 1:
                self.call_buy += vol
            elif tt == 2:
                self.call_sell += vol
        else:
            if tt == 1:
                self.put_buy += vol
            elif tt == 2:
                self.put_sell += vol

    @property
    def call_net(self) -> int:
        return self.call_buy - self.call_sell

    @property
    def put_net(self) -> int:
        return self.put_buy - self.put_sell

    @property
    def dealer_sell_pressure(self) -> int:
        """自營賣壓（張）：認售淨買 − 認購淨買。正值＝自營傾向賣股解避險。"""
        return self.put_net - self.call_net

    def _side_chg(self, side: str) -> float:
        """該方向權證的成交量加權漲跌%（以參考價 ref 為基準）。"""
        num = den = 0.0
        for w in self.warrants.values():
            if w["side"] != side or not w["ref"]:
                continue
            wt = w["vol"] or 1
            num += (w["last"] - w["ref"]) / w["ref"] * 100 * wt
            den += wt
        return (num / den) if den else 0.0

    def snapshot(self) -> dict:
        n_call = sum(1 for w in self.warrants.values() if w["side"] == "call")
        n_put = sum(1 for w in self.warrants.values() if w["side"] == "put")
        return {
            "code": self.code, "name": self.name,
            "call_buy": self.call_buy, "call_sell": self.call_sell,
            "put_buy": self.put_buy, "put_sell": self.put_sell,
            "call_net": self.call_net, "put_net": self.put_net,
            "call_chg": round(self._side_chg("call"), 2),
            "put_chg": round(self._side_chg("put"), 2),
            "sell_pressure": self.dealer_sell_pressure,
            "n_call": n_call, "n_put": n_put,
        }
