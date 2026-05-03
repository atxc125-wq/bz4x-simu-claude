"""
BZ4X Range Predictor - Toyota Official CSV Fetcher
トヨタ公式（TEEMO）の裏側で使われているCSVファイルを直接ダウンロードし、
100kW / 150kW の超高出力機のみを抽出してシミュレーターに統合します。
"""

import requests
import csv
import json
import os
import time
import logging
import re
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

# 47都道府県のローマ字リスト（CSVのファイル名ルール）
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
    """緯度経度から距離(km)を計算"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def geocode_address(address, shop_name):
    """住所（または店舗名）から緯度経度を取得する"""
    try:
        # 住所だけでピンズレする場合は、店舗名も繋げて検索精度を上げる
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
    
    # URLのキャッシュ回避用パラメータ（YYYYMM形式など適当な値でOK）
    cache_buster = datetime.now().strftime("%Y%m")
    
 # 🌟 ここに偽装ヘッダーを追記！
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://toyota.jp/info/e-toyota/teemo/shop_list/"
    }

    for pref in PREFECTURES:
        url = f"https://toyota.jp/info/e-toyota/teemo/members/csv/{pref}.csv?{cache_buster}"
        try:
            # 🌟 ここに「headers=headers」を足す！
            res = requests.get(url, headers=headers)
            if res.status_code != 200:
                continue
    for pref in PREFECTURES:
        url = f"https://toyota.jp/info/e-toyota/teemo/members/csv/{pref}.csv?{cache_buster}"
        try:
            res = requests.get(url)
            if res.status_code != 200:
                continue
            
            # トヨタのCSVはほぼShift-JISなのでデコード
            res.encoding = 'shift_jis'
            csv_text = res.text
            
            # CSV解析
            reader = csv.reader(StringIO(csv_text))
            for row in reader:
                if not row:
                    continue
                
                # 行全体を結合して「〇〇kW」の文字が含まれているかチェック
                row_str = " ".join(row)
                kw_match = re.search(r'(\d+)\s*kW', row_str, re.IGNORECASE)
                
                if kw_match:
                    kw = int(kw_match.group(1))
                    # 90kW以上（100kW, 150kW等）だけを抽出
                    if kw >= 90:
                        # CSVの一般的な構造から抽出 (例: row[2]=ディーラー名, row[3]=店舗名, row[5]=住所)
                        # 列の順番が変わっていても対応できるように安全策を取る
                        dealer_name = row[2] if len(row) > 2 else ""
                        shop_name = row[3] if len(row) > 3 else "トヨタ販売店"
                        address = row[5] if len(row) > 5 else row[0]
                        
                        full_name = f"{dealer_name} {shop_name}".strip()
                        
                        # 緯度経度へ変換 (API制限に引っかからないよう少し待つ)
                        lat, lng = geocode_address(address, full_name)
                        time.sleep(0.5) 
                        
                        if lat and lng:
                            charger = {
                                "name": f"{full_name} ({kw}kW)",
                                "address": address,
                                "type": "normal", # トヨタの急速充電は熱ダレあり(normal)
                                "powers": [int(kw)],
                                "lat": lat,
                                "lng": lng,
                                "powerKw": int(kw),
                                "direction": "none"
                            }
                            chargers.append(charger)
                            log.info(f"抽出成功: {full_name} ({kw}kW)")

        except Exception as e:
            log.error(f"{pref}.csv の処理中にエラー: {e}")
            
    return chargers

def main():
    toyota_chargers = fetch_toyota_csvs()
    log.info(f"全国から合計 {len(toyota_chargers)} 件の高出力トヨタ充電器を取得しました。")

    # 既存の chargers_data.js を読み込む
    v1_file = "chargers_data.js"
    existing_data = []
    if os.path.exists(v1_file):
        with open(v1_file, "r", encoding="utf-8") as f:
            content = f.read().replace("const CHARGER_DATA = ", "").strip()
            if content.endswith(";"): content = content[:-1]
            existing_data = json.loads(content)

    # 💡 重複排除ロジック（V1/V2ですでに登録されている場合はスキップ）
    final_chargers = list(existing_data)
    for tc in toyota_chargers:
        is_duplicate = False
        for ec in existing_data:
            if haversine_km(tc["lat"], tc["lng"], ec["lat"], ec["lng"]) < 0.15: # 150m以内
                is_duplicate = True
                break
        
        if not is_duplicate:
            final_chargers.append(tc)

    # 再度上書き保存
    with open(v1_file, "w", encoding="utf-8") as f:
        f.write("const CHARGER_DATA = ")
        json.dump(final_chargers, f, ensure_ascii=False, indent=2)
        f.write(";\n")
        
    log.info(f"トヨタ公式データを統合した chargers_data.js を生成しました！(総計: {len(final_chargers)}件)")

if __name__ == "__main__":
    main()
