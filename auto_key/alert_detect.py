"""畫面警告辨識：範本比對、各區域裁切、警報閂鎖與背景監看執行緒。"""
import time
import queue
import threading

from alert_templates import (
    CURSE_ALERT_TEXT_PNG, ALERT_TITLE_PNG, ALERT_TITLE_PNG_2,
    ALERT_GAME_TITLE_PNG, ALERT_PREP_TITLE_REAL_PNG,
    ALERT_GAME_TITLE_REAL_PNG, ALERT_GAME_TITLE_REAL_PNG_2,
    ALERT_PREP_TITLE_CLEAR_PNG_A, ALERT_PREP_TITLE_CLEAR_PNG_B,
    VERIFY_CHECK_BUTTON_PNG, CURSE_PURPLE_TEXT_PNG,
)
from tg_notify import send_telegram, TG_ALERT_REPEAT_INTERVAL, TG_CONFIGURED

ALERT_SCAN_SECONDS = 0.12  # 第一階段很短，縮短輪詢間隔；實際仍受截圖/比對耗時限制
PREP_ALERT_THRESHOLD = 0.78  # 第一階段：小型、半透明提示使用較寬鬆門檻
GAME_ALERT_THRESHOLD = 0.90  # 第二階段：中央畫面維持嚴格門檻
CURSE_ALERT_THRESHOLD = 0.82  # 舊樣式詛咒橫幅（深灰底灰白字）：完整兩行固定文字的灰階比對
CURSE_PURPLE_THRESHOLD = 0.65  # 新樣式詛咒橫幅（紫框紫字）：紫色程度比對（實測詛咒 ≥0.85、一般畫面 ≤0.44）
CURSE_PURPLE_TEXT_WIDTH = 363.0  # 新樣式範本寬度（像素）
CURSE_OLD_SCALES = tuple(value / 100 for value in range(140, 261, 10))  # 舊樣式沿用原本的縮放範圍
_CURSE_PURPLE_TEMPLATE = None
VERIFY_ALERT_THRESHOLD = 0.60  # 輸入驗證視窗：Check 按鈕灰階比對分數（實測驗證視窗 ≥0.85、一般畫面 ≤0.33）
VERIFY_BOX_WIDTH = 324.0      # Check 範本取樣時，白色輸入框的寬度（像素）；其他位置都依輸入框寬度等比換算
VERIFY_RED_MIN_COVERAGE = 0.30  # 輸入框上方紅色警告字所在那一條，至少要有這個比例的欄位出現紅色
_CHECK_TEMPLATE = None
ALERT_CLEAR_SECONDS = 5.0
_TEMPLATE_CACHE = None


def load_title_templates():
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is None:
        import base64
        import cv2
        import numpy as np
        templates = []
        # 第一階段與第二階段分開辨識；第二階段標題優先，避免短暫畫面漏掉。
        sources = (
            ("第二階段實際標題A", ALERT_GAME_TITLE_REAL_PNG),
            ("第二階段實際標題B", ALERT_GAME_TITLE_REAL_PNG_2),
            ("第二階段標題", ALERT_GAME_TITLE_PNG),
            ("準備畫面清晰標題A", ALERT_PREP_TITLE_CLEAR_PNG_A),
            ("準備畫面清晰標題B", ALERT_PREP_TITLE_CLEAR_PNG_B),
            ("準備畫面實際標題", ALERT_PREP_TITLE_REAL_PNG),
            ("準備畫面標題A", ALERT_TITLE_PNG_2),
            ("準備畫面標題B", ALERT_TITLE_PNG),
        )
        game_scales = [1.0, 0.95, 1.05, 0.9, 1.1, 0.8, 1.2, 1.25, 1.5, 0.7]
        # 第一階段提示較小，且視窗模式大小可能改變，因此使用更密的縮放比例。
        prep_scales = [value / 100 for value in range(50, 151, 5)]
        for name, encoded in sources:
            scales = prep_scales if name.startswith("準備畫面") else game_scales
            for scale in scales:
                original = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                template = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
                templates.append((name, template))
        _TEMPLATE_CACHE = templates
    return _TEMPLATE_CACHE


def center_alert_area(image):
    """第二階段固定出現在畫面中央。"""
    width, height = image.size
    margin_x = int(width * 0.15)
    margin_y = int(height * 0.15)
    return image.crop((margin_x, margin_y, width - margin_x, height - margin_y))


def prepare_alert_area(image):
    """第一階段大約位於遊戲視窗右下；保留較寬範圍容許位置偏差。"""
    width, height = image.size
    return image.crop((int(width * 0.35), int(height * 0.35), width, height))


def curse_alert_area(image):
    """舊樣式詛咒橫幅固定在遊戲畫面上方中央。"""
    width, height = image.size
    return image.crop((int(width * 0.12), int(height * 0.04),
                       int(width * 0.88), int(height * 0.38)))


def load_check_template():
    global _CHECK_TEMPLATE
    if _CHECK_TEMPLATE is None:
        import base64
        import cv2
        import numpy as np
        _CHECK_TEMPLATE = cv2.imdecode(
            np.frombuffer(base64.b64decode(VERIFY_CHECK_BUTTON_PNG), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    return _CHECK_TEMPLATE


def verification_dialog_scores(image):
    """在整個畫面找輸入驗證視窗（位置、標題文字都不固定）；回傳 (各項分數, 最高分)。

    以每次都一樣的三個元素組合判斷：
      ①白色輸入框：近乎純白、寬高比 6～12 的長方形（先用它找出候選位置與大小）；
      ②Check 按鈕：在輸入框左下方，依輸入框寬度把該區縮放到範本大小後做灰階比對（分數＝最高分）；
      ③紅色警告字：輸入框上方約 1 個框寬處要有一條紅字，沒有就不算（分數記為 0）。
    """
    import cv2
    import numpy as np
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    template = load_check_template()
    white = cv2.inRange(rgb, (235, 235, 235), (255, 255, 255))
    count, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=4)
    best = (0.0, 0.0, 0.0)  # (分數, Check, 紅字)；取分數最高、同分時 Check 較高的候選
    candidates = 0
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if w < 120 or h < 12 or not 6 <= w / h <= 12 or area / float(w * h) < 0.7:
            continue
        candidates += 1
        unit = w / VERIFY_BOX_WIDTH
        # Check 按鈕：輸入框左下方。
        x1, y1 = max(0, int(x - 80 * unit)), y + h
        x2, y2 = min(width, int(x + 60 * unit)), min(height, int(y + h + 80 * unit))
        check = 0.0
        if x2 > x1 and y2 > y1:
            roi = cv2.resize(rgb[y1:y2, x1:x2], None, fx=1 / unit, fy=1 / unit, interpolation=cv2.INTER_AREA)
            if roi.shape[0] >= template.shape[0] and roi.shape[1] >= template.shape[1]:
                gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
                check = float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED).max())
        # 紅色警告字：輸入框上方約 300～340 像素（依框寬換算）的一條。
        rx1, rx2 = max(0, int(x - 30 * unit)), min(width, int(x + 360 * unit))
        ry1, ry2 = max(0, int(y - 340 * unit)), max(0, int(y - 300 * unit))
        red = 0.0
        if rx2 > rx1 and ry2 > ry1:
            band = rgb[ry1:ry2, rx1:rx2].astype(np.int16)
            redness = band[..., 0] - np.maximum(band[..., 1], band[..., 2])
            red = float((redness >= 60).any(axis=0).mean())
        best = max(best, (check if red >= VERIFY_RED_MIN_COVERAGE else 0.0, check, red))
    score, check, red = best
    return {"輸入驗證候選框": float(candidates), "輸入驗證Check": check, "輸入驗證紅字": red}, score


def _purple_mask(rgb):
    """明顯偏紫（藍、紅都比綠高）的像素；新樣式橫幅的外框、文字、鎖頭都是這個顏色。"""
    import numpy as np
    r, g, b = (rgb[..., index].astype(np.int16) for index in range(3))
    return (((b - g) >= 50) & ((r - g) >= 25) & (b >= 120)).astype(np.uint8) * 255


def _purpleness(rgb):
    """每個像素的紫色程度 0～120；比對前稍微模糊，容許縮放造成的筆畫變細。"""
    import cv2
    import numpy as np
    r, g, b = (rgb[..., index].astype(np.int16) for index in range(3))
    value = np.clip(np.minimum(b - g, (r - g) * 2) - 20, 0, 120).astype(np.float32)
    return cv2.GaussianBlur(value, (3, 3), 0)


def load_curse_templates():
    """回傳 (新樣式紫色程度範本, 舊樣式灰階範本)。"""
    global _CURSE_PURPLE_TEMPLATE
    import base64
    import cv2
    import numpy as np
    if _CURSE_PURPLE_TEMPLATE is None:
        purple = cv2.imdecode(np.frombuffer(base64.b64decode(CURSE_PURPLE_TEXT_PNG), dtype=np.uint8),
                              cv2.IMREAD_GRAYSCALE).astype(np.float32)
        old = cv2.imdecode(np.frombuffer(base64.b64decode(CURSE_ALERT_TEXT_PNG), dtype=np.uint8),
                           cv2.IMREAD_GRAYSCALE)
        _CURSE_PURPLE_TEMPLATE = (cv2.GaussianBlur(purple, (3, 3), 0), old)
    return _CURSE_PURPLE_TEMPLATE


def _resize(template, scale):
    import cv2
    return cv2.resize(template, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def _best_match(image, template):
    """回傳 (最高分, 左上角位置)；範本比圖大時分數為 0。"""
    import cv2
    if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1] or min(template.shape) < 4:
        return 0.0, (0, 0)
    _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED))
    return float(score), location


def _coarse_to_fine(image, template, scales, coarse_width=300.0):
    """先在縮小的圖上試所有大小，再用原圖、只在找到的位置附近細修最像的兩個大小（±3%）。"""
    import cv2
    shrink = min(1.0, coarse_width / image.shape[1])
    small = cv2.resize(image, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA) if shrink < 1 else image
    coarse = sorted((_best_match(small, _resize(template, scale * shrink)) + (scale,) for scale in scales),
                    reverse=True)[:2]
    best = 0.0
    for _, (x, y), scale in coarse:
        x, y = int(x / shrink), int(y / shrink)
        for factor in (0.97, 1.0, 1.03):
            fine = _resize(template, scale * factor)
            margin_x, margin_y = int(fine.shape[1] * 0.1) + 8, int(fine.shape[0] * 0.5) + 8
            area = image[max(0, y - margin_y):y + fine.shape[0] + margin_y,
                         max(0, x - margin_x):x + fine.shape[1] + margin_x]
            best = max(best, _best_match(area, fine)[0])
    return best


def curse_purple_score(rgb):
    """新樣式：先用紫色像素找出橫幅可能的位置（整個畫面、不限上方），再在該處比對兩行固定文字。"""
    import cv2
    purple_template, _ = load_curse_templates()
    mask = _purple_mask(rgb)
    grown = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 15)))
    count, _, stats, _ = cv2.connectedComponentsWithStats(grown, connectivity=8)
    height, width = mask.shape
    best = 0.0
    for index in range(1, count):
        x, y, w, h, _ = (int(value) for value in stats[index])
        if w < 150 or mask[y:y + h, x:x + w].sum() / 255 < 300:
            continue
        x1, y1, x2, y2 = max(0, x - 10), max(0, y - 10), min(width, x + w + 10), min(height, y + h + 10)
        region = _purpleness(rgb[y1:y2, x1:x2])
        low = max(0.3, 0.35 * w / CURSE_PURPLE_TEXT_WIDTH)
        high = min(region.shape[1] / purple_template.shape[1], region.shape[0] / purple_template.shape[0])
        scales = []
        while low <= high:
            scales.append(low)
            low *= 1.06
        if scales:
            best = max(best, _coarse_to_fine(region, purple_template, scales))
    return best


def curse_scores(image):
    """詛咒橫幅：新樣式在整個畫面找、舊樣式在上方中央找；回傳 (各項分數, 分數, 該樣式的門檻)。"""
    import cv2
    import numpy as np
    _, old_template = load_curse_templates()
    rgb = np.asarray(image.convert("RGB"))
    purple = curse_purple_score(rgb)
    old_area = np.asarray(curse_alert_area(image).convert("L"))
    old = _coarse_to_fine(old_area, old_template, CURSE_OLD_SCALES, coarse_width=480.0)
    details = {"詛咒新樣式": purple, "詛咒舊樣式": old}
    if purple / CURSE_PURPLE_THRESHOLD >= old / CURSE_ALERT_THRESHOLD:
        return details, purple, CURSE_PURPLE_THRESHOLD
    return details, old, CURSE_ALERT_THRESHOLD


def title_match_scores(image, full=False, allowed_prefixes=None, threshold=None):
    """回傳各辨識特徵的最高分，以及所有特徵中的最高分。"""
    import cv2
    import numpy as np
    if threshold is None:
        threshold = GAME_ALERT_THRESHOLD
    frame = np.asarray(image.convert("L"))
    details = {}
    for name, template in load_title_templates():
        if allowed_prefixes and not name.startswith(allowed_prefixes):
            continue
        h, w = template.shape
        if h > frame.shape[0] or w > frame.shape[1]:
            continue
        scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, _ = cv2.minMaxLoc(scores)
        details[name] = max(details.get(name, 0.0), float(maximum))
        if not full and details[name] >= threshold:
            return details, details[name]
    return details, max(details.values(), default=0.0)


def title_match_score(image):
    """相容既有監看流程，只回傳最高分。"""
    return title_match_scores(center_alert_area(image), threshold=GAME_ALERT_THRESHOLD)[1]


def alert_score_report(frame):
    """F8：回傳各類警告的辨識分數文字（只顯示，不傳 TG）。"""
    verify_details, verify_score = verification_dialog_scores(frame)
    curse_details, curse_score, curse_threshold = curse_scores(frame)
    prepare_details, prepare_score = title_match_scores(
        prepare_alert_area(frame),
        full=True,
        allowed_prefixes=("準備畫面",),
        threshold=PREP_ALERT_THRESHOLD,
    )
    game_details, game_score = title_match_scores(
        center_alert_area(frame),
        full=True,
        allowed_prefixes=("第二階段",),
        threshold=GAME_ALERT_THRESHOLD,
    )
    prepare_text = "、".join(
        f"{name}={value:.3f}"
        for name, value in sorted(
            prepare_details.items(), key=lambda item: item[1], reverse=True
        )
    )
    game_text = "、".join(
        f"{name}={value:.3f}"
        for name, value in sorted(
            game_details.items(), key=lambda item: item[1], reverse=True
        )
    )
    curse_text = (f"新樣式（整個畫面，紫字）={curse_details['詛咒新樣式']:.3f}、"
                  f"舊樣式（上方中央，灰字）={curse_details['詛咒舊樣式']:.3f}")
    verify_text = (
        f"白色輸入框候選 {int(verify_details['輸入驗證候選框'])} 個；"
        f"最像的一個 Check={verify_details['輸入驗證Check']:.3f}、"
        f"紅字覆蓋={verify_details['輸入驗證紅字']:.2f}（需≥{VERIFY_RED_MIN_COVERAGE}）"
    )
    return (
        f"整個畫面輸入驗證視窗：{verify_text}；分數={verify_score:.3f}\n"
        f"詛咒畫面：{curse_text}；分數={curse_score:.3f}（門檻 {curse_threshold}）\n"
        f"右下區域第一階段：{prepare_text}；最高={prepare_score:.3f}\n"
        f"中央第二階段：{game_text}；最高={game_score:.3f}\n"
        f"輸入驗證門檻={VERIFY_ALERT_THRESHOLD}；"
        f"詛咒畫面門檻=新樣式 {CURSE_PURPLE_THRESHOLD}／舊樣式 {CURSE_ALERT_THRESHOLD}；"
        f"第一階段門檻={PREP_ALERT_THRESHOLD}；"
        f"第二階段門檻={GAME_ALERT_THRESHOLD}；F8 不傳送 TG"
    )

class AlertLatch:
    def __init__(self):
        self.hits = 0
        self.active = False
        self.clear_since = None

    def update(self, matched, now):
        if matched:
            self.clear_since = None
            self.hits += 1
            if self.hits >= 1 and not self.active:
                self.active = True
                return True
        else:
            self.hits = 0
            if self.active:
                if self.clear_since is None:
                    self.clear_since = now
                elif now - self.clear_since >= ALERT_CLEAR_SECONDS:
                    self.active = False
        return False


class ScreenAlert:
    def __init__(self, gui, events):
        self.gui, self.events = gui, events
        self.region = None
        self.stop_event = threading.Event()
        self.pause_required = threading.Event()
        self.present = threading.Event()
        self.thread = None
        self.latch = AlertLatch()
        self.last_score = 0.0
        self.last_details = {}
        self.scan_count = 0
        self.phase = "等待遊戲視窗內的準備畫面"
        self.notify_queue = queue.Queue()
        self.notify_thread = threading.Thread(target=self.notifications, daemon=True)
        self.notify_thread.start()

    def notifications(self):
        while True:
            item = self.notify_queue.get()
            if item is None:
                return
            if isinstance(item, tuple):
                text, repeat_count = item
            else:
                text, repeat_count = item, 1
            repeat_count = max(1, int(repeat_count))
            if not TG_CONFIGURED:
                continue  # 未設定 TG：啟動時已提示過，不重複警告
            for index in range(repeat_count):
                ok, message = send_telegram(text)
                if not ok:
                    print(f"[警告] TG {index + 1}/{repeat_count} 發送失敗：{message}", flush=True)
                if index < repeat_count - 1:
                    time.sleep(TG_ALERT_REPEAT_INTERVAL)

    def start(self, region):
        if self.thread is not None and self.thread.is_alive():
            raise ValueError("監看已開啟")
        load_title_templates()  # 缺套件時在主執行緒顯示錯誤
        load_check_template()
        load_curse_templates()
        self.region = region
        self.latch = AlertLatch()
        self.stop_event.clear()
        self.present.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.present.clear()

    def loop(self):
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                region = self.region() if callable(self.region) else self.region
                frame = self.gui.screenshot(region=region)

                # 輸入驗證會持續顯示、位置不固定：在整個畫面找輸入框＋Check＋紅字，不分析或代填答案。
                verify_details, verify_score = verification_dialog_scores(frame)
                if verify_score >= VERIFY_ALERT_THRESHOLD:
                    details = verify_details
                    score = verify_score
                    threshold = VERIFY_ALERT_THRESHOLD
                    stage = "中央輸入驗證：怪物名稱或狀態"
                    self.phase = "等待輸入驗證消失"
                else:
                    # 詛咒橫幅：新樣式（紫字）在整個畫面找，舊樣式（灰字）在上方中央找。
                    curse_details, curse_score, curse_threshold = curse_scores(frame)
                    if curse_score >= curse_threshold:
                        details = {**verify_details, **curse_details}
                        score = curse_score
                        threshold = curse_threshold
                        stage = "詛咒畫面：解除詛咒提示"
                        self.phase = "等待詛咒提示消失"
                    else:
                        # 第一階段提示框很小且短暫，掃較小的右下範圍。
                        prepare_details, prepare_score = title_match_scores(
                            prepare_alert_area(frame),
                            allowed_prefixes=("準備畫面",),
                            threshold=PREP_ALERT_THRESHOLD,
                        )
                        if prepare_score >= PREP_ALERT_THRESHOLD:
                            details = {**verify_details, **curse_details, **prepare_details}
                            score = prepare_score
                            threshold = PREP_ALERT_THRESHOLD
                            stage = "第一階段：遊戲視窗內準備畫面"
                            self.phase = "等待中央第二階段"
                        else:
                            # 即使第一階段太短而漏掉，中央第二階段仍可獨立補抓。
                            game_details, game_score = title_match_scores(
                                center_alert_area(frame),
                                allowed_prefixes=("第二階段",),
                                threshold=GAME_ALERT_THRESHOLD,
                            )
                            details = {
                                **verify_details, **curse_details,
                                **prepare_details, **game_details,
                            }
                            score = game_score
                            threshold = GAME_ALERT_THRESHOLD
                            stage = "第二階段：中央小遊戲"
                            if game_score >= GAME_ALERT_THRESHOLD:
                                self.phase = "等待提示消失"
                self.last_details = details
                self.last_score = score
                self.scan_count += 1
                if self.stop_event.is_set():
                    return
                matched = score >= threshold
                if matched:
                    self.present.set()
                else:
                    self.present.clear()
                if self.latch.update(matched, time.monotonic()):
                    self.pause_required.set()
                    self.events.put(("screen_alert", {"score": score, "stage": stage}))
            except Exception:
                self.pause_required.set()
                self.events.put(("alert_error", None))
                return
            self.stop_event.wait(max(0.01, ALERT_SCAN_SECONDS - (time.monotonic() - cycle_started)))

    def close(self):
        self.stop()
        self.notify_queue.put(None)
