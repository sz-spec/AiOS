/**
 * VOS3 API Client — Typed HTTP client for VOS3 platform APIs
 */

import type { VOS3AppConfig, VOS3Response } from "./types";

const DEFAULT_BASE_URL = "https://api.vos3.app";

export class VOS3Client {
  private baseUrl: string;
  private headers: Record<string, string>;

  constructor(config: VOS3AppConfig) {
    this.baseUrl = config.baseUrl || DEFAULT_BASE_URL;
    this.headers = {
      "Authorization": `Bearer ${config.apiKey}`,
      "X-VOS3-App-Id": config.appId,
      "Content-Type": "application/json",
    };
    if (config.version) {
      this.headers["X-VOS3-App-Version"] = config.version;
    }
  }

  /** Make an authenticated API request */
  async request<T>(method: string, path: string, body?: unknown): Promise<VOS3Response<T>> {
    const url = `${this.baseUrl}${path}`;
    const init: RequestInit = {
      method,
      headers: this.headers,
    };
    if (body) {
      init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    const data = await res.json();
    if (!res.ok) {
      return { data: data as T, ok: false, error: data.detail || res.statusText };
    }
    return { data, ok: true };
  }

  // === Entities ===

  async listEntities(orgId: string) {
    return this.request<any[]>("GET", `/api/apps/v1/entities?org=${orgId}`);
  }

  async getEntity(entityId: string) {
    return this.request<any>("GET", `/api/apps/v1/entities/${entityId}`);
  }

  // === Records ===

  async listRecords(entityId: string) {
    return this.request<any[]>("GET", `/api/apps/v1/records?entity=${entityId}`);
  }

  async createRecord(entityId: string, data: Record<string, unknown>) {
    return this.request<any>("POST", `/api/apps/v1/records`, { entityId, data });
  }

  // === Workflows ===

  async executeWorkflow(workflowId: string, input?: Record<string, unknown>) {
    return this.request<any>("POST", `/api/apps/v1/workflows/${workflowId}/execute`, { input });
  }

  // === AI ===

  async generateText(prompt: string, options?: { model?: string; maxTokens?: number }) {
    return this.request<{ content: string }>("POST", `/api/apps/v1/ai/generate`, { prompt, ...options });
  }

  // === Files ===

  async readFile(path: string) {
    return this.request<{ content: string }>("GET", `/api/apps/v1/files?path=${encodeURIComponent(path)}`);
  }

  async writeFile(path: string, content: string) {
    return this.request<void>("PUT", `/api/apps/v1/files`, { path, content });
  }
}
