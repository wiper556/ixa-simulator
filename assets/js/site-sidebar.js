/* ============ サイドバーナビゲーション(現在地ハイライト+モバイル用開閉) ============
   狭い画面ではCSS側でサイドバーを既定非表示(オーバーレイ方式)にしており、
   このスクリプトが開閉ボタンとオーバーレイをDOMに挿入して手動表示を可能にする。
   全ページ共通のためHTML側は一切変更せず、ここだけで完結させる。
============================================ */
/* ---- サイドバーのページ内検索 ----
   いま開いているページの文字を拾って黄色く塗り、件数と前後移動を出す。
   ブラウザのCtrl+Fは奪わない(奪うと戻せなくて困る人が出る)。
   探すのは本文の文字だけ。入力欄の中身とサイドバー自身は対象外。 */
function buildPageSearch(sidebar){
  const scope = document.querySelector('.site-content') || document.body;
  const box = document.createElement('div');
  box.className = 'sidebar-search';
  box.innerHTML =
    '<input type="search" class="sidebar-search-input" placeholder="このページ内を検索"'
    + ' aria-label="このページ内を検索" autocomplete="off">'
    + '<div class="sidebar-search-bar">'
    + '<span class="sidebar-search-count"></span>'
    + '<span class="sidebar-search-nav">'
    + '<button type="button" data-d="-1" aria-label="前へ">▲</button>'
    + '<button type="button" data-d="1" aria-label="次へ">▼</button>'
    + '</span></div>';
  const nav = sidebar.querySelector('.sidebar-nav');
  sidebar.insertBefore(box, nav || null);

  const input = box.querySelector('.sidebar-search-input');
  const count = box.querySelector('.sidebar-search-count');
  let marks = [];
  let at = -1;

  function clearMarks(){
    marks.forEach(function(m){
      const p = m.parentNode;
      if(!p) return;
      p.replaceChild(document.createTextNode(m.textContent), m);
      p.normalize();
    });
    marks = [];
    at = -1;
  }

  // 文字を持つノードだけを集める。台本・様式・入力欄・サイドバーは避ける
  function textNodes(){
    const skip = /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA|INPUT|SELECT|OPTION)$/;
    const w = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode: function(n){
        if(!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        for(let p = n.parentNode; p && p !== scope; p = p.parentNode){
          if(p.nodeType !== 1) continue;
          if(skip.test(p.nodeName)) return NodeFilter.FILTER_REJECT;
          if(p.id === 'siteSidebar') return NodeFilter.FILTER_REJECT;
          const st = p.ownerDocument.defaultView.getComputedStyle(p);
          if(st && (st.display === 'none' || st.visibility === 'hidden'))
            return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const out = [];
    for(let n = w.nextNode(); n; n = w.nextNode()) out.push(n);
    return out;
  }

  function run(){
    clearMarks();
    const q = input.value.trim();
    if(!q){ count.textContent = ''; box.classList.remove('has-q'); return; }
    box.classList.add('has-q');
    const lq = q.toLowerCase();
    textNodes().forEach(function(node){
      const s = node.nodeValue;
      const l = s.toLowerCase();
      let i = l.indexOf(lq);
      if(i < 0) return;
      let cur = node;
      let base = 0;
      while(i >= 0){
        const rest = cur.splitText(i - base);
        rest.splitText(q.length);
        const m = document.createElement('mark');
        m.className = 'sidebar-search-hit';
        m.textContent = rest.nodeValue;
        rest.parentNode.replaceChild(m, rest);
        marks.push(m);
        cur = m.nextSibling;
        if(!cur) break;
        base = i + q.length;
        i = cur.nodeValue.toLowerCase().indexOf(lq);
        if(i >= 0) i += base;
      }
    });
    count.textContent = marks.length ? ('1 / ' + marks.length) : '見つからない';
    if(marks.length) go(0);
  }

  function go(i){
    if(!marks.length) return;
    if(at >= 0 && marks[at]) marks[at].classList.remove('is-current');
    at = (i + marks.length) % marks.length;
    marks[at].classList.add('is-current');
    marks[at].scrollIntoView({block: 'center', behavior: 'smooth'});
    count.textContent = (at + 1) + ' / ' + marks.length;
  }

  let timer = null;
  input.addEventListener('input', function(){
    clearTimeout(timer);
    timer = setTimeout(run, 220);
  });
  input.addEventListener('keydown', function(e){
    if(e.key !== 'Enter') return;
    e.preventDefault();
    if(!marks.length){ run(); return; }
    go(at + (e.shiftKey ? -1 : 1));
  });
  box.querySelectorAll('.sidebar-search-nav button').forEach(function(b){
    b.addEventListener('click', function(){ go(at + Number(b.dataset.d)); });
  });
}

document.addEventListener('DOMContentLoaded', function(){
  const sidebar = document.getElementById('siteSidebar');
  if(!sidebar) return;

  const currentPage = location.pathname.split('/').pop() || 'index.html';
  sidebar.querySelectorAll('a').forEach(function(a){
    const hrefPage = a.getAttribute('href').split('#')[0];
    if(hrefPage === currentPage) a.classList.add('current');
  });

  buildPageSearch(sidebar);

  const header = document.querySelector('.site-header');
  if(!header) return;

  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'sidebar-toggle-btn';
  toggleBtn.setAttribute('aria-label', 'メニューを開閉する');
  toggleBtn.setAttribute('aria-expanded', 'false');
  toggleBtn.textContent = '☰';
  header.insertBefore(toggleBtn, header.firstChild);

  const overlay = document.createElement('div');
  overlay.className = 'sidebar-overlay';
  document.body.appendChild(overlay);

  function openSidebar(){
    sidebar.classList.add('sidebar-open');
    overlay.classList.add('is-visible');
    toggleBtn.setAttribute('aria-expanded', 'true');
  }
  function closeSidebar(){
    sidebar.classList.remove('sidebar-open');
    overlay.classList.remove('is-visible');
    toggleBtn.setAttribute('aria-expanded', 'false');
  }

  toggleBtn.addEventListener('click', function(){
    if(sidebar.classList.contains('sidebar-open')) closeSidebar();
    else openSidebar();
  });
  overlay.addEventListener('click', closeSidebar);
  sidebar.querySelectorAll('a').forEach(function(a){
    a.addEventListener('click', closeSidebar);
  });
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape') closeSidebar();
  });
});
