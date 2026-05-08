"""
BZ4X Range Predictor ? Charger Data Builder (完全自動・名称正規化マージ・ログ機能復活版)
全国のSA/PAを緯度経度と正規化名称で統合し、詳細なログを build_chargers.log に保存します。
"""

import json, os, time, re, sys, math, logging
import requests
from bs4 import BeautifulSoup

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BZ4X-RangePredictor/1.0)"}

# ── ログ設定 ──
_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_chargers.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8", mode="w"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("charger_builder")

def get_api_key():
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        key = input("Google Maps API Key (Geocoding用) を入力してください: ").strip()
    return key

def geocode(address, api_key):
    try:
        r = requests.get(GEOCODING_URL, params={"address": address, "key": api_key, "language": "ja"}, timeout=10)
        d = r.json()
        if d["status"] == "OK":
            loc = d["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception as e:
        log.error(f"Geocode error '{address}': {e}")
    return None, None

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    dLat, dLng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def parse_power_kw(text):
    m = re.search(r"(\d+)\s*[kK][wW]", text)
    return int(m.group(1)) if m else None

def normalize_sa_pa_name(name):
    """ SA/PA名を正規化してマージしやすくする """
    # 修正箇所：第2引数の 'OO' を 'O' に、'YY' を 'Y' に修正
    n = name.translate(str.maketrans('０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ','0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')).replace(" ","").replace("　","")
    match = re.search(r'([^0-9]+[SP]A).*(上り|下り|上線|下線|INBOUND|OUTBOUND)', n)
    if match:
        base = match.group(1)
        direction = match.group(2).replace("上線","上り").replace("下線","下り").replace("INBOUND","上り").replace("OUTBOUND","下り")
        if "自動車道" in base: base = base.split("自動車道")[-1]
        if "高速" in base: base = base.split("高速")[-1]
        return f"{base}{direction}"
    return n

# ── 各サイトの取得ロジック ──

def scrape_flash():
    log.info("Scraping Flash chargers...")
    out = []
    try:
        r = requests.get("https://ev-charger.jp/area/", headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        blocks = soup.find_all("div", class_=re.compile(r"weluka-col-inner"))
        for block in blocks:
            name_s = block.find("span", style=re.compile(r"font-size:\s*18px"))
            if not name_s: continue
            name = name_s.get_text(strip=True)
            addr = block.find("p", class_="resizing").get_text(strip=True) if block.find("p", class_="resizing") else ""
            pw = 90
            p_tag = block.find(lambda t: t.name == "p" and "最大出力：" in t.get_text())
            if p_tag:
                m = re.search(r"(\d+)", p_tag.get_text())
                if m: pw = int(m.group(1))
            if addr: out.append({"name": name, "address": addr, "type": "flash", "powers": [pw]})
    except Exception as e:
        log.error(f"Flash scrape error: {e}")
    return out

def scrape_emp():
    log.info("Scraping EMP SA/PA chargers...")
    out = []
    search_url = "https://www.evcharger.e-mobipower.co.jp/ia-eweb/em/charger_spot_list/?category=1101&check_qc_contained=on"
    for page in range(1, 30):  # 最大29ページまで（約580件対応）
        try:
            r = requests.get(f"{search_url}&page={page}", headers=HEADERS, timeout=20)
            lines = [t.strip() for t in BeautifulSoup(r.text, "html.parser").get_text(separator="\n").splitlines() if t.strip()]
            for i, line in enumerate(lines):
                if "【所在地" in line:
                    addr = re.sub(r'.*【所在地\s*】\s*', '', line).strip()
                    name = ""
                    for j in range(i-1, max(-1, i-10), -1):
                        if any(x in lines[j] for x in ["】", "■", "件中", "絞り込み", "≪", "閉じる"]): continue
                        if lines[j]: name = lines[j]; break
                    if not name or len(addr) < 5: continue
                    powers = []
                    for j in range(i+1, min(len(lines), i+200)):
                        if "【所在地" in lines[j]: break
                        kw = parse_power_kw(lines[j])
                        if kw and kw not in powers: powers.append(kw)
                    is_hw = any(x in (name + addr) for x in ["SA", "PA", "サービスエリア", "パーキングエリア", "高速", "自動車道"])
                    out.append({"name": name, "address": addr, "type": "sa_pa" if is_hw else "emp", "powers": powers or [50]})
        except Exception as e:
            log.error(f"EMP page {page} error: {e}")
            break
    return out

# ── 主要SA/PAシードデータ（スクレイピングで取れない場合の補完用） ──
# 東名・新東名・名神・新名神などの重要区間を手動で確保
SA_PA_SEED = [
    # 東名高速（下り=大阪方面）
    {"name": "海老名SA（下り）", "address": "神奈川県海老名市大谷南 東名高速道路", "type": "sa_pa", "powers": [90, 50]},
    {"name": "足柄SA（下り）",   "address": "静岡県小山町須走 東名高速道路",     "type": "sa_pa", "powers": [50]},
    {"name": "富士川SA（下り）", "address": "静岡県富士市岩渕 東名高速道路",     "type": "sa_pa", "powers": [50]},
    {"name": "浜名湖SA（下り）", "address": "静岡県浜松市西区 東名高速道路",     "type": "sa_pa", "powers": [90, 50]},
    {"name": "岡崎SA（下り）",   "address": "愛知県岡崎市 東名高速道路",         "type": "sa_pa", "powers": [50]},
    # 東名高速（上り=東京方面）
    {"name": "海老名SA（上り）", "address": "神奈川県海老名市大谷南 東名高速道路", "type": "sa_pa", "powers": [90, 50]},
    {"name": "足柄SA（上り）",   "address": "静岡県小山町須走 東名高速道路",       "type": "sa_pa", "powers": [50]},
    {"name": "富士川SA（上り）", "address": "静岡県富士市岩渕 東名高速道路",       "type": "sa_pa", "powers": [50]},
    {"name": "浜名湖SA（上り）", "address": "静岡県浜松市西区 東名高速道路",       "type": "sa_pa", "powers": [90, 50]},
    {"name": "岡崎SA（上り）",   "address": "愛知県岡崎市 東名高速道路",           "type": "sa_pa", "powers": [50]},
    # 新東名高速（下り）
    {"name": "NEOPASA清水（下り）",  "address": "静岡県静岡市清水区 新東名高速道路",  "type": "sa_pa", "powers": [150, 90]},
    {"name": "NEOPASA静岡（下り）",  "address": "静岡県静岡市葵区 新東名高速道路",    "type": "sa_pa", "powers": [150, 90]},
    {"name": "NEOPASA浜松（下り）",  "address": "静岡県浜松市浜北区 新東名高速道路", "type": "sa_pa", "powers": [150, 90]},
    {"name": "NEOPASA岡崎（下り）",  "address": "愛知県岡崎市 新東名高速道路",       "type": "sa_pa", "powers": [150, 90]},
    {"name": "長篠設楽原PA（下り）", "address": "愛知県新城市 新東名高速道路",       "type": "sa_pa", "powers": [50]},
    {"name": "長篠設楽原PA（上り）", "address": "愛知県新城市 新東名高速道路",       "type": "sa_pa", "powers": [50]},
    # 新東名高速（上り）
    {"name": "NEOPASA清水（上り）",  "address": "静岡県静岡市清水区 新東名高速道路",  "type": "sa_pa", "powers": [150, 90]},
    {"name": "NEOPASA静岡（上り）",  "address": "静岡県静岡市葵区 新東名高速道路",    "type": "sa_pa", "powers": [150, 90]},
    {"name": "NEOPASA浜松（上り）",  "address": "静岡県浜松市浜北区 新東名高速道路", "type": "sa_pa", "powers": [150, 90]},
    {"name": "NEOPASA岡崎（上り）",  "address": "愛知県岡崎市 新東名高速道路",       "type": "sa_pa", "powers": [150, 90]},
    # 名神高速
    {"name": "多賀SA（下り）",   "address": "滋賀県多賀町 名神高速道路",       "type": "sa_pa", "powers": [90, 50]},
    {"name": "多賀SA（上り）",   "address": "滋賀県多賀町 名神高速道路",       "type": "sa_pa", "powers": [90, 50]},
    {"name": "草津PA（下り）",   "address": "滋賀県草津市 名神高速道路",       "type": "sa_pa", "powers": [50]},
    {"name": "桂川PA（下り）",   "address": "京都府京都市西京区 名神高速道路", "type": "sa_pa", "powers": [50]},
    # 新名神高速
    {"name": "甲南PA（下り）",   "address": "滋賀県甲賀市 新名神高速道路",     "type": "sa_pa", "powers": [90]},
    {"name": "甲南PA（上り）",   "address": "滋賀県甲賀市 新名神高速道路",     "type": "sa_pa", "powers": [90]},
    {"name": "土山SA（下り）",   "address": "滋賀県甲賀市土山 新名神高速道路", "type": "sa_pa", "powers": [90, 50]},
    {"name": "土山SA（上り）",   "address": "滋賀県甲賀市土山 新名神高速道路", "type": "sa_pa", "powers": [90, 50]},
    # 東北自動車道
    {"name": "羽生PA（下り）",       "address": "埼玉県羽生市 東北自動車道",                   "type": "sa_pa", "powers": [90, 50]},
    {"name": "羽生PA（上り）",       "address": "埼玉県羽生市 東北自動車道",                   "type": "sa_pa", "powers": [90, 50]},
    {"name": "蓮田SA（下り）",       "address": "埼玉県蓮田市 東北自動車道",                   "type": "sa_pa", "powers": [90, 50]},
    {"name": "蓮田SA（上り）",       "address": "埼玉県蓮田市 東北自動車道",                   "type": "sa_pa", "powers": [90, 50]},
    {"name": "佐野SA（下り）",       "address": "栃木県佐野市 東北自動車道",                   "type": "sa_pa", "powers": [90]},
    {"name": "佐野SA（上り）",       "address": "栃木県佐野市 東北自動車道",                   "type": "sa_pa", "powers": [90]},
    {"name": "都賀西方PA（下り）",   "address": "栃木県栃木市都賀町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "上河内SA（下り）",     "address": "栃木県宇都宮市上河内 東北自動車道",           "type": "sa_pa", "powers": [50]},
    {"name": "那須高原SA（下り）",   "address": "栃木県那須郡那須町 東北自動車道",             "type": "sa_pa", "powers": [90]},
    {"name": "那須高原SA（上り）",   "address": "栃木県那須郡那須町 東北自動車道",             "type": "sa_pa", "powers": [90]},
    {"name": "安積PA（下り）",       "address": "福島県郡山市安積町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "安積PA（上り）",       "address": "福島県郡山市安積町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "安達太良SA（下り）",   "address": "福島県本宮市 東北自動車道",                   "type": "sa_pa", "powers": [90]},
    {"name": "国見SA（下り）",       "address": "福島県伊達郡国見町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "菅生PA（下り）",       "address": "宮城県柴田郡村田町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "鶴巣PA（下り）",       "address": "宮城県黒川郡大和町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "長者原SA（下り）",     "address": "宮城県大崎市古川 東北自動車道",               "type": "sa_pa", "powers": [50]},
    {"name": "古川SA（下り）",       "address": "宮城県大崎市古川 東北自動車道",               "type": "sa_pa", "powers": [90]},
    {"name": "前沢SA（下り）",       "address": "岩手県奥州市前沢 東北自動車道",               "type": "sa_pa", "powers": [50]},
    {"name": "紫波SA（下り）",       "address": "岩手県紫波郡紫波町 東北自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "岩手山SA（下り）",     "address": "岩手県八幡平市 東北自動車道",                 "type": "sa_pa", "powers": [50]},
    {"name": "花輪SA（下り）",       "address": "秋田県鹿角市花輪 東北自動車道",               "type": "sa_pa", "powers": [50]},
    {"name": "津軽SA（下り）",       "address": "青森県つがる市 東北自動車道",                 "type": "sa_pa", "powers": [50]},
    # 磐越自動車道
    {"name": "阿武隈高原SA（下り）", "address": "福島県石川郡玉川村 磐越自動車道",             "type": "sa_pa", "powers": [50]},
    {"name": "阿武隈高原SA（上り）", "address": "福島県石川郡玉川村 磐越自動車道",             "type": "sa_pa", "powers": [50]},
    # 常磐自動車道
    {"name": "守谷SA（下り）",       "address": "茨城県守谷市 常磐自動車道",                   "type": "sa_pa", "powers": [90, 50]},
    {"name": "守谷SA（上り）",       "address": "茨城県守谷市 常磐自動車道",                   "type": "sa_pa", "powers": [90, 50]},
    {"name": "中郷SA（下り）",       "address": "茨城県北茨城市 常磐自動車道",                 "type": "sa_pa", "powers": [50]},
    {"name": "南相馬鹿島SA（下り）", "address": "福島県南相馬市 常磐自動車道",                 "type": "sa_pa", "powers": [50]},
    # 中央自動車道
    {"name": "談合坂SA（下り）", "address": "山梨県上野原市 中央自動車道", "type": "sa_pa", "powers": [90, 50]},
    {"name": "談合坂SA（上り）", "address": "山梨県上野原市 中央自動車道", "type": "sa_pa", "powers": [90, 50]},
    {"name": "双葉SA（下り）",   "address": "山梨県甲斐市 中央自動車道",   "type": "sa_pa", "powers": [90, 50]},
    {"name": "双葉SA（上り）",   "address": "山梨県甲斐市 中央自動車道",   "type": "sa_pa", "powers": [90, 50]},
    {"name": "駒ヶ岳SA（下り）", "address": "長野県駒ヶ根市 中央自動車道", "type": "sa_pa", "powers": [50]},
    {"name": "駒ヶ岳SA（上り）", "address": "長野県駒ヶ根市 中央自動車道", "type": "sa_pa", "powers": [50]},
    # 関越自動車道
    {"name": "三芳PA（下り）",   "address": "埼玉県三芳町 関越自動車道", "type": "sa_pa", "powers": [90, 50]},
    {"name": "三芳PA（上り）",   "address": "埼玉県三芳町 関越自動車道", "type": "sa_pa", "powers": [90, 50]},
    {"name": "高坂SA（下り）",   "address": "埼玉県東松山市 関越自動車道", "type": "sa_pa", "powers": [50]},
    {"name": "高坂SA（上り）",   "address": "埼玉県東松山市 関越自動車道", "type": "sa_pa", "powers": [50]},
]

def load_existing(path):
    """既存の chargers_data.js があれば読み込む"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        import re as _re
        m = _re.search(r'const CHARGER_DATA\s*=\s*(\[[\s\S]*?\]);', src)
        if m:
            data = json.loads(m.group(1))
            log.info(f"Loaded existing data: {len(data)} entries")
            return data
    except Exception as e:
        log.warning(f"Could not load existing data: {e}")
    return []

def find_duplicate(entry, existing, dist_km=1.5):
    """既存データと重複するエントリを返す（なければNone）"""
    entry_name = entry.get("name", "")
    entry_is_up   = "上り" in entry_name
    entry_is_down = "下り" in entry_name
    for e in existing:
        if e.get("name") == entry_name:
            return e
        if e.get("lat") and entry.get("lat"):
            if haversine_km(e["lat"], e["lng"], entry["lat"], entry["lng"]) < dist_km:
                # 上り/下りが逆向きのペアは同座標でも別施設 → 重複扱いしない
                e_name = e.get("name", "")
                e_is_up   = "上り" in e_name
                e_is_down = "下り" in e_name
                if (entry_is_up and e_is_down) or (entry_is_down and e_is_up):
                    continue
                return e
    return None

def is_duplicate(entry, existing, dist_km=1.5):
    return find_duplicate(entry, existing, dist_km) is not None

def main():
    log.info("=== BZ4X Charger Data Building Start ===")
    api_key = get_api_key()

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chargers_data.js")

    # 0. 既存データ読み込み（上書き防止）
    existing_data = load_existing(out_path)

    # 1. 収集
    flash_data = scrape_flash()
    emp_data = scrape_emp()
    raw_data = flash_data + emp_data + SA_PA_SEED
    log.info(f"Scraped: Flash={len(flash_data)}, EMP={len(emp_data)}, Seed={len(SA_PA_SEED)}")

    # 既存エントリのpowersを最新スクレイピング結果でアップデート（150kW追加等に対応）
    update_count = 0
    new_entries = []
    for d in raw_data:
        match = find_duplicate(d, existing_data)
        if match:
            old_powers = set(match.get("powers", [match.get("powerKw", 0)]))
            new_powers = set(d.get("powers", []))
            merged = sorted(old_powers | new_powers, reverse=True)
            if merged != sorted(old_powers, reverse=True):
                match["powers"] = merged
                match["powerKw"] = merged[0]
                update_count += 1
                log.info(f"Updated: {match['name']} {sorted(old_powers,reverse=True)} → {merged}")
        else:
            new_entries.append(d)
    log.info(f"Updated {update_count} existing entries / New to geocode: {len(new_entries)} (skipped {len(raw_data)-update_count-len(new_entries)} unchanged)")
    total = len(new_entries)

    # 2. ジオコーディング（進捗表示付き）
    log.info(f"Starting Geocoding for {total} new spots...")
    geocoded = []
    for i, d in enumerate(new_entries):
        
        # ▼▼▼ ここを修正 ▼▼▼
        # 施設名と住所を合体させて、Googleの検索精度を上げる
        search_query = f'{d["name"]} {d["address"]}'
        lat, lng = geocode(search_query, api_key)
        # ▲▲▲ ここまで ▲▲▲
        
        if lat:
            d["lat"], d["lng"] = lat, lng
            geocoded.append(d)
            
        if (i + 1) % 50 == 0 or (i + 1) == total:
            log.info(f"Progress: {i + 1} / {total} ({(i + 1)/total*100:.1f}%) processed...")
            
        time.sleep(0.1)
    log.info(f"Geocoding finished. Success: {len(geocoded)} / {total}")

    # 3. 近接マージ
    log.info("Merging nearby chargers...")
    merged = []
    for g in geocoded:
        g["norm_name"] = normalize_sa_pa_name(g["name"])
        found = False
        for m in merged:
            dist = haversine_km(g["lat"], g["lng"], m["lat"], m["lng"])
            if dist < 2.0:
                m["powers"].extend(g["powers"])
                if "SA" in g["norm_name"] or "PA" in g["norm_name"]:
                    m["name"] = g["name"]
                found = True
                break
        if not found:
            merged.append(g)

    # 4. 整形
    for m in merged:
        m["powers"] = sorted(list(set(m["powers"])), reverse=True)
        m["powerKw"] = m["powers"][0]
        if any(x in m["name"] for x in ["上り", "上線"]): m["direction"] = "up"
        elif any(x in m["name"] for x in ["下り", "下線"]): m["direction"] = "down"
        if "norm_name" in m: del m["norm_name"]

    # 5. フィルタリング（無効データ除去）
    before = len(merged)
    merged = [m for m in merged if m.get("powerKw", 0) >= 10]
    log.info(f"Filtered out {before - len(merged)} entries with powerKw < 10")

    # 6. 既存データと統合して保存
    final = existing_data + merged
    log.info(f"Final dataset: {len(existing_data)} existing + {len(merged)} new = {len(final)} total")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("const CHARGER_DATA = ")
        json.dump(final, f, ensure_ascii=False, indent=2)
        f.write(";")

    log.info(f"Successfully saved: {out_path} ({len(final)} total spots)")
    log.info("Check 'build_chargers.log' for detailed execution history.")

if __name__ == "__main__":
    main()