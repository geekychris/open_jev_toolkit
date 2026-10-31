// Regenerate the playground screenshots in docs/images from a live playground.
//
//   node docs/screenshots.mjs <out dir> <playground url>
//   node docs/screenshots.mjs docs/images http://spark.local:3020/
//
// Drives headless Chrome over the DevTools protocol in real time (not --virtual-time-budget, whose
// clock stops during network waits and would make Run all's wall time read as ~0 s). It runs
// "Run all" four at a time, then a full Throughput test (levels 1..32, 10 s each, about 70 s), and
// saves run-all-parallel, throughput-running and throughput-finished. Needs Node 22+ (global
// WebSocket) and Google Chrome; the playground must be reachable without a token (local or --debug).
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const [, , outDir, base] = process.argv;
if (!outDir || !base) { console.error("usage: node docs/screenshots.mjs <out dir> <playground url>"); process.exit(1); }
const PORT = 9333, sleep = ms => new Promise(r => setTimeout(r, ms));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "openjev-shots-"))}`, "--hide-scrollbars", "--window-size=1280,1100", "about:blank"], { stdio: "ignore" });
process.on("exit", () => chrome.kill());   // also on a timeout or other error
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === "page"); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let id = 0; const waiting = new Map();
ws.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } });
const send = (method, params = {}) => new Promise(r => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async expr => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
const shot = async (name, h) => {
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: h, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${outDir}/${name}.png`, Buffer.from(r.result.data, "base64")); console.log("saved", name);
};
const until = async (expr, ms = 300000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await js(expr)) return true; await sleep(1000); } throw new Error("timeout: " + expr); };
await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: base });
await until(`document.querySelectorAll("#list .item[data-id]").length > 0 && document.querySelector("#status .dot.up") !== null`, 30000);

// 1. Run all, 4 at a time (real time, so the wall-clock figure is genuine)
await js(`document.querySelector("#par").value = "4"; document.querySelector("#runall").click(); true`);
await until(`/ (\\d+)\\/\\1 samples/.test(document.querySelector(".stickybar .t")?.textContent || "") && !document.querySelector("#runall").disabled`);
await sleep(500);
console.log("runall:", await js(`document.querySelector(".stickybar .t").textContent`));
await shot("playground-run-all-parallel", 760);

// 2. Throughput test: default levels 1..32, 10 s each; one screenshot mid-run, one at the end
await js(`document.querySelector("#benchbtn").click(); true`);
await until(`!!document.querySelector("#b-levels")`, 10000);
await js(`document.querySelector("#b-levels").value = "1,2,4,8,16,32"; document.querySelector("#b-dur").value = "10"; document.querySelector("#b-unique").checked = true; document.querySelector("#b-start").click(); true`);
await until(`(document.querySelector(".live")?.textContent || "").includes("concurrency 8")`, 120000);
await sleep(5000);
await shot("playground-throughput-running", 1000);
await until(`(document.querySelector(".live")?.textContent || "").startsWith("Finished")`, 300000);
await sleep(1500);
console.log("bench:", await js(`document.querySelector(".peak")?.textContent.replace(/\\s+/g, " ")`));
await shot("playground-throughput", 1130);
ws.close(); chrome.kill(); process.exit(0);
