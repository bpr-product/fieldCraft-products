# FieldCraft — Selected UI, Integrations, ETL & Eden Source

This repository is a **focused extraction of existing application source code**, not a rewritten mock application. It preserves original implementation patterns for **QuickBooks, ABC Supply, EagleView, ETL, and the Eden AI assistant**. Unrelated product features, core tenant workflows, deployment infrastructure and original production configuration have been removed.

## What's included

| Requested feature | Existing UI source | Existing backend source |
| --- | --- | --- |
| QuickBooks | `frontend/tenant-web/src/pages/AdminTemplates/integrations/QuickBooks/` and `src/services/integrations/QuickBooksService.ts` | `backend/external-services/source/quickbooks/`, QBO API/callbacks, relevant Python repositories and Java QuickBooks/ExternalMap classes |
| ABC Supply | `frontend/tenant-web/src/pages/AdminTemplates/integrations/ABCSupply/` and `src/services/integrations/ABCSupplyService.ts` | `backend/external-services/source/api/ABCSupplyAPI.py`, service, import/UOM mapping and repository classes |
| EagleView | `frontend/tenant-web/src/services/MeasurementService.ts`, existing asset/estimate measurement UI and support components | `backend/external-services/source/service/EagleviewService.py`, `externalService/MeasurementServices.py`, `api/MeasurementServiceAPI.py`, and selected Java service-request classes |
| ETL / data pipeline | No separately identified ETL UI in the provided tenant web source | `data/etl-pipeline/source/pipeline/` (bronze, silver, gold, ingestion and supporting source), related tests and Java ETL classes |
| Eden AI assistant | `frontend/tenant-web/src/pages/Chatbot/`, `src/services/AIChatService.ts`, `ChatApiService.ts`, and `ChatBotService.ts` | `ai/eden/source/` agent, orchestration, routing, context, memory, model clients, APIs and tests |

## What this is and isn't

- Source files are **copied from the previously sanitized application migration**, not replaced with samples. The main deliberate code changes are in `frontend/tenant-web/src/auth/OidcConfig.ts` (service URLs and login configuration) and `backend/external-services/source/api/MeasurementServiceAPI.py` (removing unrelated provider registrations).
- The selected React screens and shared helpers retain the original code and relative file hierarchy. The full React `App.tsx`, unrelated application shell/routes, backend platform modules, tenancy layer, and full Java dependencies were not copied. **This extraction is not a standalone replacement for the complete application.** A full end-to-end run requires a host application or integration work.
- The Python external-service gateway retains the three requested provider integration routes; actual provider calls still need your own configured test credentials and environment.
- Eden and ETL retain their substantial original source projects. Starting them requires your own database, model services, message brokers, authorization and infrastructure, not the removed original deployments.
- Java files are a selected reference slice for these features, **not a separately compilable Java multi-module build**.
- All real customer data, deployment-only material and obvious secret files were excluded; source-only heuristic scanning does **not** guarantee the absence of personal or confidential content. Review source and rights/licenses manually before making this public.

## Source navigation

- [Feature-by-feature map](docs/FEATURE_MAP.md)
- [Build/verification limitations](reports/BUILD_VERIFICATION.md)
- [Security publishing review](reports/SECURITY_REVIEW.md)
- [Source manifest](reports/SOURCE_MANIFEST.json)

## Development notes

The original frontend dependency manifests are retained under `frontend/tenant-web/`. Its source can be integrated back into a compatible React shell; the standalone `npm run build` is **not** promised to succeed from this reduced extraction. Import dependency closure was followed for the selected UI feature entry points, preserving imported feature modules and styles.

For the external integration server, run from `backend/external-services/` after providing your **own** environment and dependencies:

```bash
python -m pip install -r requirements.txt
python main.py
```

The gateway defaults to port 5001 and exposes route documentation from FastAPI. Be aware that real startup may require databases, authentication and configured provider clients.

Eden requires its own setup from `ai/eden/`:

```bash
python -m pip install -r requirements.txt
python main.py
```

ETL is a distributed data-processing service, not a one-command local demo. See the existing sources under `data/etl-pipeline/` and its original requirements.

## Ownership and publication

This is a source migration prepared on the user's stated authorization to publish. Publisher must independently confirm authorization, legal notices, internal hostnames, customer data and secrets before making a public release. The selected source is published for engineering review. This focused extraction does not include the full original application shell or deployment infrastructure.