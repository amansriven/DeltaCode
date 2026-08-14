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
  assert.match(html, /Connect GitHub/);
  assert.doesNotMatch(html, /Install the bot|Install the GitHub bot/);
  assert.doesNotMatch(html, /codex-preview/);
  assert.doesNotMatch(html, /react-loading-skeleton/);
});

test("login starts the real GitHub OAuth flow", async () => {
  const response = await render("/login");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Continue with GitHub/);
  assert.match(html, /href="[^"]*\/auth\/github\/login\?redirect_uri=%2Fmigrations"/);
  assert.doesNotMatch(html, /Preview GitHub sign-in/);
  assert.doesNotMatch(html, /href="\/onboarding"[^>]*>[^<]*Preview GitHub sign-in/);
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
    repositoriesResponse,
    integrationsResponse,
    settingsResponse,
  ] = await Promise.all([
    render("/repositories"),
    render("/settings/integrations"),
    render("/settings/account"),
  ]);
  assert.equal(repositoriesResponse.status, 200);
  assert.equal(integrationsResponse.status, 200);
  assert.equal(settingsResponse.status, 200);

  const [repositories, integrations, settings] = await Promise.all([
    repositoriesResponse.text(),
    integrationsResponse.text(),
    settingsResponse.text(),
  ]);
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
