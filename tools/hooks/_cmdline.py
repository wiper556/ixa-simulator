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
    try:
        lx = shlex.shlex(cmd, posix=posix, punctuation_chars=True)
        lx.whitespace_split = True
        toks = [t for t in lx if t]
    except ValueError:          # 引用符が閉じていない等
        toks = [t for t in re.split(r"\s+", (cmd or "").strip()) if t]
    segs, cur = [], []
    for t in toks:
        if t and not t.strip("|&;<>()\n"):     # 区切り記号だけの token
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
