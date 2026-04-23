"""Statistical helpers for broker data analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from services.broker_data_service import BrokerDataResult, BrokerRecord


def _parse_vol(v: str) -> int:
    """Parse a volume string like '1,234' or '1234' into an int."""
    try:
        return int(v.replace(",", "").replace(" ", ""))
    except (ValueError, AttributeError):
        return 0


@dataclass
class BrokerStat:
    broker_name: str
    price: str
    buy_volume: int
    sell_volume: int
    net: int  # buy - sell


@dataclass
class StatsResult:
    """Aggregated statistics computed from BrokerDataResult."""

    brokers: list[BrokerStat] = field(default_factory=list)

    # Counts
    total_buy_volume: int = 0
    total_sell_volume: int = 0
    buyer_count: int = 0   # brokers with net > 0
    seller_count: int = 0  # brokers with net < 0
    neutral_count: int = 0

    # Rankings (sorted by |net|, descending)
    top_buyers: list[BrokerStat] = field(default_factory=list)
    top_sellers: list[BrokerStat] = field(default_factory=list)

    # Concentration
    top5_buy_volume: int = 0
    top5_sell_volume: int = 0
    top5_buy_pct: float = 0.0   # top 5 buyers buy_volume / total_buy_volume
    top5_sell_pct: float = 0.0  # top 5 sellers sell_volume / total_sell_volume

    # Gauge — net buying force vs net selling force
    net_buy_force: int = 0   # sum of net for all net-buyers
    net_sell_force: int = 0  # sum of |net| for all net-sellers
    buy_ratio: float = 0.5   # net_buy_force / (net_buy_force + net_sell_force)


def compute_stats(result: BrokerDataResult, top_n: int = 5) -> StatsResult:
    """Compute statistical aggregates from raw broker data.

    Same broker branch (broker_name) may appear multiple times at different
    prices. We aggregate buy/sell volumes per broker before computing stats.
    """
    stats = StatsResult()

    # --- Group by broker_name and accumulate volumes ---
    agg: dict[str, list[int]] = {}  # broker_name -> [total_buy, total_sell]
    for rec in result.records:
        bv = _parse_vol(rec.buy_volume)
        sv = _parse_vol(rec.sell_volume)
        if rec.broker_name in agg:
            agg[rec.broker_name][0] += bv
            agg[rec.broker_name][1] += sv
        else:
            agg[rec.broker_name] = [bv, sv]

    for broker_name, (bv, sv) in agg.items():
        net = bv - sv
        stats.brokers.append(
            BrokerStat(
                broker_name=broker_name,
                price="",  # aggregated across prices
                buy_volume=bv,
                sell_volume=sv,
                net=net,
            )
        )
        stats.total_buy_volume += bv
        stats.total_sell_volume += sv

        if net > 0:
            stats.buyer_count += 1
        elif net < 0:
            stats.seller_count += 1
        else:
            stats.neutral_count += 1

    # Sort by |net| descending by default
    stats.brokers.sort(key=lambda b: abs(b.net), reverse=True)

    # Sort by net descending for buyers, ascending for sellers
    sorted_by_net = sorted(stats.brokers, key=lambda b: b.net, reverse=True)
    stats.top_buyers = [b for b in sorted_by_net if b.net > 0][:top_n]
    stats.top_sellers = [b for b in sorted_by_net if b.net < 0][-top_n:]
    # Reverse sellers so largest sell is first
    stats.top_sellers = list(reversed(stats.top_sellers))

    # Concentration
    stats.top5_buy_volume = sum(b.buy_volume for b in stats.top_buyers)
    stats.top5_sell_volume = sum(b.sell_volume for b in stats.top_sellers)

    if stats.total_buy_volume > 0:
        stats.top5_buy_pct = stats.top5_buy_volume / stats.total_buy_volume
    if stats.total_sell_volume > 0:
        stats.top5_sell_pct = stats.top5_sell_volume / stats.total_sell_volume

    # Gauge — net force: sum of positive nets vs sum of |negative nets|
    stats.net_buy_force = sum(b.net for b in stats.brokers if b.net > 0)
    stats.net_sell_force = abs(sum(b.net for b in stats.brokers if b.net < 0))
    total_force = stats.net_buy_force + stats.net_sell_force
    if total_force > 0:
        stats.buy_ratio = stats.net_buy_force / total_force

    return stats


def fmt_number(n: int) -> str:
    """Format integer with thousands separator."""
    return f"{n:,}"
