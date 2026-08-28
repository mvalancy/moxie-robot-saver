// Validate every ```mermaid block in the repo's markdown. Requires: npm i mermaid jsdom
// Run:  node scripts/check-mermaid.mjs
import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!DOCTYPE html><body></body>', { pretendToBeVisual:true });
globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.navigator = dom.window.navigator;
const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad:false, securityLevel:'loose' });
function walk(d){ let o=[]; for(const e of readdirSync(d)){ if(e==='.git'||e==='node_modules')continue; const p=join(d,e); const s=statSync(p); if(s.isDirectory())o=o.concat(walk(p)); else if(e.endsWith('.md'))o.push(p);} return o; }
let n=0,bad=0;
for(const f of walk('.')){
  const t=readFileSync(f,'utf8');
  const blocks=[...t.matchAll(/```mermaid\n([\s\S]*?)```/g)].map(m=>m[1]);
  for(let i=0;i<blocks.length;i++){ n++;
    try{ await mermaid.parse(blocks[i]); }
    catch(e){ bad++; console.log(`FAIL ${f} block ${i+1}: ${String(e.message||e).split('\n')[0]}`); }
  }
}
console.log(`checked ${n} mermaid diagrams, ${bad} failed`);
process.exit(bad?1:0);
