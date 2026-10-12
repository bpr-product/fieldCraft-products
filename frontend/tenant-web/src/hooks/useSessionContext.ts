/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : Builds the EDEN SessionContext fields derived from router
*                state — currentRoute, currentEntity, timeOnPage, routeHistory.
*                utterance/userId/tenantId are filled in per-message by the caller.
*/
import { useCallback, useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { CurrentEntity } from "../services/AIChatService";
import { FEATURE_FLAGS } from "../constants/FeatureFlag";

export interface ChatSessionContext {
  currentRoute: string;
  currentEntity?: CurrentEntity;
  timeOnPage: number;
  routeHistory: string[];
}

// Project-scoped routes: pages that carry a project in their router state.
// Kept in sync with the backend's entity_required=project routes
// (contracts/route_registry.json). Expanded from Gantt-only so EDEN knows
// which project you're viewing on any of these pages, not just the schedule.
const PROJECT_SCOPED_ROUTES = [
  "/ganttCharts",
  "/jobLedger",
  "/soldproduction/projectinformation",
  "/salesestimate/projectinformation",
  "/soldproduction/projectinformation/bom",
  "/soldproduction/projectinformation/payments",
  "/soldproduction/projectinformation/documents",
  "/soldproduction/projectinformation/wipreport",
  "/soldproduction/projectinformation/vendorbill",
  "/soldproduction/projectinformation/deliveryslip",
  "/soldproduction/projectinformation/invoiceview",
  "/soldproduction/projectinformation/invoicePreview",
  "/podetails",
  "/bill",
];

const MAX_ROUTE_HISTORY = 10;

// A projectId is only usable if it's a scalar (string/number) -- some pages
// stash an object under these keys, and String(object) would produce
// "[object Object]", a garbage id. Guard against that.
function scalarId(value: any): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number") return String(value);
  return undefined;
}

export function deriveCurrentEntity(
  pathname: string,
  state: any
): CurrentEntity | undefined {
  // Kill switch: disabling this flag makes EDEN behave as if no route ever
  // carries a project -- same as pre-feature behavior -- without touching
  // any other part of the request.
  if (!FEATURE_FLAGS.ENABLE_EDEN_PROJECT_CONTEXT) return undefined;
  if (!PROJECT_SCOPED_ROUTES.includes(pathname)) return undefined;
  // Pages carry the project in DIFFERENT shapes: some as a `project` object
  // (Gantt), some as a bare `rowData` projectId (Project Information). Read
  // whichever is present, preferring the exact id -- the backend resolves an
  // id exactly (immune to the duplicate-name problem), a name only fuzzily.
  const project = state?.project;
  const id =
    scalarId(project?.id) ??
    scalarId(project?.projectId) ??
    scalarId(state?.rowData) ??
    scalarId(state?.projectId);
  if (id) return { type: "project", id };
  const name = project?.name ?? project?.projectName;
  if (name) return { type: "project", name: String(name) };
  return undefined;
}

export function useSessionContext(): () => ChatSessionContext {
  const location = useLocation();
  const enteredAtRef = useRef<number>(Date.now());
  const routeHistoryRef = useRef<string[]>([location.pathname]);

  useEffect(() => {
    enteredAtRef.current = Date.now();
    const history = routeHistoryRef.current;
    if (history[history.length - 1] !== location.pathname) {
      routeHistoryRef.current = [...history, location.pathname].slice(
        -MAX_ROUTE_HISTORY
      );
    }
  }, [location.pathname]);

  return useCallback(
    () => ({
      currentRoute: location.pathname || "/",
      currentEntity: deriveCurrentEntity(location.pathname, location.state),
      timeOnPage: Math.max(
        0,
        Math.round((Date.now() - enteredAtRef.current) / 1000)
      ),
      routeHistory: routeHistoryRef.current,
    }),
    [location]
  );
}
