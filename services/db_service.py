"""Database service for storing broker trading statistics into MSSQL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pymssql

from services.broker_data_service import BrokerDataResult, BrokerRecord


def _normalize_date(raw: str) -> str:
    """Convert ROC date to 'yyyy-mm-dd'.

    Supported inputs:
      - '115年4月13日'  -> '2026-04-13'  (ROC calendar with 年/月/日)
      - '114/04/10'     -> '2025-04-10'  (ROC with slashes)
      - '2025/04/10'    -> '2025-04-10'
      - '2025-04-10'    -> '2025-04-10'  (already correct)
    """
    raw = raw.strip()
    # Already yyyy-mm-dd
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    # ROC with 年月日: '115年4月13日'
    m = re.match(r"^(\d{1,3})年(\d{1,2})月(\d{1,2})日$", raw)
    if m:
        year = int(m.group(1)) + 1911
        return f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # yyyy/mm/dd
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # ROC: yyy/mm/dd
    m = re.match(r"^(\d{1,3})/(\d{1,2})/(\d{1,2})$", raw)
    if m:
        year = int(m.group(1)) + 1911
        return f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # Fallback: return as-is
    return raw


# ---------------------------------------------------------------------------
# Aggregation helper — weighted average prices per broker
# ---------------------------------------------------------------------------

def _parse_vol(v: str) -> int:
    try:
        return int(v.replace(",", "").replace(" ", ""))
    except (ValueError, AttributeError):
        return 0


def _parse_price(v: str) -> float:
    try:
        return float(v.replace(",", "").replace(" ", ""))
    except (ValueError, AttributeError):
        return 0.0


def _split_broker(raw_name: str) -> tuple[str, str]:
    """Split raw broker string like '9A00 永豐金-博愛' into (code, name).

    Returns:
        (broker_code, broker_name)  e.g. ('9A00', '永豐金-博愛')
        If no code prefix found, returns ('', raw_name).
    """
    raw_name = raw_name.strip()
    m = re.match(r"^([A-Za-z0-9]{2,6})\s+(.+)$", raw_name)
    if m:
        return m.group(1), m.group(2).strip()
    return "", raw_name


@dataclass
class BrokerAgg:
    broker_code: str              # e.g. '9A00'
    broker_name: str              # e.g. '永豐金-博愛'
    buy_volume: int = 0
    sell_volume: int = 0
    net_volume: int = 0
    avg_buy_price: float | None = None
    avg_sell_price: float | None = None
    avg_price: float | None = None      # weighted by total volume (buy+sell)


def aggregate_brokers(records: list[BrokerRecord]) -> list[BrokerAgg]:
    """Group raw records by broker_name and compute weighted average prices."""
    # Accumulators: broker -> [buy_vol, sell_vol, buy_cost, sell_cost, total_cost, total_vol]
    acc: dict[str, list[float]] = {}

    for rec in records:
        bv = _parse_vol(rec.buy_volume)
        sv = _parse_vol(rec.sell_volume)
        price = _parse_price(rec.price)

        if rec.broker_name not in acc:
            acc[rec.broker_name] = [0, 0, 0.0, 0.0, 0.0, 0]

        a = acc[rec.broker_name]
        a[0] += bv
        a[1] += sv
        a[2] += price * bv   # buy cost
        a[3] += price * sv   # sell cost
        a[4] += price * (bv + sv)  # total cost
        a[5] += bv + sv            # total volume

    result: list[BrokerAgg] = []
    for broker_raw, (bv, sv, bc, sc, tc, tv) in acc.items():
        bv = int(bv)
        sv = int(sv)
        code, name = _split_broker(broker_raw)
        agg = BrokerAgg(
            broker_code=code,
            broker_name=name,
            buy_volume=bv,
            sell_volume=sv,
            net_volume=bv - sv,
            avg_buy_price=round(bc / bv, 2) if bv > 0 else None,
            avg_sell_price=round(sc / sv, 2) if sv > 0 else None,
            avg_price=round(tc / tv, 2) if tv > 0 else None,
        )
        result.append(agg)

    return result


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_STOCK_DAILY_SUMMARY = """
IF NOT EXISTS (
    SELECT * FROM sys.tables WHERE name = 'StockDailySummary'
)
CREATE TABLE StockDailySummary (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    stock_code    NVARCHAR(10)   NOT NULL,
    stock_name    NVARCHAR(50)   NULL,
    trade_date    DATE           NOT NULL,
    total_trades  NVARCHAR(20)   NULL,
    total_amount  NVARCHAR(30)   NULL,
    total_volume  NVARCHAR(30)   NULL,
    open_price    NVARCHAR(20)   NULL,
    high_price    NVARCHAR(20)   NULL,
    low_price     NVARCHAR(20)   NULL,
    close_price   NVARCHAR(20)   NULL,
    created_at    DATETIME       DEFAULT GETDATE(),

    CONSTRAINT UQ_StockDailySummary
        UNIQUE (stock_code, trade_date)
);
"""

_DDL_BROKER_DAILY_STATS = """
IF NOT EXISTS (
    SELECT * FROM sys.tables WHERE name = 'BrokerDailyStats'
)
CREATE TABLE BrokerDailyStats (
    id             INT IDENTITY(1,1) PRIMARY KEY,
    stock_code     NVARCHAR(10)    NOT NULL,
    trade_date     DATE            NOT NULL,
    broker_code    NVARCHAR(10)    NOT NULL DEFAULT '',
    broker_name    NVARCHAR(100)   NOT NULL,
    buy_volume     INT             NOT NULL DEFAULT 0,
    sell_volume    INT             NOT NULL DEFAULT 0,
    net_volume     INT             NOT NULL DEFAULT 0,
    avg_buy_price  DECIMAL(10,2)   NULL,
    avg_sell_price DECIMAL(10,2)   NULL,
    avg_price      DECIMAL(10,2)   NULL,
    created_at     DATETIME        DEFAULT GETDATE(),

    CONSTRAINT UQ_BrokerDailyStats
        UNIQUE (stock_code, trade_date, broker_code, broker_name)
);
"""

_DDL_INDEX = """
IF NOT EXISTS (
    SELECT * FROM sys.indexes
    WHERE name = 'IX_BrokerDailyStats_stock_date'
)
CREATE INDEX IX_BrokerDailyStats_stock_date
    ON BrokerDailyStats (stock_code, trade_date);
"""

_DDL_HOLDER_DISTRIBUTION = """
IF NOT EXISTS (
    SELECT * FROM sys.tables WHERE name = 'StockHolderDistribution'
)
CREATE TABLE StockHolderDistribution (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    stock_code    NVARCHAR(10)   NOT NULL,
    report_date   DATE           NOT NULL,
    level         NVARCHAR(5)    NOT NULL,
    level_label   NVARCHAR(50)   NOT NULL,
    holders       INT            NOT NULL DEFAULT 0,
    shares        BIGINT         NOT NULL DEFAULT 0,
    pct           DECIMAL(8,4)   NOT NULL DEFAULT 0,
    created_at    DATETIME       DEFAULT GETDATE(),

    CONSTRAINT UQ_HolderDist
        UNIQUE (stock_code, report_date, level)
);
"""

_DDL_INSTI_DAILY = """
IF NOT EXISTS (
    SELECT * FROM sys.tables WHERE name = 'InstiDailyTrade'
)
CREATE TABLE InstiDailyTrade (
    id                INT IDENTITY(1,1) PRIMARY KEY,
    stock_code        NVARCHAR(10)  NOT NULL,
    trade_date        DATE          NOT NULL,
    foreign_buy       INT           NOT NULL DEFAULT 0,
    foreign_sell      INT           NOT NULL DEFAULT 0,
    foreign_net       INT           NOT NULL DEFAULT 0,
    trust_buy         INT           NOT NULL DEFAULT 0,
    trust_sell        INT           NOT NULL DEFAULT 0,
    trust_net         INT           NOT NULL DEFAULT 0,
    dealer_self_buy   INT           NOT NULL DEFAULT 0,
    dealer_self_sell  INT           NOT NULL DEFAULT 0,
    dealer_self_net   INT           NOT NULL DEFAULT 0,
    dealer_hedge_buy  INT           NOT NULL DEFAULT 0,
    dealer_hedge_sell INT           NOT NULL DEFAULT 0,
    dealer_hedge_net  INT           NOT NULL DEFAULT 0,
    three_insti_net   INT           NOT NULL DEFAULT 0,
    created_at        DATETIME      DEFAULT GETDATE(),

    CONSTRAINT UQ_InstiDaily
        UNIQUE (stock_code, trade_date)
);
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DbService:
    """Manages MSSQL connection and broker data persistence."""

    def __init__(
        self,
        server: str = "127.0.0.1:1433",
        user: str = "TSE_USER",
        password: str = "fuckme",
        database: str = "TSE",
    ):
        self._conn_params = dict(
            server=server, user=user, password=password, database=database,
        )
        self._conn: pymssql.Connection | None = None

    # -- Connection ---------------------------------------------------------

    def connect(self):
        if self._conn is None:
            self._conn = pymssql.connect(**self._conn_params)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _cursor(self):
        self.connect()
        return self._conn.cursor()

    # -- DDL ----------------------------------------------------------------

    def ensure_tables(self):
        """Create tables and indexes if they don't exist.

        Also checks if existing tables have the correct schema and
        recreates them if needed (e.g. trade_date should be DATE).
        """
        cur = self._cursor()

        # Check if StockDailySummary.trade_date is still nvarchar (old schema)
        cur.execute("""
            SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME='StockDailySummary' AND COLUMN_NAME='trade_date'
        """)
        row = cur.fetchone()
        if row and row[0] != "date":
            cur.execute("DROP TABLE StockDailySummary")

        # Check if BrokerDailyStats is missing broker_code (old schema)
        cur.execute("""
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME='BrokerDailyStats' AND COLUMN_NAME='broker_code'
        """)
        if not cur.fetchone():
            cur.execute("""
                IF EXISTS (SELECT * FROM sys.tables WHERE name='BrokerDailyStats')
                DROP TABLE BrokerDailyStats
            """)

        cur.execute(_DDL_STOCK_DAILY_SUMMARY)
        cur.execute(_DDL_BROKER_DAILY_STATS)
        cur.execute(_DDL_INDEX)
        cur.execute(_DDL_HOLDER_DISTRIBUTION)
        cur.execute(_DDL_INSTI_DAILY)
        self._conn.commit()

    # -- Write --------------------------------------------------------------

    def save_result(self, result: BrokerDataResult):
        """Aggregate and save a single stock's broker data into the DB.

        Uses MERGE (upsert) so re-downloading the same stock+date overwrites
        cleanly without duplicate errors.
        """
        cur = self._cursor()
        trade_date = _normalize_date(result.trade_date)

        # Clean stock_name: '1815 富喬' -> '富喬'
        stock_name = re.sub(r"^\d+\s+", "", result.stock_name).strip() or result.stock_name

        # 1. Upsert summary
        cur.execute("""
            MERGE StockDailySummary AS tgt
            USING (SELECT %s AS stock_code, %s AS trade_date) AS src
                ON tgt.stock_code = src.stock_code
               AND tgt.trade_date = src.trade_date
            WHEN MATCHED THEN UPDATE SET
                stock_name   = %s,
                total_trades = %s,
                total_amount = %s,
                total_volume = %s,
                open_price   = %s,
                high_price   = %s,
                low_price    = %s,
                close_price  = %s
            WHEN NOT MATCHED THEN INSERT (
                stock_code, trade_date, stock_name,
                total_trades, total_amount, total_volume,
                open_price, high_price, low_price, close_price
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (
            result.stock_code, trade_date,
            # UPDATE values
            stock_name, result.total_trades, result.total_amount,
            result.total_volume, result.open_price, result.high_price,
            result.low_price, result.close_price,
            # INSERT values
            result.stock_code, trade_date, stock_name,
            result.total_trades, result.total_amount, result.total_volume,
            result.open_price, result.high_price, result.low_price,
            result.close_price,
        ))

        # 2. Aggregate broker records and upsert each
        agg_list = aggregate_brokers(result.records)

        for agg in agg_list:
            cur.execute("""
                MERGE BrokerDailyStats AS tgt
                USING (SELECT %s AS stock_code, %s AS trade_date,
                              %s AS broker_code, %s AS broker_name) AS src
                    ON tgt.stock_code  = src.stock_code
                   AND tgt.trade_date  = src.trade_date
                   AND tgt.broker_code = src.broker_code
                   AND tgt.broker_name = src.broker_name
                WHEN MATCHED THEN UPDATE SET
                    buy_volume     = %s,
                    sell_volume    = %s,
                    net_volume     = %s,
                    avg_buy_price  = %s,
                    avg_sell_price = %s,
                    avg_price      = %s
                WHEN NOT MATCHED THEN INSERT (
                    stock_code, trade_date, broker_code, broker_name,
                    buy_volume, sell_volume, net_volume,
                    avg_buy_price, avg_sell_price, avg_price
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                result.stock_code, trade_date, agg.broker_code, agg.broker_name,
                # UPDATE
                agg.buy_volume, agg.sell_volume, agg.net_volume,
                agg.avg_buy_price, agg.avg_sell_price, agg.avg_price,
                # INSERT
                result.stock_code, trade_date, agg.broker_code, agg.broker_name,
                agg.buy_volume, agg.sell_volume, agg.net_volume,
                agg.avg_buy_price, agg.avg_sell_price, agg.avg_price,
            ))

        self._conn.commit()
        return len(agg_list)

    # -- Read ---------------------------------------------------------------

    def stock_exists(self, stock_code: str, trade_date: str) -> bool:
        """Check if data for this stock+date already exists."""
        cur = self._cursor()
        trade_date = _normalize_date(trade_date)
        cur.execute(
            "SELECT 1 FROM StockDailySummary WHERE stock_code=%s AND trade_date=%s",
            (stock_code, trade_date),
        )
        return cur.fetchone() is not None

    def get_all_broker_buys_by_date(self, trade_date: str) -> list[dict]:
        """All broker buy records for a given date, with stock info."""
        cur = self._cursor()
        trade_date = _normalize_date(trade_date)
        cur.execute("""
            SELECT b.stock_code, s.stock_name, s.close_price, s.total_volume,
                   b.broker_code, b.broker_name, b.buy_volume, b.sell_volume
            FROM BrokerDailyStats b
            JOIN StockDailySummary s
              ON b.stock_code = s.stock_code AND b.trade_date = s.trade_date
            WHERE b.trade_date = %s
            ORDER BY b.stock_code
        """, (trade_date,))
        return [
            {
                "stock_code": r[0], "stock_name": r[1],
                "close_price": r[2], "total_volume": r[3],
                "broker_code": r[4], "broker_name": r[5],
                "buy_volume": r[6], "sell_volume": r[7],
            }
            for r in cur.fetchall()
        ]

    def get_latest_volume(self, stock_code: str) -> list[dict]:
        """Get the last 2 trading days' volume and close price for a stock."""
        cur = self._cursor()
        cur.execute("""
            SELECT TOP 2 trade_date, close_price, total_volume
            FROM StockDailySummary
            WHERE stock_code=%s
            ORDER BY trade_date DESC
        """, (stock_code,))
        return [
            {
                "trade_date": str(r[0]),
                "close_price": r[1],
                "total_volume": r[2],
            }
            for r in cur.fetchall()
        ]

    def search_stocks(self, keyword: str) -> list[dict]:
        """Search stocks by code or name. Returns list of {stock_code, stock_name}."""
        cur = self._cursor()
        kw = f"%{keyword}%"
        cur.execute("""
            SELECT DISTINCT stock_code, stock_name
            FROM StockDailySummary
            WHERE stock_code LIKE %s OR stock_name LIKE %s
            ORDER BY stock_code
        """, (kw, kw))
        return [{"stock_code": r[0], "stock_name": r[1]} for r in cur.fetchall()]

    def get_stock_date_range(self, stock_code: str) -> tuple[str, str]:
        """Return (min_date, max_date) for a stock as 'yyyy-mm-dd' strings."""
        cur = self._cursor()
        cur.execute("""
            SELECT MIN(trade_date), MAX(trade_date)
            FROM StockDailySummary WHERE stock_code=%s
        """, (stock_code,))
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0]), str(row[1])
        return "", ""

    def get_brokers_summary(
        self, stock_code: str, start_date: str, end_date: str,
    ) -> list[dict]:
        """Aggregated broker data for a stock within a date range.

        Returns list of dicts with keys:
          broker_code, broker_name, buy_volume, sell_volume, net_volume,
          avg_buy_price, avg_sell_price
        """
        cur = self._cursor()
        cur.execute("""
            SELECT broker_code, broker_name,
                   SUM(buy_volume)  AS buy_vol,
                   SUM(sell_volume) AS sell_vol,
                   SUM(net_volume)  AS net_vol,
                   CASE WHEN SUM(buy_volume) > 0
                        THEN ROUND(
                            SUM(avg_buy_price * buy_volume) / SUM(buy_volume), 2
                        ) ELSE NULL END AS avg_buy,
                   CASE WHEN SUM(sell_volume) > 0
                        THEN ROUND(
                            SUM(avg_sell_price * sell_volume) / SUM(sell_volume), 2
                        ) ELSE NULL END AS avg_sell
            FROM BrokerDailyStats
            WHERE stock_code=%s AND trade_date >= %s AND trade_date <= %s
            GROUP BY broker_code, broker_name
            ORDER BY SUM(net_volume) DESC
        """, (stock_code, start_date, end_date))
        return [
            {
                "broker_code": r[0], "broker_name": r[1],
                "buy_volume": r[2], "sell_volume": r[3], "net_volume": r[4],
                "avg_buy_price": float(r[5]) if r[5] is not None else None,
                "avg_sell_price": float(r[6]) if r[6] is not None else None,
            }
            for r in cur.fetchall()
        ]

    def get_broker_daily(
        self, stock_code: str, broker_code: str, broker_name: str,
        start_date: str, end_date: str,
    ) -> list[dict]:
        """Daily data for a specific broker+stock within a date range."""
        cur = self._cursor()
        cur.execute("""
            SELECT trade_date, buy_volume, sell_volume, net_volume,
                   avg_buy_price, avg_sell_price
            FROM BrokerDailyStats
            WHERE stock_code=%s AND broker_code=%s AND broker_name=%s
              AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date
        """, (stock_code, broker_code, broker_name, start_date, end_date))
        return [
            {
                "trade_date": str(r[0]), "buy_volume": r[1],
                "sell_volume": r[2], "net_volume": r[3],
                "avg_buy_price": float(r[4]) if r[4] is not None else None,
                "avg_sell_price": float(r[5]) if r[5] is not None else None,
            }
            for r in cur.fetchall()
        ]

    def get_stock_prices(
        self, stock_code: str, start_date: str, end_date: str,
    ) -> list[dict]:
        """Daily prices for a stock within a date range."""
        cur = self._cursor()
        cur.execute("""
            SELECT trade_date, close_price, open_price, high_price, low_price
            FROM StockDailySummary
            WHERE stock_code=%s AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date
        """, (stock_code, start_date, end_date))
        return [
            {
                "trade_date": str(r[0]),
                "close_price": r[1], "open_price": r[2],
                "high_price": r[3], "low_price": r[4],
            }
            for r in cur.fetchall()
        ]

    def get_all_brokers_daily(
        self, stock_code: str, start_date: str, end_date: str,
    ) -> list[dict]:
        """All brokers' daily data + stock volume for correlation analysis."""
        cur = self._cursor()
        cur.execute("""
            SELECT b.trade_date, b.broker_code, b.broker_name,
                   b.buy_volume, b.sell_volume, b.net_volume,
                   s.close_price, s.total_volume
            FROM BrokerDailyStats b
            JOIN StockDailySummary s
              ON b.stock_code = s.stock_code AND b.trade_date = s.trade_date
            WHERE b.stock_code=%s AND b.trade_date >= %s AND b.trade_date <= %s
            ORDER BY b.broker_code, b.broker_name, b.trade_date
        """, (stock_code, start_date, end_date))
        return [
            {
                "trade_date": str(r[0]), "broker_code": r[1],
                "broker_name": r[2], "buy_volume": r[3],
                "sell_volume": r[4], "net_volume": r[5],
                "close_price": r[6], "total_volume": r[7],
            }
            for r in cur.fetchall()
        ]

    # -- Holder distribution ------------------------------------------------

    def save_distribution(self, stock_code: str, report_date: str,
                          levels: list[dict]) -> int:
        """Upsert shareholding distribution levels."""
        cur = self._cursor()
        count = 0
        for lv in levels:
            cur.execute("""
                MERGE StockHolderDistribution AS tgt
                USING (SELECT %s AS stock_code, %s AS report_date,
                              %s AS level) AS src
                    ON tgt.stock_code = src.stock_code
                   AND tgt.report_date = src.report_date
                   AND tgt.level = src.level
                WHEN MATCHED THEN UPDATE SET
                    level_label = %s, holders = %s, shares = %s, pct = %s
                WHEN NOT MATCHED THEN INSERT (
                    stock_code, report_date, level, level_label,
                    holders, shares, pct
                ) VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, (
                stock_code, report_date, lv["level"],
                lv["label"], lv["holders"], lv["shares"], lv["pct"],
                stock_code, report_date, lv["level"],
                lv["label"], lv["holders"], lv["shares"], lv["pct"],
            ))
            count += 1
        self._conn.commit()
        return count

    def get_distribution_history(self, stock_code: str) -> list[dict]:
        """Get weekly distribution summary (retail/mid/big pct) over time."""
        cur = self._cursor()
        cur.execute("""
            SELECT report_date,
                SUM(CASE WHEN level IN ('1','2','3','4','5')
                    THEN pct ELSE 0 END) AS retail_pct,
                SUM(CASE WHEN level IN ('6','7','8','9','10','11')
                    THEN pct ELSE 0 END) AS mid_pct,
                SUM(CASE WHEN level IN ('12','13','14','15')
                    THEN pct ELSE 0 END) AS big_pct
            FROM StockHolderDistribution
            WHERE stock_code=%s
            GROUP BY report_date
            ORDER BY report_date
        """, (stock_code,))
        return [
            {
                "report_date": str(r[0]),
                "retail_pct": float(r[1]),
                "mid_pct": float(r[2]),
                "big_pct": float(r[3]),
            }
            for r in cur.fetchall()
        ]

    # -- Institutional daily trade ------------------------------------------

    def save_insti_daily_batch(self, rows: list) -> int:
        """Upsert a batch of InstiDaily records."""
        cur = self._cursor()
        count = 0
        for r in rows:
            # 15 data fields matching the UPDATE SET and INSERT columns
            vals = (
                r.foreign_buy, r.foreign_sell, r.foreign_net,
                r.trust_buy, r.trust_sell, r.trust_net,
                r.dealer_self_buy, r.dealer_self_sell, r.dealer_self_net,
                r.dealer_hedge_buy, r.dealer_hedge_sell, r.dealer_hedge_net,
                r.three_insti_net,
            )
            # USING=2 + UPDATE=13 + INSERT VALUES=2+13 = 30 placeholders
            cur.execute("""
                MERGE InstiDailyTrade AS tgt
                USING (SELECT %s AS stock_code, %s AS trade_date) AS src
                    ON tgt.stock_code = src.stock_code
                   AND tgt.trade_date = src.trade_date
                WHEN MATCHED THEN UPDATE SET
                    foreign_buy=%s, foreign_sell=%s, foreign_net=%s,
                    trust_buy=%s, trust_sell=%s, trust_net=%s,
                    dealer_self_buy=%s, dealer_self_sell=%s, dealer_self_net=%s,
                    dealer_hedge_buy=%s, dealer_hedge_sell=%s, dealer_hedge_net=%s,
                    three_insti_net=%s
                WHEN NOT MATCHED THEN INSERT (
                    stock_code, trade_date,
                    foreign_buy, foreign_sell, foreign_net,
                    trust_buy, trust_sell, trust_net,
                    dealer_self_buy, dealer_self_sell, dealer_self_net,
                    dealer_hedge_buy, dealer_hedge_sell, dealer_hedge_net,
                    three_insti_net
                ) VALUES (%s, %s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s);
            """, (
                r.stock_code, r.trade_date,
                *vals,
                r.stock_code, r.trade_date,
                *vals,
            ))
            count += 1
        self._conn.commit()
        return count

    def get_insti_history(self, stock_code: str,
                          start_date: str, end_date: str) -> list[dict]:
        """Get institutional daily data for a stock in a date range."""
        cur = self._cursor()
        cur.execute("""
            SELECT trade_date,
                   foreign_net, trust_net,
                   dealer_self_net, dealer_hedge_net,
                   dealer_hedge_buy, dealer_hedge_sell,
                   three_insti_net
            FROM InstiDailyTrade
            WHERE stock_code=%s AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date
        """, (stock_code, start_date, end_date))
        return [
            {
                "trade_date": str(r[0]),
                "foreign_net": r[1], "trust_net": r[2],
                "dealer_self_net": r[3], "dealer_hedge_net": r[4],
                "dealer_hedge_buy": r[5], "dealer_hedge_sell": r[6],
                "three_insti_net": r[7],
            }
            for r in cur.fetchall()
        ]
