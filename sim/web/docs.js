/* docs.js — the docs explorer: tree, full-text search, Markdown + Mermaid rendering,
 * deep links, keyboard shortcuts.
 *
 * Lived inline in `docs.html` until 2026-09-04 and was the single largest inline block on
 * the site (405 lines, 25 KB). Moved out for `script-src 'self'` (see `sim/web/_headers`):
 * hashing a block this size means every one-character edit to the explorer must be paired
 * with a regenerated header or the page goes blank. A file needs no hash at all.
 */
(function(){
  "use strict";
  var BUNDLE="docs-bundle/", idx=null, current=null, mmReady=false, order=[];
  var SEARCH=null, searchLoading=false;
  function loadSearch(cb){
    if(SEARCH){ cb&&cb(); return; }
    if(searchLoading) return;
    searchLoading=true;
    fetch("docs-search.json").then(function(r){return r.json();}).then(function(j){
      SEARCH=j; searchLoading=false; cb&&cb();
    }).catch(function(){ searchLoading=false; });
  }
  var HOME="_root/README.md";
  var elTree=document.getElementById("tree"), elContent=document.getElementById("content"),
      elCrumb=document.getElementById("crumb"), elSrc=document.getElementById("src"), elQ=document.getElementById("q");
  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion:reduce)").matches;

  try{ mermaid.initialize({startOnLoad:false, theme:"dark", securityLevel:"loose",
    themeVariables:{fontFamily:"Inter, sans-serif", darkMode:true, background:"#0b0e15",
      primaryColor:"#0d2029", primaryBorderColor:"#0e7490", primaryTextColor:"#eef2f8",
      lineColor:"#5a6577", secondaryColor:"#161226", tertiaryColor:"#101018",
      clusterBkg:"#0b0e15", clusterBorder:"#242a36", edgeLabelBackground:"#0b0e15"}}); mmReady=true;
  }catch(e){}

  var esc=function(s){return String(s).replace(/[&<>]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});};
  var SUBSECTION_LABEL={ "":"Overview", "phone":"📱 Phone side", "protocol":"🔌 Protocol", "runtime":"🧠 Runtime (brain & face)", "firmware":"🧱 Firmware", "hardware":"🦾 Hardware", "keys":"Keys", "manifests":"Manifests", "recovered-proto":"Recovered protos" };
  var SUB_ORDER=["","phone","protocol","runtime","firmware","hardware","recovered-proto","manifests","keys"];
  var SECTION_LABEL={ "_root":"Start here","reverse-engineering":"Reverse engineering","architecture":"Architecture",
    "guides":"Guides","features":"Features","design":"Design","debugging":"Debugging","docs":"Reference" };
  var SECTION_ORDER={"_root":0,"guides":1,"architecture":2,"reverse-engineering":3,"debugging":4,"features":5,"design":6,"docs":7};

  function grouped(filter){
    var by={}; idx.files.forEach(function(f){ if(filter&&!matches(f,filter)) return; (by[f.section]=by[f.section]||[]).push(f); });
    // When searching, order each section's hits by relevance (title/heading match +
    // full-text hit count) so the doc that actually documents the term floats to the
    // top of its section instead of whatever sorts first (e.g. the section README).
    // With no filter, keep the curated README reading order untouched.
    if(filter){ var q=filter.toLowerCase();
      Object.keys(by).forEach(function(s){ by[s].sort(function(a,b){ return score(b,q)-score(a,q); }); }); }
    return Object.keys(by).sort(function(a,b){ return (SECTION_ORDER[a]==null?9:SECTION_ORDER[a])-(SECTION_ORDER[b]==null?9:SECTION_ORDER[b])||a.localeCompare(b); })
      .map(function(s){ return {sec:s, items:by[s]}; });
  }
  function score(f,q){  // higher = more relevant to query q (already lower-cased)
    var s=0;
    if(f.title.toLowerCase().indexOf(q)>=0) s+=1000;                                   // title hit
    if((f.headings||[]).some(function(h){return h.toLowerCase().indexOf(q)>=0;})) s+=100; // heading hit
    if(f.path.toLowerCase().indexOf(q)>=0) s+=50;                                       // filename hit
    var body=SEARCH&&SEARCH[f.path]; if(body){ var lc=body.toLowerCase(),i=0,n=0;       // full-text hit count
      while((i=lc.indexOf(q,i))>=0){ n++; i+=q.length; } s+=n; }
    return s;
  }
  function matches(f,q){ q=q.toLowerCase();
    if(f.title.toLowerCase().indexOf(q)>=0||f.path.toLowerCase().indexOf(q)>=0||(f.headings||[]).some(function(h){return h.toLowerCase().indexOf(q)>=0;})) return true;
    return !!(SEARCH && SEARCH[f.path] && SEARCH[f.path].toLowerCase().indexOf(q)>=0); }
  function snippetFor(f,q){
    if(!SEARCH||!q) return "";
    var body=SEARCH[f.path]; if(!body) return "";
    var lc=body.toLowerCase(), i=lc.indexOf(q.toLowerCase()); if(i<0) return "";
    var start=Math.max(0,i-42), end=Math.min(body.length,i+q.length+64);
    var seg=(start>0?"…":"")+body.slice(start,end)+(end<body.length?"…":"");
    return highlight(seg,q); }
  function highlight(t,q){ if(!q) return esc(t); var i=t.toLowerCase().indexOf(q.toLowerCase());
    return i<0?esc(t):esc(t.slice(0,i))+"<em>"+esc(t.slice(i,i+q.length))+"</em>"+esc(t.slice(i+q.length)); }

  var CARET='<svg class="caret" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>';
  var ARROW='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';

  function buildTree(filter){
    elTree.innerHTML=""; order=[];
    var groups=grouped(filter), any=false;
    var activeSec=current?current.split("/")[0]:"_root";
    groups.forEach(function(g){
      if(!g.items.length) return; any=true;
      var wrap=document.createElement("div"); wrap.className="grp";
      var openIt = !!filter || g.sec===activeSec || g.sec==="_root";
      if(openIt) wrap.classList.add("open");
      var gh=document.createElement("button"); gh.className="gh";
      gh.innerHTML=CARET+'<span>'+(SECTION_LABEL[g.sec]||g.sec)+'</span><span class="cnt">'+g.items.length+'</span>';
      gh.onclick=function(){ wrap.classList.toggle("open"); };
      wrap.appendChild(gh);
      var items=document.createElement("div"); items.className="items";
      function renderItem(f){ order.push(f.path);
        var a=document.createElement("a"); a.className="doc"; a.href="#"+f.path; a.dataset.path=f.path;
        if(filter) a.classList.add("hit");
        a.innerHTML='<span>'+highlight(f.title,filter)+'</span>'+(f.mermaid?'<span class="dm">◈'+f.mermaid+'</span>':'');
        a.onclick=function(ev){ ev.preventDefault(); openDoc(f.path); if(window.innerWidth<=760) document.body.classList.remove("nav-open"); };
        items.appendChild(a);
        if(filter){ var sn=snippetFor(f,filter); if(sn){ var sd=document.createElement("div"); sd.className="snip"; sd.innerHTML=sn; sd.onclick=function(){ openDoc(f.path); if(window.innerWidth<=760) document.body.classList.remove("nav-open"); }; items.appendChild(sd); } }
      }
      // bucket a section's docs by their subfolder (path segment [1]); "" = docs at the section root.
      var buckets={}, hasSub=false;
      g.items.forEach(function(f){ var seg=f.path.split("/"); var sub=(seg.length>=3)?seg[1]:"";
        // route known nested containers (manifests/, recovered-proto/, keys/) to their own sub-group
        // instead of lumping them into the parent subfolder (e.g. firmware/manifests/*.tsv → "Manifests")
        if(seg.length>=4 && /^(manifests|recovered-proto|keys)$/.test(seg[2])) sub=seg[2];
        if(sub) hasSub=true; (buckets[sub]=buckets[sub]||[]).push(f); });
      if(filter||!hasSub){
        // searching (or a flat section): render in relevance/curated order, no sub-headers
        g.items.forEach(renderItem);
      } else {
        // browsing a section with subfolders: group under labeled sub-headers
        var subkeys=Object.keys(buckets).sort(function(a,b){ var ia=SUB_ORDER.indexOf(a),ib=SUB_ORDER.indexOf(b); return (ia<0?99:ia)-(ib<0?99:ib)||a.localeCompare(b); });
        subkeys.forEach(function(sub){
          var sh=document.createElement("div"); sh.className="subhead"; sh.textContent=SUBSECTION_LABEL[sub]||sub||"Overview"; items.appendChild(sh);
          buckets[sub].forEach(renderItem);
        });
      }
      wrap.appendChild(items); elTree.appendChild(wrap);
    });
    if(!any) elTree.innerHTML='<div class="grp"><div class="gh"><span>no matches</span></div></div>';
    setActive(current);
  }
  function setActive(path){ Array.prototype.forEach.call(elTree.querySelectorAll("a.doc"),function(a){ a.classList.toggle("active",a.dataset.path===path); }); }

  // resolve a "#heading-anchor" to its element in the rendered doc — exact id first
  // (marked headerIds), then a fallback matching a heading's slugified text.
  function slugify(t){ return String(t).toLowerCase().trim().replace(/[^\w\- ]+/g,"").replace(/\s+/g,"-").replace(/\-+/g,"-"); }
  function anchorEl(root, anchor){
    var id=String(anchor||"").replace(/^#/,""); if(!id) return null;
    var el=null; try{ el=root.querySelector("#"+CSS.escape(id)); }catch(e){}
    if(el) return el;
    var hs=root.querySelectorAll("h1,h2,h3,h4,h5");
    for(var i=0;i<hs.length;i++){ if(slugify(hs[i].textContent)===id) return hs[i]; }
    return null;
  }
  /* Collapse "a/b/../c" style segments onto a base directory. Shared by the link and the
     image rewrites below, which resolve in two different path spaces. */
  function joinPath(baseDir, rel){
    var parts=(baseDir?baseDir.split("/"):[]);
    rel.split("/").forEach(function(seg){ if(seg===".."){parts.pop();} else if(seg==="."||seg===""){} else {parts.push(seg);} });
    return parts.join("/");
  }
  /* The REPO path of a doc, from its path in the bundle — the inverse of what
     `sim/tools/build_docs_bundle.py` does when it copies: the docs/ tree is flattened onto
     the bundle root, and the two top-level docs are namespaced under `_root/`. */
  function repoPathOf(bundlePath){
    return bundlePath.indexOf("_root/")===0 ? bundlePath.slice(6) : "docs/"+bundlePath;
  }
  /* A doc's <img src> is written REPO-relative, because GitHub renders the same Markdown
     straight out of the repo. This page serves it from `docs-bundle/`, a different depth,
     so the raw string resolves against `/docs.html` and 404s (or, for the off-site URL this
     replaced, is refused by `img-src 'self' data: blob:` — see
     docs/architecture/backlog/vendor-the-readme-hero.md). Resolve it against the doc's own
     repo path instead, then map that onto the URL serving the same bytes: `sim/web/` IS the
     site root (wrangler.toml `pages_build_output_dir`), so the prefix simply comes off.
     That is the whole rule, and it is why doc images live under `sim/web/img/` — anything
     else has no served URL to map to, which `sim/tests/test_no_offsite_images.py` enforces
     at commit time rather than leaving to a browser months later. */
  function fixImages(root, fromPath){
    var repoDir=repoPathOf(fromPath).replace(/\/?[^/]*$/,"");
    Array.prototype.forEach.call(root.querySelectorAll("img[src]"),function(img){
      var src=img.getAttribute("src");
      if(!src||/^([a-z][a-z0-9+.-]*:|\/\/|\/)/i.test(src)) return;   // absolute / data: / blob: — leave alone
      var abs=joinPath(repoDir, src);
      if(abs.indexOf("sim/web/")===0) img.setAttribute("src", abs.slice(8));
    });
  }
  function fixLinks(root, fromPath){
    var baseDir=fromPath.indexOf("/")>=0?fromPath.replace(/\/[^/]*$/,""):"";
    fixImages(root, fromPath);
    Array.prototype.forEach.call(root.querySelectorAll("a[href]"),function(a){
      var href=a.getAttribute("href");
      if(/^(https?:|mailto:|#)/.test(href)){ if(/^https?:/.test(href)){a.target="_blank";a.rel="noopener";} return; }
      var hash=""; var m=href.match(/#.*$/); if(m){ hash=m[0]; href=href.slice(0,m.index); }
      if(!href||!/\.(md|tsv|dts)$/i.test(href)) return;   // .md docs + bundled .tsv/.dts manifests
      var target=joinPath(baseDir, href);
      var hit=idx.files.some(function(f){return f.path===target;})?target:(idx.files.some(function(f){return f.path==="_root/"+target;})?"_root/"+target:null);
      if(hit){ var frag=hash; a.setAttribute("href","#"+hit); if(frag) a.dataset.anchor=frag; a.onclick=function(ev){ ev.preventDefault(); openDoc(hit, frag); }; }
    });
  }
  var HLJS_ALIAS={proto:"protobuf",sh:"bash",shell:"bash",jsonc:"json",yml:"yaml",js:"javascript",py:"python"};
  var _hljsLangs=null;
  // Highlight every occurrence of the search query in the rendered prose (skipping
  // code/mermaid so it never disrupts hljs or diagrams) and return the first mark.
  function markMatches(root, q){
    if(!q) return null;
    q=q.toLowerCase();
    var first=null, nodes=[];
    var walker=document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode:function(n){
        if(!n.nodeValue || n.nodeValue.toLowerCase().indexOf(q)<0) return NodeFilter.FILTER_REJECT;
        for(var p=n.parentNode; p && p!==root; p=p.parentNode){
          var t=p.nodeName;
          // allow code (runs after hljs, so highlighting a token is safe) but never
          // touch rendered mermaid/SVG, scripts, styles, or already-marked text.
          if(t==="SCRIPT"||t==="STYLE"||t==="MARK"||t==="svg"||
             (p.classList&&p.classList.contains("mermaid"))) return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    while(walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function(n){
      var text=n.nodeValue, low=text.toLowerCase(), i=0, pos, frag=document.createDocumentFragment();
      while((pos=low.indexOf(q,i))>=0){
        if(pos>i) frag.appendChild(document.createTextNode(text.slice(i,pos)));
        var mk=document.createElement("mark"); mk.className="qmatch";
        mk.textContent=text.slice(pos,pos+q.length);
        if(!first){ first=mk; mk.classList.add("qmatch-first"); }
        frag.appendChild(mk);
        i=pos+q.length;
      }
      if(i<text.length) frag.appendChild(document.createTextNode(text.slice(i)));
      n.parentNode.replaceChild(frag, n);
    });
    return first;
  }

  function highlightCode(root){
    if(typeof hljs==="undefined") return;
    if(!_hljsLangs){ try{ _hljsLangs={}; hljs.listLanguages().forEach(function(l){_hljsLangs[l]=1;}); }catch(e){ _hljsLangs={}; } }
    Array.prototype.forEach.call(root.querySelectorAll("pre code"),function(code){
      var cls=(code.className||"").match(/language-([A-Za-z0-9_+-]+)/);
      var lang=cls?cls[1].toLowerCase():null;
      if(lang==="mermaid") return;                       // handled separately
      if(lang){ lang=HLJS_ALIAS[lang]||lang;
        if(_hljsLangs[lang]){ code.className="language-"+lang;
          try{ hljs.highlightElement(code); }catch(e){} }
        // unknown language: leave as plain text (no console error)
      }
    });
  }

  // Returns a Promise that resolves once every diagram has rendered (so callers can
  // scroll to an anchor AFTER the layout settles).
  function renderMermaid(root){
    if(!mmReady) return Promise.resolve();
    var blocks=root.querySelectorAll("pre code.language-mermaid, code.language-mermaid");
    if(!blocks.length) return Promise.resolve();
    function doRender(){
      var ps=[];
      Array.prototype.forEach.call(blocks,function(code,i){
        if(!code.isConnected) return;
        var pre=code.closest("pre")||code, div=document.createElement("div"); div.className="mermaid";
        var id="mm-"+(current||"x").replace(/\W/g,"")+"-"+i;
        pre.replaceWith(div);
        ps.push(mermaid.render(id, code.textContent).then(function(r){ div.innerHTML=r.svg; })
          .catch(function(e){ div.innerHTML='<div class="err">mermaid: '+esc(e.message||e)+'</div>'; }));
      });
      return Promise.all(ps);
    }
    // Render only once the webfont (Inter) has loaded, so Mermaid measures node
    // boxes with the SAME metrics it renders with — otherwise it sizes boxes with
    // the fallback font and the real font reflows taller, clipping label text.
    return (document.fonts&&document.fonts.ready) ? document.fonts.ready.then(doRender) : doRender();
  }
  function heroHtml(){
    return '<div class="hero"><div class="row"><div class="txt">'+
      '<h1>Moxie Robot Saver</h1>'+
      '<p>An open, self-hostable revival of the Moxie robot. Everything below is the reverse-engineering that makes it possible — firmware, protocol, hardware, and the path to bring a robot back to life.</p>'+
      '<div class="fwline">firmware under analysis · '+esc(idx.firmware)+'</div>'+
      '</div><img src="img/hero-moxie-think.png" alt="The 3D Moxie simulator"></div></div>';
  }
  function pager(path){
    var i=order.indexOf(path); if(i<0) return "";
    var prev=i>0?order[i-1]:null, next=i<order.length-1?order[i+1]:null;
    function label(p){ var f=idx.files.filter(function(x){return x.path===p;})[0]; return f?esc(f.title):p; }
    var h='<div class="pager">';
    h+= prev?('<a href="#'+prev+'" data-go="'+prev+'"><small>Previous</small>'+label(prev)+'</a>'):'<span></span>';
    h+= next?('<a class="next" href="#'+next+'" data-go="'+next+'"><small>Next</small>'+label(next)+'</a>'):'<span></span>';
    return h+'</div>';
  }
  /* Read from `<template id="docfoot">` in docs.html rather than built here.
   *
   * The other four pages keep this footer in MARKUP; only the explorer built it in code,
   * and that was invisible while the code lived in an inline <script>. Moving the block to
   * `docs.js` on 2026-09-04 put it in front of `sim/tests/test_no_deployment_defaults.py`,
   * which scans shipped JS for hostnames and correctly flagged the two links. The honest
   * fix is not an exemption — it is to keep branding where the rest of the site keeps it. */
  function footHtml(){
    var t=document.getElementById("docfoot");
    return t?t.innerHTML:"";
  }

  var _tocObs=null;
  function slug(t){ return t.toLowerCase().replace(/[^\w\s-]/g,"").trim().replace(/\s+/g,"-").slice(0,60)||"s"; }
  function buildTOC(art){
    var hs=art.querySelectorAll("h2, h3"); if(hs.length<2) return null;
    var nav=document.createElement("nav"); nav.className="toc";
    var seen={}, html='<div class="toc-h">On this page</div>';
    Array.prototype.forEach.call(hs,function(h){
      var id=slug(h.textContent); while(seen[id]){ id+="-x"; } seen[id]=1; h.id=id;
      html+='<a href="#'+id+'" class="'+h.tagName.toLowerCase()+'" data-id="'+id+'">'+esc(h.textContent)+'</a>';
    });
    nav.innerHTML=html;
    Array.prototype.forEach.call(nav.querySelectorAll("a"),function(a){
      a.onclick=function(ev){ ev.preventDefault(); var t=art.querySelector("#"+CSS.escape(a.dataset.id));
        if(t){ var main=document.getElementById("main"); main.scrollTo({top:t.offsetTop-60, behavior:reduce?"auto":"smooth"}); } };
    });
    return nav;
  }
  // add a Copy button to each code block (skips Mermaid sources, which become diagrams)
  function addCopyButtons(root){
    Array.prototype.forEach.call(root.querySelectorAll("pre > code"),function(code){
      if(/language-mermaid/.test(code.className||"")) return;
      var pre=code.parentNode; if(!pre) return;
      if(pre.parentNode&&pre.parentNode.classList&&pre.parentNode.classList.contains("codewrap")) return;
      var wrap=document.createElement("div"); wrap.className="codewrap";
      pre.parentNode.insertBefore(wrap, pre); wrap.appendChild(pre);
      var btn=document.createElement("button"); btn.className="copy-btn"; btn.type="button";
      btn.textContent="Copy"; btn.setAttribute("aria-label","Copy code to clipboard");
      btn.onclick=function(){
        var t=code.textContent;
        function legacy(){ try{ var ta=document.createElement("textarea"); ta.value=t; ta.style.position="fixed"; ta.style.opacity="0";
          document.body.appendChild(ta); ta.select(); document.execCommand("copy"); document.body.removeChild(ta); }catch(e){} }
        // async Clipboard API first; on unavailable/denied, fall back to execCommand
        // (and always catch the rejection so it never logs a console error)
        if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(t).catch(legacy); }
        else legacy();
        btn.textContent="Copied"; btn.classList.add("copied");
        setTimeout(function(){ btn.textContent="Copy"; btn.classList.remove("copied"); },1200);
      };
      wrap.appendChild(btn);
    });
  }
  // add a copyable "#" permalink to each section heading (ids set by buildTOC)
  function addHeadingLinks(art){
    Array.prototype.forEach.call(art.querySelectorAll("h2[id], h3[id]"),function(h){
      if(h.querySelector(".hlink")) return;
      var a=document.createElement("a"); a.className="hlink"; a.href="#"+current+"#"+h.id;
      a.textContent="#"; a.title="Copy link to this section"; a.setAttribute("aria-label","Copy link to this section");
      a.onclick=function(ev){ ev.preventDefault();
        try{ location.hash=current+"#"+h.id; }catch(e){}
        try{ if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(location.href); }catch(e){}
        a.classList.add("copied"); setTimeout(function(){ a.classList.remove("copied"); },1100);
      };
      h.appendChild(a);
    });
  }
  function spyTOC(art, toc){
    if(_tocObs){ _tocObs.disconnect(); _tocObs=null; }
    var links={}; Array.prototype.forEach.call(toc.querySelectorAll("a"),function(a){ links[a.dataset.id]=a; });
    var hs=art.querySelectorAll("h2, h3"); if(!("IntersectionObserver" in window)) return;
    var visible={};
    _tocObs=new IntersectionObserver(function(ents){
      ents.forEach(function(e){ if(e.isIntersecting) visible[e.target.id]=e.boundingClientRect.top; else delete visible[e.target.id]; });
      var top=null, best=1e9;
      Object.keys(visible).forEach(function(id){ if(visible[id]<best){ best=visible[id]; top=id; } });
      if(!top){ // none intersecting: pick the last heading above the fold
        var above=null; Array.prototype.forEach.call(hs,function(h){ if(h.getBoundingClientRect().top<120) above=h.id; }); top=above;
      }
      Object.keys(links).forEach(function(id){ links[id].classList.toggle("active", id===top); });
    },{root:document.getElementById("main"), rootMargin:"-56px 0px -70% 0px", threshold:0});
    Array.prototype.forEach.call(hs,function(h){ _tocObs.observe(h); });
  }

  function openDoc(path, anchor){
    var f=idx.files.filter(function(x){return x.path===path;})[0]; if(!f) return;
    current=path; try{ location.hash=path+(anchor||""); }catch(e){}
    elCrumb.innerHTML=(SECTION_LABEL[f.section]||f.section)+' / <b>'+esc(f.title)+'</b>';
    // compact reading-time + diagram-count from the index metadata (~1100 bytes/min ≈ 200 wpm)
    var meta=document.getElementById("docmeta");
    if(meta){ var mins=Math.max(1,Math.round((f.bytes||0)/1100));
      meta.textContent='~'+mins+' min'+(f.mermaid?'  ·  ◈ '+f.mermaid+' diagram'+(f.mermaid>1?'s':''):''); }
    var srcRel=f.path.indexOf("_root/")===0?("../../"+f.path.replace("_root/","")):("../../docs/"+f.path);
    elSrc.href=srcRel;
    // ensure the doc's section group is open + active
    var sec=path.split("/")[0];
    var grp=elTree.querySelector('a.doc[data-path="'+CSS.escape(path)+'"]');
    if(grp){ var g=grp.closest(".grp"); if(g) g.classList.add("open"); }
    setActive(path);
    fetch(BUNDLE+encodeURI(f.path)).then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.text(); })
      .then(function(md){
        var isHome=(path===HOME);
        var isText=(f.kind==="text")||!/\.md$/i.test(f.path);   // .tsv/.dts manifests → code, not markdown
        var art=document.createElement("article");
        if(isText){
          var ext=(f.path.split(".").pop()||"").toLowerCase();
          var lang=ext==="dts"?"cpp":"plaintext";              // DTS is C-like; TSV is plain
          art.innerHTML='<h1>'+esc(f.title)+'</h1><p class="filemeta">Source manifest — rendered verbatim.</p>'+
                        '<pre><code class="language-'+lang+'">'+esc(md)+'</code></pre>';
        } else {
          art.innerHTML=marked.parse(md);
          fixLinks(art, f.path);
        }
        // pager + footer
        var pg=document.createElement("div"); pg.innerHTML=pager(path)+footHtml();
        Array.prototype.forEach.call(pg.querySelectorAll("a[data-go]"),function(a){ a.onclick=function(ev){ ev.preventDefault(); openDoc(a.dataset.go); }; });
        elContent.innerHTML="";
        if(isHome) elContent.insertAdjacentHTML("beforeend", heroHtml());
        var toc=isText?null:buildTOC(art); if(!isText) addHeadingLinks(art);
        var wrap=document.createElement("div"); wrap.className="docwrap";
        wrap.appendChild(art); if(toc) wrap.appendChild(toc);
        elContent.appendChild(wrap);
        while(pg.firstChild) art.appendChild(pg.firstChild);
        highlightCode(art); addCopyButtons(art);
        if(toc) spyTOC(art, toc);
        // scroll target priority: search hit > cross-doc heading anchor > top
        var q=elQ.value.trim();
        var firstHit=q?markMatches(art,q):null;
        var anchEl=(!firstHit&&anchor)?anchorEl(art,anchor):null;
        document.getElementById("main").scrollTop=0;
        // scroll AFTER Mermaid finishes (it renders async and shifts layout)
        renderMermaid(art).then(function(){
          if(firstHit&&firstHit.isConnected) firstHit.scrollIntoView({block:"center",behavior:"auto"});
          else if(anchEl&&anchEl.isConnected) anchEl.scrollIntoView({block:"start",behavior:"auto"});
        });
      })
      .catch(function(e){ elContent.innerHTML='<article><p class="err" style="padding:30px">Could not load '+esc(f.path)+' — '+esc(e.message)+'</p></article>'; });
  }

  marked.setOptions({gfm:true, breaks:false, headerIds:true, mangle:false});
  fetch("docs-index.json").then(function(r){return r.json();}).then(function(d){
    idx=d; document.getElementById("fw").textContent=d.firmware;
    var ih=parseHash();
    current = (ih.path && idx.files.some(function(f){return f.path===ih.path;})) ? ih.path : HOME;
    if(!idx.files.some(function(f){return f.path===HOME;})) current=idx.files[0].path;
    buildTree(""); openDoc(current, ih.path===current?ih.anchor:"");
  }).catch(function(e){
    elTree.innerHTML='<div style="padding:14px" class="err">Could not load docs-index.json.<br>Run <code>python3 sim/tools/build_docs_bundle.py</code>.</div>';
  });

  var qt; elQ.addEventListener("input",function(){ clearTimeout(qt); qt=setTimeout(function(){ var v=elQ.value.trim(); if(v&&!SEARCH){ loadSearch(function(){ buildTree(elQ.value.trim()); }); } buildTree(v); },110); });
  document.getElementById("burger").onclick=function(){ document.body.classList.toggle("nav-open"); };
  // the URL fragment is "<docpath>" optionally followed by "#<heading-anchor>", so a
  // link to a section is shareable/bookmarkable (doc paths never contain '#').
  function parseHash(){
    var raw=(location.hash||"").replace(/^#/,""); var i=raw.indexOf("#");
    return i>=0 ? {path:raw.slice(0,i), anchor:"#"+raw.slice(i+1)} : {path:raw, anchor:""};
  }
  window.addEventListener("hashchange",function(){
    var hh=parseHash(); if(!hh.path||!idx) return;
    if(hh.path!==current){ if(idx.files.some(function(f){return f.path===hh.path;})) openDoc(hh.path, hh.anchor); }
    else if(hh.anchor){ var el=anchorEl(elContent, hh.anchor); if(el) el.scrollIntoView({block:"start",behavior:"smooth"}); }
  });

  // keyboard shortcuts: "/" focus search · Esc clear/blur · [ / ] prev / next doc
  document.addEventListener("keydown",function(e){
    if(e.ctrlKey||e.metaKey||e.altKey) return;
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target&&e.target.tagName)||"");
    if(e.key==="/"){ if(!typing){ e.preventDefault(); elQ.focus(); elQ.select(); } return; }
    if(e.key==="Escape"){ if(e.target===elQ){ if(elQ.value){ elQ.value=""; buildTree(""); } elQ.blur(); } return; }
    if(typing) return;
    if(e.key==="["||e.key==="]"){
      if(!order.length||!current) return;
      var i=order.indexOf(current); if(i<0) return;
      var j=e.key==="]"?i+1:i-1;
      if(j>=0&&j<order.length){ e.preventDefault(); openDoc(order[j]); }
    }
  });
})();
