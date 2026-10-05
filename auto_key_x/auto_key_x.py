"""auto_key_x：只有模式1 的自動按鍵（參考 auto_key，畫面偵測、TG、快捷鍵保護都相同）。

模式1：先按住右方向鍵 13～15 秒（每次隨機），期間每 0.05～0.15 秒（每次隨機）點一下 X；
       時間到放開，換邊按左，之後左右輪流。
補技能只在換邊空檔：先放開方向鍵與 X、等 0.5 秒；
       PgUp 以實際按下的瞬間起算每 360.5 秒一次；Insert＋Home 每 600 秒一次。
快捷鍵：Windows F1～F9；Mac 用 Control+1～9，啟動時會印出對照表。

啟動：Windows（在 C:\\jay\\python）  python auto_key_x\\auto_key_x.py
      Mac（在 jay-python）          .venv/bin/python auto_key_x/auto_key_x.py

程式結構：
  auto_key_x.py    啟動入口
  x_settings.py    時間參數、快捷鍵說明、視窗記憶（x_window.json／Mac 為 x_window_mac.json）
  x_controller.py  主控制器：快捷鍵事件、視窗鎖定/監看、模式1、補技能
  沿用 auto_key（只讀不改）：platforms/、alert_detect.py、tg_notify.py（TG 設定讀 auto_key/tg_config.json）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(os.path.dirname(HERE), "auto_key"))

import platforms  # noqa: E402
import x_settings  # noqa: E402


def main():
    if not platforms.SUPPORTED:
        print(platforms.UNSUPPORTED_MESSAGE)
        raise SystemExit(1)
    if platforms.PLATFORM_NAME == "macOS":
        main_mac()
        return
    platforms.enable_dpi_awareness()
    try:
        import pyautogui
    except ImportError:
        print("請先執行：py -m pip install pyautogui keyboard pillow")
        raise SystemExit(1)
    from x_controller import Controller
    pyautogui.FAILSAFE = False  # 允許滑鼠停在螢幕角落；仍可 F2 暫停、F3 停止
    pyautogui.PAUSE = 0.01
    print(x_settings.hotkey_guide(str.upper), flush=True)
    Controller(pyautogui).run()


def main_mac():
    from platforms.mac import hotkey_label
    gui = platforms.create_gui()  # 缺套件或權限時會說明並結束
    print(x_settings.hotkey_guide(hotkey_label), flush=True)
    from x_controller import Controller
    try:
        Controller(gui).run()
    finally:
        gui.close()  # 最後一道保險：不論怎麼結束，都放開所有還按住的鍵


if __name__ == "__main__":
    main()
