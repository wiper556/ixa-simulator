/* ============ サイドバーナビゲーション(常時表示・現在地ハイライトのみ) ============ */
document.addEventListener('DOMContentLoaded', function(){
  const sidebar = document.getElementById('siteSidebar');
  if(!sidebar) return;

  const currentPage = location.pathname.split('/').pop() || 'index.html';
  sidebar.querySelectorAll('a').forEach(function(a){
    const hrefPage = a.getAttribute('href').split('#')[0];
    if(hrefPage === currentPage) a.classList.add('current');
  });
});
