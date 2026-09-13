/**
 * VOS3Hook — React hook for frontend extensions
 *
 * Usage in React components:
 *   const { data, loading, error } = useVOS3("vos3:records:read", () =>
 *     app.client.listRecords(entityId)
 *   );
 */

import type { VOS3Scope } from "./types";

/** Hook registration for frontend extensions */
export class VOS3Hook {
  private _extensions: Map<string, { component: any; scope: VOS3Scope }> = new Map();

  /** Register a UI extension */
  registerExtension(slot: string, component: any, requiredScope: VOS3Scope) {
    this._extensions.set(slot, { component, scope: requiredScope });
  }

  /** Get registered extension for a slot */
  getExtension(slot: string) {
    return this._extensions.get(slot);
  }

  /** List all registered extensions */
  listExtensions() {
    return Array.from(this._extensions.entries()).map(([slot, ext]) => ({
      slot,
      scope: ext.scope,
    }));
  }
}
