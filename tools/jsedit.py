# -*- coding: utf-8 -*-
"""JS配列リテラルへの安全な要素追記ヘルパー。

このリポジトリのHTML内JS配列には
  - 要素行の末尾に `// コメント` が付く
  - コメント本文に `[A]` のような角括弧が入る
  - `sourceCharacters:[]` と空のことがある
という3つの罠があり、素朴な文字列操作だと壊れる。ここに集約しておく。
"""
import re


def close_bracket(s, start):
    """s[start] が '[' のとき、対応する ']' の位置を返す(文字列/行コメントを無視)。"""
    depth, i, n = 0, start, len(s)
    while i < n:
        c = s[i]
        if c == '/' and i + 1 < n and s[i + 1] == '/':
            i = s.find('\n', i)
            if i < 0:
                return -1
            continue
        if c in '"\'':
            q = c
            i += 1
            while i < n:
                if s[i] == '\\':
                    i += 2
                    continue
                if s[i] == q:
                    break
                i += 1
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _comment_pos(line):
    """行内の // の位置(文字列の外)。無ければ -1。"""
    i, n, q = 0, len(line), ''
    while i < n:
        c = line[i]
        if q:
            if c == '\\':
                i += 2
                continue
            if c == q:
                q = ''
        elif c in '"\'':
            q = c
        elif c == '/' and i + 1 < n and line[i + 1] == '/':
            return i
        i += 1
    return -1


def append_comma(body):
    """配列の中身(文字列)の最後の要素にカンマを足す。
    末尾行に // コメントがある場合はコメントの手前に入れる(コメント内に入れると次要素と繋がって壊れる)。"""
    if body.strip() == '':
        return ''
    lines = body.rstrip().split('\n')
    for idx in range(len(lines) - 1, -1, -1):
        ln = lines[idx]
        cp = _comment_pos(ln)
        code = ln if cp < 0 else ln[:cp]
        if code.strip() == '':
            continue                      # コメントだけの行は飛ばす
        if code.rstrip().endswith(','):
            return '\n'.join(lines)       # すでにカンマ付き
        if cp < 0:
            lines[idx] = code.rstrip() + ','
        else:
            lines[idx] = code.rstrip() + ', ' + ln[cp:]
        return '\n'.join(lines)
    return '\n'.join(lines)


def insert_elements(s, arr_open, arr_close, new_lines, nl):
    """s[arr_open] が '[' の配列に new_lines(要素文字列のリスト)を末尾追加した新しい s を返す。"""
    open_at = arr_open + 1
    block = s[open_at:arr_close]
    ind = '        '
    m = re.search(r'\n(\s*)\{', block)
    if m:
        ind = m.group(1)
    tail = re.search(r'\n([ \t]*)$', block)
    closeind = tail.group(1) if tail else '      '
    body = append_comma(block)
    joined = (',' + nl).join(ind + x for x in new_lines)
    return s[:open_at] + body + nl + joined + nl + closeind + s[arr_close:]
