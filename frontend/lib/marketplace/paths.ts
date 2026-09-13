/**
 * Path conventions for vertical-aware kernel VFS storage.
 *
 * When a platform (vertical) is active, per-platform artifacts go into
 *   /disk/<artifact>/<vertical-id>/...
 * so the dashboard can list a single subdir to filter by platform without
 * reading every file's contents.
 *
 * The synthetic "all" vertical maps to the unscoped root path so existing
 * un-tagged artifacts remain reachable.
 */

import type { VerticalId } from './verticals';

const WORKFLOWS_ROOT = '/disk/workflows';

export function workflowsDirFor(platform: VerticalId): string {
  if (platform === 'all') return WORKFLOWS_ROOT;
  return `${WORKFLOWS_ROOT}/${platform}`;
}

export const WORKFLOWS_BASE_DIR = WORKFLOWS_ROOT;
