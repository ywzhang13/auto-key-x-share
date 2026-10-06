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

ALERT_SCAN_SECONDS = 0.22  # 每輪間隔：準備畫面只顯示 1～2 秒，每輪都看；最慢 0.22 秒＋截圖比對約 0.03 秒，留空間給系統排程延遲
SLOW_SCAN_SECONDS = 1.0    # 輸入驗證、第二階段會停留較久，每秒看一次；結果沿用到下次檢查
CURSE_SCAN_SECONDS = 3.0   # 詛咒橫幅會一直顯示到解除，每 3 秒看一次
PREP_ALERT_THRESHOLD = 0.78  # 第一階段：準備面板標題「準備尋找透明圖形」
GAME_ALERT_THRESHOLD = 0.90  # 第二階段：上方標題「尋找透明圖形」
# 面板位置由畫面找出來；標題文字的寬度直接在面板頂端量（實機截圖量得的版面比例）：
PREP_TITLE_HEIGHT = 0.18   # 準備面板：標題在面板最上面這個比例的高度內
STAGE2_BAR_ASPECT = (6.0, 20.0)  # 第二階段：上方深灰標題列的寬 ÷ 高（實測約 12.7；背景深灰相連時會變矮胖）
DETECT_WIDTH = 640         # 找面板時先把畫面縮到這個寬度（只用來找位置，比對標題用原圖）
PREP_AREA_START = 0.35     # 準備面板出現在遊戲視窗右下：只看右邊、下面 65% 的範圍（與舊版相同）
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
_TITLE_ENTRIES = {}
_LAST_CYCLE = {}  # 監看執行緒最近一輪各項偵測的耗時（毫秒），F8 報告顯示


# 準備畫面（第一階段）與第二階段的標題範本：白字、深灰底。
TITLE_SOURCES = (
    ("第二階段實際標題A", ALERT_GAME_TITLE_REAL_PNG),
    ("第二階段實際標題B", ALERT_GAME_TITLE_REAL_PNG_2),
    ("第二階段標題", ALERT_GAME_TITLE_PNG),
    ("準備畫面清晰標題A", ALERT_PREP_TITLE_CLEAR_PNG_A),
    ("準備畫面清晰標題B", ALERT_PREP_TITLE_CLEAR_PNG_B),
    ("準備畫面實際標題", ALERT_PREP_TITLE_REAL_PNG),
    ("準備畫面標題A", ALERT_TITLE_PNG_2),
    ("準備畫面標題B", ALERT_TITLE_PNG),
)


def prep_region(region):
    """遊戲視窗 (x, y, 寬, 高) → 準備面板會出現的右下角範圍。"""
    x, y, width, height = region
    dx, dy = int(width * PREP_AREA_START), int(height * PREP_AREA_START)
    return (x + dx, y + dy, width - dx, height - dy)


def prep_area(image):
    """整張截圖 → 準備面板會出現的右下角。"""
    width, height = image.size
    return image.crop((int(width * PREP_AREA_START), int(height * PREP_AREA_START), width, height))


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
    """明顯偏紫（藍、紅都比綠高）的像素；新樣式橫幅的外框、文字、鎖頭都是這個顏色。
    用 OpenCV 的飽和減法（負數變 0），結果與逐像素相減相同，但快很多。"""
    import cv2
    import numpy as np
    red, green, blue = cv2.split(np.ascontiguousarray(rgb))
    mask = (cv2.subtract(blue, green) >= 50) & (cv2.subtract(red, green) >= 25) & (blue >= 120)
    return mask.astype(np.uint8) * 255


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


def _text_box(template):
    """範本裡白字的範圍 (x, y, 寬, 高)。"""
    import numpy as np
    ys, xs = np.nonzero(template >= 170)
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def load_title_templates(prefix):
    """回傳 [(名稱, 範本, 文字寬)]；prefix 是「準備畫面」或「第二階段」。"""
    if prefix not in _TITLE_ENTRIES:
        import base64
        import cv2
        import numpy as np
        entries = []
        for name, encoded in TITLE_SOURCES:
            if name.startswith(prefix):
                template = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                entries.append((name, template, _text_box(template)[2]))
        _TITLE_ENTRIES[prefix] = entries
    return _TITLE_ENTRIES[prefix]


def _components(mask, shrink, close):
    """回傳遮罩裡各塊的 (x, y, 寬, 高, 填滿比例)，座標已換回原圖。"""
    import cv2
    import numpy as np
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    return [(int(x / shrink), int(y / shrink), int(w / shrink), int(h / shrink), area / float(w * h))
            for x, y, w, h, area in (stats[index] for index in range(1, count))]


def _shrunk_rgb(image):
    import cv2
    import numpy as np
    rgb = np.asarray(image.convert("RGB"))
    shrink = min(1.0, DETECT_WIDTH / float(rgb.shape[1]))
    small = cv2.resize(rgb, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_LINEAR) if shrink < 1 else rgb
    return rgb, np.ascontiguousarray(small), shrink


def _panel_gray(rgb):
    """遊戲提示面板的深灰色（約 44～52，三色幾乎一樣）；用 OpenCV 運算，比 numpy 快。"""
    import cv2
    import numpy as np
    rgb = np.ascontiguousarray(rgb)
    red, green, blue = cv2.split(rgb)
    spread = cv2.max(cv2.max(cv2.absdiff(red, green), cv2.absdiff(green, blue)), cv2.absdiff(red, blue))
    in_range = cv2.inRange(rgb, (38, 38, 38), (60, 60, 60))
    return ((in_range > 0) & (spread <= 8)).astype(np.uint8)


def _title_extent(rgb, box):
    """在深灰標題列 box=(x1, y1, x2, y2) 裡量白字標題的左右範圍；回傳 (左, 右) 或 None。"""
    import numpy as np
    x1, y1, x2, y2 = (max(0, int(value)) for value in box)
    strip = rgb[y1:y2, x1:x2]
    if strip.size == 0:
        return None
    import cv2
    # 只算面板裡的白點（排除框外上下左右的背景）：那一列、那一欄都大半是面板深灰，
    # 而且附近有面板深灰（範圍約字高，字放大後筆畫變粗也算得到）。
    panel = _panel_gray(strip)
    inside_rows = panel.mean(axis=1, keepdims=True) >= 0.5
    if not inside_rows.any():
        return None
    inside_columns = panel[inside_rows[:, 0]].mean(axis=0) >= 0.4  # 欄的比例只看面板裡的列
    window = max(9, strip.shape[0] // 3) | 1
    near_panel = cv2.blur(panel.astype(np.float32), (window, window)) >= 0.3
    bright = strip.min(axis=2) >= 170  # 與範本量字寬同一門檻
    columns = (bright & near_panel & inside_rows).any(axis=0) & inside_columns
    xs = np.nonzero(columns)[0]
    if len(xs) == 0:
        return None
    gap = max(4, int((x2 - x1) * 0.04))                  # 字與字之間的空隙不算斷開
    runs, start = [], xs[0]
    for previous, current in zip(xs, xs[1:]):
        if current - previous > gap:
            runs.append((start, previous))
            start = current
    runs.append((start, xs[-1]))
    left, right = max(runs, key=lambda run: run[1] - run[0])
    return x1 + int(left), x1 + int(right) + 1


def _match_title(gray, rgb, box, prefix, threshold):
    """量出 box 裡標題的寬度，把該組標題範本縮放到同寬（±3%）在附近比對；回傳 {名稱: 最高分}。"""
    import cv2
    extent = _title_extent(rgb, box)
    if extent is None or extent[1] - extent[0] < 20:
        return {}
    text_left, text_right = extent
    text_width = text_right - text_left
    _, y1, _, y2 = (max(0, int(value)) for value in box)
    pad = int(text_width * 0.2) + 4
    x1 = max(0, text_left - pad)
    area = gray[y1:y2, x1:text_right + pad]
    details = {}
    for name, template, template_width in load_title_templates(prefix):
        for factor in (1.0, 0.97, 1.03):
            scale = text_width * factor / template_width
            sized = cv2.resize(template, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            if sized.shape[0] > area.shape[0] or sized.shape[1] > area.shape[1] or min(sized.shape) < 4:
                continue
            score = float(cv2.matchTemplate(area, sized, cv2.TM_CCOEFF_NORMED).max())
            details[name] = max(details.get(name, 0.0), score)
            if threshold is not None and score >= threshold:
                return details
    return details


def prep_panel_scores(image, threshold=PREP_ALERT_THRESHOLD):
    """第一階段：整個畫面找「近乎正方形的深灰面板」，再比對面板頂端的標題「準備尋找透明圖形」。"""
    import cv2
    rgb, small, shrink = _shrunk_rgb(image)
    gray = None
    details = {}
    for x, y, w, h, fill in _components(_panel_gray(small), shrink, 5):
        if w < 120 or not 0.75 <= h / float(w) <= 1.3 or fill < 0.6:
            continue
        if gray is None:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        found = _match_title(gray, rgb, (x, y, x + w, y + h * PREP_TITLE_HEIGHT), "準備畫面", threshold)
        for name, score in found.items():
            details[name] = max(details.get(name, 0.0), score)
        if threshold is not None and max(details.values(), default=0.0) >= threshold:
            break
    return details, max(details.values(), default=0.0)


def stage2_scores(image, threshold=GAME_ALERT_THRESHOLD):
    """第二階段：整個畫面找「又寬又扁的深灰標題列、正下方是金色紋理」，再比對標題「尋找透明圖形」。
    不看中間的圖形（星星、三角形、圓形、正方形都可能）。"""
    import cv2
    rgb, small, shrink = _shrunk_rgb(image)
    red, green, blue = cv2.split(small)
    red_green, green_blue = cv2.subtract(red, green), cv2.subtract(green, blue)  # 小於 0 時為 0
    # 金色紋理：R 比 G 高 10～50、G 比 B 高 25～80（夕陽、橘紅色背景的 R−G 更大，不算）。
    gold = ((red_green >= 10) & (red_green <= 50) & (green_blue >= 25) & (green_blue <= 80) & (red >= 90))
    gray = None
    details = {}
    for x, y, w, h, fill in _components(_panel_gray(small), shrink, 3):
        if w < 150 or not STAGE2_BAR_ASPECT[0] <= w / float(h) <= STAGE2_BAR_ASPECT[1] or fill < 0.55:
            continue
        sx1, sx2 = int(x * shrink), int((x + w) * shrink)
        sy1, sy2 = int((y + h) * shrink), int((y + 3 * h) * shrink)
        below = gold[sy1:sy2, sx1:sx2]
        if below.size == 0 or below.mean() < 0.5:
            continue  # 標題列正下方不是金色紋理
        if gray is None:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        found = _match_title(gray, rgb, (x, y, x + w, y + h), "第二階段", threshold)
        for name, score in found.items():
            details[name] = max(details.get(name, 0.0), score)
        if threshold is not None and max(details.values(), default=0.0) >= threshold:
            break
    return details, max(details.values(), default=0.0)


def _timed(timings, label, function, *args):
    started = time.perf_counter()
    result = function(*args)
    timings[label] = (time.perf_counter() - started) * 1000
    return result


def _scores_text(details):
    return "、".join(f"{name}={value:.3f}" for name, value in
                    sorted(details.items(), key=lambda item: item[1], reverse=True)) or "沒有找到面板"


def alert_score_report(frame):
    """F8：回傳各類警告的辨識分數與耗時（只顯示，不傳 TG）。"""
    timings = {}
    verify_details, verify_score = _timed(timings, "輸入驗證", verification_dialog_scores, frame)
    curse_details, curse_score, curse_threshold = _timed(timings, "詛咒", curse_scores, frame)
    prepare_details, prepare_score = _timed(timings, "準備畫面", prep_panel_scores, prep_area(frame), None)
    game_details, game_score = _timed(timings, "第二階段", stage2_scores, frame, None)
    curse_text = (f"新樣式（整個畫面，紫字）={curse_details['詛咒新樣式']:.3f}、"
                  f"舊樣式（上方中央，灰字）={curse_details['詛咒舊樣式']:.3f}")
    verify_text = (
        f"白色輸入框候選 {int(verify_details['輸入驗證候選框'])} 個；"
        f"最像的一個 Check={verify_details['輸入驗證Check']:.3f}、"
        f"紅字覆蓋={verify_details['輸入驗證紅字']:.2f}（需≥{VERIFY_RED_MIN_COVERAGE}）"
    )
    timing_text = "、".join(f"{label} {value:.0f}ms" for label, value in timings.items())
    lines = [
        f"整個畫面輸入驗證視窗：{verify_text}；分數={verify_score:.3f}",
        f"詛咒畫面：{curse_text}；分數={curse_score:.3f}（門檻 {curse_threshold}）",
        f"第一階段準備面板（視窗右下角找深灰面板）：{_scores_text(prepare_details)}；最高={prepare_score:.3f}",
        f"第二階段（整個畫面找金色區＋標題列）：{_scores_text(game_details)}；最高={game_score:.3f}",
        f"輸入驗證門檻={VERIFY_ALERT_THRESHOLD}；"
        f"詛咒畫面門檻=新樣式 {CURSE_PURPLE_THRESHOLD}／舊樣式 {CURSE_ALERT_THRESHOLD}；"
        f"第一階段門檻={PREP_ALERT_THRESHOLD}；第二階段門檻={GAME_ALERT_THRESHOLD}；F8 不傳送 TG",
        f"這次報告各項偵測耗時：{timing_text}",
    ]
    if _LAST_CYCLE:
        lines.append(
            f"監看中每輪耗時：截圖 {_LAST_CYCLE.get('截圖', 0):.0f}ms＋準備畫面 {_LAST_CYCLE.get('準備畫面', 0):.0f}ms"
            f"（每 {ALERT_SCAN_SECONDS} 秒）；輸入驗證 {_LAST_CYCLE.get('輸入驗證', 0):.0f}ms、"
            f"第二階段 {_LAST_CYCLE.get('第二階段', 0):.0f}ms（每 {SLOW_SCAN_SECONDS:g} 秒）；"
            f"詛咒 {_LAST_CYCLE.get('詛咒', 0):.0f}ms（每 {CURSE_SCAN_SECONDS:g} 秒）")
    return "\n".join(lines)

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
        load_title_templates("準備畫面")  # 缺套件時在主執行緒顯示錯誤
        load_title_templates("第二階段")
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
        slow = curse = None
        next_slow = next_curse = 0.0
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                timings = {}
                region = self.region() if callable(self.region) else self.region
                full_scan = slow is None or curse is None or cycle_started >= min(next_slow, next_curse)
                if full_scan:
                    frame = _timed(timings, "截圖", self.gui.screenshot, region)
                    prepare_area = prep_area(frame)
                else:
                    # 只看準備面板的輪次：只截右下角，截圖與比對都省一半以上。
                    prepare_area = _timed(timings, "截圖", self.gui.screenshot, prep_region(region))
                # 第一階段準備面板只顯示 1～2 秒：每輪截圖後第一個看，從出現到暫停 ≤0.3 秒。
                prepare_details, prepare_score = _timed(timings, "準備畫面", prep_panel_scores, prepare_area)
                if slow is None or cycle_started >= next_slow:
                    # 輸入驗證、第二階段會停留較久：每秒看一次，結果沿用到下次檢查。
                    next_slow = cycle_started + SLOW_SCAN_SECONDS
                    slow = (_timed(timings, "輸入驗證", verification_dialog_scores, frame),
                            _timed(timings, "第二階段", stage2_scores, frame))
                if curse is None or cycle_started >= next_curse:
                    next_curse = cycle_started + CURSE_SCAN_SECONDS
                    curse = _timed(timings, "詛咒", curse_scores, frame)
                (verify_details, verify_score), (game_details, game_score) = slow
                curse_details, curse_score, curse_threshold = curse

                # 輸入驗證：在整個畫面找輸入框＋Check＋紅字，不分析或代填答案。
                if verify_score >= VERIFY_ALERT_THRESHOLD:
                    details = verify_details
                    score = verify_score
                    threshold = VERIFY_ALERT_THRESHOLD
                    stage = "中央輸入驗證：怪物名稱或狀態"
                    self.phase = "等待輸入驗證消失"
                elif curse_score >= curse_threshold:
                    # 詛咒橫幅：新樣式（紫字）在整個畫面找，舊樣式（灰字）在上方中央找。
                    details = {**verify_details, **curse_details}
                    score = curse_score
                    threshold = curse_threshold
                    stage = "詛咒畫面：解除詛咒提示"
                    self.phase = "等待詛咒提示消失"
                elif prepare_score >= PREP_ALERT_THRESHOLD:
                    details = {**verify_details, **curse_details, **prepare_details}
                    score = prepare_score
                    threshold = PREP_ALERT_THRESHOLD
                    stage = "第一階段：遊戲視窗內準備畫面"
                    self.phase = "等待中央第二階段"
                else:
                    # 即使第一階段太短而漏掉，第二階段仍可獨立補抓。
                    details = {
                        **verify_details, **curse_details,
                        **prepare_details, **game_details,
                    }
                    score = game_score
                    threshold = GAME_ALERT_THRESHOLD
                    stage = "第二階段：中央小遊戲"
                    if game_score >= GAME_ALERT_THRESHOLD:
                        self.phase = "等待提示消失"
                _LAST_CYCLE.update(timings)
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
