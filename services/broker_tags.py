"""Known broker trading style tags based on market research.

Tags:
  沖 = 當沖 (day trading)
  隔 = 隔日沖 (next-day flip)
  短 = 短線 (short-term)
  波 = 波段 (swing / medium-term)

A broker can have multiple tags.
Broker names match the DB format (no dashes).
"""

from __future__ import annotations

# Tag definitions
TAG_DAY   = "沖"   # 當沖
TAG_NEXT  = "隔"   # 隔日沖
TAG_SHORT = "短"   # 短線
TAG_SWING = "波"   # 波段

TAG_COLORS = {
    TAG_DAY:   "#ff6d00",  # orange
    TAG_NEXT:  "#ef5350",  # red
    TAG_SHORT: "#42a5f5",  # blue
    TAG_SWING: "#66bb6a",  # green
}

TAG_LABELS = {
    TAG_DAY:   "當沖",
    TAG_NEXT:  "隔日沖",
    TAG_SHORT: "短線",
    TAG_SWING: "波段",
}

# Mapping: broker_name -> set of tags
_BROKER_TAGS: dict[str, set[str]] = {}


def _add(names: list[str], tag: str):
    for n in names:
        _BROKER_TAGS.setdefault(n, set()).add(tag)


# ---- 當沖 ----
_add([
    "星洲瑞銀", "摩根大通", "美林證券", "元大", "元富", "台新", "亞東",
    "新光", "群益", "元大中壢", "永全八德", "兆豐南門",
    "凱基大里", "凱基台北", "凱基屏東", "凱基員林", "凱基站前",
    "富邦嘉義", "永昌中正", "聯邦銀",
], TAG_DAY)

# ---- 隔日沖 ----
_add([
    "美商高盛亞", "港商野村", "星洲瑞銀", "摩根大通", "元大", "元富",
    "元大土城永", "元大太平", "元大成功", "元大竹科",
    "元大虎尾", "元大鹿港", "元大新竹", "元大彰化",
    "永昌忠孝", "永豐金虎尾", "永豐金桃園",
    "兆豐中港", "兆豐北高雄", "兆豐虎尾",
    "兆豐南京", "兆豐復興", "凱基士林", "凱基台北",
    "凱基市政", "凱基板橋", "凱基屏東",
    "富邦台中", "富邦台南", "富邦虎尾", "富邦建國",
    "統一仁愛", "統一松江", "統一南京",
    "永昌竹北", "永昌長虹", "永昌嘉義",
    "群益內湖", "群益海山", "群益館前",
], TAG_NEXT)

# ---- 短線 ----
_add([
    "星洲瑞銀", "摩根大通", "玉山證券", "康和",
    "台企銀嘉義", "台新台中", "台新建北", "台新高雄",
    "玉山台南", "兆豐大安", "兆豐忠孝", "合庫台中",
    "國泰博愛", "國票長城", "凱基市政", "凱基桃園",
    "富邦建國", "富邦員林", "統一敦南",
], TAG_SHORT)

# ---- 波段 ----
_add([
    "永豐金匯立", "台灣摩根", "港商野村", "瑞士信貸",
    "摩根大通", "元富", "台新", "宏遠", "國泰綜合", "富邦",
    "華南永昌", "新光", "群益", "中信託", "福邦",
    "元大敦化", "永昌龍潭", "台企銀桃園", "台新高雄",
    "兆豐忠孝", "兆豐復興", "國票和平", "國票長城",
    "凱基大安", "凱基中港", "富邦南屯", "統一敦南",
], TAG_SWING)


# Dealer HQ names (自營商總部 — no branch suffix)
# These broker_names in BrokerDailyStats represent the firm's proprietary desk.
DEALER_HQ_NAMES = {
    "元大", "台新", "凱基", "富邦", "統一", "群益", "兆豐", "新光",
    "永豐金", "合庫", "國票", "國泰綜合", "康和", "宏遠", "亞東", "福邦",
    "大昌", "大展", "永全", "華南永昌", "日進", "京城", "安泰", "彰銀",
    "美好", "致和", "高橋", "永興", "光和", "德信", "石橋", "北城", "奔亞",
    "盈溢", "富隆", "新百王", "寶盛", "福勝", "中農", "日茂", "台企銀",
}


def is_dealer_hq(broker_name: str) -> bool:
    """Check if a broker name is a dealer HQ (自營商總部)."""
    return broker_name in DEALER_HQ_NAMES


def get_broker_tags(broker_name: str) -> list[str]:
    """Return list of tags for a broker name. Exact match only."""
    tags = _BROKER_TAGS.get(broker_name)
    return sorted(tags) if tags else []
