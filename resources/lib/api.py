"""misaka_danmu_server 客户端 (弹弹play API v1 兼容接口, 路径: /api/v1/{token}/...)"""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


class ApiError(Exception):
    pass


def _ssl_hint(e):
    return ('HTTPS 证书验证失败(%s)。可尝试: 1) 在设置中关闭"验证 HTTPS 证书"; '
            '2) 改用 http:// 内网地址; 3) 检查设备日期时间是否正确' % (getattr(e, 'reason', None) or e))


class MisakaApi(object):
    def __init__(self, base, token, verify_ssl=True):
        self.verify_ssl = verify_ssl
        self.base = (base or '').strip().rstrip('/')
        for tail in ('/api/v1', '/api'):  # 常见误填: 把接口路径也粘贴进来了
            if self.base.lower().endswith(tail):
                self.base = self.base[:-len(tail)].rstrip('/')
        self.token = (token or '').strip().strip('/')
        if not self.base:
            raise ApiError('未设置服务器地址')

    def _call(self, path, params=None, body=None, timeout=20):
        prefix = (urllib.parse.quote(self.token) if self.token else '')
        url = self.base + '/api/v1/%s/%s' % (prefix, path)
        if params:
            url += '?' + urllib.parse.urlencode(params)
        headers = {'Accept': 'application/json', 'User-Agent': 'Kodi-MisakaDanmaku/0.1'}
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST' if body is not None else 'GET')
        ctx = None
        if url.lower().startswith('https') and not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                body = r.read()
                status, ctype, final_url = r.status, r.headers.get('Content-Type', ''), r.geturl()
            try:
                result = json.loads(body.decode('utf-8-sig'))
            except ValueError:
                raise ApiError(self._not_json(url, final_url, status, ctype, body))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ApiError('Token 无效或无权限 (HTTP %s)' % e.code)
            if e.code == 404:
                raise ApiError('接口不存在, 请检查服务器地址和 Token (HTTP 404)')
            raise ApiError('服务器错误 HTTP %s' % e.code)
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise ApiError(_ssl_hint(e.reason))
            raise ApiError(str(e.reason))
        except ssl.SSLError as e:
            raise ApiError(_ssl_hint(e))
        except (ValueError, OSError) as e:
            raise ApiError(str(e))
        if isinstance(result, dict) and result.get('success') is False:
            raise ApiError(result.get('errorMessage') or '服务器返回失败')
        return result

    def _mask(self, url):
        return url.replace('/' + urllib.parse.quote(self.token) + '/', '/***/') if self.token else url

    def _not_json(self, url, final_url, status, ctype, body):
        snippet = body[:80].decode('utf-8', 'replace').replace('\n', ' ').replace('\r', ' ').strip()
        msg = '服务器返回的不是 JSON (HTTP %s, %s)\n请求: %s' % (status, ctype or '无类型', self._mask(url))
        if final_url != url:
            msg += '\n被重定向到: %s' % self._mask(final_url)
        msg += '\n内容开头: %s' % (snippet or '(空)')
        msg += '\n请检查: 地址和端口是否指向 misaka_danmu_server、Token 是否正确、反向代理是否放行 /api 路径'
        return msg

    def version(self):
        return self._call('version', timeout=8)

    def match(self, file_name):
        return self._call('match', body={
            'fileName': file_name, 'fileHash': '0' * 32, 'fileSize': 0,
            'videoDuration': 0, 'matchMode': 'fileNameOnly'})

    def search_episodes(self, anime, episode=None):
        params = {'anime': anime}
        if episode:
            params['episode'] = str(episode)
        return self._call('search/episodes', params=params)

    def comments(self, episode_id, with_related=True, retries=2):
        # 首次请求时服务端可能要现场抓取弹幕, 较慢, 超时后重试
        err = None
        for _ in range(retries + 1):
            try:
                return self._call('comment/%s' % episode_id,
                                  params={'withRelated': 'true' if with_related else 'false', 'chConvert': 0},
                                  timeout=90)
            except ApiError as e:
                err = e
                if 'HTTP 4' in str(e) or '无效' in str(e) or '不存在' in str(e):
                    break  # 4xx 重试没用
        raise err


def candidates_from_match(data):
    out = []
    for m in (data or {}).get('matches') or []:
        out.append({'episodeId': m.get('episodeId'), 'anime': m.get('animeTitle', ''),
                    'label': '%s - %s' % (m.get('animeTitle', ''), m.get('episodeTitle', ''))})
    return out


def candidates_from_search(data, limit=80):
    out = []
    for a in (data or {}).get('animes') or []:
        for e in a.get('episodes') or []:
            out.append({'episodeId': e.get('episodeId'), 'anime': a.get('animeTitle', ''),
                        'label': '%s - %s' % (a.get('animeTitle', ''), e.get('episodeTitle', ''))})
            if len(out) >= limit:
                return out
    return out
