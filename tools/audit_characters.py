# -*- coding: utf-8 -*-
"""武将・スキルデータの全件監査ツール(2026-08-07作成)

サイト内のデータ同士の矛盾(内部整合性)と、ixanary.com の一次情報源との
食い違い(外部照合)を、推測を挟まず機械的に洗い出す。

使い方:
    python tools/audit_characters.py            # 内部整合性のみ(オフライン・数秒)
    python tools/audit_characters.py --online   # 外部照合も行う(初回は数分、以降キャッシュ)

結果は tools/audit_out/ に出力される。

■外部照合の原理(最重要・docs/character-registration-manual.md A-3-0 と対応)
ixanary のスキル個別ページ(/skills/{スキル名}/)の合成テーブルは 1次/2次/3次 の世代構造で、
  武将Cのslot Sの skill      = Cの「初期スキル」のページの 1次・slot S
  武将Cのslot Sの afterSkill = 同ページの 2次・slot S
と1対1で対応する。参照先は候補スキル自身のページではなく **武将の初期スキルのページ** である点に注意
(武将のカードページには合成テーブルが載っていない)。

ステータスは /cards/{No}/ の成長表から照合する。ただし ixanary 側にも誤りがあるため
(例: No.1213 のコスト)、不一致が出た場合はカード画像を含む複数ソースで individual に判断すること。
"""
import json, io, re, os, sys, time, collections, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from extract_data import TARGETS as DATA_TARGETS  # noqa: E402

OUT = os.path.join(ROOT, "tools", "audit_out")
CACHE_SKILL = os.path.join(OUT, "cache_skill")
CACHE_CARD = os.path.join(OUT, "cache_card")
for d in (OUT, CACHE_SKILL, CACHE_CARD):
    if not os.path.isdir(d):
        os.makedirs(d)
ONLINE = "--online" in sys.argv


# ---------------- データ抽出 ----------------
# ページのJSはブラウザ前提なので、評価するための最小限の受け皿。
# 触られても落ちないように、何を呼んでも自分を返すだけの物を渡す。
_JS_STUB = """
var __noop = function(){ return __stub; };
var __stub = new Proxy(function(){}, {
  get: function(t, k){ if (k === Symbol.toPrimitive) return function(){ return ""; };
                       return __stub; },
  set: function(){ return true; },
  apply: function(){ return __stub; },
  construct: function(){ return __stub; }
});
var document = __stub, window = __stub, navigator = __stub, location = __stub;
var localStorage = __stub, sessionStorage = __stub, fetch = __noop, console = __stub;
var setTimeout = __noop, setInterval = __noop, requestAnimationFrame = __noop;
var addEventListener = __noop, alert = __noop, history = __stub, screen = __stub;
"""


def extract_array(path, varname):
    """HTML内のJS配列を、**そのページを実行したときの値**として取り出す。

    N-1(2026-08-13 第4回レッドチーム): 以前は `const generals = [ … ]` の
    **リテラル部分だけ**を切り出して評価していた。ページが実際に描画に使うのは
    変数の実行時の値なので、リテラルの後ろに1行足すだけで両者を食い違わせられた。

        ];
        generals.forEach(function(g){ if (g.no === '1310') { g.approved = true; } });

    これで監査からは「130体・赤丸118」に見えるのに、ページ上は「131体・赤丸120」。
    データを見る検査(赤丸の増分・錠前の母集団・rules)は全部この関数を通っているので、
    一箇所で全部同時に盲目になった。「赤丸はユーザーが明言したときだけ」も1行で迂回された。

    そこで、宣言を含む script を**丸ごと評価してから**変数を読む。
    後から書き換えられていれば、その結果が取れる。
    """
    from quickjs import Context
    with io.open(path, encoding="utf-8") as f:
        html = f.read()

    blocks = [m.group(2) for m in
              re.finditer(r"<script([^>]*)>([\s\S]*?)</script>", html)
              if "src=" not in m.group(1)
              and "json" not in (m.group(1) or "").lower()]

    # 宣言を探すときはコメントを消した文字列で位置を決める(H-6)。
    decl = re.compile(r"(?:const|let|var)\s+" + re.escape(varname) + r"\s*=")
    target, at = None, None
    for i, s in enumerate(blocks):
        masked = re.sub(r"/\*[\s\S]*?\*/", lambda x: " " * len(x.group(0)), s)
        masked = re.sub(r"(?m)//[^\n]*", lambda x: " " * len(x.group(0)), masked)
        m = decl.search(masked)
        if m:
            target, at = i, m.span()
            break
    if target is None:
        raise RuntimeError(varname + " not found in " + path)

    # 宣言を globalThis への代入に書き換える。こうしておけば、
    # そのブロックの後半(描画処理など)が落ちても、代入済みの値は残る。
    # ページのJSはブラウザ前提なので途中で落ちるのが普通(別ファイルの関数を呼ぶ等)。
    src = blocks[target]
    src = src[:at[0]] + ("globalThis.%s =" % varname) + src[at[1]:]

    ctx = Context()
    ctx.eval(_JS_STUB)
    for s in blocks[:target] + [src]:
        try:
            ctx.eval(s)
        except Exception:
            # 落ちた後ろに書いてある改変は拾えないが、それは check_js が別途見る。
            continue
    try:
        out = ctx.eval("JSON.stringify(globalThis.%s)" % varname)
    except Exception as e:
        raise RuntimeError("%s の評価に失敗(%s): %s" % (varname, path, e))
    if not out or out == "undefined":
        raise RuntimeError("%s が評価後に取れない: %s" % (varname, path))
    return json.loads(out)


def _drop_notes(x):
    """出典の記録(notes / note)は監査の対象データではないので落とす。"""
    if isinstance(x, dict):
        return {k: _drop_notes(v) for k, v in x.items() if k not in ("notes", "note")}
    if isinstance(x, list):
        return [_drop_notes(v) for v in x]
    return x


def _page_matches(page_val, src_val):
    """ページに**載っている**値が、正本と同じかどうか。

     ・ページに無いものは見ない  … 一覧に載せないフィールドは正本にだけある
     ・ページに余分にあるものは不一致 … 実行時に足された/手で書き足した の形
    """
    if isinstance(page_val, dict):
        return (isinstance(src_val, dict)
                and all(k in src_val and _page_matches(v, src_val[k])
                        for k, v in page_val.items()))
    if isinstance(page_val, list):
        return (isinstance(src_val, list) and len(page_val) == len(src_val)
                and all(_page_matches(a, b) for a, b in zip(page_val, src_val)))
    return page_val == src_val


def load_source(page, array):
    """正本(data/)を読み、ページの配列が正本と食い違っていないかも見る。

    2026-08-14: 一覧ページには**一覧に要る分しか置かなくなった**
    (build_data.LIST_FIELDS)。鍛錬表も合成表もページ上には無いので、
    監査する中身は正本 data/ から取る。

    ただし、それだけにすると N-1(第4回レッドチーム)で塞いだ穴が開き直る。
    N-1 は「配列の後ろに1行足して実行時に値を書き換える」手口だった:

        ];
        generals.forEach(function(g){ if (g.no === '1310') { g.approved = true; } });

    正本しか見なければ、ページ上だけ赤丸が増えていても監査は気づかない。
    そこで **ページを実行した結果を、正本と突き合わせる**。

    比べ方は「ページに載っている値が正本と同じか」。何を載せるかの決定
    (build_data.LIST_FIELDS)には踏み込まない。載せる/載せないの取り違えは
    tools/check_generated.py が別に見る(正本から作り直して差分を取る)ので、
    ここで二重に持つと、絞り込みを変えるたびに両方直すことになる。

    返り値: (正本の全データ, 食い違いの説明)
    """
    from build_data import current_order, derived, load_entries
    outdir, keyfld = {a: (d, k) for _p, a, d, k in DATA_TARGETS}[array]
    path = os.path.join(ROOT, page)
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    full = [_drop_notes(e) for e in
            load_entries(outdir, keyfld, current_order(text, array, keyfld))]
    # 正本に持たせず No. から出している値(極の種別など)。
    # ページ側にはこれが載るので、比べる相手にも同じ規則で足しておく。
    # 手で書き換えられていれば、ここで食い違いとして出る。
    for e in full:
        e.update(derived(e, array))

    bad = []
    live = extract_array(path, array)
    if len(live) != len(full):
        bad.append("%s の %s は実行後 %d件、正本(%s)は %d件"
                   % (page, array, len(live), outdir, len(full)))
    else:
        for src, one in zip(full, live):
            if not _page_matches(one, src):
                bad.append("%s の %s「%s」がページ上で正本と違う値になっている"
                           % (page, array, src.get(keyfld)))
    return full, bad


def load():
    p = lambda n: os.path.join(ROOT, n)
    d, tamper = {}, []
    for page, array in (("characters.html", "generals"),
                        ("characters-kyoku.html", "kyokuGenerals"),
                        ("characters-kyoku-ps.html", "kyokuPsGenerals"),
                        ("characters-parallel.html", "parallelGenerals"),
                        ("characters-toku-s.html", "tokuSecretGenerals"),
                        ("characters-toku.html", "tokuGenerals"),
                        ("characters-ue.html", "ueGenerals"),
                        ("characters-jo.html", "joGenerals"),
                        ("characters-do.html", "doGenerals"),
                        # 傑は少数だが sourceCharacters の db 判定(S-07)に要る
                        ("characters-ketsu.html", "ketsuGenerals"),
                        ("skills.html", "skills")):
        d[array], bad = load_source(page, array)
        tamper += bad
    d["tamper"] = tamper
    # 2026-08-14: 極は「通常極」と「プラチナ+シークレット」の2ページに分かれた。
    # 検査の中身は極かどうかで決まり、どちらのページに載っているかは関係ないので、
    # 以降はまとめた kyokuAll を使う。ページごとに見るのは改変の突き合わせだけ。
    d["kyokuAll"] = d["kyokuGenerals"] + d["kyokuPsGenerals"]
    # 2026-08-16: 天パラレルと特シークレットを足したとき、ここに入れ忘れると
    # 逆引き検査(chars)と重複検査(all_g)の対象から静かに外れる(担当P1の指摘)。
    d["extraAll"] = (d["parallelGenerals"] + d["tokuSecretGenerals"]
                     + d["tokuGenerals"] + d["ueGenerals"] + d["joGenerals"]
                     + d["doGenerals"])
    # LINKED_SKILLS はページが手で持っている配列(生成物ではない)ので、そのまま読む
    d["LINKED_SKILLS"] = extract_array(p("characters.html"), "LINKED_SKILLS")
    d["KK_LINKED_SKILLS"] = extract_array(p("characters-kyoku.html"), "KK_LINKED_SKILLS")
    d["KP_LINKED_SKILLS"] = extract_array(p("characters-kyoku-ps.html"),
                                          "KP_LINKED_SKILLS")
    # シミュレーター側(P-04)。JSファイルなので生テキストで持つ
    with io.open(p(os.path.join("assets", "js", "ixa-data.js")), encoding="utf-8") as f:
        d["ixaDataSrc"] = f.read()
    # 一覧ページ(S-06)。各ページが sourceCharacters を独自に複製している
    d["listPages"] = {}
    for n in sorted(os.listdir(ROOT)):
        if n.startswith("skills-") and n.endswith(".html"):
            with io.open(p(n), encoding="utf-8") as f:
                d["listPages"][n] = f.read()
    # 2026-08-27: くじのページも武将名を独自に持っている。
    # 誰も突き合わせておらず、483行が正本とずれていた。
    d["gachaPages"] = {}
    for n in sorted(os.listdir(ROOT)):
        if n.startswith("gacha-") and n.endswith(".html"):
            with io.open(p(n), encoding="utf-8") as f:
                d["gachaPages"][n] = f.read()
    return d


HAS_DATA = ("imageFull", "initialSkill", "atkBase", "skillDetail",
            "trTable", "synthesisTable")


def status(g):
    if g.get("approved"):
        return "赤丸"
    if g.get("reviewedOk"):
        return "黄丸"
    # 2026-08-13: 以前はここが `if g.get("imageFull")` だけだった。印を全件外した
    # ときに、画像だけ無い5体(10063/10065/10066/10067/10069)がまるごと監査から
    # 消えて発覚した。赤丸判定が先に当たっていたので今までは対象に入っていただけで、
    # **画像が無い武将は監査されない**という穴が最初から空いていた。
    # 中身(初期スキル・数値・表)が1つでもあるなら青丸として監査する。
    if any(g.get(k) for k in HAS_DATA):
        return "青丸"
    return "無印"


def rate_num(r):
    if not r:
        return None
    m = re.findall(r"(\d+(?:\.\d+)?)\s*%", str(r))
    return float(m[0]) if len(m) == 1 else None


def norm(s):
    return re.sub(r"[\s　]", "", s) if s else s


# ---------------- 取得(オンライン時のみ) ----------------
def _fetch(url, path):
    if os.path.exists(path):
        return io.open(path, encoding="utf-8", errors="replace").read()
    if not ONLINE:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        html = "FETCH_ERROR " + str(e)
    io.open(path, "w", encoding="utf-8").write(html)
    time.sleep(0.7)
    return html


def safe(s):
    return re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿]", "_", s)


def fetch_skill(name):
    return _fetch("https://ixanary.com/skills/" + urllib.parse.quote(name) + "/",
                  os.path.join(CACHE_SKILL, safe(name) + ".html"))


def fetch_card(no):
    return _fetch("https://ixanary.com/cards/" + no + "/",
                  os.path.join(CACHE_CARD, no + ".html"))


def parse_generations(html):
    """合成テーブルの 1次/2次 を {gen: {slot: (skill名, ランク)}} で返す"""
    if not html or html.startswith("FETCH_ERROR") or "Page Not Found" in html:
        return None
    tbl = re.search(r"合成テーブル([\s\S]{0,8000}?)(合成素材|開発)", html)
    if not tbl:
        return None
    res = {}
    for gen in ("1次", "2次"):
        m = re.search(r"<th>" + gen + r"</th>([\s\S]*?)</tr>", tbl.group(1))
        if not m:
            continue
        vals = []
        for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", m.group(1)):
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
            if not t:
                vals.append(None)
                continue
            t = re.sub(r"^[^：:]{0,6}[：:]", "", t).strip()
            mr = re.search(r"\s+(SSS|SS|S|A|B|C|D|E|F|X{1,3})$", t)
            vals.append((re.sub(r"\s+(SSS|SS|S|A|B|C|D|E|F|X{1,3})$", "", t).strip(),
                         mr.group(1) if mr else None))
        res[gen] = {s: vals[i] for i, s in enumerate(["A", "B", "C", "S1", "S2"]) if i < len(vals)}
    return res or None


# ---------------- 監査 ----------------
def main():
    D = load()
    skills = {s["name"]: s for s in D["skills"]}
    chars = ([(g, "天覇") for g in D["generals"]]
             + [(g, "極") for g in D["kyokuAll"]]
             + [(g, "天パラレル/特/上/序") for g in D["extraAll"]])
    targets = [(g, s) for g, s in chars if status(g) != "無印"]
    R = []
    # M-1(2026-08-13 第4回レッドチーム): ここは1行のラムダで、
    # 錠前はチェック名の文字列しか数えず、自己テストは固定の注入点でしか鳴らさない。
    # そこで「登録作業中の武将は対象外」に見える1行を足すだけで、
    # 特定武将の指摘を全種別まとめて握り潰せた(門番もCIも緑、出力も正常時と同一)。
    #
    # J-1(第4回): さらに、錠前が種別を集めるのは `add("名前"` という**テキスト**なので、
    # チェック本体を消して名前をコメントに残せば「消えていない」ことになった。
    #
    # 対処は2つに分けた。
    #  J-1 → 錠前と自己テストの種別収集を AST にする(コメントは構文木に出ない)
    #  M-1 → audit_characters.py 自身を錠前のハッシュ対象に入れる(配り口の改変が分かる)
    # ここは素直な関数のままにしておく。
    def add(cat, sev, msg):
        R.append((cat, sev, msg))

    # 公開されるページの値が、監査している正本と違う(N-1の再発防止。load_source参照)
    for m in D["tamper"]:
        add("ページの配列が正本と違う", "HIGH",
            m + " / 配列は data/ からの生成物。手で書き換えず "
                "`python tools/build_data.py` で作り直す")

    for n, c in collections.Counter([s["name"] for s in D["skills"]]).items():
        if c > 1:
            add("重複登録", "HIGH", "skills.html に同名スキルが複数: " + n)

    # 移植前/移植後の性能が各スキル自身の登録と食い違わないか
    for g, src in targets:
        st = status(g)
        for row in g.get("synthesisTable") or []:
            sk_, af = row.get("skill"), row.get("afterSkill")
            if sk_ in skills:
                b, rn = skills[sk_].get("baseRate"), rate_num(row.get("rate"))
                if rn is not None and isinstance(b, (int, float)) and abs(rn - b) > 0.01:
                    add("移植前の数値矛盾", "HIGH", "[%s] %s No.%s %s枠: %s の rate=%s / baseRate=%s" %
                        (st, g["name"], g["no"], row.get("slot"), sk_, row.get("rate"), b))
            if af and af in skills and af != sk_:
                a, arn = skills[af].get("baseRate"), rate_num(row.get("afterRate"))
                if row.get("afterRate") is None:
                    add("移植後の性能未設定", "HIGH", "[%s] %s No.%s %s枠: %s→%s の after* が未設定" %
                        (st, g["name"], g["no"], row.get("slot"), sk_, af))
                elif arn is not None and isinstance(a, (int, float)) and abs(arn - a) > 0.01:
                    add("移植後の数値矛盾", "HIGH", "[%s] %s No.%s %s枠: %s の afterRate=%s / baseRate=%s" %
                        (st, g["name"], g["no"], row.get("slot"), af, row.get("afterRate"), a))

    # 同じスキルが武将により別の afterSkill になっていないか
    mapping, where = collections.defaultdict(set), collections.defaultdict(list)
    for g, src in targets:
        for row in g.get("synthesisTable") or []:
            sk_, af = row.get("skill"), row.get("afterSkill")
            if sk_ and af:
                mapping[sk_].add(af)
                where[(sk_, af)].append("%s No.%s %s枠" % (g["name"], g["no"], row.get("slot")))
    for sk_, afs in sorted(mapping.items()):
        if len(afs) > 1:
            add("afterSkill不一致", "HIGH", "「%s」が武将により別スキルに化けている: %s" %
                (sk_, " / ".join("→%s(%s)" % (a, ", ".join(where[(sk_, a)])) for a in sorted(afs))))

    # 同じスキルが武将により別の発動確率になっていないか。
    # 2026-08-16: ixaixa.com由来の再調査で No.2402 森可成の覇王ノ守人が
    # 50%→57% と分かり、同じスキルを持つ他の武将を横断で見たら同種の
    # 食い違いが計4件あった。うち No.2432 は **LV1の確率にLV10の効果を
    # 組み合わせていた**(戦陣 一閃 30%/100% ← 正しくは 50%/100%)。
    # 1体ずつ見ていては気づけない類なので、横断で鳴らす。
    rates, rwhere = collections.defaultdict(set), collections.defaultdict(list)
    for g, src in targets:
        for row in g.get("synthesisTable") or []:
            sk_, rt = row.get("skill"), row.get("rate")
            if sk_ and rt:
                rates[sk_].add(rt)
                rwhere[(sk_, rt)].append("%s No.%s %s枠"
                                         % (g["name"], g["no"], row.get("slot")))
    for sk_, rs in sorted(rates.items()):
        if len(rs) > 1:
            add("確率が武将により違う", "HIGH",
                "「%s」の発動確率が武将によって違う: %s" %
                (sk_, " / ".join("%s(%s)" % (r, ", ".join(rwhere[(sk_, r)]))
                                 for r in sorted(rs))))

    # 同じレアリティの中に完全同名の武将がいないか。
    # 2026-08-16: 武将名の（N）は**レアリティごとの通し番号**だと分かった
    # (うぐさんがゲーム内で確認。天の1166 織田信長（4）【覇】と極の2595
    # 織田信長（4）は別レアリティなので両立する)。
    # したがって「天と極に同名」は正常だが、「同じレアリティに完全同名」は
    # 片方に番号が要る、という判定になる。
    # ixawiki の一覧はレアリティごと**かつ覇/非覇で別カウンタ**に振っており、
    # ゲーム内の番号とは別物なので根拠にできない。ここで鳴らして人に聞く。
    def _rarity(no):
        n = str(no or "")
        if len(n) == 5 and n[:2] in ("20", "21", "22"):
            return "傑"
        if len(n) == 4 and n[0] == "3":
            # 2026-08-23: 特とシークレット特を分けていなかった。実データは
            # busho-toku=3001〜3689 / busho-toku-s=3701〜3730 で、サイトの
            # データベースも別ページ。まとめていたので石川五右衛門
            # (3564=特 / 3701=シークレット特)が同名として鳴っていた。
            # 境目は regbuild.kyoku_dir と同じ 3700。
            return "特シークレット" if int(n) >= 3700 else "特"
        # 2026-08-16: 上は4桁の4始まり(No.4109〜4321 を実物のカードで確認)。
        # ここに無いと上武将が「天」に混ざり、同名判定が別レアリティ同士で鳴る。
        if len(n) == 4 and n[0] == "4":
            return "上"
        # 2026-08-16: 序は4桁の5始まり(No.5082〜5269 を実物のカードで確認)。
        if len(n) == 4 and n[0] == "5":
            return "序"
        # 2026-08-23: 童は4桁の1800番台(No.1801〜1856)。ここに無いと童が「天」に
        # 混ざり、同名判定が「天の吉法師が2枚」のように別レアリティ名で鳴る
        # (上を足したときと同じ穴。1804/1852 吉法師 と 1810/1851 茶々 で発覚)。
        if len(n) == 4 and 1800 <= int(n) <= 1899:
            return "童"
        if len(n) == 4 and n[0] in "27":
            return "極"
        return "天"

    # **パラレルは元カード(番号-30000)と同じ武将の別バージョン**なので、
    # 同名なのが正しい。番号を振ると元カードと見分けがつかなくなる。
    # 2026-08-16: パラレル12枚を登録したらこの検査が全部鳴った(検査側の穴)。
    same = collections.defaultdict(list)
    for g, src in targets:
        no = str(g.get("no") or "")
        if len(no) == 5 and no[0] in "34":
            continue
        if g.get("name") and g.get("no") is not None:
            same[(_rarity(g["no"]), g["name"])].append(no)
    for (rar, nm), nos in sorted(same.items()):
        if len(nos) > 1:
            add("同じレアリティに同名", "MID",
                "%s の「%s」が %d枚ある(No.%s)。どれかに（N）が要る。"
                "番号はゲーム内でしか分からないのでユーザーに聞くこと"
                % (rar, nm, len(nos), "、".join(sorted(nos))))

    # ownHiddenCandidate は武将側 afterSkill から導出できるはず
    for s in D["skills"]:
        exp = mapping.get(s["name"])
        ohc = (s.get("ownHiddenCandidate") or {}).get("skill")
        if exp and len(exp) == 1:
            e = list(exp)[0]
            if ohc and ohc != e:
                add("ownHiddenCandidate不整合", "HIGH", "「%s」: 登録→%s / 武将側→%s" % (s["name"], ohc, e))
            elif not ohc:
                add("ownHiddenCandidate未設定", "MID", "「%s」: 武将側では→%s" % (s["name"], e))

    # sourceCharacters 逆引き
    for g, src in targets:
        for row in g.get("synthesisTable") or []:
            sk_ = row.get("skill")
            if sk_ in skills and not any(c.get("no") == g["no"] for c in (skills[sk_].get("sourceCharacters") or [])):
                add("逆引き漏れ", "HIGH", "[%s] %s No.%s %s枠の%s が sourceCharacters に無い" %
                    (status(g), g["name"], g["no"], row.get("slot"), sk_))

    # sourceCharacters の slot が、武将の synthesisTable の実枠と一致しているか。
    # 2026-08-16: No.1279 荒木村重（2）の魔導禁鎖が synthesisTable の A・B 枠に
    # 載っているのにスキル側は「移植不可」になっていた(ユーザー指摘)。全件見たら
    # 26件あり、うち5件は「移植不可なのに実は移植できる」という**使い方を
    # 間違えさせる**種類の誤りだった。逆引きの有無しか見ていなかったので通っていた。
    real_slots = collections.defaultdict(lambda: collections.defaultdict(list))
    for g, _src in targets:
        for row in g.get("synthesisTable") or []:
            if row.get("skill") and row.get("slot"):
                real_slots[str(g["no"])][row["skill"]].append(row["slot"])
    _ORD = {"A": 0, "B": 1, "C": 2, "S1": 3, "S2": 4}
    for s in D["skills"]:
        for c in s.get("sourceCharacters") or []:
            got = real_slots.get(str(c.get("no")), {}).get(s["name"])
            if not got:
                continue          # 合成表に無い = 「移植不可」でよい
            want = "・".join(sorted(set(got), key=lambda x: _ORD.get(x, 9)))
            if (c.get("slot") or "") != want:
                add("slotが実枠と違う", "HIGH",
                    "「%s」の %s No.%s: slot=%s(合成表では %s枠)"
                    % (s["name"], c.get("name"), c.get("no"),
                       c.get("slot") or "無し", want))

    # LINKED_SKILLS
    for arr, label, glist in [(D["LINKED_SKILLS"], "LINKED_SKILLS", D["generals"]),
                              (D["KK_LINKED_SKILLS"], "KK_LINKED_SKILLS", D["kyokuGenerals"]),
                       (D["KP_LINKED_SKILLS"], "KP_LINKED_SKILLS", D["kyokuPsGenerals"])]:
        for n in arr:
            if n not in skills:
                add("LINKED_SKILLS", "MID", "%s の「%s」が skills.html に無い" % (label, n))
        used = set()
        for g in glist:
            if status(g) == "無印":
                continue
            for row in g.get("synthesisTable") or []:
                for k in ("skill", "afterSkill"):
                    if row.get(k) in skills:
                        used.add(row[k])
            if g.get("initialSkill") in skills:
                used.add(g["initialSkill"])
        for n in sorted(used - set(arr)):
            add("LINKED_SKILLS", "MID", "%s 未登録だがページ有り: %s" % (label, n))

    # categoryLinks 9項目
    # 2026-08-16: 無尽は攻撃用と防御用の2ページに割れた(skills-mujin-atk/def)。
    # 飛翔と同じく**ページ名の一部**で見る。どちらか片方に載っていればよい。
    KW = [("無尽", "skills-mujin"), ("撤退", "skills-taitai.html"), ("覇道", "skills-hadou.html"),
          ("不屈", "skills-fukutsu.html"), ("兵站", "skills-heitan.html"), ("卓越", "skills-takuetsu.html")]
    for s in D["skills"]:
        text = (s.get("effectSummary") or "") + " " + (s.get("target") or "")
        cl = [c.get("href") for c in (s.get("categoryLinks") or [])]
        for kw, page in KW:
            if kw in text and not any(page in (c or "") for c in cl):
                add("categoryLinks漏れ", "MID", "「%s」: 『%s』があるが %s へのリンク無し" % (s["name"], kw, page))
        # 「飛翔を持たない〜」は他者の飛翔の話なので対象外
        if re.search(r"飛翔(?!を持たない)", text) and "飛翔" in (s.get("target") or "") \
                and not any("hishou" in (c or "") for c in cl):
            add("categoryLinks漏れ", "MID", "「%s」: 飛翔一覧へのリンク無し" % s["name"])
        # 2026-08-13: 「防御参加武将数」を含むだけで拾っていたので、
        # 「防御参加武将数が150以下のとき効果3倍」のように**人数を条件に使っているだけ**の
        # スキルまで人数依存一覧へ誘導していた(鳳凰ノ幻華)。
        # C-01は「一覧のエントリとして載っている一覧だけ」なので、
        # 効果が人数に比例するもの(×防御参加武将数)に限る。
        prop = re.search(r"[×x\*)]\s*防御参加武将数|防御参加武将数\s*[×x\*]", text)
        if prop and not any("count" in (c or "") for c in cl):
            add("categoryLinks漏れ", "MID", "「%s」: 人数依存一覧へのリンク無し" % s["name"])

    # 未計算の式。
    # 母数が決まっているのは [[feedback_count_dependent_skill_format]] の2つだけ
    # (部隊内系=4人 / 防御参加武将数=280人)。
    # 「飛翔を持たない防御参加武将数」「自軍攻撃武将数」のような、敵味方の編成しだいで
    # 決まる部分集合には母数が無いので、数値化を求めない(サイト全体で symbolic のまま揃えている)。
    # 2026-08-26: 「×防御参加武将数」の順しか見ておらず、
    # 「防御参加武将数×3.2%」と**逆に書かれた26行**を素通ししていた。
    # 「自部隊内の『海賊衆』を指揮する武将数」のような部分集合は母数が無いので、
    # 直前が「の」のものは逆順の側では拾わない。
    KNOWN_BASE = re.compile(
        r"[×x]\s*(?<!飛翔を持たない)(防御参加武将数|部隊内[^\d\s)=]*武将数|無尽武将数)"
        r"|(?<!の)(?:防御参加武将数|無尽武将数)\s*[×x]\s*[\d.]")
    # 2026-08-26: ここは**武将側しか見ていなかった**ので、
    # スキルページ側に残った未計算式(天勇雷槍光陣ほか)を素通ししていた。
    for g, src in targets + [(s, "スキル") for s in D["skills"]]:
        for row in g.get("trTable") or []:
            eff = row.get("effect") or ""
            if KNOWN_BASE.search(eff) and not re.search(r"=\s*[\d.]", eff):
                add("未計算式", "MID", "[%s] %s No.%s %s: %s"
                    % (src if src == "スキル" else status(g), g["name"],
                       g.get("no") or "-", row.get("level"), eff))

    # 「未確認」と表示される段には、調べた先の記録が要る(RULES.md I-01)
    # 表示規則(RULES.md V-01): 値が判明している一番上の段までは、空でも「未確認」として出る。
    # そこで終わりにするのが2026-08-12の違反(I-01)だったので、
    # tools/research_log.json に2ソース以上の記録が無い「未確認」はHIGHにする。
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        from reslog import sources as _res_sources
    except Exception:
        def _res_sources(k, verified_only=False):
            return []
    ORDER = ["LV10", "TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]
    seen_unk = set()
    for g, src in targets + [(s, "skills.html") for s in D["skills"]]:
        rows = g.get("trTable") or []
        filled = [ORDER.index(r["level"]) for r in rows
                  if r.get("effect") and r.get("level", "").startswith("TR")]
        if not filled:
            continue
        blanks = [r["level"] for r in rows
                  if r.get("level", "").startswith("TR") and not r.get("effect")
                  and ORDER.index(r["level"]) < max(filled)]
        if not blanks:
            continue
        skill = g.get("initialSkill") if src != "skills.html" else g.get("name")
        if not skill:
            continue
        key = "TR:" + skill
        # F-7/G-1/H-1/I-2(2026-08-13 第3回レッドチーム、4体が独立に指摘):
        # reslog.py の docstring は「証拠つき(fetch_and_log)の件数だけを数える」と
        # 書いていたのに、ここが verified_only を渡していなかった。
        # HTTPを1回も叩かず log() を2回呼ぶだけでHIGHが消えていた。
        # I-01(調べずに「未確認」と書いた重い違反)を防ぐための検査そのものが空だった。
        got = _res_sources(key, verified_only=True)
        if len(got) < 2 and key not in seen_unk:
            seen_unk.add(key)
            add("未確認の根拠なし", "HIGH",
                "「%s」の%sが未確認表示だが、調査ログの情報源が%d件(2件以上必要)。"
                "調べてから埋めるか、当たった先を tools/reslog.py で記録する"
                % (skill, "/".join(blanks), len(got)))

    # ============================================================
    # 2026-08-12追加分。docs/RULES.md で「機械○ / 監査✗」だった項目を埋める。
    # いずれも同日に手作業で見つかった不備で、監査に無かったから見逃していた。
    # ============================================================
    RANKS_HI = ("S", "SS", "SSS", "X", "XX", "XXX")
    all_g = (D["generals"] + D["kyokuAll"] + D["extraAll"]
             + D["ketsuGenerals"])
    kyoku_no = {g["no"] for g in D["kyokuAll"]}
    ketsu_no = {g["no"] for g in D["ketsuGenerals"]}

    # S-01: S以上のスキルにページが無い(初期スキルだけでなく合成候補も対象)
    # 2026-08-12、初期スキルしか数えず「0件」と誤報告した(違反S-01)。合成候補で31種漏れていた。
    miss_pages = {}
    for g in all_g:
        det = g.get("skillDetail") or ""
        ini = g.get("initialSkill")
        if ini and ini not in skills and det.split("/")[0].strip() in RANKS_HI:
            miss_pages.setdefault(ini, set()).add("初期:%s" % g["no"])
        for row in g.get("synthesisTable") or []:
            for nk, rk in (("skill", "rank"), ("afterSkill", "afterRank")):
                nm, rk_ = row.get(nk), row.get(rk)
                if nm and rk_ in RANKS_HI and nm not in skills:
                    miss_pages.setdefault(nm, set()).add("候補:%s" % g["no"])
    for nm, where in sorted(miss_pages.items()):
        add("S以上でページ無し", "HIGH",
            "「%s」のskills.htmlページが無い(%d箇所で参照: %s)"
            % (nm, len(where), "/".join(sorted(where)[:4])))

    # S-07: sourceCharacters の db 指定ミス(極なのに characters.html を指す等)
    # 2026-08-16: 特シークレットを足したので "toku" を足す。天パラレルは
    # db を付けない(元カードと同じ武将で #No 転送のため)。
    toku_no = {g["no"] for g in D["tokuSecretGenerals"]}

    def db_want(no):
        if no in kyoku_no:
            return "kyoku"
        if no in toku_no:
            return "toku"
        return "ketsu" if no in ketsu_no else None

    # 2026-08-14: ここは skills.html の**本文**を正規表現で読んでいた。
    # 配列が data/skill/ からの生成物になったので、判定は正本の側で行う。
    # (本文を読んだままだと、正本を直しても生成するまで鳴らない)
    for s in D["skills"]:
        for row in s.get("sourceCharacters") or []:
            no = str(row.get("no") or "")
            want, got = db_want(no), row.get("db")
            if got != want:
                add("sourceCharactersのdb", "HIGH",
                    "data/skill/%s.json: %s No.%s の db が %s(正しくは %s)"
                    % (s.get("name"), row.get("name"), no,
                       got or "無し", want or "無し(通常DB)"))
    # 一覧ページ(skills-*.html)は sourceCharacters を独自に複製しているので、本文を見る
    for page, text in D["listPages"].items():
        for m in re.finditer(r'\{name:"([^"]*)", no:"(\d+)"([^{}]*)\}', text):
            no, rest = m.group(2), m.group(3)
            want = db_want(no)
            got = re.search(r'db:"([^"]*)"', rest)
            got = got.group(1) if got else None
            if got != want:
                add("sourceCharactersのdb", "HIGH",
                    "%s: %s No.%s の db が %s(正しくは %s)"
                    % (page, m.group(1), no, got or "無し", want or "無し(通常DB)"))

    # D-08 / V-01: trTable の段が飛んでいる
    ORD = ["LV10", "TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]
    for g, src in [(x, "武将") for x in all_g] + [(s, "スキル") for s in D["skills"]]:
        lv = [r.get("level") for r in (g.get("trTable") or [])]
        idx = [ORD.index(x) for x in lv if x in ORD]
        if idx and sorted(idx) != list(range(min(idx), max(idx) + 1)):
            add("trTableの段飛び", "MID", "[%s] %s %s: %s"
                % (src, g.get("name"), g.get("no") or "", "/".join(lv)))

    # P-04: シミュレーター側(ixa-data.js)に cost が無い
    body = D["ixaDataSrc"][D["ixaDataSrc"].index("const generalGrowthDB"):]
    for m in re.finditer(r"^  \{ name:'([^']+)', no:'(\d+)'(.*)$", body, re.M):
        no = m.group(2)
        # 2026-08-30: ここは長らく「パラレルはコスト概念なし」として5桁の3/4始まりを
        # 飛ばしていたが、**それは誤り。** パラレルは元カードと同じコストを持ち、
        # 正本(data/busho-parallel/)にも一覧ページにもコストが入っている。
        # 飛ばしていたせいで、シミュレーターのパラレル30件がコスト未設定のまま
        # 「?」と表示され続けていた(うぐさん指摘)。飛ばさずに見る。
        if not re.search(r"\bcost:\s*[\d.]+", m.group(3)):
            add("シミュのcost未設定", "MID", "%s No.%s に cost が無い" % (m.group(1), no))

    # P-06: 正本にあるパラレルがシミュレーターに入っていない(2026-08-31)
    #
    # シミュレーターの武将DB(generalGrowthDB)は**手で保守している配列**なので、
    # 正本に足しても入れ忘れる。実際、32章のパラレル12件(31310〜31321)が
    # 抜けたままで**部隊に組めなかった**(うぐさんのコスト指摘を追う途中で判明)。
    #
    # シミュレーターは載せる武将を絞っているので「正本の全カードが要る」とは
    # 言えない。だが **元カードが載っているのにそのパラレルだけ無い**のは
    # ただの入れ忘れなので、そこだけを見る。
    sim_no = set(re.findall(r"^  \{ name:'[^']+', no:'(\d+)'", body, re.M))
    for g in D.get("parallelGenerals") or []:
        no = str(g.get("no") or "")
        if not no or no in sim_no:
            continue
        orig = str(int(no) - 30000) if no.isdigit() else ""
        if orig in sim_no:
            add("シミュにパラレルが無い", "MID",
                "%s No.%s が generalGrowthDB に無い。元カード No.%s は載っているので"
                "入れ忘れ(部隊に組めない)" % (g.get("name"), no, orig))

    # P-05: シミュレーターの武将名・初期スキル名が正本と違う(2026-08-16)
    #
    # (N)を114件振り直したとき**シミュレーター側を直し忘れて26件ずれた**。
    # 対のデータは両側を見る、という原則どおりに動けていない。
    # さらに初期スキル名に1〜2文字の綴り間違いが9件あった
    # (荷天滅陣←倚天滅陣 / 倫蝮不蓁←倫魁不羈 / 島穿覓槍←島穿鬼槍 など)。
    # **specialSkills も同じ綴りで書いてあるので計算は通ってしまう。**
    # 画面に出る名前とスキルページへの繋がりだけが壊れ、目では気付けない。
    by_no = {str(g.get("no")): g for g in all_g}
    for m in re.finditer(r"^  \{ name:'([^']+)', no:'(\d+)'(.*)$", body, re.M):
        nm, no, rest = m.group(1), m.group(2), m.group(3)
        g = by_no.get(no)
        if not g:
            continue
        if g.get("name") and nm != g["name"]:
            add("シミュの名前が正本と違う", "HIGH",
                "No.%s の武将名: シミュ「%s」/ 正本「%s」" % (no, nm, g["name"]))
        s = re.search(r"initialSkill:'([^']*)'", rest)
        if s and g.get("initialSkill") and s.group(1) != g["initialSkill"]:
            add("シミュの名前が正本と違う", "HIGH",
                "No.%s %s の初期スキル: シミュ「%s」/ 正本「%s」"
                % (no, nm, s.group(1), g["initialSkill"]))

    # S-15: スキル側の sourceCharacters が武将側と噛み合っているか(2026-08-24)
    #
    # 逆引きは「武将→スキル」だけを見ていて、**スキル側に書いてある持ち主が
    # 本当にそのスキルを持っているか**は誰も検査していなかった。
    # 実際に7件の誤りが残っていた。内訳は
    #   ・移植後(afterSkill)にしか出ないのに持ち主として並べていた 6件(A-3-12違反)
    #   ・カード番号の取り違え 1件(龍驤虎躍に No.1238。正しくは No.1239)
    _by_no = {str(g.get("no")): g for g in all_g}
    for s in D["skills"]:
        for c in s.get("sourceCharacters") or []:
            no = str(c.get("no") or "")
            g = _by_no.get(no)
            if not g:
                add("スキルの持ち主が正本に無い", "HIGH",
                    "「%s」の持ち主 No.%s が正本に無い" % (s["name"], no))
                continue
            st = g.get("synthesisTable") or []
            pre = {r.get("slot") for r in st if r.get("skill") == s["name"]}
            if c.get("slot") == "移植不可":
                if g.get("initialSkill") != s["name"]:
                    add("スキルの持ち主が噛み合わない", "MID",
                        "「%s」の No.%s %s: 移植不可と書いてあるが初期スキルは「%s」"
                        % (s["name"], no, g.get("name"), g.get("initialSkill")))
            elif not (set((c.get("slot") or "").split("・")) & pre):
                post = {r.get("slot") for r in st
                        if r.get("afterSkill") == s["name"] and r.get("skill") != s["name"]}
                why = ("移植後にしか出ない(A-3-12によりgrantedViaSkillsに書く)"
                       if post else "この武将はこのスキルを持っていない")
                add("スキルの持ち主が噛み合わない", "MID",
                    "「%s」の No.%s %s の枠 %s: %s"
                    % (s["name"], no, g.get("name"), c.get("slot"), why))

    # S-16: 武将側とスキル側で LV10 の効果の数値が食い違っていないか(2026-08-24)
    #
    # 突き合わせたら94件が違っていた。ほとんどは言い回しや中黒の差で実害が無いが、
    # **3件はスキル側が効果そのものを落としていた**(天神旋武の防御と速度など)。
    # 言い回しの差で鳴らすと埋もれるので、「攻撃/防御/速度/破壊 のN%上昇」の
    # 組み合わせだけを比べる。
    _EFF = re.compile(r"(攻撃|防御|速度|破壊)\s*([\d.]+)%上昇")
    _skmap = {s["name"]: s for s in D["skills"]}
    for g in all_g:
        s = _skmap.get(g.get("initialSkill"))
        if not s:
            continue
        a = (g.get("trTable") or [{}])[0].get("effect") or ""
        b = (s.get("trTable") or [{}])[0].get("effect") or ""
        if not a or not b:
            continue
        ea, eb = set(_EFF.findall(a)), set(_EFF.findall(b))
        if not ea or not eb or ea == eb:
            continue
        miss = ea - eb
        extra = eb - ea
        if miss:
            add("スキル側が効果を落としている", "MID",
                "「%s」(No.%s %s): スキルページに %s が無い"
                % (s["name"], g["no"], g.get("name"),
                   "・".join("%s%s%%上昇" % x for x in sorted(miss))))
        if extra:
            add("武将側が効果を落としている", "MID",
                "「%s」(No.%s %s): 武将側に %s が無い"
                % (s["name"], g["no"], g.get("name"),
                   "・".join("%s%s%%上昇" % x for x in sorted(extra))))

    # S-17: 合成表のランクが、そのスキルのページのランクと合っているか(2026-08-26)
    #
    # No.3542 団忠正 の S2枠が「BB」という存在しないランクになっていた。
    # 合成表のランクを全部数えると12486件中「BB」は1件だけで、Bの打ち間違い。
    # 表記そのものを見る検査が無く、誰も気付いていなかった。
    RANKS_OK = {"XXX", "XX", "X", "SSS", "SS", "S", "A", "B", "C", "D", "E", "F"}
    _skrank = {s["name"]: s.get("rank") for s in D["skills"]}
    for g in all_g:
        for row in g.get("synthesisTable") or []:
            for nk, rk in (("skill", "rank"), ("afterSkill", "afterRank")):
                nm, v = row.get(nk), row.get(rk)
                if not v:
                    continue
                if v not in RANKS_OK:
                    add("ランクの表記が表に無い", "MID",
                        "%s No.%s %s枠 %s: 「%s」はランクの表に無い"
                        % (g["name"], g["no"], row.get("slot"), rk, v))
                    continue
                want = _skrank.get(nm)
                if want and v != want:
                    add("合成表のランクがスキルと違う", "MID",
                        "%s No.%s %s枠 「%s」: 合成表=%s / スキルページ=%s"
                        % (g["name"], g["no"], row.get("slot"), nm, v, want))
    # S-22: 一覧ページの lv10Effect が正本の LV10 と食い違っていないか(2026-08-28)
    #
    # 一覧ページは効果文を独自に持っており、正本を直しても取り残される。
    # 実際に 火槍猛進(740% → 正本は 740%×2=1480%)と
    # 朝曇ノ明麗(110% → 正本は 290%。2026-08-02 に正本だけ直していた)が
    # 古い値のまま残っていた。数値が**1つも重ならない**ものだけ鳴らす
    # (書き方の差で鳴ると埋もれるため)。
    _skmap2 = {s["name"]: s for s in D["skills"]}

    def _pcts(x):
        return set(re.findall(r"(\d+(?:\.\d+)?)%上昇", x or ""))

    for page, text in D["listPages"].items():
        for m in re.finditer(r'name:"([^"]+)", skillPage:"[^"]*"(.{0,400}?)lv10Effect:"([^"]*)"',
                             text, re.S):
            nm, eff = m.group(1), m.group(3)
            s = _skmap2.get(nm)
            if not s:
                continue
            tr = [r for r in (s.get("trTable") or []) if r.get("level") == "LV10"]
            if not tr:
                continue
            a, b = _pcts(eff), _pcts(tr[0].get("effect"))
            if a and b and not (a & b):
                add("一覧の効果が正本と違う", "MID",
                    "%s の「%s」: 一覧=%s / 正本のLV10=%s"
                    % (page, nm, "・".join(sorted(a)[:3]), "・".join(sorted(b)[:3])))

    # S-21: 隠し候補・移植元の参照先が S以上ならページが要る(2026-08-28)
    #
    # S-01 は「武将の初期スキルと合成候補」だけを見ており、
    # スキル側の ownHiddenCandidate / grantedViaSkills の**参照先**は見ていない。
    # そのため、どの武将の合成表にも出てこない移植先のページ抜けを拾えなかった
    # (朧雲ノ進撃 SSS / 覇獄竜王 SS の2件が抜けていた)。
    _skset = {s["name"] for s in D["skills"]}
    _NEED = ("S", "SS", "SSS", "X", "XX", "XXX")
    for s in D["skills"]:
        _refs = []
        o = s.get("ownHiddenCandidate") or {}
        if o.get("skill"):
            _refs.append(("ownHiddenCandidate", o["skill"], o.get("rank")))
        for gv in s.get("grantedViaSkills") or []:
            if gv.get("skill"):
                _refs.append(("grantedViaSkills", gv["skill"], gv.get("rank")))
        for kind, nm, rk in _refs:
            if nm in _skset:
                continue
            if rk in _NEED:
                add("参照先のスキルページが無い", "HIGH",
                    "「%s」の %s が指す「%s」[%s] のページが無い"
                    % (s["name"], kind, nm, rk))

    # S-20: effectSummary に「(係数×…)%」が伏せ字のまま残っていないか(2026-08-27)
    #
    # trTable には係数も計算結果も入っているのに、**一覧やスキルページに出るのは
    # effectSummary の方**で、そちらだけ伏せ字のままだった14件があった。
    # 値があるのに読み手には見えない。本文に係数が書いてあるもの
    # (一調天成の「係数(LV10=13)」)は除く。
    for s in D["skills"]:
        v = s.get("effectSummary") or ""
        if not re.search(r"\(係数×[^)]*\)%", v):
            continue
        if re.search(r"係数[はが（(]{0,2}(?:LV\d+\s*=)?\s*[\d.]", v):
            continue
        add("effectSummaryが伏せ字", "MID",
            "「%s」: effectSummary に「(係数×…)%%」が残っている(trTable には実値がある)"
            % s["name"])

    # S-19: rankGrades の値がランクの表にあるか(2026-08-27)
    #
    # S-17 は合成表のランクだけを見ており、**統率(rankGrades)は誰も見ていなかった**。
    # ixawiki が全角で書いた「Ａ」(No.1812 源五郎)と、未記入のときに出す
    # 「(SSS〜Fのいずれか)」という説明文(No.4245 長谷川秀一)が正本に焼き付いていた。
    for g in all_g:
        for k, v in (g.get("rankGrades") or {}).items():
            if v and v not in RANKS_OK:
                add("統率の表記が表に無い", "MID",
                    "%s No.%s の %s: 「%s」はランクの表に無い"
                    % (g["name"], g["no"], k, v[:24]))

    # S-18: くじのページの武将名が正本と合っているか(2026-08-27)
    #
    # gacha-simulator.html と gacha-kuji-*.html は排出候補を
    # {no:1310, name:'織田信秀'} の形で独自に持っている。
    # 誰も突き合わせておらず、**3ページで483行**が正本とずれていた
    # (（N）や【覇】が落ちている)。改名しても気付けない。
    for page, text in D["gachaPages"].items():
        for m in re.finditer(r"\{no:(\d{4,5}), name:'([^']*)'", text):
            no, nm = m.group(1), m.group(2)
            g = _by_no.get(no)
            if not g:
                add("くじの武将が正本に無い", "HIGH",
                    "%s の No.%s「%s」が正本に無い" % (page, no, nm))
            elif g.get("name") and nm != g["name"]:
                add("くじの武将名が正本と違う", "MID",
                    "%s の No.%s: くじ「%s」/ 正本「%s」" % (page, no, nm, g["name"]))

    # S-23: くじの確率が合計100%になっているか(2026-08-30)
    #
    # 排出確率の表は BASE_RATES などの定数から作るようになっていて
    # 表と定数のずれは起きない。だが **定数そのものの合計は誰も見ていない。**
    # 1つ書き換えれば表も一緒に変わるので、見た目からは気付けないまま
    # 実際に引かれる確率だけが狂う。武将別の内訳(w)も同じで、
    # 各レア度の合計がその確率と一致していなければ配分がずれる。
    for page, text in D["gachaPages"].items():
        consts = {}
        for m in re.finditer(r"const ([A-Z_]*RATES) = \{([^}]*)\}", text):
            v = {k: int(x) for k, x in re.findall(r"([傑天極特上])\s*:\s*(\d+)", m.group(2))}
            if v:
                consts[m.group(1)] = v
                if sum(v.values()) != 100000:
                    add("くじの確率の合計が100%でない", "HIGH",
                        "%s の %s が合計 %.3f%%(100%% でない): %s"
                        % (page, m.group(1), sum(v.values()) / 1000.0,
                           "・".join("%s=%.3f%%" % (k, x / 1000.0) for k, x in v.items())))
        # 武将別の内訳。「const 傑CHARS = [ {no:..., w:0.0060}, ... ]」の形
        for m in re.finditer(r"const ([A-Z_]+_CHARS[A-Z_]*) = \[(.*?)\];", text, re.S):
            ws = [float(x) for x in re.findall(r"w:\s*([\d.]+)", m.group(2))]
            if len(ws) < 2:
                continue
            tier = {"KETSU": "傑", "TEN": "天", "KYOKU": "極",
                    "TOKU": "特", "JOU": "上"}.get(m.group(1).split("_")[0])
            kind = ("TENTH_GUARANTEE_RATES" if "GUARANTEE" in m.group(1)
                    else "TENTH_BOOST_RATES" if "BOOST" in m.group(1) else "BASE_RATES")
            want = (consts.get(kind) or {}).get(tier)
            if want is None:
                continue
            if abs(sum(ws) - want / 1000.0) > 0.0005:
                add("くじの武将別の内訳が確率と合わない", "HIGH",
                    "%s の %s(%d件)の合計が %.4f%%。%s では %s は %.3f%%"
                    % (page, m.group(1), len(ws), sum(ws), kind, tier, want / 1000.0))

    # S-24: 鍛錬(TR)の有無(2026-08-30、うぐさんの規則)
    #
    #   ・鍛錬が登場したのは **No.1263 が追加された28章から。**
    #   ・**傑・特・上・序は現在まで鍛錬の追加が無い。**
    #   ・28章以降でも鍛錬の無い武将は居るので、**そちらは何も言わない。**
    #   ・古いカードでも後から鍛錬が付いたものが6枚ある(すべて天)。
    #     天と極は章が古くても鍛錬を持ちうるので、ここでは鳴らさない。
    #
    # データもこの規則を裏付けている(27章は LV10 のみ、28章は LV10〜TR5)。
    TANREN_FIRST_CH = 28
    # レアリティは正本のディレクトリではなくページの配列から取る
    # (この監査はページを読んでおり、ディレクトリの情報は持っていない)
    no_tanren_no = {}
    for _arr, _label in (("ketsuGenerals", "傑"), ("tokuGenerals", "特"),
                         ("tokuSecretGenerals", "特シークレット"),
                         ("ueGenerals", "上"), ("joGenerals", "序")):
        for _x in D.get(_arr) or []:
            no_tanren_no[str(_x.get("no"))] = _label

    def _ch_num(c):
        m = re.match(r"^(\d+)(?:-\d+)?章$", c or "")
        return int(m.group(1)) if m else None

    for g in all_g:
        n = _ch_num(g.get("ch"))
        rar = no_tanren_no.get(str(g.get("no")))
        old = n is not None and n < TANREN_FIRST_CH
        if not (rar or old):
            continue
        rows = g.get("trTable") or []
        tr = [r for r in rows if str(r.get("level", "")).startswith("TR")]
        real = [r for r in tr if (r.get("effect") or "").strip()]
        lv = next((r for r in rows if r.get("level") == "LV10"), None)
        txt = (lv.get("effect") or "") if lv else ""
        if rar and real:
            add("鍛錬の無いレアリティにTRがある", "HIGH",
                "%s No.%s は %s で、%s は現在まで鍛錬の追加が無いのに TR に値がある(%s)"
                % (g.get("name"), g.get("no"), rar, rar,
                   "/".join(str(r.get("level")) for r in real)))
        if tr and not real:
            add("鍛錬が無いのに空のTR段がある", "HIGH",
                "%s No.%s(%s)は鍛錬が無い%sなのに、値の入っていない TR の段を持つ(%s)。"
                "段だけ残ると鍛錬があるように見える"
                % (g.get("name"), g.get("no"), g.get("ch") or "章不明",
                   ("レアリティ(%s)" % rar) if rar else "%d章より前" % TANREN_FIRST_CH,
                   "/".join(str(r.get("level")) for r in tr)))
        if lv and txt.strip() and not tr and "TRなし" not in txt and "TR以降" not in txt:
            add("鍛錬なしと書けるのに書いていない", "MID",
                "%s No.%s(%s)は%sなので鍛錬なしと言い切れるが、LV10 に「TRなし」が無い"
                % (g.get("name"), g.get("no"), g.get("ch") or "章不明",
                   ("鍛錬の追加が無い%s" % rar) if rar else "%d章より前" % TANREN_FIRST_CH))

    # F-07: effectShort の接頭辞の剥がし損ね
    for g in all_g:
        for row in g.get("synthesisTable") or []:
            for k in ("effectShort", "afterEffectShort"):
                v = row.get(k) or ""
                if re.match(r"^[\d.]+%\s*/", v) or v.startswith("効果 ") or " / 効果 " in v:
                    add("effectShortの接頭辞", "MID", "%s No.%s %s枠 %s: %s"
                        % (g["name"], g["no"], row.get("slot"), k, v[:60]))

    # S-06: 一覧ページ側の sourceCharacters の同期漏れ
    for page, text in D["listPages"].items():
        for m in re.finditer(r'\{name:"([^"]+)", skillPage:"[^"]*"', text):
            nm = m.group(1)
            if nm not in skills:
                continue
            seg = text[m.start():m.start() + 4000]
            listed = {x for x in re.findall(r'no:"(\d+)"', seg.split("]}", 1)[0])}
            main_ = {c.get("no") for c in (skills[nm].get("sourceCharacters") or [])}
            missing = main_ - listed
            if missing:
                add("一覧の逆引き同期漏れ", "MID", "%s の「%s」に %s が無い(skills.html側にはある)"
                    % (page, nm, "/".join(sorted(missing))[:60]))

    # D-03: ドット付きランク
    for g in all_g:
        for k, v in (g.get("rankGrades") or {}).items():
            if isinstance(v, str) and v.startswith("."):
                add("ドット付きランク", "MID", "%s No.%s %s=%s" % (g["name"], g["no"], k, v))

    # D-09: synthesisTable の行が5枠そろっていない
    # 2026-08-23: **童(1800番台)には S2 の枠が無い**(うぐさん)。4行が正しい。
    # 2026-08-27: 童以外にも4枠のカードがあった(No.2589 難攻不落の天守。
    # ixawiki の候補表で「S2枠なし」を確認済み)。番号で例外を並べる形は
    # 見つかるたびに増えるので、**A/B/C/S1 が揃っていれば4行も正しい**とする。
    for g in all_g:
        st = g.get("synthesisTable")
        if st is None:
            continue
        _slots = {r.get("slot") for r in st}
        if _slots - {"A", "B", "C", "S1", "S2"}:
            continue
        if len(st) in (0, 5):
            continue
        if len(st) == 4 and _slots == {"A", "B", "C", "S1"}:
            continue                      # S2 を持たないカード(童・2589 など)
        add("synthesisTableの行数", "MID",
            "%s No.%s: %d行(A/B/C/S1/S2の5行が標準。S2が無いカードは A/B/C/S1 の4行)"
            % (g["name"], g["no"], len(st)))
    # D-10 / D-11: slot の独自語
    for s in D["skills"]:
        for c in (s.get("sourceCharacters") or []):
            sl = c.get("slot") or ""
            if sl and not re.fullmatch(r"(A|B|C|S1|S2|移植不可)(・(A|B|C|S1|S2))*(\(.*\))?", sl):
                add("slotの独自語", "MID", "「%s」の %s No.%s: slot=%s"
                    % (s["name"], c.get("name"), c.get("no"), sl))

    # V-07(2026-08-13、ユーザー決定): 合成表の確率に「+」を付けない。
    # 同じスキルなのに武将ごとに「+70%」と「70%」が混在していた(520項目)。
    # 数値そのものはスキルページの確率と全行一致していて、割れていたのは書式だけ。
    # trTable / effectSummary が「確率 100%」と + なしなので、そちらに揃えた。
    # 放っておくと1件ずつ書き足されて元に戻るので、機械で見張る。
    for g in all_g:
        for row in (g.get("synthesisTable") or []):
            for k in ("rate", "afterRate"):
                v = row.get(k)
                if isinstance(v, str) and re.fullmatch(r"\+\d+(\.\d+)?%", v):
                    add("確率に+が付いている", "MID",
                        "No.%s %s の %s枠 %s=%s(「+」を外して %s に揃える)"
                        % (g["no"], g.get("name"), row.get("slot"), k, v, v[1:]))

    # D-12: 武将名の表記ゆれ(半角括弧・半角【】まわり)
    for g in all_g:
        nm = g.get("name") or ""
        if re.search(r"\(\d+\)|\(覇\)|\(復刻\)", nm):
            add("武将名の表記ゆれ", "MID", "No.%s %s(全角（）/-復刻-/【覇】に統一)" % (g["no"], nm))

    # D-13: データ内に生のHTMLタグ
    for g, lbl in [(x, "武将") for x in all_g] + [(s, "スキル") for s in D["skills"]]:
        for k in ("skillDetail", "effectSummary", "effect"):
            v = g.get(k) or ""
            if "<" in v and re.search(r"<\w+[^>]*>", v):
                add("データ内のHTMLタグ", "MID", "[%s] %s の %s" % (lbl, g.get("name"), k))

    # F-02: 修飾語なしの「模倣不可」が①より後ろにある
    for g, lbl in [(x, "武将") for x in all_g] + [(s, "スキル") for s in D["skills"]]:
        t = g.get("skillDetail") or g.get("effectSummary") or ""
        if "模倣不可" not in t or "①" not in t:
            continue
        lines = t.split("\n")
        solo = [i for i, l in enumerate(lines) if l.strip() == "模倣不可"]
        first = next((i for i, l in enumerate(lines) if l.startswith("①")), None)
        if solo and first is not None and min(solo) > first:
            add("模倣不可の位置", "MID", "[%s] %s: 模倣不可が①より後ろ" % (lbl, g.get("name")))

    # D-07: テンプレートのフィールドを省略していない(nullで明示する)
    NEED = ["ch", "cost", "troop", "sub", "effect", "furigana", "illustrator",
            "atkBase", "atkGrowth", "defBase", "defGrowth", "tacticsBase",
            "tacticsGrowth", "lv0Troops", "rankGrades", "initialSkill", "skillDetail"]
    for g in D["generals"] + D["kyokuAll"]:
        lack = [k for k in NEED if k not in g]
        if lack:
            add("フィールドの省略", "MID", "%s No.%s: %s が無い(nullで明示する)"
                % (g["name"], g["no"], "/".join(lack)))

    # D-15: synthesisTable が埋まっていないのに黄丸/赤丸になっている。
    # 青丸(登録しただけ)は未完成でよいので対象外([[project_registration_manualization]])。
    # **合成不可カードの例外(noSynthesis)を用意してある**が、いまのところ
    # 使っている武将は無い。唯一の候補だった赤べこ(No.2616)は、ixanary と
    # ixawiki が両方「合成不可」と書いていただけで、**実際には合成候補が
    # あった**(2026-08-16、うぐさんがゲーム内の画面で確認)。
    # 「情報源が合成不可と書いている」を根拠に立てないこと。
    for g, src in targets:
        if status(g) not in ("黄丸", "赤丸"):
            continue
        if g.get("noSynthesis"):
            continue
        st = g.get("synthesisTable") or []
        if not st or all(not r.get("skill") for r in st):
            add("合成表なしで検証済み", "HIGH", "[%s] %s No.%s: synthesisTable が空のまま"
                % (status(g), g["name"], g["no"]))

    # S-13: 初期スキルがA/B/C枠に載っているなら、その afterSkill は S1 のスキルと一致する
    for g in all_g:
        st = g.get("synthesisTable") or []
        s1 = next((r.get("skill") for r in st if r.get("slot") == "S1"), None)
        ini = g.get("initialSkill")
        if not s1 or not ini:
            continue
        for r in st:
            if r.get("slot") in ("A", "B", "C") and r.get("skill") == ini \
                    and r.get("afterSkill") and r["afterSkill"] != s1:
                add("移植後がS1と不一致", "MID",
                    "%s No.%s %s枠: %s→%s だが S1 は %s(A-3-13)"
                    % (g["name"], g["no"], r["slot"], ini, r["afterSkill"], s1))

    # F-01: skillDetail に読点(、)を使わない
    for g, lbl in [(x, "武将") for x in all_g] + [(s, "スキル") for s in D["skills"]]:
        t = g.get("skillDetail") or g.get("effectSummary") or ""
        if "、" in t:
            add("skillDetailに読点", "MID", "[%s] %s: 「、」が%d個"
                % (lbl, g.get("name"), t.count("、")))

    # V-04: 横スクロールの根本原因になる min-width の欠落
    css_path = os.path.join(ROOT, "assets", "css", "site.css")
    if os.path.exists(css_path):
        with io.open(css_path, encoding="utf-8") as f:
            css = f.read()
        m = re.search(r"\.site-main\{([^}]*)\}", css)
        if m and "min-width:0" not in m.group(1).replace(" ", ""):
            add("横スクロール対策の欠落", "HIGH",
                ".site-main に min-width:0 が無い([[feedback_horizontal_scroll_root_cause]])")

    # C-3/C-4/D-5: ルール索引と違反ログ自体の腐りを見る。
    # 索引が腐ると「ルールを認識できない」状態に戻るので、データと同じ扱いで監査する。
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import rules as _rules
        _rules.ROOT = ROOT
        _rules.RULES = os.path.join(ROOT, "docs", "RULES.md")
        _rules.VIOL = os.path.join(ROOT, "docs", "RULE-VIOLATIONS.md")
        for cat, sev, msg in _rules.problems():
            add(cat, sev, msg)
    except Exception as e:
        add("ルール索引の検査が動かない", "HIGH", "tools/rules.py が失敗: %s" % e)

    # 第3回レッドチーム: 検査そのもの・比較基準・母集団を、検査される側が
    # 同じコミットで書き換えられた。「減った/すり替わった」を錠前で見る。
    try:
        import lock as _lock
        _lock.ROOT = ROOT
        _lock.PATH = os.path.join(ROOT, "tools", "checks.lock")
        for cat, sev, msg in _lock.problems():
            add(cat, sev, msg)
    except Exception as e:
        add("錠前の検査が動かない", "HIGH", "tools/lock.py が失敗: %s" % e)

    # A-7: 門番のフックそのものが正本どおりに入っているか。
    # ここを見ないと「機械で止めている」という前提が確かめられない(.git/hooks はgit管理外)。
    # precommit_check は取り出した一時ツリーで監査を回すので、そこでは .git が無い。
    # 本物のリポジトリで走ったときだけ見る(本物では毎回見るので抜け道にはならない)。
    if os.path.exists(os.path.join(ROOT, ".git")):
        # L-3 / Q-2(第4回): 素の import だと tools/__pycache__ に偽の .pyc を置くだけで
        # diffs() が空を返した(gitignore されていて git status にも出ない)。
        # バイトコードを使わない別プロセスで実行する。
        try:
            import subprocess as _sp
            _r = _sp.run([sys.executable, "-B", "-c",
                          "import json,os,sys;"
                          "sys.path.insert(0, os.path.join(os.getcwd(),'tools'));"
                          "from install_hooks import diffs;"
                          "print('@@H@@'+json.dumps(diffs(), ensure_ascii=False))"],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
            got = None
            for line in (_r.stdout or "").split("\n"):
                if line.startswith("@@H@@"):
                    got = json.loads(line[5:])
            if got is None:
                add("フックが正本と違う", "HIGH",
                    "フックの確認自体ができない: %s" % ((_r.stderr or "")[-200:]))
            else:
                for h, why in got:
                    add("フックが正本と違う", "HIGH",
                        "%s: %s(python tools/install_hooks.py で入れ直す)" % (h, why))
        except Exception as e:
            add("フックが正本と違う", "HIGH", "フックの確認自体ができない: %s" % e)

    # V-05: サイト上の出典言及
    for n in sorted(os.listdir(ROOT)):
        if not n.endswith(".html"):
            continue
        with io.open(os.path.join(ROOT, n), encoding="utf-8") as f:
            html = f.read()
        vis = re.sub(r"<script[\s\S]*?</script>|<!--[\s\S]*?-->", " ", html)
        for kw in ("出典元", "出典:", "出典："):
            if kw in vis:
                add("サイト上の出典言及", "MID", "%s に「%s」" % (n, kw))

    # ---- 外部照合 ----
    ext = []
    if ONLINE:
        ok = ng = skip = 0
        for g, src in targets:
            init = g.get("initialSkill")
            if not init or not (g.get("synthesisTable") or []):
                continue
            gen = parse_generations(fetch_skill(init))
            if not gen or "1次" not in gen:
                skip += 1
                continue
            bad = []
            for row in g["synthesisTable"]:
                slot = row.get("slot")
                if slot not in ("A", "B", "C", "S1", "S2"):
                    continue
                for key, gname, label in (("skill", "1次", "移植前"), ("afterSkill", "2次", "移植後")):
                    e = (gen.get(gname) or {}).get(slot)
                    if e and e[0] and row.get(key) and norm(e[0]) != norm(row[key]):
                        bad.append("    %s枠 %s: サイト=%s / ixanary%s=%s" % (slot, label, row[key], gname, e[0]))
            if bad:
                ng += 1
                ext.append("[%s] %s No.%s (初期スキル:%s)\n%s" % (status(g), g["name"], g["no"], init, "\n".join(bad)))
            else:
                ok += 1
        ext.insert(0, "合成テーブル照合: 一致%d体 / 不一致%d体 / 照合不可%d体\n" % (ok, ng, skip))

        ok = ng = skip = 0
        stat_bad = []
        for g, src in targets:
            html = fetch_card(g["no"])
            if not html or html.startswith("FETCH_ERROR") or "Page Not Found" in html:
                skip += 1
                continue
            t = re.sub(r"\|+", "|", re.sub(r"<[^>]+>", "|", html))
            m = re.search(r"初期値\|(\d+)\|(\d+)\|([\d.]+)\|成長値\|\+?([\d.]+)\|\+?([\d.]+)\|\+?([\d.]+)\|", t)
            if not m:
                skip += 1
                continue
            exp = {"atkBase": m.group(1), "defBase": m.group(2), "tacticsBase": m.group(3),
                   "atkGrowth": m.group(4), "defGrowth": m.group(5), "tacticsGrowth": m.group(6)}
            m2 = re.search(r"コスト\|指揮\|スキル\|" + re.escape(g["no"]) + r"\|[^|]*\|([\d.]+)\|(\d+)\|", t)
            if m2:
                exp["cost"], exp["lv0Troops"] = m2.group(1), m2.group(2)
            d = []
            for k, v in exp.items():
                got = g.get(k)
                if got is None:
                    continue
                try:
                    if abs(float(got) - float(v)) > 0.001:
                        d.append("    %s: サイト=%s / ixanary=%s" % (k, got, v))
                except Exception:
                    pass
            if d:
                ng += 1
                stat_bad.append("[%s] %s No.%s\n%s" % (status(g), g["name"], g["no"], "\n".join(d)))
            else:
                ok += 1
        ext.append("\nステータス照合: 一致%d体 / 不一致%d体 / 照合不可%d体\n" % (ok, ng, skip))
        ext += stat_bad

    # ---- 出力 ----
    order = {"HIGH": 0, "MID": 1, "LOW": 2}
    R.sort(key=lambda x: (order[x[1]], x[0]))
    with io.open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8") as f:
        f.write("=== 武将・スキルデータ監査 ===\n対象 %d体 / スキル %d件\n\n--- サマリ ---\n"
                % (len(targets), len(D["skills"])))
        for (cat, sev), n in sorted(collections.Counter((c, s) for c, s, m in R).items(),
                                    key=lambda kv: (order[kv[0][1]], -kv[1])):
            f.write("  [%s] %s: %d件\n" % (sev, cat, n))
        f.write("\n内部整合性 合計 %d件\n" % len(R))
        cur = None
        for cat, sev, msg in R:
            if (cat, sev) != cur:
                cur = (cat, sev)
                f.write("\n===== [%s] %s =====\n" % (sev, cat))
            f.write("  " + msg + "\n")
        if ext:
            f.write("\n\n########## 一次情報源(ixanary)との照合 ##########\n")
            f.write("\n".join(ext) + "\n")
        elif not ONLINE:
            f.write("\n(--online を付けると ixanary との外部照合も行う)\n")
    print("wrote " + os.path.join(OUT, "report.txt"))

    # pre-commitフックが差分を取れるよう、機械可読な形でも出す。
    # 本文(msg)をそのままキーにすると件数や武将名の変化で別物になってしまうので、
    # 「種別+深刻度+本文」の組をそのまま指紋として使い、集合の差で新規発生を判定する。
    with io.open(os.path.join(OUT, "findings.json"), "w", encoding="utf-8") as f:
        json.dump([{"cat": c, "sev": s, "msg": m} for c, s, m in R],
                  f, ensure_ascii=False, indent=1)
    print("wrote " + os.path.join(OUT, "findings.json"))


if __name__ == "__main__":
    main()
