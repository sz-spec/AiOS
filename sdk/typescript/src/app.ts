/**
 * VOS3App — Base class for VOS3 platform applications
 *
 * Usage:
 *   const app = new VOS3App({
 *     appId: "my-app",
 *     apiKey: process.env.VOS3_API_KEY!,
 *     scopes: ["vos3:records:read", "vos3:records:write"],
 *   });
 */

import { VOS3Client } from "./client";
import type { VOS3AppConfig, VOS3Scope } from "./types";

export class VOS3App {
  readonly appId: string;
  readonly client: VOS3Client;
  readonly scopes: VOS3Scope[];
  private _hooks: Map<string, Array<(...args: any[]) => void | Promise<void>>> = new Map();

  constructor(config: VOS3AppConfig) {
    this.appId = config.appId;
    this.scopes = config.scopes || [];
    this.client = new VOS3Client(config);
  }

  /** Register a hook handler */
  on(hookName: string, handler: (...args: any[]) => void | Promise<void>) {
    if (!this._hooks.has(hookName)) {
      this._hooks.set(hookName, []);
    }
    this._hooks.get(hookName)!.push(handler);
  }

  /** Emit a hook event */
  async emit(hookName: string, ...args: any[]) {
    const handlers = this._hooks.get(hookName) || [];
    for (const handler of handlers) {
      await handler(...args);
    }
  }

  /** Check if a scope is granted */
  hasScope(scope: VOS3Scope): boolean {
    return this.scopes.includes(scope);
  }
}
