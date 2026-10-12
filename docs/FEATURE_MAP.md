# Feature map — real source locations

## QuickBooks
- Original React sync/connect/status/components: `frontend/tenant-web/src/pages/AdminTemplates/integrations/QuickBooks/`
- OAuth callback: `frontend/tenant-web/src/pages/QuickBooksCallback.tsx`
- API client: `frontend/tenant-web/src/services/integrations/QuickBooksService.ts`
- FastAPI QBO endpoints: `backend/external-services/source/api/ImportExportSyncAPI.py`, `quickbooksCallbackAPI.py`
- QBO domain operations: `backend/external-services/source/quickbooks/`
- Repositories: `backend/external-services/source/dal/QuickBooksRepository.py` and related DAL
- Java credential sync, external mapping: selected files under `backend/java-platform/tenant/`

## ABC Supply
- React material import and account UI: `frontend/tenant-web/src/pages/AdminTemplates/integrations/ABCSupply/`
- API client: `frontend/tenant-web/src/services/integrations/ABCSupplyService.ts`
- Python endpoints: `backend/external-services/source/api/ABCSupplyAPI.py`
- Provider client: `backend/external-services/source/service/ABCSupplyService.py`
- Import to tenant: `backend/external-services/source/service/ABCSupplyFIELDCRAFTImportService.py`
- UOM maps and DAL: `backend/external-services/source/service/ABCSupplyUomMappingConfig.py`, `source/dal/ABCSupplyRepository.py`

## EagleView / Measurements
- React measurement request service: `frontend/tenant-web/src/services/MeasurementService.ts`
- Asset/estimate UI: `frontend/tenant-web/src/components/asset/`, `components/createbid/requirements/`
- External API / service: `backend/external-services/source/api/MeasurementServiceAPI.py`, `source/service/EagleviewService.py`, `source/externalService/MeasurementServices.py`
- Java request endpoints: selected files under `backend/java-platform/tenant/`

## ETL
- Existing data pipeline: `data/etl-pipeline/source/pipeline/`, including ingestion, bronze, silver, gold, shared and related tests
- Java ETL bridge: `backend/java-platform/tenant/fieldcraft-tenant-services/src/main/java/com/fieldcraft/tenant/service/ETLService.java`

## Eden AI Assistant
- Existing React chat UI: `frontend/tenant-web/src/pages/Chatbot/`
- React API/service clients: `frontend/tenant-web/src/services/AIChatService.ts`, `ChatApiService.ts`, `ChatBotService.ts`
- Existing Python agent runtime: `ai/eden/source/agents/`, `source/orchestrator/`, `source/resolvers/`, `source/utils/`, `source/api/`
- Relevant ETL retrieval documents: `data/etl-pipeline/source/pipeline/gold/`

