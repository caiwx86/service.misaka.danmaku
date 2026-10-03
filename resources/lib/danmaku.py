"""弹幕解析、轨道分配、ASS 生成、与外挂字幕合并"""
import os
import re
import unicodedata
from collections import namedtuple

Danmaku = namedtuple('Danmaku', 't mode color text')  # mode: scroll / top / bottom


def parse(raw, blocklist=(), show_top=True, show_bottom=True, dedupe=True):
    """raw: /comment/{id} 返回的 dict, p = "时间,模式,颜色(十进制RGB),来源" """
    out = []
    last = {}  # 文本 -> 上次出现时间, 用于去掉刷屏
    for c in (raw or {}).get('comments') or []:
        p = str(c.get('p', '')).split(',')
        text = str(c.get('m', '')).replace('\r', ' ').replace('\n', ' ').strip()
        if len(p) < 3 or not text:
            continue
        try:
            t = float(p[0])
            m = int(p[1])
            color = int(p[2])
        except ValueError:
            continue
        if blocklist and any(k in text for k in blocklist):
            continue
        if m in (1, 2, 3, 6):
            mode = 'scroll'
        elif m == 5 and show_top:
            mode = 'top'
        elif m == 4 and show_bottom:
            mode = 'bottom'
        else:
            continue
        out.append(Danmaku(t, mode, color & 0xFFFFFF, text))
    out.sort(key=lambda d: d.t)
    if dedupe:
        kept = []
        for d in out:
            if d.t - last.get(d.text, -1e9) < 3.0:
                continue
            last[d.text] = d.t
            kept.append(d)
        out = kept
    return out


def overlay_text(text):
    """Kodi 的 Label 会解析 $INFO[] 和 [B]/[COLOR] 等标记, 弹幕里出现时要转成全角"""
    return text.replace('$', '＄').replace('[', '［').replace(']', '］')


def text_width(text, px):
    w = 0.0
    for ch in text:
        w += px if unicodedata.east_asian_width(ch) in ('W', 'F') else px * 0.55
    return w


class Lanes(object):
    """滚动弹幕恒速(不会追尾); 顶部/底部弹幕固定停留 fixed 秒"""

    def __init__(self, count, speed, fixed=4.0, gap=0.3):
        count = max(1, count)
        fixed_count = max(1, count // 2)
        self.speed = float(speed)
        self.fixed = fixed
        self.gap = gap
        self.scroll = [-1e9] * count
        self.top = [-1e9] * fixed_count
        self.bottom = [-1e9] * fixed_count

    def duration(self, width, screen_w):
        return (screen_w + width) / self.speed

    def alloc(self, mode, t, width):
        if mode == 'scroll':
            arr, hold = self.scroll, width / self.speed + self.gap
        else:
            arr, hold = (self.top if mode == 'top' else self.bottom), self.fixed
        for i, free in enumerate(arr):
            if free <= t:
                arr[i] = t + hold
                return i
        return None


# ---------------- ASS ----------------

def _ts(t):
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return '%d:%02d:%02d.%02d' % (h, m, s, cs)


def _bgr(color):
    return '&H%02X%02X%02X&' % (color & 255, (color >> 8) & 255, (color >> 16) & 255)


def _style_line(H, opts):
    px = max(8, int(opts['font_px'] * H / 1080.0))
    alpha = int(255 * (100 - opts['opacity']) / 100.0)
    ol = max(1, int(2 * H / 1080.0))
    return ('Style: Danmaku,Arial,%d,&H%02XFFFFFF,&H00FFFFFF,&H00000000,&H00000000,'
            '0,0,0,0,100,100,0,0,1,%d,0,7,0,0,0,1' % (px, alpha, ol))


def build_events(items, W, H, opts):
    px = max(8, int(opts['font_px'] * H / 1080.0))
    lh = px * 1.25
    n = int(H * opts['area'] / 100.0 / lh)
    speed = W / float(max(1, opts['scroll_seconds']))
    lanes = Lanes(n, speed)
    top_m, bot_m = H * 0.03, H * 0.15
    off = opts.get('offset', 0.0)
    events = []
    for d in items:
        text = d.text.replace('{', '(').replace('}', ')')
        w = text_width(text, px) + px
        lane = lanes.alloc(d.mode, d.t, w)
        if lane is None:
            continue
        t0 = d.t + off
        col = '' if d.color == 0xFFFFFF else '\\c' + _bgr(d.color)
        if d.mode == 'scroll':
            y = int(top_m + lane * lh)
            dur = lanes.duration(w, W)
            tags = '\\an7\\move(%d,%d,%d,%d)%s' % (W, y, -int(w), y, col)
            events.append('Dialogue: 0,%s,%s,Danmaku,,0,0,0,,{%s}%s' % (_ts(t0), _ts(t0 + dur), tags, text))
        elif d.mode == 'top':
            y = int(top_m + lane * lh)
            events.append('Dialogue: 0,%s,%s,Danmaku,,0,0,0,,{\\an8\\pos(%d,%d)%s}%s' %
                          (_ts(t0), _ts(t0 + lanes.fixed), W // 2, y, col, text))
        else:
            y = int(H - bot_m - lane * lh)
            events.append('Dialogue: 0,%s,%s,Danmaku,,0,0,0,,{\\an2\\pos(%d,%d)%s}%s' %
                          (_ts(t0), _ts(t0 + lanes.fixed), W // 2, y, col, text))
    return events


_FORMATS = ('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
            'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, '
            'Shadow, Alignment, MarginL, MarginR, MarginV, Encoding')


def _header(W, H):
    return '\n'.join([
        '[Script Info]', 'ScriptType: v4.00+', 'PlayResX: %d' % W, 'PlayResY: %d' % H,
        'WrapStyle: 2', 'ScaledBorderAndShadow: yes', '',
        '[V4+ Styles]', _FORMATS,
        'Style: Default,Arial,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,40,1',
        '', '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text', ''])


def build_ass(items, opts, W=1920, H=1080):
    lines = _header(W, H).split('\n')
    # 在 Default 样式后插入 Danmaku 样式
    idx = max(i for i, l in enumerate(lines) if l.startswith('Style:'))
    lines.insert(idx + 1, _style_line(H, opts))
    lines += build_events(items, W, H, opts)
    return '\n'.join(lines) + '\n'


def _read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'ignore')


_SRT_RE = re.compile(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)\s*\n(.*?)(?:\n\s*\n|\Z)', re.S)


def _srt_to_ass(text):
    def sec(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, '0')[:3]) / 1000.0
    lines = _header(1920, 1080).split('\n')
    for m in _SRT_RE.finditer(text.replace('\r\n', '\n').replace('\r', '\n') + '\n\n'):
        a = sec(*m.group(1, 2, 3, 4))
        b = sec(*m.group(5, 6, 7, 8))
        body = re.sub(r'<[^>]+>|\{[^}]*\}', '', m.group(9)).strip().replace('\n', '\\N')
        if body:
            lines.append('Dialogue: 0,%s,%s,Default,,0,0,0,,%s' % (_ts(a), _ts(b), body))
    return '\n'.join(lines) + '\n'


def _play_res(lines):
    W = H = None
    for l in lines:
        s = l.strip()
        if s.startswith('PlayResX:'):
            W = int(s.split(':')[1])
        elif s.startswith('PlayResY:'):
            H = int(s.split(':')[1])
        elif s.startswith('[V4') or s.startswith('[Events'):
            break
    return W or 384, H or 288


def merge_subtitle(sub_path, items, opts, out_path):
    """把弹幕合并进 .srt / .ass 外挂字幕, 写入 out_path; 失败返回 None"""
    ext = os.path.splitext(sub_path)[1].lower()
    if ext not in ('.srt', '.ass'):
        return None
    text = _read_text(sub_path)
    if ext == '.srt':
        text = _srt_to_ass(text)
    lines = text.replace('\r\n', '\n').split('\n')
    last_style = first_dlg = -1
    in_styles = False
    for i, l in enumerate(lines):
        s = l.strip()
        if s.lower().startswith('[v4+'):
            in_styles = True
        elif s.startswith('['):
            in_styles = False
        if in_styles and s.startswith('Style:'):
            last_style = i
        if s.startswith('Dialogue:') and first_dlg < 0:
            first_dlg = i
    if last_style < 0:
        return None
    W, H = _play_res(lines)
    events = build_events(items, W, H, opts)
    if first_dlg < 0:
        lines += events
    else:  # 同层按文件顺序绘制, 弹幕放前面 = 在字幕下面
        lines[first_dlg:first_dlg] = events
    lines.insert(last_style + 1, _style_line(H, opts))
    with open(out_path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines) + '\n')
    return out_path
