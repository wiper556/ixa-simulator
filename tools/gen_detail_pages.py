# -*- coding: utf-8 -*-
"""武将1体・スキル1件ごとの単独ページを生成し、導線とサイトマップまで整える。

    python tools/gen_detail_pages.py

いつ走らせるか: characters*.html / skills.html のデータを足したり直したときは毎回。
prerender.py と同じで、生成物(busho/ ・ skill/)は手で編集しないこと。

なぜ必要か: 詳細パネルの中身はJSでしか描かれないため、そのままではクローラーに
読まれず、URLも characters-kyoku.html#2613 のように1本しかない。1項目1URLの
静的ページを別に持つことで、「戦国IXA {武将名} 合成」のような検索の受け皿を作る。

作り: 描画は各DBページが持つ描画関数を実機で動かしてinnerHTMLを取り出す。
描画ロジックを二重に持たないので、本体を直せば再生成するだけで追随する。
"""
import contextlib
import functools
import http.server
import socket
import socketserver
import threading


@contextlib.contextmanager
def local_server(root, port=0):
    """生成のあいだだけ静的サーバを立てる。
    skills.html の「初期スキル保持者」欄が実行時fetchで他ページを読むため、
    file:// では描画できずhttpが要る。portは空きを自動で拾う。"""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('127.0.0.1', port), handler) as srv:
        srv.RequestHandlerClass.log_message = lambda *a, **k: None
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield 'http://127.0.0.1:%d/' % srv.server_address[1]
        srv.shutdown()


import datetime
import glob
import io, os, re, json, subprocess, sys, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

# 2026-08-13: ここは r'c:\Users\uesug\ixa-simulator' の決め打ちだった。
# **どこで実行しても本体リポジトリに chdir する**ので、
#  ・CI(Linux)では起動した瞬間に FileNotFoundError
#  ・git worktree や clone で回しても、生成されるのは本体のほう
# という状態だった。tools/check_generated.py が「一致している」と言っていたのは、
# 取り出したツリーではなく本体を再生成した結果を見ていたからで、検査になっていなかった。
# スクリプトの位置からリポジトリの根を求める。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 'https://wiper556.github.io/ixa-simulator/'
os.chdir(ROOT)

SRC = [
    # (ソースページ, 配列名, 描画関数, 詳細コンテナid, 出力先, 種別ラベル, 一覧ページ)
    ('characters.html',       'generals',      'gdbRenderDetail', 'gdbDetailBody', 'busho', '武将',   'characters.html'),
    ('characters-kyoku.html', 'kyokuGenerals', 'kkRenderDetail',  'kkDetailBody',  'busho', '極武将', 'characters-kyoku.html'),
    ('characters-ketsu.html', 'ketsuGenerals', 'ktRenderDetail',  'ktDetailBody',  'busho', '傑武将', 'characters-ketsu.html'),
    ('skills.html',           'skills',        'sklRenderDetail', 'sklDetailBody', 'skill', 'スキル', 'skills.html'),
]

NAV = '''  <aside class="site-sidebar" id="siteSidebar">
    <div class="sidebar-header"><span class="sidebar-title">メニュー</span></div>
    <nav class="sidebar-nav">
      <a href="../index.html">シミュレーター一覧</a>
      <a href="../attack-simulator.html">攻撃/防御シミュレーター</a>
      <a href="../gacha-simulator.html">くじシミュレーター</a>
      <a href="../characters-hub.html">武将データベース</a>
      <a href="../skills-hub.html">スキル一覧</a>
      <a href="../blog.html">記事</a>
      <a href="../privacy.html">プライバシーポリシー</a>
    </nav>
  </aside>'''

FOOTER = '''  <footer class="site-footer">
    <p>本サイトは戦国IXA(Sengoku IXA)の非公式ファンメイドツールです。運営会社・関連団体とは一切関係ありません。<br>
    記載されている会社名・製品名・システム名などは、各社の商標、または登録商標です。<br>
    &copy; SQUARE ENIX</p>
    <p><a href="../privacy.html">プライバシーポリシー</a></p>
  </footer>'''


def esc(x):
    return (str(x).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def slug(name):
    """スキル名をファイル名にする。全角/半角の空白だけアンダースコアに置換する。"""
    return name.replace(' ', '_').replace('\u3000', '_')


def fix_paths(html, known_nos, known_skills):
    """詳細HTML内の相対パスを busho/ ・ skill/ 配下から見た形に直す。
    DBページの#アンカー参照は、対応する単独ページがあればそちらへ向ける
    (475ページが相互リンクし、クローラーが全ページに辿り着けるようにするため)。"""
    def repl(m):
        attr, url = m.group(1), m.group(2)
        if url.startswith(('../', 'http', '#', '/', 'data:', 'mailto:')):
            return m.group(0)
        # 武将DBページの#No → busho/{No}.html
        mm = re.match(r'characters(?:-kyoku|-ketsu)?\.html#(.+)$', url)
        if mm:
            no = urllib.parse.unquote(mm.group(1))
            if no in known_nos:
                return '%s="../busho/%s.html"' % (attr, no)
        # スキル一覧の#名前 → skill/{名前}.html
        mm = re.match(r'skills\.html#(.+)$', url)
        if mm:
            nm = urllib.parse.unquote(mm.group(1))
            if nm in known_skills:
                return '%s="../skill/%s"' % (attr, urllib.parse.quote(slug(nm) + '.html'))
        return '%s="../%s"' % (attr, url)
    return re.sub(r'(src|href)="([^"]+)"', repl, html)


def page(*, title, desc, canon, styles, body, crumb, img):
    return '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-7334304987274613"
     crossorigin="anonymous"></script>
<title>%(title)s</title>
<meta name="description" content="%(desc)s">
<link rel="stylesheet" href="../assets/css/site.css">
<link rel="icon" href="../favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="192x192" href="../assets/img/icon-192.png">
<link rel="apple-touch-icon" href="../assets/img/apple-touch-icon.png">
<link rel="canonical" href="%(canon)s">
<meta property="og:type" content="article">
<meta property="og:site_name" content="戦国IXA シミュレーター置き場">
<meta property="og:locale" content="ja_JP">
<meta property="og:title" content="%(title)s">
<meta property="og:url" content="%(canon)s">
<meta property="og:image" content="%(img)s">
<meta property="og:description" content="%(desc)s">
<meta name="twitter:card" content="summary_large_image">
%(ld)s
<style>
%(styles)s
</style>
</head>
<body>
<div class="site-layout">
%(nav)s
  <div class="site-content">
  <header class="site-header">
    <a class="site-logo" href="../index.html">戦国IXA シミュレーター置き場</a>
  </header>

  <main class="site-main">
    <p class="article-meta">%(crumbhtml)s</p>
%(body)s
  </main>

%(footer)s
  </div>
</div>
<script src="../assets/js/site-sidebar.js"></script>
</body>
</html>
''' % {
        'title': esc(title), 'desc': esc(desc), 'canon': canon, 'img': img,
        'styles': styles, 'body': body, 'nav': NAV, 'footer': FOOTER,
        'crumbhtml': ' ／ '.join('<a href="%s">%s</a>' % (h, esc(t)) if h else esc(t)
                                for t, h in crumb),
        'ld': '<script type="application/ld+json">%s</script>' % json.dumps({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": t,
                 **({"item": BASE + h.replace('../', '')} if h else {})}
                for i, (t, h) in enumerate(crumb)]
        }, ensure_ascii=False),
    }


def main(HOST):
    made = {'busho': [], 'skill': []}
    for d in ('busho', 'skill'):
        os.makedirs(d, exist_ok=True)

    # 先に全ページのNo./スキル名を集めておく(リンク書き換えの判定に使う)
    known_nos, known_skills = set(), set()
    with sync_playwright() as pw0:
        b0 = pw0.chromium.launch()
        for src, var, _f, _c, outdir, _k, _l in SRC:
            p0 = b0.new_page()
            p0.goto(HOST + src, wait_until='networkidle'); p0.wait_for_timeout(400)
            for e in p0.evaluate('()=>JSON.parse(JSON.stringify(%s))' % var):
                (known_nos if outdir == 'busho' else known_skills).add(
                    e['no'] if outdir == 'busho' else e['name'])
            p0.close()
        b0.close()
    print('リンク先候補: 武将%d / スキル%d' % (len(known_nos), len(known_skills)))

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for src, var, fn, cid, outdir, kind, listpage in SRC:
            html = io.open(src, encoding='utf-8').read()
            styles = '\n'.join(m.group(1) for m in
                               re.finditer(r'<style>(.*?)</style>', html, re.S))
            pg = br.new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(HOST + src, wait_until='networkidle')
            pg.wait_for_timeout(700)
            data = pg.evaluate('()=>JSON.parse(JSON.stringify(%s))' % var)

            for idx, e in enumerate(data):
                # const宣言の配列はwindowのプロパティにならないので、
                # 変数名・関数名はJSソースに直接埋め込んで参照する
                detail = pg.evaluate(
                    '(idx)=>{ %s(%s[idx]); const el=document.getElementById("%s");'
                    ' return el ? el.innerHTML : null; }' % (fn, var, cid), idx)
                if not detail:
                    print('  ! 描画できない:', e.get('name')); continue
                detail = fix_paths(detail, known_nos, known_skills)

                if outdir == 'busho':
                    no, name = e['no'], e['name']
                    fname = '%s.html' % no
                    title = '%s(No.%s)の性能・スキル・合成候補｜戦国IXA' % (name, no)
                    bits = [e.get('effect') or '', e.get('initialSkill') or '']
                    desc = ('戦国IXAの%s %s(カードNo.%s)のデータ。初期値・成長値・統率・'
                            'スキル効果・鍛錬(TR)別の数値・スキル追加合成の候補をまとめています。%s'
                            % (kind, name, no, ('初期スキルは%s。' % e['initialSkill']) if e.get('initialSkill') else ''))
                    h1 = '%s <span style="font-size:.6em;color:var(--muted);">No.%s</span>' % (esc(name), esc(no))
                    back = '<p style="margin-top:28px;"><a href="../%s#%s">%s一覧に戻る</a></p>' % (listpage, no, kind)
                    imgf = e.get('imageFull') or ''
                    img = BASE + imgf if imgf else BASE + 'assets/img/icon-192.png'
                    crumb = [('ホーム', '../index.html'), ('武将データベース', '../characters-hub.html'),
                             ('%s一覧' % kind, '../' + listpage), (name, None)]
                    key = no
                else:
                    name = e['name']
                    fname = '%s.html' % slug(name)
                    title = '%s[%s]の効果・合成候補・所持武将｜戦国IXA' % (name, e.get('rank', ''))
                    desc = ('戦国IXAのスキル「%s」[%s]の効果と発動確率、鍛錬(TR)別の数値、'
                            'このスキルを持つ武将、スキル追加合成での移植先をまとめています。'
                            % (name, e.get('rank', '')))
                    h1 = '%s <span style="font-size:.6em;color:var(--muted);">[%s]</span>' % (esc(name), esc(e.get('rank', '')))
                    back = '<p style="margin-top:28px;"><a href="../%s#%s">スキル一覧に戻る</a></p>' % (
                        listpage, urllib.parse.quote(name))
                    img = BASE + 'assets/img/icon-192.png'
                    crumb = [('ホーム', '../index.html'), ('スキル一覧', '../skills-hub.html'),
                             ('全スキル', '../' + listpage), (name, None)]
                    key = name

                body = ('    <h1 class="page-title">%s</h1>\n%s\n%s' % (h1, detail, back))
                io.open(os.path.join(outdir, fname), 'w', encoding='utf-8', newline='').write(
                    page(title=title, desc=desc, canon=BASE + outdir + '/' + urllib.parse.quote(fname),
                         styles=styles, body=body, crumb=crumb, img=img))
                made[outdir].append((key, name, fname))

            print('%-24s %3d件 → %s/  JSエラー=%s' % (src, len(data), outdir, errs[:1] or 'なし'))
            pg.close()
        br.close()

    # クロール用の索引ページ
    for d, heading, lead in (
        ('busho', '武将ページ一覧', '登録済みの武将カード1体ごとのページです。カードNo.順に並んでいます。'),
        ('skill', 'スキルページ一覧', '登録済みのスキル1件ごとのページです。'),
    ):
        items = sorted(made[d], key=lambda x: (len(str(x[0])), str(x[0])))
        li = '\n'.join('        <li><a href="%s">%s</a></li>' %
                       (urllib.parse.quote(f), esc(n if d == 'skill' else '%s No.%s' % (n, k)))
                       for k, n, f in items)
        body = ('    <h1 class="page-title">%s</h1>\n'
                '    <p class="page-lead">%s</p>\n'
                '    <div class="content-block">\n'
                '      <ul style="columns:2;column-gap:32px;line-height:2;font-size:13.5px;">\n%s\n      </ul>\n'
                '    </div>' % (heading, lead, li))
        io.open(os.path.join(d, 'index.html'), 'w', encoding='utf-8', newline='').write(
            page(title='%s｜戦国IXA シミュレーター置き場' % heading,
                 desc=lead, canon=BASE + d + '/', styles='', body=body,
                 crumb=[('ホーム', '../index.html'), (heading, None)],
                 img=BASE + 'assets/img/icon-192.png'))
        print('%s/index.html … %d件' % (d, len(items)))

    # 2026-08-13: os.environ['TEMP'] は Windows にしか無く、CI(Linux)で KeyError になった。
    # 生成したページの一覧を後から見るためだけの控えなので、環境に依存しない置き場にする。
    import tempfile
    json.dump({k: [list(x) for x in v] for k, v in made.items()},
              open(os.path.join(tempfile.gettempdir(), 'gen_pages_made.json'),
                   'w', encoding='utf-8'),
              ensure_ascii=False)
    print('\n合計 %d ページ' % (len(made['busho']) + len(made['skill']) + 2))



def wire():
    # ---------- 1. 詳細パネルの下に単独ページへのリンク ----------
    # (ページ, 詳細bodyのid, リンク先の作り方JS)
    PERMA = [
        ('characters.html',       'gdbDetailBody', 'gdbRoute',  "'busho/'+g.no+'.html'",  'g'),
        ('characters-kyoku.html', 'kkDetailBody',  'kkRoute',   "'busho/'+g.no+'.html'",  'g'),
        ('characters-ketsu.html', 'ktDetailBody',  'ktRoute',   "'busho/'+g.no+'.html'",  'g'),
        ('skills.html',           'sklDetailBody', 'sklRoute',
         "'skill/'+encodeURIComponent(s.name.replace(/[ \\u3000]/g,'_'))+'.html'", 's'),
    ]
    for f, bodyid, routefn, expr, var in PERMA:
        s = io.open(f, encoding='utf-8', newline='').read()
        boxid = bodyid.replace('DetailBody', 'Permalink')
    
        # 置き場所: 詳細bodyの直後(コメント欄の前)
        anchor = '<div id="%s"></div>' % bodyid
        assert anchor in s, f
        if ('id="%s"' % boxid) not in s:
            s = s.replace(anchor, anchor + '\n      <p id="%s" class="detail-permalink"></p>' % boxid, 1)
    
        # ルーティングで中身を入れる。
        # 開始/終了マーカーで挟み、再実行時はマーカーごと消してから入れ直す
        # (目印なしで正規表現に範囲を推測させると、入れ子になって二重化する)
        BEG, END = '    // PERMALINK:start', '    // PERMALINK:end'
        s = re.sub(re.escape(BEG) + r'.*?' + re.escape(END) + r'\n', '', s, flags=re.S)

        anchor2 = "    window.scrollTo(0, 0);\n  }\n\n  window.addEventListener('hashchange', %s);" % routefn
        assert anchor2 in s, '差し込み位置が見つからない: ' + f
        block = (
            BEG + ' この項目だけの単独ページ(検索から直接来られるようにしている)\n'
            '    (function(){\n'
            "      const box = document.getElementById('%s');\n"
            '      if(box) box.innerHTML = \'<a href="\' + %s + \'">このページの単独URL</a>\';\n'
            '    })();\n' % (boxid, expr)
            + END + '\n')
        s = s.replace(anchor2, block + anchor2, 1)
        io.open(f, 'w', encoding='utf-8', newline='').write(s)
        print('  リンク追加:', f)
    
    # 見た目(共通CSSに1つだけ足す)
    css = io.open('assets/css/site.css', encoding='utf-8', newline='').read()
    if '.detail-permalink' not in css:
        css += ('\n/* 武将/スキルの詳細から、その項目だけの単独ページ(busho/・skill/)へ行くリンク。\n'
                '   検索エンジンに単独ページの存在を伝える導線も兼ねている。 */\n'
                '.detail-permalink{margin:18px 0 0;font-size:12.5px;}\n'
                '.detail-permalink a{color:var(--muted);}\n'
                '.detail-permalink a:hover{color:var(--vermillion-bright);}\n')
        io.open('assets/css/site.css', 'w', encoding='utf-8', newline='').write(css)
        print('  CSS追加: .detail-permalink')
    
    # ---------- 2. ハブページから索引へ ----------
    HUB = [('characters-hub.html', 'busho/', '武将ページ一覧(1体ずつ)',
            'カードNo.ごとの単独ページです。検索やブックマークから直接開けます。'),
           ('skills-hub.html', 'skill/', 'スキルページ一覧(1件ずつ)',
            'スキル名ごとの単独ページです。効果・鍛錬別の数値・所持武将をまとめています。')]
    for f, d, label, note in HUB:
        s = io.open(f, encoding='utf-8', newline='').read()
        if 'href="%sindex.html"' % d in s:
            print('  索引リンクは既にある:', f); continue
        m = list(re.finditer(r'</main>', s))
        block = ('''    <div class="content-block">
          <h2 style="font-family:'Noto Serif JP',serif;font-size:17px;margin:0 0 6px;color:var(--vermillion-bright);">%s</h2>
          <p style="margin:0 0 10px;font-size:13.5px;line-height:1.8;">%s</p>
          <p style="margin:0;"><a href="%sindex.html">%s を開く</a></p>
        </div>
    
    ''' % (label, note, d, label))
        s = s[:m[-1].start()] + block + s[m[-1].start():]
        io.open(f, 'w', encoding='utf-8', newline='').write(s)
        print('  索引リンク追加:', f)
    
    # ---------- 3. sitemap ----------
    def lastmod(p):
        try:
            o = subprocess.run(['git', 'log', '-1', '--format=%cs', '--', p],
                               capture_output=True, text=True, timeout=20).stdout.strip()
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', o):
                return o
        except Exception:
            pass
        return datetime.date.today().isoformat()
    
    today = datetime.date.today().isoformat()
    order = ['index.html', 'attack-simulator.html', 'gacha-simulator.html', 'honmaru-simulator.html',
             'characters-hub.html', 'skills-hub.html', 'blog.html']
    top = sorted(glob.glob('*.html'))
    rows = []
    for p in order + [x for x in top if x not in order]:
        if not os.path.exists(p):
            continue
        rows.append('  <url><loc>%s</loc><lastmod>%s</lastmod></url>'
                    % (BASE if p == 'index.html' else BASE + p, lastmod(p)))
    for d in ('busho', 'skill'):
        files = sorted(glob.glob(d + '/*.html'))
        files.sort(key=lambda x: (not x.endswith('index.html'), x))
        for p in files:
            u = BASE + d + '/' + urllib.parse.quote(os.path.basename(p))
            rows.append('  <url><loc>%s</loc><lastmod>%s</lastmod></url>' % (u, today))
    io.open('sitemap.xml', 'w', encoding='utf-8', newline='\n').write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + '\n'.join(rows) + '\n</urlset>\n')
    print('sitemap.xml: %d URL' % len(rows))


if __name__ == '__main__':
    with local_server(ROOT) as host:
        print('一時サーバ:', host)
        main(host)
    wire()
    print('\n完了。busho/ と skill/ は生成物なので手で編集しないこと。')
