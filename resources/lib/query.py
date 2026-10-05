"""搜索词构造: 清洗标题 / 季变体 / 放宽查询 / 按集数挑集"""
import re
from collections import OrderedDict

_BRACKETS = re.compile(r'\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）|\{[^}]*\}')
_TECH = re.compile(r'(?i)(?<![a-z0-9])(2160p|1080[pi]|720p|480p|4k|x26[45]|h\.?26[45]|hevc|avc|aac|ac3|flac|dts|web-?dl|web-?rip|bd-?rip|bluray|remux|hdr10?|10bit|8bit|chs|cht|big5|gb)(?![a-z0-9])')
_EP_TAIL = re.compile(r'(?i)(\bS\d{1,2}\s*E\d{1,3}\b|\bE[Pp]?\d{1,3}\b|\s-\s*\d{1,3}(v\d)?\b|第\s*\d+\s*[话話集]).*$')
_SEASON = re.compile(r'(?i)(第\s*[0-9一二三四五六七八九十]+\s*[季期部]|\bseason\s*\d+\b|\b\d+(st|nd|rd|th)\s+season\b|\bS\d{1,2}\b)')
_EXT = re.compile(r'(?i)\.(mkv|mp4|avi|ts|m2ts|rmvb|wmv|flv|mov|iso|strm)$')
_YEAR = re.compile(r'\s*(19\d{2}|20[0-2]\d)\s*$')
_CJK = re.compile(r'[\u4e00-\u9fff]')
_CN = '零一二三四五六七八九十'


def clean_title(s):
    s = _BRACKETS.sub(' ', s or '')
    s = _TECH.sub(' ', s)
    s = _EP_TAIL.sub('', s)
    s = _SEASON.sub(' ', s)
    s = _YEAR.sub('', s)
    if ' ' not in s.strip():
        s = re.sub(r'[._]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip(' -_.~')


def parse_filename(name):
    return clean_title(_EXT.sub('', name or ''))


def _season_variants(t, season):
    if season <= 1:
        return [t]
    cn = _CN[season] if season <= 10 else str(season)
    return [t + ' 第%s季' % cn, t + ' %d' % season, t + ' Season %d' % season, t]


def _shorten(t):
    out = []
    for sep in (':', '：', ' - ', '～', '~'):
        head = t.split(sep)[0].strip()
        if len(head) >= 2 and head != t:
            out.append(head)
    if _CJK.search(t) and len(t) > 8:
        out.append(t[:6])
    return out


def build_queries(show, original, title, filename, season):
    """-> [(query, exact)]; exact=False 为放宽查询, 结果必须让用户确认"""
    seen, strict, loose = set(), [], []

    def add(lst, q):
        k = q.lower()
        if len(q) >= 2 and k not in seen:
            seen.add(k)
            lst.append(q)

    for base in (show, original, title, parse_filename(filename)):
        t = clean_title(base)
        if not t:
            continue
        for v in _season_variants(t, season):
            add(strict, v)
        for v in _shorten(t):
            add(loose, v)
    return [(q, True) for q in strict] + [(q, False) for q in loose]


def _ep_patterns(ep):
    return [re.compile(r'第\s*0*%d\s*[话話集回]' % ep),
            re.compile(r'(?i)\bepisode\s*0*%d(?!\d)' % ep),
            re.compile(r'(?i)\bS\d{1,2}\s*E0*%d(?!\d)' % ep),
            re.compile(r'(?i)^\s*(?:EP?\.?\s*)?0*%d(?!\d)' % ep)]


def _is_ep(cand, pats):
    return any(p.search(cand.get('ep_title', '')) for p in pats)


def pick_episode(cands, ep):
    """按集数从 candidates_from_search 的结果里挑集, 每部番最多一集"""
    if not ep:
        return cands
    pats = _ep_patterns(ep)
    groups = OrderedDict()
    for c in cands:
        groups.setdefault(c.get('animeId') or c['anime'], []).append(c)
    out = []
    for g in groups.values():
        hit = [c for c in g if _is_ep(c, pats)]
        if hit:
            out.append(hit[0])
        elif len(g) == 1:       # 服务端已按集数过滤, 只剩一集
            out.append(g[0])
    return out


def find_episode_index(cands, ep):
    """手动搜索结果里, 当前正在播放的那一集的位置 (找不到返回 0)"""
    if not ep:
        return 0
    pats = _ep_patterns(ep)
    for i, c in enumerate(cands):
        if _is_ep(c, pats):
            return i
    return 0
