// Exercise the production HTML's connection selection, not a copied algorithm.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname,
  '../rerun/crates/viewer/re_web_viewer_server/web_viewer/index.html'), 'utf8');
const begin = html.indexOf('        const user_urls =');
const end = html.indexOf('        const options =', begin);
assert(begin > 0 && end > begin, 'Production connection setup must be located');
for (const [search, expected] of [
  ['', 'rerun+http://127.0.0.1:9876/proxy'],
  ['url=', 'rerun+http://127.0.0.1:9876/proxy'],
  ['url=%20%20&url=', 'rerun+http://127.0.0.1:9876/proxy'],
  ['url=&grpc_host=example.test&grpc_port=9988', 'rerun+http://example.test:9988/proxy'],
  ['url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy', 'rerun+http://127.0.0.1:9877/proxy'],
]) {
  const context = {query:new URLSearchParams(search), window:{location:{hostname:'127.0.0.1'}}, console};
  vm.runInNewContext(html.slice(begin,end) + '\nglobalThis.urls = effective_urls;', context);
  assert.equal(context.urls.length, 1);
  assert.equal(context.urls[0], expected);
  assert.equal(context.window.__web_monitor_proxy_url, expected);
}
console.log('PASS: five production entrypoint cases, including empty URL and nondefault proxy');
