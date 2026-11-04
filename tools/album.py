#!/usr/bin/env python3
"""9번 탭 「사진과 영상」에 사진과 영상을 더한다.

원본은 앨범-원본/ 에 둔다. 그 폴더는 .gitignore 에 있어 공개 저장소에 올라가지 않는다.
이 도구는 원본의 크기를 줄이고 사진 속 정보(찍은 기기, 시각, 위치)를 지운 사본을
album/ 에 만든 뒤, index.html 의 ALBUM 목록에 날짜와 내용을 적는다.

  python3 -I tools/album.py add --date 2026-11-20 --kind 초음파 \
      --title "첫 초음파" --note "아기집을 확인했습니다." \
      앨범-원본/a.jpg 앨범-원본/b.mp4

  --crop 2=570,740,2670,3504   2번째 파일을 잘라 낸다. 회전을 바로잡은 뒤의 픽셀 기준
  --alt "사진 설명"            화면 읽기 프로그램이 읽는 글. 이번에 넣는 파일 모두에 붙는다

  python3 -I tools/album.py list

같은 날짜와 같은 제목이 이미 있으면 그 칸에 파일을 더한다.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'index.html')
OUT = os.path.join(ROOT, 'album')
TH = os.path.join(OUT, 'th')
WEB_PX, TH_PX, VID_PX = 1600, 720, 1280
KINDS = {'배아': 'embryo', '테스트기': 'test', '피검사': 'blood', '초음파': 'us',
         '입체초음파': 'us3d', '영상': 'video', '태동': 'kick', '만삭': 'late',
         '출산': 'birth', '아기': 'baby', '기타': 'etc'}
IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
VID_EXT = {'.mp4', '.mov', '.m4v', '.3gp', '.avi', '.mkv'}
MARK = re.compile(r'/\*ALBUM\*/(.*?)/\*/ALBUM\*/', re.S)


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


def load():
    s = open(PAGE, encoding='utf-8').read()
    m = MARK.search(s)
    if not m:
        sys.exit('index.html 에 ALBUM 목록 표시가 없습니다')
    return s, m, json.loads(m.group(1))


def save(s, m, data):
    data.sort(key=lambda e: e['d'])
    body = '[\n' + ',\n'.join(json.dumps(e, ensure_ascii=False) for e in data) + '\n]' if data else '[]'
    s = s[:m.start(1)] + body + s[m.end(1):]
    open(PAGE, 'w', encoding='utf-8').write(s)


def next_base(date, slug):
    n = 1
    while any(os.path.exists(os.path.join(OUT, f'{date}-{slug}-{n}{e}')) for e in ('.jpg', '.mp4')):
        n += 1
    return f'{date}-{slug}-{n}'


def open_image(path):
    if os.path.splitext(path)[1].lower() in ('.heic', '.heif'):
        out = os.path.join(tempfile.mkdtemp(), 'x.jpg')
        subprocess.run(['sips', '-s', 'format', 'jpeg', path, '--out', out], check=True, capture_output=True)
        path = out
    return ImageOps.exif_transpose(Image.open(path)).convert('RGB')


def fit(im, px):
    im = im.copy()
    im.thumbnail((px, px), Image.LANCZOS)
    return im


def put_image(src, base, crop):
    im = open_image(src)
    if crop:
        im = im.crop(crop)
    web, th = fit(im, WEB_PX), fit(im, TH_PX)
    f, t = os.path.join(OUT, base + '.jpg'), os.path.join(TH, base + '.jpg')
    # exif 를 넘기지 않고 저장하므로 사진 속 정보는 따라오지 않는다
    web.save(f, 'JPEG', quality=84, optimize=True, progressive=True)
    th.save(t, 'JPEG', quality=80, optimize=True, progressive=True)
    return {'f': rel(f), 'th': rel(t), 'w': web.width, 'h': web.height}


def probe(path, entries, stream=None):
    cmd = ['ffprobe', '-v', 'error']
    if stream:
        cmd += ['-select_streams', stream]
    cmd += ['-show_entries', entries, '-of', 'csv=p=0:s=x', path]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def put_video(src, base):
    f, t = os.path.join(OUT, base + '.mp4'), os.path.join(TH, base + '.jpg')
    vf = (f"scale='if(gt(iw,ih),min({VID_PX},iw),-2)':'if(gt(iw,ih),-2,min({VID_PX},ih))'")
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-map_metadata', '-1',
                    '-map', '0:v:0', '-map', '0:a:0?', '-vf', vf, '-c:v', 'libx264', '-preset', 'medium',
                    '-crf', '26', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k',
                    '-movflags', '+faststart', f], check=True)
    dur = float(probe(f, 'format=duration') or 0)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', str(min(1.0, dur / 2)), '-i', f,
                    '-frames:v', '1', '-vf', f'scale={TH_PX}:-2', '-q:v', '4', t], check=True)
    w, h = (int(v) for v in probe(f, 'stream=width,height', 'v:0').split('x')[:2])
    mb = os.path.getsize(f) / 1e6
    if mb > 45:
        print(f'  [주의] {os.path.basename(f)} 가 {mb:.0f}MB 입니다. GitHub 는 한 파일에 100MB 까지만 받습니다')
    return {'f': rel(f), 'th': rel(t), 'w': w, 'h': h, 'v': 1, 'dur': round(dur)}


def cmd_add(a):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', a.date):
        sys.exit('날짜는 2026-11-20 처럼 씁니다')
    crops = {}
    for c in a.crop or []:
        i, box = c.split('=')
        crops[int(i)] = tuple(int(v) for v in box.split(','))
    os.makedirs(TH, exist_ok=True)
    s, m, data = load()
    entry = next((e for e in data if e['d'] == a.date and e['t'] == a.title), None)
    if entry is None:
        entry = {'d': a.date, 'k': a.kind, 't': a.title, 'n': a.note or '', 'm': []}
        data.append(entry)
    elif a.note:
        entry['n'] = a.note
    slug = KINDS.get(a.kind, 'etc')
    for i, src in enumerate(a.files, 1):
        ext = os.path.splitext(src)[1].lower()
        base = next_base(a.date, slug)
        if ext in IMG_EXT:
            item = put_image(src, base, crops.get(i))
        elif ext in VID_EXT:
            item = put_video(src, base)
        else:
            sys.exit(f'모르는 파일 종류입니다: {src}')
        if a.alt:
            item['alt'] = a.alt
        entry['m'].append(item)
        print('  더함', item['f'], f"{item['w']}x{item['h']}")
    save(s, m, data)
    print(f"{a.date} 「{a.title}」 파일 {len(entry['m'])}개")


def cmd_list(_):
    for e in load()[2]:
        print(e['d'], e['k'], e['t'], f"파일 {len(e['m'])}개")


def main():
    ap = argparse.ArgumentParser(description='9번 탭 「사진과 영상」에 파일을 더한다')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('add')
    p.add_argument('--date', required=True)
    p.add_argument('--kind', required=True, help='배아, 테스트기, 피검사, 초음파, 입체초음파, 영상, 출산, 아기 등')
    p.add_argument('--title', required=True)
    p.add_argument('--note')
    p.add_argument('--alt')
    p.add_argument('--crop', action='append')
    p.add_argument('files', nargs='+')
    p.set_defaults(fn=cmd_add)
    sub.add_parser('list').set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
