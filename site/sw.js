/* 英语小伙伴 App 离线兜底 Service Worker
   背景：电台流本身直连第三方电台 CDN（不经本站），VPS 故障时真正不可用的只有
        页面壳(index.html)与数据(stations.json/manifest.json)。本 SW 让这些资源断网仍可用。
   策略：
   - 导航请求 / 核心资源：网络优先（保证更新即时），失败回缓存（保证可用）
   - 核心资源清单：index.html、api/stations.json、api/manifest.json、assets/hls.min.js
   - 电台流 / 跨域 / 其他请求：一律放行不拦截
   - 缓存 key 全部去掉 ?v= 时间戳归一化，避免每次请求产生新副本
   更新机制：发版时递增 VERSION，install 重新预缓存，activate 删旧版本缓存并立即接管。
*/
var VERSION = 'v1';
var CACHE = 'xb-app-' + VERSION;
var SHELL = [
  './',
  './index.html',
  './api/stations.json',
  './assets/hls.min.js'
];
var CORE_PATHS = ['index.html', 'api/stations.json', 'api/manifest.json', 'assets/hls.min.js'];

function normKey(url){
  var i = url.indexOf('?');
  return i < 0 ? url : url.substring(0, i);
}
function isCorePath(pathname){
  return CORE_PATHS.some(function(c){ return pathname === '/' + c || pathname.slice(-(c.length + 1)) === '/' + c; });
}
/* 网络优先：成功回写缓存；失败（断网/超时/HTTP异常）回缓存 */
function netFirst(req){
  return fetch(req).then(function(r){
    if(r && r.ok){
      var cp = r.clone();
      caches.open(CACHE).then(function(c){
        c.put(new Request(normKey(req.url)), cp);
      }).catch(function(){});
    }
    return r;
  }).catch(function(){
    return caches.match(normKey(req.url), {ignoreSearch: true}).then(function(m){
      return m || null;
    });
  });
}

self.addEventListener('install', function(e){
  e.waitUntil(
    caches.open(CACHE).then(function(c){ return c.addAll(SHELL); })
      .then(function(){ return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function(e){
  e.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(keys.filter(function(k){ return k !== CACHE; }).map(function(k){ return caches.delete(k); }));
    }).then(function(){ return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function(e){
  var req = e.request;
  if(req.method !== 'GET') return;                      // 只拦 GET
  var url = new URL(req.url);
  if(url.origin !== self.location.origin) return;       // 跨域（电台流/CDN）放行
  if(req.mode === 'navigate'){                          // 页面导航：网络优先，失败回缓存
    e.respondWith(
      netFirst(req).then(function(r){
        if(r) return r;
        return caches.match(new Request(self.registration.scope + 'index.html')).then(function(m){
          return m || Response.error();
        });
      })
    );
    return;
  }
  if(isCorePath(url.pathname)){                         // 核心数据资源：网络优先，失败回缓存
    e.respondWith(
      netFirst(req).then(function(r){ return r || Response.error(); })
    );
  }
  /* 其余同源请求（articles/dict/img 等）不拦截，保持原行为 */
});
