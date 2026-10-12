export const gpsTaskTypes = ["Manual Task", "Create BOM Task", "Approve PO Task", "Confirm PO Task", "Receive Task",
    "Remove Task", "Install Task", "Verify Task", "Thank You Card Task", "Approve Contract Task", "Create Estimate Task",
    "Payment Verification Task", "Receive PO Task", "Dispatch PO Task", "Return PO Task", 'Verify PO Task', 'Approve Invoice Task', "Miscellaneous Service", "Customer Task",
    "Create PO Task"
]

export const actionableTasks = ["Approve Contract Task", "Approve Invoice Task", "Approve PO Task", "Payment Verification Task", "Approve Invoice Task"]

// "Return PO Task" is deliberately absent from every PO group below: these lists are the PO
// delete/completion GROUP, so deleting one member resolves and deletes the rest. Return-to-stock
// sits in postConstruction and is reached through the BOM tab, not through the purchase order.
// "Dispatch PO Task" IS a member -- it is the same activeConstruction step as "Receive PO Task".
export const poTaskGroup:any = ["Create PO Task", "Create BOM Task",  "Approve PO Task", "Confirm PO Task", 
    "Receive PO Task", "Dispatch PO Task", "Verify PO Task", 
]

export const miscTaskGroup = ["Create PO Task",  "Approve PO Task", "Confirm PO Task"];

export const poTaskGroupwithOutBom = ["Approve PO Task", "Confirm PO Task", "Create PO Task",
    "Receive PO Task", "Dispatch PO Task", "Verify PO Task"
]

export const invoiceTaskGroup = ["Create Invoice Task", "Approve Invoice Task", 
    // "Payment Task", "Remainder Invoice Task", 
    "Payment Verification Task"
]

export const APPROVE_CONTRACT_TASK = "Approve Contract Task";

export const APPROVE_INVOICE_TASK = "Approve Invoice Task";

export const APPROVE_PO_TASK = "Approve PO Task";

export const PAYMENT_VERIFICATION_TASK = "Payment Verification Task";

export const CREATE_BOM_TASK = "Create BOM Task";

export const MISCELLANEOUS_SERVICE = "Miscellaneous Service";
export const ACTIVECONSTRUCTION = "activeconstruction";
export const SALES = "sales";
export const PRECONSTRUCTION = "preconstruction";
export const SUBCONTRACTOR_TASK = "Subcontractor Task";
export const INSTALL_TASK = "Install Task";
export const REMOVE_TASK = "Remove Task";
export const VERIFY_TASK = "Verify Task";
export const ALLOC_VALUE_MAX_LENGTH = 4;
export const ALLOC_VALUE_HELPER_TEXT = "Allocated units must be greater than 0.";
export const DURATION_FETCH_ERROR_TEXT = "Couldn't calculate Duration. Please re-select Start/End or enter it manually.";

// Task types that must run through their own workflow — they may not be force-completed
// or skipped from any surface (Gantt V2 toolbar + bulk bar + per-row skip icon, GoPAL).
// AB#28644 follow-up: "Create Invoice Task", "Payment Task" and "Create PO Task" added —
// each one OPENS a workflow the later steps depend on (the invoice, the payment record,
// the purchase order), so short-circuiting it leaves the chain behind it unreachable.
// Their absence was an omission, not a decision: every other stage of those same three
// chains was already here.
export const mandatoryTaskList = ["Create BOM Task", "Create Estimate Task", "Approve Contract Task", "Approve Invoice Task",
    "Payment Verification Task", "Approve PO Task", "Confirm PO Task", "Receive PO Task", "Dispatch PO Task", "Return PO Task", "Verify PO Task", "Thank You Card Task", "Miscellaneous Service", "Customer Task",
    "Create Invoice Task", "Payment Task", "Create PO Task"
]

// Task types no user may delete on any surface (legacy Gantt V1, BidTaskList, TaskList,
// and — since AB#28644 — Gantt V2 via isTaskTypeDeleteProtected). These are generated and
// owned by the billing/contract workflow, not user work.
// AB#28644 follow-up: "Create Estimate Task" and "Approve Contract Task" added — both are
// contract-lifecycle steps the project's own status transitions depend on.
export const nonDeletableTasks = [
  "Payment Task",
  "Approve Invoice Task", "Create Invoice Task",
  "Customer Task", "Payment Verification Task",
  "Create Estimate Task", "Approve Contract Task"
];

// Deletable, but never by accident: the reminder/remainder chasers. Ruled deletable by the
// product owner (they are follow-ups, not workflow steps), with a named confirmation so a
// bulk delete cannot sweep them up silently — "your selection contains these, are you sure?"
//
// A SEPARATE list rather than a flag on nonDeletableTasks, because the two mean opposite
// things to every caller: nonDeletableTasks DISABLES the control, this one only decorates
// the confirmation. Folding them together would disable delete again on the legacy grids.
//
// NOTE FOR THE LEGACY SURFACES: these three were previously in nonDeletableTasks, so
// treegridGantt / BidTaskList / TaskList disabled their delete. They now permit it and
// have no wiring for this warning — flagged to the owner, not silently accepted.
export const deleteWarningTasks = [
  "Remainder Invoice Task",
  "Reminder Invoice Task",
  "Remainder Customer Task"
];

export const NON_RESOURCE_BASED = ["Create PO Task", "Create Invoice Task", "Remainder Invoice Task", "Remainder Customer Task", "Payment Task", "Customer Task","Subcontractor Task"];

export const TASKTYPEGROUP = ["bidReview","awaitingEstimation","estimationApproval"];

export const STOCK_BOM_DELETE_GROUP = ["Create PO Task","Approve PO Task","Confirm PO Task"];

export const STOCK_BOM_GROUP = ["Create BOM Task","Receive PO Task","Dispatch PO Task","Verify PO Task"];


export const CONFIRM_PO_TASK = "Confirm PO Task"

export const RECEIVE_PO_TASK = "Receive PO Task"

export const DISPATCH_PO_TASK = "Dispatch PO Task"

export const RETURN_PO_TASK = "Return PO Task"

// AB#32124 — the task types whose GoPAL card swaps its action to "Complete" once the
// material step has actually been done. Listed here rather than in the widget so the
// card's own branches and the BOM page's announcement agree on one definition; a fourth
// material task type added later swaps correctly by being added once, here.
export const MATERIAL_ACTION_TASK_TYPES: string[] = [
  RECEIVE_PO_TASK,
  DISPATCH_PO_TASK,
  RETURN_PO_TASK,
]

export const VERIFY_PO_TASK = "Verify PO Task"

export const CREATE_PO_TASK = "Create PO Task"

export const VENDOR = "Vendor";

export const STOCK = "Stock";

export const BOM_GROUP_TASKS = [
  CREATE_PO_TASK,
  APPROVE_PO_TASK,
  CONFIRM_PO_TASK,
  RECEIVE_PO_TASK,
  DISPATCH_PO_TASK,
  VERIFY_PO_TASK,
];

export const BOM_TYPES = {
  VENDOR: "VENDOR",
  STOCK: "STOCK",
  MIXED: "MIXED",
} as const

export const SKIPPED = "skipped";

export const COMPLETED = "completed";

export const FORCE_COMPLETED = "FORCE_COMPLETED";

export const BLOCKED = "BLOCKED";

export const ACTIVE = "active";

export const nonCompletableTasks = ["Create BOM Task", "Create Estimate Task", "Approve Contract Task", "Approve Invoice Task",
    "Payment Verification Task", "Approve PO Task", "Confirm PO Task", "Receive PO Task", "Dispatch PO Task", "Return PO Task", "Verify PO Task", "Customer Task", "Payment Task", "Create Invoice Task",
    "Create PO Task"
]

export const GPS_TASK_DELETED = "gps.task.deleted";

export const ALLOCATED_UNIT_TYPES = ["Install Task", "Remove Task"];

export const FORCE_COMPLETE = "FORCE_COMPLETE";

export const QUERY_TYPES = [INSTALL_TASK, REMOVE_TASK, VERIFY_TASK] as const;