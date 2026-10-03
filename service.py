import json
import os
import threading
import time
import traceback
import urllib.parse

import xbmc
import xbmcgui
import xbmcvfs

from resources.lib import danmaku as dm
from resources.lib import settings
from resources.lib.api import (ApiError, MisakaApi, candidates_from_match,
                               candidates_from_search)
from resources.lib.overlay import Overlay

ADDON_ID = settings.ADDON_ID
HOME = xbmcgui.Window(10000)
TMP = xbmcvfs.translatePath('special://temp/misaka_danmaku/')
DATA = xbmcvfs.translatePath('special://profile/addon_data/%s/' % ADDON_ID)
MAP_FILE = os.path.join(DATA, 'mapping.json')
CACHE_TTL = 12 * 3600


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[misaka.danmaku] %s' % msg, level)


def notify(msg, ms=4000):
    xbmcgui.Dialog().notification('Misaka 弹幕', msg, xbmcgui.NOTIFICATION_INFO, ms, False)


def cleanup_tmp(max_age=7 * 86400):
    try:
        for f in os.listdir(TMP):
            p = os.path.join(TMP, f)
            if os.path.isfile(p) and time.time() - os.path.getmtime(p) > max_age:
                os.remove(p)
    except OSError:
        pass


def _load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    try:
        xbmcvfs.mkdirs(os.path.dirname(path) + os.sep)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError as e:
        log('save %s failed: %s' % (path, e), xbmc.LOGWARNING)


class Controller(object):
    def __init__(self, player):
        self.player = player
        self.overlay = Overlay(player)
        self.cfg = settings.load()
        self.items = []
        self.episode_id = None
        self.title = ''
        self.ext_sub = None
        self.show_key = None      # 当前剧集的记忆键 (剧名|季)
        self.gen = 0              # 播放会话序号, 防止过期线程写入
        self._ass_active = False
        self._ass_seq = 0
        self._timer = None
        self._tlock = threading.Lock()

    # ---------- 状态 (供面板读取) ----------
    def status(self, text=None):
        if text is not None:
            HOME.setProperty('misaka.status', text)
        HOME.setProperty('misaka.episode', self.title or '')
        HOME.setProperty('misaka.count', str(len(self.items)))
        HOME.setProperty('misaka.mode', str(self.cfg['render_mode']))
        HOME.setProperty('misaka.enabled', '1' if self.cfg['enabled'] else '0')

    def refresh_cfg(self):
        self.cfg = settings.load()
        self.overlay.update_cfg(self.cfg)

    def api(self):
        return MisakaApi(self.cfg['server_url'], self.cfg['token'], self.cfg['verify_ssl'])

    def schedule_apply(self, delay=0.8):
        """防抖: 设置连续变化/命令连发时只应用一次"""
        with self._tlock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(delay, self._apply_safe)
            self._timer.daemon = True
            self._timer.start()

    def _apply_safe(self):
        try:
            self.apply()
            self.status()
        except Exception as e:
            log('apply error: %r' % e, xbmc.LOGERROR)

    # ---------- 播放事件 ----------
    def on_start(self):
        self.gen += 1
        self.items, self.episode_id, self.title, self.ext_sub = [], None, '', None
        self._ass_active = False
        self.overlay.stop()
        HOME.setProperty('misaka.candidates', '')
        self.refresh_cfg()
        self.status('等待匹配')
        if not self.cfg['auto_match']:
            return
        try:
            f = self.player.getPlayingFile()
            total = self.player.getTotalTime()
        except RuntimeError:
            return
        if f.startswith('pvr://') or (total and total < self.cfg['min_minutes'] * 60):
            self.status('已跳过(直播或时长过短)')
            return
        threading.Thread(target=self._auto, args=(self.gen,), daemon=True).start()

    def on_stop(self):
        self.gen += 1
        self.overlay.stop()
        self.items, self.episode_id, self.title = [], None, ''
        self._ass_active = False
        self.status('')

    # ---------- 匹配 ----------
    def _names(self):
        show = title = ''
        season = ep = year = 0
        try:
            tag = self.player.getVideoInfoTag()
            show, title = tag.getTVShowTitle(), tag.getTitle()
            season, ep, year = tag.getSeason(), tag.getEpisode(), tag.getYear()
        except Exception:
            pass
        try:
            f = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(self.player.getPlayingFile()).path))
        except Exception:
            f = ''
        names = []
        if show and ep > 0:
            names.append('%s S%02dE%02d' % (show, season if season > 0 else 1, ep))
        elif title:
            names.append('%s (%s)' % (title, year) if year else title)
        if f:
            names.append(f)
        self.show_key = ('%s|%s' % (show.lower(), season)) if show else None
        return names, (show or title), (ep if ep > 0 else None)

    def _remembered(self, api, ep):
        """上次手动选过这部剧 -> 直接用同一部番的对应集"""
        anime = _load_json(MAP_FILE, {}).get(self.show_key or '')
        if not anime or not ep:
            return None
        cands = [c for c in candidates_from_search(api.search_episodes(anime, ep)) if c['anime'] == anime]
        return cands[0] if len(cands) == 1 else None

    def _remember(self, anime):
        if self.show_key and anime:
            m = _load_json(MAP_FILE, {})
            m[self.show_key] = anime
            _save_json(MAP_FILE, m)

    def _auto(self, gen):
        xbmc.sleep(1500)
        if gen != self.gen:
            return
        try:
            api = self.api()
            names, query, ep = self._names()
            hit = self._remembered(api, ep)
            if hit:
                self.load_episode(hit['episodeId'], hit['label'], gen)
                return
            cands = []
            for n in names:
                data = api.match(n)
                if gen != self.gen:
                    return
                cands = candidates_from_match(data)
                if data.get('isMatched') and cands:
                    self.load_episode(cands[0]['episodeId'], cands[0]['label'], gen)
                    return
                if cands:
                    break
            if not cands and query:
                cands = candidates_from_search(api.search_episodes(query, ep))
            if gen != self.gen:
                return
            if not cands:
                self.status('未匹配到弹幕')
                notify('未匹配到弹幕, 可按快捷键打开面板手动搜索')
            elif len(cands) == 1 or not self.cfg['show_picker']:
                self.load_episode(cands[0]['episodeId'], cands[0]['label'], gen)
            else:
                HOME.setProperty('misaka.candidates', json.dumps(cands, ensure_ascii=False))
                self.status('请选择匹配结果')
                xbmc.executebuiltin('RunScript(%s,panel)' % ADDON_ID)
        except ApiError as e:
            self.status('出错: %s' % e)
            notify('弹幕服务器: %s' % e)
        except Exception as e:  # 不能让线程静默死掉
            log('auto error: %r' % e, xbmc.LOGERROR)
            self.status('出错: %s' % e)

    # ---------- 加载 ----------
    def _fetch(self, episode_id):
        xbmcvfs.mkdirs(TMP)
        path = os.path.join(TMP, 'c%s_%d.json' % (episode_id, int(self.cfg['with_related'])))
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
            raw = _load_json(path, None)
            if raw:
                return raw
        raw = self.api().comments(episode_id, self.cfg['with_related'])
        _save_json(path, raw)
        return raw

    def load_episode(self, episode_id, label, gen=None):
        gen = self.gen if gen is None else gen
        self.status('正在获取弹幕(首次可能较慢)…')
        try:
            raw = self._fetch(episode_id)
        except ApiError as e:
            self.status('出错: %s' % e)
            notify('获取弹幕失败: %s' % e)
            return
        if gen != self.gen:
            return
        c = self.cfg
        self.items = dm.parse(raw, c['blocklist'], c['show_top'], c['show_bottom'], c['dedupe'])
        self.episode_id, self.title = episode_id, label
        self.apply()
        self.status('已加载')
        notify('已加载 %d 条弹幕: %s' % (len(self.items), label))

    def _opts(self):
        c = self.cfg
        return {'font_px': c['font_px'], 'opacity': c['opacity'], 'area': c['area'],
                'scroll_seconds': c['scroll_seconds'], 'offset': c['offset']}

    def _leave_ass(self):
        """离开 ASS 模式: 恢复外挂字幕, 没有就关闭字幕 (弹幕 ASS 无法单独卸载)"""
        if not self._ass_active:
            return
        self._ass_active = False
        if self.ext_sub:
            self.player.setSubtitles(self.ext_sub)
        else:
            self.player.showSubtitles(False)

    def apply(self):
        """按渲染方式生效: 0=弹幕层, 1=ASS 外挂字幕"""
        if not self.player.isPlaying():
            return
        cfg = self.cfg
        if cfg['render_mode'] == 0:
            self.overlay.load(self.items, cfg)
            self.overlay.start()
            self._leave_ass()
            return
        self.overlay.stop()
        if not cfg['enabled'] or not self.items:
            self._leave_ass()
            return
        xbmcvfs.mkdirs(TMP)
        opts = self._opts()
        self._ass_seq += 1  # 文件名每次不同, 避免 Kodi 缓存旧字幕
        path = None
        if self.ext_sub:
            out = os.path.join(TMP, '%s.%d.merged.ass' % (self.episode_id, self._ass_seq))
            path = dm.merge_subtitle(self.ext_sub, self.items, opts, out)
            if not path:
                notify('无法合并该字幕(仅支持 .srt/.ass), 将只加载弹幕')
        if not path:
            path = os.path.join(TMP, '%s.%d.ass' % (self.episode_id, self._ass_seq))
            with open(path, 'w', encoding='utf-8-sig') as f:
                f.write(dm.build_ass(self.items, opts))
        self.player.setSubtitles(path)
        self.player.showSubtitles(True)
        self._ass_active = True

    def set_external_subtitle(self, path):
        self.ext_sub = path
        if self.cfg['render_mode'] == 1 and self.items and self.cfg['enabled']:
            self.apply()
        else:
            self.player.setSubtitles(path)
            self.player.showSubtitles(True)
            self._ass_active = False
        notify('已加载外挂字幕')

    # ---------- 面板命令 ----------
    def command(self, cmd):
        act = cmd.get('action')
        self.refresh_cfg()
        if not self.player.isPlaying():
            notify('当前没有在播放视频')
            return
        if act == 'load':
            if not self.show_key:
                self._names()
            self._remember(cmd.get('anime'))
            threading.Thread(target=self.load_episode, args=(cmd['episodeId'], cmd.get('label', ''))).start()
            return
        if act == 'subtitle':
            self.set_external_subtitle(cmd['path'])
        elif act == 'toggle':
            settings.set_bool('enabled', not self.cfg['enabled'])
            self.refresh_cfg()
            self.schedule_apply(0.2)
        elif act == 'mode':
            settings.set_int('render_mode', 1 - self.cfg['render_mode'])
            self.refresh_cfg()
            self.schedule_apply(0.2)
        elif act == 'shift':
            value = max(-30000, min(30000, int(round(self.cfg['offset'] * 1000)) + cmd['ms']))
            settings.set_int('offset_ms', value)
            self.refresh_cfg()
            if self.cfg['render_mode'] == 1:
                self.schedule_apply()
        self.status()


class MonitorImpl(xbmc.Monitor):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl

    def onSettingsChanged(self):
        self.ctl.refresh_cfg()
        if self.ctl.items:
            self.ctl.schedule_apply()

    def onNotification(self, sender, method, data):
        if sender != ADDON_ID or not method.endswith('cmd'):
            return
        try:
            self.ctl.command(json.loads(HOME.getProperty('misaka.cmd')))
        except Exception as e:
            log('cmd error: %r' % e, xbmc.LOGERROR)


class PlayerImpl(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.ctl = Controller(self)

    def onAVStarted(self):
        if self.isPlayingVideo():
            self.ctl.on_start()

    def onPlayBackStopped(self):
        self.ctl.on_stop()

    def onPlayBackEnded(self):
        self.ctl.on_stop()

    def onPlayBackError(self):
        self.ctl.on_stop()

    def onPlayBackPaused(self):
        self.ctl.overlay.pause()

    def onPlayBackResumed(self):
        self.ctl.overlay.resume()

    def onPlayBackSpeedChanged(self, speed):
        # 快进/快退时弹幕没有意义, 暂时隐藏; 回到 1x 后补回
        if speed != 1:
            self.ctl.overlay.pause()
        else:
            self.ctl.overlay.resume()

    def onPlayBackSeek(self, time, offset):
        self.ctl.overlay.seek()

    def onPlayBackSeekChapter(self, chapter):
        self.ctl.overlay.seek()


if __name__ == '__main__':
    try:
        cleanup_tmp()
        player = PlayerImpl()
        monitor = MonitorImpl(player.ctl)
        log('service started')
        monitor.waitForAbort()
        player.ctl.overlay.stop()
        log('service stopped')
    except Exception:
        log('service crashed:\n' + traceback.format_exc(), xbmc.LOGERROR)
        raise
