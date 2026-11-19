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


def open_page(pw, path, size='pc', scheme='light', when=FIXED, hash=''):
    w, h = SIZES[size] if isinstance(size, str) else size
    ctx = pw.browser.new_context(viewport={'width': w, 'height': h}, timezone_id='Asia/Seoul',
                                 locale='ko-KR', color_scheme=scheme)
    pw.contexts.append(ctx)
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


def panel_texts(page):
    out = {}
    for pid in PANELS:
        go(page, pid)   # 숨은 화면의 그림은 열릴 때 그려진다
        out[pid] = page.evaluate(JS_TEXT, pid)
    go(page, 'top')
    return out


@check('content')
def content(pw, path):
    fails = []
    base = open_page(pw, baseline_path())
    new = open_page(pw, path)
    bt, nt = panel_texts(base), panel_texts(new)
    for pid in PANELS:
        if nt[pid] is None:
            fails.append('%s: 패널이 없다' % pid)
            continue
        a, b = allowed(norm(bt[pid])), norm(nt[pid])
        if a != b:
            fails.append('%s: 글이 다르다. %s' % (pid, first_diff(a, b)))
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


def main():
    ap = argparse.ArgumentParser(description='디자인 개편 확인 도구')
    ap.add_argument('names', nargs='*', help='돌릴 검사 이름. 없으면 전부')
    ap.add_argument('--page', default=PAGE, help='index.html 대신 볼 파일')
    args = ap.parse_args()
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
