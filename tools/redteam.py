# -*- coding: utf-8 -*-
"""レッドチームの回を開き、閉じる。本物の作業ツリーが1バイトも動いていないことを保証する。

## なぜ要るか(2026-08-13)

レッドチームには「本物の作業ツリーは読むだけ。攻撃は使い捨てクローンの中で」と
**文章で**渡していた。守られているかを確かめる手段は無く、報告の自己申告だけだった。
これはこの仕組み全体が潰そうとしている「検証をサボって成功を自己申告する」と同じ形で、
しかも**攻撃側にそれを許していた**。攻撃側が本物を触れるなら、
「緑のまま通した」という戦果が本物の改変によるものかどうかも区別できない。

なので回の前後で**指紋**を採り、機械で突き合わせる。

    python tools/redteam.py --start "第6回 BA〜BJ"   # 回を開く(汚れたツリーでは開けない)
    python tools/redteam.py --check                  # 途中でも何度でも
    python tools/redteam.py --end                    # 回を閉じる(差分があれば閉じない)
    python tools/redteam.py --status

回が開いている間は、PreToolUse フック(`no_redteam_write.py`)が
本物のリポジトリへの書き込みを機械的に拒否する。指紋はその**二重の**保険。
フックはツール経由の書き込みしか見られないが、指紋は結果だけを見るので、
どんな経路で書かれても後から必ず分かる。

開始・終了は `docs/redteam-log.txt` にも残る(追跡ファイルなので、消せば git の差分に出る)。
"""
import argparse
import datetime
import shutil
import hashlib
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "docs", "redteam-log.txt")

# 指紋から外すもの。「走らせれば必ず変わる」ものだけ。
SKIP_DIRS = (".git", "__pycache__", "node_modules", "tools/audit_out",
             ".claude/worktrees", ".claude/sounds", ".pytest_cache")
# .git の中でも、門番の実体と設定は見る。
# 第6回 BG: `.git/config` が指紋の外だったので、そこに core.hooksPath を書けば
# 門番を丸ごと外したまま「本物は無傷」と表示できた。
GIT_WATCH = (".git/hooks", ".git/config", ".git/info",
             # EE-1(第10回、高): 参照が指紋の外だったので、クローンから
             # `push origin HEAD:refs/heads/x` で本物に検査を通していない
             # コミットを入れても「無傷」と出た。作業ツリーは動かないが、
             # リポジトリの中身は動いている。
             ".git/refs", ".git/packed-refs", ".git/HEAD")
# この道具自身の追記記録。指紋を採った直後に START 行を書くので、
# 含めると必ず自分で自分を「変わった」と判定して回が閉じられなくなる(実地1回目の不具合)。
# 追跡ファイルなので改竄は git の差分に出るし、開閉の対応は rules.py が見る。
SKIP_FILES = ("docs/redteam-log.txt",)


def state_dir():
    """回の記録はリポジトリの**外**に置く。中に置くと攻撃対象になる。"""
    home = os.path.expanduser("~")
    key = hashlib.sha256(ROOT.lower().encode("utf-8")).hexdigest()[:12]
    d = os.path.join(home, ".claude", "redteam", key)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def active_path():
    return os.path.join(state_dir(), "active.json")


def _rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _skip(rel):
    if rel in SKIP_FILES:
        return True
    if rel.startswith(".git/") and not any(rel == w or rel.startswith(w + "/")
                                           for w in GIT_WATCH):
        return True
    for s in SKIP_DIRS:
        if s == ".git":
            continue
        if rel == s or rel.startswith(s + "/"):
            return True
    return False


def fingerprint():
    """作業ツリー全体の指紋。追跡・未追跡・無視、すべて見る。

    git の管理下かどうかで分けない。`.gitignore` に足してから書き換える、が
    いちばん簡単な抜け道になるため。
    """
    out = {}
    # 参照は「ファイルとして」だけでなく一覧としても採る。
    # packed-refs と loose ref のどちらに入るかは git の都合で変わるため。
    r = subprocess.run(["git", "for-each-ref",
                        "--format=%(refname) %(objectname)"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8")
    out["@refs"] = hashlib.sha256(
        (r.stdout or "").encode("utf-8")).hexdigest()[:16]
    for base, dirs, files in os.walk(ROOT):
        rb = _rel(base) if base != ROOT else ""
        dirs[:] = [d for d in sorted(dirs)
                   if not _skip((rb + "/" + d).lstrip("/"))]
        for name in sorted(files):
            rel = (rb + "/" + name).lstrip("/")
            if _skip(rel):
                continue
            p = os.path.join(base, name)
            try:
                with open(p, "rb") as f:
                    h = hashlib.sha256(f.read()).hexdigest()[:16]
                st = os.stat(p)
                # 第6回 BD-4: 中身のハッシュだけだと「書き換えて→実行して→戻す」が
                # 前後2点の比較では見えない。更新時刻と大きさも一緒に採る。
                # 素朴な復元(内容を戻すだけ)はこれで必ず差分になる。
                out[rel] = "%s:%d:%d" % (h, st.st_mtime_ns, st.st_size)
            except OSError as e:
                out[rel] = "読めない: %s" % e
    return out


def digest(files):
    """指紋全体を1つのハッシュにまとめる。追跡ファイルに焼き付けるため。

    第6回 BD-5: 指紋の実体 `~/.claude/redteam/<key>/active.json` はリポジトリの外に
    あり、錠前もフックもCIも見ていなかった。サンドボックスの python は無条件で
    走らせられるので、`files` を「今のツリー」で上書きすれば、本物を恒久的に
    改変したままでも `--end` が「無傷だった」と言った。
    START のときに全体のダイジェストを**追跡ファイル**(docs/redteam-log.txt)へ書き、
    --end はそれと突き合わせる。書き換えるには追跡ファイルの過去行を触ることになり、
    それは append-only の検査(_log_tampered)に引っかかる。
    """
    s = "\n".join("%s\t%s" % (k, files[k]) for k in sorted(files))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _log_tampered():
    """記録の過去行が書き換えられていないか(追記だけのはず)。"""
    r = subprocess.run(["git", "show", "HEAD:docs/redteam-log.txt"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        return None                     # まだコミットされていない
    old = (r.stdout or "").replace("\r\n", "\n")
    cur = ""
    if os.path.exists(LOG):
        cur = io.open(LOG, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    if not cur.startswith(old):
        return "docs/redteam-log.txt の過去行が書き換えられている(追記しかしないはず)"
    return None


def started_digest(started=None):
    """記録に残っている START のダイジェスト。

    CD-2(第7回): 「最後の START」を見ていたので、START 行を1本**追記**するだけで
    ダイジェストを付け替えられた。追記なので改竄検査(prefix)も通ってしまう。
    開いている回の開始時刻と一致する行だけを使い、END/ABORT を挟まない
    連続した START は異常として扱う。
    """
    if not os.path.exists(LOG):
        return None
    hit, open_start = None, False
    for line in io.open(LOG, encoding="utf-8", newline="").read().split("\n"):
        c = line.split("\t")
        if not c or not c[0]:
            continue
        if c[0] == "START":
            if open_start:
                return "連続START"      # 閉じずにもう1本開いている = 異常
            open_start = True
            if len(c) >= 7 and (started is None or c[1] == started):
                hit = c[6]
        elif c[0] in ("END", "ABORT", "END-FAILED"):
            if c[0] != "END-FAILED":
                open_start = False
    return hit


def head():
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                       capture_output=True, text=True)
    return (r.stdout or "").strip()


def dirty():
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    return [l for l in (r.stdout or "").split("\n") if l.strip()]


def log(line):
    prev = ""
    if os.path.exists(LOG):
        prev = io.open(LOG, encoding="utf-8", newline="").read()
    if prev and not prev.endswith("\n"):
        prev += "\n"
    io.open(LOG, "w", encoding="utf-8", newline="\n").write(prev + line + "\n")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def diff(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return added, removed, changed


def default_sandbox():
    """テスト環境の既定位置。リポジトリの外であることが唯一の条件。"""
    import tempfile
    return os.path.join(tempfile.gettempdir(), "claude").replace("\\", "/")


def cmd_start(label, sandbox):
    if os.path.exists(active_path()):
        a = json.load(io.open(active_path(), encoding="utf-8"))
        print("[停止] レッドチームの回がまだ開いている: %s(%s)" % (a["label"], a["started"]))
        print("先に python tools/redteam.py --end で閉じる。")
        return 1
    d = dirty()
    if d:
        # 汚れたまま始めると、回の途中で出た差分が「元からあったもの」に紛れる。
        print("[停止] 作業ツリーに未コミットの変更がある。攻撃前に片付ける: %d件" % len(d))
        for l in d[:10]:
            print("  " + l)
        return 1
    # 第7回 CB が狙っていた形: ラベルは1行1件の記録にそのまま入るので、
    # 改行を入れれば「END」の行を偽造できた(--abort だけが検疫していた)。
    for name, val in (("ラベル", label), ("テスト環境", sandbox or "")):
        if "\t" in val or "\n" in val or "\r" in val:
            print("[停止] %s にタブ・改行は使えない(記録が1行1件で壊れる)" % name)
            return 1
    sandbox = (sandbox or default_sandbox()).replace("\\", "/").rstrip("/")
    if not os.path.isabs(sandbox):
        print("[停止] テスト環境は絶対パスで指定する。")
        return 1
    low = sandbox.lower().rstrip("/")
    if low == ROOT.replace("\\", "/").lower() or \
            low.startswith(ROOT.replace("\\", "/").lower() + "/"):
        print("[停止] テスト環境をリポジトリの中に置かない: %s" % sandbox)
        return 1
    if not os.path.isdir(sandbox):
        os.makedirs(sandbox)
    fp = fingerprint()
    io.open(active_path(), "w", encoding="utf-8", newline="\n").write(
        json.dumps({"label": label, "started": now(), "head": head(),
                    "root": ROOT, "sandbox": sandbox, "files": fp},
                   ensure_ascii=False, indent=1))
    # 7列目に指紋全体のダイジェスト。追跡ファイルに焼き付けて、
    # リポジトリ外の記録(active.json)を差し替えられても気づけるようにする(BD-5)。
    log("START\t%s\t%s\t%s\t%d files\t%s\t%s"
        % (now(), head()[:12], label, len(fp), sandbox, digest(fp)))
    print("レッドチームの回を開いた: %s" % label)
    print("  HEAD      : %s" % head()[:12])
    print("  指紋      : %d ファイル" % len(fp))
    print("  テスト環境: %s" % sandbox)
    print("  記録      : %s" % active_path())
    print()
    print("この間、テスト環境の外への書き込みはフックが拒否する(ホワイトリスト方式)。")
    print("攻撃は使い捨てクローンの中だけで行う。")
    return 0


def cmd_check(quiet=False):
    if not os.path.exists(active_path()):
        print("開いている回は無い。")
        return 0
    a = json.load(io.open(active_path(), encoding="utf-8"))
    added, removed, changed = diff(a["files"], fingerprint())
    n = len(added) + len(removed) + len(changed)
    if head() != a["head"]:
        print("[違反] HEAD が動いている: %s → %s" % (a["head"][:12], head()[:12]))
        n += 1
    # BD-5: リポジトリ外の記録が差し替えられていないか。
    want = started_digest(a.get("started"))
    if want == "連続START":
        print("[違反] 記録に START が連続している。開いた回を閉じずに"
              "もう1本開いた形跡がある(ダイジェストの付け替え)")
        n += 1
        want = None
    if want and digest(a["files"]) != want:
        print("[違反] 指紋の記録が START のときと違う(%s → %s)。"
              "リポジトリ外の active.json が差し替えられた疑い"
              % (want, digest(a["files"])))
        n += 1
    # BD-2: 記録そのものの改竄。
    t = _log_tampered()
    if t:
        print("[違反] " + t)
        n += 1
    if n == 0:
        if not quiet:
            print("本物の作業ツリーは無傷(%d ファイル、差分なし)。回: %s"
                  % (len(a["files"]), a["label"]))
        return 0
    print("=" * 62)
    print("[違反] 攻撃中に本物の作業ツリーが変わっている: %d件" % n)
    print("=" * 62)
    for k in changed[:20]:
        print("  変更 %s" % k)
    for k in added[:20]:
        print("  追加 %s" % k)
    for k in removed[:20]:
        print("  消失 %s" % k)
    if n > 60:
        print("  ... ほか")
    print()
    print("この回の指摘は全部無効。docs/RULE-VIOLATIONS.md に「重」で記録し、")
    print("本物のツリーを元に戻してから、回をやり直す。")
    return 1


def cmd_end():
    if not os.path.exists(active_path()):
        print("開いている回は無い。")
        return 0
    a = json.load(io.open(active_path(), encoding="utf-8"))
    rc = cmd_check(quiet=True)
    if rc:
        log("END-FAILED\t%s\t%s\t%s" % (now(), head()[:12], a["label"]))
        print()
        print("差分があるので回は閉じない(閉じると次の検査もできなくなる)。")
        return 1
    log("END\t%s\t%s\t%s\t差分なし" % (now(), head()[:12], a["label"]))
    os.remove(active_path())
    print("回を閉じた: %s。本物の作業ツリーは無傷だった。" % a["label"])
    return 0


def cmd_abort(reason):
    """回を中断する。閉じられない状態から抜ける唯一の出口。

    `--end` は本物に差分があると閉じない(それでいい)。だが道具側の不具合で
    閉じられなくなることもある(実際、初日に自分の記録を指紋に入れていて詰まった)。
    出口が無いと、次に困った人が記録を手で消す。それがいちばん悪い。

    出口は残すが、**痕跡は永久に残す**: ABORT は追跡ファイルに書かれ、
    監査が毎回 MID で「中断した回がある」と言い続ける。
    ごまかすには追跡ファイルに嘘の理由を書くことになり、それは記録に残る嘘になる。
    """
    if len(reason.strip()) < 10:
        print("--reason は10文字以上。なぜ閉じられなかったのかを書く。")
        return 1
    if "\t" in reason or "\n" in reason:
        print("理由にタブ・改行は使えない(記録が1行1件で壊れる)。")
        return 1
    label = "(記録なし)"
    if os.path.exists(active_path()):
        label = json.load(io.open(active_path(), encoding="utf-8"))["label"]
        os.remove(active_path())
    log("ABORT\t%s\t%s\t%s\t%s" % (now(), head()[:12], label, reason))
    print("回を中断した: %s" % label)
    print("理由を docs/redteam-log.txt に残した。監査はこれを MID で報せ続ける。")
    return 0


def cmd_close_abort(stamp, reason):
    """中断した回に、始末をつけた記録を足す(2026-08-13)。

    なぜ要るか: ABORT の痕跡は消せない設計にしたが、**指摘を閉じる手段を
    用意していなかった**。そのため一度でも中断すると CI が永久に赤になる。
    永久に赤いCIは見なくなるので、いちばん危ない状態になる。
    実際、今日の中断2件でこの状態になった(どちらも道具の不具合と実証テスト)。

    消すのではなく**足す**。ABORT の行はそのまま残り、その下に
    ABORT-CLOSED が並ぶ。追記専用・digest で連結・錠前で保護なので、
    「無かったこと」にはできない。できるのは「こう始末した」と書き足すことだけ。
    """
    if len(reason.strip()) < 20:
        print("--reason は20文字以上。何をして始末をつけたのかを書く。")
        return 1
    if "\t" in reason or "\n" in reason:
        print("理由にタブ・改行は使えない(記録が1行1件で壊れる)。")
        return 1
    aborts, closed = [], set()
    for line in io.open(LOG, encoding="utf-8").read().split("\n"):
        c = line.split("\t")
        if c[0] == "ABORT" and len(c) > 1:
            aborts.append(c[1])
        elif c[0] == "ABORT-CLOSED" and len(c) > 2:
            closed.add(c[2])
    if stamp not in aborts:
        print("その時刻の ABORT が記録に無い: %s" % stamp)
        print("中断している回: %s" % (", ".join(x for x in aborts if x not in closed) or "なし"))
        return 1
    if stamp in closed:
        print("その回は既に始末がついている: %s" % stamp)
        return 1
    log("ABORT-CLOSED\t%s\t%s\t%s" % (now(), stamp, reason))
    print("中断した回 %s に始末の記録を足した。ABORT の行はそのまま残る。" % stamp)
    return 0


def cmd_status():
    if not os.path.exists(active_path()):
        print("開いている回は無い(本物のリポジトリへの書き込みは通常どおり)。")
        return 0
    a = json.load(io.open(active_path(), encoding="utf-8"))
    print("回が開いている: %s(%s から / HEAD %s / %d ファイル)"
          % (a["label"], a["started"], a["head"][:12], len(a["files"])))
    print("テスト環境: %s" % a.get("sandbox", "?"))
    print("この外への書き込みは拒否される(ホワイトリスト方式)。")
    return 0


def selftest():
    """道具そのものの自己テスト。CI と門番テストから叩く。

    実地で1回確かめても、次に誰かが直したときに壊れれば意味がない。
    ここで見るのは「壊れると気づけなくなる」2点だけ。
    """
    import tempfile
    ng = []

    # EE-1(第10回): 参照が指紋に入っているか。
    # 入っていないと、クローンから本物へ push しても「無傷」と出る。
    fp = fingerprint()
    if "@refs" not in fp:
        ng.append("指紋に参照(@refs)が入っていない。push を捕まえられない(EE-1)")
    else:
        # HA-3(第13回): 鍵の有無だけ見ていたので、値が参照を反映しなくなっても
        # (for-each-ref が失敗して空でも)緑のままだった。**振る舞い**を見る。
        r = subprocess.run(["git", "for-each-ref", "--format=%(refname)"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8")
        # 2026-08-13: ここは「出力が空でもNG」にしていた。CIの checkout は
        # detached HEAD でローカルブランチを作らないので for-each-ref が空になり、
        # Linux では必ず落ちていた(そのせいで CI は一度も緑になっていない)。
        # 参照が0本なのは壊れているのではなく、そういう状態というだけ。
        # HA-3 が本当に見たかったのは「参照が増えたら @refs が動くか」という
        # **振る舞い**で、それは下のクローンでの検査が担っている。そちらは
        # for-each-ref が壊れていれば必ず落ちるので、空を許しても弱くならない。
        if r.returncode:
            ng.append("git for-each-ref が失敗する。"
                      "@refs が中身の無いハッシュになる(EE-1)")
        else:
            # 使い捨てクローンで参照を1本足し、@refs が動くことを確かめる
            d = tempfile.mkdtemp(prefix="rt_refs_")
            rp = os.path.join(d, "r")
            subprocess.run(["git", "clone", "-q", ROOT.replace("\\", "/"), rp],
                           capture_output=True)
            keep = globals()["ROOT"]
            try:
                globals()["ROOT"] = rp
                if not os.path.isdir(os.path.join(rp, ".git")):
                    ng.append("使い捨てクローンを作れないので @refs の"
                              "振る舞いを確かめられない")
                else:
                    a = fingerprint().get("@refs")
                    # detached HEAD のクローンでもブランチは作れるように、
                    # 明示的に HEAD を指して1本足す。
                    subprocess.run(["git", "-C", rp, "branch",
                                    "rt-selftest-tmp", "HEAD"],
                                   capture_output=True)
                    b = fingerprint().get("@refs")
                    if a == b:
                        ng.append("参照を1本足しても @refs が変わらない。"
                                  "指紋が参照を見ていない(EE-1)")
            finally:
                globals()["ROOT"] = keep
                shutil.rmtree(d, ignore_errors=True)
    if not any(k.startswith(".git/refs") or k == ".git/HEAD" for k in fp):
        ng.append("指紋に .git/refs / .git/HEAD が入っていない(EE-1)")
    # .git の中でも見ない場所があること(全部入れると走らせるたびに変わる)
    if any(k.startswith(".git/objects") for k in fp):
        ng.append("指紋に .git/objects が入っている。走らせるたびに変わってしまう")

    # FG-4(第11回): 記録が「開いている」のに実体が無ければ、開いている扱いにする。
    sys.path.insert(0, os.path.join(ROOT, "tools", "hooks"))
    import no_redteam_write as W
    tmp = tempfile.mkdtemp(prefix="rt_self_")
    os.makedirs(os.path.join(tmp, "docs"))
    io.open(os.path.join(tmp, "docs", "redteam-log.txt"), "w",
            encoding="utf-8", newline="\n").write(
        "START\t2026-01-01 00:00:00\tdead\t自己テスト\t1 files\t/tmp\tdead\n")
    keep = W.ROOT
    try:
        W.ROOT = tmp
        if not W._log_says_open():
            ng.append("未クローズの START を「開いている」と読めていない(FG-4)")
        io.open(os.path.join(tmp, "docs", "redteam-log.txt"), "a",
                encoding="utf-8", newline="\n").write(
            "END\t2026-01-01 00:00:01\tdead\t自己テスト\t差分なし\n")
        if W._log_says_open():
            ng.append("END の後も「開いている」と読んでいる(FG-4)")
    finally:
        W.ROOT = keep
        shutil.rmtree(tmp, ignore_errors=True)

    for x in ng:
        print("  NG  " + x)
    print("指紋 %d項目 / 失敗 %d件" % (len(fp), len(ng)))
    return 1 if ng else 0


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--start", metavar="ラベル")
    p.add_argument("--sandbox", metavar="パス",
                   help="テスト環境の場所。既定は <TEMP>/claude")
    p.add_argument("--check", action="store_true")
    p.add_argument("--end", action="store_true")
    p.add_argument("--abort", action="store_true",
                   help="閉じられない回から抜ける。--reason が要る(記録は永久に残る)")
    p.add_argument("--close-abort", metavar="時刻", dest="close_abort",
                   help="中断した回に始末の記録を足す。--reason(20文字以上)が要る。"
                        "ABORT の行は消えない")
    p.add_argument("--reason", default="")
    p.add_argument("--status", action="store_true")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    if a.start:
        return cmd_start(a.start, a.sandbox)
    if a.check:
        return cmd_check()
    if a.selftest:
        return selftest()
    if a.close_abort:
        return cmd_close_abort(a.close_abort, a.reason)
    if a.abort:
        return cmd_abort(a.reason)
    if a.end:
        return cmd_end()
    return cmd_status()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
