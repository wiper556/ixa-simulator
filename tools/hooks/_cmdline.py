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
    try:
        lx = shlex.shlex(cmd, posix=posix, punctuation_chars=True)
        lx.whitespace_split = True
        toks = [t for t in lx if t]
    except ValueError:          # 引用符が閉じていない等
        toks = [t for t in re.split(r"\s+", (cmd or "").strip()) if t]
    segs, cur = [], []
    for t in toks:
        if t and not t.strip("|&;<>()"):       # 区切り記号だけの token
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
