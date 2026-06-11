"""
NewsFilter — 抓鉅亨網(cnyes)台股新聞,依「內文重點(7類) × 公司高層」交集條件過濾,
輸出 docs/data.js 給靜態網頁使用。

流程:
  1. 抓新聞(增量)→ 全部原始文章累積存 data/raw.json(gitignored)
  2. 每次都從 raw 重新套 keywords.json 規則過濾 + 相似新聞去重
     (所以改 keywords.json 後重跑一次,整個存檔都會套用新規則)
  3. 過濾結果有變才重寫 docs/data.js(沒變就不動檔案、不觸發 push)

用法:
    python fetch_news.py              # 初次回補 INITIAL_DAYS 天;之後增量
    python fetch_news.py --rescan     # 不抓新聞,只用現有 raw 重套規則
環境變數(皆可省略):
    INITIAL_DAYS=30        初次回補天數
    FETCH_OLDER_DAYS=N     一次性往回回補 N 天(需已有資料)
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# 排程環境(stdout 非主控台)下 Windows 預設 cp950 會讓中文輸出爆掉,強制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DOCS_DIR = os.path.join(BASE, "docs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)
RAW_JSON = os.path.join(DATA_DIR, "raw.json")
DATA_JS = os.path.join(DOCS_DIR, "data.js")
KEYWORDS_JSON = os.path.join(BASE, "keywords.json")

LOCAL_TZ = ZoneInfo("Asia/Taipei")
API = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
INITIAL_DAYS = int(os.getenv("INITIAL_DAYS", "30"))
PAGE_LIMIT = 30
PAGE_SLEEP = 0.35  # 頁間禮貌限速
# 相似新聞判定(依 30 天實測資料校準):
#   A. 跨日(7 天內)標題 bigram Jaccard ≥ 0.55 → 同則
#   B. 同一天 + 台股個股重疊 + (標籤 Jaccard ≥ 0.5 或標題相似 ≥ 0.3) → 同事件
#   C. 同一天 + 相同〈〉【】標題前綴(股東會/展望等系列) +
#      台股個股有交集且標籤 Jaccard ≥ 0.3,或雙方都沒掛台股個股且標籤 Jaccard ≥ 0.6
#      (COMPUTEX/GTC 這類大展前綴會掛在很多不同公司的新聞上,必須用個股/高標籤門檻把關;
#       0.6 是看實測:同場演講系列 ~0.8,同展不同公司 ~0.33–0.5)
#   注意:每日固定專欄(盤前要聞等)跨日標籤幾乎相同,所以 B/C 必須限同一天;
#   個股交集只看台股代號(US-NVDA 之類幾乎每篇 AI 新聞都有,會把不同公司黏在一起)。
SIM_TITLE_STRONG = 0.55
SIM_WINDOW_DAYS = 7
SUMMARY_MAX = 160

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------- 關鍵字規則

def load_rules():
    with open(KEYWORDS_JSON, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    def compile_words(words):
        out = []
        for w in words:
            if w.startswith("re:"):
                out.append(re.compile(w[3:]))
            else:
                out.append(w)
        return out

    cats = {}
    for key, c in cfg["categories"].items():
        cats[key] = {"label": c["label"], "words": compile_words(c["words"])}
    src = cfg["source"]
    return {
        "cats": cats,
        "src_words": compile_words(src["words"]),
        "src_title_words": compile_words(src.get("title_words", [])),
        "src_label": src.get("label", "公司高層"),
    }


def find_hits(text, words):
    """回傳命中的關鍵字(顯示用字串)清單,保持規則順序、不重複。"""
    hits = []
    for w in words:
        if isinstance(w, str):
            if w in text:
                hits.append(w)
        else:
            m = w.search(text)
            if m:
                hits.append(WS_RE.sub("", m.group(0)))
    return hits


# ---------------------------------------------------------------- 文字處理

def clean_text(raw_html):
    """cnyes 的 content 是 escape 過的 HTML(&lt;p&gt;…),還原成純文字。"""
    s = html.unescape(raw_html or "")
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)  # 殘餘的 &amp; &nbsp; 等
    return WS_RE.sub(" ", s).strip()


TITLE_PREFIX_RE = re.compile(r"^([〈《【\[(].{0,20}?[〉》】\])])\s*")
TITLE_DROP_RE = re.compile(r"[\s\W]+", re.UNICODE)


def title_prefix(title):
    """取媒體慣用前綴(〈台積電股東會〉等),沒有則回空字串。"""
    m = TITLE_PREFIX_RE.match(title)
    return m.group(1) if m else ""


def norm_title(title):
    """去媒體慣用前綴與標點,供相似度比對。"""
    t = TITLE_PREFIX_RE.sub("", title)
    return TITLE_DROP_RE.sub("", t).lower()


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------- 抓取

def fetch_range(start_at, end_at, seen_ids):
    """抓 [start_at, end_at] 區間內所有新聞(跳過已有 id),回傳原始項目 list。"""
    out = []
    page = 1
    last_page = 1
    while page <= last_page:
        params = {
            "startAt": start_at, "endAt": end_at,
            "page": page, "limit": PAGE_LIMIT,
        }
        for attempt in range(3):
            try:
                r = requests.get(API, params=params, headers=HEADERS, timeout=30)
                r.raise_for_status()
                payload = r.json()["items"]
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f"  ! 第 {page} 頁失敗({e}),重試…")
                time.sleep(2.0 * (attempt + 1))
        last_page = payload.get("last_page", 1)
        for item in payload.get("data", []):
            nid = item.get("newsId")
            if not nid or nid in seen_ids:
                continue
            seen_ids.add(nid)
            out.append(slim_raw(item))
        if page == 1:
            print(f"  區間共 {payload.get('total', '?')} 則 / {last_page} 頁")
        if page % 10 == 0:
            print(f"  …第 {page}/{last_page} 頁")
        page += 1
        time.sleep(PAGE_SLEEP)
    return out


def extract_cover(item):
    cover = item.get("coverSrc") or {}
    if isinstance(cover, dict):
        for size in ("m", "l", "s", "xs"):
            v = cover.get(size)
            if isinstance(v, dict) and v.get("src"):
                return v["src"]
    return None


def extract_stocks(item):
    out = []
    for s in item.get("stock") or []:
        if isinstance(s, str):
            out.append(s)
        elif isinstance(s, dict):
            name = s.get("name") or s.get("title") or ""
            symbol = s.get("symbol") or s.get("code") or ""
            symbol = re.sub(r":.*$", "", str(symbol))  # TWS:2330:STOCK → TWS
            disp = f"{name}" if not symbol else f"{name}({symbol})" if name else symbol
            if disp:
                out.append(disp)
    return out


def slim_raw(item):
    """原始 API 項目 → 我們要長期保存的欄位(含全文純文字)。"""
    nid = item["newsId"]
    return {
        "id": nid,
        "publishAt": item.get("publishAt"),
        "title": html.unescape(item.get("title") or ""),
        "text": clean_text(item.get("content")),
        "summary": clean_text(item.get("summary")),
        "tags": [t for t in (item.get("keyword") or []) if isinstance(t, str)],
        "stocks": extract_stocks(item),
        "cover": extract_cover(item),
        "url": f"https://news.cnyes.com/news/id/{nid}",
    }


def load_raw():
    if os.path.exists(RAW_JSON):
        with open(RAW_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"items": []}


def save_raw(raw):
    with open(RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)


# ---------------------------------------------------------------- 過濾 + 去重

def build_filtered(raw_items, rules):
    """套 A∧B 條件 + 相似去重,回傳給前端的精簡項目(時間正序)。"""
    kept = []
    for it in sorted(raw_items, key=lambda x: (x["publishAt"] or 0, x["id"])):
        title = it["title"]
        text = it["text"]
        full = title + "\n" + text

        # 條件 A:七類內文重點,任一類命中即可(可多類)
        hits = {}
        for key, cat in rules["cats"].items():
            h = find_hits(full, cat["words"])
            if h:
                hits[key] = h
        if not hits:
            continue

        # 條件 B:內文出現高層六詞,或標題帶「澄清」
        src_hits = find_hits(text, rules["src_words"])
        title_hits = find_hits(title, rules["src_title_words"])
        if not src_hits and not title_hits:
            continue

        dt = datetime.fromtimestamp(it["publishAt"], tz=timezone.utc).astimezone(LOCAL_TZ)
        iso = dt.isocalendar()
        monday = dt.date() - timedelta(days=iso.weekday - 1)
        sunday = monday + timedelta(days=6)
        summary = it["summary"] or text[:SUMMARY_MAX]
        if len(summary) > SUMMARY_MAX:
            summary = summary[:SUMMARY_MAX].rstrip() + "…"
        kept.append({
            "id": it["id"],
            "title": title,
            "url": it["url"],
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "week": f"{iso.year}-W{iso.week:02d}",
            "week_range": f"{monday.month}/{monday.day}–{sunday.month}/{sunday.day}",
            "ts": it["publishAt"],
            "cats": list(hits.keys()),
            "hits": hits,
            "src": src_hits,
            "src_title": title_hits,
            "summary": summary,
            "tags": it["tags"][:8],
            "stocks": it["stocks"][:6],
            "cover": it["cover"],
            "dupes": [],
        })

    # 相似新聞去重:後出現者併入最早的相似主篇(規則見檔頭常數說明)
    primaries = []
    sigs = []  # (bigrams, prefix, stock_set, tag_set)
    for item in kept:
        sig = (
            bigrams(norm_title(item["title"])),
            title_prefix(item["title"]),
            {s for s in item["stocks"] if s[:1].isdigit()},  # 台股代號
            set(item["tags"]),
        )
        best = None
        for p, ps in zip(primaries, sigs):
            if item["ts"] - p["ts"] > SIM_WINDOW_DAYS * 86400:
                continue
            tsim = jaccard(sig[0], ps[0])
            if tsim >= SIM_TITLE_STRONG:
                best = p
                break
            if item["date"] == p["date"]:
                tag_sim = jaccard(sig[3], ps[3])
                if (sig[2] & ps[2]) and (tag_sim >= 0.5 or tsim >= 0.3):
                    best = p
                    break
                if sig[1] and sig[1] == ps[1]:
                    if (sig[2] & ps[2]) and tag_sim >= 0.3:
                        best = p
                        break
                    if not sig[2] and not ps[2] and tag_sim >= 0.6:
                        best = p
                        break
        if best is not None:
            best["dupes"].append({
                "id": item["id"], "title": item["title"], "url": item["url"],
                "date": item["date"], "time": item["time"],
            })
        else:
            primaries.append(item)
            sigs.append(sig)
    return primaries


def cat_labels(rules):
    return {key: cat["label"] for key, cat in rules["cats"].items()}


def write_data_js(items, rules):
    """有變化才寫檔;回傳是否有寫。"""
    payload = {
        "tz": "Asia/Taipei",
        "source": "鉅亨網",
        "cat_labels": cat_labels(rules),
        "src_label": rules["src_label"],
        "items": items,
    }
    content = "window.NEWS_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n"
    if os.path.exists(DATA_JS):
        with open(DATA_JS, "r", encoding="utf-8") as f:
            if f.read() == content:
                return False
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write(content)
    return True


# ---------------------------------------------------------------- 主流程

def main():
    rescan = "--rescan" in sys.argv
    raw = load_raw()
    items = raw["items"]
    seen_ids = {it["id"] for it in items}
    now = int(time.time())

    if not rescan:
        older_days = int(os.getenv("FETCH_OLDER_DAYS") or 0)
        if older_days:
            if not items:
                print("✗ 沒有既有資料;請先正常跑一次 fetch_news.py。")
                return
            oldest = min(it["publishAt"] for it in items)
            start, end = oldest - older_days * 86400, oldest
            print(f"→ 往回回補 {older_days} 天…")
        elif items:
            newest = max(it["publishAt"] for it in items)
            start, end = newest - 7200, now  # 2 小時重疊緩衝,防漏
            print("→ 增量更新…")
        else:
            start, end = now - INITIAL_DAYS * 86400, now
            print(f"→ 初次回補最近 {INITIAL_DAYS} 天…")
        new_items = fetch_range(start, end, seen_ids)
        print(f"✓ 本次新抓 {len(new_items)} 則(原始)")
        if new_items:
            items.extend(new_items)
            save_raw(raw)
    else:
        print(f"→ rescan:不抓新聞,用現有 {len(items)} 則原始資料重套規則")

    rules = load_rules()
    filtered = build_filtered(items, rules)
    n_dupes = sum(len(it["dupes"]) for it in filtered)
    print(f"✓ 原始 {len(items)} 則 → 符合條件 {len(filtered) + n_dupes} 則"
          f"(顯示 {len(filtered)} 則 + 相似併入 {n_dupes} 則)")
    labels = cat_labels(rules)
    for key, label in labels.items():
        n = sum(1 for it in filtered if key in it["cats"])
        print(f"   {label}: {n}")

    if write_data_js(filtered, rules):
        print(f"✓ 已更新 {DATA_JS}")
    else:
        print("✓ 過濾結果無變化,不重寫 data.js(不觸發推送)。")


if __name__ == "__main__":
    main()
