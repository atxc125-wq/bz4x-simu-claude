"""
BZ4X Range Predictor - Toyota Official CSV Fetcher
"""

import requests
import csv
import json
import os
import time
import logging
import math
from io import StringIO
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("toyota_csv")

def get_api_key():
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        log.error("GOOGLE_API_KEYが設定されていません。")
        exit(1)
    return key

API_KEY = get_api_key()
GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"

PREFECTURES = [
    "hokkaido", "aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima",
    "ibaraki", "tochigi", "gunma", "saitama", "chiba", "tokyo", "kanagawa",
    "niigata", "toyama", "ishikawa", "fukui", "yamanashi", "nagano", "gifu",
    "shizuoka", "aichi", "mie", "shiga", "kyoto", "osaka", "hyogo",
    "nara", "wakayama", "tottori", "shimane", "okayama", "hiroshima", "yamaguchi",
    "tokushima", "kagawa", "ehime", "kochi", "fukuoka", "saga", "nagasaki",
    "kumamoto", "oita", "miyazaki", "kagoshima", "okinawa"
]

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def geocode_address(address, shop_name):
    try:
        query = f"{address} {shop_name}"
        res = requests.get(GEOCODING_URL, params={"address": query, "key": API_KEY, "language": "ja"})
        res.raise_for_status()
        data = res.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            return location["lat"], location["lng"]
        else:
            log.warning(f"Geocoding失敗 ({query}): {data['status']}")
            return None, None
    except Exception as e:
        log.error(f"Geocoding通信エラー: {e}")
        return None, None

def fetch_toyota_csvs():
    log.info("トヨタ公式(TEEMO)から47都道府県のCSVを一斉取得します...")
    chargers = []
    
    cache_buster = datetime.now().strftime("%Y%m")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://toyota.jp/info/e-toyota/teemo/shop_list/"
    }

    for pref in PREFECTURES:
        url = f"https://toyota.jp/info/e-toyota/teemo/members/csv/{pref}.csv?{cache_buster}"
        try:
            res = requests.get(url, headers=headers)
            if res.status_code != 200:
                continue
            
            # 文字化け絶対許さないマン
            try:
                csv_text = res.content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    csv_text = res.content.decode('cp932')
                except UnicodeDecodeError:
                    csv_text = res.content.decode('shift_jis', errors='ignore')
            
            reader = csv.reader(StringIO(csv_text))
            headers_row = next(reader, None)
            if not headers_row:
                continue
            
            # 🌟 「出力」の列、および「店舗名」「住所」の列が何番目にあるかを自動で探す
            try:
                power_idx = next(i for i, h in enumerate(headers_row) if '出力' in h)
                name_idx = next(i for i, h in enumerate(headers_row) if '店舗' in h)
                # 住所列が複数ある場合（秋田のCSV等）は最初のものを取る
                address_idx = next(i for i, h in enumerate(headers_row) if '住所' in h)
            except StopIteration:
                log.warning(f"{pref}.csv に必要な列が見つかりません。スキップします。")
                continue

            for row in reader:
                if len(row) <= max(power_idx, name_idx, address_idx):
                    continue
                
                power_str = row[power_idx].strip()
                # 数字だけで構成されているかチェック
                if power_str.isdigit():
                    kw = int(power_str)
                    
                    if kw >= 90:
                        shop_name = row[name_idx].strip()
                        address = row[address_idx].strip()
                        
                        lat, lng = geocode_address(address, shop_name)
                        time.sleep(0.5) 
                        
                        if lat and lng:
                            charger = {
                                "name": f"{shop_name} ({kw}kW)",
                                "address": address,
                                "type": "normal",
                                "powers": [kw],
                                "lat": lat,
                                "lng": lng,
                                "powerKw": kw,
                                "direction": "none"
                            }
                            chargers.append(charger)
                            log.info(f"抽出成功: {shop_name} ({kw}kW)")

        except Exception as e:
            log.error(f"{pref}.csv の処理中にエラー: {e}")
            
    return chargers

def main():
    toyota_chargers = fetch_toyota_csvs()
    log.info(f"全国から合計 {len(toyota_chargers)} 件の高出力トヨタ充電器を取得しました。")

    v1_file = "chargers_data.js"
    existing_data = []
    if os.path.exists(v1_file):
        with open(v1_file, "r", encoding="utf-8") as f:
            content = f.read().replace("const CHARGER_DATA = ", "").strip()
            if content.endswith(";"): content = content[:-1]
            existing_data = json.loads(content)

    final_chargers = list(existing_data)
    for tc in toyota_chargers:
        is_duplicate = False
        for ec in existing_data:
            if haversine_km(tc["lat"], tc["lng"], ec["lat"], ec["lng"]) < 0.15:
                is_duplicate = True
                break
        
        if not is_duplicate:
            final_chargers.append(tc)

    with open(v1_file, "w", encoding="utf-8") as f:
        f.write("const CHARGER_DATA = ")
        json.dump(final_chargers, f, ensure_ascii=False, indent=2)
        f.write(";\n")
        
    log.info(f"トヨタ公式データを統合した chargers_data.js を生成しました！(総計: {len(final_chargers)}件)")

if __name__ == "__main__":
    main()
