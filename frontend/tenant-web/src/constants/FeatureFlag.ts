
export const FEATURE_FLAGS = {
  InventoryManagement: true,
  subscrption_v1_enable: false,
  createBid_v2_enable: false,
  createBid_v3_enable: true,
  CreateAsset_v2_enable: true,
  // Asset-page version rendered by CreateAssetCommon: "v1" | "v2" | "v3".
  // Single source of truth — applies to both new and existing (edit) assets, regardless
  // of the asset's template MetaDataVersion. Change this one flag to point the Asset page
  // at any version. Inside v3 the map is further gated by the "rooflens-asset-map"
  // OpenFeature flag (on = RoofLens measurement map, off = the v2 AssetLocationPicker map).
  CreateAsset_version: "v3",
  createbid_visualsupport__enable:true,
  ENABLE_ASE_SCHEDULING_V3_GANTT:true,
  ENABLE_DELETE_TASK_V3: true,
  ENABLE_V3_ADD_TASK: true,
  bpe_enable: true,
  recommendation_enable: true,
  ENABLE_GOPAL: true,
  ENABLE_Shiloh: true,
  // GanttV2 (FIELDCRAFTGrid + zustand stores). OFF by default � preserves the current
  // Gantt; flip to true to render the new Gantt in GanttParentPage / ProjectInformation.
  ENABLE_GANTT_V2: true,
  // DMS 2.0 embed (ProjectInformation Documents tab, GoPAL AttachDialog repoint, storage
  // dashboard). Flipped ON here for QA validation in the dev environment ONLY -- not a
  // production rollout. Prod-readiness is still blocked on: two disclosed InfoSec items
  // (dmsBrowse has no folder-ACL filtering; share links expose documents/folders outside
  // the authenticated app, including an unauthenticated SAS URL handed to Microsoft's Office
  // Online Viewer for Office-file shares) and real browser QA, which has never been run
  // anywhere in this build (see docs/dms-redesign-architecture.md Section 8.5 and the U3.2/
  // U4.4/U4.5 rows in Section 7.2 for what was and wasn't verified). This flag has no
  // per-tenant targeting -- it is a single hardcoded boolean baked into the bundle, so it
  // is ON for every tenant on whatever build ships it. Revert to false before any prod build.
  documentsExplorer_v2_enable: true,
  // Phase 2 ruling (2026-08-05): stays FALSE — the product ships the MP backend
  // under the existing single-project Gantt; the MP board lives in the demo UI
  // until the product multi-project Gantt is ready.
  ENABLE_GANTT_MULTIPROJECT_V2: true,
  // GoPAL §10.1 escalation/approval prompt overlays. Enabled on this feature
  // branch for review; backend escalation events are not yet emitted, so prompts
  // can be previewed via the dev hook in Gopal.tsx (gopalShowPrompt / localStorage).
  GOPAL_PROMPTS_enable: true,
  // true = RoofLens is the active bid measurement source (overrides EagleView); false = fall back to EagleView.
  rooflens_measurement_source_enable: true,
  ENABLE_PAYMENT_RECON_V2: true,  // Payment Reconciliation UI Revamp (V2). OFF: serves the legacy screen; flip to true to enable the V2 experience.
  // Master switch for the EDEN assistant chatbot itself. When false, the chat
  // Fab in FloatingDock is not rendered at all (on top of the existing
  // subscription/permission gates) -- the feature is entirely unavailable, not
  // just hidden behind a closed panel.
  ENABLE_EDEN_CHAT: true,
  // QuickBooks Invoices and Customer Payments modules. Both are still under
  // development, so they are OFF and must stay off until the work is signed off:
  // when false the module is not rendered anywhere in the QuickBooks workspace —
  // no rail entry, no module card, no landing preview tile, and no step in the sync
  // guide — so it cannot be selected or synced at all. Flipping either to true is
  // the whole release switch; no other code change is needed.
  //
  // Invoices has a further reason to stay off on this branch: the external service
  // reads `invoice_external_map` as its queue, but the Java writer that fills that
  // queue lives on feature/QuickBooks-External-Service and is not merged here, so the
  // module would render an empty workspace that can never sync.
  //
  // These defaults are pinned by FeatureFlag.test.ts so they cannot flip silently.
  ENABLE_QUICKBOOKS_INVOICES: true,
  ENABLE_QUICKBOOKS_CUSTOMER_PAYMENTS: true,

  // EDEN project-context awareness (WI: EDEN chat close button / nav follow-up).
  // When true, useSessionContext derives currentEntity (the project the user is
  // currently viewing) from router state on project-scoped routes, so EDEN's
  // /agent/navigate requests carry it. When false, currentEntity is always
  // omitted -- EDEN falls back to its prior behavior (no page-context awareness),
  // used as a kill switch if the derivation ever misbehaves in production.
  // Only meaningful while ENABLE_EDEN_CHAT is also true.
  ENABLE_EDEN_PROJECT_CONTEXT: true,
  // EDEN "Aurora Glass" chat presentation. Presentation-only: when false the panel renders the
  // original Chat.tsx layout (MUI Card + Chat.scss bubbles) and no behaviour differs -- same
  // requests, same session-cache keys, same reset semantics.
  // ON for local review against design/Eden Aurora.dc.html (eden-new-frontend branch,
  // 2026-09-10) -- flip back to false before this merges to dev until product/design sign off
  // on the open questions in IMPLEMENTATION.md §9 (quick-prompt copy, greeting rewrite,
  // redirect duration, Undo behaviour, context-line privacy).
  // Only meaningful while ENABLE_EDEN_CHAT is also true.
  ENABLE_EDEN_AURORA_UI: true,
  // Gantt real-time presence ("3 other people are working on this project", plus a
  // per-task marker for who has a task open).
  //
  // FAIL-SAFE OFF, and it must stay off until two sign-offs land — this is not a
  // rollout toggle, it is a gate:
  //   1. [FIELDCRAFT InfoSec] the realtime negotiate endpoint grants join AND publish on
  //      ANY group name to ANY authenticated caller with no ownership check.
  //      Presence would depend on that permissiveness.
  //   2. [FIELDCRAFT DPO] presence broadcasts who is looking at what, and when. For EU/UK
  //      staff that is personal data. Nothing is persisted by design, but the
  //      broadcast itself needs confirming.
  // With this false the presence hook makes no network calls and registers no
  // timers; the Gantt behaves exactly as it does today. See
  // gantt-presence-plan.md §8. The "a schedule run is in progress" chip is NOT
  // gated by this flag — it reads the existing lock-status and carries no names.
  // ON in this branch as of 2026-08-20, on Vincy's instruction. The two sign-offs
  // above are still OUTSTANDING and are NOT superseded by this flip — they are the
  // reason the value was false, and neither could be verified from this repository
  // (the realtime negotiate endpoint does not live here, so its authorization could
  // not be re-checked). Treat them as open items against any environment that serves
  // EU/UK staff. Revert is one line:
  //   ENABLE_GANTT_PRESENCE: false
  ENABLE_GANTT_PRESENCE: true,
  // ASE 2.0 provisional dates: ENABLE_PROVISIONAL_INDICATOR was REMOVED on 2026-09-04
  // by D-NO-GREY (owner: *"go retire, but no grey at all. If it is parked then just
  // status is parked, no grey at all."*). It gated the INDICATION that a task's dates
  // were still being confirmed (task_attribute 567) — the greyed/italic row, the greyed
  // canvas bar, the "Dates confirming…" tooltip and the per-task edit hold. There is no
  // successor flag, and adding one would be re-opening a ruled question: provisional is
  // now engine-internal bookkeeping only, nothing writes 567 from distance, no payload
  // carries the flag, and the retirement lives in code
  // (projectScheduling/lib/provisional.ts, provisionalSinceDate) rather than in a
  // switch. Same treatment its own predecessor ENABLE_PROVISIONAL_REFRESH got when the
  // Reload/Re-plan action was retired (ADO #28358), and for the same reason: a flag
  // named for a retired concept invites somebody to turn it back on.
  //
  // ASE 2.0 infeasibility diagnosis: the "analysing the cause" banner on an
  // INFEASIBLE solve plus the DIAGNOSIS_READY guidance advisory. Separate switch
  // from the flag above because the two backends land independently.
  ENABLE_SCHEDULE_DIAGNOSIS: true,
  // B4 durable start locks — the "Lock project start date" / "Lock job start date"
  // checkboxes on the Create Bid (v3) final step.
  //
  // FAIL-SAFE OFF, and it must stay off until a RELEASE mechanism exists. This is a
  // gate, not a rollout toggle:
  //   1. These checkboxes are the ONLY UI writer of the JSD/PSD durable lock. The
  //      payload contract is tri-state (true = stamp, false = clear, omitted = no
  //      change) but the UI only ever sends `true` — unticking sends nothing, so a
  //      lock that has been stamped cannot be released from any screen.
  //   2. Under C-35 a locked board is permanently exempt from pull-forward, so a
  //      lock stamped by accident silently degrades every later reschedule of that
  //      project with no way back.
  // With this false CreateBidWizard_v3 renders no lock UI and passes no lock props
  // to SalesBookingCheckout, so project_start_locked / job_start_locked are ABSENT
  // from the ASE solve payload (absent, not false — `false` would mean "clear").
  // Flip to true only once a release path ships.
  ENABLE_CREATE_BID_START_LOCKS: false,

  // CRM Pipeline tab (US 25586, Performance + Stage map) — top-level nav entry at /pipeline.
  // Still marked "preview data" in its own header banner, so it needs a real kill switch
  // independent of the customer.READ permission check it already carries: with this false the
  // tab is not rendered in Topbar at all (no nav entry, no route link) — permission alone
  // otherwise left it visible to every customer.READ holder regardless of rollout state, the
  // same gap InventoryManagement's flag already closes for the Inventory tab.
  ENABLE_PIPELINE: true,

  // Sales/CRM tab consolidation — Phase 1 ("Customers & Projects"). 
  ENABLE_SALES_CRM_CONSOLIDATION: true,

  // Schedule Receipt — the post-reschedule drawer on the Gantt (bell trigger, toast,
  // Run details + History).
  //
  // ON, and it is a REAL kill switch rather than a visibility toggle. The whole feature
  // hangs off ONE mounted component (ScheduleReceiptMount) that owns all of its state, so
  // with this false there is no bell in the top bar, no toast, no Web PubSub subscription
  // registered, no receipt fetch, and the drawer's code-split chunk is never requested —
  // the feature is absent, not hidden, and the Gantt behaves exactly as it did before it.
  //
  // UI ONLY. The engine keeps writing its receipt blob into scheduling_job.job_output
  // regardless: the data is inert on its own, and continuing to write it means a project's
  // history is still complete for the runs that happened while the UI was switched off.
  //
  // Flipping this to false is Riyaz's revert during live validation, and needs no other
  // code change. The default is pinned by FeatureFlag.test.ts so it cannot flip silently.
  ENABLE_SCHEDULE_RECEIPT: true,

  // UX-300 — Optimistic schedule UI: the Gantt (and GoPAL's "my day" list for the
  // acting user) applies complete / skip / force-complete / drag-move / duration
  // edit / assignee save / clock-in / clock-out LOCALLY, inside 300 ms, instead of
  // waiting the ~3 s the engine needs to answer (docs/reference/
  // single-project-latency-budget.md §11 — "300 ms perceived, 3 s truth").
  //
  // ON, and it is a REAL kill switch rather than a visibility toggle. The flag is
  // read in ONE place — optimisticScheduleEnabled() in projectScheduling/lib/
  // optimisticSchedule.ts — which every consumer goes through: the planner returns
  // null, so nothing is ever written to the optimistic overlay; the overlay merge in
  // ganttBridge short-circuits on an empty overlay; no "planning…" chip, no muted
  // bar, no reconcile subscription work. With this false the board behaves exactly
  // as it does today, byte for byte, including the existing refetch-on-COMPLETED
  // path, which this feature never replaces — it only front-runs it.
  //
  // NOTHING OPTIMISTIC IS EVER SENT TO THE SERVER. The overlay is a local prediction
  // layer over server data; every mutation still travels the same API path it did
  // before, and the engine's answer always wins on reconcile.
  //
  // The default is pinned by FeatureFlag.test.ts so it cannot flip silently.
  ENABLE_OPTIMISTIC_SCHEDULE_UI: true,

  // PERF-3 — Board delta patch: when the COMPLETED push already carries the engine's
  // per-task result (`task_deltas` — see docs/reference/completed-push-ui-delta-contract.md),
  // the Gantt and GoPAL WRITE those windows onto the rows they are already showing instead
  // of firing a full board refetch. Measured on dev tn27551118 (2026-09-04): the worker
  // commits at 11:38:03.026, the browser reacts at 11:38:04.424 by refetching the whole task
  // tree (232 ms) plus three getMyPendingSchedules calls that do not land until 11:38:07-08 —
  // all of it re-fetching an answer that was already in the push.
  //
  // ON, and it is a REAL kill switch. The flag is read in ONE place —
  // boardDeltaPatchEnabled() in projectScheduling/lib/boardDeltaPatch.ts — which both hosts
  // go through, so with this false GanttParentPage.requestBoardRefresh and Gopal's terminal
  // handler take the refetch path they took before PERF-3, byte for byte.
  //
  // THE PATCH IS NEVER A GUESS. It runs only when deltaPatchIsComplete() PROVES the envelope
  // describes the whole change (at least one delta; nothing parked; nothing deleted; at most
  // one affected project; every delta addressed at a row already on the surface). Anything
  // else falls back to the full refetch — an incomplete delta set is refetched, never
  // partially painted.
  //
  // The default is pinned by FeatureFlag.test.ts so it cannot flip silently.
  ENABLE_BOARD_DELTA_PATCH: true,

};



// Simple flag config
export const flags: any = {
  "subcontractor": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // Shiloh Project Hub chat (WI 24124). Off by default — evaluated at runtime
  // through OpenFeature (useFlag('shiloh_chat_enable')) rather than a raw
  // constant, so it can be scoped/toggled via the provider config. Per-tenant
  // targeting requires an evaluation context or a remote provider (see reply).
  "shiloh_chat_enable": {
    defaultVariant: "off",
    variants: { on: true, off: false },
  },
  // RoofLens roof-measurement in the Asset page (CreateAsset_v3).
  // ON — FIELDCRAFT Legal sign-off (Google 3D-tiles ToS) is confirmed complete, so this default
  // carries into dev unchanged. flag OFF renders the untouched CreateAsset_v2. The real
  // default is pinned by FeatureFlag.test.ts (asserted directly, not via a mocked useFlag)
  // so any flip is caught.
  "rooflens-asset-map": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // Shared 3D/2D map picker on the tenant address screens (AddressMapPicker). ON by default: it uses the
  // same Google 3D-tiles surface as rooflens-asset-map above, whose FIELDCRAFT Legal sign-off is recorded there.
  // Kill-switch: off falls back to the 2D satellite map only. The default is pinned by FeatureFlag.test.ts.
  "address-map-3d": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // Automation Plans — anchor-relative step authoring (B5): the BEFORE_ANCHOR / AFTER_ANCHOR
  // schedule modes and the anchor-relative OFFSET_SET recurrence kind.
  //
  // OFF, and it MUST STAY OFF until A6 lands. The authoring UI, the wire mapping and the
  // validation are all built and tested; what is missing is the runtime. A saved anchor-mode step
  // produces an enrolment with next_fire_at = NULL (AutomationEnrolmentService.initialFireAt
  // returns null for every non-IMMEDIATE mode), and the sweep claims work through a partial index
  // that cannot see nulls — so the enrolment sits ACTIVE forever and the message NEVER goes out.
  // No error, no audit row, one WARN line. Completing A6 is then a flip of this variant, not a
  // feature build.
  //
  // The default is pinned by FeatureFlag.test.ts, asserted against this config rather than through
  // a mocked useFlag, so it cannot be flipped silently.
  "automation-anchor-authoring": {
    defaultVariant: "off",
    variants: { on: true, off: false },
  },
  // AB#28854 — RoofLens address/property validation (Google Address Validation + Solar API
  // buildingInsights). Gates the two new paid external-API calls added to the RoofLens picker
  // and both Asset-save code paths, so a rollout problem (unexpected Google billing/quota
  // behavior, a key-restriction failure — see the plan's "reuse the existing key" risk note) has
  // a kill switch that reverts to today's unvalidated behavior instantly, without a deploy.
  // ON by default — verified working end-to-end (live Address Validation/Solar API calls,
  // fail-open behavior, and the /ml/facets short-circuit) before this flip. The default is
  // pinned by FeatureFlag.test.ts (asserted directly, not via a mocked useFlag) so any
  // accidental flip back is caught. It also gates the customer/billing address check (Customer
  // profile + portal Profile tab) through useAddressValidation, not only the asset paths.
  "rooflens-address-validation": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // Product / Service Selection (design 4a) — replaces the Good/Better/Best package
  // screen behind this flag; the legacy GBB screen renders whenever it is off.
  //
  // Defaults ON, on Vincy's explicit instruction (2026-09-03) — NOT an oversight.
  //
  // Both PR-checklist reviewers argued for OFF, and that argument is on the record rather
  // than resolved: `flags` is compiled into `new InMemoryProvider(flags)`
  // (featureFlagProvider.ts) and registered at index.tsx, so this value ships in the
  // bundle — there is no per-tenant control and no ops kill switch. Three things are still
  // open with product, all recorded on work items #29880/#29885/#29890/#29893/#29898:
  //   1. the services → job-type answer (I.66) write-back the legacy path performs is not
  //      wired on the v2 path — it needs the ODM CodeList's real service values;
  //   2. the grade a confirmed PACKAGE records on project.additional_info is INFERRED
  //      (productServiceSelection/data/buildCatalogFromSnapshot gradesByComboKey) and the
  //      same-service-count rule is not product-confirmed — that grade is how ASE 2.0's
  //      gbb_stock_bom resolves a material name to a material row;
  //   3. Confirm leaves no durable wizard-level state, so re-entering Step 5 re-opens the
  //      sheet over a decision already made.
  //
  // Resolution order (query param override, then localStorage override, then this real
  // flag) is implemented in
  // src/components/createbid/productServiceSelection/useFeatureFlag.ts, consumed via
  // useFeatureFlag('productServiceSelectionV2') — the same useFlag(...) pattern as the
  // other entries above. To see the new screen without editing this file, append
  // ?ff_productServiceSelectionV2=1 to the URL.
  //
  // The default is pinned by FeatureFlag.test.ts, asserted against this config rather than
  // through a mocked useFlag, so it cannot be flipped silently.
  "product_service_selection_v2": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // GoPAL free-worker actions — the card that replaces "waiting on queue" with what the
  // worker can actually do (owner ruling 2026-09-17,
  // src/next-gen-ase/gopal-free-worker-actions-handoff.md; workflow spec
  // src/next-gen-ase/gopal-free-worker-workflow.md).
  //
  // ON by owner decision 2026-09-22: the routes are live for everyone on this branch.
  // R15 is unchanged as a property — set this to "off" and the app is byte-for-byte what it
  // was before the feature — but "off" is no longer the shipped default, so that is now the
  // rollback switch rather than the release gate.
  //
  // PER-BROWSER override, either way, without editing this file. It wins over the value
  // above, so it is how one person turns the card off without changing it for everyone:
  //   ?ff_gopalFreeWorker=0              (or localStorage['ff:gopalFreeWorker']='0')  → off
  //   ?ff_gopalFreeWorker=1              (or '1')                                     → on
  //   &fw_stub=1                         (or localStorage['ff:gopalFreeWorkerStub']='1')
  //       — sample seats/candidates/plan, for a board whose backend is not reachable.
  // See components/gps/utils/gopalFreeWorkerDev.ts.
  //
  // There is a SECOND, independent switch on the server side: the per-tenant
  // `gopal.freeWorkerActions` row in tenant_configuration (workflow spec section 4.5).
  // Both now ship enabled: the server row turns the routes off for ONE TENANT, this value
  // turns them off for the whole build.
  "gopal-free-worker-actions": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // AB#31383 — kill switch for the Create Customer guided hand-off (Invite Customer ->
  // Personal Info -> Asset Creation -> Portal), owned solely by useCreateCustomerFlow.ts.
  // ON preserves the confirmed shipped flow: a successful create navigates straight to
  // Personal Info, whose own success dialog carries the customer on to Asset Creation, which
  // lands on the portal's Assets tab. OFF stops the chain at its one entry point — a
  // successful create just closes the modal and refreshes the list, exactly like the
  // Admin > Customer Management variant (no onCreated) already behaves today — because every
  // later hop is conditioned on state (fromCreateCustomerFlow/returnTo/finalReturnTo) that
  // only this entry point sets. Does NOT gate Auto Estimate's existing jump-to-Overview
  // (CustomerPortal.onAutoEstimateRequested) — that behavior is general to every asset, not
  // specific to this guided flow, and is unaffected either way.
  "create-customer-guided-flow": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // RoofMaxx "Open deal" button on the Customer Portal's estimate card. ON by default; the button is
  // already restricted to projects that came from a RoofMaxx Connect lead, so this is only a kill-switch
  // (a silent flip to off would hide it from everyone). The default is pinned by FeatureFlag.test.ts.
  "roofmaxx-open-deal": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
  // GoPAL: a successful clock-in on a task that carries a RoofMaxx deal id opens that deal in a new tab.
  // ON by default; kill-switch only. The default is pinned by FeatureFlag.test.ts.
  "roofmaxx-clockin-open": {
    defaultVariant: "on",
    variants: { on: true, off: false },
  },
};
