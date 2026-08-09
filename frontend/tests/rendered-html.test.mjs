import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import test, { after, before } from "node:test";

const templateRoot = new URL("../", import.meta.url);
const frontendDirectory = fileURLToPath(templateRoot);
let serverProcess;
let baseUrl;
let serverOutput = "";

async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const { port } = address;
  await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  return port;
}

before(async () => {
  const port = await reservePort();
  baseUrl = `http://127.0.0.1:${port}`;
  serverProcess = spawn(
    process.execPath,
    ["node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: frontendDirectory,
      env: { ...process.env, NODE_ENV: "production" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  serverProcess.stdout.on("data", (chunk) => {
    serverOutput += chunk;
  });
  serverProcess.stderr.on("data", (chunk) => {
    serverOutput += chunk;
  });

  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (serverProcess.exitCode !== null) {
      throw new Error(`Next.js exited before becoming ready.\n${serverOutput}`);
    }
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for Next.js.\n${serverOutput}`);
});

after(async () => {
  if (!serverProcess || serverProcess.exitCode !== null) return;
  serverProcess.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => serverProcess.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ]);
});

async function render(path = "/") {
  return fetch(new URL(path, baseUrl), {
    headers: { accept: "text/html" },
  });
}

test("server-renders the Delta Code landing page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Delta Code<\/title>/i);
  assert.match(html, /<link rel="icon" href="\/favicon\.svg" type="image\/svg\+xml"/i);
  assert.match(html, /The AI review bot/);
  assert.match(html, /for breaking API changes/);
  assert.match(html, /Delta Code ships API migrations/);
  assert.match(html, /auth\/github\/login\?redirect_uri=%2Fmigrations/);
  assert.doesNotMatch(html, /codex-preview/);
  assert.doesNotMatch(html, /react-loading-skeleton/);
});

test("server-renders the expanded public product site", async () => {
  const responses = await Promise.all([
    render("/product"),
    render("/how-it-works"),
    render("/docs"),
    render("/security"),
  ]);
  responses.forEach((response) => assert.equal(response.status, 200));

  const [product, workflow, docs, security] = await Promise.all(
    responses.map((response) => response.text()),
  );
  assert.match(product, /One review bot from provider change to verified PR/);
  assert.match(workflow, /A provider change enters. A verified draft PR comes out/);
  assert.match(docs, /Local quickstart/);
  assert.match(security, /Least privilege from source capture to pull request/);
});

test("server-renders the complete authenticated product routes", async () => {
  const [
    overviewResponse,
    runsResponse,
    detailResponse,
    repositoriesResponse,
    integrationsResponse,
    settingsResponse,
  ] = await Promise.all([
    render("/overview"),
    render("/runs"),
    render("/runs/14"),
    render("/repositories"),
    render("/settings/integrations"),
    render("/settings/account"),
  ]);
  assert.equal(overviewResponse.status, 200);
  assert.equal(runsResponse.status, 200);
  assert.equal(detailResponse.status, 200);
  assert.equal(repositoriesResponse.status, 200);
  assert.equal(integrationsResponse.status, 200);
  assert.equal(settingsResponse.status, 200);

  const [overview, runs, detail, repositories, integrations, settings] = await Promise.all([
    overviewResponse.text(),
    runsResponse.text(),
    detailResponse.text(),
    repositoriesResponse.text(),
    integrationsResponse.text(),
    settingsResponse.text(),
  ]);
  assert.match(overview, /Workspace overview/);
  assert.match(overview, /AI Triage/);
  assert.match(overview, /No model request is being made|AI interpretation—not verification evidence/);
  assert.match(overview, /Repository health/);
  assert.match(runs, /Recent runs/);
  assert.match(runs, /By repo/);
  assert.match(runs, /Loading verification runs|You’re exploring a product preview/);
  assert.match(detail, /Loading run evidence|Verification verdict/);
  assert.match(repositories, /Repository directory/);
  assert.match(repositories, /Loading repository access|Product preview|GitHub App access/);
  assert.match(integrations, /Connected services/);
  assert.match(integrations, /Loading GitHub connection|GitHub identity/);
  assert.match(settings, /Settings sections/);
  assert.match(settings, /Account/);
  assert.match(settings, /Loading settings|Sign in required/);
});

test("server-renders the migration inbox and review workflow", async () => {
  const [inboxResponse, migrationResponse, changeResponse, providersResponse] = await Promise.all([
    render("/migrations"),
    render("/migrations/migration-checkout-source"),
    render("/changes/change-payments-source"),
    render("/providers"),
  ]);
  [inboxResponse, migrationResponse, changeResponse, providersResponse].forEach((response) => {
    assert.equal(response.status, 200);
  });

  const [inbox, migration, change, providers] = await Promise.all([
    inboxResponse.text(),
    migrationResponse.text(),
    changeResponse.text(),
    providersResponse.text(),
  ]);
  assert.match(inbox, /Migration inbox/);
  assert.match(inbox, /Repository migrations/);
  assert.match(inbox, /Automation readiness/);
  assert.match(migration, /Loading migration evidence|Charge requests must use payment_method/);
  assert.match(change, /Loading normalized provider change|Normalized provider change/);
  assert.match(providers, /Source operations/);
  assert.match(providers, /Provider preview|Source health/);
});

test("starter preview implementation is removed", async () => {
  const { access, readFile } = await import("node:fs/promises");
  await assert.rejects(access(new URL("../app/_sites-preview", templateRoot)));

  const packageJson = await readFile(new URL("../package.json", import.meta.url), "utf8");
  const appSource = await readFile(new URL("../app/DeltaCodeApp.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(appSource, /github\.com\/apps\/deltacodeapp\/installations\/new/);
  assert.match(appSource, /github\.com\/amansriven\/DeltaCode/);
});

test("GitHub identity uses the authenticated user's name and avatar fallback", async () => {
  const { readFile } = await import("node:fs/promises");
  const appSource = await readFile(new URL("../app/DeltaCodeApp.tsx", import.meta.url), "utf8");
  const dataSource = await readFile(new URL("../app/lib/data.ts", import.meta.url), "utf8");

  assert.match(dataSource, /name: string \| null/);
  assert.match(appSource, /function userDisplayName/);
  assert.match(appSource, /function userInitials/);
  assert.match(appSource, /<UserAvatar user=\{user\}/);
  assert.doesNotMatch(appSource, /Connected as amansriven/);
  assert.doesNotMatch(dataSource, /amansriven\//);
});
