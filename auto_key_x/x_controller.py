"""auto_key_x 主控制器：模式1（右鍵起手、按住 13～15 秒連點 X、左右輪流）與換邊空檔補技能。

畫面偵測、TG、平台層（快捷鍵、前景視窗、Mac 送鍵）直接沿用 auto_key 的模組，只讀不改；
需要先把 auto_key 資料夾加進 sys.path（auto_key_x.py 與測試都會做）。
"""
import queue
import random
import time

import platforms
from alert_detect import ScreenAlert, alert_score_report
from tg_notify import TG_CONFIGURED, TG_NOT_CONFIGURED_HINT, TG_ALERT_REPEAT_COUNT

import x_settings as cfg

UNUSED_HOTKEYS = ("1", "2", "3", "f10", "f11", "f12")  # auto_key 的選模式與小地圖，這支程式沒有


class Interrupted(Exception):
    pass


class TargetWindowInactive(Exception):
    """已鎖定的應用程式不在最前景。"""


class Controller:
    def __init__(self, gui):
        self.gui = gui
        self.events = queue.Queue()
        self.running = self.stopped = self.initialized = False
        self.direction = cfg.FIRST_DIRECTION
        self.switch_requested = False
        self.started = time.monotonic()
        self.pause_time = None
        self.last_pgup = None  # PgUp 實際按下的瞬間
        self.last_ins = 0
        self.last_x = None  # 上一次按下 X 的瞬間
        self.focus_lost_since = None
        self.native_hotkeys = None
        self.held = set()
        self.alert_window = None  # None = 整個主螢幕
        self.alert_window_title = cfg.load_saved_window_title()
        self.alert = ScreenAlert(gui, self.events)

    # ---------------- 狀態與視窗 ----------------
    def pause(self, message="已暫停"):
        if self.running:
            self.pause_time = time.monotonic()
        self.running = False
        self.focus_lost_since = None
        self.release()
        print(message, flush=True)

    def alert_region(self):
        size = self.gui.size()
        if self.alert_window is None:
            return (0, 0, size[0], size[1])
        return platforms.window_region(self.alert_window, size)

    def restore_alert_window(self):
        if not self.alert_window_title:
            return False
        handle = platforms.find_window_by_title(self.alert_window_title)
        if handle is None:
            return False
        platforms.window_region(handle, self.gui.size())
        self.alert_window = handle
        return True

    def start_alert_monitor(self):
        if self.alert.thread and self.alert.thread.is_alive():
            return False
        self.alert_region()
        self.alert.start(self.alert_region)
        return True

    def target_window_is_foreground(self):
        if self.alert_window is None:
            return True
        try:
            return platforms.foreground_window_handle() == self.alert_window
        except Exception:
            return False

    def begin_focus_wait(self):
        if self.focus_lost_since is None:
            self.focus_lost_since = time.monotonic()
            self.release()
            print("已切離鎖定應用程式，自動按鍵暫停", flush=True)

    def finish_focus_wait(self):
        """回到遊戲時順延計時（PgUp 以實際按下時間為準，不順延）。"""
        if self.focus_lost_since is None:
            return
        inactive_seconds = max(0, time.monotonic() - self.focus_lost_since)
        self.focus_lost_since = None
        self.started += inactive_seconds
        self.last_ins += inactive_seconds

    # ---------------- 快捷鍵事件 ----------------
    def handle_events(self):
        invalidated = False
        while not self.events.empty():
            key, mouse = self.events.get_nowait()
            if key == "f3":
                self.stopped = True
                self.pause("程式結束")
                invalidated = True
            elif self.stopped or key in UNUSED_HOTKEYS:
                continue
            elif key == "screen_alert":
                stage = mouse.get("stage", "透明圖形提示") if isinstance(mouse, dict) else "透明圖形提示"
                score = mouse.get("score", 0.0) if isinstance(mouse, dict) else 0.0
                self.pause(f"偵測到{stage}（分數 {score:.3f}），已暫停，請手動處理")
                self.alert.pause_required.clear()
                self.alert.notify_queue.put((
                    f"⚠️ 遊戲偵測到{stage}，自動按鍵已暫停。請回到電腦手動處理。\n"
                    + time.strftime("%Y-%m-%d %H:%M:%S"),
                    TG_ALERT_REPEAT_COUNT))
                invalidated = True
            elif key == "alert_error":
                self.pause("畫面監看失敗，已暫停；請確認截圖可用後按 F7 重新開啟")
                self.alert.pause_required.clear()
                invalidated = True
            elif key in ("f5", "f6"):
                if self.running:
                    print("請先按 F2 暫停，再更換監看目標", flush=True)
                    continue
                try:
                    if self.alert.thread and self.alert.thread.is_alive():
                        self.alert.stop()
                    if key == "f6":
                        self.alert_window = None
                        self.alert_window_title = ""
                        cfg.save_window_title("")
                        self.start_alert_monitor()
                        print("已改為整個主螢幕，並自動開啟監看", flush=True)
                    else:
                        handle = mouse or platforms.foreground_window_handle()
                        platforms.window_region(handle, self.gui.size())
                        title = platforms.get_window_title(handle)
                        if not title:
                            raise ValueError("無法取得目前視窗標題")
                        self.alert_window = handle
                        self.alert_window_title = title
                        cfg.save_window_title(title)
                        self.start_alert_monitor()
                        print(f"已記憶視窗「{title}」，並自動開啟監看", flush=True)
                except Exception as exc:
                    print(f"監看目標設定失敗：{exc}", flush=True)
            elif key == "f7":
                if self.alert.thread and self.alert.thread.is_alive():
                    self.alert.stop()
                    print("畫面監看已關閉", flush=True)
                else:
                    try:
                        self.start_alert_monitor()
                        print("畫面監看已開啟；偵測到即暫停，並以 1 秒間隔連續傳送 3 次 TG", flush=True)
                    except Exception as exc:
                        print(f"無法開啟監看：{exc}", flush=True)
            elif key == "f8":
                try:
                    print(alert_score_report(self.gui.screenshot(region=self.alert_region())), flush=True)
                except Exception as exc:
                    print(f"比對失敗：{exc}", flush=True)
            elif key == "f9":
                if not TG_CONFIGURED:
                    print(TG_NOT_CONFIGURED_HINT, flush=True)
                    continue
                self.alert.notify_queue.put("✅ 遊戲畫面通知測試：Telegram 連線正常。")
            elif key == "f2":
                self.pause()
                invalidated = True
            elif key == "f4" and self.running:
                self.switch_requested = True  # 中斷目前這一邊，下一輪從另一邊開始
                invalidated = True
            elif key == "f1" and not self.running:
                if self.alert.present.is_set() or self.alert.pause_required.is_set():
                    print("仍偵測到提示，請先手動處理；辨識清除後再按 F1", flush=True)
                    continue
                if not (self.alert.thread and self.alert.thread.is_alive()):
                    try:
                        if self.alert_window is None and self.alert_window_title:
                            if not self.restore_alert_window():
                                raise ValueError("找不到已記憶的遊戲視窗，請先開啟遊戲並按 F5 重新設定")
                        self.start_alert_monitor()
                    except Exception as exc:
                        print(f"無法開始：畫面監看啟動失敗：{exc}", flush=True)
                        continue
                if self.pause_time is not None:
                    self.last_ins += max(0, time.monotonic() - self.pause_time)
                self.pause_time = None
                self.running = True
        return invalidated

    # ---------------- 按鍵動作 ----------------
    def release(self):
        fail_safe = self.gui.FAILSAFE
        self.gui.FAILSAFE = False
        try:
            for key in set(self.held) | cfg.GAME_KEYS:
                self.gui.keyUp(key)
            self.held.clear()
        finally:
            self.gui.FAILSAFE = fail_safe

    def checkpoint(self):
        if self.handle_events() or self.stopped or not self.running or self.alert.pause_required.is_set():
            raise Interrupted()
        if not self.target_window_is_foreground():
            raise TargetWindowInactive()
        if time.monotonic() - self.started >= cfg.AUTO_STOP_SECONDS:
            self.stopped = True
            self.pause("已達 20 小時，停止")
            raise Interrupted()

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.checkpoint()
            time.sleep(min(0.01, max(0, until - time.monotonic())))

    def press(self, key):
        self.gui.keyDown(key)
        self.held.add(key)

    def tap(self, key, seconds):
        """按下 seconds 秒後放開；回傳實際按下的瞬間。"""
        self.checkpoint()
        try:
            self.press(key)
            pressed_at = time.monotonic()
            self.wait(seconds)
        finally:
            self.gui.keyUp(key)
            self.held.discard(key)
        return pressed_at

    def pgup_due(self, now):
        return self.last_pgup is None or now - self.last_pgup >= cfg.PGUP_INTERVAL_SECONDS

    def ins_home_due(self, now):
        return not self.initialized or now - self.last_ins >= cfg.INS_HOME_INTERVAL

    def buffs(self):
        """換邊空檔補技能：先放開方向鍵與 X、等 0.5 秒，再依到期狀態按 PgUp、Insert＋Home。"""
        now = time.monotonic()
        pgup, ins_home = self.pgup_due(now), self.ins_home_due(now)
        if not (pgup or ins_home):
            return
        self.release()
        self.wait(cfg.BUFF_RELEASE_SECONDS)
        if pgup:
            # 按壓完整完成才更新時間戳；中途暫停就保持到期，下次空檔再補。
            self.last_pgup = self.tap("pgup", cfg.PGUP_PRESS_SECONDS)
        if ins_home:
            self.wait(0.5)
            self.tap("insert", 0.05)
            self.wait(0.1)
            self.tap("home", 0.05)
            self.last_ins = time.monotonic()
        self.initialized = True

    @staticmethod
    def x_interval():
        return random.uniform(cfg.X_INTERVAL_MIN, cfg.X_INTERVAL_MAX)

    def hold_side(self):
        """按住目前方向 13～15 秒，期間每 0.05～0.15 秒點一下 X；完整做完才換邊。"""
        direction = self.direction
        self.checkpoint()
        try:
            self.press(direction)
            now = time.monotonic()
            end = now + random.uniform(cfg.HOLD_MIN_SECONDS, cfg.HOLD_MAX_SECONDS)
            # 換邊後的第一下也和上一下 X 保持 0.05～0.15 秒，節奏不會在換邊時擠在一起。
            next_press = now if self.last_x is None else max(now, self.last_x + self.x_interval())
            while True:
                self.wait(max(0.0, min(next_press, end) - time.monotonic()))
                remaining = end - time.monotonic()
                if remaining < cfg.X_PRESS_SECONDS:  # 不足一次點擊就不再按 X，準時放開方向鍵
                    self.wait(max(0.0, remaining))
                    break
                self.last_x = self.tap("x", cfg.X_PRESS_SECONDS)
                next_press = self.last_x + self.x_interval()
        finally:
            self.release()
        self.direction = "left" if direction == "right" else "right"

    def step(self):
        if self.switch_requested:
            self.switch_requested = False
            self.direction = "left" if self.direction == "right" else "right"
        self.buffs()
        self.hold_side()

    # ---------------- 主迴圈 ----------------
    def run(self):
        self.native_hotkeys = platforms.NativeHotkeys(self.events, position=self.gui.position)
        self.native_hotkeys.start()
        print("[就緒 auto_key_x] 按 F1 開始：先按住右鍵 13～15 秒並連點 X，之後左右輪流", flush=True)
        if not TG_CONFIGURED:
            print(TG_NOT_CONFIGURED_HINT, flush=True)
        if self.alert_window_title:
            try:
                if self.restore_alert_window():
                    self.start_alert_monitor()
                else:
                    print(f"尚未找到已記憶的視窗「{self.alert_window_title}」；開啟遊戲後按 F5 一次即可",
                          flush=True)
            except Exception as exc:
                print(f"自動還原畫面監看失敗：{exc}", flush=True)
        try:
            while not self.stopped:
                try:
                    self.handle_events()
                    if not self.running:
                        time.sleep(0.02)
                        continue
                    if not self.target_window_is_foreground():
                        self.begin_focus_wait()
                        time.sleep(0.05)
                        continue
                    self.finish_focus_wait()
                    self.step()
                except TargetWindowInactive:
                    self.begin_focus_wait()
                    time.sleep(0.05)
                except Interrupted:
                    self.release()
                    time.sleep(0.01)
                except Exception as exc:
                    self.pause(f"執行錯誤，已暫停：{exc}")
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self.alert.close()
            if self.native_hotkeys is not None:
                self.native_hotkeys.stop()
            self.release()
            print("程式已完全結束", flush=True)
