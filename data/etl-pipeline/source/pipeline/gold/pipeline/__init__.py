"""Gold pipeline orchestration modules.

Submodules:
    tenant_discovery  — discover active tenants from Silver PostgreSQL
    step_registry     — build ordered loader steps per tenant
    dq_gate           — post-load data quality assertions
    tenant_runner     — run all steps for a single tenant
    pipeline_runner   — loop over all tenants, emit metrics
"""
