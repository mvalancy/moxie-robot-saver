export default {
  name: 'Moxie Robot Saver',
  origin: 'https://moxie.mattvalancy.com',
  webRoot: 'sim/web',
  include: file => /^sim\/web\/[^/]+\.html$/.test(file) && !file.endsWith('/404.html'),
  async extra(file, read) {
    if (file === 'docs.html') {
      const index = JSON.parse(await read('docs-index.json'));
      return '\n## Published documentation\n\n' + index.files.map(item => `- [${item.title}](https://moxie.mattvalancy.com/docs-bundle/${item.path})`).join('\n') + '\n';
    }
    if (file === 'cloud.html') {
      const data = JSON.parse(await read('fixtures/cloud.json'));
      return '\n## Example console data\n\nThis is the same published sample fixture used by the console, not a live account.\n\n```json\n' + JSON.stringify(data, null, 2) + '\n```\n';
    }
    return '';
  },
  async discovery(read) {
    return JSON.parse(await read('docs-index.json')).files.map(item => ({ title: item.title, path: 'docs-bundle/' + item.path }));
  },
};
