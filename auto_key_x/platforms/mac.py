"""macOS 平台層：Quartz 事件攔截、CGEvent 送鍵、NSWorkspace 前景判斷、Quartz 截圖都只在這裡。

介面與 platforms/windows.py 相同，另外提供 create_gui()（Mac 不用 pyautogui）。
Mac 的「視窗 handle」是 App 的 pid；座標一律用 Mac 的「點」（左上為原點），Retina 截圖會縮回點的大小。
所有 pyobjc 匯入都放在函式裡，讓 Windows 也能載入本檔跑測試。
"""
import signal
import threading
import time

from constants import HOTKEY_NAMES

UNSUPPORTED_MESSAGE = "auto_key 目前只支援 Windows 與 macOS。"

# 快捷鍵名稱 → (Mac 實體鍵碼 kVK_*, 要按住的修飾鍵, 顯示名稱)；按著 Command 一律不算。
# F1～F12 → Control+1～9、0、-、=（Windows 鍵盤就是 Ctrl+數字，任何 App 都算，單按數字可正常打字）；
# 模式2、3 → Option+2、3（Windows 鍵盤就是 Alt+2、3，任何 App 都算）；模式1 已移除。
# 只比對 Control／Option／Command，所以 Control+2（F2）和 Option+2（模式2）不會互相誤判。
FLAG_CONTROL, FLAG_OPTION, FLAG_COMMAND = 1 << 18, 1 << 19, 1 << 20
HOTKEY_MODIFIER_MASK = FLAG_CONTROL | FLAG_OPTION | FLAG_COMMAND
MODIFIER_LABELS = {0: "", FLAG_CONTROL: "Control+", FLAG_OPTION: "Option+"}
GAME_APP_KEYWORD = "MapleStory"  # 直接按數字的快捷鍵，只在名稱含這段文字的 App 在最前面時生效
HOTKEY_KEYS = {
    "f1": (18, FLAG_CONTROL, "1"), "f2": (19, FLAG_CONTROL, "2"), "f3": (20, FLAG_CONTROL, "3"),
    "2": (19, FLAG_OPTION, "2"), "3": (20, FLAG_OPTION, "3"),
    "f4": (21, FLAG_CONTROL, "4"), "f5": (23, FLAG_CONTROL, "5"), "f6": (22, FLAG_CONTROL, "6"),
    "f7": (26, FLAG_CONTROL, "7"), "f8": (28, FLAG_CONTROL, "8"), "f9": (25, FLAG_CONTROL, "9"),
    "f10": (29, FLAG_CONTROL, "0"), "f11": (27, FLAG_CONTROL, "-"), "f12": (24, FLAG_CONTROL, "="),
}
HOTKEY_PURPOSES = {
    "f1": "開始／繼續", "f2": "暫停", "f3": "停止並結束程式",
    "2": "模式2", "3": "模式3",
    "f4": "手動換向", "f5": "鎖定目前遊戲視窗並開啟監看", "f6": "改回整個主螢幕監看",
    "f7": "監看開關", "f8": "顯示警告辨識分數", "f9": "TG 測試訊息",
    "f10": "小地圖校準", "f11": "測試黃點定位", "f12": "清除校準",
}


def hotkey_label(name):
    _, modifiers, key = HOTKEY_KEYS[name]
    return f"{MODIFIER_LABELS[modifiers]}{key}"


# (快捷鍵名稱, Mac 鍵碼, 修飾鍵)；順序沿用 HOTKEY_NAMES（停止/暫停優先）。模式4 不提供 Mac 快捷鍵。
HOTKEYS = tuple((name,) + HOTKEY_KEYS[name][:2] for name in HOTKEY_NAMES if name in HOTKEY_KEYS)
HOTKEY_GUIDE = "\n".join(
    ["Mac 快捷鍵（Windows 鍵盤：Control＝Ctrl、Option＝Alt，不要按 Win 鍵）。"
     "程式訊息裡的 F1～F12、1～3 請對照："]
    + [f"  {name.upper():>3} → {hotkey_label(name):<10} {HOTKEY_PURPOSES[name]}" for name in HOTKEY_KEYS]
)

# 遊戲按鍵名稱（與 pyautogui 相同）→ Mac 實體鍵碼。Insert 在 Mac 鍵盤上是 Help 鍵（0x72）。
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27,
    "8": 28, "0": 29,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
    "insert": 0x72, "home": 0x73, "pgup": 0x74, "pageup": 0x74,
    "delete": 0x75, "del": 0x75, "end": 0x77, "pgdn": 0x79, "pagedown": 0x79,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "shift": 56, "ctrl": 59, "alt": 58, "option": 58,
}
# 修飾鍵送的是「修飾鍵改變」事件，並帶上對應旗標，遊戲才會認為它被按住。
MODIFIER_FLAGS = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "option": 1 << 19}
# 低血量喝水在背景短按的鍵：不搶走方向鍵的自動重複（實體鍵盤會被搶走，角色可能停下）。
NO_REPEAT_KEYS = frozenset(("end",))
KEY_REPEAT_DELAY = 0.5     # 和實體鍵盤一樣：按住 0.5 秒後開始自動重複
KEY_REPEAT_INTERVAL = 0.033

PERMISSION_APP = "你用來執行這支程式的終端機（Terminal 或 iTerm）"
REQUIRED_PACKAGES_HINT = (
    "請先在 jay-python 資料夾執行：\n"
    ".venv/bin/python -m pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa "
    "pyobjc-framework-ApplicationServices pillow opencv-python numpy"
)


def _autorelease_pool():
    try:
        import objc
        return objc.autorelease_pool()
    except ImportError:  # 測試環境（假 Quartz）沒有 pyobjc
        import contextlib
        return contextlib.nullcontext()


def enable_dpi_awareness():
    pass  # macOS 不需要；Retina 由 MacGui.screenshot 縮回「點」的大小


# ---------------- 前景 App 與視窗 ----------------
def foreground_window_handle():
    """回傳最前景 App 的 pid；沒有時回傳 None。只在主執行緒呼叫（主程式的 F5 就是在主執行緒處理）。"""
    from AppKit import NSDate, NSDefaultRunLoopMode, NSRunLoop, NSWorkspace
    # 沒處理 run loop 的話，frontmostApplication 會一直停在程式啟動時的 App（POC 實測過）。
    # distantPast＝只處理已到的系統通知、不等待，幾乎不花時間。
    run_loop = NSRunLoop.currentRunLoop()
    for _ in range(5):
        if not run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.distantPast()):
            break
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app is not None else None


def get_window_title(handle):
    """Mac 以 App 名稱當作視窗標題（例如「MapleStory Worlds」）。"""
    from AppKit import NSRunningApplication
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(handle) if handle else None
    return (app.localizedName() or "").strip() if app is not None else ""


def app_window_bounds(pid):
    """回傳該 App 畫面上最大的一般視窗 (x, y, 寬, 高)，單位是點；含約 30 點的標題列。"""
    import Quartz
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    best = None
    for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        if info.get("kCGWindowOwnerPID") != pid or info.get("kCGWindowLayer", 0) != 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        rect = (int(bounds.get("X", 0)), int(bounds.get("Y", 0)),
                int(bounds.get("Width", 0)), int(bounds.get("Height", 0)))
        if rect[2] > 0 and rect[3] > 0 and (best is None or rect[2] * rect[3] > best[2] * best[3]):
            best = rect
    return best


def find_window_by_title(saved_title):
    """先找 App 名稱完全相同、再放寬為包含關係（與 Windows 版相同規則）；只找畫面上有視窗的 App。"""
    if not saved_title:
        return None
    from AppKit import NSWorkspace
    normalized_saved = saved_title.casefold()
    exact_matches = []
    partial_matches = []
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        name = (app.localizedName() or "").strip()
        pid = int(app.processIdentifier())
        if not name or app_window_bounds(pid) is None:
            continue
        normalized_name = name.casefold()
        if normalized_name == normalized_saved:
            exact_matches.append(pid)
        elif len(normalized_saved) >= 3 and (
                normalized_saved in normalized_name or normalized_name in normalized_saved):
            partial_matches.append(pid)
    if exact_matches:
        return exact_matches[0]
    if partial_matches:
        return partial_matches[0]
    return None


def window_region(handle, screen_size):
    rect = app_window_bounds(handle) if handle else None
    if rect is None:
        raise ValueError("遊戲視窗已關閉或最小化，請還原視窗")
    sw, sh = screen_size
    x1, y1 = max(0, rect[0]), max(0, rect[1])
    x2, y2 = min(sw, rect[0] + rect[2]), min(sh, rect[1] + rect[3])
    if x2 <= x1 or y2 <= y1:
        raise ValueError("請將遊戲視窗移到主螢幕")
    return (x1, y1, x2 - x1, y2 - y1)


# ---------------- 權限與啟動 ----------------
def input_monitoring_granted():
    """IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)：0=允許。無法判斷時回傳 None。"""
    import ctypes
    import ctypes.util
    try:
        iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
        return iokit.IOHIDCheckAccess(1) == 0
    except (OSError, AttributeError, TypeError):
        return None


def missing_permissions():
    """回傳缺少的權限 [(名稱, 系統設定位置)]；同時請系統把終端機加進清單，方便使用者勾選。"""
    import Quartz
    from ApplicationServices import AXIsProcessTrusted
    checks = (
        ("輔助使用", bool(AXIsProcessTrusted()),
         "系統設定 →「隱私權與安全性」→「輔助使用」"),
        ("輸入監控", input_monitoring_granted(),
         "系統設定 →「隱私權與安全性」→「輸入監控」"),
        ("螢幕錄製", bool(Quartz.CGPreflightScreenCaptureAccess()),
         "系統設定 →「隱私權與安全性」→「螢幕與系統錄音」（舊版叫「螢幕錄製」）"),
    )
    missing = [(name, where) for name, granted, where in checks if granted is False]
    if any(name == "螢幕錄製" for name, _ in missing):
        Quartz.CGRequestScreenCaptureAccess()
    return missing


def permission_message(missing):
    lines = ["❌ 還缺少 Mac 權限，auto_key 無法攔截快捷鍵、送按鍵或截圖："]
    lines += [f"  ・{name}：請到 {where}，把{PERMISSION_APP}打開" for name, where in missing]
    lines.append("開好後請「完全結束」終端機（Command+Q）再重開，然後重新執行 auto_key。")
    return "\n".join(lines)


def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt()


def create_gui():
    """Mac 啟動檢查＋建立送鍵／截圖物件；缺套件或權限時用中文說明並結束程式。"""
    try:
        import Quartz  # noqa: F401
        import AppKit  # noqa: F401
        import ApplicationServices  # noqa: F401
        import PIL  # noqa: F401
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        print(f"缺少套件（{exc.name}）。{REQUIRED_PACKAGES_HINT}")
        raise SystemExit(1)
    missing = missing_permissions()
    if missing:
        print(permission_message(missing))
        raise SystemExit(1)
    # 關閉終端機視窗（SIGHUP）或被 kill（SIGTERM）時，走和 Control+C 相同的收尾：解除攔截、放開按鍵。
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, _raise_keyboard_interrupt)
    gui = MacGui()
    gui.start_key_repeat()
    return gui


# ---------------- 送鍵、截圖（取代 pyautogui） ----------------
class MacGui:
    """提供主程式用到的 pyautogui 介面：keyDown/keyUp/screenshot/size/position/FAILSAFE。"""
    FAILSAFE = False  # 主程式會切換這個值；Mac 沒有滑鼠角落中止機制
    PAUSE = 0.01      # 與 Windows 的 pyautogui.PAUSE 相同：每次按下／放開後等 0.01 秒

    def __init__(self, quartz=None, clock=time.monotonic, sleep=time.sleep):
        if quartz is None:
            import Quartz as quartz
        self.q = quartz
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.pressed = set()        # 已送出按下、還沒確認放開的鍵
        self.repeat_key = None      # 和實體鍵盤一樣，只有最後按下的一般鍵會自動重複
        self.repeat_since = 0.0
        self.repeat_stop = threading.Event()
        self.repeat_thread = None

    @staticmethod
    def keycode(key):
        key = str(key).lower()
        if key not in KEYCODES:
            raise ValueError(f"Mac 版不支援按鍵「{key}」")
        return key, KEYCODES[key]

    def _flags(self):
        flags = 0
        for key in self.pressed:
            flags |= MODIFIER_FLAGS.get(key, 0)
        return flags

    def _post(self, key, code, down, flags, autorepeat=False):
        q = self.q
        event = q.CGEventCreateKeyboardEvent(None, code, down)
        if key in MODIFIER_FLAGS:
            q.CGEventSetType(event, q.kCGEventFlagsChanged)
        # 旗標只反映本程式按住的修飾鍵，避免使用者正按著 Control+Option 時變成組合鍵。
        q.CGEventSetFlags(event, flags)
        if autorepeat:
            q.CGEventSetIntegerValueField(event, q.kCGKeyboardEventAutorepeat, 1)
        q.CGEventPost(q.kCGHIDEventTap, event)

    def keyDown(self, key):
        key, code = self.keycode(key)
        with self.lock:
            self.pressed.add(key)  # 先記錄再送出：送到一半出錯，收尾時也會補放開
            self._post(key, code, True, self._flags())
            if key not in MODIFIER_FLAGS and key not in NO_REPEAT_KEYS:
                self.repeat_key, self.repeat_since = key, self.clock()
        self.sleep(self.PAUSE)

    def keyUp(self, key):
        key, code = self.keycode(key)
        with self.lock:
            if key == self.repeat_key:
                self.repeat_key = None
            # 修飾鍵只放開本程式按下的，不去動使用者手上正按著的 Control/Option。
            if key in MODIFIER_FLAGS and key not in self.pressed:
                return
            # 放開修飾鍵時，旗標要拿掉它自己；放開成功才從 pressed 移除。
            self._post(key, code, False, self._flags() & ~MODIFIER_FLAGS.get(key, 0))
            self.pressed.discard(key)
        self.sleep(self.PAUSE)

    def repeat_once(self):
        """按住滿 0.5 秒的鍵送一次自動重複；與 keyUp 同一把鎖，放開後絕不會再補送按下。"""
        with self.lock:
            key = self.repeat_key
            if key is None or key not in self.pressed or self.clock() - self.repeat_since < KEY_REPEAT_DELAY:
                return False
            self._post(key, KEYCODES[key], True, self._flags(), autorepeat=True)
            return True

    def _repeat_loop(self):
        while not self.repeat_stop.wait(KEY_REPEAT_INTERVAL):
            try:
                self.repeat_once()
            except Exception:
                pass  # 自動重複失敗不影響主流程；放開由 keyUp／close 負責

    def start_key_repeat(self):
        self.repeat_thread = threading.Thread(target=self._repeat_loop, daemon=True)
        self.repeat_thread.start()

    def close(self):
        """停止自動重複，並把仍按住的鍵逐一放開；某個鍵失敗不影響其他鍵。回傳放不開的鍵。"""
        self.repeat_stop.set()
        if self.repeat_thread is not None:
            self.repeat_thread.join(timeout=1)
        failed = []
        for key in sorted(self.pressed, key=lambda k: k in MODIFIER_FLAGS):  # 一般鍵先放，修飾鍵最後
            try:
                self.keyUp(key)
            except Exception:
                failed.append(key)
        if failed:
            print(f"⚠️ 無法自動放開 {failed}，請在遊戲裡手動按一下這些鍵", flush=True)
        return failed

    def size(self):
        bounds = self.q.CGDisplayBounds(self.q.CGMainDisplayID())
        return (int(bounds.size.width), int(bounds.size.height))

    def position(self):
        location = self.q.CGEventGetLocation(self.q.CGEventCreate(None))
        return (int(round(location.x)), int(round(location.y)))

    def screenshot(self, region=None):
        """region=(x, y, 寬, 高)，單位是點；回傳的圖一律是點的大小（Retina 會縮回 1 倍）。"""
        from PIL import Image
        q = self.q
        x, y, w, h = region if region is not None else (0, 0) + self.size()
        w, h = int(round(w)), int(round(h))
        # 背景執行緒每秒截好幾次：要在 autorelease pool 裡做完，否則 Quartz 物件不會被釋放。
        with _autorelease_pool():
            image = q.CGWindowListCreateImage(q.CGRectMake(x, y, w, h), q.kCGWindowListOptionOnScreenOnly,
                                              q.kCGNullWindowID, q.kCGWindowImageDefault)
            if image is None:
                raise ValueError("截圖失敗，請確認螢幕錄製權限已開啟")
            if q.CGImageGetBitsPerPixel(image) != 32:
                raise ValueError("截圖格式不支援")
            pw, ph = q.CGImageGetWidth(image), q.CGImageGetHeight(image)
            row_bytes = q.CGImageGetBytesPerRow(image)
            cf_data = q.CGDataProviderCopyData(q.CGImageGetDataProvider(image))
            # 不能用 bytes(cf_data)：pyobjc 每次都會漏掉整張圖的記憶體（實測每張約 7MB，久了會到幾十 GB）。
            view = memoryview(cf_data)
            try:
                data = view.tobytes()
            finally:
                view.release()
            del cf_data, image
        frame = Image.frombuffer("RGB", (pw, ph), data, "raw", "BGRX", row_bytes, 1)
        return frame if (pw, ph) == (w, h) else frame.resize((w, h), Image.BILINEAR)


# ---------------- 全域快捷鍵 ----------------
def cleanup_event_tap(quartz, tap, source, loop):
    """停用 → 從 run loop 移除 → 銷毀 event tap；每一步獨立執行，前一步失敗也會做下一步。"""
    steps = []
    if tap is not None:
        steps.append(("停用鍵盤攔截", lambda: quartz.CGEventTapEnable(tap, False)))
    if source is not None and loop is not None:
        steps.append(("移除攔截來源", lambda: quartz.CFRunLoopRemoveSource(
            loop, source, quartz.kCFRunLoopDefaultMode)))
    if tap is not None:
        steps.append(("銷毀鍵盤攔截", lambda: quartz.CFMachPortInvalidate(tap)))
    failed = []
    for name, step in steps:
        try:
            step()
        except Exception as exc:
            failed.append(name)
            print(f"⚠️ {name}失敗：{exc}；若鍵盤異常，關閉終端機即可恢復", flush=True)
    return failed


class MacHotkeys:
    """獨立執行緒的 event tap：攔下數字鍵／Option+數字快捷鍵（不交給系統與遊戲），只送事件到主程式。"""
    def __init__(self, events, position=None, quartz=None, game_is_frontmost=None):
        self.events = events
        self.position = position
        self.q = quartz
        self.game_is_frontmost = game_is_frontmost or self._game_is_frontmost
        self.codes = {code for _, code, _ in HOTKEYS}
        self.names = {(code, modifiers): name for name, code, modifiers in HOTKEYS}
        self.swallowed = set()  # 已攔下按下的鍵，放開時也要一起攔
        self.tap = None
        self.start_error = ""
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None

    def handle(self, event_type, event):
        """回傳 None＝攔下；回傳 event＝原樣放行。"""
        q = self.q
        if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            q.CGEventTapEnable(self.tap, True)
            return event
        code = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
        if code not in self.codes:
            return event
        if event_type == q.kCGEventKeyUp:
            if code in self.swallowed:
                self.swallowed.discard(code)
                return None
            return event
        modifiers = q.CGEventGetFlags(event) & HOTKEY_MODIFIER_MASK
        name = self.names.get((code, modifiers))
        if name is not None and modifiers == 0 and code not in self.swallowed and not self.game_is_frontmost():
            name = None  # 遊戲不在最前面：數字鍵照常交給其他 App（例如在終端機打字）
        if name is None:
            return event if code not in self.swallowed else None  # 放開修飾鍵後的自動重複也攔下
        self.swallowed.add(code)
        if not q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat):
            # F5 的前景 App 交給主執行緒查詢（NSWorkspace 要在主執行緒更新）；F10 帶滑鼠位置。
            mouse = tuple(self.position()) if name == "f10" and self.position else None
            self.events.put((name, mouse))
        return None

    def _game_is_frontmost(self):
        """最上層的一般視窗屬於遊戲時回傳 True；從 event tap 執行緒呼叫，所以用 Quartz 而不用 NSWorkspace。"""
        q = self.q
        options = q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements
        for info in q.CGWindowListCopyWindowInfo(options, q.kCGNullWindowID) or []:
            if info.get("kCGWindowLayer", 0) == 0:
                return GAME_APP_KEYWORD.casefold() in str(info.get("kCGWindowOwnerName") or "").casefold()
        return False

    def callback(self, proxy, event_type, event, refcon):
        try:
            return self.handle(event_type, event)
        except Exception:
            return event  # 回呼出錯一律放行，絕不讓鍵盤被卡住

    def loop(self):
        q = self.q
        tap = source = run_loop = None
        try:
            mask = q.CGEventMaskBit(q.kCGEventKeyDown) | q.CGEventMaskBit(q.kCGEventKeyUp)
            tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
                                     q.kCGEventTapOptionDefault, mask, self.callback, None)
            if tap is None:
                self.start_error = "無法攔截鍵盤，請確認「輔助使用」和「輸入監控」都已開啟"
                return
            self.tap = tap
            source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
            run_loop = q.CFRunLoopGetCurrent()
            q.CFRunLoopAddSource(run_loop, source, q.kCFRunLoopDefaultMode)
            q.CGEventTapEnable(tap, True)
            self.ready.set()
            while not self.stop_event.is_set():
                q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, 0.1, False)
        except Exception as exc:
            if not self.ready.is_set():
                self.start_error = f"鍵盤攔截啟動失敗：{exc}"
            else:
                print(f"快捷鍵偵測失敗：{exc}；已要求停止程式", flush=True)
                self.events.put(("f3", None))
        finally:
            # 不論正常、出錯或停止，都一定解除攔截。
            cleanup_event_tap(q, tap, source, run_loop)
            self.ready.set()

    def start(self):
        if self.q is None:
            import Quartz
            self.q = Quartz
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.start_error:
            print(self.start_error or "鍵盤攔截啟動逾時", flush=True)
            self.stop()
            raise SystemExit(1)

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
