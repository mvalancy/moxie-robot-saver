import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import path from 'node:path';
import { htmlToMarkdown } from './html-markdown.mjs';
const root = path.resolve(import.meta.dirname, '../..');
const read = file => readFile(path.join(root, 'sim/web', file), 'utf8');

test('generated pages remain current, discoverable and limited to published source', async () => {
  execFileSync(process.execPath, ['tools/markdown/generate.mjs', '--check'], { cwd: root });
  const index = await read('llms.txt');
  const docs = JSON.parse(await read('docs-index.json'));
  for (const file of ['index', 'sim', 'setup', 'cloud', 'docs']) {
    assert.ok(index.includes('/' + file + '.md'));
    assert.ok((await read(file + '.html')).includes(`href="/${file}.md"`));
    assert.ok((await read(file + '.md')).length > 300);
  }
  for (const doc of docs.files) assert.ok(index.includes('/docs-bundle/' + doc.path));
  assert.ok(!index.includes('/.env') && !index.includes('/server/'));
  assert.ok((await read('cloud.md')).includes('not a live account'));
  const fixture = JSON.parse(await read('fixtures/cloud.json'));
  assert.ok((await read('cloud.md')).includes(JSON.stringify(fixture, null, 2)));
  assert.match(await read('_headers'), /Content-Type: text\/markdown; charset=utf-8/);
});

test('HTML content changes reach Markdown while scripts and hidden content stay out', () => {
  const html = '<html><head><title>Example</title></head><body><h1>Current title</h1><p>Fresh text &amp; evidence.</p><script>privateRuntime()</script><div hidden>not published text</div><a href="asset.json">Download</a></body></html>';
  const md = htmlToMarkdown(html, 'index.html', 'https://moxie.mattvalancy.com');
  assert.ok(md.includes('# Current title') && md.includes('Fresh text & evidence.'));
  assert.ok(md.includes('(https://moxie.mattvalancy.com/asset.json)'));
  assert.ok(!md.includes('privateRuntime') && !md.includes('not published text'));
  assert.ok(htmlToMarkdown(html.replace('Fresh text', 'Changed text'), 'index.html', 'https://moxie.mattvalancy.com').includes('Changed text'));
});

test('the real local server serves Markdown MIME, HEAD, downloads and missing-file404s', async () => {
  const server = spawn('python3', ['sim/serve.py', '0'], { cwd: root, stdio: ['ignore', 'pipe', 'inherit'] });
  const timeout = setTimeout(() => server.kill(), 15_000);
  try {
    const [output] = await once(server.stdout, 'data');
    const base = output.toString().match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
    assert.ok(base);
    for (const route of ['/index.md', '/docs.md', '/cloud.md', '/docs-bundle/_root/README.md']) {
      const response = await fetch(base + route);
      assert.equal(response.status, 200);
      assert.match(response.headers.get('content-type'), /^text\/markdown/);
      assert.ok((await response.text()).length > 100);
      const head = await fetch(base + route, { method: 'HEAD' });
      assert.equal(head.status, 200); assert.equal(await head.text(), '');
    }
    assert.equal((await fetch(base + '/fixtures/cloud.json')).status, 200);
    assert.match((await fetch(base + '/')).headers.get('content-type'), /^text\/html/);
    assert.equal((await fetch(base + '/not-a-real-moxie-file.md')).status, 404);
    assert.ok((await read('404.html')).includes('Page not found'));
  } finally {
    clearTimeout(timeout);
    if (server.exitCode === null) { const done = once(server, 'exit'); server.kill(); await done; }
  }
});
