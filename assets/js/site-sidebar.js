/* ============ サイドバーナビゲーション(現在地ハイライト+モバイル用開閉) ============
   狭い画面ではCSS側でサイドバーを既定非表示(オーバーレイ方式)にしており、
   このスクリプトが開閉ボタンとオーバーレイをDOMに挿入して手動表示を可能にする。
   全ページ共通のためHTML側は一切変更せず、ここだけで完結させる。
============================================ */
document.addEventListener('DOMContentLoaded', function(){
  const sidebar = document.getElementById('siteSidebar');
  if(!sidebar) return;

  const currentPage = location.pathname.split('/').pop() || 'index.html';
  sidebar.querySelectorAll('a').forEach(function(a){
    const hrefPage = a.getAttribute('href').split('#')[0];
    if(hrefPage === currentPage) a.classList.add('current');
  });

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
