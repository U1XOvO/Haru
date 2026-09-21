'use strict';
// Shared capability/transport adapter; the browser preview still uses its own HTTP RPC.
function haruHasNative(){
 return !!window.webkit?.messageHandlers?.haru || navigator.userAgent.includes('HaruDesktop/Windows');
}
function haruWindowsReady(){
 if(window.pywebview?.api?.request)return Promise.resolve();
 return new Promise((resolve,reject)=>{
  const ready=()=>{clearTimeout(timer);window.removeEventListener('pywebviewready',ready);resolve();};
  const timer=setTimeout(()=>{window.removeEventListener('pywebviewready',ready);reject(new Error('桌面连接尚未就绪，请重新运行 start.cmd。'));},15000);
  window.addEventListener('pywebviewready',ready,{once:true});
 });
}
function haruPostMessage(message){
 if(window.webkit?.messageHandlers?.haru){window.webkit.messageHandlers.haru.postMessage(message);return;}
 haruWindowsReady().then(()=>window.pywebview.api.request(message))
  .then(result=>window.haruResolve(message.id,result))
  .catch(error=>window.haruResolve(message.id,{ok:false,error:error.message||'桌面连接失败。'}));
}
