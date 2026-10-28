/** Offline publishing only: allowlisted public HTML/data, never backend or local files. */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { parseHTML } from 'linkedom';
import { htmlToMarkdown } from './html-markdown.mjs';
import config from './site.config.mjs';

const root = path.resolve(import.meta.dirname, '../..');
const web = path.join(root, config.webRoot);
const check = process.argv.includes('--check');
const tracked = execFileSync('git', ['ls-files', '-z'], { cwd: root }).toString().split('\0').filter(Boolean);
const files = tracked.filter(file => config.include(file)).map(file => path.posix.relative(config.webRoot || '.', file)).sort();
if (!files.includes('index.html')) throw new Error('No public homepage selected');
const read = file => readFile(path.join(web, file), 'utf8');
const outputs = new Map();
const entries = [];
for (const file of files) {
  const source = await read(file);
  const { document } = parseHTML(source);
  const title = document.querySelector('title')?.textContent.trim() || file;
  const target = file.replace(/\.html$/, '.md');
  const alternate = `<link rel="alternate" type="text/markdown" href="/${target}" title="Markdown">`;
  const stripped = source.replace(/<link\b[^>]*\brel="alternate"[^>]*\btype="text\/markdown"[^>]*>\s*/g, '');
  if (!stripped.includes('</head>')) throw new Error(`Missing HTML head: ${file}`);
  const html = stripped.replace('</head>', alternate + '\n</head>');
  const extra = await config.extra?.(file, read) || '';
  outputs.set(file, html);
  outputs.set(target, htmlToMarkdown(html, file, config.origin) + extra);
  entries.push({ title, path: target });
}
const more = await config.discovery?.(read) || [];
outputs.set('llms.txt', `# ${config.name}\n\n> Markdown counterparts of the public website, regenerated from its HTML and published data.\n\nUse these explicit URLs without JavaScript. Interactive simulations and live controls remain in the HTML view. This index does not expose private repository or backend files.\n\n## Pages\n\n${entries.map(e => `- [${e.title}](${config.origin}/${e.path})`).join('\n')}\n${more.length ? '\n## Published documents\n\n' + more.map(e => `- [${e.title}](${config.origin}/${e.path})`).join('\n') + '\n' : ''}`);
const marker = '# BEGIN generated Markdown headers';
let headers = await read('_headers');
headers = headers.replace(/# BEGIN generated Markdown headers[\s\S]*?# END generated Markdown headers\n?/g, '').trimEnd();
outputs.set('_headers', `${headers}\n\n${marker}\n/*.md\n  Content-Type: text/markdown; charset=utf-8\n/llms.txt\n  Content-Type: text/plain; charset=utf-8\n  Cache-Control: no-cache\n# END generated Markdown headers\n`);
const stale = [];
for (const [file, content] of outputs) {
  if (check) {
    let current; try { current = await read(file); } catch { /* missing is stale */ }
    if (current !== content) stale.push(file);
  } else {
    await mkdir(path.dirname(path.join(web, file)), { recursive: true });
    await writeFile(path.join(web, file), content);
  }
}
if (stale.length) throw new Error(`Stale Markdown/discovery: ${stale.join(', ')}. Run npm --prefix tools/markdown run generate and commit the generated files.`);
console.log(`${check ? 'Checked' : 'Generated'} ${entries.length} public Markdown pages and ${more.length} existing public documents.`);
