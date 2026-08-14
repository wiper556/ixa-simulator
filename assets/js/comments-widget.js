/* ============ コメント欄ウィジェット(Supabase連携) ============
   使い方:
   1. ページに <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script> を先に読み込む
   2. <script src="assets/js/comments-widget.js"></script> を読み込む
   3. コメントを表示したい場所に <div id="任意のID"></div> を置く
   4. renderCommentsWidget('そのID', 'ページを識別するキー') を呼ぶ
      キーの例: "char:1298"(武将No別) "skill:勇冠三軍"(スキル名別) "page:index"(ページ単位)
   SPA形式のページ(characters.html等)では、表示対象が切り替わるたびに再度呼び出せば良い
   (コンテナの中身は毎回作り直される)。
================================================================ */
(function(){
  const CW_SUPABASE_URL = 'https://oykzgzagkyygobbfkmwq.supabase.co';
  const CW_SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im95a3pnemFna3l5Z29iYmZrbXdxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODE2NzMsImV4cCI6MjEwMDQ1NzY3M30.lP8rcREG6iy_A9qA3oDrZh1uf5y3OQeBhGieMFgbUEs';
  let cwClient = null;
  function cwGetClient(){
    if(!cwClient && window.supabase){
      cwClient = window.supabase.createClient(CW_SUPABASE_URL, CW_SUPABASE_ANON_KEY);
    }
    return cwClient;
  }

  function cwEscapeHtml(s){
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function cwFormatDate(iso){
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, '0');
    return d.getFullYear()+'/'+pad(d.getMonth()+1)+'/'+pad(d.getDate())+' '+pad(d.getHours())+':'+pad(d.getMinutes());
  }

  async function cwLoadList(containerId, pageId){
    const client = cwGetClient();
    const listEl = document.getElementById(containerId+'-cwList');
    if(!listEl) return;
    // 【2026-08-14追加】投稿側には同じ判定があったが、読み込み側だけ抜けていた。
    // CDN(cdn.jsdelivr.net)が読めないと client が null になり、この先で例外が飛ぶ。
    if(!client){
      listEl.innerHTML = '<p class="cw-error">コメントの読み込みに失敗しました。</p>';
      return;
    }
    listEl.innerHTML = '<p class="cw-loading">読み込み中...</p>';
    const {data, error} = await client
      .from('comments')
      .select('body, display_name, created_at')
      .eq('page_id', pageId)
      .order('created_at', {ascending: false})
      .limit(50);
    if(error){
      listEl.innerHTML = '<p class="cw-error">コメントの読み込みに失敗しました。</p>';
      return;
    }
    if(!data || data.length === 0){
      listEl.innerHTML = '<p class="cw-empty">まだコメントはありません。</p>';
      return;
    }
    listEl.innerHTML = data.map(function(c){
      const name = c.display_name ? cwEscapeHtml(c.display_name) : '名無しさん';
      return '<div class="cw-item">'
        + '<div class="cw-item-head"><span class="cw-item-name">'+name+'</span><span class="cw-item-date">'+cwFormatDate(c.created_at)+'</span></div>'
        + '<div class="cw-item-body">'+cwEscapeHtml(c.body).replace(/\n/g, '<br>')+'</div>'
        + '</div>';
    }).join('');
  }

  function renderCommentsWidget(containerId, pageId){
    const container = document.getElementById(containerId);
    if(!container) return;
    container.innerHTML =
      '<div class="cw-wrap">'
        + '<h2 class="cw-title">コメント</h2>'
        + '<div class="cw-form">'
          + '<input type="text" class="cw-name-input" id="'+containerId+'-cwName" placeholder="名前(任意)" maxlength="30">'
          + '<textarea class="cw-body-input" id="'+containerId+'-cwBody" placeholder="コメントを入力(1000文字まで)" maxlength="1000" rows="3"></textarea>'
          + '<div class="cw-form-row"><button type="button" class="cw-submit-btn" id="'+containerId+'-cwSubmit">投稿する</button><span class="cw-status" id="'+containerId+'-cwStatus"></span></div>'
        + '</div>'
        + '<div class="cw-list" id="'+containerId+'-cwList"></div>'
      + '</div>';

    cwLoadList(containerId, pageId);

    document.getElementById(containerId+'-cwSubmit').addEventListener('click', async function(){
      const nameEl = document.getElementById(containerId+'-cwName');
      const bodyEl = document.getElementById(containerId+'-cwBody');
      const statusEl = document.getElementById(containerId+'-cwStatus');
      const body = bodyEl.value.trim();
      if(!body){
        statusEl.textContent = 'コメントを入力してください。';
        return;
      }
      const client = cwGetClient();
      if(!client){
        statusEl.textContent = '通信エラーが発生しました。';
        return;
      }
      statusEl.textContent = '投稿中...';
      const {error} = await client.from('comments').insert({
        page_id: pageId,
        body: body,
        display_name: nameEl.value.trim() || null
      });
      if(error){
        statusEl.textContent = '投稿に失敗しました。';
        return;
      }
      bodyEl.value = '';
      statusEl.textContent = '';
      cwLoadList(containerId, pageId);
    });
  }

  window.renderCommentsWidget = renderCommentsWidget;
})();
