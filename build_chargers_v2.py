"""
BZ4X Range Predictor - New Places API Charger Fetcher
Google Mapsの「New Places API」を使用して、出力kW数（evChargeOptions）を取得し、
90kW以上の高出力充電器（特に150kW機）を抽出する特化型スクリプトです。
"""

import requests
import json
import os
import time
import logging

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

# 検索したいキーワードや地域のリスト
# 日本全国を網羅するため、都道府県名や主要都市名を入れると効果的です
SEARCH_QUERIES = [
    "宮城県 仙台市 EV急速充電器",
    "東京都 EV急速充電器",
    "愛知県 名古屋市 EV急速充電器",
    "大阪府 EV急速充電器",
    # --- 重点カバーエリア（生活圏・レジャー・遠征先） ---
    "東京都 東大和市 EV急速充電器",
    "東京都 武蔵村山市 EV急速充電器",
    "東京都 立川市 EV急速充電器",
    "千葉県 浦安市 EV急速充電器",
    "神奈川県 箱根町 EV急速充電器",
    "北海道 千歳市 EV急速充電器",
    "北海道 旭川市 EV急速充電器",
    "北海道 北広島市 EV急速充電器",
    # --- 日本の人口トップ30都市（広域絨毯爆撃） ---
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

    # --- 国産ディーラー系 ---
    "トヨタ EV急速充電",
    "レクサス EV急速充電",
    "日産 EV急速充電",
    "ホンダ EV急速充電",
    "三菱自動車 EV急速充電", # 「三菱」だけだと電機メーカー等が混ざるため

    # --- 商業施設・その他 ---
    "スーパーオートバックス EV急速充電",
    "道の駅 EV急速充電器",
    "イオンモール EV急速充電器",
    "ファミリーマート EV急速充電"
    "セブンイレブン　EV急速充電"# 一部で100kW機導入が進んでいるため念のため
]


def fetch_high_power_chargers(query):
    log.info(f"検索開始: {query}")
    high_power_chargers = []
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        # ★ここが新APIのキモ！欲しいデータだけを指定する
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

            # 90kW以上の充電器のみを抽出（150kWを狙い撃ち！）
            if max_kw >= 90:
                name = place.get("displayName", {}).get("text", "Unknown")
                address = place.get("formattedAddress", "")
                lat = place.get("location", {}).get("latitude")
                lng = place.get("location", {}).get("longitude")
                
                # シミュレーターの形式に変換
                charger = {
                    "name": f"{name} ({int(max_kw)}kW)",
                    "address": address,
                    "type": "flash" if max_kw >= 150 else "normal", # 150kW以上は水冷(flash)扱い
                    "powers": [int(max_kw)],
                    "lat": lat,
                    "lng": lng,
                    "powerKw": int(max_kw),
                    "direction": "none" # 基本下道扱い
                }
                high_power_chargers.append(charger)
                log.info(f"発見！: {charger['name']} at {address}")

    except Exception as e:
        log.error(f"APIリクエストエラー ({query}): {e}")

    time.sleep(1) # API制限を回避するためのウェイト
    return high_power_chargers

def main():
    all_high_power_chargers = []
    
    for query in SEARCH_QUERIES:
        chargers = fetch_high_power_chargers(query)
        all_high_power_chargers.extend(chargers)
        
    # 重複の排除（緯度経度がほぼ同じものを消す処理などがあればベターです）
    # ここでは簡易的に名前と住所で重複排除
    unique_chargers = { (c['lat'], c['lng']): c for c in all_high_power_chargers }.values()

    output_list = list(unique_chargers)
    log.info(f"合計 {len(output_list)} 件の超高出力充電器を取得しました。")

    # 別ファイルとして保存（HTML側で両方読み込ませる）
    output_filename = "chargers_v2_data.js"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write("const CHARGER_V2_DATA = ")
        json.dump(output_list, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    
    log.info(f"{output_filename} を生成しました！")

if __name__ == "__main__":
    main()
