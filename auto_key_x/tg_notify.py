"""Telegram 通知：設定與發送。

Token 與 Chat ID 不寫在程式裡，依序從這兩處讀取（前者優先）：
  1. 環境變數 TG_BOT_TOKEN、TG_CHAT_ID
  2. 本資料夾的 tg_config.json（不上傳 git；格式見 tg_config.example.json）
都沒有設定時程式照常運作，只是不發 TG。
"""
import os
import json

TG_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_config.json")
TG_NOT_CONFIGURED_HINT = (
    "[提示] 尚未設定 Telegram，偵測到警告時仍會暫停，但不會發 TG。"
    "設定方式：把 auto_key 資料夾裡的 tg_config.example.json 複製成 tg_config.json，並填入 bot_token、chat_id。"
)


def load_tg_settings(config_file=TG_CONFIG_FILE, environ=None):
    """回傳 (token, chat_id, 問題說明)；設定完整時問題說明為空字串。"""
    environ = os.environ if environ is None else environ
    token = environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = environ.get("TG_CHAT_ID", "").strip()
    problem = ""
    if not (token and chat_id):
        try:
            with open(config_file, encoding="utf-8") as file:
                data = json.load(file)
            token = token or str(data.get("bot_token") or "").strip()
            chat_id = chat_id or str(data.get("chat_id") or "").strip()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            problem = "tg_config.json 格式錯誤，無法讀取"
    if not (token and chat_id) and not problem:
        problem = "未設定 bot_token 或 chat_id"
    return token, chat_id, problem


# 警示只辨識固定標題並通知使用者，不操作驗證視窗。
TG_BOT_TOKEN, TG_CHAT_ID, TG_SETTINGS_PROBLEM = load_tg_settings()
TG_CONFIGURED = bool(TG_BOT_TOKEN and TG_CHAT_ID)
TG_ALERT_REPEAT_COUNT = 3  # 正式警報連續發送次數
TG_ALERT_REPEAT_INTERVAL = 0.33  # 每次警報的間隔秒數


def send_telegram(text):
    """只傳文字；不輸出含有 Token 的 URL 或例外內容。"""
    import json
    import urllib.request
    import urllib.error
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False, "請在本機設定 TG_BOT_TOKEN 和 TG_CHAT_ID"
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + TG_BOT_TOKEN + "/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
        if data.get("ok"):
            return True, "Telegram 已接受通知，請確認手機通知設定"
        return False, "Telegram 拒絕通知，請檢查 Token、Chat ID 與 bot 權限"
    except urllib.error.HTTPError as exc:
        return False, f"Telegram HTTP {exc.code}，請檢查 Token、Chat ID，並先向 bot 按 Start"
    except Exception:
        return False, "Telegram 連線失敗或逾時；無法確認是否送達"
