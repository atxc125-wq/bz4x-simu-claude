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
    for page in range(1, 13):
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
                    for j in range(i+1, min(len(lines), i+50)):
                        if "【所在地" in lines[j]: break
                        kw = parse_power_kw(lines[j])
                        if kw: powers.append(kw)
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
    # 東北自動車道（補完）
    {"name": "羽生PA（下り）",   "address": "埼玉県羽生市 東北自動車道", "type": "sa_pa", "powers": [90, 50]},
    {"name": "羽生PA（上り）",   "address": "埼玉県羽生市 東北自動車道", "type": "sa_pa", "powers": [90, 50]},
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

def main():
    log.info("=== BZ4X Charger Data Building Start ===")
    api_key = get_api_key()

    # 1. 収集
    flash_data = scrape_flash()
    emp_data = scrape_emp()
    raw_data = flash_data + emp_data + SA_PA_SEED
    log.info(f"Added {len(SA_PA_SEED)} seed SA/PA entries")
    total = len(raw_data)
    log.info(f"Total entries: {total} (Flash: {len(flash_data)}, EMP: {len(emp_data)}, Seed: {len(SA_PA_SEED)})")

    # 2. ジオコーディング（進捗表示付き）
    log.info(f"Starting Geocoding for {total} spots...")
    geocoded = []
    for i, d in enumerate(raw_data):
        
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

    # 6. 保存
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chargers_data.js")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("const CHARGER_DATA = ")
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write(";")

    log.info(f"Successfully generated: {out_path} (Total spots: {len(merged)})")
    log.info("Check 'build_chargers.log' for detailed execution history.")

if __name__ == "__main__":
    main()