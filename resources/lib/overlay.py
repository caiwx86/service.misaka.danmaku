"""弹幕层: 向全屏视频窗口(12005)添加 ControlLabel, 用 GUI 动画滚动。
不占用字幕通道, 可与外挂字幕共存。

偏移约定: offset > 0 表示弹幕延后出现 (与 ASS 模式一致)。
暂停/拖动/恢复后, 会把此刻本该在屏幕上的弹幕按正确位置补回来。"""
import bisect
import threading

import xbmc
import xbmcgui

from . import danmaku as dm

FULLSCREEN_VIDEO = 12005
TOP_MARGIN = 0.03
BOTTOM_MARGIN = 0.15


class Overlay(object):
    def __init__(self, player):
        self.player = player
        self.items = []
        self.times = []
        self.cfg = None
        self._thread = None
        self._stop = threading.Event()
        self._paused = False
        self._resync = True
        self._lock = threading.Lock()
        self._monitor = xbmc.Monitor()

    # ---- 外部接口 ----
    def load(self, items, cfg):
        with self._lock:
            self.items = items
            self.times = [d.t for d in items]
            self.cfg = cfg
            self._resync = True

    def update_cfg(self, cfg):
        with self._lock:
            self.cfg = cfg
            self._resync = True

    def start(self):
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        if self._thread and self._thread.is_alive():
            self._thread.join(2)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='misaka-overlay')
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(2)
        self._thread = None

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False
        self._resync = True

    def seek(self):
        self._resync = True

    # ---- 内部 ----
    @staticmethod
    def _lanes(W, H, cfg):
        px = cfg['font_px'] * H / 1080.0
        n = int(H * cfg['area'] / 100.0 / (px * 1.25))
        return dm.Lanes(n, W / float(max(1, cfg['scroll_seconds'])))

    def _run(self):
        win = xbmcgui.Window(FULLSCREEN_VIDEO)
        W, H = win.getWidth(), win.getHeight()
        active = []  # (到期的媒体时间, 控件)
        lanes = None
        idx = 0
        last_t = None

        def remove(ctrl):
            try:
                win.removeControl(ctrl)
            except Exception:
                pass

        def clear():
            for _, c in active:
                remove(c)
            del active[:]

        while not self._stop.is_set():
            if self._monitor.waitForAbort(0.1):
                break
            cfg = self.cfg
            visible = xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)')
            if not cfg or not cfg['enabled'] or self._paused or not visible:
                if active:
                    clear()
                last_t = None  # 回来后强制重新同步并补回屏幕上的弹幕
                continue
            try:
                t = self.player.getTime() - cfg['offset']
            except RuntimeError:
                continue

            if self._resync or last_t is None or abs(t - last_t) > 1.5:
                self._resync = False
                clear()
                lanes = self._lanes(W, H, cfg)
                with self._lock:
                    items, times = self.items, self.times
                lo = bisect.bisect_left(times, t - cfg['scroll_seconds'] * 1.6)
                idx = bisect.bisect_right(times, t)
                for d in items[lo:idx]:  # 补回此刻仍在屏幕上的弹幕
                    if len(active) >= cfg['max_active']:
                        break
                    ctrl, exp = self._make(win, d, t, t - d.t, W, H, cfg, lanes)
                    if ctrl is not None:
                        active.append((exp, ctrl))
            last_t = t

            for e in [e for e in active if e[0] <= t]:
                active.remove(e)
                remove(e[1])

            with self._lock:
                items, n_items = self.items, len(self.items)
            while idx < n_items and items[idx].t <= t:
                d = items[idx]
                idx += 1
                if len(active) >= cfg['max_active'] or t - d.t > 1.0:
                    continue
                ctrl, exp = self._make(win, d, t, t - d.t, W, H, cfg, lanes)
                if ctrl is not None:
                    active.append((exp, ctrl))
        clear()

    @staticmethod
    def _make(win, d, now, elapsed, W, H, cfg, lanes):
        """elapsed: 该弹幕已出现了多久(新弹幕≈0, 补回的 >0)"""
        px = cfg['font_px'] * H / 1080.0
        lh = px * 1.25
        w = int(dm.text_width(d.text, px) + px)       # 估算宽度, 用于轨道
        cw = int(w * 1.4) + 8                          # 控件宽度留余量, 避免估算偏小时文字被截成省略号
        if d.mode != 'scroll' and elapsed >= lanes.fixed:
            return None, 0
        lane = lanes.alloc(d.mode, d.t, w)
        if lane is None:
            return None, 0
        alpha = int(255 * cfg['opacity'] / 100.0)
        color = '0x%02X%06X' % (alpha, d.color)
        text = dm.overlay_text(d.text)
        top_m, bot_m = H * TOP_MARGIN, H * BOTTOM_MARGIN
        try:
            if d.mode == 'scroll':
                x0 = int(W - elapsed * lanes.speed)
                remain = (x0 + cw) / lanes.speed
                if remain < 0.3:
                    return None, 0
                y = int(top_m + lane * lh)
                c = xbmcgui.ControlLabel(0, y, cw, int(lh), text, font=cfg['font_name'], textColor=color)
                win.addControl(c)
                c.setAnimations([('conditional', 'effect=slide start=%d,0 end=%d,0 time=%d tween=linear condition=true'
                                  % (x0, -cw, int(remain * 1000)))])
                return c, now + remain
            y = int(top_m + lane * lh) if d.mode == 'top' else int(H - bot_m - (lane + 1) * lh)
            c = xbmcgui.ControlLabel(0, y, W, int(lh), text, font=cfg['font_name'],
                                     textColor=color, alignment=2)  # 2 = 水平居中
            win.addControl(c)
            return c, now + (lanes.fixed - elapsed)
        except Exception as e:
            xbmc.log('[misaka.danmaku] overlay error: %s' % e, xbmc.LOGWARNING)
            return None, 0
