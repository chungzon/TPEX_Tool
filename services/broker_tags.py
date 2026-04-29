"""Known broker trading style tags based on market research.

Tags:
  沖 = 當沖 (day trading)
  隔 = 隔日沖 (next-day flip)
  短 = 短線 (short-term)
  波 = 波段 (swing / medium-term)

A broker can have multiple tags.
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
    "新加坡瑞銀", "摩根大通", "美林", "元大", "元富", "台新", "亞東",
    "新光", "群益", "元大-中壢", "永全-八德", "兆豐-南門",
    "凱基-大里", "凱基-台北", "凱基-屏東", "凱基-員林", "凱基-站前",
    "富邦-嘉義", "華南-中正", "聯邦-富強",
], TAG_DAY)

# ---- 隔日沖 ----
_add([
    "美商高盛", "港商野村", "新加坡瑞銀", "摩根大通", "元大", "元富",
    "元大-土城永寧", "元大-太平", "元大-成功", "元大-竹科",
    "元大-虎尾", "元大-鹿港", "元大-新竹", "元大-彰化",
    "日盛-忠孝", "永豐金-虎尾", "永豐金-桃園",
    "兆豐-中港", "兆豐-北高雄", "兆豐-虎尾",
    "兆豐-南京", "兆豐-復興", "凱基-士林", "凱基-台北",
    "凱基-市政", "凱基-板橋", "凱基-屏東",
    "富邦-台中", "富邦-台南", "富邦-虎尾", "富邦-建國",
    "統一-仁愛", "統一-松江", "統一-南京",
    "華南-竹北", "華南-長虹", "華南-嘉義",
    "群益-內湖", "群益-海山", "群益-館前",
], TAG_NEXT)

# ---- 短線 ----
_add([
    "新加坡瑞銀", "摩根大通", "玉山", "康和",
    "台企銀-嘉義", "台新-台中", "台新-建北", "台新-高雄",
    "玉山-台南", "兆豐-大安", "兆豐-忠孝", "合庫-台中",
    "國泰-博愛", "國票-長城", "凱基-市政", "凱基-桃園",
    "富邦-建國", "富邦-員林", "統一-敦南",
], TAG_SHORT)

# ---- 波段 ----
_add([
    "台灣匯立", "台灣摩根", "港商野村", "瑞士信貸",
    "摩根大通", "元富", "台新", "宏遠", "國泰綜合", "富邦",
    "華南永昌", "新光", "群益", "中國信託", "福邦",
    "元大-敦化", "日盛-龍潭", "台企銀-桃園", "台新-高雄",
    "兆豐-忠孝", "兆豐-復興", "國票-和平", "國票-長城",
    "凱基-大安", "凱基-中港", "富邦-南屯", "統一-敦南",
], TAG_SWING)


def get_broker_tags(broker_name: str) -> list[str]:
    """Return list of tags for a broker name.

    Tries exact match first, then falls back to matching the firm name
    (part before the hyphen).
    """
    # Exact match
    tags = _BROKER_TAGS.get(broker_name)
    if tags:
        return sorted(tags)

    # Try firm name only (e.g. "永豐金-博愛" → try "永豐金")
    if "-" in broker_name:
        firm = broker_name.split("-")[0]
        tags = _BROKER_TAGS.get(firm)
        if tags:
            return sorted(tags)

    return []
