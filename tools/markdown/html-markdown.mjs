import { parseHTML } from 'linkedom';
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';

export function htmlToMarkdown(html, file, origin) {
  const { document } = parseHTML(html);
  const title = document.querySelector('title')?.textContent || file;
  const interactive = !!document.querySelector('canvas, script[type="module"]');
  document.querySelectorAll('script, style, head, svg, canvas, template, nav, button, select, input, textarea, label.inc, [aria-hidden="true"], [hidden]').forEach(el => el.remove());
  // CSS provides spacing and columns in the browser. Preserve that structure in text.
  for (const node of document.querySelectorAll('span, strong, b')) { node.before(' '); node.after(' '); }
  for (const grid of document.querySelectorAll('.glist, .olist, .plist')) {
    const rows = [...grid.children];
    if (!rows[0]?.classList.contains('head')) continue;
    const table = document.createElement('table');
    rows.forEach((row, i) => {
      const group = document.createElement(i === 0 ? 'thead' : 'tbody');
      const tr = document.createElement('tr');
      const cells = [...row.children].flatMap(cell => cell.classList.contains('stats') ? [...cell.children] : [cell]);
      for (const cell of cells) {
        const td = document.createElement(i === 0 ? 'th' : 'td');
        td.innerHTML = cell.innerHTML;
        tr.append(td);
      }
      group.append(tr); table.append(group);
    });
    grid.replaceWith(table);
  }
  for (const node of document.querySelectorAll('[href], [src]')) {
    for (const attr of ['href', 'src']) {
      const value = node.getAttribute(attr);
      if (value && !/^(?:javascript|data):/i.test(value)) node.setAttribute(attr, new URL(value, origin + '/' + file).href);
      else if (value) node.removeAttribute(attr);
    }
  }
  const converter = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced', bulletListMarker: '-' });
  converter.use(gfm);
  converter.addRule('readableTableCells', {
    filter: ['th', 'td'],
    replacement: (content, node) => `${node === node.parentNode.firstChild ? '| ' : ' '}${content.trim().replace(/\n+/g, '<br>').replace(/(?<!\\)\|/g, '\\|')} |`,
  });
  converter.addRule('sectionLabels', {
    filter: node => node.nodeName === 'DIV' && node.classList.contains('sec'),
    replacement: content => '\n\n## ' + content.trim() + '\n\n',
  });
  converter.addRule('videoLinks', {
    filter: ['video'],
    replacement: (_content, node) => node.getAttribute('src') ? `[Video](${node.getAttribute('src')})` : '',
  });
  const body = converter.turndown(document.body?.innerHTML || document.toString());
  return `${document.querySelector('h1') ? '' : '# ' + title + '\n\n'}> [HTML view](${origin + '/' + file.replace(/index\.html$/, '')}) · [Markdown index](${origin}/llms.txt). Generated from the same published source.${interactive ? ' Interactive controls and 3D scenes are available in the HTML view.' : ''}\n\n${body}\n`;
}

