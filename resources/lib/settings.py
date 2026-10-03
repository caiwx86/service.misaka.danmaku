import xbmc
import xbmcaddon

ADDON_ID = 'service.misaka.danmaku'

# 默认值同时决定读取时的类型转换 (bool / int / str)
DEFAULTS = {
    'server_url': '', 'token': '', 'verify_ssl': True, 'auto_match': True, 'show_picker': True, 'with_related': True,
    'dedupe': True, 'min_minutes': 3, 'enabled': True, 'render_mode': 0, 'opacity': 85,
    'font_name': 'font13', 'font_px': 34, 'area': 50, 'max_active': 30, 'scroll_seconds': 9,
    'offset_ms': 0, 'blocklist': '', 'show_top': True, 'show_bottom': True,
}
_warned = set()


def _log(msg):
    xbmc.log('[misaka.danmaku] ' + msg, xbmc.LOGWARNING)


def _addon():
    try:
        return xbmcaddon.Addon(ADDON_ID)
    except Exception as e:
        _log('xbmcaddon.Addon failed: %r' % e)
        return None


def _get(a, key):
    """用最宽松的 getSetting() 读取, 再按默认值的类型转换。
    getSettingString/Bool/Int 在设置项缺失或类型不符时会抛异常, 这里任何异常都回退到默认值。"""
    default = DEFAULTS[key]
    try:
        raw = a.getSetting(key) if a else ''
    except Exception as e:
        if key not in _warned:
            _warned.add(key)
            _log('read setting %s failed: %r' % (key, e))
        return default
    if raw is None or raw == '':
        return default
    try:
        if isinstance(default, bool):
            return str(raw).strip().lower() in ('true', '1', 'yes')
        if isinstance(default, int):
            return int(float(raw))
        return str(raw)
    except (TypeError, ValueError):
        return default


def load():
    a = _addon()
    g = lambda k: _get(a, k)
    block = [k.strip() for k in str(g('blocklist')).replace('，', ',').split(',') if k.strip()]
    return {
        'server_url': g('server_url'),
        'token': g('token'),
        'verify_ssl': g('verify_ssl'),
        'auto_match': g('auto_match'),
        'show_picker': g('show_picker'),
        'with_related': g('with_related'),
        'dedupe': g('dedupe'),
        'min_minutes': g('min_minutes'),
        'enabled': g('enabled'),
        'render_mode': g('render_mode'),
        'opacity': g('opacity'),
        'font_name': g('font_name') or 'font13',
        'font_px': g('font_px'),
        'area': g('area'),
        'max_active': g('max_active'),
        'scroll_seconds': g('scroll_seconds'),
        'offset': g('offset_ms') / 1000.0,
        'blocklist': block,
        'show_top': g('show_top'),
        'show_bottom': g('show_bottom'),
    }


def _set(key, text):
    a = _addon()
    try:
        a.setSetting(key, text)
    except Exception as e:
        _log('write setting %s failed: %r' % (key, e))


def set_bool(key, value):
    _set(key, 'true' if value else 'false')


def set_int(key, value):
    _set(key, str(int(value)))
