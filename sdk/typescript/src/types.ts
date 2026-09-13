/**
 * VOS3 SDK Types
 */

/** OAuth 2.0 scopes for VOS3 API access */
export type VOS3Scope =
  | "vos3:entities:read"
  | "vos3:entities:write"
  | "vos3:records:read"
  | "vos3:records:write"
  | "vos3:workflows:execute"
  | "vos3:ai:generate"
  | "vos3:files:read"
  | "vos3:files:write"
  | "vos3:kernel:execute";

/** App configuration */
export interface VOS3AppConfig {
  appId: string;
  apiKey: string;
  baseUrl?: string;
  scopes?: VOS3Scope[];
  version?: string;
}

/** App manifest (app.json) */
export interface AppManifest {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  type: "plugin" | "fullstack" | "kernel";
  main: string;
  scopes: VOS3Scope[];
  category: string;
  pricing: "free" | "paid" | "subscription";
  price?: number;
  icon?: string;
  screenshots?: string[];
  hooks?: string[];
  config_schema?: Record<string, unknown>;
}

/** API response wrapper */
export interface VOS3Response<T> {
  data: T;
  ok: boolean;
  error?: string;
}
