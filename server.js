// Minimal static file server for local preview of the app (index.html) and the docs viewer (docs.html).
// No dependencies. Usage: node server.js  (PORT env var optional, default 8080)
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 8080;
const HOST = process.env.HOST || '127.0.0.1'; // loopback only by default; HOST=0.0.0.0 to share on the LAN
const ROOT = __dirname;

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.yml': 'text/yaml; charset=utf-8',
  '.sh': 'text/plain; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

function send(res, status, body, type) {
  res.writeHead(status, { 'Content-Type': type || 'text/plain; charset=utf-8', 'Cache-Control': 'no-cache' });
  res.end(body);
}

function notFound(res) {
  fs.readFile(path.join(ROOT, '404.html'), (err, page) =>
    err ? send(res, 404, '404 Not Found') : send(res, 404, page, MIME_TYPES['.html']));
}

const server = http.createServer((req, res) => {
  let urlPath;
  try {
    urlPath = decodeURIComponent(new URL(req.url, `http://${req.headers.host || 'localhost'}`).pathname);
  } catch (e) {
    return send(res, 400, '400 Bad Request');
  }
  if (urlPath.includes('\0')) return send(res, 400, '400 Bad Request');
  if (urlPath === '/') urlPath = '/index.html';
  if (urlPath === '/docs' || urlPath === '/docs/') urlPath = '/docs.html';
  // Hidden files and directories (.git, .envrc, .github, …) are never served.
  if (urlPath.split('/').some(segment => segment.startsWith('.'))) return notFound(res);

  // Resolve inside the repository root only (no path traversal).
  const filePath = path.normalize(path.join(ROOT, urlPath));
  if (!filePath.startsWith(ROOT + path.sep) && filePath !== ROOT) return send(res, 403, '403 Forbidden');

  fs.readFile(filePath, (err, content) => {
    if (err) {
      if (err.code === 'ENOENT' || err.code === 'EISDIR') return notFound(res);
      return send(res, 500, `Server Error: ${err.code}`);
    }
    const ext = path.extname(filePath).toLowerCase();
    const type = MIME_TYPES[ext] || (ext === '' ? 'text/plain; charset=utf-8' : 'application/octet-stream');
    send(res, 200, content, type);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`\n🚀 NovaSmart AI Governance Dashboard App is running at:`);
  console.log(`   ➜ App:  http://localhost:${PORT}`);
  console.log(`   ➜ Docs: http://localhost:${PORT}/docs.html`);
  console.log(`   (bound to ${HOST}; set HOST=0.0.0.0 to allow other devices on your network)\n`);
});
