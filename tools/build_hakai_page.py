# -*- coding: utf-8 -*-
"""「破壊」が100%以上あるスキルの一覧ページ(skills-hakai.html)を正本から作る。

なぜ生成にするか:
既存の一覧ページは **ページの中にスキルの配列を手で持っている。** そのため
正本を直しても一覧だけ古い値が残る事故が起きていた(2026-08-28、監査 S-22 で
火槍猛進と朝曇ノ明麗の食い違いが見つかった)。このページは
`data/skill/*.json` から丸ごと組み立てるので、ずれようがない。

    python tools/build_hakai_page.py            # 中身を表示するだけ
    python tools/build_hakai_page.py --write    # skills-hakai.html を書き出す

拾い方(2026-08-29 うぐさんのご依頼):
  ・効果文の「破壊 N%上昇」を読む。書き方が
    「破壊N%」「破壊がN%」「破壊: N%」「破壊：N%」「破壊+N%」「破壊力N%」
    と揺れているので全部拾う。
  ・**「敵軍の破壊効果を25%低下」のような下げる効果は入れない。**
  ・LV10 が100%以上のもの。LV10 に破壊が無くても鍛錬(TR)で100%以上に
    なるものは載せる(炎統極刃。LV10には破壊が無く TR3 から付く)。
"""
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NL = chr(10)
OUT = os.path.join(ROOT, "skills-hakai.html")
PAGE_VERSION = "v2026-08-29.1"

# 「破壊」+数値% を拾う。前後の書き方の揺れをすべて吸収する
PAT = re.compile(r"破壊(?:力|効果)?\s*[:：+]?\s*(?:が\s*)?([\d.]+)\s*[%％]")
# 敵の破壊を下げる効果。これは「破壊力がある」ではないので除く
DOWN = re.compile(r"破壊(?:力|効果)?[^。]{0,14}低下")

DIR2PAGE = {
    "busho": "characters.html",
    "busho-parallel": "characters-parallel.html",
    "busho-ketsu": "characters-ketsu.html",
    "busho-kyoku": "characters-kyoku.html",
    "busho-kyoku-ps": "characters-kyoku-ps.html",
    "busho-toku": "characters-toku.html",
    "busho-toku-s": "characters-toku-s.html",
    "busho-ue": "characters-ue.html",
    "busho-jo": "characters-jo.html",
    "busho-do": "characters-do.html",
}
LEVELS = ["LV10", "TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]


def hakai_of(text):
    """効果文から破壊の値を取り出す。無ければ None。"""
    if not text:
        return None
    best = None
    for m in PAT.finditer(text):
        near = text[max(0, m.start() - 16):m.end() + 10]
        if DOWN.search(near) or "上昇" not in near:
            continue
        v = float(m.group(1))
        if best is None or v > best:
            best = v
    return best


def card_pages():
    """カードNo. → 武将データベースのページ名"""
    out = {}
    for f in glob.glob(os.path.join(ROOT, "data", "busho*", "*.json")):
        d = os.path.basename(os.path.dirname(f))
        out[os.path.basename(f)[:-5]] = DIR2PAGE.get(d, "characters.html")
    return out


def collect():
    pages = card_pages()
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "skill", "*.json"))):
        j = json.load(io.open(f, encoding="utf-8"))
        eff = {}
        for r in (j.get("trTable") or []):
            if r.get("level") and r.get("effect"):
                eff[r["level"]] = r["effect"]
        v10 = hakai_of(eff.get("LV10", ""))
        best, best_lv = None, ""
        for lv in LEVELS:
            v = hakai_of(eff.get(lv, ""))
            if v is not None and (best is None or v > best):
                best, best_lv = v, lv
        if best is None or best < 100:
            continue
        holders = []
        for s in (j.get("sourceCharacters") or []):
            no = str(s.get("no") or "")
            holders.append({"name": s.get("name") or "", "no": no,
                            "slot": s.get("slot") or "",
                            "page": pages.get(no, "characters.html")})
        rows.append({
            "name": j["name"],
            "rank": j.get("rank") or "-",
            "rate": j.get("baseRate"),
            "target": j.get("target") or "全",
            "hakai": v10,
            "hakaiMax": best,
            "hakaiMaxLv": best_lv,
            "effect": eff.get("LV10", ""),
            "holders": holders,
        })
    # 破壊(LV10)の大きい順。LV10に破壊が無いものは最後に回す
    rows.sort(key=lambda r: (-(r["hakai"] or -1), -r["hakaiMax"], r["name"]))
    return rows


HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-7334304987274613"
     crossorigin="anonymous"></script>
<title>破壊力一覧(100%以上)｜戦国IXA シミュレーター置き場</title>
<meta name="description" content="戦国IXA(Sengoku IXA)で破壊効果が100%以上あるスキルの一覧。発動率・対象兵科・破壊の上昇量・入手できる武将を比較できる非公式ファンメイドツール。">
<link rel="stylesheet" href="assets/css/site.css">
<style>
  body{background:#ffffff;}
  .cs-table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .cs-table{width:100%;border-collapse:collapse;font-size:13px;}
  .cs-table th,.cs-table td{border:1px solid var(--line);padding:9px 12px;text-align:center;white-space:nowrap;}
  .cs-table th{background:var(--bg-panel-2);color:var(--muted);font-weight:700;}
  .cs-table td:first-child{text-align:left;font-family:'Noto Serif JP',serif;font-weight:700;}
  .cs-table td.cs-col-effect{text-align:left;white-space:normal;min-width:260px;}
  .cs-col-hakai{font-weight:700;color:#c0392b;}
  .cs-note{color:var(--muted);font-size:12px;line-height:1.7;margin:0 0 14px;}
  .cs-search{margin-bottom:14px;}
  .cs-search input{
    width:100%;font-family:'Zen Kaku Gothic New',sans-serif;font-size:15px;padding:11px 14px;
    background:#fff;border:1px solid var(--line);color:var(--paper);border-radius:5px;
  }
  .cs-search input:focus{outline:none;border-color:var(--gold);}
  .s2-hide-filter{margin-bottom:14px;}
  .s2-hide-toggle{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;color:var(--muted);cursor:pointer;user-select:none;}
  .s2-hide-toggle input{width:16px;height:16px;cursor:pointer;accent-color:var(--gold);}
  @media (max-width:600px){
    .cs-table{font-size:12px;}
    .cs-table th,.cs-table td{padding:6px 8px;}
  }
  .sim-version-badge{
    position:fixed; bottom:6px; right:8px; z-index:9999;
    font-family:'JetBrains Mono',monospace; font-size:10px; color:#9a9382;
    background:rgba(255,255,255,0.85); padding:2px 7px; border-radius:4px;
    border:1px solid var(--line); pointer-events:none; user-select:text;
  }
</style>
</head>
<body>
<div class="site-layout">
  <aside class="site-sidebar" id="siteSidebar">
    <div class="sidebar-header">
      <span class="sidebar-title">メニュー</span>
    </div>
    <nav class="sidebar-nav">
      <a href="index.html">シミュレーター一覧</a>
      <a href="attack-simulator.html">攻撃/防御シミュレーター</a>
      <a href="gacha-simulator.html">金くじシミュレーター</a>
      <a href="characters-hub.html">武将データベース</a>
      <a href="skills-hub.html">スキル一覧</a>
      <a href="blog.html">記事</a>
      <a href="privacy.html">プライバシーポリシー</a>
    </nav>
  </aside>
  <div class="site-content">
  <header class="site-header">
    <a class="site-logo" href="index.html">戦国IXA シミュレーター置き場</a>
  </header>

  <main class="site-main">
    <h1 class="page-title">破壊力一覧(100%以上)</h1>
    <p class="cs-note">
      拠点や砦への「破壊」効果が <strong>100%以上</strong> あるスキルの一覧です。LV10 の破壊が大きい順に並べています。<br>
      ランクが A 以下のスキルも、破壊が100%以上あるものはこの一覧に載せ、個別ページも用意しています。<br>
      <strong>「敵軍の破壊効果を低下させる」スキルは入っていません。</strong>破壊を上げるものだけを集めています。<br>
      LV10 に破壊が無く、鍛錬(TR)で初めて破壊が付くスキルは表の最後に置き、LV10 の欄を「-」にしています。
    </p>

    <div class="cs-search">
      <input type="text" id="hkSearchBox" placeholder="スキル名または武将名で検索(例: 梵天浄界、安土城 など)">
    </div>

    <div class="s2-hide-filter">
      <label class="s2-hide-toggle"><input type="checkbox" id="hkHideS2Toggle">S2で入手できる武将名を非表示</label>
    </div>

    <div class="cs-table-scroll">
      <table class="cs-table">
        <thead>
          <tr><th>スキル名</th><th>ランク</th><th>発動率</th><th>対象兵科</th><th>破壊(LV10)</th><th>破壊(鍛錬最大)</th><th>主効果(LV10時)</th><th>入手可能な武将</th></tr>
        </thead>
        <tbody id="hkList">
"""

TAIL_1 = """</tbody>
      </table>
    </div>
  </main>

  <footer class="site-footer">
    <p>本サイトは戦国IXA(Sengoku IXA)の非公式ファンメイドツールです。運営会社・関連団体とは一切関係ありません。<br>
    記載されている会社名・製品名・システム名などは、各社の商標、または登録商標です。<br>
    &copy; SQUARE ENIX</p>
    <p><a href="privacy.html">プライバシーポリシー</a></p>
  </footer>
  <div class="sim-version-badge" id="simVersionBadge">v-</div>
  <div id="pageComments"></div>
  </div>
</div>
"""


JS = """<script>
  const HK_VERSION = '__VERSION__';
  document.addEventListener('DOMContentLoaded', ()=>{
    const badge = document.getElementById('simVersionBadge');
    if(badge) badge.textContent = HK_VERSION;
  });

  /* ============ 破壊力一覧(100%以上) ============
     **この配列は tools/build_hakai_page.py が data/skill/*.json から
     作っています。手で書き換えないこと。** 正本を直したら
     python tools/build_hakai_page.py --write を回すこと。
  ============================================ */
  const hakaiSkills = __ARRAY__;

  function hkEscapeHtml(s){
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;');
  }

  // スキルページのファイル名は空白を _ に置き換えてある(skills.html と同じ規則)
  function hkPageName(n){
    return encodeURIComponent(String(n).replace(/[ \\u3000]/g, '_'));
  }

  let hkHideS2 = false;

  function hkPct(v){ return (v === null || v === undefined) ? '-' : (v + '%'); }

  function hkMatches(s, q){
    if(!q) return true;
    if(s.name.toLowerCase().indexOf(q) >= 0) return true;
    return s.holders.some(function(h){
      return h.name.toLowerCase().indexOf(q) >= 0 || h.no.indexOf(q) >= 0;
    });
  }

  function hkRender(){
    const box = document.getElementById('hkSearchBox');
    const q = box ? box.value.trim().toLowerCase() : '';
    const body = document.getElementById('hkList');
    if(!body) return;
    body.innerHTML = hakaiSkills.filter(function(s){ return hkMatches(s, q); }).map(function(s){
      const hs = s.holders.filter(function(h){ return !(hkHideS2 && String(h.slot) === 'S2'); });
      const who = hs.length ? hs.map(function(h){
        return '<a href="' + h.page + '#' + encodeURIComponent(h.no) + '">'
             + hkEscapeHtml(h.name) + '(' + hkEscapeHtml(h.slot) + ')</a>';
      }).join('<br>') : '-';
      const mx = (s.hakai !== null && s.hakaiMax === s.hakai)
        ? '-' : (hkPct(s.hakaiMax) + '(' + s.hakaiMaxLv + ')');
      return '<tr>'
        + '<td><a href="skill/' + hkPageName(s.name) + '.html">' + hkEscapeHtml(s.name) + '</a></td>'
        + '<td>' + hkEscapeHtml(s.rank) + '</td>'
        + '<td>' + (s.rate === null || s.rate === undefined ? '-' : s.rate + '%') + '</td>'
        + '<td>' + hkEscapeHtml(s.target) + '</td>'
        + '<td class="cs-col-hakai">' + hkPct(s.hakai) + '</td>'
        + '<td>' + mx + '</td>'
        + '<td class="cs-col-effect">' + hkEscapeHtml(s.effect) + '</td>'
        + '<td style="white-space:normal;">' + who + '</td>'
        + '</tr>';
    }).join('');
  }

  const hkBox = document.getElementById('hkSearchBox');
  if(hkBox) hkBox.addEventListener('input', hkRender);
  const hkTgl = document.getElementById('hkHideS2Toggle');
  if(hkTgl) hkTgl.addEventListener('change', function(){ hkHideS2 = this.checked; hkRender(); });

  hkRender();
</script>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script src="assets/js/comments-widget.js"></script>
<script>renderCommentsWidget('pageComments', 'page:skills-hakai');</script>
<script src="assets/js/site-sidebar.js"></script>
</body>
</html>"""


def js_block(rows):
    """ページに埋め込むJS。配列は正本から作ったものをそのまま置く。"""
    arr = json.dumps(rows, ensure_ascii=False, indent=1)
    return JS.replace("__VERSION__", PAGE_VERSION).replace("__ARRAY__", arr)


def main(argv):
    rows = collect()
    print("破壊が100%%以上あるスキル: %d件" % len(rows))
    for r in rows:
        v = "-" if r["hakai"] is None else ("%g%%" % r["hakai"])
        mx = "" if (r["hakai"] is not None and r["hakaiMax"] == r["hakai"]) \
            else ("  鍛錬最大 %g%%(%s)" % (r["hakaiMax"], r["hakaiMaxLv"]))
        print("  %-8s %-16s [%-3s] 持ち主%2d体%s"
              % (v, r["name"], r["rank"], len(r["holders"]), mx))
    if "--write" not in argv:
        print()
        print("(まだ書いていない。--write で skills-hakai.html を書き出す)")
        return 0
    body = HEAD + NL + TAIL_1 + js_block(rows) + NL
    io.open(OUT, "w", encoding="utf-8", newline=NL).write(body)
    print()
    print("skills-hakai.html を書き出した(%d行)" % body.count(NL))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
