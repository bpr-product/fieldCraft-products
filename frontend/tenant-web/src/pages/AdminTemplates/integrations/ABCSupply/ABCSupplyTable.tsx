/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose : ABC Supply material catalog search — Admin Management tab.
*           Search ABC materials, review account pricing,
*           and add selected materials into the material list.
*/
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import abcSupplyLogo from "../../../../assets/imgs/Abc supply.jpg";
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  FormControlLabel,
  IconButton,
  InputAdornment,
  InputBase,
  MenuItem,
  Paper,
  Select,
  Skeleton,
  Snackbar,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  type SelectChangeEvent,
} from "@mui/material";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import Inventory2RoundedIcon from "@mui/icons-material/Inventory2Rounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import LocalOfferRoundedIcon from "@mui/icons-material/LocalOfferRounded";
import BusinessRoundedIcon from "@mui/icons-material/BusinessRounded";
import StraightenRoundedIcon from "@mui/icons-material/StraightenRounded";
import CategoryRoundedIcon from "@mui/icons-material/CategoryRounded";
import StorefrontRoundedIcon from "@mui/icons-material/StorefrontRounded";
import QrCode2RoundedIcon from "@mui/icons-material/QrCode2Rounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded";
import PlaylistAddCheckRoundedIcon from "@mui/icons-material/PlaylistAddCheckRounded";
import LayersRoundedIcon from "@mui/icons-material/LayersRounded";
import OpenInNewRoundedIcon from "@mui/icons-material/OpenInNewRounded";
import DoneAllRoundedIcon from "@mui/icons-material/DoneAllRounded";
import { getAllVendors } from "../../../../services/VendorService";
import {
  searchABCSupplyItems,
  getABCSupplyItem,
  getABCSupplyConnectionStatus,
  priceABCSupplyItems,
  importSelectedABCSupplyMaterialsToFIELDCRAFT,
  getImportedABCSupplyMaterials,
  getValidTradeNames,
  type ABCSearchRequest,
  type ABCSupplyConnectionStatus,
  type ABCSupplyImportedMaterial,
} from "../../../../services/integrations/ABCSupplyService";
import "./ABCSupplyTable.scss";

interface ABCItem {
  itemNumber?: string;
  sku?: string;
  externalId?: string;
  itemDescription?: string;
  description?: string;
  name?: string;
  manufacturerName?: string;
  manufacturer?: string;
  brand?: string;
  manufacturerPartNumber?: string;
  categoryName?: string;
  category?: string;
  productType?: string;
  sellingUnit?: string;
  uom?: string;
  unitOfMeasure?: string;
  listPrice?: number;
  price?: number;
  available?: boolean | string | number;
  branchAvailability?: string;
  branches?: any[];
  [key: string]: any;
}

type SearchErrorKind = "not_configured" | "api_error" | "network_error";

interface SearchError {
  kind: SearchErrorKind;
  message: string;
}

interface PriceResult {
  itemNumber: string;
  netPrice?: number;
  listPrice?: number;
  branchPrice?: number;
  price?: number;
  uom?: string;
  currency?: string;
  fetchedAt: string;
  raw?: Record<string, any>;
}

interface ToastState {
  message: string;
  severity: "success" | "error" | "info" | "warning";
}

interface ImportResultCounts {
  fetched?: number;
  prepared?: number;
  created?: number;
  skippedExisting?: number;
  skippedMissingPrice?: number;
  skippedInvalid?: number;
  linkedExisting?: number;
  failed?: number;
  priceErrors?: number;
}

interface ImportResult {
  ok?: boolean;
  counts?: ImportResultCounts;
  created?: Array<Record<string, any>>;
  skipped?: Array<Record<string, any>>;
  failed?: Array<Record<string, any>>;
  linkedExisting?: Array<Record<string, any>>;
  priceErrors?: Array<Record<string, any>>;
  materials?: Array<Record<string, any>>;
}


const getSkippedImportCount = (counts?: ImportResultCounts): number =>
  (counts?.skippedExisting ?? 0) +
  (counts?.skippedMissingPrice ?? 0) +
  (counts?.skippedInvalid ?? 0);

const getImportCreatedCount = (counts?: ImportResultCounts): number => counts?.created ?? 0;

const buildImportSummaryText = (counts?: ImportResultCounts): string => {
  const created = getImportCreatedCount(counts);
  const linked = counts?.linkedExisting ?? 0;
  const skipped = getSkippedImportCount(counts);
  const failed = counts?.failed ?? 0;

  const parts: string[] = [];
  if (created > 0) parts.push(`${created} added`);
  if (linked > 0) parts.push(`${linked} linked to an existing material`);
  if (skipped > 0) parts.push(`${skipped} skipped`);
  if (failed > 0) parts.push(`${failed} failed`);

  return parts.length ? parts.join(", ") + "." : "No materials were added.";
};

const getImportRecordItemNumber = (record: Record<string, any>): string =>
  record?.itemNumber || record?.itemCode || record?.sku || "Unknown item";

const getImportRecordName = (record: Record<string, any>): string =>
  record?.name || record?.description || record?.itemDescription || "";

const getImportRecordTitle = (record: Record<string, any>): string => {
  const itemNumber = getImportRecordItemNumber(record);
  const name = getImportRecordName(record);
  return name ? `${itemNumber} - ${name}` : String(itemNumber);
};

const getImportRecordReason = (record: Record<string, any>): string =>
  record?.reason || "No reason returned.";

const REASON_CODE_SEVERITY: Record<string, "warning" | "info" | "error"> = {
  MISSING_PRICING: "warning",
  ABC_UOM_MISSING: "warning",
  UOM_MAPPING_MISSING: "warning",
  FIELDCRAFT_UOM_MISSING: "warning",
  FIELDCRAFT_UOM_VALIDATION_UNAVAILABLE: "warning",
  ALREADY_IMPORTED: "info",
  DUPLICATE_MATERIAL: "info",
  INVALID_MATERIAL: "error",
};

const getImportRecordSeverity = (record: Record<string, any>): "warning" | "info" | "error" => {
  const reasonCode = record?.reasonCode ? String(record.reasonCode) : "";
  if (reasonCode && REASON_CODE_SEVERITY[reasonCode]) return REASON_CODE_SEVERITY[reasonCode];
  // Records with a reason but no known reasonCode come from hard failures (e.g. Tenant API errors).
  return record?.reason ? "error" : "info";
};

interface VendorOption {
  id?: number | string;
  vendorId?: number | string;
  companyName?: string;
  name?: string;
  displayName?: string;
  [key: string]: any;
}

interface TradeOption {
  id?: number | string;
  tradeId?: number | string;
  tradeName?: string;
  trade_name?: string;
  trade?: string;
  name?: string;
  label?: string;
  value?: string;
  [key: string]: any;
}

type ResponseKey = "search" | "pricing" | "detail" | "import";

interface ResponseSnapshot {
  key: ResponseKey;
  title: string;
  message: string;
  payload: any;
  capturedAt: string;
}

const PAGE_SIZE_OPTIONS = [10, 20, 50];
const LIBRARY_PAGE_SIZE_OPTIONS = [20, 50];

type RequiredBooleanValue = "" | "true" | "false";

const toRequiredBoolean = (value: RequiredBooleanValue): boolean => value === "true";

const getItemNumber = (item: ABCItem): string =>
  item?.itemNumber ?? item?.sku ?? item?.externalId ?? "";

const getDescription = (item: ABCItem): string =>
  item?.itemDescription ?? item?.description ?? item?.name ?? "";

const getManufacturer = (item: ABCItem): string =>
  item?.manufacturerName ?? item?.manufacturer ?? item?.brand ?? "";

const getCategory = (item: ABCItem): string =>
  item?.categoryName ?? item?.category ?? item?.productType ?? "";

const getUom = (item: ABCItem): string =>
  item?.sellingUnit ?? item?.uom ?? item?.unitOfMeasure ?? "";

const getVendorId = (vendor?: VendorOption | null): string => {
  const value = vendor?.id ?? vendor?.vendorId ?? "";
  return value === undefined || value === null ? "" : String(value);
};

const getVendorName = (vendor?: VendorOption | null): string =>
  vendor?.companyName ?? vendor?.displayName ?? vendor?.name ?? "";

const getTradeName = (trade?: TradeOption | string | null): string => {
  if (!trade) return "";
  if (typeof trade === "string") return trade;
  const value =
    trade.tradeName ??
    trade.trade_name ??
    trade.trade ??
    trade.name ??
    trade.label ??
    trade.value ??
    "";
  return value === undefined || value === null ? "" : String(value);
};

const extractTradeList = (payload: any): TradeOption[] => {
  const raw =
    payload?.data?.trades ??
    payload?.data?.tradeNames ??
    payload?.data?.validTradeNames ??
    payload?.data ??
    payload?.trades ??
    payload?.tradeNames ??
    payload?.validTradeNames ??
    payload;

  if (!Array.isArray(raw)) return [];

  return raw
    .map((trade) => (typeof trade === "string" ? { tradeName: trade } : trade))
    .filter((trade) => getTradeName(trade));
};

const formatPrice = (value?: number): string => {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(Number(value));
};

const getBestPrice = (price?: PriceResult | null, item?: ABCItem | null): number | undefined =>
  price?.netPrice ?? price?.branchPrice ?? price?.price ?? item?.price ?? item?.listPrice;

const getPriceLineNumber = (line: any): string =>
  String(line?.itemNumber ?? line?.item_number ?? line?.sku ?? line?.materialNumber ?? "");

const normalizePriceLine = (line: any): PriceResult | null => {
  const itemNumber = getPriceLineNumber(line);
  if (!itemNumber) return null;
  return {
    itemNumber,
    netPrice: line?.netPrice ?? line?.customerPrice ?? line?.net_price,
    listPrice: line?.listPrice ?? line?.list_price,
    branchPrice: line?.branchPrice ?? line?.branch_price,
    price: line?.price ?? line?.unitPrice ?? line?.unit_price,
    uom: line?.uom ?? line?.unitOfMeasure ?? line?.unit_of_measure,
    currency: line?.currency ?? "USD",
    fetchedAt: new Date().toLocaleTimeString(),
    raw: line,
  };
};

const extractItems = (payload: any): ABCItem[] =>
  payload?.items ?? payload?.results ?? payload?.data?.items ?? (Array.isArray(payload) ? payload : []);

const extractTotal = (payload: any, fallback: number): number =>
  payload?.pagination?.totalItems ?? payload?.total ?? payload?.totalCount ?? fallback;

const extractPriceLines = (payload: any): any[] =>
  payload?.lines ?? payload?.data?.lines ?? payload?.prices ?? payload?.data?.prices ?? [];

interface ABCSupplyTableProps {
  tenantId?: string;
  hideHeader?: boolean;
  connectionStatus?: ABCSupplyConnectionStatus;
}

const ABCSupplyTable: React.FC<ABCSupplyTableProps> = ({ tenantId, hideHeader, connectionStatus }) => {
  const { accessToken } = useOidcAccessToken();

  const [query, setQuery] = useState<string>("");
  const [itemNumberQuery, setItemNumberQuery] = useState<string>("");
  const [branchNumber, setBranchNumber] = useState<string>("");
  const [shipToNumber, setShipToNumber] = useState<string>("");
  const [connectionDefaults, setConnectionDefaults] = useState<ABCSupplyConnectionStatus | null>(
    connectionStatus ?? null
  );
  const [pageSize, setPageSize] = useState<number>(20);
  const [page, setPage] = useState<number>(1);
  const [includeFamilyItems, setIncludeFamilyItems] = useState<boolean>(false);

  const [items, setItems] = useState<ABCItem[]>([]);
  const [totalItems, setTotalItems] = useState<number>(0);
  const [hasSearched, setHasSearched] = useState<boolean>(false);
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<SearchError | null>(null);
  const [lastSearchQuery, setLastSearchQuery] = useState<string>("");

  const [selectedItemNumbers, setSelectedItemNumbers] = useState<Set<string>>(new Set());
  const [vendorList, setVendorList] = useState<VendorOption[]>([]);
  const [selectedVendor, setSelectedVendor] = useState<VendorOption | null>(null);
  const [isLoadingVendors, setIsLoadingVendors] = useState<boolean>(true);
  const [vendorError, setVendorError] = useState<string | null>(null);
  const [tradeList, setTradeList] = useState<TradeOption[]>([]);
  const [selectedTrade, setSelectedTrade] = useState<TradeOption | null>(null);
  const [selectedInStock, setSelectedInStock] = useState<RequiredBooleanValue>("");
  const [selectedAllowDecimal, setSelectedAllowDecimal] = useState<RequiredBooleanValue>("");
  const [isLoadingTrades, setIsLoadingTrades] = useState<boolean>(true);
  const [tradeError, setTradeError] = useState<string | null>(null);
  const [isImportingToFIELDCRAFT, setIsImportingToFIELDCRAFT] = useState<boolean>(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importedMaterialMap, setImportedMaterialMap] = useState<Record<string, ABCSupplyImportedMaterial>>({});
  const [isLoadingImportedStatus, setIsLoadingImportedStatus] = useState<boolean>(false);
  const [addedMaterials, setAddedMaterials] = useState<ABCSupplyImportedMaterial[]>([]);
  const [addedMaterialsPage, setAddedMaterialsPage] = useState<number>(1);
  const [addedMaterialsPageSize, setAddedMaterialsPageSize] = useState<number>(20);
  const [addedMaterialsTotal, setAddedMaterialsTotal] = useState<number>(0);
  const [isLoadingAddedMaterials, setIsLoadingAddedMaterials] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<'search' | 'library'>('search');
  const [libraryFilter, setLibraryFilter] = useState<string>('');
  const [appliedLibraryFilter, setAppliedLibraryFilter] = useState<string>('');
  const [importDetailPanel, setImportDetailPanel] = useState<'added' | 'linked' | 'skipped' | null>(null);

  const [priceMap, setPriceMap] = useState<Record<string, PriceResult>>({});
  const [isFetchingTablePrices, setIsFetchingTablePrices] = useState<boolean>(false);
  const [priceMessage, setPriceMessage] = useState<string | null>(null);

  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);
  const [selectedItem, setSelectedItem] = useState<ABCItem | null>(null);
  const [fullDetail, setFullDetail] = useState<any>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState<boolean>(false);

  const [responses, setResponses] = useState<Partial<Record<ResponseKey, ResponseSnapshot>>>({});
  const [latestResponseKey, setLatestResponseKey] = useState<ResponseKey | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  const searchInputRef = useRef<HTMLInputElement>(null);
  const vendorListboxRef = useRef<HTMLUListElement>(null);
  const tradeListboxRef = useRef<HTMLUListElement>(null);
  // Guards against out-of-order My Materials responses: rapid page/filter/page-size changes can
  // fire overlapping requests, and a stale one resolving after a newer one must not clobber it.
  const addedMaterialsRequestIdRef = useRef(0);
  const resetListboxScroll = (ref: React.RefObject<HTMLUListElement>) => () => {
    if (ref.current) ref.current.scrollTop = 0;
  };

  const fieldcraftVendorId = getVendorId(selectedVendor);
  const fieldcraftVendorName = getVendorName(selectedVendor);
  const selectedTradeName = getTradeName(selectedTrade);
  const hasInStockSelection = selectedInStock !== "";
  const hasAllowDecimalSelection = selectedAllowDecimal !== "";
  const isQueryActive = Boolean(query.trim());
  const isItemNumActive = Boolean(itemNumberQuery.trim());
  const addBlockReason = (() => {
    const missing: string[] = [];
    if (!fieldcraftVendorId) missing.push("vendor");
    if (!selectedTradeName) missing.push("trade");
    if (!hasInStockSelection) missing.push("stock status");
    if (!hasAllowDecimalSelection) missing.push("decimal quantity setting");
    return missing.length ? `Select ${missing.join(", ")} in the import settings above` : "";
  })();
  const latestResponse = latestResponseKey ? responses[latestResponseKey] : null;
  const defaultBranchNumber = (connectionDefaults?.branchNumber ?? "").trim();
  const enteredBranchNumber = branchNumber.trim();
  const enteredShipToNumber = shipToNumber.trim();
  const hasDefaultPricingAccount = Boolean(
    connectionDefaults?.shipToNumber || connectionDefaults?.billToNumberPresent
  );
  const effectiveBranchNumber = enteredBranchNumber || defaultBranchNumber;
  const isUsingDefaultBranch = !enteredBranchNumber && Boolean(defaultBranchNumber);
  const isUsingDefaultShipTo = !enteredShipToNumber && hasDefaultPricingAccount;
  const abcServiceId = connectionDefaults?.serviceId ?? null;

  // Branch/Ship-To are free-typed connection identifiers, not pagination state. Reading them via
  // a ref (kept current below, without itself triggering a reload) lets loadAddedMaterials use the
  // latest value on every actual reload without changing identity on every keystroke — otherwise
  // the effect that auto-reloads My Materials would refire, and fetch, on every character typed.
  const addedMaterialsConnectionRef = useRef({ branchNumber: effectiveBranchNumber, shipToNumber: enteredShipToNumber });
  useEffect(() => {
    addedMaterialsConnectionRef.current = { branchNumber: effectiveBranchNumber, shipToNumber: enteredShipToNumber };
  }, [effectiveBranchNumber, enteredShipToNumber]);

  // Same "latest value via ref" need for the library-filter debounce effect below: it must use
  // the current page size on every reload it triggers, without refiring (and double-fetching)
  // just because the page size changed — that change already triggers its own explicit reload.
  const addedMaterialsPageSizeRef = useRef(addedMaterialsPageSize);
  useEffect(() => {
    addedMaterialsPageSizeRef.current = addedMaterialsPageSize;
  }, [addedMaterialsPageSize]);

  const getImportedMaterial = useCallback(
    (item: ABCItem | string): ABCSupplyImportedMaterial | undefined => {
      const itemNumber = typeof item === "string" ? item : getItemNumber(item);
      return itemNumber ? importedMaterialMap[itemNumber] : undefined;
    },
    [importedMaterialMap]
  );

  const isMaterialAlreadyAdded = useCallback(
    (item: ABCItem | string): boolean => Boolean(getImportedMaterial(item)),
    [getImportedMaterial]
  );

  const selectedItems = useMemo(
    () => items.filter((item) => selectedItemNumbers.has(getItemNumber(item))),
    [items, selectedItemNumbers]
  );
  const selectableItems = useMemo(
    () => items.filter((item) => !isMaterialAlreadyAdded(item)),
    [isMaterialAlreadyAdded, items]
  );
  const allVisibleSelected =
    selectableItems.length > 0 && selectableItems.every((item) => selectedItemNumbers.has(getItemNumber(item)));
  const partiallySelected = selectedItems.length > 0 && !allVisibleSelected;
  const getMissingPricingFields = useCallback((): string[] => {
    const missing: string[] = [];
    if (!effectiveBranchNumber) missing.push("Branch");
    if (!enteredShipToNumber && !hasDefaultPricingAccount) missing.push("Ship-To #");
    return missing;
  }, [effectiveBranchNumber, enteredShipToNumber, hasDefaultPricingAccount]);

  const showToast = useCallback(
    (message: string, severity: ToastState["severity"] = "info") => setToast({ message, severity }),
    []
  );

  const rememberResponse = useCallback((snapshot: Omit<ResponseSnapshot, "capturedAt">) => {
    const fullSnapshot: ResponseSnapshot = {
      ...snapshot,
      capturedAt: new Date().toLocaleString(),
    };
    setResponses((prev) => ({ ...prev, [snapshot.key]: fullSnapshot }));
    setLatestResponseKey(snapshot.key);
  }, []);

  const fetchPricesForItems = useCallback(
    async (materials: ABCItem[]) => {
      const validItems = materials
        .map((item, index) => ({ item, itemNumber: getItemNumber(item), index }))
        .filter(({ itemNumber }) => !!itemNumber)
        .slice(0, 50);

      if (!validItems.length) {
        setPriceMap({});
        setPriceMessage("No item numbers were available for ABC pricing.");
        return;
      }

      const missingFields = getMissingPricingFields();
      if (missingFields.length) {
        setPriceMap({});
        setPriceMessage(
          `Enter ${missingFields.join(" and ")}, or save it in ABC Supply connection settings to load prices.`
        );
        return;
      }

      setIsFetchingTablePrices(true);
      setPriceMessage(null);
      try {
        const response = await priceABCSupplyItems(
          {
            tenant_id: tenantId ?? null,
            branch_number: effectiveBranchNumber || null,
            ship_to_number: enteredShipToNumber || null,
            purpose: "estimating",
            request_id: `fieldcraft-visible-prices-${Date.now()}`,
            lines: validItems.map(({ item, itemNumber }, index) => ({
              id: String(index + 1),
              itemNumber,
              quantity: 1,
              uom: getUom(item) || undefined,
            })),
          },
          accessToken
        );

        if (!response.ok) {
          setPriceMap({});
          setPriceMessage(response.message ?? "ABC pricing was not available for the visible items.");
          rememberResponse({
            key: "pricing",
            title: "ABC Supply pricing response",
            message: response.message ?? "ABC returned an error while fetching visible material prices.",
            payload: response,
          });
          return;
        }

        const nextPriceMap: Record<string, PriceResult> = {};
        extractPriceLines(response.data).forEach((line) => {
          const parsed = normalizePriceLine(line);
          if (parsed) nextPriceMap[parsed.itemNumber] = parsed;
        });
        setPriceMap(nextPriceMap);
        setPriceMessage(
          Object.keys(nextPriceMap).length
            ? `Loaded prices for ${Object.keys(nextPriceMap).length} visible material${Object.keys(nextPriceMap).length === 1 ? "" : "s"}.`
            : "ABC responded, but no line-level prices were returned for the visible items."
        );
        rememberResponse({
          key: "pricing",
          title: "ABC Supply pricing response",
          message: `ABC pricing completed for ${validItems.length} visible material${validItems.length === 1 ? "" : "s"}.`,
          payload: response.data,
        });
      } catch (error: any) {
        setPriceMap({});
        setPriceMessage("Could not fetch ABC prices. Materials without pricing cannot be added to the material list.");
        rememberResponse({
          key: "pricing",
          title: "ABC Supply pricing response",
          message: "ABC pricing request failed before a usable response was returned.",
          payload: { error: error?.message ?? String(error) },
        });
      } finally {
        setIsFetchingTablePrices(false);
      }
    },
    [accessToken, effectiveBranchNumber, enteredShipToNumber, getMissingPricingFields, rememberResponse, tenantId]
  );

  const fetchImportedStatusForItems = useCallback(
    async (materials: ABCItem[]) => {
      const itemNumbers = Array.from(new Set(materials.map(getItemNumber).filter(Boolean)));
      if (!tenantId || !itemNumbers.length) {
        setImportedMaterialMap({});
        return;
      }

      setIsLoadingImportedStatus(true);
      try {
        const vendorId = Number(fieldcraftVendorId);
        const response = await getImportedABCSupplyMaterials(
          {
            tenant_id: tenantId,
            service_id: abcServiceId,
            external_item_ids: itemNumbers,
            branch_number: effectiveBranchNumber || null,
            ship_to_number: enteredShipToNumber || null,
            fieldcraft_vendor_id: Number.isFinite(vendorId) && vendorId > 0 ? vendorId : null,
            page: 1,
            limit: Math.min(Math.max(itemNumbers.length, 1), 500),
          },
          accessToken
        );

        if (!response.ok) {
          setImportedMaterialMap({});
          return;
        }

        const nextMap: Record<string, ABCSupplyImportedMaterial> = {};
        (response.data?.items ?? []).forEach((material) => {
          const keys = [material.externalItemId, material.itemNumber, material.itemCode].filter(Boolean);
          keys.forEach((key) => {
            nextMap[String(key)] = material;
          });
        });
        setImportedMaterialMap(nextMap);
        const addedNumbers = new Set(Object.keys(nextMap));
        if (addedNumbers.size) {
          setSelectedItemNumbers((prev) => {
            const next = new Set(prev);
            addedNumbers.forEach((itemNumber) => next.delete(itemNumber));
            return next;
          });
        }
      } catch {
        setImportedMaterialMap({});
      } finally {
        setIsLoadingImportedStatus(false);
      }
    },
    [accessToken, abcServiceId, effectiveBranchNumber, enteredShipToNumber, fieldcraftVendorId, tenantId]
  );

  // Page/page-size navigation always passes explicit values (mirroring handleSearch's pattern for
  // the catalog results below), so they're deliberately left out of this dependency array — that
  // keeps this callback's identity stable across page/page-size changes instead of forcing the
  // auto-load effect below to re-fire on every click. targetPageSize's default reads from a ref
  // (always current) rather than the dependency array, for the same reason. appliedLibraryFilter
  // stays a real dependency: it's already debounced upstream, and the auto-load effect is what
  // triggers the actual reload once a new filter value settles — see the effect below.
  const loadAddedMaterials = useCallback(async (
    targetPage = 1,
    targetPageSize = addedMaterialsPageSizeRef.current,
    targetSearch = appliedLibraryFilter
  ) => {
    if (!tenantId) {
      showToast("Tenant is required to load ABC Supply materials.", "warning");
      return;
    }

    // Only the most recently started request is allowed to write to state — if the user
    // pages/filters again before this one resolves, its (possibly out-of-order) response is dropped.
    const requestId = ++addedMaterialsRequestIdRef.current;
    setIsLoadingAddedMaterials(true);
    try {
      // Read Branch/Ship-To from the ref (not the reactive derived values) so that typing in
      // those fields never changes this callback's identity — see addedMaterialsConnectionRef.
      const { branchNumber: currentBranchNumber, shipToNumber: currentShipToNumber } =
        addedMaterialsConnectionRef.current;
      // Intentionally not filtered by fieldcraftVendorId: the vendor picker controls where newly
      // imported materials get assigned, not which already-imported materials are visible here.
      const response = await getImportedABCSupplyMaterials(
        {
          tenant_id: tenantId,
          service_id: abcServiceId,
          branch_number: currentBranchNumber || null,
          ship_to_number: currentShipToNumber || null,
          fieldcraft_vendor_id: null,
          search: targetSearch || null,
          page: targetPage,
          limit: targetPageSize,
        },
        accessToken
      );

      if (requestId !== addedMaterialsRequestIdRef.current) return;

      if (!response.ok) {
        setAddedMaterials([]);
        setAddedMaterialsTotal(0);
        showToast(response.message ?? "Could not load added ABC materials.", "error");
        return;
      }

      const items = response.data?.items ?? [];
      const total = Number(response.data?.total ?? items.length);
      setAddedMaterials(items);
      setAddedMaterialsTotal(Number.isFinite(total) ? total : items.length);
      setAddedMaterialsPage(Number(response.data?.page ?? targetPage));
      setAddedMaterialsPageSize(Number(response.data?.limit ?? targetPageSize));
    } catch {
      if (requestId !== addedMaterialsRequestIdRef.current) return;
      setAddedMaterials([]);
      setAddedMaterialsTotal(0);
      showToast("Could not load added ABC materials.", "error");
    } finally {
      if (requestId === addedMaterialsRequestIdRef.current) {
        setIsLoadingAddedMaterials(false);
      }
    }
  }, [accessToken, abcServiceId, appliedLibraryFilter, showToast, tenantId]);

  useEffect(() => {
    if (connectionStatus !== undefined) {
      setConnectionDefaults(connectionStatus ?? null);
      return;
    }

    let active = true;

    const loadConnectionDefaults = async () => {
      if (!tenantId) {
        setConnectionDefaults(null);
        return;
      }

      try {
        const response = await getABCSupplyConnectionStatus({ tenant_id: tenantId }, accessToken);
        if (active && response.ok) setConnectionDefaults(response.data ?? null);
      } catch {
        if (active) setConnectionDefaults(null);
      }
    };

    loadConnectionDefaults();
    return () => {
      active = false;
    };
  }, [accessToken, connectionStatus, tenantId]);

  useEffect(() => {
    let active = true;

    const loadVendors = async () => {
      setIsLoadingVendors(true);
      setVendorError(null);
      try {
        const response = await getAllVendors(accessToken);
        if (!active) return;
        const vendors = Array.isArray(response?.data) ? response.data : [];
        setVendorList(vendors);
        const abcVendor = vendors.find((vendor: VendorOption) =>
          getVendorName(vendor).toLowerCase().includes("abc supply")
        );
        if (abcVendor) setSelectedVendor(abcVendor);
      } catch {
        if (active) setVendorError("Could not load vendors.");
      } finally {
        if (active) setIsLoadingVendors(false);
      }
    };

    loadVendors();
    return () => {
      active = false;
    };
  }, [accessToken]);

  useEffect(() => {
    let active = true;

    const loadTrades = async () => {
      setIsLoadingTrades(true);
      setTradeError(null);
      try {
        const response = await getValidTradeNames(accessToken);
        if (!active) return;
        const trades = extractTradeList(response);
        setTradeList(trades);
        const roofingTrade = trades.find((trade) => getTradeName(trade).toLowerCase() === "roofing");
        setSelectedTrade((current) => current ?? roofingTrade ?? trades[0] ?? null);
        if (!trades.length) setTradeError("No valid trades were returned.");
      } catch {
        if (active) {
          setTradeList([]);
          setSelectedTrade(null);
          setTradeError("Could not load valid trades.");
        }
      } finally {
        if (active) setIsLoadingTrades(false);
      }
    };

    loadTrades();
    return () => {
      active = false;
    };
  }, [accessToken]);

  // Only updates state — does not call loadAddedMaterials directly. When the debounce settles on
  // a genuinely new value, appliedLibraryFilter changes, which changes loadAddedMaterials's
  // identity (it's a real dependency, see above), which is what triggers the effect below to
  // reload. If the trimmed value is unchanged, React bails out of the state update and nothing
  // re-fires — so typing and then deleting back to the same filter doesn't cause an extra fetch.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setAddedMaterialsPage(1);
      setAppliedLibraryFilter(libraryFilter.trim());
    }, 350);

    return () => window.clearTimeout(handle);
  }, [libraryFilter]);

  // Fires on mount and whenever tenantId/accessToken/appliedLibraryFilter actually change —
  // NOT on page or page-size changes, which are handled by explicit calls at their own call sites
  // (goToAddedMaterialsPage, handleAddedMaterialsRowsPerPageChange, Refresh, post-import reload).
  useEffect(() => {
    if (!tenantId) return;
    loadAddedMaterials();
  }, [tenantId, accessToken, loadAddedMaterials]);

  const toggleItemSelection = useCallback((item: ABCItem) => {
    const itemNumber = getItemNumber(item);
    if (!itemNumber || isMaterialAlreadyAdded(item)) return;
    setSelectedItemNumbers((prev) => {
      const next = new Set(prev);
      if (next.has(itemNumber)) next.delete(itemNumber);
      else next.add(itemNumber);
      return next;
    });
  }, [isMaterialAlreadyAdded]);

  const toggleAllVisibleSelection = useCallback(() => {
    setSelectedItemNumbers((prev) => {
      const next = new Set(prev);
      const validNumbers = selectableItems.map(getItemNumber).filter(Boolean);
      const shouldSelectAll = validNumbers.some((num) => !next.has(num));
      validNumbers.forEach((num) => {
        if (shouldSelectAll) next.add(num);
        else next.delete(num);
      });
      return next;
    });
  }, [selectableItems]);

  const handleImportSelectedToFIELDCRAFT = useCallback(
    async (itemsToImport?: ABCItem[]) => {
      const materials = itemsToImport?.length ? itemsToImport : selectedItems;
      const vendorId = Number(fieldcraftVendorId);

      if (!materials.length) {
        showToast("Select at least one ABC material first.", "warning");
        return;
      }
      if (!Number.isFinite(vendorId) || vendorId <= 0) {
        showToast("Select the vendor that should own these ABC materials.", "warning");
        return;
      }
      if (!selectedTradeName) {
        showToast("Select a valid trade before adding ABC materials.", "warning");
        return;
      }
      if (!hasInStockSelection) {
        showToast("Select whether these ABC materials are in stock.", "warning");
        return;
      }
      if (!hasAllowDecimalSelection) {
        showToast("Select whether decimal quantities are allowed for these ABC materials.", "warning");
        return;
      }
      const missingFields = getMissingPricingFields();
      if (missingFields.length) {
        showToast(
          `Enter ${missingFields.join(" and ")}, or save it in ABC Supply connection settings before adding materials. Pricing is required.`,
          "warning"
        );
        return;
      }

      setIsImportingToFIELDCRAFT(true);
      setImportResult(null);
      try {
        const response = await importSelectedABCSupplyMaterialsToFIELDCRAFT(
          {
            tenant_id: tenantId ?? null,
            service_id: abcServiceId,
            materials,
            branch_number: effectiveBranchNumber || null,
            ship_to_number: enteredShipToNumber || null,
            fieldcraft_vendor_id: vendorId,
            fieldcraft_vendor_name: fieldcraftVendorName || "ABC Supply",
            default_trade: selectedTradeName,
            default_grade: "a",
            default_base_unit: "EA",
            default_conversion_factor: "1",
            default_cost: "0",
            default_manufacturer: "Unknown",
            allow_decimal: toRequiredBoolean(selectedAllowDecimal),
            in_stock: toRequiredBoolean(selectedInStock),
            fetch_prices: true,
            require_pricing: true,
            dry_run: false,
            batch_size: 50,
          },
          accessToken
        );

        if (!response.ok) {
          rememberResponse({
            key: "import",
            title: "Material list import response",
            message: response.message ?? "The selected ABC materials could not be added to the material list.",
            payload: response,
          });
          showToast(response.message ?? "Could not add selected ABC materials to the material list.", "error");
          return;
        }

        const data: ImportResult = response.data ?? {};
        const counts = data.counts ?? {};
        setImportResult(data);
        const skipped = getSkippedImportCount(counts);
        const failed = counts.failed ?? 0;
        const summaryText = buildImportSummaryText(counts);
        rememberResponse({
          key: "import",
          title: "Material list import response",
          message: summaryText,
          payload: data,
        });
        showToast(summaryText, failed > 0 ? "error" : skipped > 0 ? "warning" : "success");

        if ((counts.failed ?? 0) === 0) {
          fetchImportedStatusForItems(materials);
          setAddedMaterialsPage(1);
          loadAddedMaterials(1, addedMaterialsPageSize, appliedLibraryFilter);
          setSelectedItemNumbers((prev) => {
            const next = new Set(prev);
            materials.map(getItemNumber).filter(Boolean).forEach((num) => next.delete(num));
            return next;
          });
        }
      } catch (error: any) {
        rememberResponse({
          key: "import",
          title: "Material list import response",
          message: "Could not reach the material import service.",
          payload: { error: error?.message ?? String(error) },
        });
        showToast("Could not reach the material import service. Check your connection.", "error");
      } finally {
        setIsImportingToFIELDCRAFT(false);
      }
    },
    [
      accessToken,
      abcServiceId,
      effectiveBranchNumber,
      enteredShipToNumber,
      selectedTradeName,
      hasInStockSelection,
      hasAllowDecimalSelection,
      selectedInStock,
      selectedAllowDecimal,
      fieldcraftVendorId,
      fieldcraftVendorName,
      fetchImportedStatusForItems,
      getMissingPricingFields,
      addedMaterialsPageSize,
      appliedLibraryFilter,
      loadAddedMaterials,
      rememberResponse,
      selectedItems,
      showToast,
      tenantId,
    ]
  );

  const handleSearch = useCallback(
    async (targetPage = 1, overrideQuery?: string, overridePageSize?: number) => {
      const effectiveQuery = overrideQuery ?? query;
      const effectivePageSize = overridePageSize ?? pageSize;
      const hasQuery = effectiveQuery.trim() || itemNumberQuery.trim() || effectiveBranchNumber;
      if (!hasQuery) {
        searchInputRef.current?.focus();
        return;
      }

      setIsSearching(true);
      setSearchError(null);
      setPage(targetPage);
      setPriceMap({});
      setImportedMaterialMap({});
      setPriceMessage(null);

      const request: ABCSearchRequest = {
        tenant_id: tenantId ?? null,
        query: effectiveQuery.trim() || null,
        item_number: itemNumberQuery.trim() || null,
        branch_number: effectiveBranchNumber || null,
        items_per_page: effectivePageSize,
        page_number: targetPage,
        include_family_items: includeFamilyItems,
      };

      try {
        const response = await searchABCSupplyItems(request, accessToken);

        if (!response.ok) {
          const kind: SearchErrorKind = response.status === 400 ? "not_configured" : "api_error";
          setSearchError({
            kind,
            message:
              response.message ??
              (kind === "not_configured"
                ? "ABC Supply integration is not configured. Contact your administrator."
                : "ABC Supply API is unavailable. Please try again."),
          });
          setItems([]);
          setTotalItems(0);
          rememberResponse({
            key: "search",
            title: "ABC Supply search response",
            message: response.message ?? "ABC Supply returned an error for this search.",
            payload: response,
          });
        } else {
          const rawItems = extractItems(response.data);
          const total = extractTotal(response.data, rawItems.length);
          setItems(rawItems);
          setSelectedItemNumbers(new Set());
          setImportResult(null);
          setTotalItems(total);
          setLastSearchQuery(effectiveQuery.trim() || itemNumberQuery.trim() || effectiveBranchNumber);
          rememberResponse({
            key: "search",
            title: "ABC Supply search response",
            message: `ABC returned ${rawItems.length} visible material${rawItems.length === 1 ? "" : "s"}${total ? ` out of ${total}` : ""}. Review prices, select the needed rows, then add them to the material list.`,
            payload: response.data,
          });
          if (rawItems.length) {
            fetchPricesForItems(rawItems);
            fetchImportedStatusForItems(rawItems);
          }
        }
      } catch (error: any) {
        setSearchError({
          kind: "network_error",
          message: "Could not reach ABC Supply service. Check your connection.",
        });
        setItems([]);
        setTotalItems(0);
        rememberResponse({
          key: "search",
          title: "ABC Supply search response",
          message: "The search request failed before a usable ABC response was returned.",
          payload: { error: error?.message ?? String(error), request },
        });
      } finally {
        setIsSearching(false);
        setHasSearched(true);
      }
    }, [
      accessToken,
      effectiveBranchNumber,
      fetchImportedStatusForItems,
      fetchPricesForItems,
      includeFamilyItems,
      itemNumberQuery,
      pageSize,
      query,
      rememberResponse,
      tenantId,
    ]
  );

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") handleSearch(1);
  };

  const handlePageChange = (_: unknown, newZeroBasedPage: number) => {
    const newPage = newZeroBasedPage + 1;
    setPage(newPage);
    handleSearch(newPage);
  };

  const handleRowsPerPageChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newSize = parseInt(event.target.value, 10);
    setPageSize(newSize);
    setPage(1);
    handleSearch(1, undefined, newSize);
  };

  const addedMaterialsTotalPages = Math.max(
    1,
    Math.ceil(addedMaterialsTotal / Math.max(addedMaterialsPageSize, 1))
  );

  // Mirrors handleSearch's pattern for the catalog results below: call the loader directly with
  // the new value instead of only updating state and relying on the auto-load effect to catch up.
  const goToAddedMaterialsPage = (direction: -1 | 1) => {
    const newPage = Math.min(addedMaterialsTotalPages, Math.max(1, addedMaterialsPage + direction));
    setAddedMaterialsPage(newPage);
    loadAddedMaterials(newPage, addedMaterialsPageSize, appliedLibraryFilter);
  };

  const handleAddedMaterialsRowsPerPageChange = (event: SelectChangeEvent<number>) => {
    const newSize = Number(event.target.value);
    setAddedMaterialsPage(1);
    setAddedMaterialsPageSize(newSize);
    loadAddedMaterials(1, newSize, appliedLibraryFilter);
  };

  const handleSelectRow = async (item: ABCItem) => {
    const itemNumber = getItemNumber(item);
    setSelectedItem(item);
    setDrawerOpen(true);
    setFullDetail(null);

    if (!itemNumber) return;
    setIsLoadingDetail(true);
    try {
      const response = await getABCSupplyItem(
        {
          tenant_id: tenantId ?? null,
          item_number: itemNumber,
          include_price: getMissingPricingFields().length === 0,
          branch_number: effectiveBranchNumber || null,
          ship_to_number: enteredShipToNumber || null,
        },
        accessToken
      );
      const detailPayload = response.ok ? response.data?.item ?? response.data ?? item : item;
      setFullDetail(detailPayload);
      rememberResponse({
        key: "detail",
        title: `ABC Supply item detail: ${itemNumber}`,
        message: response.ok
          ? `ABC returned detail for item ${itemNumber}.`
          : `Could not load full detail for item ${itemNumber}; showing the search-row data instead.`,
        payload: response.ok ? response.data : response,
      });
    } catch (error: any) {
      setFullDetail(item);
      rememberResponse({
        key: "detail",
        title: `ABC Supply item detail: ${itemNumber}`,
        message: `Could not fetch detail for item ${itemNumber}; showing the search-row data instead.`,
        payload: { error: error?.message ?? String(error), fallback: item },
      });
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const handleCloseDrawer = () => {
    setDrawerOpen(false);
    setSelectedItem(null);
    setFullDetail(null);
  };

  const renderSkeletonRows = () =>
    Array.from({ length: 5 }).map((_, idx) => (
      <TableRow key={idx} className="abc-table-row abc-skeleton-row">
        {Array.from({ length: 9 }).map((__, columnIndex) => (
          <TableCell key={columnIndex}>
            <Skeleton variant="text" width={columnIndex === 2 ? "80%" : columnIndex === 6 ? 70 : "60%"} height={20} />
          </TableCell>
        ))}
      </TableRow>
    ));

  const renderIdleState = () => (
    <Box className="abc-idle-state">
      <Box className="abc-idle-icon-wrap">
        <LayersRoundedIcon className="abc-idle-icon" />
      </Box>
      <Typography className="abc-idle-title">Search ABC Supply catalog</Typography>
      <Typography className="abc-idle-sub">
        Search by product name, description, or item number. Prices load automatically for the visible results.
      </Typography>
      <Stack direction="row" gap={1} justifyContent="center" flexWrap="wrap" mt={2}>
        {["Asphalt shingles", "Ridge cap", "Ice & water", "Felt underlayment", "Drip edge"].map((term) => (
          <Chip
            key={term}
            label={term}
            className="abc-suggestion-chip"
            onClick={() => {
              setQuery(term);
              handleSearch(1, term);
            }}
            clickable
          />
        ))}
      </Stack>
    </Box>
  );

  const renderErrorState = () => {
    const isConfig = searchError?.kind === "not_configured";
    return (
      <Box className="abc-error-state">
        <ErrorOutlineRoundedIcon className={`abc-error-icon ${isConfig ? "abc-error-icon--config" : ""}`} />
        <Typography className="abc-error-title">{isConfig ? "Integration not configured" : "Search failed"}</Typography>
        <Typography className="abc-error-sub">{searchError?.message}</Typography>
        {!isConfig && (
          <Button startIcon={<RefreshRoundedIcon />} className="abc-retry-btn" onClick={() => handleSearch(1)}>
            Try again
          </Button>
        )}
      </Box>
    );
  };

  const renderEmptyState = () => (
    <Box className="abc-empty-state">
      <SearchRoundedIcon className="abc-empty-icon" />
      <Typography className="abc-empty-title">No items found</Typography>
      <Typography className="abc-empty-sub">
        No ABC Supply items matched {lastSearchQuery ? <strong>"{lastSearchQuery}"</strong> : "your search"}.
        Try a different keyword or search by item number.
      </Typography>
    </Box>
  );

  const renderResponsePanel = () => {
    if (!latestResponse) return null;

    return (
      <Box className="abc-response-panel">
        <Box className="abc-response-panel-body">
          <Typography className="abc-response-title">{latestResponse.title}</Typography>
          <Typography className="abc-response-message">{latestResponse.message}</Typography>
        </Box>
        <IconButton
          size="small"
          className="abc-response-panel-close"
          onClick={() => setLatestResponseKey(null)}
          aria-label="Dismiss"
        >
          <CloseRoundedIcon />
        </IconButton>
      </Box>
    );
  };

  const renderImportResult = () => {
    if (!importResult?.counts) return null;

    const counts = importResult.counts;
    const createdCount = getImportCreatedCount(counts);
    const skippedCount = getSkippedImportCount(counts);
    const linkedCount = counts.linkedExisting ?? 0;
    const failedCount = counts.failed ?? 0;
    const hasIssues = skippedCount > 0 || failedCount > 0;
    const addedRecords = importResult.created ?? [];
    const linkedRecords = importResult.linkedExisting ?? [];
    const issueRecords = [...(importResult.skipped ?? []), ...(importResult.failed ?? [])];

    const renderDetailChevron = (panel: 'added' | 'linked' | 'skipped') => (
      <Box
        component="span"
        sx={{
          display: 'inline-flex',
          transition: 'transform 0.2s',
          transform: importDetailPanel === panel ? 'rotate(180deg)' : 'rotate(0deg)',
        }}
      >▾</Box>
    );

    const renderDetailRows = (records: Array<Record<string, any>>) => (
      <Box className="abc-import-toast-rows">
        {records.map((record, i) => {
          const reason = record?.reason;
          const severity = getImportRecordSeverity(record);
          return (
            <Box
              key={i}
              className={`abc-import-toast-row${reason ? ` abc-import-toast-row--${severity}` : ""}`}
            >
              <Stack direction="row" alignItems="baseline" gap={0.75} flexWrap="nowrap" sx={{ minWidth: 0 }}>
                <Typography component="span" className="abc-import-toast-row-code">
                  {getImportRecordItemNumber(record)}
                </Typography>
                <Typography
                  component="span"
                  className="abc-import-toast-row-name"
                  title={getImportRecordName(record) || undefined}
                >
                  {getImportRecordName(record) || "—"}
                </Typography>
              </Stack>
              {reason && (
                <Typography className="abc-import-toast-row-reason" title={reason}>
                  {getImportRecordReason(record)}
                </Typography>
              )}
            </Box>
          );
        })}
      </Box>
    );

    return (
      <Box className={`abc-import-toast ${hasIssues ? 'abc-import-toast--warn' : 'abc-import-toast--ok'}`}>
        <Box className="abc-import-toast-icon">
          {hasIssues ? <ErrorOutlineRoundedIcon /> : <DoneAllRoundedIcon />}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1} flexWrap="wrap">
            <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
              {createdCount > 0 && (
                <Box component="span" className="abc-import-badge abc-import-badge--ok">
                  {createdCount} added
                </Box>
              )}
              {linkedCount > 0 && (
                <Box component="span" className="abc-import-badge abc-import-badge--linked">
                  {linkedCount} linked to existing
                </Box>
              )}
              {skippedCount > 0 && (
                <Box component="span" className="abc-import-badge abc-import-badge--skip">
                  {skippedCount} skipped
                </Box>
              )}
              {failedCount > 0 && (
                <Box component="span" className="abc-import-badge abc-import-badge--fail">
                  {failedCount} failed
                </Box>
              )}
              {createdCount === 0 && linkedCount === 0 && skippedCount === 0 && failedCount === 0 && (
                <Typography className="abc-import-toast-msg">Nothing was added</Typography>
              )}
            </Stack>
            <Stack direction="row" alignItems="center" gap={0.5}>
              {addedRecords.length > 0 && (
                <Button
                  size="small"
                  className={`abc-import-corner-btn${importDetailPanel === 'added' ? ' abc-import-corner-btn--active' : ''}`}
                  onClick={() => setImportDetailPanel((v) => v === 'added' ? null : 'added')}
                  endIcon={renderDetailChevron('added')}
                >
                  View added
                </Button>
              )}
              {linkedRecords.length > 0 && (
                <Button
                  size="small"
                  className={`abc-import-corner-btn${importDetailPanel === 'linked' ? ' abc-import-corner-btn--active' : ''}`}
                  onClick={() => setImportDetailPanel((v) => v === 'linked' ? null : 'linked')}
                  endIcon={renderDetailChevron('linked')}
                >
                  View linked
                </Button>
              )}
              {issueRecords.length > 0 && (
                <Button
                  size="small"
                  className={`abc-import-corner-btn${importDetailPanel === 'skipped' ? ' abc-import-corner-btn--active' : ''}`}
                  onClick={() => setImportDetailPanel((v) => v === 'skipped' ? null : 'skipped')}
                  endIcon={renderDetailChevron('skipped')}
                >
                  View skipped
                </Button>
              )}
            </Stack>
          </Stack>
          {(counts.skippedMissingPrice ?? 0) > 0 && (
            <Typography className="abc-import-toast-note">
              {skippedCount === 1 ? '1 item' : `${skippedCount} items`} had no ABC Supply price — pricing is required before saving to your list.
            </Typography>
          )}
          {importDetailPanel === 'added' && addedRecords.length > 0 && renderDetailRows(addedRecords)}
          {importDetailPanel === 'linked' && linkedRecords.length > 0 && renderDetailRows(linkedRecords)}
          {importDetailPanel === 'skipped' && issueRecords.length > 0 && renderDetailRows(issueRecords)}
        </Box>
        <IconButton size="small" className="abc-import-toast-close" onClick={() => { setImportResult(null); setImportDetailPanel(null); }} aria-label="Dismiss">
          <CloseRoundedIcon />
        </IconButton>
      </Box>
    );
  };

  const renderLibraryView = () => {
    const hasLibrarySearch = Boolean(appliedLibraryFilter);

    // Full skeleton only for the first load (nothing to show yet). Once a page of data has
    // rendered, subsequent loads (paging/filtering/page-size) keep it on screen — with the
    // pager and Refresh controls disabled — instead of flashing back to a blank skeleton.
    if (isLoadingAddedMaterials && !addedMaterials.length) {
      return (
        <Box className="abc-library-view">
          <Box className="abc-lib-toolbar">
            <Skeleton variant="rounded" height={38} sx={{ flex: 1, bgcolor: 'rgba(0,0,0,0.06)' }} />
            <Skeleton variant="rounded" width={96} height={38} sx={{ bgcolor: 'rgba(0,0,0,0.06)' }} />
          </Box>
          <Box className="abc-table-wrap">
            <Stack gap={0} divider={<Divider />}>
              {Array.from({ length: 6 }).map((_, i) => (
                <Box key={i} sx={{ px: 2, py: 1.5 }}>
                  <Skeleton variant="text" width="40%" height={14} />
                  <Skeleton variant="text" width="70%" height={16} />
                </Box>
              ))}
            </Stack>
          </Box>
        </Box>
      );
    }

    if (!addedMaterials.length) {
      return (
        <Box className="abc-library-view">
          <Box className="abc-lib-toolbar">
            <Box className="abc-lib-filter">
              <SearchRoundedIcon className="abc-lib-filter-icon" />
              <InputBase
                placeholder="Search by name, item #, or vendor…"
                value={libraryFilter}
                onChange={(e) => setLibraryFilter(e.target.value)}
                className="abc-lib-filter-input"
              />
              {libraryFilter && (
                <IconButton size="small" onClick={() => setLibraryFilter('')} sx={{ padding: '2px', color: 'var(--abc-muted)' }}>
                  <CloseRoundedIcon sx={{ fontSize: '14px' }} />
                </IconButton>
              )}
            </Box>
            <Button
              className="abc-lib-refresh-btn"
              startIcon={isLoadingAddedMaterials ? <CircularProgress size={13} /> : <RefreshRoundedIcon />}
              onClick={() => loadAddedMaterials(addedMaterialsPage, addedMaterialsPageSize, appliedLibraryFilter)}
            >
              Refresh
            </Button>
          </Box>
          <Box className="abc-library-empty">
            <Box className="abc-idle-icon-wrap">
              <Inventory2RoundedIcon className="abc-idle-icon" />
            </Box>
            <Typography className="abc-empty-title">{hasLibrarySearch ? "No matching materials" : "No materials imported yet"}</Typography>
            <Typography className="abc-empty-sub">
              {hasLibrarySearch
                ? "Try a different name, item number, or vendor."
                : "Search the ABC Supply catalog and add materials to build your library."}
            </Typography>
            {hasLibrarySearch ? (
              <Button className="abc-ghost-btn" onClick={() => setLibraryFilter('')} startIcon={<CloseRoundedIcon />}>
                Clear Search
              </Button>
            ) : (
              <Button className="abc-ghost-btn" onClick={() => setActiveTab('search')} startIcon={<SearchRoundedIcon />}>
                Go to Search Catalog
              </Button>
            )}
          </Box>
        </Box>
      );
    }

    return (
      <Box className="abc-library-view">
        <Box className="abc-lib-toolbar">
          <Box className="abc-lib-filter">
            <SearchRoundedIcon className="abc-lib-filter-icon" />
            <InputBase
              placeholder="Search by name, item #, or vendor…"
              value={libraryFilter}
              onChange={(e) => setLibraryFilter(e.target.value)}
              className="abc-lib-filter-input"
            />
            {libraryFilter && (
              <IconButton size="small" onClick={() => setLibraryFilter('')} sx={{ padding: '2px', color: 'var(--abc-muted)' }}>
                <CloseRoundedIcon sx={{ fontSize: '14px' }} />
              </IconButton>
            )}
          </Box>
          <Box className="abc-lib-pager">
            <Typography className="abc-lib-pager-range">
              {addedMaterialsTotal === 0
                ? "0 of 0"
                : `${(addedMaterialsPage - 1) * addedMaterialsPageSize + 1}–${Math.min(
                    addedMaterialsPage * addedMaterialsPageSize,
                    addedMaterialsTotal
                  )} of ${addedMaterialsTotal}`}
            </Typography>
            <Box className="abc-lib-pager-nav">
              <IconButton
                size="small"
                className="abc-lib-pager-btn"
                disabled={addedMaterialsPage <= 1 || isLoadingAddedMaterials}
                onClick={() => goToAddedMaterialsPage(-1)}
                aria-label="Previous page"
              >
                <ChevronLeftRoundedIcon fontSize="small" />
              </IconButton>
              <IconButton
                size="small"
                className="abc-lib-pager-btn"
                disabled={addedMaterialsPage >= addedMaterialsTotalPages || isLoadingAddedMaterials}
                onClick={() => goToAddedMaterialsPage(1)}
                aria-label="Next page"
              >
                <ChevronRightRoundedIcon fontSize="small" />
              </IconButton>
            </Box>
            <Select
              size="small"
              variant="outlined"
              value={addedMaterialsPageSize}
              onChange={handleAddedMaterialsRowsPerPageChange}
              disabled={isLoadingAddedMaterials}
              className="abc-lib-pager-size"
              inputProps={{ "aria-label": "Per page" }}
              IconComponent={ExpandMoreRoundedIcon}
              renderValue={(value) => `${value} / page`}
              MenuProps={{
                disableScrollLock: true,
                classes: { paper: "abc-dropdown-menu-paper" },
                anchorOrigin: { vertical: "bottom", horizontal: "right" },
                transformOrigin: { vertical: "top", horizontal: "right" },
              }}
            >
              {LIBRARY_PAGE_SIZE_OPTIONS.map((opt) => (
                <MenuItem key={opt} value={opt} className="abc-dropdown-menu-item">
                  <span>{opt} per page</span>
                  {opt === addedMaterialsPageSize && (
                    <CheckRoundedIcon className="abc-dropdown-menu-check" fontSize="small" />
                  )}
                </MenuItem>
              ))}
            </Select>
          </Box>
          <Button
            className="abc-lib-refresh-btn"
            startIcon={isLoadingAddedMaterials ? <CircularProgress size={13} /> : <RefreshRoundedIcon />}
            onClick={() => loadAddedMaterials(addedMaterialsPage, addedMaterialsPageSize, appliedLibraryFilter)}
            disabled={isLoadingAddedMaterials}
          >
            Refresh
          </Button>
        </Box>

        <Box className="abc-table-wrap">
          <TableContainer className="abc-library-table-container">
            <Table stickyHeader size="small" aria-label="Imported ABC Supply materials">
              <TableHead>
                <TableRow className="abc-table-head-row">
                  <TableCell className="abc-th" sx={{ width: 130 }}>Item #</TableCell>
                  <TableCell className="abc-th">Material</TableCell>
                  <TableCell className="abc-th" align="center" sx={{ width: 120 }}>Vendor</TableCell>
                  <TableCell className="abc-th" align="center" sx={{ width: 64 }}>UOM</TableCell>
                  <TableCell className="abc-th" align="right" sx={{ width: 96 }}>Unit Cost</TableCell>
                  <TableCell className="abc-th" align="center" sx={{ width: 100 }}>Imported</TableCell>
                  <TableCell className="abc-th" align="center" sx={{ width: 84 }}>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {addedMaterials.map((material, idx) => (
                  <TableRow
                    key={`${material.materialExternalMapId ?? material.itemNumber ?? idx}`}
                    className="abc-table-row"
                    style={{ '--row-idx': idx } as React.CSSProperties}
                  >
                    <TableCell className="abc-td abc-td--sku">
                      <span className="abc-sku-badge">
                        {material.itemNumber || material.externalItemId || '—'}
                      </span>
                    </TableCell>
                    <TableCell className="abc-td abc-td--desc">
                      <Tooltip title={material.name || material.externalDisplayName || ''} placement="top-start" enterDelay={600}>
                        <span className="abc-library-name">
                          {material.name || material.externalDisplayName || '—'}
                        </span>
                      </Tooltip>
                    </TableCell>
                    <TableCell className="abc-td" align="center">
                      <span className="abc-vendor-tag">{material.vendorName || 'ABC Supply'}</span>
                    </TableCell>
                    <TableCell className="abc-td abc-td--uom" align="center">{material.unit || '—'}</TableCell>
                    <TableCell className="abc-td abc-td--price" align="right">
                      <span className={`abc-inline-price${material.cost === undefined || material.cost === null ? ' abc-inline-price--empty' : ''}`}>
                        {formatPrice(material.cost === undefined || material.cost === null ? undefined : Number(material.cost))}
                      </span>
                    </TableCell>
                    <TableCell className="abc-td" align="center">
                      <span className="abc-library-date">
                        {material.updatedAt
                          ? new Date(material.updatedAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                          : '—'}
                      </span>
                    </TableCell>
                    <TableCell className="abc-td" align="center">
                      <span className="abc-synced-chip">
                        <span className="abc-synced-dot" />
                        Added
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      </Box>
    );
  };

  const renderResults = () => (
    <>
      <Box className="abc-table-wrap">
      <TableContainer className="abc-table-container">
        <Table stickyHeader size="small" aria-label="ABC Supply search results">
          <TableHead>
            <TableRow className="abc-table-head-row">
              <TableCell className="abc-th abc-th--select" padding="checkbox">
                <Checkbox
                  size="small"
                  checked={allVisibleSelected}
                  indeterminate={partiallySelected}
                  onChange={toggleAllVisibleSelection}
                  disabled={!selectableItems.length || isSearching}
                  inputProps={{ "aria-label": "Select all visible ABC materials" }}
                />
              </TableCell>
              <TableCell className="abc-th">Item #</TableCell>
              <TableCell className="abc-th">Description</TableCell>
              <TableCell className="abc-th">Manufacturer</TableCell>
              <TableCell className="abc-th">Category</TableCell>
              <TableCell className="abc-th">UOM</TableCell>
              <TableCell className="abc-th">Price</TableCell>
              <TableCell className="abc-th">Status</TableCell>
              <TableCell className="abc-th abc-th--action" align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isSearching
              ? renderSkeletonRows()
              : items.map((item, idx) => {
                  const itemNumber = getItemNumber(item);
                  const desc = getDescription(item);
                  const manufacturer = getManufacturer(item);
                  const category = getCategory(item);
                  const uom = getUom(item);
                  const price = priceMap[itemNumber];
                  const displayPrice = getBestPrice(price, item);
                  const importedMaterial = getImportedMaterial(item);
                  const alreadyAdded = Boolean(importedMaterial);

                  return (
                    <TableRow
                      key={itemNumber || idx}
                      className={`abc-table-row${alreadyAdded ? " abc-table-row--added" : ""}`}
                      style={{ "--row-idx": idx } as React.CSSProperties}
                      onClick={() => toggleItemSelection(item)}
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") toggleItemSelection(item);
                      }}
                    >
                      <TableCell className="abc-td abc-td--select" padding="checkbox" onClick={(event) => event.stopPropagation()}>
                        <Checkbox
                          size="small"
                          checked={!!itemNumber && selectedItemNumbers.has(itemNumber)}
                          disabled={alreadyAdded}
                          onChange={() => toggleItemSelection(item)}
                          inputProps={{ "aria-label": `Select ${itemNumber || desc || "ABC material"}` }}
                        />
                      </TableCell>
                      <TableCell className="abc-td abc-td--sku"><span className="abc-sku-badge">{itemNumber}</span></TableCell>
                      <TableCell className="abc-td abc-td--desc">
                        <Tooltip title={desc} placement="top-start" enterDelay={600}>
                          <span className="abc-desc-text">{desc || "—"}</span>
                        </Tooltip>
                      </TableCell>
                      <TableCell className="abc-td">{manufacturer || "—"}</TableCell>
                      <TableCell className="abc-td">{category || "—"}</TableCell>
                      <TableCell className="abc-td abc-td--uom">{uom || "—"}</TableCell>
                      <TableCell className="abc-td abc-td--price">
                        {isFetchingTablePrices ? (
                          <span className="abc-price-loading"><CircularProgress size={12} /> Loading</span>
                        ) : (
                          <span className={displayPrice !== undefined ? "abc-inline-price" : "abc-inline-price abc-inline-price--empty"}>
                            {formatPrice(displayPrice)}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="abc-td">
                        {isLoadingImportedStatus ? (
                          <Chip label="Checking" size="small" className="abc-status-chip abc-status-chip--checking" />
                        ) : alreadyAdded ? (
                          <Chip label="Added" size="small" className="abc-status-chip abc-status-chip--added" />
                        ) : (
                          <Chip label="Not added" size="small" className="abc-status-chip abc-status-chip--new" />
                        )}
                      </TableCell>
                      <TableCell className="abc-td" align="right" onClick={(event) => event.stopPropagation()}>
                        <Button
                          size="small"
                          className="abc-view-btn"
                          endIcon={<OpenInNewRoundedIcon sx={{ fontSize: "14px !important" }} />}
                          onClick={(event) => {
                            event.stopPropagation();
                            handleSelectRow(item);
                          }}
                        >
                          View
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
          </TableBody>
        </Table>
      </TableContainer>
      </Box>
      <TablePagination
        component="div"
        count={totalItems}
        page={page - 1}
        rowsPerPage={pageSize}
        rowsPerPageOptions={PAGE_SIZE_OPTIONS}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
        className="abc-pagination"
      />
    </>
  );

  const displayItem = fullDetail ?? selectedItem;
  const detailItemNumber = displayItem ? getItemNumber(displayItem) : "";
  const detailDescription = displayItem ? getDescription(displayItem) : "";
  const detailManufacturer = displayItem ? getManufacturer(displayItem) : "";
  const detailCategory = displayItem ? getCategory(displayItem) : "";
  const detailUom = displayItem ? getUom(displayItem) : "";
  const detailPrice = detailItemNumber ? priceMap[detailItemNumber] : undefined;
  const detailPriceValue = getBestPrice(detailPrice, displayItem);
  const detailAlreadyAdded = detailItemNumber ? isMaterialAlreadyAdded(detailItemNumber) : false;

  const renderDrawerSpec = (icon: React.ReactNode, label: string, value?: string) =>
    value ? (
      <Stack direction="row" gap={1.5} alignItems="flex-start" className="abc-spec-row">
        <span className="abc-spec-icon">{icon}</span>
        <span>
          <Typography className="abc-spec-label">{label}</Typography>
          <Typography className="abc-spec-value">{value}</Typography>
        </span>
      </Stack>
    ) : null;

  const renderDetailDrawer = () => (
    <Drawer anchor="right" open={drawerOpen} onClose={handleCloseDrawer} PaperProps={{ className: "abc-drawer-paper", elevation: 0 }}>
      <Box className="abc-drawer-header">
        <Stack direction="row" alignItems="center" gap={1.5} flex={1} minWidth={0}>
          <QrCode2RoundedIcon className="abc-drawer-sku-icon" />
          <Box minWidth={0}>
            <Typography className="abc-drawer-sku" noWrap>{detailItemNumber || "Item Detail"}</Typography>
            <Typography className="abc-drawer-sku-sub">ABC Supply Product</Typography>
          </Box>
        </Stack>
        <IconButton onClick={handleCloseDrawer} className="abc-drawer-close" aria-label="Close">
          <CloseRoundedIcon />
        </IconButton>
      </Box>

      <Box className="abc-drawer-body">
        {isLoadingDetail ? (
          <Box className="abc-drawer-loading">
            <Stack gap={1.5}>
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} variant="rounded" height={36} />)}
            </Stack>
          </Box>
        ) : (
          <>
            <Box className="abc-drawer-desc-block">
              <Typography className="abc-drawer-desc">{detailDescription || "No description"}</Typography>
            </Box>
            <Divider className="abc-drawer-divider" />
            <Box className="abc-drawer-specs">
              {renderDrawerSpec(<BusinessRoundedIcon />, "Manufacturer", detailManufacturer || "Unknown")}
              {renderDrawerSpec(<CategoryRoundedIcon />, "Category", detailCategory)}
              {renderDrawerSpec(<StraightenRoundedIcon />, "Selling Unit", detailUom)}
              {renderDrawerSpec(<StorefrontRoundedIcon />, "Branch", branchNumber || displayItem?.branchNumber || "Default")}
              {displayItem?.manufacturerPartNumber && renderDrawerSpec(<QrCode2RoundedIcon />, "Manufacturer Part #", displayItem.manufacturerPartNumber)}
            </Box>
            <Divider className="abc-drawer-divider" />
            <Box className="abc-drawer-pricing">
              <Stack direction="row" alignItems="center" gap={1} mb={2}>
                <LocalOfferRoundedIcon className="abc-pricing-icon" />
                <Typography className="abc-pricing-title">Account Price</Typography>
                <Tooltip title="Price loads automatically from ABC Supply for visible results. It is account and branch-specific." placement="top">
                  <InfoOutlinedIcon className="abc-pricing-info" />
                </Tooltip>
              </Stack>
              <Box className="abc-price-result abc-price-result--static">
                <Typography className="abc-price-amount">{formatPrice(detailPriceValue)}</Typography>
                <Typography className="abc-price-per">
                  per {detailPrice?.uom || detailUom || "unit"} · {detailPrice ? `Fetched at ${detailPrice.fetchedAt}` : "Price not returned in the current ABC response"}
                </Typography>
                {detailPrice?.listPrice && <Typography className="abc-price-list">List: {formatPrice(detailPrice.listPrice)}</Typography>}
              </Box>
            </Box>
          </>
        )}
      </Box>

      <Box className="abc-drawer-footer">
        <Tooltip title={detailAlreadyAdded ? "This ABC Supply material is already in your material list." : addBlockReason || "Add this material to your FIELDCRAFT material list."} placement="top" arrow>
          <span style={{ display: "block", width: "100%" }}>
            <Button
              variant="contained"
              fullWidth
              disabled={isImportingToFIELDCRAFT || detailAlreadyAdded || !detailItemNumber || Boolean(addBlockReason)}
              className="abc-add-bom-btn"
              startIcon={isImportingToFIELDCRAFT ? <CircularProgress size={14} color="inherit" /> : <LayersRoundedIcon />}
              onClick={() => displayItem && handleImportSelectedToFIELDCRAFT([displayItem])}
            >
              {isImportingToFIELDCRAFT ? "Adding…" : detailAlreadyAdded ? "Already added to material list" : "Add this material to material list"}
            </Button>
          </span>
        </Tooltip>
        <Typography className="abc-drawer-coming-soon">
          Once added, this ABC material is saved to the material list and becomes available in normal BOM selection.
        </Typography>
      </Box>
    </Drawer>
  );

  const showResults = hasSearched && !searchError && !isSearching;
  const resultsLabel =
    hasSearched && !searchError
      ? isSearching
        ? "Searching…"
        : totalItems > 0
        ? `${totalItems.toLocaleString()} result${totalItems !== 1 ? "s" : ""}${lastSearchQuery ? ` for "${lastSearchQuery}"` : ""}`
        : null
      : null;

  return (
    <Box className="abc-shell">
      <Box className="abc-hero">
        {!hideHeader && (
          <Stack direction="row" alignItems="center" justifyContent="space-between" className="abc-hero-eyebrow" flexWrap="wrap" gap={1}>
            <Stack direction="row" alignItems="center" gap={1.5}>
              <Box className="abc-brand-mark">
                <img src={abcSupplyLogo} alt="ABC Supply" className="abc-brand-logo-img" />
              </Box>
              <Box>
                <Typography className="abc-brand-name">ABC Supply</Typography>
                <Typography className="abc-brand-sub">Material Catalog</Typography>
              </Box>
            </Stack>
            {effectiveBranchNumber && (
              <Stack direction="row" alignItems="center" gap={1} className="abc-ctx-tags">
                <Chip icon={<StorefrontRoundedIcon />} label={`Branch ${effectiveBranchNumber}`} size="small" className="abc-ctx-chip" />
              </Stack>
            )}
          </Stack>
        )}

        <Box className="abc-tab-rail">
          <Button
            className={`abc-tab-btn${activeTab === 'search' ? ' abc-tab-btn--active' : ''}`}
            startIcon={<SearchRoundedIcon />}
            onClick={() => setActiveTab('search')}
            disableRipple
          >
            Search Catalog
          </Button>
          <Button
            className={`abc-tab-btn${activeTab === 'library' ? ' abc-tab-btn--active' : ''}`}
            startIcon={<Inventory2RoundedIcon />}
            onClick={() => setActiveTab('library')}
            disableRipple
          >
            My Materials
            {addedMaterialsTotal > 0 && (
              <Box component="span" className="abc-tab-count">{addedMaterialsTotal}</Box>
            )}
          </Button>
        </Box>

        {activeTab === 'search' && (isLoadingVendors ? (
          <Box className="abc-hero-skeleton">
            <Skeleton variant="rounded" height={54} sx={{ bgcolor: "rgba(255,255,255,0.10)", borderRadius: "14px", mb: 1.5, width: "100%" }} />
            <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}>
              <Stack direction="row" gap={1.5}>
                <Skeleton variant="rounded" width={110} height={34} sx={{ bgcolor: "rgba(255,255,255,0.08)", borderRadius: "8px" }} />
                <Skeleton variant="rounded" width={110} height={34} sx={{ bgcolor: "rgba(255,255,255,0.08)", borderRadius: "8px" }} />
              </Stack>
              <Stack direction="row" gap={1.5} flexWrap="wrap">
                <Skeleton variant="rounded" width={130} height={34} sx={{ bgcolor: "rgba(255,255,255,0.08)", borderRadius: "8px" }} />
                <Skeleton variant="rounded" width={72} height={34} sx={{ bgcolor: "rgba(255,255,255,0.08)", borderRadius: "8px" }} />
                <Skeleton variant="rounded" width={60} height={34} sx={{ bgcolor: "rgba(255,255,255,0.08)", borderRadius: "8px" }} />
                <Skeleton variant="rounded" width={96} height={26} sx={{ bgcolor: "rgba(255,255,255,0.07)", borderRadius: "6px" }} />
                <Skeleton variant="rounded" width={108} height={26} sx={{ bgcolor: "rgba(255,255,255,0.07)", borderRadius: "6px" }} />
                <Skeleton variant="rounded" width={90} height={26} sx={{ bgcolor: "rgba(255,255,255,0.07)", borderRadius: "6px" }} />
              </Stack>
            </Stack>
          </Box>
        ) : (
          <>
            <Box className={`abc-search-stage${isItemNumActive ? " abc-search-stage--blocked" : ""}`}>
              <SearchRoundedIcon className="abc-search-prefix-icon" />
              <InputBase
                inputRef={searchInputRef}
                className="abc-search-input"
                placeholder={isItemNumActive ? "Clear item # below to search by keyword" : "Shingles, underlayment, flashing, ridge cap, roofing felt…"}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isItemNumActive}
                fullWidth
                inputProps={{ "aria-label": "Search ABC Supply catalog" }}
              />
              {isQueryActive && !isItemNumActive && (
                <Tooltip title="Clear keyword search" placement="top">
                  <IconButton size="small" className="abc-search-clear" onClick={() => setQuery("")} aria-label="Clear search">
                    <CloseRoundedIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
              <Button className="abc-search-submit" onClick={() => handleSearch(1)} disabled={isSearching || isItemNumActive} aria-label="Search">
                {isSearching ? <CircularProgress size={18} color="inherit" /> : <ArrowForwardRoundedIcon />}
              </Button>
            </Box>

            <Stack direction="column" gap={1.25} mt={1.5} className="abc-filter-row">
              {/* Row 1: Search refinements */}
              <Stack direction="row" alignItems="center" gap={1} sx={{ width: "100%" }}>
                <InputBase
                  className={`abc-filter-input${isQueryActive ? " abc-filter-input--blocked" : ""}`}
                  placeholder={isQueryActive ? "Clear search first" : "Item #"}
                  value={itemNumberQuery}
                  onChange={(event) => setItemNumberQuery(event.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={isQueryActive}
                  sx={{ flex: 1 }}
                  startAdornment={<InputAdornment position="start"><QrCode2RoundedIcon className="abc-filter-icon" /></InputAdornment>}
                  endAdornment={isItemNumActive ? (
                    <InputAdornment position="end">
                      <Tooltip title="Clear item #" placement="top">
                        <IconButton size="small" sx={{ padding: "2px", color: "rgba(255,255,255,0.5)" }} onClick={() => setItemNumberQuery("")}>
                          <CloseRoundedIcon sx={{ fontSize: "14px" }} />
                        </IconButton>
                      </Tooltip>
                    </InputAdornment>
                  ) : undefined}
                />
                <InputBase
                  className="abc-filter-input"
                  placeholder="Branch"
                  value={branchNumber}
                  onChange={(event) => setBranchNumber(event.target.value)}
                  onKeyDown={handleKeyDown}
                  sx={{ flex: 1 }}
                  startAdornment={<InputAdornment position="start"><StorefrontRoundedIcon className="abc-filter-icon" /></InputAdornment>}
                />
                <InputBase
                  className="abc-filter-input"
                  placeholder="Ship-To #"
                  value={shipToNumber}
                  onChange={(event) => setShipToNumber(event.target.value)}
                  onKeyDown={handleKeyDown}
                  sx={{ flex: 1 }}
                  startAdornment={<InputAdornment position="start"><BusinessRoundedIcon className="abc-filter-icon" /></InputAdornment>}
                />
              </Stack>

              {/* Row 2: Import settings */}
              <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap" sx={{ width: "100%" }}>
                <Box className={`abc-vendor-picker-box${!selectedVendor ? " abc-picker-required" : ""}`}>
                  <Autocomplete
                    size="small"
                    loading={isLoadingVendors}
                    options={vendorList}
                    value={selectedVendor}
                    onChange={(_, value) => setSelectedVendor(value)}
                    getOptionLabel={(option) => getVendorName(option) || "Unnamed vendor"}
                    isOptionEqualToValue={(option, value) => getVendorId(option) === getVendorId(value)}
                    onInputChange={resetListboxScroll(vendorListboxRef)}
                    ListboxProps={{ className: "abc-vendor-listbox", ref: vendorListboxRef }}
                    PaperComponent={(props) => <Paper {...props} className="abc-vendor-paper" />}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Vendor"
                        variant="standard"
                        InputProps={{
                          ...params.InputProps,
                          disableUnderline: true,
                          startAdornment: (
                            <>
                              <BusinessRoundedIcon className="abc-vendor-box-icon" />
                              {params.InputProps.startAdornment}
                            </>
                          ),
                          endAdornment: (
                            <>
                              {params.InputProps.endAdornment}
                            </>
                          ),
                        }}
                      />
                    )}
                  />
                  {vendorError && <Typography className="abc-vendor-error">{vendorError}</Typography>}
                </Box>
                <Box className={`abc-trade-picker-box${!selectedTrade ? " abc-picker-required" : ""}`}>
                  <Autocomplete
                    size="small"
                    loading={isLoadingTrades}
                    options={tradeList}
                    value={selectedTrade}
                    onChange={(_, value) => setSelectedTrade(value)}
                    getOptionLabel={(option) => getTradeName(option) || "Unnamed trade"}
                    isOptionEqualToValue={(option, value) => getTradeName(option) === getTradeName(value)}
                    onInputChange={resetListboxScroll(tradeListboxRef)}
                    ListboxProps={{ className: "abc-vendor-listbox", ref: tradeListboxRef }}
                    PaperComponent={(props) => <Paper {...props} className="abc-vendor-paper" />}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Trade"
                        variant="standard"
                        InputProps={{
                          ...params.InputProps,
                          disableUnderline: true,
                          startAdornment: (
                            <>
                              <CategoryRoundedIcon className="abc-vendor-box-icon" />
                              {params.InputProps.startAdornment}
                            </>
                          ),
                        }}
                      />
                    )}
                  />
                  {tradeError && <Typography className="abc-vendor-error">{tradeError}</Typography>}
                </Box>
                <Box className={`abc-toggle-setting-box${!hasInStockSelection ? " abc-picker-required" : ""}`}>
                  <Inventory2RoundedIcon className="abc-vendor-box-icon" />
                  <Typography className="abc-toggle-setting-label">In stock</Typography>
                  <Tooltip
                    title="Do you want this material marked as in stock?"
                    placement="top"
                    arrow
                  >
                    <InfoOutlinedIcon className="abc-toggle-info-icon" />
                  </Tooltip>
                  <ToggleButtonGroup
                    exclusive
                    size="small"
                    value={selectedInStock || null}
                    onChange={(_, value) => {
                      if (value !== null) setSelectedInStock(value as RequiredBooleanValue);
                    }}
                    className="abc-yesno-toggle"
                    aria-label="In stock"
                  >
                    <ToggleButton value="true" aria-label="In stock: Yes">Yes</ToggleButton>
                    <ToggleButton value="false" aria-label="In stock: No">No</ToggleButton>
                  </ToggleButtonGroup>
                </Box>
                <Box className={`abc-toggle-setting-box${!hasAllowDecimalSelection ? " abc-picker-required" : ""}`}>
                  <StraightenRoundedIcon className="abc-vendor-box-icon" />
                  <Typography className="abc-toggle-setting-label">Decimal qty</Typography>
                  <Tooltip
                    title="Do you want to allow decimal quantities (e.g. 2.5) for this material?"
                    placement="top"
                    arrow
                  >
                    <InfoOutlinedIcon className="abc-toggle-info-icon" />
                  </Tooltip>
                  <ToggleButtonGroup
                    exclusive
                    size="small"
                    value={selectedAllowDecimal || null}
                    onChange={(_, value) => {
                      if (value !== null) setSelectedAllowDecimal(value as RequiredBooleanValue);
                    }}
                    className="abc-yesno-toggle"
                    aria-label="Decimal quantity allowed"
                  >
                    <ToggleButton value="true" aria-label="Decimal qty: Yes">Yes</ToggleButton>
                    <ToggleButton value="false" aria-label="Decimal qty: No">No</ToggleButton>
                  </ToggleButtonGroup>
                </Box>
                <Divider orientation="vertical" flexItem className="abc-filter-divider" />
                <Select
                  value={pageSize}
                  onChange={(event) => setPageSize(Number(event.target.value))}
                  size="small"
                  variant="outlined"
                  className="abc-page-size-select"
                  inputProps={{ "aria-label": "Per page" }}
                  IconComponent={ExpandMoreRoundedIcon}
                  renderValue={(value) => `${value} / page`}
                  MenuProps={{
                    disableScrollLock: true,
                    classes: { paper: "abc-dropdown-menu-paper" },
                    anchorOrigin: { vertical: "bottom", horizontal: "right" },
                    transformOrigin: { vertical: "top", horizontal: "right" },
                  }}
                >
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <MenuItem key={size} value={size} className="abc-dropdown-menu-item">
                      <span>{size} per page</span>
                      {size === pageSize && (
                        <CheckRoundedIcon className="abc-dropdown-menu-check" fontSize="small" />
                      )}
                    </MenuItem>
                  ))}
                </Select>
                <Divider orientation="vertical" flexItem className="abc-filter-divider" />
                <FormControlLabel className="abc-family-toggle" control={<Switch checked={includeFamilyItems} onChange={(event) => setIncludeFamilyItems(event.target.checked)} size="small" className="abc-switch" />} label="Family items" />
              </Stack>
            </Stack>

            {(isUsingDefaultBranch || isUsingDefaultShipTo) && (
              <Stack direction="row" alignItems="center" gap={1.5} mt={0.75} className="abc-defaults-bar">
                {isUsingDefaultBranch && (
                  <Stack direction="row" alignItems="center" gap={0.5}>
                    <StorefrontRoundedIcon className="abc-defaults-bar-icon" />
                    <Typography className="abc-defaults-bar-label">Branch</Typography>
                    <Typography className="abc-defaults-bar-value">{defaultBranchNumber}</Typography>
                  </Stack>
                )}
                {isUsingDefaultShipTo && connectionDefaults?.shipToNumber && (
                  <Stack direction="row" alignItems="center" gap={0.5}>
                    <BusinessRoundedIcon className="abc-defaults-bar-icon" />
                    <Typography className="abc-defaults-bar-label">Ship-To</Typography>
                    <Typography className="abc-defaults-bar-value">{connectionDefaults.shipToNumber}</Typography>
                  </Stack>
                )}
                <Typography className="abc-defaults-bar-hint">using default</Typography>
              </Stack>
            )}
          </>
        ))}

      </Box>

      {activeTab === 'search' && (
        <Box className="abc-results-area">
          {resultsLabel && (
            <Box className="abc-results-toolbar">
              <Box>
                <Typography className="abc-results-count">{resultsLabel}</Typography>
                {priceMessage && <Typography className="abc-price-message">{priceMessage}</Typography>}
              </Box>
              <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
                {isLoadingImportedStatus && <Chip label="Checking added status" size="small" className="abc-selected-count-chip" />}
                {selectedItems.length > 0 && <Chip label={`${selectedItems.length} selected`} size="small" className="abc-selected-count-chip" />}
                <Tooltip title={addBlockReason} disableHoverListener={!addBlockReason} placement="top" arrow>
                  <span>
                    <Button
                      variant="contained"
                      size="small"
                      className="abc-add-selected-btn abc-add-selected-btn--smart"
                      disabled={isImportingToFIELDCRAFT || selectedItems.length === 0 || Boolean(addBlockReason)}
                      startIcon={isImportingToFIELDCRAFT ? <CircularProgress size={14} color="inherit" /> : <PlaylistAddCheckRoundedIcon />}
                      onClick={() => handleImportSelectedToFIELDCRAFT()}
                    >
                      {isImportingToFIELDCRAFT
                        ? "Adding to material list…"
                        : addBlockReason
                          ? "Complete import settings"
                          : selectedItems.length ? `Add ${selectedItems.length} to material list` : "Select materials to add"}
                    </Button>
                  </span>
                </Tooltip>
              </Stack>
            </Box>
          )}

          {renderImportResult()}
          {!importResult?.counts && renderResponsePanel()}

          {!hasSearched && renderIdleState()}
          {hasSearched && searchError && renderErrorState()}
          {showResults && items.length === 0 && !isSearching && renderEmptyState()}
          {(isSearching || (showResults && items.length > 0)) && renderResults()}
        </Box>
      )}

      {activeTab === 'library' && renderLibraryView()}

      {renderDetailDrawer()}

      <Snackbar open={!!toast} autoHideDuration={4000} onClose={() => setToast(null)} anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        {toast ? <Alert severity={toast.severity} onClose={() => setToast(null)} variant="filled">{toast.message}</Alert> : undefined}
      </Snackbar>
    </Box>
  );
};

export default ABCSupplyTable;
