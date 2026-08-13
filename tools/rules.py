# -*- coding: utf-8 -*-
"""ルール索引と違反ログの整合を見る。監査(audit_characters.py)から呼ばれる。

なぜ要るか(C-3/C-4/D-5、2026-08-12レッドチーム指摘):

 C-3 違反ログのIDを書く側(=違反した本人)が選んでいた。
     同じ原因の違反でも別IDを付ければ「2回目」の扱いを避けられる。
     実際 I-01(調べずに未確認と書いた)と S-01(自作の狭い検査で自己合格)は
     どちらも「自分の判定を自分で承認した」という同じ原因なのに別IDになっていて、
     いちばん重いペナルティが発火しなかった。
     → IDは索引に実在するものに限る。加えて根本原因のタグを別列で持ち、
       IDが違ってもタグが同じなら2回目として数える。

 C-4 違反ログは手で書くので、書かなければ何も起きない。
     → 「監査に足したか=まだ」の行を毎回数えて出す。放置が見えるようにする。

 D-5 棚卸しは「月1回」と書いてあるだけで、誰も呼ばないと永久に来ない。
     → 最終棚卸し日と、索引の件数表記が実数と合っているかを毎回見る。
       件数がずれていたら索引が腐り始めた合図。

    python tools/rules.py          # 今の状態を見る
"""
import collections
import datetime
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "docs", "RULES.md")
VIOL = os.path.join(ROOT, "docs", "RULE-VIOLATIONS.md")
OPER = os.path.join(ROOT, "docs", "RULE-OPERATION.md")
STALE_DAYS = 31


def _read(p):
    if not os.path.exists(p):
        return ""
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def rule_ids():
    """索引の本体テーブルにあるID。末尾のまとめ表(「D-01 / D-02」のような
    複数IDをまとめた行)は拾わないよう、1セル1IDの行だけを見る。"""
    out = {}
    for line in _read(RULES).split("\n"):
        m = re.match(r"^\|\s*([A-Z]-\d{2})\s*\|(.*)$", line)
        if m:
            out.setdefault(m.group(1), m.group(2).split("|")[0].strip())
    return out


SEV = ("軽", "中", "重")
# 監査の種別ではなく、門番の名前。これらは注入ケースの対象にならない。
TOOL_NAMES = {"pre-commit", "pre-push", "pre-merge-commit", "PreToolUse",
              "audit_selftest", "check_js", "install_hooks", "lock"}


def _table_lines(text, header_key):
    """指定の見出しを持つ表の、データ行だけを返す。

    E-8 対応: 以前は「| で始まり8列で日付形式に一致する行」だけを拾い、
    条件を満たさない行は**黙って捨てて**いた。列を1つ減らす・日付を `2026/08/13` にする・
    行頭に空白を1つ入れる、のどれかで違反行を検査から消せた(実証済み)。
    表の範囲を先に決めてから、その中の行は全部「解釈できたか」を見る。
    """
    out, inside = [], False
    for raw in text.split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s.startswith("|"):
            if inside and s == "":
                inside = False
            continue
        cells = [x.strip() for x in s.strip("|").split("|")]
        if not inside:
            if header_key in cells:
                inside = True
            continue
        if set("".join(cells)) <= set("-: "):     # 区切り行
            continue
        out.append((raw, cells))
    return out


def violations():
    """違反ログの行。列は | 日付 | ID | 根本原因 | 区分 | 何をしたか | 影響 | 対応 | 監査 |

    解釈できない行は捨てずに parse_error として返す。捨てると隠せてしまうため。
    """
    text = _read(VIOL)
    rows = []
    seen = set()
    for raw, c in _table_lines(text, "根本原因"):
        seen.add(raw.strip())
        if len(c) != 8 or not re.match(r"^\d{4}-\d{2}-\d{2}$", c[0]):
            rows.append({"parse_error": raw.strip()[:110], "date": "", "id": "",
                         "cause": "", "sev": "", "what": "", "impact": "",
                         "fix": "", "audit": ""})
            continue
        rows.append({"parse_error": None, "date": c[0], "id": c[1], "cause": c[2],
                     "sev": c[3], "what": c[4], "impact": c[5], "fix": c[6],
                     "audit": c[7]})

    # F-3/H-5/I-6D(2026-08-13 第3回、3体が指摘): 正規の表の外に書かれた行は
    # parse_error にもならず黙って消えていた。表の途中に空行を1つ入れる、
    # 見出し語の違う別表へ移す、のどちらでも「人間には違反ログ、機械には存在しない」
    # 状態が作れた。ファイル全体から日付で始まる行を拾い、表に入っていなければ報告する。
    for line in text.split("\n"):
        s = line.strip()
        if not re.match(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|", s):
            continue
        if s in seen:
            continue
        rows.append({"parse_error": "正規の表の外にある: " + s[:90], "date": "",
                     "id": "", "cause": "", "sev": "", "what": "", "impact": "",
                     "fix": "", "audit": ""})
    return rows


def cause_tags():
    """ログ末尾のタグ表に載っている根本原因タグ。ここに無いタグは使えない。"""
    return {c[0] for _raw, c in _table_lines(_read(VIOL), "タグ") if len(c) >= 2}


def selftest_covered():
    """`audit_selftest.py` に注入ケースがあるチェック種別。

    「自己承認」が3回目になったので(2026-08-13 I-01)、instanceではなくパターンを塞ぐ。
    3回とも形は同じ: **チェックを足したと書いたが、それが鳴ることを確かめていない。**
    種別が実在するかだけでなく、**鳴ることを示す注入ケースがあるか**まで見る。

    L-2 / M-5(2026-08-13 第4回レッドチーム): ここは
    `^\\s{4}\\("([^"]+)",` という**テキストの正規表現**だった。
    走らないリストや docstring に4スペース字下げで1行足すだけで
    「注入ケースがある」ことにでき、しかも自己テストの未カバー件数も動かなかった。
    実際に CASES / EVASIONS に入っているものだけを構文木から取る。
    """
    import ast
    src = _read(os.path.join(ROOT, "tools", "audit_selftest.py"))
    if not src:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not ({"CASES", "EVASIONS"} & set(names)):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for el in node.value.elts:
            # ("種別", ...) と ("種別", ファイル) + v_set(...) の両方の形がある
            first = None
            if isinstance(el, ast.Tuple) and el.elts:
                first = el.elts[0]
            elif isinstance(el, ast.BinOp) and isinstance(el.left, ast.Tuple) \
                    and el.left.elts:
                first = el.left.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(first.value)
    return out


def gate_rule_ids():
    """門番の自己テスト(gate_selftest.py)に筋書きがあるルールID。

    L-1(第4回)対応。`足した(pre-commit)` のような門番名を認めるのは、
    その門番がそのルールで実際に止まることを示す筋書きがあるときだけ。
    """
    import ast
    src = _read(os.path.join(ROOT, "tools", "gate_selftest.py"))
    if not src:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "CASES" for t in node.targets):
            out = set()
            for el in getattr(node.value, "elts", []):
                if isinstance(el, ast.Tuple) and el.elts \
                        and isinstance(el.elts[0], ast.Constant):
                    out.add(el.elts[0].value)
            return out
    return set()


def known_checks():
    """監査が実際に出しうるチェック種別の名前。

    「監査に足した」と書いたときに、その種別が本当に存在するかを照合するために使う。

    S-4 / T-5 / Y-6(2026-08-13 第5回、3体が指摘): ここは正規表現だった。
    `lock.check_names()` は同じ穴を塞いで構文木から取るようにしたのに、
    **こちらは直っていなかった**。コメントや docstring に `add("架空の種別")` と
    1行書くだけで、違反ログの「足した(架空の種別)」が実在照合を通った。
    錠前と同じ AST の実装を使う(2つ持たない)。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import lock as L
    L.ROOT = ROOT
    names = set(L.check_names())
    # 監査ではなく pre-commit / PreToolUse で止めているものも「機械で見ている」に含める
    names |= TOOL_NAMES
    return names


def last_inventory():
    m = re.search(r"最終棚卸し:\s*(\d{4}-\d{2}-\d{2})", _read(RULES))
    return m.group(1) if m else None


def _guard(out, label, fn, *a):
    """区画ごとに囲って走らせる。

    X-5(2026-08-13 第5回): ここは1本の長い関数だったので、途中で例外が出ると
    **それ以降の約30種別がまるごと出力から消えた**。
    `docs/RULES.md` の「最終棚卸し: 2026-99-99」の1行で
    `datetime.date.fromisoformat` が落ち、違反ログの検査まで全部黙る。
    区画ごとに囲って、落ちた区画だけを指摘に変える。
    """
    try:
        fn(out, *a)
    except Exception as e:
        out.append(("ルール検査が例外で落ちた", "HIGH",
                    "「%s」の検査が %s: %s で落ちた。"
                    "この区画の指摘は**一切出ていない**。素材(RULES.md/違反ログ/"
                    "settings.json/rules.yml)の書式が壊れていないか先に直す"
                    % (label, type(e).__name__, e)))


def _p_ruledoc(out, ids, n):
    """ルール索引の件数と棚卸しの期限。"""
    # D-5: 索引の件数表記が実数とずれていないか。ずれ=棚卸しがされていない合図。
    for f in ("docs/RULES.md", "docs/RULE-OPERATION.md",
              ".claude/agents/data-writer.md", ".claude/agents/kanshi-yaku.md"):
        for m in re.finditer(r"(\d+)\s*件の索引", _read(os.path.join(ROOT, f))):
            if int(m.group(1)) != n:
                out.append(("ルール件数の表記ずれ", "MID",
                            "%s が「%s件の索引」と書いているが実数は%d件。"
                            "棚卸し(RULE-OPERATION.md)をしていない合図"
                            % (f, m.group(1), n)))

    # D-5: 棚卸しの期限
    d = last_inventory()
    if not d:
        out.append(("棚卸しの記録が無い", "MID",
                    "docs/RULES.md に「最終棚卸し: YYYY-MM-DD」の行が無い。"
                    "期限を機械で見られない"))
    else:
        age = (datetime.date.today() - datetime.date.fromisoformat(d)).days
        if age > STALE_DAYS:
            out.append(("棚卸しの期限切れ", "MID",
                        "最終棚卸しが %s(%d日前)。%d日を超えた。"
                        "RULE-OPERATION.md「定期的な棚卸し」を実施する" % (d, age, STALE_DAYS)))


def retired_sources():
    """`docs/RULES.md` が「使わない」と書いている情報源を、索引から読み取る。

    一覧をここに書き写すと、索引を直したときにこちらが古いままになる(それが W-14)。
    索引を唯一の入口にしたまま機械が読めるように、索引の本文から取る。
    """
    out = set()
    for line in _read(RULES).split("\n"):
        for m in re.finditer(r"([^|。、]*)は使わない", line):
            out |= set(re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*\."
                                  r"(?:com|info|net|jp|org)", m.group(1)))
    return out


# 「もう使わない」と分かる書き方。これが同じ行にあれば、注記なので通す。
_RETIRED_OK = ("使わない", "使用しない", "参照しない", "候補から外", "外れた",
               "除外", "廃止", "やめ", "旧", "禁止")


def _p_sources(out, ids, n):
    """廃止した情報源が、手順の原文に残っていないか。

    W-14(2026-08-12)そのもの。I-02(情報源の変更)を `docs/RULES.md` にだけ書いて、
    マニュアル13箇所・エージェント定義5箇所が旧ソースを指したままだった。
    「索引を直したが原文を直していない」を機械で見る。

    対象は**手順**だけ。ファイル名に日付が入っているものは、その日の作業記録なので
    当時の記述のままでよい(過去の記録まで書き換えるほうが害が大きい)。
    """
    dead = retired_sources()
    if not dead:
        return
    targets = []
    for rel in ("docs", ".claude/agents"):
        d = os.path.join(ROOT, rel.replace("/", os.sep))
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith(".md"):
                continue
            if re.search(r"\d{4}-\d{2}-\d{2}", name):   # その日の作業記録
                continue
            targets.append(rel + "/" + name)
    for rel in targets:
        for i, line in enumerate(_read(os.path.join(ROOT, *rel.split("/")))
                                 .split("\n"), 1):
            hit = sorted(s for s in dead if s in line)
            if not hit or any(w in line for w in _RETIRED_OK):
                continue
            out.append(("廃止した情報源が手順に残っている", "HIGH",
                        "%s:%d が %s を指したまま。索引(I-02)を直したときに"
                        "原文を直し忘れている。使わないなら、その行にそう書く"
                        % (rel, i, "/".join(hit))))


def _p_redteam(out, ids, n):
    """レッドチームの回が、開きっぱなし/閉じ損ねになっていないか(R-02/R-03)。

    `docs/redteam-log.txt` は追記だけの記録。START に対応する END が無ければ、
    回が閉じられていない = 本物が無傷だったことを誰も確かめていない。
    """
    # 記録そのものが消えた場合は錠前の PRESENT が「必須ファイルが消えた」で拾う。
    # ここで別種別を作ると、注入ケースを置けない(消す注入ができない)種別が増える。
    p = os.path.join(ROOT, "docs", "redteam-log.txt")
    # BD-2(第6回): この記録は錠前の PRESENT(存在するか)だけで、**中身は誰も
    # 守っていなかった**。_p_redteam の唯一の入力なので、行を消せば HIGH も MID も
    # 消えた。「ABORT の痕跡は永久に残る」は成立していなかった。
    # 追記しかしないファイルなので、コミット済みの内容が先頭に残っているかを見る。
    import subprocess as _sp
    r = _sp.run(["git", "show", "HEAD:docs/redteam-log.txt"], cwd=ROOT,
                capture_output=True, text=True, encoding="utf-8")
    if not r.returncode:
        cur = _read(p).replace("\r\n", "\n")
        if not cur.startswith((r.stdout or "").replace("\r\n", "\n")):
            out.append(("レッドチームの記録が書き換えられた", "HIGH",
                        "docs/redteam-log.txt は追記だけの記録なのに、"
                        "コミット済みの過去行が変わっている。"
                        "回の開閉・中断の履歴を消した疑い(R-02/R-03)"))
    opened, failed = None, []
    aborted, closed = [], set()
    for line in _read(p).split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        kind = line.split("\t")[0]
        if kind == "START":
            opened = line
        elif kind == "END":
            # BD-3(第6回): ここで failed も空にしていたので、本物を触って
            # END-FAILED が出ても、ツリーを戻して**次の回を普通に閉じるだけ**で
            # 「レッドチームが本物を触った」の HIGH が消えた。記録を触る必要すら
            # 無かった。END では消さない。消せるのは理由つきの ABORT だけ。
            opened = None
        elif kind == "ABORT":
            # 出口はあるが、痕跡は永久に残る。
            # 直前に END-FAILED(本物を触った)があったかも一緒に残す。
            aborted.append((line, len(failed)))
            opened, failed = None, []
        elif kind == "ABORT-CLOSED":
            # 2026-08-13: 以前はここが無く、一度でも中断すると CI が**永久に赤**
            # だった。永久に赤いCIは見なくなるので、いちばん危ない状態になる。
            # 記録を消せるようにするのではなく、「こう始末した」を**足せる**ように
            # した。ABORT の行はそのまま残り、その下に ABORT-CLOSED が並ぶ。
            # 追記専用・digestで連結・錠前で保護なので無かったことにはできない。
            closed.add(line.split("\t")[2] if len(line.split("\t")) > 2 else "")
        elif kind == "END-FAILED":
            failed.append(line)
    for line in failed:
        out.append(("レッドチームが本物を触った", "HIGH",
                    "回を閉じようとして差分が出ている: %s。"
                    "その回の指摘は無効。違反ログに「重」で記録して回をやり直す"
                    % line[:100]))
    # 実在しない中断に「始末をつけた」と書いてある = 記録の捏造か書式の壊れ。
    # 始末の記録は指摘を消す力があるので、宛先が実在することは機械で確かめる。
    for stamp in sorted(closed - set(l.split("\t")[1] for l, _ in aborted
                                     if len(l.split("\t")) > 1)):
        out.append(("レッドチームの始末の記録が宙に浮いている", "HIGH",
                    "%s の ABORT-CLOSED に対応する ABORT が記録に無い。"
                    "始末の記録は中断の指摘を消すので、宛先が実在しないものは通さない"
                    % (stamp or "(時刻なし)")))
    for line, nfail in aborted:
        c = line.split("\t")
        if len(c) > 1 and c[1] in closed:
            continue        # 始末の記録がある(ABORTの行自体は消えていない)
        out.append(("レッドチームの回を中断した", "MID",
                    "%s の回「%s」は --abort で抜けた%s: %s"
                    % (c[1] if len(c) > 1 else "?", c[3] if len(c) > 3 else "?",
                       "(本物に差分が出たあと)" if nfail else "",
                       c[4] if len(c) > 4 else "(理由なし)")))
    if opened:
        out.append(("レッドチームの回が閉じていない", "HIGH",
                    "%s に開いた回が閉じられていない。"
                    "python tools/redteam.py --end で本物が無傷か確かめる"
                    % opened.split("\t")[1] if "\t" in opened else opened[:60]))


def _p_settings(out, ids, n):
    """門番を起動する設定(.claude/settings.json)そのもの。"""
    # G-9/H-9/F-8(第3回): 門番を起動する設定そのものが、どの検査の対象でもなかった。
    # `.claude/settings.json` の hooks を消す、CIのトリガを1行変える、
    # ワークフローを消す、のどれも指摘0件で通った。中身を見る。
    # P-4 / M-2(2026-08-13 第4回): ここは部分文字列しか見ていなかったので、
    # `PreToolUse` を `PostToolUse` に**改名するだけ**でT-01フックが死ぬのに指摘0件だった
    # (PostToolUseはツール実行後なので deny に意味が無い)。JSONとして構造で見る。
    stp = os.path.join(ROOT, ".claude", "settings.json")
    st = _read(stp)
    if not st:
        out.append(("PreToolUseの設定が無い", "HIGH",
                    ".claude/settings.json が読めない。T-01を止めるフックの登録先"))
    else:
        try:
            import json as _json
            cfg = _json.loads(st)
        except Exception as e:
            cfg = None
            out.append(("PreToolUseの設定が壊れている", "HIGH",
                        ".claude/settings.json をJSONとして読めない: %s" % e))
        if cfg is not None:
            groups = (cfg.get("hooks") or {}).get("PreToolUse") or []
            # P-2(第4回): 門番やブランチ保護を外す操作を止めるフックも同様に配線を見る。
            if not any("no_protection_bypass.py" in (h.get("command") or "")
                       for g in groups for h in (g.get("hooks") or [])):
                out.append(("保護を外す操作の見張りが消えた", "HIGH",
                            ".claude/settings.json の hooks.PreToolUse に "
                            "no_protection_bypass.py の登録が無い。"
                            "ブランチ保護の削除・core.hooksPath の付け替え・"
                            "--no-verify を止めるフックが外れている"))
            wired = [(g.get("matcher") or "", h)
                     for g in groups for h in (g.get("hooks") or [])
                     if "no_heredoc_backslash.py" in (h.get("command") or "")]
            if not wired:
                out.append(("PreToolUseの配線が消えた", "HIGH",
                            ".claude/settings.json の hooks.PreToolUse に "
                            "no_heredoc_backslash.py の登録が無い。"
                            "T-01を止めるフックが外れている"
                            "(PostToolUse等へ改名されていないかも見る)"))
            else:
                mats = " ".join(m for m, _h in wired)
                if "Bash" not in mats:
                    out.append(("PreToolUseがBashを見ていない", "HIGH",
                                "matcher が %r で Bash を含まない" % mats))
                elif "PowerShell" not in mats:
                    out.append(("PreToolUseがBashだけ", "MID",
                                "matcher に PowerShell が入っていない。ツールを変えるだけで"
                                "T-01フックを素通りできる(第3回 F-9/G-12/I-11)"))
            # T-4 / V-4 / AA-6(第5回): matcher が Bash 系だけだったので、
            # **Write / Edit は一切見張られていなかった**。コマンドを実行せずに
            # `.git/hooks/pre-commit` を書き換えれば門番を無力化できた。
            wmats = " ".join(g.get("matcher") or "" for g in groups
                             for h in (g.get("hooks") or [])
                             if "no_git_internal_write.py" in (h.get("command") or ""))
            if not wmats:
                out.append((".git直接書き換えの見張りが消えた", "HIGH",
                            ".claude/settings.json の hooks.PreToolUse に "
                            "no_git_internal_write.py の登録が無い。"
                            "Write/Edit で .git/hooks を上書きすれば門番を消せる"))
            elif "Write" not in wmats or "Edit" not in wmats:
                out.append((".git直接書き換えの見張りが消えた", "HIGH",
                            "no_git_internal_write.py の matcher が %r。"
                            "Write と Edit の両方を含める必要がある" % wmats))
            # R-04: レッドチームの回の見張り。回が開いている間だけ効くので、
            # 配線が外れていても普段は何も起きない = 気づけない。配線自体を見る。
            rmats = " ".join(g.get("matcher") or "" for g in groups
                             for h in (g.get("hooks") or [])
                             if "no_redteam_write.py" in (h.get("command") or ""))
            need = ("Bash", "Write", "Edit")
            if not rmats:
                out.append(("レッドチームの見張りが消えた", "HIGH",
                            ".claude/settings.json の hooks.PreToolUse に "
                            "no_redteam_write.py の登録が無い。攻撃側が本物の"
                            "作業ツリーを触れてしまう(R-04)"))
            elif any(x not in rmats for x in need):
                out.append(("レッドチームの見張りが消えた", "HIGH",
                            "no_redteam_write.py の matcher が %r。"
                            "%s を全部含める必要がある" % (rmats, "/".join(need))))

    # AA-5(第5回): `.claude/settings.local.json` は誰も見ていなかった。
    # 個人設定は後勝ちなので、ここに hooks を書けば共有側の門番を丸ごと上書きできる。
    # gitignore 済み=共有されない=**差分にも出ない**ので、いちばん痕跡が残らない。
    lp = os.path.join(ROOT, ".claude", "settings.local.json")
    if os.path.exists(lp):
        try:
            import json as _json
            loc = _json.loads(_read(lp) or "{}")
        except Exception as e:
            out.append(("個人設定が壊れている", "MID",
                        ".claude/settings.local.json をJSONとして読めない: %s" % e))
            loc = {}
        if loc.get("hooks"):
            out.append(("個人設定が門番を上書きしている", "HIGH",
                        ".claude/settings.local.json に hooks がある。"
                        "個人設定は後勝ちなので、共有側の PreToolUse を無効化できる。"
                        "フックの定義は .claude/settings.json だけに置く"))
        for key in ("disableAllHooks", "disableBypassPermissionsMode"):
            if key in loc:
                out.append(("個人設定が門番を上書きしている", "HIGH",
                            ".claude/settings.local.json に %s がある" % key))


def _p_ci(out, ids, n):
    """CI のワークフローが本当に落ちる作りか。"""
    # P-1(2026-08-13 第4回): ここも部分文字列だったので、各ステップに `|| true` を
    # 足すだけでCIが全部緑のまま無言化できた(文字列は壊れず continue-on-error でもない)。
    # しかも自己テストの「CIを赤でも通るようにする」という回避ケース自体が回避された。
    # 実行される run の中身を1行ずつ見る。
    wf = _read(os.path.join(ROOT, ".github", "workflows", "rules.yml"))
    if not wf:
        out.append(("CIのワークフローが無い", "HIGH",
                    ".github/workflows/rules.yml が読めない。"
                    "ローカルのフックを無効化されたときの唯一の受け皿"))
    else:
        for need, why in (('branches: ["**"]', "全ブランチのpushで走る設定"),
                          ("audit_characters.py", "監査"),
                          ("tools/rules.py", "索引と違反ログの整合"),
                          ("check_js.py", "ページのJS構文"),
                          ("check_generated.py", "生成物とデータの一致"),
                          ("audit_selftest.py", "監査の自己テスト"),
                          ("gate_selftest.py", "門番の自己テスト"),
                          ("tools/lock.py", "錠前")):
            if need not in wf:
                out.append(("CIの検査が抜けている", "HIGH",
                            "rules.yml に「%s」が無い(%s)" % (need, why)))
        if "continue-on-error" in wf:
            out.append(("CIが失敗しても止まらない", "HIGH",
                        "rules.yml に continue-on-error がある。赤でも通ってしまう"))
        # 失敗を握り潰す書き方を、行単位で拾う
        for i, line in enumerate(wf.split("\n"), 1):
            if "python tools/" not in line:
                continue
            for bad in ("|| true", "|| :", "|| exit 0", "; true", "set +e"):
                if bad in line:
                    out.append(("CIが失敗しても止まらない", "HIGH",
                                "rules.yml %d行目に「%s」がある: %s"
                                % (i, bad, line.strip()[:80])))
            if re.search(r"^\s*if\s*:\s*(false|\$\{\{\s*false)", line):
                out.append(("CIが失敗しても止まらない", "HIGH",
                            "rules.yml %d行目のステップが常にスキップされる" % i))


def _p_violations(out, ids, n):
    """違反ログの中身。"""
    all_rows = violations()
    if not all_rows and os.path.exists(VIOL):
        out.append(("違反ログを読めない", "HIGH",
                    "docs/RULE-VIOLATIONS.md から1行も取れない。列構成が変わった可能性"))

    # E-8: 解釈できない行を黙って捨てない。捨てれば書式を崩すだけで隠せる。
    for r in all_rows:
        if r["parse_error"]:
            out.append(("違反ログの行を解釈できない", "HIGH",
                        "8列 + 日付YYYY-MM-DD の形になっていない: 「%s」。"
                        "崩れた行は検査から外れるので、書式を直す" % r["parse_error"]))
    rows = [r for r in all_rows if not r["parse_error"]]

    # C-3: 実在しないIDを書けば、そのルールは「初犯」のままにできてしまう
    tags = cause_tags()
    checks = known_checks()
    for r in rows:
        if r["id"] not in ids:
            out.append(("違反ログのIDが索引に無い", "HIGH",
                        "%s の ID「%s」は docs/RULES.md に無い。"
                        "実在するルールIDを使う(無いなら先に索引へ追加する)"
                        % (r["date"], r["id"])))
        # E-9: 区分は3語のみ。「中程度」等と書けば未対応の集計から外れてしまう。
        if r["sev"] not in SEV:
            out.append(("違反ログの区分が不正", "HIGH",
                        "%s %s の区分「%s」は 軽/中/重 のいずれでもない"
                        % (r["date"], r["id"], r["sev"])))
        # E-9: タグを毎回新造すれば、根本原因での2回目判定が永久に発火しない。
        if r["cause"] and r["cause"] not in tags:
            out.append(("違反ログのタグが表に無い", "HIGH",
                        "%s %s のタグ「%s」は末尾のタグ表に無い。"
                        "その場限りのタグを作らない(2回目判定が無効になる)"
                        % (r["date"], r["id"], r["cause"])))
        # G-4: タグを空欄にすると照合もCounterも素通りし、永久に初犯にできた。
        if not r["cause"]:
            out.append(("違反ログのタグが空", "HIGH",
                        "%s %s: 根本原因の欄が空。空欄だと2回目の判定から外れる"
                        % (r["date"], r["id"])))
        # E-9: 「足した」と書くだけで停止が解除できた。種別名を要求し、実在を照合する。
        a = r["audit"]
        if a.startswith("足した"):
            m = re.match(r"^足した\((.+)\)$", a)
            if not m:
                out.append(("監査に足した根拠が書式外", "HIGH",
                            "%s %s: 「足した(チェック種別)」の形で書く。今は「%s」"
                            % (r["date"], r["id"], a[:40])))
            else:
                # 「/」ではパス名が割れるので区切りに使わない
                named = [x.strip() for x in re.split(r"[+、,]", m.group(1))]
                # F-6/G-3/H-4(第3回、3体が指摘): 双方向の部分一致だったので
                # 「足した(e)」の1文字で通った("e" が "pre-commit" の部分文字列)。
                # 完全一致にする。
                unknown = [x for x in named if x not in checks]
                # 「自己承認」3回目(2026-08-13 I-01)への対処。
                # 種別が実在しても、鳴ることを確かめていなければ「足した」とは言えない。
                # 実際 I-01 は、種別も文言もあるのに配線が無く、検査は常に空だった。
                cov = selftest_covered()
                # L-1(2026-08-13 第4回レッドチーム): 門番名(pre-commit / lock 等)は
                # 実在照合も自己テスト要求も素通りする**フリーパス**になっていた。
                # 「足した(pre-commit)」の12文字で、いちばん重い2回目=作業停止が消えた。
                # pre-commit がそのルールを本当に見ているかは誰も検査していない。
                # 門番名を書くときは、それが何を止めるのかの説明を併記させる。
                bare_tool = [x for x in named if x in TOOL_NAMES]
                # Y-1 / T-1 / W-3(2026-08-13 第5回、3体が指摘):
                # `len(named) == len(bare_tool)` だったので、**無関係な監査種別を
                # 1語併記するだけ**で照合が丸ごとスキップされた。
                # 現に自分で書いた W-14 の行がその形になっていた
                # (「ルール件数の表記ずれ」は「原文18箇所を直さなかった」と無関係)。
                # 併記は免罪符にしない。門番名を書いた時点で筋書きを要求する。
                if bare_tool and r["id"] not in gate_rule_ids():
                    out.append(("門番名だけで済ませている", "HIGH",
                                "%s %s: 「%s」だけでは、その門番がこのルールを"
                                "本当に見ているか誰も確かめられない。"
                                "tools/gate_selftest.py に %s の筋書きを足して"
                                "止まることを示す(監査の種別名の併記では免除しない。"
                                "第5回 Y-1: 無関係な種別を1語足すだけで外せた)"
                                % (r["date"], r["id"], "/".join(bare_tool), r["id"])))
                audit_cats = {x for x in named if x in checks and x not in TOOL_NAMES}
                unproven = sorted(audit_cats - cov)
                if unproven:
                    out.append(("足したチェックに自己テストが無い", "HIGH",
                                "%s %s: 「%s」に注入ケースが無い。"
                                "鳴ることを確かめていないものを「足した」と書かない"
                                "(audit_selftest.py の CASES / EVASIONS に追加する)"
                                % (r["date"], r["id"], "/".join(unproven)[:60])))
                if unknown:
                    out.append(("監査に足したチェックが実在しない", "HIGH",
                                "%s %s: 「%s」という名前のチェックは無い。"
                                "audit_characters.py / rules.py が出す種別名そのもの、または"
                                "pre-commit / pre-merge-commit / pre-push / PreToolUse / "
                                "check_js / audit_selftest / install_hooks のいずれかを書く"
                                % (r["date"], r["id"], "/".join(unknown)[:60])))

    # C-3: 2回目の判定。IDだけでなく根本原因のタグでも数える。
    for label, key in (("ルールID", "id"), ("根本原因", "cause")):
        for k, c in collections.Counter(r[key] for r in rows if r[key] and r[key] != "-").items():
            if c < 2:
                continue
            # 「不可(層3)」を済み扱いにすると、いちばん繰り返す種類ほど
            # 「機械化できません」と書くだけで2回目の停止を免れてしまう。
            # 2回起きた時点で「記憶では守れない」と確定した以上、
            # 部分的にでも機械で見る手段を作るまでは済みにしない。
            done = all(r["audit"].startswith("足した") for r in rows if r[key] == k)
            if not done:
                out.append(("2回目の違反で作業停止中", "HIGH",
                            "%s「%s」が%d回目。監査にチェックを足すまで該当作業を再開しない"
                            "(RULE-OPERATION.md「同じルールを2回破ったとき」)。"
                            "全部は機械化できなくても、部分的に見られる形にしてから再開する"
                            % (label, k, c)))

    # C-4: 未対応の宿題が見えないまま溜まるのを防ぐ
    open_ = [r for r in rows if r["sev"] in ("中", "重") and r["audit"] in ("まだ", "未", "")]
    if open_:
        out.append(("違反ログに未対応が残っている", "MID",
                    "区分が中/重で監査へ未反映の行が%d件(%s)。"
                    "落とせないなら「不可(層3)」と書いて理由を残す"
                    % (len(open_), "/".join(r["id"] for r in open_))))
    return out


def problems():
    """(種別, 深刻度, 本文) のリスト。監査がそのまま指摘として出す。"""
    out = []
    try:
        ids = rule_ids()
    except Exception as e:
        out.append(("ルール検査が例外で落ちた", "HIGH",
                    "ルール索引を読めない: %s: %s" % (type(e).__name__, e)))
        return out
    n = len(ids)
    for label, fn in (("ルール索引と棚卸し", _p_ruledoc),
                      ("廃止した情報源", _p_sources),
                      ("レッドチームの回", _p_redteam),
                      ("PreToolUseの設定", _p_settings),
                      ("CIのワークフロー", _p_ci),
                      ("違反ログ", _p_violations)):
        _guard(out, label, fn, ids, n)
    return out

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ids = rule_ids()
    print("ルール: %d件 %s" % (len(ids), dict(collections.Counter(k[0] for k in ids))))
    print("最終棚卸し: %s" % (last_inventory() or "記録なし"))
    rows = violations()
    print("違反ログ: %d件" % len(rows))
    for r in rows:
        print("  %s %-5s %-3s %-22s 監査=%s" % (r["date"], r["id"], r["sev"],
                                                r["cause"][:22], r["audit"]))
    p = problems()
    print("\n指摘 %d件" % len(p))
    for cat, sev, msg in p:
        print("  [%s] %s: %s" % (sev, cat, msg))
    # I-7(b)(第3回レッドチーム): 印字するだけで終了コードを変えていなかったので、
    # CIの「ルール索引と違反ログの整合」ステップは絶対に失敗しない飾りだった。
    sys.exit(1 if p else 0)
