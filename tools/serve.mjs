// Zero-dependency local server:  node tools/serve.mjs  → http://localhost:5173
// Prints your LAN address too, so you can open it on your iPhone (same Wi-Fi).
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, extname, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { networkInterfaces } from "node:os";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = +process.env.PORT || 5173;
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml",
  ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".woff2": "font/woff2", ".txt": "text/plain", ".json": "application/json" };

createServer(async (req, res) => {
  try {
    let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (p.endsWith("/")) p += "index.html";
    const file = normalize(join(ROOT, p));
    if (!file.startsWith(ROOT)) throw 0;
    const s = await stat(file);
    const type = TYPES[extname(file).toLowerCase()] || "application/octet-stream";
    const range = req.headers.range && /bytes=(\d*)-(\d*)/.exec(req.headers.range);
    const data = await readFile(file);
    if (range) {   // Safari needs byte ranges for audio
      const start = range[1] ? +range[1] : 0, end = range[2] ? +range[2] : s.size - 1;
      res.writeHead(206, { "Content-Type": type, "Content-Range": `bytes ${start}-${end}/${s.size}`, "Accept-Ranges": "bytes", "Content-Length": end - start + 1 });
      return res.end(data.subarray(start, end + 1));
    }
    res.writeHead(200, { "Content-Type": type, "Content-Length": s.size, "Accept-Ranges": "bytes", "Cache-Control": "no-store" });
    res.end(data);
  } catch { res.writeHead(404); res.end("not found"); }
}).listen(PORT, "0.0.0.0", () => {
  console.log(`\n  Local:   http://localhost:${PORT}/?preview=ash`);
  for (const list of Object.values(networkInterfaces())) for (const n of list || [])
    if (n.family === "IPv4" && !n.internal) console.log(`  iPhone:  http://${n.address}:${PORT}/?preview=ash`);
  console.log("");
});
