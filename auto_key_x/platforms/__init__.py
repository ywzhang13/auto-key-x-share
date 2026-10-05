"""平台層入口：依作業系統選擇實作，其他模組一律只從這裡取用系統功能。

對外提供：
  SUPPORTED, PLATFORM_NAME, UNSUPPORTED_MESSAGE
  enable_dpi_awareness()
  foreground_window_handle()
  get_window_title(handle)
  find_window_by_title(saved_title)
  window_region(handle, screen_size)
  NativeHotkeys(events, position=None)  # 有 start()/stop()
  create_gui()                          # 只有 Mac：檢查權限並建立送鍵／截圖物件（Windows 用 pyautogui）
"""
import os
import sys

if os.name == "nt":
    from platforms.windows import (
        enable_dpi_awareness,
        foreground_window_handle,
        get_window_title,
        find_window_by_title,
        window_region,
        WindowsHotkeys as NativeHotkeys,
    )
    SUPPORTED = True
    PLATFORM_NAME = "Windows"
    UNSUPPORTED_MESSAGE = ""
else:
    from platforms.mac import (
        enable_dpi_awareness,
        foreground_window_handle,
        get_window_title,
        find_window_by_title,
        window_region,
        MacHotkeys as NativeHotkeys,
        UNSUPPORTED_MESSAGE,
        HOTKEY_GUIDE,
        create_gui,
    )
    SUPPORTED = sys.platform == "darwin"
    PLATFORM_NAME = "macOS" if sys.platform == "darwin" else sys.platform
