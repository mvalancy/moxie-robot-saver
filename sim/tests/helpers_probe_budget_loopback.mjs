/* Child-process helper for test_mode.mjs. That suite deliberately replaces global timers
 * and fetch for browser fixtures, so real loopback HTTP belongs in a fresh Node process. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { ProbeBudget, ProbeBudgetError } from "../tools/probe_budget.mjs";

const paths = [];
const server = createServer((req, res) => {
  paths.push(req.url);
  if (req.url === "/redirect") {
    res.writeHead(307, { Location: "/landed" });
    res.end("redirect");
    return;
  }
  if (req.url === "/slow-success" || req.url === "/slow-error") {
    res.writeHead(req.url === "/slow-success" ? 200 : 503,
                  { "Content-Type": "application/json" });
    res.flushHeaders();
    setTimeout(() => res.end('{"done":true}'), 120);
    return;
  }
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end('{"done":true}');
});

server.listen(0, "127.0.0.1");
await once(server, "listening");
const address = server.address();
const origin = `http://127.0.0.1:${address.port}`;
const proof = {};

try {
  const redirects = new ProbeBudget({ maxAttempts: 1, timeoutMs: 1000 });
  const redirected = await redirects.requestText(origin + "/redirect");
  assert.equal(redirected.response.status, 307);
  assert.deepEqual(paths, ["/redirect"]);
  assert.equal(redirects.summary(), "1/1");
  proof.redirect = { status: redirected.response.status, requests: paths.length,
                     count: redirects.summary() };

  for (const [route, kind] of [["/slow-success", "success"], ["/slow-error", "error"]]) {
    const budget = new ProbeBudget({ maxAttempts: 1, timeoutMs: 20 });
    let message = "";
    try { await budget.requestText(origin + route); }
    catch (err) {
      assert.ok(err instanceof ProbeBudgetError);
      message = err.message;
    }
    assert.match(message, /timed out after 20 ms/);
    assert.equal(budget.attempts, 1);
    proof[kind] = { timedOut: true, requests: 1, count: budget.summary() };
  }

  const ordinary = new ProbeBudget({ maxAttempts: 1, timeoutMs: 1000 });
  const completed = await ordinary.requestText(origin + "/ok");
  assert.equal(completed.text, '{"done":true}');
  assert.equal(ordinary.summary(), "1/1");
  proof.ordinary = { body: completed.text, requests: 1, count: ordinary.summary() };
} finally {
  if (typeof server.closeAllConnections === "function") server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}

process.stdout.write(JSON.stringify(proof));
