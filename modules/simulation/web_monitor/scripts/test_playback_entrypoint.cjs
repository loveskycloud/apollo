// Exercise the production HTML's connection selection, not a copied algorithm.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname,
  '../rerun/crates/viewer/re_web_viewer_server/web_viewer/index.html'), 'utf8');
const begin = html.indexOf('        const page_host =');
const end = html.indexOf('        const options =', begin);
assert(begin > 0 && end > begin, 'Production connection setup must be located');
for (const [search, hostname, expected] of [
  ['', '127.0.0.1', 'rerun+http://127.0.0.1:9876/proxy'],
  ['url=', '127.0.0.1', 'rerun+http://127.0.0.1:9876/proxy'],
  ['url=%20%20&url=', '127.0.0.1', 'rerun+http://127.0.0.1:9876/proxy'],
  ['url=&grpc_host=example.test&grpc_port=9988', '127.0.0.1', 'rerun+http://example.test:9988/proxy'],
  ['url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy', '127.0.0.1', 'rerun+http://127.0.0.1:9877/proxy'],
  // Remote browser opened via LAN IP but bookmark still has localhost.
  ['url=rerun%2Bhttp%3A%2F%2Flocalhost%3A9876%2Fproxy', '192.168.1.10', 'rerun+http://192.168.1.10:9876/proxy'],
  ['url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9876%2Fproxy', '192.168.1.10', 'rerun+http://192.168.1.10:9876/proxy'],
]) {
  const context = {
    query: new URLSearchParams(search),
    window: {location: {hostname}},
    console,
  };
  vm.runInNewContext(
    '(function () {\n' + html.slice(begin, end) + '\n  globalThis.urls = effective_urls;\n})();',
    context,
  );
  assert.equal(context.urls.length, 1);
  assert.equal(context.urls[0], expected);
  assert.equal(context.window.__web_monitor_proxy_url, expected);
}
console.log('PASS: production entrypoint cases, including loopback→page-host rewrite');
