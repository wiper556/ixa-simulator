# -*- coding: utf-8 -*-
"""データページの中身を静的HTMLとして書き出す(SEO/クローラー対策)。

■なぜ必要か
characters.html は 350KB あるが、静的HTMLとしての本文はナビとフッターだけで271文字しかない。
武将177体もスキルも全部JSの配列に入っていてクライアント側で描画しているため、
JSを実行しない読み取り方をされると「中身が無いページ」に見える。
AdSenseから「有用性の低いコンテンツ」の指摘を受けた一因と考えられる。

■方針
1. 描画ロジックをPythonで再実装しない。**ページ自身のJSをヘッドレスブラウザで実行して
   生成されたHTMLを取り出す。** 表示と必ず一致し、描画関数を直しても自動で追従する。
2. **クローラーにだけ見せて利用者に隠す(display:none等)ことは絶対にしない。**
   隠しテキストはクローキング扱いで、AdSense的にはむしろ悪化する。
   一覧は既存コンテナに出すのでJSが同内容で上書きするだけ(見た目不変)。
   詳細は「全武将のスキル詳細」という実際に見えるセクションとして追加する
   (通常は1体ずつしか見られない情報なので、画面の重複ではなく新しい情報になる)。
3. マーカーコメントで囲んで書き込むので、何度実行しても差し替わるだけ。

■使い方
    python tools/prerender.py            # 全対象ページを再生成
    python tools/prerender.py characters.html
**データを更新したら必ず実行すること。** 実行しないと静的HTMLだけ古い内容が残る。
"""
import re, sys, io, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
BEGIN = "<!-- PRERENDER:%s:start ここから下は tools/prerender.py が自動生成しています。直接編集しないこと -->"
END = "<!-- PRERENDER:%s:end -->"

# ページごとの取り出し方。
#   list_js   … 一覧HTMLを返すJS(既存コンテナに埋め戻す。JSが同内容で上書きする)
#   detail_js … 全件の詳細HTMLを返すJS(新しい可視セクションとして追加する)
TARGETS = {
    "characters.html": {
        "container": "gdbList",
        "list_js": "() => { gdbRenderList(gdbSortByChapterThenNo(generals.slice())); "
                   "return document.getElementById('gdbList').innerHTML; }",
    },
    "characters-kyoku.html": {
        "container": "kkList",
        "list_js": "() => { kkRenderList(kkSortByChapterThenNo(kyokuGenerals.slice())); "
                   "return document.getElementById('kkList').innerHTML; }",
    },
    "characters-kyoku-ps.html": {
        "container": "kpList",
        "list_js": "() => { kpRenderList(kpSortByChapterThenNo(kyokuPsGenerals.slice())); "
                   "return document.getElementById('kpList').innerHTML; }",
    },
    "characters-parallel.html": {
        "container": "tpList",
        "list_js": "() => { tpRenderList(tpSortByChapterThenNo(parallelGenerals.slice())); "
                   "return document.getElementById('tpList').innerHTML; }",
    },
    "characters-toku-s.html": {
        "container": "tsList",
        "list_js": "() => { tsRenderList(tsSortByChapterThenNo(tokuSecretGenerals.slice())); "
                   "return document.getElementById('tsList').innerHTML; }",
    },
    "characters-toku.html": {
        "container": "tkList",
        "list_js": "() => { tkRenderList(tkSortByChapterThenNo(tokuGenerals.slice())); "
                   "return document.getElementById('tkList').innerHTML; }",
    },
    # 上(じょう)と序(じょ)はローマ字が jou/jo でほぼ同じになるので、
    # 接頭辞は上=ue(訓読み)、序=jo にして見分けが付くようにしている。
    "characters-ue.html": {
        "container": "ueList",
        "list_js": "() => { ueRenderList(ueSortByChapterThenNo(ueGenerals.slice())); "
                   "return document.getElementById('ueList').innerHTML; }",
    },
    "characters-jo.html": {
        "container": "joList",
        "list_js": "() => { joRenderList(joSortByChapterThenNo(joGenerals.slice())); "
                   "return document.getElementById('joList').innerHTML; }",
    },
    "characters-do.html": {
        "container": "joList",
        "list_js": "() => { doRenderList(doSortByChapterThenNo(doGenerals.slice())); "
                   "return document.getElementById('joList').innerHTML; }",
    },
}

# スキル一覧系は「ページを開くとJSがtbodyを埋める」だけの単純な構造なので、
# 読み込み後のコンテナのinnerHTMLをそのまま静的HTMLとして埋め戻せばよい。
# (詳細パネルのような別セクションは持たないため list だけ)
SIMPLE = {
    # skills.html の詳細(sklRenderDetail)は他ページを実行時fetchして
    # 「このスキルを持つ武将」を組み立てる作りのため事前描画に向かない。一覧のみ対象。
    "skills.html": ["sklList"],
    "skills-cost-atk.html": ["csList"],
    "skills-cost-def.html": ["csList"],
    "skills-count-atk.html": ["scList"],
    "skills-count-def.html": ["scList"],
    "skills-fukutsu.html": ["fkList"],
    "skills-hadou.html": ["hdList"],
    "skills-hakai.html": ["hkList"],
    "skills-heitan.html": ["htList"],
    "skills-higai.html": ["hgList"],
    "skills-hishou-atk.html": ["hsList"],
    "skills-hishou-def.html": ["hsList"],
    "skills-leadermimic.html": ["lmList", "lmNoList"],
    "skills-mimic.html": ["mmList"],
    "skills-mujin-atk.html": ["mjList"],
    "skills-mujin-def.html": ["mjList"],
    "skills-taitai.html": ["ttList"],
    "skills-takuetsu.html": ["tkList", "tkRateList"],
}

STATIC_CSS_MARK = ".pr-static{"


def find_container_html(page, cid):
    return page.evaluate("id => { const e=document.getElementById(id); return e ? e.innerHTML : null; }", cid)


def splice(text, key, payload):
    """マーカーで囲まれた領域を差し替える。無ければ None を返す。"""
    b, e = BEGIN % key, END % key
    i, j = text.find(b), text.find(e)
    if i < 0 or j < 0:
        return None
    return text[:i] + b + "\n" + payload + "\n" + text[j:]


def run_simple(br, name, containers):
    """描画後のコンテナ内容をそのまま埋め戻すだけの単純なページ用。"""
    path = ROOT / name
    src = path.read_text(encoding="utf-8")
    pg = br.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    # 2026-08-26: playwright の既定は30秒。データが増えて ixa-data.js が
    # 1.2MB を超え、CI(GitHub Actions)で読み込みが30秒に間に合わず落ちた。
    # gen_detail_pages.py は 2026-08-23 に伸ばしたが、こちらを見落としていた。
    pg.goto(path.as_uri(), timeout=120000)
    pg.wait_for_timeout(1800)
    real = [e for e in errs if "supabase" not in e.lower()]
    if real:
        pg.close()
        return "★%s JSエラーのため中止: %s" % (name, real[:1])
    total = 0
    for cid in containers:
        html = pg.evaluate("id => { const e=document.getElementById(id); return e ? e.innerHTML : null; }", cid)
        if html is None:
            pg.close()
            return "★%s コンテナ #%s が無い" % (name, cid)
        html = re.sub(r'<img (?![^>]*loading=)', '<img loading="lazy" decoding="async" ', html)
        key = "list:" + cid
        spliced = splice(src, key, html)
        if spliced is not None:
            src = spliced
        else:
            pat = re.compile(r'(<(?:tbody|div) id="%s"[^>]*>)([\s\S]*?)(</(?:tbody|div)>)' % re.escape(cid))
            m = pat.search(src)
            if not m:
                pg.close()
                return "★%s #%s の埋め込み先を特定できない" % (name, cid)
            src = (src[:m.start(2)] + "\n" + (BEGIN % key) + "\n" + html
                   + "\n" + (END % key) + "\n" + src[m.end(2):])
        total += len(html)
    pg.close()
    path.write_text(src, encoding="utf-8", newline="")
    return "%-26s %d箇所 計%6d字 を書き出し" % (name, len(containers), total)


def run(page_names):
    log = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        for name in page_names:
            if name in SIMPLE:
                log.append(run_simple(br, name, SIMPLE[name]))
                continue
            cfg = TARGETS[name]
            path = ROOT / name
            src = path.read_text(encoding="utf-8")
            pg = br.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            # 同上(2026-08-26)
            pg.goto(path.as_uri(), timeout=120000)
            pg.wait_for_timeout(2200)
            if errs:
                log.append("★%s JSエラーのため中止: %s" % (name, errs[:1]))
                pg.close()
                continue
            list_html = pg.evaluate(cfg["list_js"])
            detail_html = pg.evaluate(cfg["detail_js"]) if cfg.get("detail_js") else None
            pg.close()

            # 静的セクションは全武将分の画像を含むため、そのままだと初回表示で100枚以上を
            # 一斉に読みに行ってしまう。遅延読み込みを付ける(表示位置に来るまで読まない)。
            if detail_html is not None:
                detail_html = re.sub(r'<img (?![^>]*loading=)',
                                     '<img loading="lazy" decoding="async" ', detail_html)

            # 1) 一覧: 既存コンテナの中に埋める(JSが起動すると同じ内容で上書きされる)。
            #    2回目以降はマーカー間を差し替えるだけにして、実行のたびにマーカーが増えないようにする。
            cid = cfg["container"]
            spliced = splice(src, "list", list_html)
            if spliced is not None:
                src = spliced
            else:
                pat = re.compile(r'(<div id="%s"[^>]*>)([\s\S]*?)(</div>)' % re.escape(cid))
                m = pat.search(src)
                if not m:
                    log.append("★%s コンテナ #%s が見つからない" % (name, cid))
                    continue
                src = (src[:m.start(2)] + "\n" + (BEGIN % "list") + "\n" + list_html
                       + "\n" + (END % "list") + "\n" + src[m.end(2):])

            # 2) 詳細: detail_js を持つページだけ、可視セクションとして追加/差し替えする。
            #    武将データベースは一覧だけを見たいというユーザー判断(2026-08-10)で detail を外した。
            if detail_html is None:
                path.write_text(src, encoding="utf-8", newline="")
                log.append("%-26s 一覧%6d字 を書き出し" % (name, len(list_html)))
                continue
            section = ('<section class="pr-static" id="prStatic">\n'
                       '  <h2>%s</h2>\n  <p class="pr-lead">%s</p>\n%s\n</section>'
                       % (cfg["detail_heading"], cfg["detail_lead"], detail_html))
            replaced = splice(src, "detail", section)
            if replaced is None:
                anchor = src.rindex("  </main>")
                src = src[:anchor] + "    " + (BEGIN % "detail") + "\n" + section + "\n    " + (END % "detail") + "\n" + src[anchor:]
            else:
                src = replaced

            path.write_text(src, encoding="utf-8", newline="")
            log.append("%-26s 一覧%6d字 / 詳細%7d字 を書き出し" % (name, len(list_html), len(detail_html)))
        br.close()
    return log


def check(page_names):
    """静的HTMLが最新データと食い違っていないかだけを調べる(書き換えない)。

    データを更新したのに prerender を流し忘れると、画面は新しいのに静的HTMLだけ
    古い、という状態になる。コミット前にこれを実行すれば検知できる。
    """
    import shutil, tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    stale, log = [], []
    for n in page_names:
        shutil.copy2(ROOT / n, tmp / n)
    run(page_names)  # いったん書き出す
    for n in page_names:
        if (ROOT / n).read_bytes() != (tmp / n).read_bytes():
            stale.append(n)
        shutil.copy2(tmp / n, ROOT / n)  # 元に戻す
    shutil.rmtree(tmp, ignore_errors=True)
    if stale:
        log.append("★静的HTMLが古くなっています。`python tools/prerender.py` を実行してください:")
        for n in stale:
            log.append("   - " + n)
    else:
        log.append("静的HTMLは最新データと一致しています(%d ページ)" % len(page_names))
    return log, stale


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--check"]
    names = args or (list(TARGETS) + list(SIMPLE))
    if "--check" in sys.argv:
        lines, stale = check(names)
        for line in lines:
            print(line)
        sys.exit(1 if stale else 0)
    for line in run(names):
        print(line)
