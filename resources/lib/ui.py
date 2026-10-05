"""自建 UI: 匹配选择 + 播放控制面板 (WindowXMLDialog)"""
import json
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcgui

from . import settings
from .api import ApiError, MisakaApi, candidates_from_search
from .query import build_queries, clean_title, find_episode_index

ADDON_ID = settings.ADDON_ID
HOME = xbmcgui.Window(10000)

ID_TITLE, ID_STATUS, ID_LIST = 10, 11, 100
BTN_SEARCH, BTN_TOGGLE, BTN_MODE, BTN_SUB, BTN_MINUS, BTN_PLUS, BTN_SETTINGS, BTN_CLOSE = \
    201, 202, 203, 204, 205, 206, 207, 208


def send(cmd):
    HOME.setProperty('misaka.cmd', json.dumps(cmd, ensure_ascii=False))
    xbmc.executebuiltin('NotifyAll(%s,cmd)' % ADDON_ID)


def _play_context():
    """当前播放的 (剧名/片名, 集数): 用来预填搜索框并定位到正在看的那一集"""
    show = title = ''
    ep = 0
    try:
        tag = xbmc.Player().getVideoInfoTag()
        show, title, ep = tag.getTVShowTitle(), tag.getTitle(), tag.getEpisode()
    except Exception:
        pass
    name = HOME.getProperty('misaka.suggest') or show or title
    return clean_title(name) or name, (ep if ep and ep > 0 else None)


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

    def _fill(self, cands, select=0):
        self.cands = cands
        lst = self.getControl(ID_LIST)
        lst.reset()
        lst.addItems([xbmcgui.ListItem(c['label']) for c in cands])
        if cands and 0 < select < len(cands):
            lst.selectItem(select)

    def _refresh(self):
        eps = HOME.getProperty('misaka.episode')
        mode = 'ASS 外挂字幕' if HOME.getProperty('misaka.mode') == '1' else '弹幕层'
        on = '开' if HOME.getProperty('misaka.enabled') != '0' else '关'
        self.getControl(ID_TITLE).setLabel(eps or '尚未加载弹幕')
        self.getControl(ID_STATUS).setLabel('%s | 弹幕 %s 条 | 方式: %s | 开关: %s' % (
            HOME.getProperty('misaka.status') or '-', HOME.getProperty('misaka.count') or '0', mode, on))

    def _search(self):
        default, ep = _play_context()
        kb = xbmc.Keyboard(default, '搜索番剧名')
        kb.doModal()
        text = kb.getText().strip() if kb.isConfirmed() else ''
        if not text:
            return
        cfg = settings.load()
        try:
            api = MisakaApi(cfg['server_url'], cfg['token'], cfg['verify_ssl'])
        except ApiError as e:
            xbmcgui.Dialog().ok('Misaka 弹幕', '搜索失败: %s' % e)
            return
        # 输入原文优先, 其次是清洗/放宽后的写法 (去掉季数、年份、副标题等)
        queries = [text]
        for q, _ in build_queries(text, '', '', '', 1):
            if q.lower() not in [x.lower() for x in queries]:
                queries.append(q)
        queries = queries[:4]
        xbmcgui.Dialog().notification('Misaka 弹幕', '搜索中…', xbmcgui.NOTIFICATION_INFO, 2000, False)

        def run(q):
            try:
                return candidates_from_search(api.search_episodes(q, timeout=15, tries=2), 400)
            except ApiError as e:
                return e

        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(ex.map(run, queries))
        errs = [r for r in results if isinstance(r, ApiError)]
        if errs and len(errs) == len(results):
            xbmcgui.Dialog().ok('Misaka 弹幕', '搜索失败: %s' % errs[0])
            return
        for q, cands in zip(queries, results):
            if cands and not isinstance(cands, ApiError):
                self._fill(cands, find_episode_index(cands, ep))
                self.setFocusId(ID_LIST)
                if q != text:
                    xbmcgui.Dialog().notification('Misaka 弹幕', '已改用「%s」搜索' % q,
                                                  xbmcgui.NOTIFICATION_INFO, 3000, False)
                return
        xbmcgui.Dialog().ok('Misaka 弹幕', '没有搜索结果\n已尝试: %s\n可换原名/别名/更短的关键词再试' % ' / '.join(queries))

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
