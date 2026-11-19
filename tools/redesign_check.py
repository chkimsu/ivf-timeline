#!/usr/bin/env python3
"""디자인 개편 확인 도구.

개편한 index.html 을 main 의 index.html(기준선)과 견주어 글, 날짜, 링크가 그대로인지 보고
화면 폭, 메뉴, 모양을 검사한다. 크롬을 화면 없이 띄워 file:// 로 연다.

  python3 tools/redesign_check.py                 등록된 검사를 다 돌린다
  python3 tools/redesign_check.py content dates   고른 검사만 돌린다
  python3 tools/redesign_check.py content --page .superpowers/mut.html   다른 파일을 본다

검사마다 PASS 나 FAIL 을 찍고, 실패가 하나라도 있으면 종료 코드 1 로 끝난다.
"""
import argparse
import pathlib
import re
import subprocess
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = str(ROOT / 'index.html')

FIXED = "2026-11-20T09:00:00+09:00"   # 이때 주수는 6주 0일, 다음 일정까지 10일
PANELS = ['timeline', 'leave', 'risk', 'why', 'weekly', 'meds', 'support', 'care', 'helper', 'album', 'hospital']
SCREENS = ['top'] + PANELS
SIZES = {'phone': (390, 844), 'tablet': (1024, 768), 'pc': (1440, 900)}

# 비교하기 전에 기준선 글에 적용하는 정규식 치환. 메뉴를 가리키는 문구를 고칠 때(Task 8) 채운다.
ALLOWED = []

CHECKS = {}


def check(name):
    def deco(fn):
        CHECKS[name] = fn
        return fn
    return deco


class Session:
    """브라우저 하나를 띄워 두고 검사들이 나눠 쓴다."""

    def __init__(self, playwright):
        self.browser = playwright.chromium.launch()
        self.contexts = []

    def close_pages(self):
        for c in self.contexts:
            c.close()
        self.contexts = []


def open_page(pw, path, size='pc', scheme='light', when=FIXED, hash='', init=None):
    w, h = SIZES[size] if isinstance(size, str) else size
    ctx = pw.browser.new_context(viewport={'width': w, 'height': h}, timezone_id='Asia/Seoul',
                                 locale='ko-KR', color_scheme=scheme)
    pw.contexts.append(ctx)
    if init:
        ctx.add_init_script(init)   # 페이지 스크립트보다 먼저 돈다
    page = ctx.new_page()
    page.rd_errors = []
    page.rd_requests = []
    page.on('console', lambda m: page.rd_errors.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda e: page.rd_errors.append(str(e)))
    page.on('request', lambda r: page.rd_requests.append(r.url))
    page.clock.set_fixed_time(when)
    page.goto(pathlib.Path(path).resolve().as_uri() + ('#' + hash if hash else ''))
    page.wait_for_timeout(120)
    return page


def go(page, pid):
    """주소의 # 를 바꿔 그 화면을 연다."""
    page.evaluate("id => { location.hash = '#' + id; }", pid)
    page.wait_for_timeout(80)


def baseline_path():
    d = ROOT / '.superpowers' / 'baseline'
    d.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(['git', 'show', 'main:index.html'], cwd=ROOT, capture_output=True, check=True).stdout
    (d / 'index.html').write_bytes(out)
    return str(d / 'index.html')


def norm(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def tight(s):
    return re.sub(r'\s+', '', s or '')


def allowed(s):
    for pat, rep in ALLOWED:
        s = re.sub(pat, rep, s)
    return s


def first_diff(a, b):
    n = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return '기준선 「…%s」 / 지금 「…%s」' % (a[max(0, n - 20):n + 30], b[max(0, n - 20):n + 30])


JS_TEXT = "id => { const e = document.getElementById(id); return e ? e.textContent : null; }"
JS_SEL = "s => { const e = document.querySelector(s); return e ? e.textContent : null; }"
JS_LIST = """() => [...document.querySelectorAll('#gl-list li')].map(li => {
  const t = s => { const e = li.querySelector(s); return e ? e.textContent : ''; };
  return {cls: li.className, all: li.textContent, date: t('.gl-date'), what: t('.gl-what'), where: t('.gl-where')};
})"""


JS_TEXT_NOSVG = """id => {
  const e = document.getElementById(id); if (!e) return null;
  const c = e.cloneNode(true); c.querySelectorAll('svg').forEach(x => x.remove()); return c.textContent;
}"""


def panel_texts(page, svg=True):
    out = {}
    for pid in PANELS:
        go(page, pid)   # 숨은 화면의 그림은 열릴 때 그려진다
        out[pid] = page.evaluate(JS_TEXT if svg else JS_TEXT_NOSVG, pid)
    go(page, 'top')
    return out


@check('content')
def content(pw, path):
    fails = []
    # 그림(SVG)은 놓인 폭이 넓으면 긴 이름을, 좁으면 짧은 이름을 쓴다. 개편하면 PC 에서 본문 폭이 달라지므로
    # 그림까지 넣은 비교는 그림 폭이 최솟값으로 같아지는 휴대폰 폭에서 하고, PC 폭에서는 그림을 빼고 비교한다.
    for size, svg in (('phone', True), ('pc', False)):
        bt = panel_texts(open_page(pw, baseline_path(), size=size), svg)
        nt = panel_texts(open_page(pw, path, size=size), svg)
        for pid in PANELS:
            if nt[pid] is None:
                fails.append('%s: 패널이 없다' % pid)
                continue
            a, b = allowed(norm(bt[pid])), norm(nt[pid])
            if a != b:
                fails.append('%s (%s): 글이 다르다. %s' % (pid, size, first_diff(a, b)))
    base = open_page(pw, baseline_path())
    new = open_page(pw, path)
    # 홈 (가) 할 일 표
    a, b = allowed(norm(base.evaluate(JS_TEXT, 'todo'))), norm(new.evaluate(JS_TEXT, 'todo'))
    if a != b:
        fails.append('top: 「할 일과 정할 일」 글이 다르다. %s' % first_diff(a, b))
    # (나) 소개 문단
    for sel in ('#top > p.sub', '#top > p.meta', '#top .gl-foot'):
        a, b = allowed(norm(base.evaluate(JS_SEL, sel))), norm(new.evaluate(JS_SEL, sel))
        if a != b:
            fails.append('top: %s 글이 다르다. %s' % (sel, first_diff(a, b)))
    # (다) 큰 일정 목록
    bl, nl = base.evaluate(JS_LIST), new.evaluate(JS_LIST)
    if len(bl) != len(nl):
        fails.append('top: 일정 줄 수가 다르다. 기준선 %d, 지금 %d' % (len(bl), len(nl)))
    for i, (x, y) in enumerate(zip(bl, nl)):
        for k in ('date', 'what', 'where'):
            if tight(x[k]) not in tight(y['all']):
                fails.append('top: 일정 %d번째 줄에 「%s」가 없다' % (i + 1, norm(x[k])))
        kx = sorted(set(x['cls'].split()) & {'done', 'next'})
        ky = sorted(set(y['cls'].split()) & {'done', 'next'})
        if kx != ky:
            fails.append('top: 일정 %d번째 줄 표시가 다르다. 기준선 %s, 지금 %s' % (i + 1, kx, ky))
    # (라) 오늘 주수와 다음 일정
    a, b = norm(base.evaluate(JS_TEXT, 'gl-ga')), norm(new.evaluate(JS_TEXT, 'gl-ga'))
    if a != b:
        fails.append('top: 오늘 주수가 다르다. 기준선 「%s」, 지금 「%s」' % (a, b))
    nxt = base.evaluate(JS_TEXT, 'gl-next') or ''
    now = tight(new.evaluate(JS_SEL, '#h-next, #gl-next'))
    for word in [w for w in re.split(r'[\s,]+', nxt.replace('다음:', '')) if w]:
        if word not in now:
            fails.append('top: 다음 일정에 「%s」가 없다' % word)
    return fails


@check('dates')
def dates(pw, path):
    js = "() => [...document.querySelectorAll('.dd')].map(e => [e.getAttribute('data-k'), e.textContent])"
    a = sorted(map(tuple, open_page(pw, baseline_path()).evaluate(js)))
    b = sorted(map(tuple, open_page(pw, path).evaluate(js)))
    fails = ['빈 날짜 칸: %s' % k for k, v in b if not v.strip()]
    if a != b:
        only_a = [x for x in a if x not in b][:5]
        only_b = [x for x in b if x not in a][:5]
        fails.append('날짜 칸이 다르다. 기준선에만 %s / 지금에만 %s' % (only_a, only_b))
    return fails


JS_TARGET = """id => {
  const t = document.getElementById(id);
  if (!t) return {missing: true};
  const p = t.closest('.tabp') || t;
  return {hidden: !!p.hidden, h: t.getBoundingClientRect().height, top: t.getBoundingClientRect().top};
}"""


# 화면 위에 붙어 있는 것(PC 의 메뉴 띠, 보일 때의 항목 줄)의 아래 끝. 없으면 0
JS_STICK = """() => {
  let line = 0;
  for (const s of ['.nav', '.subbar']) {
    const e = document.querySelector(s); if (!e || e.hidden) continue;
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || (cs.position !== 'sticky' && cs.position !== 'fixed')) continue;
    const r = e.getBoundingClientRect();
    if (r.height > 0 && r.top <= 1) line = Math.max(line, r.bottom);
  }
  return line;
}"""


@check('links')
def links(pw, path):
    fails = []
    page = open_page(pw, path)
    hrefs = page.evaluate("() => [...document.querySelectorAll('a[href^=\"#\"]')].map(a => a.getAttribute('href'))")
    for i, href in enumerate(hrefs):
        page.evaluate("i => document.querySelectorAll('a[href^=\"#\"]')[i].click()", i)
        page.wait_for_timeout(60)
        r = page.evaluate(JS_TARGET, href[1:])
        if r.get('missing'):
            fails.append('링크 %s: 가리키는 자리가 없다' % href)
        elif r['hidden'] or r['h'] <= 0:
            fails.append('링크 %s: 눌러도 가리키는 자리가 안 보인다' % href)
        elif r['top'] < page.evaluate(JS_STICK) - 1:
            fails.append('링크 %s: 가리키는 자리가 위에 붙은 메뉴에 가려진다 (top %.0f)' % (href, r['top']))
    for pid in SCREENS + ['todo', 'cert-todo', 'pgt-screen']:
        p = open_page(pw, path, hash=pid)
        r = p.evaluate(JS_TARGET, pid)
        if r.get('missing') or r['hidden'] or r['h'] <= 0:
            fails.append('주소 #%s: 바로 열었을 때 그 자리가 안 보인다' % pid)
    return fails


@check('console')
def console(pw, path):
    fails = []
    for pid in SCREENS:
        p = open_page(pw, path, hash=pid)
        p.wait_for_timeout(150)
        fails += ['#%s: %s' % (pid, e[:160]) for e in p.rd_errors]
    return fails


@check('external')
def external(pw, path):
    fails = []
    page = open_page(pw, path)
    for pid in PANELS:
        go(page, pid)
    page.wait_for_timeout(300)
    fails += ['바깥 요청: %s' % u[:120] for u in page.rd_requests if not u.startswith(('file:', 'data:', 'blob:'))]
    src = pathlib.Path(path).read_text(encoding='utf-8')
    for pat in (r'src="https?://', r'<link[^>]+href="https?://', r'@import', r'url\(\s*["\']?https?://'):
        if re.search(pat, src):
            fails.append('파일 안에 바깥 주소를 불러오는 곳이 있다: %s' % pat)
    return fails


def overflow_sizes():
    return dict(SIZES)


@check('overflow')
def overflow(pw, path):
    fails = []
    for name, size in overflow_sizes().items():
        for scheme in ('light', 'dark'):
            page = open_page(pw, path, size=size, scheme=scheme)
            for pid in SCREENS:
                go(page, pid)
                d = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if d > 0:
                    fails.append('%s %s #%s: 가로로 %dpx 밀린다' % (name, scheme, pid, d))
    return fails


JS_NARROW = """() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  document.querySelectorAll('details').forEach(d => { d.open = true; });
  const narrow = [], tightL = [];
  document.querySelectorAll('p, li, dd, td').forEach(e => {
    if (!vis(e)) return;
    const cs = getComputedStyle(e), fs = parseFloat(cs.fontSize);
    const w = e.getBoundingClientRect().width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const t = e.innerText.trim();
    if (w > 0 && t.length >= 80 && w / fs < 20) narrow.push(t.slice(0, 30));
  });
  document.querySelectorAll('ol, ul').forEach(l => {
    const it = [...l.children].filter(c => c.tagName === 'LI' && vis(c));
    for (let i = 1; i < it.length; i++) {
      const a = it[i - 1].getBoundingClientRect(), b = it[i].getBoundingClientRect();
      const lh = parseFloat(getComputedStyle(it[i - 1]).lineHeight) || 20;
      if (b.top <= a.top + 1) continue;
      if (a.height > lh * 1.6 && b.top - a.bottom < 6) tightL.push(it[i].innerText.trim().slice(0, 30));
    }
  });
  return {narrow, tight: tightL};
}"""


@check('narrow')
def narrow(pw, path):
    fails = []
    for name in ('tablet', 'pc'):
        page = open_page(pw, path, size=name)
        for pid in SCREENS:
            go(page, pid)
            r = page.evaluate(JS_NARROW)
            if r['narrow']:
                fails.append('%s #%s: 좁은 칸 %d곳. 예 「%s」' % (name, pid, len(r['narrow']), r['narrow'][0]))
            if r['tight']:
                fails.append('%s #%s: 붙은 목록 %d곳. 예 「%s」' % (name, pid, len(r['tight']), r['tight'][0]))
    return fails


# ---------- 색 ----------
PAIRS = [('ink', 'ground'), ('ink', 'card'), ('ink', 'sunk'), ('ink2', 'ground'), ('ink2', 'card'),
         ('ink3', 'ground'), ('ink3', 'card'), ('ink3', 'sunk'), ('a', 'ground'), ('a', 'card'), ('a', 'a-bg'),
         ('a', 'a-tint'), ('a', 'sunk'), ('b', 'card'), ('b', 'b-bg'), ('b', 'ground'), ('c', 'card'), ('c', 'c-bg'),
         ('g', 'g-bg'), ('g', 'card'), ('card', 'g'), ('y', 'y-bg'), ('y', 'card'), ('card', 'y'), ('card', 'ink'),
         ('ink', 'a-bg'), ('ink', 'g-bg'), ('ink', 'y-bg'), ('ink2', 'a-bg'), ('ink2', 'g-bg'), ('ink2', 'y-bg')]

# 개편 전 색. 파일에 남으면 안 된다. 주의색 #8A2846 은 새 색에서도 쓰므로 뺐다.
OLD = ['#E8EAED', '#F3F4F6', '#16181C', '#494D55', '#6C7079', '#DFE1E5', '#EDEEF1', '#1F5FBF', '#EAF1FC', '#B9D0F0',
       '#FBEBF0', '#5C6068', '#F1F2F4', '#D3D6DB', '#767A82', '#111316', '#1B1D21', '#232529', '#F2F3F5', '#C9CCD2',
       '#989DA6', '#32353B', '#26282D', '#7FAAEA', '#16222F', '#2C4360', '#E08AA6', '#2B141C', '#542634', '#A3A8B0',
       '#242629', '#3B3E44', '#9AA0A8']


def lum(h):
    h = h.lstrip('#')
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def token_block(src, head):
    """head 로 시작하는 규칙의 --이름:#색 을 읽는다."""
    i = src.find(head)
    if i < 0:
        return None
    body = src[src.find('{', i) + 1:src.find('}', i)]
    return {k: v.upper() for k, v in re.findall(r'--([a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\b', body)}


@check('contrast')
def contrast(pw, path):
    fails = []
    src = pathlib.Path(path).read_text(encoding='utf-8')
    for label, head in (('밝게', ':root, :root[data-theme="light"]'), ('어둡게', ':root[data-theme="dark"]')):
        tk = token_block(src, head)
        if tk is None:
            fails.append('%s 변수 블록을 못 찾았다' % label)
            continue
        for f, bg in PAIRS:
            if f not in tk or bg not in tk:
                fails.append('%s: 변수 --%s 나 --%s 가 없다' % (label, f, bg))
                continue
            r = ratio(tk[f], tk[bg])
            if r < 4.5:
                fails.append('%s: --%s / --%s 대비 %.2f' % (label, f, bg, r))
    return fails


@check('legacy_colors')
def legacy_colors(pw, path):
    fails = []
    here = pathlib.Path(path).resolve().parent
    for f in (pathlib.Path(path), here / 'manifest.json', here / 'icon.svg'):
        if not f.exists():
            continue
        up = f.read_text(encoding='utf-8').upper()
        left = [h for h in OLD if h in up]
        if left:
            fails.append('%s 에 옛 색 %d종이 남았다: %s' % (f.name, len(left), ' '.join(left[:8])))
    return fails


JS_BG = "() => getComputedStyle(document.body).backgroundColor"


@check('theme')
def theme(pw, path):
    fails = []
    p = open_page(pw, path, scheme='dark')
    if p.evaluate(JS_BG) != 'rgb(29, 26, 23)':
        fails.append('기기가 어둡게이고 고른 값이 없을 때 배경이 %s' % p.evaluate(JS_BG))
    p = open_page(pw, path, scheme='dark', init="try{localStorage.setItem('ivf-theme','light')}catch(e){}")
    if p.evaluate(JS_BG) != 'rgb(255, 249, 243)':
        fails.append('기기가 어둡게인데 밝게를 골랐을 때 배경이 %s' % p.evaluate(JS_BG))
    p = open_page(pw, path, scheme='light')
    btn = p.locator('[data-theme-set="dark"]:visible').first
    btn.click()
    p.wait_for_timeout(60)
    if p.evaluate(JS_BG) != 'rgb(29, 26, 23)':
        fails.append('어둡게 단추를 눌렀을 때 배경이 %s' % p.evaluate(JS_BG))
    if p.evaluate("() => localStorage.getItem('ivf-theme')") != 'dark':
        fails.append('어둡게를 고른 것이 저장되지 않았다')
    if btn.get_attribute('aria-pressed') != 'true':
        fails.append('어둡게 단추가 눌린 표시가 아니다')
    return fails


# ---------- 메뉴 ----------
NAV = ['홈', '일정', '임신 중', '출산 준비', '사진']
SUBS = {'plan': ['전체 일정', '휴가 계획'],
        'preg': ['유산 위험', 'PGT-A 배아', '시기별 할 일', '약 안내'],
        'prep': ['지원금과 보험', '조리원과 어린이집', '산후도우미', '분만 병원']}
FIRST = {'home': 'top', 'plan': 'timeline', 'preg': 'risk', 'prep': 'support', 'photo': 'album'}

JS_VISP = "() => [...document.querySelectorAll('.tabp')].filter(p => !p.hidden).map(p => p.id)"
JS_VSUB = """() => [...document.querySelectorAll('.sub-tabs')].filter(e => !e.hidden && e.getClientRects().length)
  .map(e => [...e.querySelectorAll('.sub-tab')].map(b => b.textContent.trim()))"""
JS_SEL_ON = "id => { const e = document.getElementById(id); return !!e && e.getAttribute('aria-selected') === 'true'; }"


@check('nav')
def nav(pw, path):
    fails = []
    page = open_page(pw, path)
    names = page.evaluate("() => [...document.querySelectorAll('.nav-tab')].map(b => b.textContent.trim())")
    if names != NAV:
        return ['탭 이름이 %s' % names]
    # 2. 처음 화면은 홈
    if page.evaluate(JS_VISP) != ['top']:
        fails.append('처음에 보이는 화면이 %s' % page.evaluate(JS_VISP))
    if not page.evaluate(JS_SEL_ON, 'nav-home'):
        fails.append('처음에 홈 탭이 고른 표시가 아니다')
    if page.evaluate(JS_VSUB) != []:
        fails.append('홈에서 항목 줄이 보인다')
    # 3. 탭마다 첫 화면
    for g, first in FIRST.items():
        page.click('#nav-' + g)
        page.wait_for_timeout(60)
        if page.evaluate(JS_VISP) != [first]:
            fails.append('%s 탭을 누르면 보이는 화면이 %s' % (g, page.evaluate(JS_VISP)))
        want = [SUBS[g]] if g in SUBS else []
        if page.evaluate(JS_VSUB) != want:
            fails.append('%s 탭의 항목 줄이 %s' % (g, page.evaluate(JS_VSUB)))
        if g in SUBS and not page.evaluate(JS_SEL_ON, 'tab-' + first):
            fails.append('%s 탭의 첫 항목이 고른 표시가 아니다' % g)
    # 4. 휴대폰과 폭 320 에서 아래 탭
    for size in ('phone', (320, 568)):
        p = open_page(pw, path, size=size)
        r = p.evaluate("""() => {
          const bar = document.querySelector('.nav-tabs').getBoundingClientRect();
          const tabs = [...document.querySelectorAll('.nav-tab')];
          return {gap: Math.abs(bar.bottom - innerHeight), pos: getComputedStyle(document.querySelector('.nav-tabs')).position,
            low: tabs.filter(t => t.getBoundingClientRect().height < 48).length,
            wrap: tabs.filter(t => t.scrollWidth > t.clientWidth + 1 || t.querySelector('span').getClientRects().length > 1).length,
            over: document.documentElement.scrollWidth - document.documentElement.clientWidth};
        }""")
        if r['pos'] != 'fixed' or r['gap'] > 1:
            fails.append('%s: 탭이 화면 아래에 붙어 있지 않다 (%s, 틈 %.0f)' % (size, r['pos'], r['gap']))
        if r['low']:
            fails.append('%s: 높이 48 미만인 탭 %d개' % (size, r['low']))
        if r['wrap']:
            fails.append('%s: 이름이 두 줄이 되거나 넘치는 탭 %d개' % (size, r['wrap']))
        if r['over'] > 0:
            fails.append('%s: 가로로 %dpx 밀린다' % (size, r['over']))
    # 5. 맨 아래 내용이 탭에 가려지지 않는다
    p = open_page(pw, path, size='phone', hash='hospital')
    p.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    p.wait_for_timeout(80)
    r = p.evaluate("""() => {
      const kids = [...document.getElementById('hospital').children].filter(e => e.getClientRects().length);
      return {last: kids[kids.length - 1].getBoundingClientRect().bottom, nav: document.querySelector('.nav-tabs').getBoundingClientRect().top};
    }""")
    if r['last'] > r['nav'] + 0.5:
        fails.append('휴대폰: 맨 아래 내용이 탭에 %.0fpx 가려진다' % (r['last'] - r['nav']))
    # 6. PC 에서 메뉴 띠와 항목 줄이 붙어 있다
    p = open_page(pw, path, size='pc', hash='hospital')
    p.evaluate("() => window.scrollTo(0, 800)")
    p.wait_for_timeout(80)
    r = p.evaluate("""() => { const n = document.querySelector('.nav').getBoundingClientRect(), s = document.querySelector('.subbar').getBoundingClientRect();
      return {nt: n.top, nb: n.bottom, st: s.top}; }""")
    if abs(r['nt']) > 1:
        fails.append('PC: 내려도 메뉴 띠가 위에 붙어 있지 않다 (top %.0f)' % r['nt'])
    if abs(r['st'] - r['nb']) > 1:
        fails.append('PC: 항목 줄이 메뉴 띠 바로 아래에 붙어 있지 않다 (%.0f, %.0f)' % (r['st'], r['nb']))
    # 7. 다른 탭을 누르면 맨 위부터
    p.evaluate("() => window.scrollTo(0, 2000)")
    p.click('#nav-plan')
    p.wait_for_timeout(80)
    if p.evaluate("() => window.scrollY") != 0:
        fails.append('PC: 내려 읽다가 다른 탭을 누르면 scrollY 가 %s' % p.evaluate("() => window.scrollY"))
    # 8. 키보드
    p = open_page(pw, path)
    p.focus('#nav-home')
    p.keyboard.press('ArrowRight')
    if not p.evaluate(JS_SEL_ON, 'nav-plan') or p.evaluate("() => document.activeElement.id") != 'nav-plan':
        fails.append('키보드: 홈 탭에서 오른쪽 화살표로 일정 탭에 가지 않는다')
    p.focus('#tab-timeline')
    p.keyboard.press('ArrowRight')
    if not p.evaluate(JS_SEL_ON, 'tab-leave') or p.evaluate(JS_VISP) != ['leave']:
        fails.append('키보드: 전체 일정에서 오른쪽 화살표로 휴가 계획에 가지 않는다')
    # 9. 주소의 # 를 바꾸면 화면이 따라온다
    go(p, 'why')
    if p.evaluate(JS_VISP) != ['why'] or not p.evaluate(JS_SEL_ON, 'nav-preg') or not p.evaluate(JS_SEL_ON, 'tab-why'):
        fails.append('#why 로 바꿨을 때 화면이나 고른 표시가 맞지 않는다 (%s)' % p.evaluate(JS_VISP))
    go(p, 'top')
    if p.evaluate(JS_VISP) != ['top']:
        fails.append('#top 으로 바꿨을 때 보이는 화면이 %s' % p.evaluate(JS_VISP))
    # 10. 왼쪽 차례가 남지 않았다
    if p.evaluate("() => !!document.querySelector('.side,.side-open,.side-bg')"):
        fails.append('왼쪽 차례 요소가 남았다')
    src = pathlib.Path(path).read_text(encoding='utf-8')
    fails += ['파일에 「%s」가 남았다' % w for w in ('ivf-side', 'has-side', 'side-closed') if w in src]
    return fails


JS_SVG = """sel => [...document.querySelectorAll(sel)].filter(s => s.getBoundingClientRect().width >= 300 && s.childElementCount > 0).length"""


@check('charts')
def charts(pw, path):
    fails = []
    page = open_page(pw, path)
    # 그림이 있는 화면은 다섯 개다 (전체 일정, 유산 위험, 지원금과 보험, 조리원과 어린이집, 산후도우미)
    for btn, sel, name in (('#nav-plan', '#timeline svg', '전체 일정'), ('#nav-preg', '#risk svg', '유산 위험'),
                           ('#nav-prep', '#support svg', '지원금과 보험'), ('#tab-care', '#care svg', '조리원과 어린이집'),
                           ('#tab-helper', '#helper svg', '산후도우미')):
        if not page.locator(btn).count():
            fails.append('단추 %s 가 없다' % btn)
            continue
        page.click(btn)
        page.wait_for_timeout(200)
        if page.evaluate(JS_SVG, sel) < 1:
            fails.append('%s: 탭으로 열었을 때 그림이 그려지지 않았다' % name)
    return fails


@check('print')
def print_(pw, path):
    fails = []
    page = open_page(pw, path, hash='timeline')
    page.emulate_media(media='print')
    r = page.evaluate("""ids => ({
      hid: ids.filter(id => getComputedStyle(document.getElementById(id)).display === 'none'),
      menu: ['.nav', '.subbar'].filter(s => { const e = document.querySelector(s); return !e || getComputedStyle(e).display !== 'none'; })
    })""", SCREENS)
    if r['hid']:
        fails.append('인쇄에서 빠지는 화면: %s' % r['hid'])
    if r['menu']:
        fails.append('인쇄에 메뉴가 나오거나 메뉴가 없다: %s' % r['menu'])
    return fails


# ---------- 홈 ----------
JS_BOX = """sel => { const e = document.querySelector(sel); if (!e || e.hidden || !e.getClientRects().length) return null;
  const r = e.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; }"""
JS_ARC = "() => { const e = document.getElementById('h-ring-arc'); return e ? parseFloat(e.getAttribute('stroke-dasharray')) : null; }"


@check('home')
def home(pw, path):
    fails = []
    p = open_page(pw, path)
    for sel, want in (('#h-edd', '예정일 2027년 7월 16일(금)'), ('#h-ring-n', '6주'), ('#h-left', '10일 남았습니다'),
                      ('#h-nt', '심장박동 확인'), ('.home-h', '큰 일정')):
        got = norm(p.evaluate(JS_SEL, sel))
        if got != want:
            fails.append('%s 글이 「%s」 (기대 「%s」)' % (sel, got, want))
    arc = p.evaluate(JS_ARC)
    if arc is None or abs(arc - 45.24) > 0.5:
        fails.append('고리의 호 길이가 %s (기대 45.24)' % arc)
    if '40주 가운데' not in (p.evaluate(JS_SEL, '#h-ring') or ''):
        fails.append('고리에 「40주 가운데」가 없다')
    if p.evaluate("() => { const e = document.getElementById('h-ring'); return e && e.getAttribute('aria-label'); }") != '40주 가운데 6주':
        fails.append('고리의 aria-label 이 「40주 가운데 6주」가 아니다')
    if '11월 30일(월)' not in (p.evaluate(JS_SEL, '#h-nd') or ''):
        fails.append('다음 일정 날짜에 「11월 30일(월)」이 없다')
    b = {k: p.evaluate(JS_BOX, sel) for k, sel in (('today', '.today'), ('next', '#h-next'), ('list', '#gl-list'), ('todo', '#todo'))}
    if not all(b.values()):
        return fails + ['홈의 칸이 안 보인다: %s' % [k for k, v in b.items() if not v]]
    if not b['list']['l'] > b['today']['r']:
        fails.append('PC: 큰 일정이 오늘 칸 오른쪽에 있지 않다')
    if not b['todo']['l'] < b['list']['l']:
        fails.append('PC: 할 일 표가 왼쪽 칸에 있지 않다')
    p = open_page(pw, path, size='phone')
    b = {k: p.evaluate(JS_BOX, sel) for k, sel in (('today', '.today'), ('next', '#h-next'), ('list', '#gl-list'), ('todo', '#todo'))}
    if not all(b.values()):
        return fails + ['휴대폰: 홈의 칸이 안 보인다: %s' % [k for k, v in b.items() if not v]]
    order = ['today', 'next', 'list', 'todo']
    for x, y in zip(order, order[1:]):
        if b[x]['b'] > b[y]['t'] + 0.5:
            fails.append('휴대폰: %s 칸이 %s 칸 위에 있지 않다' % (x, y))
    return fails


@check('first_screen')
def first_screen(pw, path):
    fails = []
    for size, sels in (('phone', ['#h-next']), ((360, 640), ['.today', '#h-nt'])):
        p = open_page(pw, path, size=size)
        nav_top = (p.evaluate(JS_BOX, '.nav-tabs') or {}).get('t')
        for sel in sels:
            b = p.evaluate(JS_BOX, sel)
            if not b or nav_top is None:
                fails.append('%s: %s 나 아래 탭이 없다' % (size, sel))
            elif b['b'] > nav_top + 0.5:
                fails.append('%s: %s 가 첫 화면에서 아래 탭에 %.0fpx 가려진다' % (size, sel, b['b'] - nav_top))
    return fails


@check('edges')
def edges(pw, path):
    fails = []
    p = open_page(pw, path, when='2026-10-01T09:00:00+09:00')
    if norm(p.evaluate(JS_TEXT, 'gl-ga')) != '이식 전':
        fails.append('이식 전: 주수 칸이 「%s」' % norm(p.evaluate(JS_TEXT, 'gl-ga')))
    if p.evaluate(JS_ARC) != 0:
        fails.append('이식 전: 호 길이가 %s' % p.evaluate(JS_ARC))
    if norm(p.evaluate(JS_TEXT, 'h-ring-n')) != '':
        fails.append('이식 전: 고리 안 글이 「%s」' % norm(p.evaluate(JS_TEXT, 'h-ring-n')))
    fails += ['이식 전: 오류 %s' % e[:120] for e in p.rd_errors]
    p = open_page(pw, path, when='2026-11-30T09:00:00+09:00')
    if norm(p.evaluate(JS_TEXT, 'h-left')) != '오늘입니다':
        fails.append('당일: 남은 날 칸이 「%s」' % norm(p.evaluate(JS_TEXT, 'h-left')))
    p = open_page(pw, path, when='2027-09-30T09:00:00+09:00')
    if p.evaluate(JS_BOX, '#h-next') is not None:
        fails.append('모든 일정 뒤: 다음 일정 칸이 보인다')
    cls = p.evaluate("() => [...document.querySelectorAll('#gl-list li')].map(li => li.className)")
    if not cls or any('done' not in c.split() for c in cls):
        fails.append('모든 일정 뒤: 지남이 아닌 줄이 있다 %s' % cls)
    if (p.evaluate(JS_ARC) or 0) < 301:
        fails.append('모든 일정 뒤: 호 길이가 %s' % p.evaluate(JS_ARC))
    fails += ['모든 일정 뒤: 오류 %s' % e[:120] for e in p.rd_errors]
    return fails


# ---------- 부품 모양 ----------
JS_SHAPES = """phone => {
  document.querySelectorAll('details').forEach(d => { d.open = true; });
  const vis = e => e.getClientRects().length > 0, cs = (e, ps) => getComputedStyle(e, ps), out = [];
  const each = (sel, fn) => [...document.querySelectorAll(sel)].filter(vis).forEach(e => { const m = fn(e); if (m) out.push(sel + ': ' + m); });
  each('.pts>li', e => { const c = cs(e); return (c.borderTopWidth !== '0px' || c.borderTopLeftRadius !== '24px') ? '테두리 ' + c.borderTopWidth + ', 모서리 ' + c.borderTopLeftRadius : null; });
  each('details.fold', e => cs(e).borderTopLeftRadius !== '22px' ? '모서리 ' + cs(e).borderTopLeftRadius : null);
  each('.callout', e => { const c = cs(e); return (c.borderLeftWidth !== '0px' || c.borderTopLeftRadius !== '22px') ? '왼쪽 선 ' + c.borderLeftWidth + ', 모서리 ' + c.borderTopLeftRadius : null; });
  each('table', e => (cs(e).display === 'table' && cs(e).borderTopLeftRadius !== '24px') ? '모서리 ' + cs(e).borderTopLeftRadius : null);
  if (phone) each('table.stk tr, .hl-t:not(.cards) tr', e => (cs(e).display === 'block' && cs(e).borderTopLeftRadius !== '22px') ? '펴진 줄의 모서리 ' + cs(e).borderTopLeftRadius : null);
  each('h4.warn, .callout.warn', e => { const b = cs(e, '::before'), m = b.maskImage || b.webkitMaskImage;
    return (b.width !== '18px' || !m || m === 'none') ? '주의 아이콘이 없다 (폭 ' + b.width + ')' : null; });
  return out;
}"""


def shape_run(pw, path, js, schemes=('light',), sizes=('pc', 'phone')):
    fails = []
    for size in sizes:
        for scheme in schemes:
            page = open_page(pw, path, size=size, scheme=scheme)
            for pid in SCREENS:
                go(page, pid)
                r = page.evaluate(js, size == 'phone')
                seen = {}
                for m in r:
                    seen[m] = seen.get(m, 0) + 1
                fails += ['%s %s #%s %s (%d곳)' % (size, scheme, pid, m, n) for m, n in seen.items()]
    return fails


@check('shapes')
def shapes(pw, path):
    return shape_run(pw, path, JS_SHAPES)


# 옛 모양이 남았나 — 각진 칸과 굵은 먹선.
# 빼는 것: 화면 끝까지 닿는 띠(.nav, .subbar), 표의 줄과 칸, 그림(svg) 안, 사진과 영상, 크게 보기 창.
SQUARE_SKIP = '.nav, .subbar, .al-dm'
JS_SQUARES = """skip => {
  document.querySelectorAll('details').forEach(d => { d.open = true; });
  const tags = ['HTML', 'BODY', 'IMG', 'VIDEO', 'TR', 'TD', 'TH', 'THEAD', 'TBODY', 'DIALOG', 'SVG', 'svg'];
  const out = {};
  const name = e => e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  document.querySelectorAll('body *').forEach(e => {
    if (tags.includes(e.tagName) || e.closest('svg') || e.matches(skip) || !e.getClientRects().length) return;
    const r = e.getBoundingClientRect(); if (r.width < 80 || r.height < 32) return;
    const c = getComputedStyle(e), sides = ['Top', 'Right', 'Bottom', 'Left'];
    const w = sides.map(k => c['border' + k + 'Style'] === 'none' ? 0 : parseFloat(c['border' + k + 'Width']));
    const bg = (c.backgroundColor !== 'rgba(0, 0, 0, 0)' && c.backgroundColor !== 'transparent') || c.backgroundImage !== 'none';
    const boxed = w.filter(x => x > 0).length >= 3;
    let why = null;
    if ((bg || boxed) && c.borderTopLeftRadius === '0px') why = '각진 칸';
    else if (w.some(x => x >= 2)) why = '굵은 선 ' + Math.max(...w) + 'px';
    if (why) { const k = name(e) + ' — ' + why; out[k] = (out[k] || 0) + 1; }
  });
  return Object.entries(out).map(([k, n]) => k + ' (' + n + '곳)');
}"""


@check('squares')
def squares(pw, path):
    fails = []
    for size in ('pc', 'phone'):
        page = open_page(pw, path, size=size)
        seen = {}
        for pid in SCREENS:
            go(page, pid)
            for m in page.evaluate(JS_SQUARES, SQUARE_SKIP):
                seen.setdefault(m, []).append(pid)
        fails += ['%s %s: %s' % (size, m, ' '.join(v[:4])) for m, v in seen.items()]
    return fails


def shots(args):
    """눈으로 볼 화면을 찍는다."""
    out = ROOT / '.superpowers' / 'shots'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        pw = Session(p)
        for sz in args.sizes.split(','):
            size = sz if sz in SIZES else tuple(int(x) for x in sz.split('x'))
            page = open_page(pw, args.page, size=size, scheme=args.scheme)
            for pid in (args.names or SCREENS):
                go(page, pid)
                page.wait_for_timeout(150)
                f = out / ('%s-%s-%s-%s.png' % (args.shots, pid, sz, args.scheme))
                page.screenshot(path=str(f), full_page=args.full)
                print(f)
        pw.close_pages()
        pw.browser.close()


def main():
    ap = argparse.ArgumentParser(description='디자인 개편 확인 도구')
    ap.add_argument('names', nargs='*', help='돌릴 검사 이름. 없으면 전부')
    ap.add_argument('--page', default=PAGE, help='index.html 대신 볼 파일')
    ap.add_argument('--shots', metavar='이름', help='검사 대신 화면을 찍는다. names 는 화면 id (없으면 전부). .superpowers/shots/ 에 둔다')
    ap.add_argument('--sizes', default='phone,pc', help='--shots 로 찍을 폭. 예: phone,pc 또는 360x640')
    ap.add_argument('--scheme', default='light', choices=['light', 'dark'])
    ap.add_argument('--full', action='store_true', help='--shots 를 화면 전체 길이로 찍는다')
    args = ap.parse_args()
    if args.shots:
        return shots(args)
    names = args.names or list(CHECKS)
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        sys.exit('없는 검사: %s. 있는 검사: %s' % (', '.join(unknown), ', '.join(CHECKS)))
    bad = 0
    with sync_playwright() as p:
        pw = Session(p)
        for n in names:
            try:
                fails = CHECKS[n](pw, args.page)
            except Exception as e:   # 검사가 죽은 것도 실패다
                fails = ['검사가 멈췄다: %r' % e]
            pw.close_pages()
            if fails:
                bad += 1
                print('FAIL %s (%d건)' % (n, len(fails)))
                for f in fails[:12]:
                    print('   ' + f)
                if len(fails) > 12:
                    print('   … 그 밖에 %d건' % (len(fails) - 12))
            else:
                print('PASS %s' % n)
        pw.browser.close()
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
