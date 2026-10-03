"""自建 UI: 匹配选择 + 播放控制面板 (WindowXMLDialog)"""
import json

import xbmc
import xbmcgui

from . import settings
from .api import ApiError, MisakaApi, candidates_from_search

ADDON_ID = settings.ADDON_ID
HOME = xbmcgui.Window(10000)

ID_TITLE, ID_STATUS, ID_LIST = 10, 11, 100
BTN_SEARCH, BTN_TOGGLE, BTN_MODE, BTN_SUB, BTN_MINUS, BTN_PLUS, BTN_SETTINGS, BTN_CLOSE = \
    201, 202, 203, 204, 205, 206, 207, 208


def send(cmd):
    HOME.setProperty('misaka.cmd', json.dumps(cmd, ensure_ascii=False))
    xbmc.executebuiltin('NotifyAll(%s,cmd)' % ADDON_ID)


class MisakaPanel(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cands = []

    def onInit(self):
        raw = HOME.getProperty('misaka.candidates')
        if raw:
            try:
                self._fill(json.loads(raw))
            except ValueError:
                pass
        self._refresh()
        self.setFocusId(ID_LIST if self.cands else BTN_SEARCH)

    def _fill(self, cands):
        self.cands = cands
        lst = self.getControl(ID_LIST)
        lst.reset()
        lst.addItems([xbmcgui.ListItem(c['label']) for c in cands])

    def _refresh(self):
        eps = HOME.getProperty('misaka.episode')
        mode = 'ASS 外挂字幕' if HOME.getProperty('misaka.mode') == '1' else '弹幕层'
        on = '开' if HOME.getProperty('misaka.enabled') != '0' else '关'
        self.getControl(ID_TITLE).setLabel(eps or '尚未加载弹幕')
        self.getControl(ID_STATUS).setLabel('%s | 弹幕 %s 条 | 方式: %s | 开关: %s' % (
            HOME.getProperty('misaka.status') or '-', HOME.getProperty('misaka.count') or '0', mode, on))

    def _search(self):
        kb = xbmc.Keyboard('', '搜索番剧名')
        kb.doModal()
        if not kb.isConfirmed() or not kb.getText().strip():
            return
        try:
            cfg = settings.load()
            data = MisakaApi(cfg['server_url'], cfg['token'], cfg['verify_ssl']).search_episodes(kb.getText().strip())
        except ApiError as e:
            xbmcgui.Dialog().ok('Misaka 弹幕', '搜索失败: %s' % e)
            return
        cands = candidates_from_search(data)
        if not cands:
            xbmcgui.Dialog().notification('Misaka 弹幕', '没有搜索结果', xbmcgui.NOTIFICATION_INFO, 3000, False)
            return
        self._fill(cands)
        self.setFocusId(ID_LIST)

    def onClick(self, cid):
        if cid == ID_LIST:
            pos = self.getControl(ID_LIST).getSelectedPosition()
            if 0 <= pos < len(self.cands):
                c = self.cands[pos]
                send({'action': 'load', 'episodeId': c['episodeId'], 'label': c['label'], 'anime': c.get('anime', '')})
                HOME.setProperty('misaka.candidates', '')
                self.close()
        elif cid == BTN_SEARCH:
            self._search()
        elif cid == BTN_TOGGLE:
            send({'action': 'toggle'})
        elif cid == BTN_MODE:
            send({'action': 'mode'})
        elif cid == BTN_SUB:
            start = ''
            try:
                f = xbmc.Player().getPlayingFile()
                if '://' not in f:  # 本地文件才从它所在目录开始找
                    start = f.rsplit('/', 1)[0] + '/'
            except RuntimeError:
                pass
            path = xbmcgui.Dialog().browse(1, '选择外挂字幕 (.srt / .ass)', 'files', '.srt|.ass', False, False, start)
            if path:
                send({'action': 'subtitle', 'path': path})
        elif cid == BTN_MINUS:
            send({'action': 'shift', 'ms': -500})
        elif cid == BTN_PLUS:
            send({'action': 'shift', 'ms': 500})
        elif cid == BTN_SETTINGS:
            xbmc.executebuiltin('Addon.OpenSettings(%s)' % ADDON_ID)
        elif cid == BTN_CLOSE:
            self.close()
        xbmc.sleep(300)
        self._refresh()

    def onAction(self, action):
        if action.getId() in (xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK):
            self.close()
