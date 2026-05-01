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
    # 必要に応じて追加してください（例: "ポルシェセンター 充電", "Audi 充電" など）
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
