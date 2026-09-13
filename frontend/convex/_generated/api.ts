/* eslint-disable */
  /**
   * Generated `api` utility.
   *
   * THIS CODE IS AUTOMATICALLY GENERATED.
   *
   * To regenerate, run `npx convex dev`.
   * @module
   */
  
import type { ApiFromModules, FilterApi, FunctionReference } from "convex/server";
import { anyApi } from "convex/server";
import type * as agentStatus from "../agentStatus.js";
import type * as appReviews from "../appReviews.js";
import type * as apps from "../apps.js";
import type * as authHelpers from "../authHelpers.js";
import type * as billing from "../billing.js";
import type * as builds from "../builds.js";
import type * as chatSessions from "../chatSessions.js";
import type * as developers from "../developers.js";
import type * as expertRequests from "../expertRequests.js";
import type * as organizations from "../organizations.js";
import type * as presence from "../presence.js";
import type * as projects from "../projects.js";
import type * as quota from "../quota.js";
import type * as users from "../users.js";
import type * as vcore from "../vcore.js";
import type * as webhook_seen from "../webhook_seen.js";
import type * as yjsUpdates from "../yjsUpdates.js";

const fullApi: ApiFromModules<{
  "agentStatus": typeof agentStatus,
"appReviews": typeof appReviews,
"apps": typeof apps,
"authHelpers": typeof authHelpers,
"billing": typeof billing,
"builds": typeof builds,
"chatSessions": typeof chatSessions,
"developers": typeof developers,
"expertRequests": typeof expertRequests,
"organizations": typeof organizations,
"presence": typeof presence,
"projects": typeof projects,
"quota": typeof quota,
"users": typeof users,
"vcore": typeof vcore,
"webhook_seen": typeof webhook_seen,
"yjsUpdates": typeof yjsUpdates,
}> = anyApi as any;

/**
 * A utility for referencing Convex functions in your app's public API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = api.myModule.myFunction;
 * ```
 */
export const api: FilterApi<typeof fullApi, FunctionReference<any, "public">> = anyApi as any;

/**
 * A utility for referencing Convex functions in your app's internal API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = internal.myModule.myFunction;
 * ```
 */
export const internal: FilterApi<typeof fullApi, FunctionReference<any, "internal">> = anyApi as any;
