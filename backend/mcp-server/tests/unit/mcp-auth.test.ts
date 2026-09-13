import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { randomBytes } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createAuth } from "../../src/security/mcp-auth.js";

const userToken = randomBytes(32).toString("hex");
const bobToken = randomBytes(32).toString("hex");
let temporaryDirectory: string;
const adminToken = randomBytes(32).toString("hex");
let child: ChildProcess;
let endpoint: string;
let startupLog = "";
async function request(token?: string, name = "v_quota_status", args: Record<string, unknown> = {}) {
  return fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
  });
}

beforeAll(async () => {
  const probe = createServer();
  await new Promise<void>((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", resolve);
  });
  const port = (probe.address() as { port: number }).port;
  await new Promise<void>(resolve => probe.close(() => resolve()));
  endpoint = `http://127.0.0.1:${port}/mcp`;
  temporaryDirectory = mkdtempSync(join(tmpdir(), "vos-mcp-auth-"));
  mkdirSync(join(temporaryDirectory, "cache"));
  const cache = Object.fromEntries([
    ["alice", null, null, "alice default"],
    ["bob", null, null, "bob private"],
    ["alice", "agent-a", null, "alice agent"],
    ["alice", null, "model-a", "alice model"],
  ].map(([user, agent, model, response]) => [JSON.stringify([user, agent, model, "same prompt"]), {
    response, model: "test-cache", timestamp: Date.now(),
  }]));
  writeFileSync(join(temporaryDirectory, "cache", "simple-cache-v2-principals.json"), JSON.stringify(cache));
  child = spawn(process.execPath, ["--import", import.meta.resolve("tsx"), resolve("src/server.ts")], {
    cwd: temporaryDirectory,
    env: { ...process.env, TRANSPORT: "httpStream", PORT: String(port), OPENAI_API_KEY: "", ANTHROPIC_API_KEY: "", MCP_AUTH_TOKENS: JSON.stringify([
      { token: userToken, userId: "alice", role: "user" },
      { token: adminToken, userId: "operator", role: "admin" },
      { token: bobToken, userId: "bob", role: "user" },
    ]) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout?.on("data", data => { startupLog += data.toString(); });
  child.stderr?.on("data", data => { startupLog += data.toString(); });
  for (let i = 0; i < 100; i++) {
    try { await request(); return; } catch { await new Promise(resolve => setTimeout(resolve, 50)); }
    if (child.exitCode !== null) throw new Error(`Server exited: ${startupLog}`);
  }
  throw new Error(`Server did not listen: ${startupLog}`);
}, 15000);

afterAll(async () => {
  if (child && child.exitCode === null) {
    const stopped = new Promise<void>(resolve => child.once("exit", () => resolve()));
    child.kill("SIGTERM");
    await stopped;
  }
  if (temporaryDirectory) rmSync(temporaryDirectory, { recursive: true, force: true });
});

describe("actual MCP HTTP authorization", () => {
  it("rejects missing, wrong, and forged credentials", async () => {
    for (const token of [undefined, randomBytes(32).toString("hex"), "admin"]) {
      expect((await request(token)).status).toBe(401);
    }
  });
  it("derives quota identity from the authenticated bearer", async () => {
    const response = await request(userToken);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.result.isError).not.toBe(true);
    expect(body.result.content[0].text).toContain("Quota Status for alice");
  });
  it("rejects cross-user quota reads and administrative actions", async () => {
    for (const [name, args] of [
      ["v_quota_status", { userId: "bob" }],
      ["v_set_quota", { userId: "alice", tier: "enterprise" }],
      ["v_cost_report", {}], ["v_cache_stats", {}], ["v_health", {}],
    ] as const) {
      const body = await (await request(userToken, name, args)).json();
      expect(body.result.isError, name).toBe(true);
    }
  });
  it("isolates actual cached responses by principal, agent and model", async () => {
    for (const [token, args, expected] of [
      [userToken, {}, "alice default"],
      [bobToken, {}, "bob private"],
      [userToken, { agentId: "agent-a" }, "alice agent"],
      [userToken, { forceModel: "model-a" }, "alice model"],
    ] as const) {
      const body = await (await request(token, "v_agent_chat", { message: "same prompt", ...args })).json();
      expect(body.result.isError).not.toBe(true);
      expect(body.result.content[0].text).toContain(expected);
    }
  });
  it("permits explicit administrators to manage another principal's quota", async () => {
    const body = await (await request(adminToken, "v_set_quota", { userId: "alice", tier: "pro" })).json();
    expect(body.result.isError).not.toBe(true);
    expect(body.result.content[0].text).toContain("pro");
    expect((await (await request(userToken)).json()).result.content[0].text).toContain("alice");
  });
});

it("fails closed on missing HTTP configuration and unspecified local identity", () => {
  expect(() => createAuth({})).toThrow();
  expect(() => createAuth({ TRANSPORT: "stdio" })).toThrow();
  expect(() => createAuth({ TRANSPORT: "invalid" })).toThrow();
  const local = createAuth({ TRANSPORT: "stdio", MCP_STDIO_USER_ID: "local-owner" });
  expect(local.principal(undefined)).toEqual({ userId: "local-owner", role: "user" });
});

it("uses an explicit local stdio principal without granting administrator rights", async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ["--import", "tsx", "src/server.ts"],
    cwd: process.cwd(),
    env: { PATH: process.env.PATH || "", TRANSPORT: "stdio", MCP_STDIO_USER_ID: "local-reader", OPENAI_API_KEY: "" },
    stderr: "pipe",
  });
  transport.stderr?.on("data", () => {});
  const client = new Client({ name: "auth-test", version: "1.0.0" });
  try {
    await client.connect(transport);
    const status = await client.callTool({ name: "v_quota_status", arguments: {} });
    expect(JSON.stringify(status)).toContain("Quota Status for local-reader");
    const denied = await client.callTool({ name: "v_set_quota", arguments: { userId: "local-reader", tier: "enterprise" } });
    expect(denied.isError).toBe(true);
  } finally {
    await client.close();
    await transport.close();
  }
}, 10000);

it("defaults to loopback and accepts only explicit literal bind addresses without bypassing auth", () => {
  const env = { MCP_AUTH_TOKENS: JSON.stringify([{ token: userToken, userId: "alice", role: "user" }]) };
  expect(createAuth(env).host).toBe("127.0.0.1");
  expect(createAuth({ ...env, MCP_HOST: "0.0.0.0" }).host).toBe("0.0.0.0");
  expect(createAuth({ ...env, MCP_HOST: "::1" }).host).toBe("::1");
  expect(() => createAuth({ ...env, MCP_HOST: "example.com" })).toThrow();
  expect(() => createAuth({ MCP_HOST: "0.0.0.0" })).toThrow();
});

it("serves only a minimal unauthenticated liveness response", async () => {
  const response = await fetch(endpoint.replace("/mcp", "/health"));
  expect(response.status).toBe(200);
  expect(await response.text()).toBe("ok");
});
