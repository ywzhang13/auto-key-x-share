"""Windows 平台層：所有 ctypes/windll、虛擬鍵碼、前景視窗、GetAsyncKeyState 都只在這裡。"""
import threading

from constants import HOTKEY_NAMES

# 快捷鍵名稱 → Windows 虛擬鍵碼；鍵盤上排 1/2/3，不包含數字鍵盤。
VIRTUAL_KEYS = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "2": 0x32, "3": 0x33,
}
# (按鍵名稱, Windows 虛擬鍵碼)；順序沿用 HOTKEY_NAMES（停止/暫停優先）。
HOTKEYS = tuple((key, VIRTUAL_KEYS[key]) for key in HOTKEY_NAMES)


def enable_dpi_awareness():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def foreground_window_handle():
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.user32.GetForegroundWindow
    fn.restype = wintypes.HWND
    return fn()


def get_window_title(handle):
    """取得 Windows 視窗標題。"""
    import ctypes
    from ctypes import wintypes
    api = ctypes.windll.user32
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextLengthW.restype = ctypes.c_int
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowTextW.restype = ctypes.c_int
    length = api.GetWindowTextLengthW(handle)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    api.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value.strip()


def find_window_by_title(saved_title):
    """先尋找標題完全相同的視窗，再放寬為包含關係。"""
    if not saved_title:
        return None
    import ctypes
    from ctypes import wintypes
    api = ctypes.windll.user32
    exact_matches = []
    partial_matches = []
    normalized_saved = saved_title.casefold()

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.EnumWindows.restype = wintypes.BOOL
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsWindowVisible.restype = wintypes.BOOL
    api.IsIconic.argtypes = [wintypes.HWND]
    api.IsIconic.restype = wintypes.BOOL

    @callback_type
    def enum_window(handle, _):
        if not api.IsWindowVisible(handle) or api.IsIconic(handle):
            return True
        title = get_window_title(handle)
        if not title:
            return True
        normalized_title = title.casefold()
        if normalized_title == normalized_saved:
            exact_matches.append(handle)
        elif (
            len(normalized_saved) >= 3
            and (
                normalized_saved in normalized_title
                or normalized_title in normalized_saved
            )
        ):
            partial_matches.append(handle)
        return True

    api.EnumWindows(enum_window, 0)
    if exact_matches:
        return exact_matches[0]
    if partial_matches:
        return partial_matches[0]
    return None


def window_region(handle, screen_size):
    import ctypes
    from ctypes import wintypes
    api = ctypes.windll.user32
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsIconic.argtypes = [wintypes.HWND]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetWindowRect.restype = wintypes.BOOL
    if not api.IsWindow(handle) or api.IsIconic(handle):
        raise ValueError("遊戲視窗已關閉或最小化，請還原視窗")
    rect = wintypes.RECT()
    if not api.GetWindowRect(handle, ctypes.byref(rect)):
        raise ValueError("無法取得遊戲視窗位置")
    sw, sh = screen_size
    x1, y1 = max(0, rect.left), max(0, rect.top)
    x2, y2 = min(sw, rect.right), min(sh, rect.bottom)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("請將遊戲視窗移到主螢幕")
    return (x1, y1, x2 - x1, y2 - y1)


class WindowsHotkeys:
    """獨立執行緒輪詢實體 F 鍵；只送事件，不在背景執行遊戲動作。"""
    def __init__(self, events, get_state=None, position=None):
        if get_state is None:
            import ctypes
            get_state = ctypes.windll.user32.GetAsyncKeyState
            get_state.argtypes = [ctypes.c_int]
            get_state.restype = ctypes.c_short
        self.get_state = get_state
        self.position = position
        self.events = events
        self.previous = {}
        self.stop_event = threading.Event()
        self.thread = None

    def scan(self):
        # 停止/暫停優先；按住不重複觸發。數字鍵使用鍵盤上排 1/2/3。
        for key, virtual_key in HOTKEYS:
            down = bool(self.get_state(virtual_key) & 0x8000)
            if down and not self.previous.get(key, False):
                mouse = (foreground_window_handle() if key == "f5" else
                         tuple(self.position()) if key == "f10" and self.position else None)
                self.events.put((key, mouse))
            self.previous[key] = down

    def loop(self):
        try:
            while not self.stop_event.is_set():
                self.scan()
                self.stop_event.wait(0.01)
        except Exception as exc:
            print(f"快捷鍵偵測失敗：{exc}；已要求停止程式", flush=True)
            self.events.put(("f3", None))

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
