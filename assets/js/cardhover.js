// 武将名にカーソルを置いたら、その武将の切り抜き画像を出す。
//
// 2026-08-13 に武将データベースの一覧(characters*.html)へ入れたものを、
// 2026-08-15 にスキルページの「入手可能な武将(合成素材)」と
// 「初期スキルとして持つ武将」でも使えるように共通化した(ユーザー依頼)。
// 文字だけではどの武将か思い出せない、という話への対応。
//
// 拾う書き方は2つ:
//   <tr data-thumb="assets/img/characters/no2855_char.png">  一覧の行(既存)
//   <a data-thumb-no="2855">                                  スキルページの武将名
// 後者はページの深さ(ルート直下か skill/ の中か)で相対パスが変わるため、
// 番号だけを持たせて、URLはこちら側で組み立てる。
//
// 元画像は 224x315。その7割(157px幅)で出す。高さは比率のまま。
(function () {
  'use strict';

  // 触る画面では最初から何もしない。CSSでも display:none にしてあるが、
  // 「1回目のタップがホバー扱いになって、リンクを開くのに2回要る」という
  // 端末があるので、listener自体を付けないことで確実に避ける。
  try {
    if (window.matchMedia && window.matchMedia('(hover: none)').matches) return;
  } catch (e) {}

  // busho/ や skill/ の中のページからは1つ上に戻る必要がある
  var UP = /\/(busho|skill)\/[^/]*$/.test(location.pathname) ? '../' : '';
  var SEL = '[data-thumb], [data-thumb-no]';
  var pop = null;
  var img = null;

  function srcOf(el) {
    var no = el.getAttribute('data-thumb-no');
    if (no) return UP + 'assets/img/characters/no' + no + '_char.png';
    return el.getAttribute('data-thumb');
  }

  function ensure() {
    if (pop) return;
    pop = document.createElement('div');
    pop.className = 'cardhover-pop';
    img = document.createElement('img');
    img.alt = '';
    // 画像が無い武将のときに壊れたアイコンを出さない
    img.addEventListener('error', function () { hide(); });
    pop.appendChild(img);
    document.body.appendChild(pop);
  }

  function hide() {
    if (pop) pop.style.display = 'none';
  }

  function place(ev) {
    if (!pop || pop.style.display === 'none') return;
    var w = pop.offsetWidth || 165;
    var h = pop.offsetHeight || 230;
    var pad = 16;
    var x = ev.clientX + pad;
    var y = ev.clientY + pad;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
    if (x < 8) x = 8;
    if (y + h > window.innerHeight - 8) y = window.innerHeight - h - 8;
    if (y < 8) y = 8;
    pop.style.left = x + 'px';
    pop.style.top = y + 'px';
  }

  function target(node) {
    return node && node.closest ? node.closest(SEL) : null;
  }

  document.addEventListener('mouseover', function (ev) {
    var t = target(ev.target);
    if (!t) return;
    var src = srcOf(t);
    if (!src) return;
    ensure();
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    pop.style.display = 'block';
    place(ev);
  });

  document.addEventListener('mousemove', place);

  document.addEventListener('mouseout', function (ev) {
    if (!pop) return;
    var from = target(ev.target);
    var to = target(ev.relatedTarget);
    if (from && from !== to) hide();
  });

  // 画面が動いたら消す(位置がずれたまま残るのを防ぐ)
  window.addEventListener('scroll', hide, true);
})();
