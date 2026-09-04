/* home.js — the landing page's motion: pointer/scroll parallax, sparkles, card reveal.
 * Every effect is skipped under `prefers-reduced-motion: reduce`.
 *
 * Lived inline in `index.html` until 2026-09-04; moved out for `script-src 'self'` (see
 * `sim/web/_headers`).
 */
(function(){
  "use strict";
  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion:reduce)").matches;
  // parallax: move bg layers by mouse + scroll, each by its data-depth
  var layers=[].slice.call(document.querySelectorAll("#bg [data-depth]"));
  var stage=document.getElementById("stage");
  var mx=0,my=0,tx=0,ty=0,sy=0;
  if(!reduce){
    window.addEventListener("mousemove",function(e){
      mx=(e.clientX/window.innerWidth-0.5); my=(e.clientY/window.innerHeight-0.5);
    },{passive:true});
    window.addEventListener("scroll",function(){ sy=window.scrollY||0; },{passive:true});
    (function loop(){
      tx+=(mx-tx)*0.06; ty+=(my-ty)*0.06;
      layers.forEach(function(el){ var d=parseFloat(el.dataset.depth)||0;
        el.style.transform="translate3d("+(-tx*60*d)+"px,"+(-ty*60*d - sy*d*0.35)+"px,0)"; });
      if(stage) stage.style.transform="translate3d("+(-tx*22)+"px,"+(-ty*22 - sy*0.04)+"px,0)";
      requestAnimationFrame(loop);
    })();
    // sparkles
    var bg=document.getElementById("bg");
    for(var i=0;i<28;i++){ var s=document.createElement("div"); s.className="spark";
      s.style.left=(Math.random()*100)+"%"; s.style.top=(Math.random()*100)+"%";
      s.style.animationDelay=(Math.random()*6)+"s"; bg.appendChild(s); }
  }
  // staggered card reveal
  var cards=[].slice.call(document.querySelectorAll("#cards .card"));
  if("IntersectionObserver" in window && !reduce){
    var io=new IntersectionObserver(function(es){ es.forEach(function(en){ if(en.isIntersecting){
      var i=cards.indexOf(en.target); setTimeout(function(){ en.target.classList.add("in"); }, i*70); io.unobserve(en.target); } }); },{threshold:.15});
    cards.forEach(function(c){ io.observe(c); });
  } else { cards.forEach(function(c){ c.classList.add("in"); }); }
})();
