#!/usr/bin/env python3
"""디자인 개편 확인 도구.

개편한 index.html 을 main 의 index.html(기준선)과 견주어 글, 날짜, 링크가 그대로인지 보고
화면 폭, 메뉴, 모양을 검사한다. 크롬을 화면 없이 띄워 file:// 로 연다.

  python3 tools/redesign_check.py                 등록된 검사를 다 돌린다
  python3 tools/redesign_check.py content dates   고른 검사만 돌린다
  python3 tools/redesign_check.py content --page .superpowers/mut.html   다른 파일을 본다
  python3 tools/redesign_check.py --base 028db1a                         기준선을 다른 커밋으로 (기본은 main)

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
BASE = 'main'   # 글과 날짜를 견줄 기준 커밋. --base 로 바꾼다. 개편 전 마지막 커밋은 028db1a

FIXED = "2026-11-20T09:00:00+09:00"   # 이때 주수는 6주 0일, 다음 일정까지 10일
PANELS = ['timeline', 'leave', 'risk', 'why', 'weekly', 'meds', 'size', 'support', 'care', 'helper', 'album', 'hospital']
SCREENS = ['top'] + PANELS
SIZES = {'phone': (390, 844), 'tablet': (1024, 768), 'pc': (1440, 900)}

# 비교하기 전에 기준선 글에 적용하는 정규식 치환. 개편에서 글을 고친 곳은 이것뿐이다 —
# 왼쪽 차례와 항목 번호가 없어져서 그것을 가리키던 문구를 새 메뉴 이름으로 바꿨다.
NAMES = '전체 일정|휴가 계획|유산 위험|PGT-A 배아|시기별 할 일|약 안내|아기 크기|지원금과 보험|조리원과 어린이집|산후도우미|분만 병원|사진과 영상'
ALLOWED = [
    (r'^\d+(?:-\d+)?(?=[가-힣A-Z])', ''),                                    # 화면 제목 앞의 번호
    (r'(「?)(?:\d+(?:-\d+)? )?(%s)(」?) 탭' % NAMES, r'\1\2\3 화면'),            # 「3 PGT-A 배아 탭」 → 「PGT-A 배아 화면」
    (r'6번 탭', '「지원금과 보험」 화면'),
    (r'같은 탭', '같은 화면'),
    (r'왼쪽 차례에서 주제를 고르면 그 내용이 열립니다\. 화면이 좁으면 왼쪽 위 「▸\s*차례」를 누릅니다\.',
     '메뉴에서 주제를 고르면 그 내용이 열립니다.'),
    (r'왼쪽 차례의 각 항목', '메뉴의 각 화면'),
]

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
    out = subprocess.run(['git', 'show', BASE + ':index.html'], cwd=ROOT, capture_output=True, check=True).stdout
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
    if (r.height > 0 && r.top <= line + 1) line = Math.max(line, r.bottom);   // 메뉴 띠 아래에 항목 줄이 이어 붙는다
  }
  return line;
}"""


@check('links')
def links(pw, path):
    fails = []
    page = open_page(pw, path)
    # 왼쪽 차례의 링크는 화면을 옮길 때마다 새로 만들어지므로 뺀다. 차례는 toc 검사가 따로 본다
    hrefs = page.evaluate("() => [...document.querySelectorAll('a[href^=\"#\"]:not(#toc a)')].map(a => a.getAttribute('href'))")
    for i, href in enumerate(hrefs):
        page.evaluate("i => document.querySelectorAll('a[href^=\"#\"]:not(#toc a)')[i].click()", i)
        page.wait_for_timeout(60)
        r = page.evaluate(JS_TARGET, href[1:])
        if r.get('missing'):
            fails.append('링크 %s: 가리키는 자리가 없다' % href)
        elif r['hidden'] or r['h'] <= 0:
            fails.append('링크 %s: 눌러도 가리키는 자리가 안 보인다' % href)
        elif r['top'] < page.evaluate(JS_STICK) - 1:
            fails.append('링크 %s: 가리키는 자리가 위에 붙은 메뉴에 가려진다 (top %.0f)' % (href, r['top']))
        elif r['top'] > page.evaluate("() => innerHeight") - 20:
            fails.append('링크 %s: 눌러도 그 자리로 가지 않는다 (top %.0f)' % (href, r['top']))
    # 주소에 # 를 붙여 새로 열었을 때. 브라우저가 로드 끝에 한 번 더 자리를 옮기므로 조금 기다린 뒤 본다
    ids = SCREENS + sorted(set(h[1:] for h in hrefs) | {'todo', 'cert-todo', 'pgt-screen'} - set(SCREENS))
    for size in ('phone', 'pc'):
        for pid in ids:
            p = open_page(pw, path, size=size, hash=pid)
            p.wait_for_timeout(350)
            r = p.evaluate(JS_TARGET, pid)
            if r.get('missing') or r['hidden'] or r['h'] <= 0:
                fails.append('%s 주소 #%s: 바로 열었을 때 그 자리가 안 보인다' % (size, pid))
            elif r['top'] < p.evaluate(JS_STICK) - 1 or r['top'] > p.evaluate("() => innerHeight") - 20:
                fails.append('%s 주소 #%s: 바로 열었을 때 그 자리로 가지 않는다 (top %.0f)' % (size, pid, r['top']))
            p.context.close()
            pw.contexts.remove(p.context)
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
    return dict(SIZES, small=(320, 568))   # 글자를 키운 휴대폰과 같은 폭


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
    # 이름 붙은 폭 사이에서만 나는 밀림을 잡는다. 펼쳐보기를 닫은 채로 한 번, 다 연 채로 한 번
    js = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    for w in range(320, 1441, 40):
        page = open_page(pw, path, size=(w, 900))
        for pid in SCREENS:
            go(page, pid)
            d = page.evaluate(js)
            page.evaluate("() => document.querySelectorAll('details').forEach(x => { x.open = true; })")
            d2 = page.evaluate(js)
            if d > 0 or d2 > 0:
                fails.append('폭 %d #%s: 가로로 %dpx 밀린다 (펼쳐보기를 열면 %dpx)' % (w, pid, d, d2))
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
    for name in ('tablet', (1280, 800), 'pc'):   # 1280 은 왼쪽 차례가 붙는 가장 좁은 폭
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
        'preg': ['유산 위험', 'PGT-A 배아', '시기별 할 일', '약 안내', '아기 크기'],
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
    # 4-2. 휴대폰에서 탭 안 항목 단추가 옆으로 밀지 않아도 모두 보인다
    for size in ('phone', (360, 640), (320, 568)):
        p = open_page(pw, path, size=size)
        for g in SUBS:
            p.click('#nav-' + g)
            p.wait_for_timeout(60)
            cut = p.evaluate("""g => [...document.querySelectorAll('.sub-tabs[data-group="' + g + '"] .sub-tab')].filter(b => {
              const r = b.getBoundingClientRect(); return r.left < -0.5 || r.right > innerWidth + 0.5 || b.scrollWidth > b.clientWidth + 1; }).map(b => b.textContent.trim())""", g)
            if cut:
                fails.append('%s: %s 탭의 항목 단추가 화면 밖으로 나간다 %s' % (size, g, cut))
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
        over = page.evaluate("""sel => [...document.querySelectorAll(sel)].map(s => s.closest('.g-wrap')).filter(Boolean)
          .map(w => w.scrollWidth - w.clientWidth).filter(d => d > 1)""", sel)
        if over:
            fails.append('%s: PC 에서 그림이 틀보다 %spx 넓어 틀 안에서 가로로 밀린다' % (name, over))
    # 위험 그래프는 폭에 맞춰 줄어든다. 틀의 여백이 재는 폭에 들어가면 글자가 13.5px 보다 작아진다
    JS_SCALE = """() => { const s = document.querySelector('#risk .chart svg'); if (!s) return null;
      const t = s.querySelector('text'); return t ? parseFloat(getComputedStyle(t).fontSize) * s.getBoundingClientRect().width / s.viewBox.baseVal.width : null; }"""
    for size in ('phone', 'pc'):
        p = open_page(pw, path, size=size, hash='risk')
        p.wait_for_timeout(200)
        px = p.evaluate(JS_SCALE)
        if px is None or px < 13.4:
            fails.append('%s: 위험 그래프 글자가 %s px 로 그려진다 (13.5px 보다 작으면 안 된다)' % (size, px))
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
    gap = page.evaluate("""() => { const a = document.querySelector('.today').getBoundingClientRect(), b = document.getElementById('h-next').getBoundingClientRect(); return b.top - a.bottom; }""")
    if gap < 8:
        fails.append('홈이 아닌 화면에서 인쇄하면 홈의 오늘 칸과 다음 일정 칸 사이가 %.0fpx 로 붙는다' % gap)
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
    return shape_run(pw, path, JS_SHAPES, schemes=('light', 'dark'))


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
    const clear = k => /rgba\\(\\d+, \\d+, \\d+, 0\\)|transparent/.test(c['border' + k + 'Color']);
    const w = sides.map(k => (c['border' + k + 'Style'] === 'none' || clear(k)) ? 0 : parseFloat(c['border' + k + 'Width']));
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
        for scheme in ('light', 'dark'):
            page = open_page(pw, path, size=size, scheme=scheme)
            seen = {}
            for pid in SCREENS:
                go(page, pid)
                for m in page.evaluate(JS_SQUARES, SQUARE_SKIP):
                    seen.setdefault(m, []).append(pid)
            fails += ['%s %s %s: %s' % (size, scheme, m, ' '.join(v[:4])) for m, v in seen.items()]
    return fails


# 어둡게 볼 때 밝은 칸이 그대로 남았나. 색값을 직접 쓴 규칙이 있으면 걸린다.
JS_DARK = """() => {
  document.querySelectorAll('details').forEach(d => { d.open = true; });
  const lin = v => { v /= 255; return v <= .04045 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
  const out = {};
  document.querySelectorAll('body *').forEach(e => {
    if (e.closest('svg') || ['IMG', 'VIDEO'].includes(e.tagName) || !e.getClientRects().length) return;
    const r = e.getBoundingClientRect(); if (r.width < 160 || r.height < 48) return;
    const m = getComputedStyle(e).backgroundColor.match(/rgba?\\(([\\d.]+), ([\\d.]+), ([\\d.]+)(?:, ([\\d.]+))?\\)/);
    if (!m || (m[4] !== undefined && parseFloat(m[4]) < .5)) return;
    const L = .2126 * lin(+m[1]) + .7152 * lin(+m[2]) + .0722 * lin(+m[3]);
    if (L > .6) { const k = e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\\s+/).join('.') : ''); out[k] = (out[k] || 0) + 1; }
  });
  return Object.entries(out).map(([k, n]) => k + ' (' + n + '곳)');
}"""


@check('dark_fills')
def dark_fills(pw, path):
    fails = []
    for size in ('pc', 'phone'):
        page = open_page(pw, path, size=size, scheme='dark')
        seen = {}
        for pid in SCREENS:
            go(page, pid)
            for m in page.evaluate(JS_DARK):
                seen.setdefault(m, []).append(pid)
        fails += ['%s 어둡게 밝은 칸 %s: %s' % (size, m, ' '.join(v[:4])) for m, v in seen.items()]
    return fails


# ---------- 메뉴를 가리키는 문구 ----------
@check('wording')
def wording(pw, path):
    fails = []
    page = open_page(pw, path, size='phone')
    for pid in SCREENS:
        go(page, pid)
        t = norm(page.evaluate(JS_TEXT, pid))
        for w in ('탭', '왼쪽 차례', '▸ 차례'):
            if w in t:
                i = t.find(w)
                fails.append('#%s: 「%s」가 남았다. 「…%s…」' % (pid, w, t[max(0, i - 24):i + 12]))
    n = page.evaluate("() => document.querySelectorAll('h2 .no').length")
    if n:
        fails.append('제목 앞 번호 칸이 %d개 남았다' % n)
    return fails


@check('album_marks')
def album_marks(pw, path):
    src = pathlib.Path(path).read_text(encoding='utf-8')
    n = len(re.findall(r'/\*ALBUM\*/(.*?)/\*/ALBUM\*/', src, re.S))
    return [] if n == 1 else ['사진 목록 표시(/*ALBUM*/ … /*/ALBUM*/)가 %d번 나온다. tools/album.py 가 이 표시로 목록을 찾는다' % n]


# ---------- 검토에서 나온 것 ----------
@check('focus')
def focus(pw, path):
    fails = []
    # PC 홈에서 Tab 키가 안 보이는 것에 멈추면 안 된다
    p = open_page(pw, path, size='pc')
    for i in range(12):
        p.keyboard.press('Tab')
        # 잘라서 감춘 칸 안의 단추는 제 크기를 그대로 갖는다. 그 자리를 찍어 실제로 그 단추가 나오는지 본다
        r = p.evaluate("""() => { const a = document.activeElement; if (!a || a === document.body) return null;
          a.scrollIntoView({block: 'center'});
          const b = a.getBoundingClientRect(), hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          return {seen: !!hit && (hit === a || a.contains(hit) || hit.contains(a)), t: (a.textContent || '').trim().slice(0, 12), c: a.className}; }""")
        if r and not r['seen']:
            fails.append('PC 홈: Tab %d번째가 안 보이는 것에 멈춘다 (「%s」 %s)' % (i + 1, r['t'], r['c']))
    # 주소의 # 로 화면에 바로 들어왔을 때 화면 전체에 테두리가 둘리면 안 된다
    for size in ('phone', 'pc'):
        p = open_page(pw, path, size=size, hash='timeline')
        p.wait_for_timeout(350)
        st = p.evaluate("() => getComputedStyle(document.getElementById('timeline')).outlineStyle")
        if st != 'none':
            fails.append('%s: #timeline 으로 바로 들어오면 화면 전체에 테두리가 둘린다 (%s)' % (size, st))
    # 고른 탭은 색 말고 굵기로도 구분된다
    p = open_page(pw, path, size='phone')
    r = p.evaluate("""() => ({on: getComputedStyle(document.querySelector('.nav-tab[aria-selected="true"]')).fontWeight,
      off: getComputedStyle(document.querySelector('.nav-tab[aria-selected="false"]')).fontWeight})""")
    if not (int(r['on']) >= 800 and int(r['off']) <= 700):
        fails.append('고른 탭과 안 고른 탭의 굵기가 %s, %s (800 이상과 700 이하여야 한다)' % (r['on'], r['off']))
    return fails


@check('two_digit_week')
def two_digit_week(pw, path):
    """주수가 두 자리가 되면 큰 글자가 고리와 겹치지 않아야 한다. 38주 2일로 본다."""
    fails = []
    for size in ((360, 640), 'phone', (900, 800), (960, 800), (1100, 800), 'pc'):
        p = open_page(pw, path, size=size, when='2027-07-04T09:00:00+09:00')
        # 글자가 칸 밖으로 넘칠 수 있으니 칸이 아니라 글자 자체의 자리를 잰다
        r = p.evaluate("""() => { const e = document.getElementById('gl-ga'), rg = document.createRange(); rg.selectNodeContents(e);
          const n = rg.getBoundingClientRect(), g = document.querySelector('.today .ring').getBoundingClientRect();
          return {t: e.textContent, gap: g.left - n.right}; }""")
        if r['t'] != '38주 2일':
            fails.append('%s: 주수가 「%s」' % (size, r['t']))
        elif r['gap'] < 4:
            fails.append('%s: 「38주 2일」이 고리와 %.0fpx 겹친다' % (size, 4 - r['gap']))
    return fails


@check('sizes')
def sizes(pw, path):
    """글자가 개편 전보다 작아지지 않았나. 왼쪽은 개편 전 값이다."""
    fails = []
    want = (('support', 'th', 14.5), ('support', '.fold-n', 14.5), ('top', '.gl-what', 19), ('top', '.gl-sub', 16.5),
            ('timeline', '.birth h2', 32), ('support', 'td', 14.5), ('weekly', '.sec-desc', 16.5))
    p = open_page(pw, path, size='pc')
    for pid, sel, px in want:
        go(p, pid)
        got = p.evaluate("([id, s]) => { const e = document.querySelector('#' + id + ' ' + s); return e ? parseFloat(getComputedStyle(e).fontSize) : null; }", [pid, sel])
        if got is None or got < px - 0.01:
            fails.append('#%s %s 글자가 %s px (개편 전 %s px)' % (pid, sel, got, px))
    return fails


# ---------- 미뤄 두었다가 고친 것 ----------
@check('aria')
def aria(pw, path):
    """화면 읽기 도구가 쓰는 표시."""
    fails = []
    p = open_page(pw, path, hash='risk')
    r = p.evaluate("""() => { const h = [...document.querySelectorAll('h1')];
      return {n: h.length, off: h.filter(e => e.closest('[hidden]') || getComputedStyle(e).display === 'none' || getComputedStyle(e).visibility === 'hidden').length,
        tabs: [...document.querySelectorAll('.nav-tab')].map(b => [b.id, b.getAttribute('aria-controls') || '']),
        ids: [...document.querySelectorAll('.tabp')].map(x => x.id)}; }""")
    if r['n'] != 1 or r['off']:
        fails.append('h1 이 %d개이고 그중 %d개는 홈이 아닌 화면에서 읽히지 않는다 (하나가 늘 읽혀야 한다)' % (r['n'], r['off']))
    want = {'nav-home': ['top'], 'nav-plan': ['timeline', 'leave'], 'nav-preg': ['risk', 'why', 'weekly', 'meds', 'size'],
            'nav-prep': ['support', 'care', 'helper', 'hospital'], 'nav-photo': ['album']}
    for tid, ctl in r['tabs']:
        if ctl.split() != want.get(tid):
            fails.append('%s 의 aria-controls 가 「%s」 (기대 %s)' % (tid, ctl, ' '.join(want.get(tid, []))))
    return fails


@check('leftovers')
def leftovers(pw, path):
    """개편 뒤 남은 옛 주석과 쓰지 않는 규칙."""
    src = pathlib.Path(path).read_text(encoding='utf-8')
    css = src[src.find('<style>'):src.find('</style>')]
    fails = ['「%s」가 남았다' % w for w in ('9 사진과 영상', '막대 — 3절', '굵은 선으로 나눈다', 'scroll-behavior') if w in src]
    if re.search(r'\nh2\{[^}]*display:flex', css):
        fails.append('제목(h2)에 번호 칸용 flex 가 남았다')
    return fails


@check('robust')
def robust(pw, path):
    """내용에 문단 하나가 끼어도 모양 규칙이 풀리지 않는다."""
    fails = []
    p = open_page(pw, path, size='pc', hash='support')
    js = "() => { const t = document.querySelector('#cert-todo ~ table.stk'); return t ? getComputedStyle(t.rows[1].cells[2]).minWidth : null; }"
    before = p.evaluate(js)
    p.evaluate("() => { const x = document.createElement('p'); x.textContent = '끼운 문단'; document.getElementById('cert-todo').after(x); }")
    after = p.evaluate(js)
    if before != '348px' or after != '348px':
        fails.append('임신확인서 표의 기한 칸 최소폭이 %s 였다가 문단을 끼우면 %s' % (before, after))
    return fails


# ---------- 화면 안 차례 (PC) ----------
JS_TOC = """() => { const t = document.getElementById('toc'); if (!t || !t.getClientRects().length) return null;
  const r = t.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, links: [...t.querySelectorAll('a')].map(a => a.textContent.trim())}; }"""
JS_HEADS = """id => { const p = document.getElementById(id), first = p.querySelector('h2');
  return [...p.querySelectorAll('h2, h3')].filter(h => h !== first && !h.closest('details') && h.getClientRects().length).map(h => h.textContent.trim()); }"""


@check('toc')
def toc(pw, path):
    """PC 에서 긴 화면의 절 제목을 왼쪽에 붙여 두고 눌러서 옮겨 간다."""
    fails = []
    p = open_page(pw, path, size='pc', hash='risk')
    t = p.evaluate(JS_TOC)
    if not t:
        return ['PC #risk: 왼쪽 차례가 없다']
    heads = p.evaluate(JS_HEADS, 'risk')
    if t['links'] != ['맨 위로'] + heads:
        fails.append('PC #risk: 차례의 줄이 화면의 절 제목과 다르다. 차례 %s' % t['links'][:4])
    pan = p.evaluate(JS_BOX, '#risk')
    if t['r'] > pan['l'] - 8:
        fails.append('PC #risk: 차례가 본문과 겹친다 (차례 오른쪽 %.0f, 본문 왼쪽 %.0f)' % (t['r'], pan['l']))
    # 내려도 차례가 화면 위쪽에 붙어 있다
    p.evaluate("() => window.scrollTo(0, 3000)")
    p.wait_for_timeout(120)
    t2, line = p.evaluate(JS_TOC), p.evaluate(JS_STICK)
    if not t2 or t2['t'] < line - 1 or t2['t'] > line + 40:
        fails.append('PC #risk: 3000px 내린 뒤 차례가 위에 붙어 있지 않다 (top %s, 기준선 %.0f)' % (t2 and t2['t'], line))
    # 넷째 줄을 누르면 그 절이 맨 위로 오고 그 줄이 지금 자리로 표시된다
    p.evaluate("() => document.querySelectorAll('#toc a')[3].click()")
    p.wait_for_timeout(250)
    r = p.evaluate("""() => { const a = document.querySelectorAll('#toc a')[3], h = document.getElementById(a.getAttribute('href').slice(1));
      return {top: h ? h.getBoundingClientRect().top : null, cur: a.getAttribute('aria-current'), n: document.querySelectorAll('#toc a[aria-current="true"]').length}; }""")
    line = p.evaluate(JS_STICK)
    if r['top'] is None or r['top'] < line - 1 or r['top'] > line + 60:
        fails.append('PC #risk: 차례 넷째 줄을 눌러도 그 절이 맨 위로 오지 않는다 (top %s)' % r['top'])
    if r['cur'] != 'true' or r['n'] != 1:
        fails.append('PC #risk: 누른 줄이 지금 자리로 표시되지 않는다 (표시된 줄 %d개)' % r['n'])
    # 맨 위로
    p.evaluate("() => document.querySelector('#toc a').click()")
    p.wait_for_timeout(150)
    if p.evaluate("() => window.scrollY") != 0:
        fails.append('PC #risk: 「맨 위로」를 눌러도 맨 위로 가지 않는다')
    # 절이 셋 미만인 화면과 홈에는 차례가 없고 본문이 폭을 다 쓴다
    for pid in ('weekly', 'top'):
        go(p, pid)
        if p.evaluate(JS_TOC):
            fails.append('PC #%s: 절이 적은 화면인데 차례가 있다' % pid)
        w = p.evaluate("id => document.getElementById(id).getBoundingClientRect().width / document.querySelector('.wrap').getBoundingClientRect().width", pid)
        if w < 0.99:
            fails.append('PC #%s: 차례가 없는데 본문이 폭을 다 쓰지 않는다 (%.2f)' % (pid, w))
    # 주소로 바로 들어와도 차례가 채워진다 (사진 목록은 스크립트가 나중에 그린다)
    p = open_page(pw, path, size='pc', hash='album')
    p.wait_for_timeout(300)
    t = p.evaluate(JS_TOC)
    if not t or len(t['links']) < 4:
        fails.append('PC: #album 으로 바로 들어오면 차례가 비어 있다')
    # 휴대폰과 좁은 PC 폭에는 차례가 없다
    for size in ('phone', 'tablet', (1200, 800)):
        q = open_page(pw, path, size=size, hash='risk')
        if q.evaluate(JS_TOC):
            fails.append('%s #risk: 좁은 폭인데 차례가 보인다' % (size,))
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
                page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
                page.wait_for_timeout(150)
                f = out / ('%s-%s-%s-%s.png' % (args.shots, pid, sz, args.scheme))
                page.screenshot(path=str(f), full_page=args.full)
                print(f)
        pw.close_pages()
        pw.browser.close()


# ---------- 아기 크기 ----------
JS_SIZE = """() => { const vis = e => !!e && !e.hidden && e.getClientRects().length > 0, tx = e => e.textContent.replace(/\\s+/g, ' ').trim();
  const g = id => { const e = document.getElementById(id); return vis(e) ? tx(e) : null; };
  const uses = [...document.querySelectorAll('#top use, #size use')].map(u => u.getAttribute('href') || '');
  return {t: g('h-size-t'), d: g('h-size-d'), n: document.querySelectorAll('.sz-grid li').length,
    cur: [...document.querySelectorAll('.sz-grid li.now')].map(tx), sym: document.querySelectorAll('symbol[id^="f-"]').length,
    dead: uses.filter(h => !h || !document.getElementById(h.slice(1))).length,
    name: g('sz-n'), size: g('sz-d'), next: g('sz-nt'), meas: g('sz-meas'), k: g('sz-k')}; }"""


@check('babysize')
def babysize(pw, path):
    """홈의 크기 줄과 아기 크기 화면. 주수에 맞는 과일이 나오고 4주 전에는 숨는다."""
    fails = []

    def at(when, size='phone'):
        p = open_page(pw, path, size=size, when=when)
        home = p.evaluate(JS_SIZE)
        go(p, 'size')
        return p, home, p.evaluate(JS_SIZE)

    # 오늘(6주 0일)
    p, h, s = at(FIXED)
    if h['t'] != '지금 아기는 석류알만 합니다' or h['d'] != '보통 0.4~0.9cm, 40주 크기 보기':
        fails.append('6주 0일 홈: 크기 줄이 「%s」 「%s」' % (h['t'], h['d']))
    if s['n'] != 37 or s['sym'] != 37 or s['dead']:
        fails.append('과일 칸 %s개, 그림 %s개, 그림이 없는 칸 %s개 (기대 37, 37, 0)' % (s['n'], s['sym'], s['dead']))
    if s['cur'] != ['6주지금 석류알 0.4~0.9cm'] and s['cur'] != ['6주 지금 석류알 0.4~0.9cm']:
        fails.append('6주 0일: 지금 칸이 %s' % s['cur'])
    if (s['k'], s['name'], s['next'], s['meas']) != ('이번 주, 6주', '석류알', '다음 주에는 블루베리만 해집니다', None):
        fails.append('6주 0일: 이번 주 칸이 %s' % [s['k'], s['name'], s['next'], s['meas']])
    if '0.4~0.9cm' not in (s['size'] or '') or '보통' not in (s['size'] or ''):
        fails.append('6주 0일: 이번 주 칸의 크기가 「%s」' % s['size'])
    # 홈의 크기 줄을 누르면 아기 크기 화면이 열린다
    p = open_page(pw, path, size='phone')
    p.click('#h-size')
    p.wait_for_timeout(100)
    if p.evaluate(JS_VISP) != ['size'] or not p.evaluate(JS_SEL_ON, 'nav-preg') or not p.evaluate(JS_SEL_ON, 'tab-size'):
        fails.append('홈의 크기 줄을 눌러도 아기 크기 화면이 열리지 않는다 (%s)' % p.evaluate(JS_VISP))
    # 날짜를 바꿔 본다
    for when, name, d in (('2026-11-06T09:00:00+09:00', '딸기씨', '40주 크기 보기'),
                          ('2027-01-08T09:00:00+09:00', '복숭아', '보통 6.7~7.8cm, 40주 크기 보기'),
                          ('2027-01-15T09:00:00+09:00', '귤', '보통 15cm, 93g, 40주 크기 보기'),
                          ('2027-07-04T09:00:00+09:00', '늙은호박', '보통 49cm, 3.2kg, 40주 크기 보기'),
                          ('2027-07-30T09:00:00+09:00', '수박', '보통 51cm, 3.6kg, 40주 크기 보기')):
        p, h, s = at(when)
        if h['t'] != '지금 아기는 %s만 합니다' % name or h['d'] != d:
            fails.append('%s 홈: 크기 줄이 「%s」 「%s」 (기대 %s, %s)' % (when[:10], h['t'], h['d'], name, d))
        if s['name'] != name or len(s['cur']) != 1 or name not in s['cur'][0]:
            fails.append('%s: 이번 주 칸이 「%s」, 지금 칸이 %s' % (when[:10], s['name'], s['cur']))
        if (s['next'] is None) != (name == '수박'):
            fails.append('%s: 다음 주 줄이 「%s」' % (when[:10], s['next']))
        fails += ['%s: 오류 %s' % (when[:10], e[:120]) for e in p.rd_errors]
    # 4주 전에는 크기 줄과 이번 주 칸을 숨긴다
    for when in ('2026-10-01T09:00:00+09:00', '2026-11-05T09:00:00+09:00'):
        p, h, s = at(when)
        if h['t'] is not None or s['name'] is not None or s['cur']:
            fails.append('%s(4주 전): 크기 줄 「%s」, 이번 주 칸 「%s」, 지금 칸 %s' % (when[:10], h['t'], s['name'], s['cur']))
        fails += ['%s: 오류 %s' % (when[:10], e[:120]) for e in p.rd_errors]
    # 좁은 휴대폰에서 과일 칸의 글이 칸 밖으로 넘치지 않는다
    for size in ((320, 568), (360, 640), 'phone', 'pc'):
        p = open_page(pw, path, size=size, hash='size')
        cut = p.evaluate("""() => [...document.querySelectorAll('.sz-grid li')].filter(li => li.scrollWidth > li.clientWidth + 1
          || [...li.children].some(c => c.getBoundingClientRect().right > li.getBoundingClientRect().right + 0.5)).map(li => li.textContent.trim().slice(0, 12))""")
        if cut:
            fails.append('%s: 글이 칸 밖으로 나가는 과일 칸 %s' % (size, cut[:5]))
    return fails


def main():
    global BASE
    ap = argparse.ArgumentParser(description='디자인 개편 확인 도구')
    ap.add_argument('names', nargs='*', help='돌릴 검사 이름. 없으면 전부')
    ap.add_argument('--page', default=PAGE, help='index.html 대신 볼 파일')
    ap.add_argument('--base', default=BASE, help='글과 날짜를 견줄 기준 커밋 (기본 main)')
    ap.add_argument('--shots', metavar='이름', help='검사 대신 화면을 찍는다. names 는 화면 id (없으면 전부). .superpowers/shots/ 에 둔다')
    ap.add_argument('--sizes', default='phone,pc', help='--shots 로 찍을 폭. 예: phone,pc 또는 360x640')
    ap.add_argument('--scheme', default='light', choices=['light', 'dark'])
    ap.add_argument('--full', action='store_true', help='--shots 를 화면 전체 길이로 찍는다')
    args = ap.parse_args()
    BASE = args.base
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
