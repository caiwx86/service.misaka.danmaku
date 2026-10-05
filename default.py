import json
import sys
import traceback

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import settings
from resources.lib.api import ApiError, MisakaApi

ADDON_ID = settings.ADDON_ID


def send(cmd):
    """给后台服务发命令 (与面板使用同一机制), 皮肤按钮/按键可直接调用"""
    xbmcgui.Window(10000).setProperty('misaka.cmd', json.dumps(cmd))
    xbmc.executebuiltin('NotifyAll(%s,cmd)' % ADDON_ID)


def test_connection():
    cfg = settings.load()
    dlg = xbmcgui.Dialog()
    try:
        api = MisakaApi(cfg['server_url'], cfg['token'], cfg['verify_ssl'])
    except ApiError as e:
        dlg.ok('Misaka 弹幕', str(e))
        return
    try:
        v = api.version()
        dlg.ok('Misaka 弹幕', '连接成功\n' + json.dumps(v, ensure_ascii=False)[:160])
        return
    except ApiError as first:
        err = first
    try:  # 旧版本服务端没有 /version, 退一步测试搜索接口
        api.search_episodes('test')
        dlg.ok('Misaka 弹幕', '接口可用 (版本接口不可用, 服务端可能低于 2.8.7)')
    except ApiError:
        dlg.ok('Misaka 弹幕', '连接失败: %s' % err)


if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else 'panel'
    if arg == 'test':
        try:
            test_connection()
        except Exception:
            xbmc.log('[misaka.danmaku] test failed:\n' + traceback.format_exc(), xbmc.LOGERROR)
            xbmcgui.Dialog().ok('Misaka 弹幕', '出错了, 详情见 kodi.log:\n' + traceback.format_exc().strip().splitlines()[-1])
    elif arg == 'settings':
        xbmcaddon.Addon(ADDON_ID).openSettings()
    elif arg in ('toggle', 'mode'):          # RunScript(service.misaka.danmaku,toggle|mode)
        send({'action': arg})
    elif arg == 'shift' and len(sys.argv) > 2:  # RunScript(service.misaka.danmaku,shift,500)
        send({'action': 'shift', 'ms': int(sys.argv[2])})
    else:
        from resources.lib.ui import MisakaPanel
        path = xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')
        MisakaPanel('MisakaPanel.xml', path, 'Default', '1080i').doModal()
