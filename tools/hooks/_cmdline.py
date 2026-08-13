# -*- coding: utf-8 -*-
"""コマンド行を token に割る。フックが共通で使う。

同じ処理を2箇所に持つと片方だけ直る(実際 `known_checks()` がそれで正規表現のまま
取り残された)。解析はここ1本にする。
"""
import re
import shlex


def segments(cmd, posix=True):
    r"""`;` `&&` `|` `>` などで区切って、コマンドごとの token 列にする。

    posix=True だと `\` がエスケープとして消えるので、Windows のパスが別物になる。
    呼ぶ側は both() を使って両方見ること。
    """
    # CA-1(第6回→第7回、再現済み・高): shlex は改行を**ただの空白**として扱うので、
    # `echo hi\nrm <本物>/tools/lock.py` が1つの区画に潰れ、先頭の echo が
    # 読み取り専用なので丸ごと許可された。bash は改行をコマンド区切りとして実行する。
    # 復帰(\r)も同じ。先に行で割ってから、行ごとに shlex に掛ける。
    segs = []
    for line in re.split(r"[\r\n]+", cmd or ""):
        if not line.strip():
            continue
        segs.extend(_segments_1line(line, posix))
    return segs


def _segments_1line(cmd, posix):
    # ヒアドキュメントの印は、token 比較ではなく**位置**で切る。
    # token 比較だと引用符を外したあとの `'<<'` と区別が付かない(HC-2)。
    hit = heredoc_at(cmd)
    if hit:
        cmd = cmd[:hit[1]] + " " + cmd[hit[2]:]
    try:
        lx = shlex.shlex(cmd, posix=posix, punctuation_chars=True)
        lx.whitespace_split = True
        # HF-1(第13回、高): shlex の既定は commenters="#" で、**行のどこにあっても**
        # # 以降を捨てる。bash は単語の途中・末尾の # をただの文字として扱うので、
        # `echo x#; rm -rf <本物>` が「echo x」だけに見えていた。
        # 回のフックだけでなく、常時走る P-2 まで同じ理由で無効化された。
        lx.commenters = ""
        toks = [t for t in lx if t]
    except ValueError:          # 引用符が閉じていない等
        toks = [t for t in re.split(r"\s+", (cmd or "").strip()) if t]
    # FD-1(第11回、高): ヒアドキュメントの印は token として外す。
    # 行を「最初の << より前」で切る実装だと、引用符の中の << を1つ書くだけで
    # 行の残り(`; rm -rf <本物>` など)が丸ごと検査対象から消えた。
    # shlex は引用符を解いてから token を返すので、ここで見る `<<` は
    # **本当の演算子**だけ。印とタグの2つを捨て、残りは検査に回す。
    segs, cur = [], []
    for t in toks:
        # `<` は入力なので書き込み先を作らない。区切りに数えると
        # `grep -c '<<' <ファイル>` の後ろが「書き込み先」に見えて誤検知する。
        if t and not t.strip("|&;>()"):        # 区切り記号だけの token
            if cur:
                segs.append(cur)
                cur = []
            continue
        cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def both(cmd):
    """posix / 非posix の両方で割った結果を、重複を除いて返す。"""
    out = []
    for posix in (True, False):
        for seg in segments(cmd or "", posix):
            if seg not in out:
                out.append(seg)
    return out


def base(tok):
    """コマンド名だけを小文字で取り出す(パスと .exe を落とす)。"""
    b = re.split(r"[\\/]", tok or "")[-1].lower()
    return b[:-4] if b.endswith(".exe") else b


def strip_env(toks):
    """先頭の `VAR=値` を外し、(環境変数の list, 残りの token) を返す。"""
    env = []
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        env.append(toks[0])
        toks = toks[1:]
    return env, toks


def heredoc_at(line):
    """行の中の「本物の」ヒアドキュメント演算子を探す。

    返すのは (タグ名, 開始位置, 終了位置) か None。

    HC-1 / HC-2(第13回、高): ここは shlex の token 比較だった。
    posix モードの shlex は引用符を**外してから** token を返すので、
    `'<<'` と裸の `<<` が区別できず、第11回・第12回で潰したはずの
    「引用符の中の <<」がそのまま通っていた。
    `$(( 1 << N ))`(算術左シフト)も演算子と誤読していた。
    引用符と算術展開の内側を追いながら1文字ずつ見る。
    """
    n = len(line)
    k = 0
    sq = dq = False          # 引用符の中か
    arith = 0                # $(( )) の深さ
    while k < n:
        c = line[k]
        if c == "\\" and not sq:
            k += 2
            continue
        if c == "'" and not dq:
            sq = not sq
            k += 1
            continue
        if c == '"' and not sq:
            dq = not dq
            k += 1
            continue
        if not sq and not dq:
            if line.startswith("$((", k):
                arith += 1
                k += 3
                continue
            if arith and line.startswith("))", k):
                arith -= 1
                k += 2
                continue
            if arith == 0 and line.startswith("<<", k):
                if line.startswith("<<<", k):
                    k += 3           # ヒア文字列。本文行を持たない
                    continue
                j = k + 2
                if j < n and line[j] == "-":
                    j += 1
                while j < n and line[j] in " \t":
                    j += 1
                q = ""
                if j < n and line[j] in "'\"":
                    q = line[j]
                    j += 1
                st = j
                while j < n and (line[j].isalnum() or line[j] == "_"):
                    j += 1
                tag = line[st:j]
                if q and j < n and line[j] == q:
                    j += 1
                if not tag:
                    k += 2
                    continue
                return tag, k, j
        k += 1
    return None


def heredoc_tag(line, posix=True):
    """互換用。タグ名だけ返す。"""
    hit = heredoc_at(line)
    return hit[0] if hit else None
