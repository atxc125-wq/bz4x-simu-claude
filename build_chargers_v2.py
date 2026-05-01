"""
BZ4X Range Predictor - New Places API Charger Fetcher
Google Mapsの「New Places API」を使用して、90kW以上の高出力充電器を抽出します。
※ V1データ(chargers_data.js)と照合し、重複するものはV1（FLASH等）を優先して破棄します。
"""

import requests
import json
import os
import time
import logging
import math

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("charger_v2")

def get_api_key():
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        log.error("GOOGLE_API_KEYが設定されていません。")
        exit(1)
    return key

API_KEY = get_api_key()
NEW_PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"

# --- 検索クエリ（先ほど作成した完全版リスト） ---
SEARCH_QUERIES = [
    "千葉県 浦安市 EV急速充電器",
    "神奈川県 箱根町 EV急速充電器",
    "北海道 千歳市 EV急速充電器",
    "北海道 旭川市 EV急速充電器",
    "北海道 北広島市 EV急速充電器",
    "東京都 23区 EV急速充電器",
    "神奈川県 横浜市 EV急速充電器",
    "大阪府 大阪市 EV急速充電器",
    "愛知県 名古屋市 EV急速充電器",
    "北海道 札幌市 EV急速充電器",
    "福岡県 福岡市 EV急速充電器",
    "神奈川県 川崎市 EV急速充電器",
    "兵庫県 神戸市 EV急速充電器",
    "京都府 京都市 EV急速充電器",
    "埼玉県 さいたま市 EV急速充電器",
    "広島県 広島市 EV急速充電器",
    "宮城県 仙台市 EV急速充電器",
    "千葉県 千葉市 EV急速充電器",
    "福岡県 北九州市 EV急速充電器",
    "大阪府 堺市 EV急速充電器",
    "新潟県 新潟市 EV急速充電器",
    "静岡県 浜松市 EV急速充電器",
    "熊本県 熊本市 EV急速充電器",
    "神奈川県 相模原市 EV急速充電器",
    "岡山県 岡山市 EV急速充電器",
    "静岡県 静岡市 EV急速充電器",
    "千葉県 船橋市 EV急速充電器",
    "埼玉県 川口市 EV急速充電器",
    "鹿児島県 鹿児島市 EV急速充電器",
    "東京都 八王子市 EV急速充電器",
    "兵庫県 姫路市 EV急速充電器",
    "栃木県 宇都宮市 EV急速充電器",
    "愛媛県 松山市 EV急速充電器",
    "大阪府 東大阪市 EV急速充電器",
    "兵庫県 西宮市 EV急速充電器",
    "トヨタ EV急速充電",
    "レクサス EV急速充電",
    "日産 EV急速充電",
    "ホンダ EV急速充電",
    "三菱自動車 EV急速充電",
    "スーパーオートバックス EV急速充電",
    "道の駅 EV急速充電器",
    "イオンモール EV急速充電器",
    "ファミリーマート EV急速充電"
]

# --- 距離計算関数 (緯度経度から距離を求める) ---
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# --- V1（絶対的正義）データの読み込み ---
def load_v1_data():
    v1_file = "chargers_data.js"
    if not os.path.exists(v1_file):
        return []
    try:
        with open(v1_file, "r", encoding="utf-8") as f:
            content = f.read()
        # "const CHARGER_DATA = " を取り除いてJSONとしてパース
        json_str = content.replace("const CHARGER_DATA = ", "").strip()
        if json_str.endswith(";"):
            json_str = json_str[:-1]
        return json.loads(json_str)
    except Exception as e:
        log.error(f"V1データの読み込みエラー: {e}")
        return []

def fetch_high_power_chargers(query):
    log.info(f"検索開始: {query}")
    high_power_chargers = []
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.evChargeOptions"
    }
    payload = {
        "textQuery": query,
        "languageCode": "ja"
    }

    try:
        response = requests.post(NEW_PLACES_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        for place in result.get("places", []):
            ev_options = place.get("evChargeOptions", {})
            connectors = ev_options.get("connectorAggregation", [])

            max_kw = 0
            for conn in connectors:
                kw = conn.get("maxChargeRateKw", 0)
                if kw > max_kw:
                    max_kw = kw

            if max_kw >= 90:
                name = place.get("displayName", {}).get("text", "Unknown")
                address = place.get("formattedAddress", "")
                lat = place.get("location", {}).get("latitude")
                lng = place.get("location", {}).get("longitude")
                
                # 💡 V2で見つけるものは、どんなに高出力でもすべて「通常(normal/熱ダレあり)」とする
                charger = {
                    "name": f"{name} ({int(max_kw)}kW)",
                    "address": address,
                    "type": "normal",
                    "powers": [int(max_kw)],
                    "lat": lat,
                    "lng": lng,
                    "powerKw": int(max_kw),
                    "direction": "none"
                }
                high_power_chargers.append(charger)

    except Exception as e:
        log.error(f"APIリクエストエラー ({query}): {e}")

    time.sleep(1)
    return high_power_chargers

def main():
    # 1. V1データを読み込む（これが正解データ）
    v1_chargers = load_v1_data()
    log.info(f"V1データ（FLASH/SAPA）を {len(v1_chargers)} 件読み込みました。重複チェックに使用します。")

    all_high_power_chargers = []
    
    for query in SEARCH_QUERIES:
        chargers = fetch_high_power_chargers(query)
        
        for c in chargers:
            is_duplicate = False
            
            # 💡 V1データとの重複チェック（半径150m以内にV1のピンがあれば捨てる！）
            for v1_c in v1_chargers:
                v1_lat = v1_c.get("lat", 0)
                v1_lng = v1_c.get("lng", 0)
                if haversine_km(c["lat"], c["lng"], v1_lat, v1_lng) < 0.15: # 150m以内
                    is_duplicate = True
                    log.info(f"V1データと重複するため破棄: {c['name']}")
                    break
            
            # V2同士の重複チェック（同じ場所が複数回検索された場合）
            if not is_duplicate:
                for existing in all_high_power_chargers:
                    if haversine_km(c["lat"], c["lng"], existing["lat"], existing["lng"]) < 0.15:
                        is_duplicate = True
                        break
                    
            # 完全に新規の「normal」高出力機だけを追加
            if not is_duplicate:
                all_high_power_chargers.append(c)
                log.info(f"新規追加: {c['name']}")

    log.info(f"最終的に {len(all_high_power_chargers)} 件の新規充電器をV2として保存します。")

    # ファイル書き出し
    output_filename = "chargers_v2_data.js"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write("const CHARGER_V2_DATA = ")
        json.dump(all_high_power_chargers, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    
    log.info(f"{output_filename} を生成しました！")

if __name__ == "__main__":
    main()
