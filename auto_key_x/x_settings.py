"""auto_key_x 的時間參數、快捷鍵說明與視窗記憶設定檔。

檔名一律加 x_ 前綴：auto_key 的模組（platforms、constants…）會以同名直接匯入，
本資料夾若出現 constants.py 這類同名檔，會蓋掉 auto_key 的模組。
"""
import json
import os
import sys

# 模式1：先按住右方向鍵，按住期間連點 X，時間到換邊，之後左右輪流。
FIRST_DIRECTION = "right"
HOLD_MIN_SECONDS, HOLD_MAX_SECONDS = 13.0, 15.0
X_INTERVAL_MIN, X_INTERVAL_MAX = 0.05, 0.15  # 從這次按下 X 到下次按下 X
X_PRESS_SECONDS = 0.03

# 補技能：只在換邊空檔執行；先放開方向鍵與 X，等 0.5 秒再按。
PGUP_INTERVAL_SECONDS = 360.5  # 以 PgUp 實際按下的瞬間起算
PGUP_PRESS_SECONDS = 0.20
INS_HOME_INTERVAL = 600
BUFF_RELEASE_SECONDS = 0.5
AUTO_STOP_SECONDS = 72000

GAME_KEYS = frozenset(("left", "right", "x", "pgup", "insert", "home"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 和 auto_key 分開存，不會互相覆蓋；Mac 另存 _mac（兩台視窗不同）。
WINDOW_SETTINGS_FILE = os.path.join(
    BASE_DIR, f"x_window{'_mac' if sys.platform == 'darwin' else ''}.json")

# auto_key_x 用得到的快捷鍵（名稱沿用 auto_key 的平台層）；模式選擇與小地圖不使用。
HOTKEY_PURPOSES = (
    ("f1", "開始／繼續"), ("f2", "暫停"), ("f3", "停止並結束程式"),
    ("f4", "手動換邊"), ("f5", "鎖定目前遊戲視窗並開啟監看"), ("f6", "改回整個主螢幕監看"),
    ("f7", "監看開關"), ("f8", "顯示警告辨識分數"), ("f9", "TG 測試訊息"),
)


def hotkey_guide(label):
    """label(name) 回傳該平台實際要按的鍵。"""
    return "\n".join(["auto_key_x 快捷鍵（程式訊息裡的 F1～F9 請對照）："]
                     + [f"  {name.upper():>3} → {label(name):<4} {purpose}"
                        for name, purpose in HOTKEY_PURPOSES])


def load_saved_window_title():
    try:
        with open(WINDOW_SETTINGS_FILE, encoding="utf-8") as file:
            title = json.load(file).get("alert_window_title", "")
        return title.strip() if isinstance(title, str) else ""
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


def save_window_title(title):
    """保存 F5 視窗標題；title 為空時清除設定。"""
    if not title:
        try:
            os.remove(WINDOW_SETTINGS_FILE)
        except FileNotFoundError:
            pass
        return
    temporary_file = WINDOW_SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump({"alert_window_title": title}, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, WINDOW_SETTINGS_FILE)
