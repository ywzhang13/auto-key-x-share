"""共用常數：快捷鍵名稱、各模式時間參數、設定檔位置。不含任何平台專用代碼。"""
import os
import sys

# 快捷鍵掃描順序（停止/暫停優先）；鍵盤上排 2/3（選模式），不包含數字鍵盤。
# 各平台的實體鍵碼對照放在 platforms/ 底下。
HOTKEY_NAMES = (
    "f3", "f2", "f1",
    "2", "3",
    "f4", "f5", "f6",
    "f7", "f8", "f9",
    "f10", "f11", "f12",
)

AUTO_STOP_SECONDS = 72000
PGUP_INTERVAL_SECONDS = 240.5
PGUP_PRESS_SECONDS = 0.20
PGUP_ADX_RELEASE_SECONDS = 0.5
PGUP_BLOCKING_KEYS = frozenset(("a", "d", "x"))
INS_HOME_INTERVAL = 600
M6_RIGHT_SECONDS = 10.0
M6_LEFT_SECONDS = 9.5
# 模式 2 的攻擊節奏（M5_ 開頭是沿用原模式 1 的名稱）。
M5_ACTION_INTERVAL = 0.1
M5_DIR_PRE_DELAY_MIN, M5_DIR_PRE_DELAY_MAX = 0.175, 0.185
M5_KEY_PRESS_TIME_MIN, M5_KEY_PRESS_TIME_MAX = 0.175, 0.185
M6_DOWN_C_HOLD_SECONDS = 0.25  # 模式 2 換向前下+C 的秒數
M6_DOWN_C_COUNT_MIN, M6_DOWN_C_COUNT_MAX = 2, 3
M6_DOWN_C_GAP_SECONDS = 0.1  # 次與次之間放開按鍵的時間

# 設定檔一律放在本資料夾（與拆檔前的 auto_key.py 同一位置、同檔名）。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# F5 鎖定的視窗會以標題記憶在程式旁，下次啟動時自動找回。
# Mac 另存 *_mac.json（不進 git），兩台電腦的視窗大小不同，校準不能共用，也不會互相覆蓋。
SETTINGS_SUFFIX = "_mac" if sys.platform == "darwin" else ""
WINDOW_SETTINGS_FILE = os.path.join(BASE_DIR, f"remote_agent_window{SETTINGS_SUFFIX}.json")
MINIMAP_SETTINGS_FILE = os.path.join(BASE_DIR, f"mode3_minimap{SETTINGS_SUFFIX}.json")
MINIMAP_DEBUG_IMAGE = os.path.join(BASE_DIR, "mode3_minimap_debug.png")

M3_POSITION_TIMEOUT = 8.0
M3_POSITION_SCAN_SECONDS = 0.50
M3_POSITION_SETTLE_SECONDS = 0.50
M3_CENTER_SAFE_OFFSET = 20.0
M3_CALIBRATION_PROMPTS = (
    "小地圖地形區左上角（不要包含標題列）",
    "小地圖地形區右下角",
    "中央藍點中心",
)
