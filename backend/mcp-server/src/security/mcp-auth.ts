import { createHash, timingSafeEqual } from "node:crypto";
import { isIP } from "node:net";
import type { IncomingMessage } from "node:http";
import { z } from "zod";

export type Principal = { userId: string; role: "user" | "admin" };
const identity = z.object({ userId: z.string().min(1).max(128), role: z.enum(["user", "admin"]) }).strict();
const credential = identity.extend({ token: z.string().min(32).max(512) }).strict();
const digest = (value: string) => createHash("sha256").update(value).digest();

export function createAuth(env: NodeJS.ProcessEnv) {
  const transport = env.TRANSPORT || "httpStream";
  if (transport !== "stdio" && transport !== "httpStream") throw new Error("Unsupported MCP transport");
  const host = env.MCP_HOST || "127.0.0.1";
  if (!isIP(host)) throw new Error("MCP_HOST must be a literal IPv4 or IPv6 address");
  const local = transport === "stdio"
    ? identity.parse({ userId: env.MCP_STDIO_USER_ID, role: env.MCP_STDIO_ROLE || "user" })
    : undefined;
  const entries = transport === "httpStream"
    ? z.array(credential).min(1).parse(JSON.parse(env.MCP_AUTH_TOKENS || "[]"))
    : [];
  if (new Set(entries.map(entry => entry.token)).size !== entries.length) throw new Error("Duplicate MCP credentials");
  const credentials = entries.map(({ token, ...principal }) => ({ digest: digest(token), principal }));
  return {
    transport,
    host,
    async authenticate(request: IncomingMessage): Promise<Principal | undefined> {
      const authorization = request.headers.authorization;
      if (!authorization || !/^Bearer [^\s]+$/.test(authorization)) return undefined;
      const candidate = digest(authorization.slice(7));
      let match: Principal | undefined;
      for (const entry of credentials) {
        if (timingSafeEqual(candidate, entry.digest)) match = entry.principal;
      }
      return match;
    },
    principal(session: Principal | undefined): Principal {
      const principal = transport === "stdio" ? local : session;
      if (!principal) throw new Error("Authentication required");
      return principal;
    },
  };
}

export function requireAdmin(principal: Principal): void {
  if (principal.role !== "admin") throw new Error("Administrator permission required");
}
export function quotaSubject(principal: Principal, requested?: string): string {
  if (requested !== undefined && requested !== principal.userId) requireAdmin(principal);
  return requested ?? principal.userId;
}
