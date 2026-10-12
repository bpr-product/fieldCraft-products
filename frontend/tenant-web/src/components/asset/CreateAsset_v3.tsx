/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Create Asset v2 Component - Handles asset creation and editing with dynamic SDM templates
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#31383       | 28-Sep-2026 |    Vaisnave     | The "Use customer billing address as asset
*                |             |                 | address" checkbox only rendered when the caller
*                |             |                 | threaded billingAddress through navigation
*                |             |                 | state (only Create Bid's TaskSelector did) — now
*                |             |                 | self-fetches it via customerUid when missing, so
*                |             |                 | it works from every entry point. Also: removed
*                |             |                 | the post-save redirect to Personal Info — Personal
*                |             |                 | Info now precedes Asset Creation in the
*                |             |                 | create-customer flow (see useCreateCustomerFlow.ts),
*                |             |                 | so onNextPage's fromCreateCustomerFlow branch was
*                |             |                 | unreachable and is gone
* AB#31383       | 29-Sep-2026 |    Vaisnave     | The billing-address checkbox now also renders
*                |             |                 | above the Asset Map, not only Property Details —
*                |             |                 | extracted into renderBillingAddressCheckbox so
*                |             |                 | both stay in sync off the same state
* AB#32415       | 30-Sep-2026 |     Steeve      | A map pick now also writes the picked address into
*                |             |                 | assetData (via utils/pickedAddress.ts); applying the
*                |             |                 | billing address drops the previously picked pin; the
*                |             |                 | OIDC token is read through a ref so a silent renewal
*                |             |                 | no longer remounts the map or re-seeds the form
*/

/**
 * To display the asset information in a wizard based ui
 * 
 * 
 */
import * as React from 'react';
import { convertLocation, isGoogleMapsGeocoderReady } from '../../utils/mapUtils';
import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import Box from '@mui/material/Box';
import {
    Alert,
    Button,
    Checkbox,
    Dialog,
    DialogActions,
    DialogContent,
    DialogContentText,
    DialogTitle,
    FormControlLabel,
    Grid,
    Snackbar,
    Typography,
    useMediaQuery,
    useTheme,
    Container,
    Paper,
    CircularProgress,
    Card,
    CardContent,
    Chip
} from '@mui/material';
import { SxProps, Theme } from '@mui/material/styles';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import Header from '../Header';
import './Asset.scss'
import { editAsset, saveAsset, getAsset, validatePropertyAddress } from '../../services/AssetService';
import AssetLocationPicker, { ParsedAddress } from './AssetLocationPicker';
import { useFlag } from '@openfeature/react-sdk';
import RoofLensAssetMap from './rooflens/RoofLensAssetMap';
import { PropertyValidationResult, RoofMetrics } from './rooflens/types';
import { RoofLensExportData } from './rooflens/exportRoofLens';
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import { useNavigate } from 'react-router';
import { useLocation } from 'react-router-dom';
import { saveAssetDocument } from '../../services/AssetDocumentService';
import CheckCircleOutlinedIcon from '@mui/icons-material/CheckCircleOutlined';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import MapOutlinedIcon from '@mui/icons-material/MapOutlined';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import "../createbid/selectorComponent/TaskSelector.scss";
import { getUserSession, useHandleUnauthorized, UserContext } from '../../hooks/UserSession';
import { getpersonalInfo } from '../../services/EmployeeService';
import { resolveBillingAddressForCustomer, CustomerBillingAddress } from '../../services/CustomerService';
import CustomStepper from '../common/CustomStepper';
import CustomDialog from '../../pages/Dialog';
import {
    ItemGroupDef,
    ItemDef,
    CodeList,
    ConditionDef,
    BasicDefinitions,
    MethodDef
} from '../../interface/SdmInterfaces';
import { getselectedTemplatesByVersion, getTemplatesByTradeName } from '../../services/TradeTemplateService';
import { resolveMergedTemplateVersion } from '../../utils/sdmMergeUtils';

import QuestionRenderer_v2 from '../../pages/QuestionRenderer/QuestionRenderer_v2';
import { findDuplicateAsset } from '../../utils/assetDuplicate';
import { buildPickedAddress, PICK_ADDRESS_KEYS } from '../../utils/pickedAddress';

// Alias MetaKeys for RoofLens-measurement-derived fields — shared between the live
// per-facet auto-fill (handleMetricsComputed) and handleLocationPicked's "stale prior
// address" clear, so the two lists can never drift apart. Mirrors the backend's own
// ROOF_DETAIL_META_KEY_ALIASES (EstimateAutomationService.java) for the roof-detail
// metrics, plus 'area' (handled separately from those on the backend, but lives
// alongside them here since both are Auto-Detect-derived answers on this page).
const MEASUREMENT_METAKEY_ALIASES: Record<string, string[]> = {
    area: ['area'],
    predominantPitch: ['predominantPitch', 'pitch', 'roofPitch'],
    ridge: ['ridgeLength', 'ridge'],
    hip: ['hipLength', 'hip'],
    ridgeHip: ['ridgeHipLength', 'ridgesHips'],
    valley: ['valleyLength', 'valley'],
    rake: ['rakeLength', 'rake'],
    eave: ['eaveLength', 'eave'],
    attic: ['estimatedAttic', 'atticSqft', 'attic'],
    stories: ['stories', 'numberOfStories', 'noOfStories'],
    height: ['buildingHeight', 'height'],
    complexity: ['structureComplexity', 'complexity'],
};

const CreateAsset_v3: React.FC = () => {

    // Create ref for QuestionRendererShared_v2
    const questionRendererRef = useRef<any>(null);
    // Holds the exact "lat,lng" the user pinned on the map; when set it takes
    // precedence over address geocoding (ADO 22357 / C9).
    const pickedLocationRef = useRef<string | null>(null);
    // Latest finalized RoofLens measurement (asset.measurement_data), fed by the
    // map's onMeasurementChange. Attached to the asset payload on save.
    const roofLensMeasurementRef = useRef<RoofLensExportData | null>(null);
    // Tri-state flag: true once the user has touched the RoofLens panel this
    // session. Distinguishes "panel untouched → preserve stored measurement" from
    // "user emptied the panel → clear stored measurement" when the ref is null.
    const measurementTouchedRef = useRef(false);

    // Stable, unique identity for a never-saved asset. Without this, every unsaved asset
    // falls back to the literal 'new' as its RoofLens assetKey, and the module-scope reset /
    // auto-detect guards (keyed by assetKey, surviving SPA navigation) can't tell one new
    // asset from the next in the same session — so Asset B would inherit Asset A's stale
    // facets and skip its own auto-detect. A per-instance key makes the identity-change
    // reset fire correctly; saved assets still key off their real assetUid/id.
    const [sessionAssetKey] = useState(() =>
        (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
            ? crypto.randomUUID()
            : `new-${Date.now()}-${Math.random().toString(36).slice(2)}`
    );

    // Map inside the v3 Asset page: ON → RoofLens roof-measurement map; OFF → the legacy
    // v2 AssetLocationPicker map. Gated separately from FEATURE_FLAGS.CreateAsset_version
    // so the ToS-sensitive RoofLens map can be turned off while keeping the v3 page.
    const { value: roofLensMapEnabled } = useFlag("rooflens-asset-map", false);
    // AB#28854 — off by default (FeatureFlag.ts); gates both the pre-save address/property
    // check below and the picker-level check inside RoofLensAssetMap. Flag OFF must leave the
    // save flow completely unchanged.
    const { value: addressValidationEnabled } = useFlag("rooflens-address-validation", false);

    let location = useLocation()
    const { accessToken, accessTokenPayload } = useOidcAccessToken();
    const [open, setOpen] = useState(false);
    const [count, setCount] = useState(0);
    const [showBillingAddress, setShowBillingAddress] = useState(true);
    const { handleUnauthorized } = useHandleUnauthorized();
    const [isSaving, setIsSaving] = useState(false);

    // Helper: Convert asset data to format with BOTH ODM structure AND flat fields
    const convertToODMFormat = (assetData: any) => {

        // Build ItemData array dynamically from dynamicQuestionData
        const itemData: any[] = [];

        // Iterate through dynamicQuestionData to create ItemData entries
        Object.keys(dynamicQuestionData).forEach(itemOID => {
            const itemDef = assetItemDefs.find(item => item.OID === itemOID);
            const value = dynamicQuestionData[itemOID];

            if (itemDef) {
                // Use MetaKey from ItemDef, fallback to OID if not defined
                const metaKey = itemDef.MetaKey || itemOID;

                // Determine if this is a single-select or multi-select by checking CodeListRef
                const hasCodeList = itemDef.CodeListRef !== undefined;

                if (hasCodeList) {
                    // For single-select/multi-select, value might be the text, need to find code
                    const codeListOID = itemDef.CodeListRef?.CodeListOID;
                    const codeList = assetCodeListDefs.find(cl => cl.OID === codeListOID);
                    const codeListItems = codeList?.CodeListItem || [];

                    // Find the matching code(s) for the value
                    const matchingCodes: string[] = [];
                    if (Array.isArray(value)) {
                        // Multi-select
                        value.forEach(val => {
                            const codeItem = codeListItems.find(
                                item => item.CodedValue === val || item.Decode?.TranslatedText?.text === val
                            );
                            if (codeItem) {
                                matchingCodes.push(codeItem.CodedValue);
                            }
                        });
                    } else {
                        // Single-select
                        const codeItem = codeListItems.find(
                            item => item.CodedValue === value || item.Decode?.TranslatedText?.text === value
                        );
                        if (codeItem) {
                            matchingCodes.push(codeItem.CodedValue);
                        }
                    }

                    itemData.push({
                        ItemOID: itemOID,
                        Value: Array.isArray(value) ? value.join(', ') : String(value || ''),
                        CodeList: matchingCodes,
                        MetaKey: metaKey,
                        ItemGroupRepeatKey: 0
                    });
                } else {
                    // Regular text/integer/float field
                    itemData.push({
                        ItemOID: itemOID,
                        Value: String(value || ''),
                        CodeList: [],
                        MetaKey: metaKey,
                        ItemGroupRepeatKey: 0
                    });
                }
            } else {
                // console.warn(`No ItemDef found for ${itemOID}, skipping...`);
            }
        });


        // Create ODM structure with dynamically generated ItemData
        const odmStructure = {
            EventData: [{
                FormData: [{
                    ItemGroupData: [{
                        ItemData: itemData,
                        ItemGroupOID: assetItemGroupDefs[0]?.OID || "IG.1",
                        ItemGroupRepeatKey: 0
                    }],
                    FormOID: "F.1",
                    TransactionType: "Insert"
                }],
                EventOID: "E.1"
            }],
            OID: "A.1",
            Key: "123",
            MetaDataVersionOID: assetQuestionData?.OID
        };

        // Destructure to get all fields EXCEPT old assetTemplateData
        const { assetTemplateData: oldTemplate, ...restOfAssetData } = assetData;

        // Return payload with BOTH ODM structure AND ALL fields from assetData
        // Fix empty strings for numeric fields - convert to null or 0
        const hybridPayload = {
            // Use our NEW ODM structure (not the old one from SDM form)
            assetTemplateData: JSON.stringify(odmStructure),

            // Copy ALL fields from assetData EXCEPT old assetTemplateData
            ...restOfAssetData,

            // Fix numeric fields - backend expects numbers or null, not empty strings
            area: assetData.area || 0,
            stories: assetData.stories || 0,
            location: assetData.location || null, // null instead of empty string if backend expects number

            // Override timestamps to ensure they're fresh
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString()
        };

        return hybridPayload;
    };

    // SDM Data States
    const [assetItemGroupDefs, setAssetItemGroupDefs] = useState<ItemGroupDef[]>([]);
    const [assetItemDefs, setAssetItemDefs] = useState<ItemDef[]>([]);
    const [assetCodeListDefs, setAssetCodeListDefs] = useState<CodeList[]>([]);
    const [assetConditionDefs, setAssetConditionDefs] = useState<ConditionDef[]>([]);
    const [assetMethodDefs, setAssetMethodDefs] = useState<MethodDef[]>([]);
    const [assetMeasureDef, setAssetMeasureDef] = useState<BasicDefinitions>();
    const [assetOID, setAssetOID] = useState<string>("");
    const [isLoadingTemplate, setIsLoadingTemplate] = useState(true);

    // Dynamic question data
    const isSyncingRef = useRef(false);

    const [dynamicQuestionData, setDynamicQuestionData] = useState<any>(() => {
        const existingData = location.state?.assetObjectData?.assetTemplateData;
        if (existingData) {
            try {
                const parsed = JSON.parse(existingData);
                const itemData = parsed?.EventData?.[0]?.FormData?.[0]?.ItemGroupData
                    ?.flatMap((group: any) => group.ItemData || []);
                const dataObj: any = {};
                itemData?.forEach((item: any) => {
                    if (item.Value !== undefined && item.Value !== null && item.Value !== '') {
                        // Differentiate between single-select and multi-select:
                        // - Single-select: CodeList has 1 item, Value has no comma
                        // - Multi-select: CodeList has multiple items OR Value contains comma
                        if (item.CodeList && Array.isArray(item.CodeList) && item.CodeList.length > 0) {
                            // Check if this is multi-select (multiple selections or comma in value)
                            const isMultiSelect = item.CodeList.length > 1 ||
                                (typeof item.Value === 'string' && item.Value.includes(','));

                            if (isMultiSelect) {
                                // Multi-select: use the CodeList array
                                dataObj[item.ItemOID] = item.CodeList;
                            } else {
                                // Single-select: use the first (and only) CodeList item as a string
                                dataObj[item.ItemOID] = item.CodeList[0];
                            }
                        } else {
                            dataObj[item.ItemOID] = item.Value;  // Use the value for non-coded types
                        }
                    }
                });
                return dataObj;
            } catch (e) {
                return {};
            }
        }
        return {};
    });

    // Stable callback to prevent infinite loop with child component
    const handleDataChange = useCallback((data: any) => {
        if (!isSyncingRef.current) {
            isSyncingRef.current = true;
            setDynamicQuestionData(data);
            // Reset flag after a short delay to allow the update cycle to complete
            setTimeout(() => {
                isSyncingRef.current = false;
            }, 100);
        } else {
console.warn('Skipping update to dynamicQuestionData to prevent infinite loop'); }
    }, []);

    // Stepper state - will be dynamically generated from ItemGroupDefs
    const [activeStep, setActiveStep] = useState(0);
    const [steps, setSteps] = useState<any[]>([]);

    const [message, setMessage] = useState<any>({
        name: '',
        country: '',
        zipCode: '',
        state: '',
        address: '',
        city: '',
        stories: null,
        area: '',
        checkbox: false,
    });

    const { action, customerUid, billingAddress: locationBillingAddress } = location.state || {};
    // AB#31383 — every caller except Create Bid's "add property" flow (TaskSelector_v2/v3.tsx)
    // opens this wizard without threading the customer's billing address through navigation
    // state, so the "Use customer billing address" checkbox below silently never appeared for
    // them (CustomerPortal's Add/Edit Asset, the Admin Asset list). Falling back to the existing
    // asset's own customerUid (edit mode) covers callers that pass assetObjectData but no
    // top-level customerUid.
    const billingAddressCustomerUid = customerUid || location.state?.assetObjectData?.customerUid;
    const [fetchedBillingAddress, setFetchedBillingAddress] = useState<CustomerBillingAddress | null>(null);
    useEffect(() => {
        if (locationBillingAddress || !billingAddressCustomerUid) return;
        let cancelled = false;
        resolveBillingAddressForCustomer(billingAddressCustomerUid, accessToken).then(result => {
            if (!cancelled) setFetchedBillingAddress(result);
        });
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [billingAddressCustomerUid, locationBillingAddress]);
    const customerBillingAddress = locationBillingAddress || fetchedBillingAddress || null;

    const hasBillingAddress = !!(
        customerBillingAddress &&
        (customerBillingAddress.address || customerBillingAddress.city ||
         customerBillingAddress.state || customerBillingAddress.country ||
         customerBillingAddress.zipCode)
    );

    const [useBillingAddress, setUseBillingAddress] = useState(false);

    const getOIDByMetaKey = useCallback((metaKey: string): string | null => {
        const found = assetItemDefs.find(
            (item) => item.MetaKey?.toLowerCase() === metaKey.toLowerCase()
        );
        return found?.OID ?? null;
    }, [assetItemDefs]);


    const handleUseBillingAddressChange = useCallback((checked: boolean) => {
        setUseBillingAddress(checked);

        if (checked && customerBillingAddress) {
            const addressFields: Record<string, string> = {
                address: customerBillingAddress.address || '',
                city: customerBillingAddress.city || '',
                state: customerBillingAddress.state || '',
                country: customerBillingAddress.country || '',
                zipCode: customerBillingAddress.zipCode || '',
            };

            setDynamicQuestionData((prev: any) => {
                const updated = { ...prev };
                Object.entries(addressFields).forEach(([metaKey, value]) => {
                    const oid = getOIDByMetaKey(metaKey);
                    if (oid && value) {
                        updated[oid] = value;
                    }
                });
                return updated;
            });
            // RoofLensAssetMap's `address` prop (CreateAsset_v3.tsx render, ~line 1841) is built
            // from assetData.address/city/state/zipCode, NOT dynamicQuestionData — without also
            // updating assetData here, the map never learns the billing address was applied, so
            // its search box/geocode/auto-detect never fire. Clear `location` too: a stale saved
            // pin would otherwise win over the new address in the map's own effect (it prefers an
            // existing pin over geocoding), leaving the map centered on the OLD location.
            // Drop any pin picked for the PREVIOUS address too, or resolveLocation() at save time
            // would write that old pin next to the billing address (the map itself re-derives it).
            pickedLocationRef.current = null;
            setAssetData((prev: any) => ({
                ...prev,
                address: addressFields.address,
                city: addressFields.city,
                state: addressFields.state,
                country: addressFields.country,
                zipCode: addressFields.zipCode,
                location: undefined,
            }));
        } else if (!checked) {
            // Clear billing address fields
            setDynamicQuestionData((prev: any) => {
                const updated = { ...prev };
                PICK_ADDRESS_KEYS.forEach((metaKey) => {
                    const oid = getOIDByMetaKey(metaKey);
                    if (oid) {
                        updated[oid] = '';
                    }
                });
                return updated;
            });
            setAssetData((prev: any) => ({
                ...prev,
                address: '',
                city: '',
                state: '',
                country: '',
                zipCode: '',
            }));
        }
    }, [customerBillingAddress, getOIDByMetaKey]);

    // AB#31383 — shown both above the Asset Map (so checking it also re-centers/searches the map,
    // since handleUseBillingAddressChange above updates assetData.address/city/state/zipCode,
    // which RoofLensAssetMap's `address` prop is built from) and above Property Details' own
    // question fields; `idSuffix` keeps the two rendered checkboxes' ids distinct, since both can
    // be on screen at once.
    const renderBillingAddressCheckbox = (idSuffix: string, sx: SxProps<Theme>) => hasBillingAddress && (
        <FormControlLabel
            control={
                <Checkbox
                    id={`create-asset-v2-checkbox-use-billing-address-${idSuffix}`}
                    checked={useBillingAddress}
                    onChange={(e) => handleUseBillingAddressChange(e.target.checked)}
                    color="primary"
                    size="small"
                />
            }
            label={
                <Typography variant="body2" sx={{ fontWeight: 500, color: 'text.secondary' }}>
                    Use customer billing address as asset address&nbsp;
                    <Typography
                        component="span"
                        variant="body2"
                        sx={{ color: 'text.disabled', fontStyle: 'italic' }}
                    >
                        ({[
                            customerBillingAddress?.address,
                            customerBillingAddress?.city,
                            customerBillingAddress?.state,
                            customerBillingAddress?.zipCode,
                            customerBillingAddress?.country,
                        ].filter(Boolean).join(', ')})
                    </Typography>
                </Typography>
            }
            sx={sx}
        />
    );

    const [assetData, setAssetData] = useState<any>({
        address: "",
        name: "",
        country: "",
        zipCode: '',
        state: "",
        city: "",
        location: "",
        assetType: "Residential",
        stories: 0,
        status: "Active",
        customerUid: (customerUid && customerUid !== '') ? customerUid : accessTokenPayload?.oid,
        area: 0,
        checkbox: false,
        createdAt: new Date(),
        updatedAt: new Date(),
        isBilling: false,
        assetTemplateData: ''
    });

    const componentData = useRef<any>(null)
    // A silent OIDC renewal changes `accessToken` mid-edit. Depending on it would refetch the template (the
    // spinner unmounts the whole wizard, map included) and re-seed assetData over the user's edits, so the
    // effects below read it through this ref and depend only on `hasAccessToken` (L-WEB-047).
    const accessTokenRef = useRef(accessToken);
    accessTokenRef.current = accessToken;
    const hasAccessToken = !!accessToken;
    const theme = useTheme();
    const smScreen = useMediaQuery(theme.breakpoints.up("sm"));
    let navigate = useNavigate()
    // Image upload UI removed in v3 (the RoofLens map replaces it); `image` stays
    // null so the submit's optional file-upload branch is simply skipped.
    const [image] = useState<any>(null);
    const [toastMessage, setToastMessage] = useState('');
    const assetDataObject = location.state?.assetObjectData;
    const [success, setSuccess] = useState(false);
    const [error, setError] = useState(false);
    const [noChange, setNoChange] = useState(false);
    const [isBillingAddress, setIsBillingAdress] = useState(false);
    const [hasData, setHasData] = useState(false);

    const [update, setUpdate] = useState(false);
    const [createdAssetUid, setCreatedAssetUid] = useState<string | null>(null); // Store newly created asset UID
    const [createdAssetName, setCreatedAssetName] = useState<string | null>(null); // Store newly created asset name

    const [assetIds, setAssetIds] = useState<any>({});
    const [errorCode, setErrorCode] = useState<any>(undefined);
    const [errorReason, setErrorReason] = useState<any>(undefined);
    const [errorMessage, setErrorMessage] = useState<any>(undefined);
    const [timestamp, setTimestamp] = useState<any>(undefined);
    const [openDialogs, setOpenDialogs] = useState(false);
    const [openUpdateDialog, setOpenUpdateDialog] = useState(false);
    const [updateSuccess, setUpdateSuccess] = useState(false);
    const [updateError, setUpdateError] = useState(false);
    const [createSuccess, setCreateSuccess] = useState(false);
    const [createError, setCreateError] = useState(false);
    const [successType, setSuccessType] = useState('');
    const [validationErrors, setValidationErrors] = useState<string[]>([]);
    // AB#28854 — non-blocking "couldn't verify this address" notice, separate from the blocking
    // validationErrors list: an UNVERIFIED result still lets the save proceed (fail-open), so it
    // must not render inside the "Please fix the following errors" block.
    const [addressWarning, setAddressWarning] = useState<string | null>(null);
    const [duplicateDialogOpen, setDuplicateDialogOpen] = useState(false);
    const [duplicateDialogMessage, setDuplicateDialogMessage] = useState('');
    const [assetQuestionData, setAssetQuestionData] = useState<any>(null);

    // Constants
    const ASSET_OID = "A.1";
    const QUESTION_STATUS = "publish";
    const DEFAULT_EVENTOID = "E.1";
    const DEFAULT_FORMOID = "F.1";
    // convertLocation is imported from the shared ../../utils/mapUtils (null-safe) —
    // was a duplicated local copy; consolidated to a single source.

    // The template follows the asset's template version, not the auth token.
    const templateVersion = assetDataObject?.assetQuestionTemplateVersion;

    // Fetch SDM Template Data
    useEffect(() => {
        const fetchAssetTemplate = async () => {
            try {
                setIsLoadingTemplate(true);
                let response;

                // Check if editing existing asset with specific version
                if (templateVersion) {
                    const resp = await getselectedTemplatesByVersion(ASSET_OID, templateVersion);
                    if (resp?.data?.questionTemplateData) {
                        response = JSON.parse(resp.data.questionTemplateData);
                    }
                } else {
                    // Fetch latest template for new asset
                    const resp = await getTemplatesByTradeName(ASSET_OID, QUESTION_STATUS, accessTokenRef.current);
                    if (resp?.data?.questionTemplateData) {
                        response = JSON.parse(resp.data.questionTemplateData);
                    }
                }

                if (response) {
                    const latestVersion = resolveMergedTemplateVersion(
                        response?.SDM?.Building?.TemplateVersion
                    );

                    if (latestVersion) {
                        const eventDef = latestVersion?.EventDef?.find((event: any) => event.OID === DEFAULT_EVENTOID);
                        const formDef = latestVersion?.FormDef?.find((form: any) => form.OID === DEFAULT_FORMOID);

                        // Extract ItemGroupDefs
                        const itemGroupDefs: ItemGroupDef[] = [];
                        formDef?.ItemGroupRef?.forEach((igRef: any) => {
                            const itemGroup = latestVersion?.ItemGroupDef?.find(
                                (ig: ItemGroupDef) => ig.OID === igRef.ItemGroupOID
                            );
                            if (itemGroup) {
                                itemGroupDefs.push(itemGroup);
                            }
                        });

                        // Extract ItemDefs from ItemGroupDefs
                        const itemDefs: ItemDef[] = [];
                        itemGroupDefs.forEach((itemGroup: ItemGroupDef) => {
                            itemGroup.ItemRef?.forEach((itemRef: any) => {
                                const itemDef = latestVersion?.ItemDef?.find(
                                    (item: ItemDef) => item.OID === itemRef.ItemOID
                                );
                                if (itemDef) {
                                    itemDefs.push(itemDef);
                                }
                            });
                        });

                        setAssetQuestionData(latestVersion)
                        setAssetItemGroupDefs(itemGroupDefs);
                        setAssetItemDefs(itemDefs);
                        setAssetCodeListDefs(latestVersion?.CodeList || []);
                        setAssetConditionDefs(latestVersion?.ConditionDef || []);
                        setAssetMethodDefs(latestVersion?.MethodDef || []);
                        setAssetOID(response?.SDM?.Building?.OID || "");

                        // Initial steps setup (completion will be calculated dynamically by useMemo)
                        const initialSteps = itemGroupDefs.map((group, index) => ({
                            id: group.OID,
                            title: group.Description?.TranslatedText?.text || group.Name || `Section ${index + 1}`,
                            description: group.Description?.TranslatedText?.text || '',
                            completed: false // Will be dynamically calculated
                        }));

                        setSteps(initialSteps);
                    }
                }
            } catch (error) {
                // Surface the failure instead of swallowing it — otherwise the wizard
                // renders with an empty template (steps=[]) and Save silently no-ops.
                console.error('Error fetching asset template:', error);
                setAssetQuestionData(null);
            } finally {
                setIsLoadingTemplate(false);
            }
        };

        if (hasAccessToken) {
            fetchAssetTemplate();
        }
    }, [hasAccessToken, templateVersion]);

    useEffect(() => {
        getUserSession(accessTokenRef.current).then(res => {
            getpersonalInfo(res.email, accessTokenRef.current).then(response => {
                handleUnauthorized(response)
                if (response.status === 200 || response.status === 201) {
                    if (response.data === true) {
                        setHasData(true)
                    }
                }
            })

        });
        // callParentComponent();
        const assetDataObject = location.state?.assetObjectData;

        if (assetDataObject) {
            if (assetDataObject?.isBilling === true) {
                setShowBillingAddress(false);
            }
            setAssetData(assetDataObject);
            componentData.current = convertLocation(assetDataObject.location);
        }
        if (assetData.isBilling === true) {
            if (!update) {
                handleSubmit();

            }
            else {
                updateHandleSubmit(assetIds.assetId);
            }
        }
    }, [hasAccessToken, location.state, assetData.isBilling])

    useEffect(() => {

    }, [assetData.location])

    // Helper function to check a single condition
    const checkSingleCondition = useCallback((itemOID: string, expectedValue: string): boolean => {
        // Check in dynamicQuestionData
        const currentValue = dynamicQuestionData[itemOID];

        if (currentValue !== null && currentValue !== undefined && currentValue !== '') {
            // Handle boolean value mapping for Yes/No questions
            if (expectedValue === 'Yes' && (currentValue === 'Yes' || currentValue === '1' || currentValue === 'true')) {
                return true;
            }
            if (expectedValue === 'No' && (currentValue === 'No' || currentValue === '2' || currentValue === 'false')) {
                return true;
            }
            // Direct value comparison
            return String(currentValue) === String(expectedValue);
        }

        return false;
    }, [dynamicQuestionData]);

    // Helper function to evaluate condition expressions
    const evaluateConditionExpression = useCallback((conditionText: string): boolean => {
        if (!conditionText) return true;

        const isNegated = conditionText.startsWith('!');
        const cleanExpression = conditionText.replace(/[!()]/g, '').trim();

        // Handle OR conditions
        if (cleanExpression.includes('||')) {
            const orParts = cleanExpression.split('||');
            let anyConditionMet = false;

            for (const part of orParts) {
                const [itemOID, expectedValue] = part.split('==').map(s => s.trim().replace(/"/g, ''));
                if (checkSingleCondition(itemOID, expectedValue)) {
                    anyConditionMet = true;
                    break;
                }
            }

            return isNegated ? !anyConditionMet : anyConditionMet;
        }

        // Handle AND conditions
        if (cleanExpression.includes('&&')) {
            const andParts = cleanExpression.split('&&');
            let allConditionsMet = true;

            for (const part of andParts) {
                const [itemOID, expectedValue] = part.split('==').map(s => s.trim().replace(/"/g, ''));
                if (!checkSingleCondition(itemOID, expectedValue)) {
                    allConditionsMet = false;
                    break;
                }
            }

            return isNegated ? !allConditionsMet : allConditionsMet;
        }

        // Handle simple conditions
        const [itemOID, expectedValue] = cleanExpression.split('==').map(s => s.trim().replace(/"/g, ''));
        const conditionMet = checkSingleCondition(itemOID, expectedValue);

        return isNegated ? !conditionMet : conditionMet;
    }, [checkSingleCondition]);

    // Check if a question should be shown based on conditions
    const shouldShowQuestionWithData = useCallback((itemDef: ItemDef, itemRef: any): boolean => {
        if (!itemRef?.CollectionExceptionConditionOID) {
            return true;
        }

        const condition = assetConditionDefs.find(c => c.OID === itemRef.CollectionExceptionConditionOID);
        if (!condition || !condition.FormalExpression?.text) {
            return true;
        }

        // IMPORTANT: CollectionExceptionCondition defines when to HIDE a question
        // So we need to INVERT the condition result:
        // - If condition is TRUE → HIDE question (return false)
        // - If condition is FALSE → SHOW question (return true)
        const conditionResult = evaluateConditionExpression(condition.FormalExpression.text);
        return !conditionResult; // INVERT: CollectionExceptionCondition = hide when true
    }, [assetConditionDefs, evaluateConditionExpression]);

    // Clear dependent questions when a question value changes
    const clearDependentQuestions = useCallback((changedItemOID: string, updatedData: any) => {
        let hasChanges = false;
        let clearedData = updatedData;

        // Temporarily update dynamicQuestionData for evaluation
        const tempData = { ...dynamicQuestionData, ...updatedData };

        assetItemGroupDefs.forEach(itemGroup => {
            itemGroup.ItemRef?.forEach((itemRef: any) => {
                if (!itemRef.CollectionExceptionConditionOID) return;

                const condition = assetConditionDefs.find(c => c.OID === itemRef.CollectionExceptionConditionOID);
                if (!condition?.FormalExpression?.text) return;

                const conditionText = condition.FormalExpression.text;
                if (conditionText.includes(changedItemOID)) {
                    // IMPORTANT: CollectionExceptionCondition defines when to HIDE
                    // So we INVERT the result: if condition is TRUE → HIDE (clear the field)
                    const conditionResult = evaluateConditionExpressionWithData(conditionText, tempData);
                    const shouldShow = !conditionResult; // INVERT: hide when condition is true


                    if (!shouldShow && clearedData[itemRef.ItemOID] !== undefined && clearedData[itemRef.ItemOID] !== '') {
                        if (!hasChanges) {
                            clearedData = { ...updatedData };
                            hasChanges = true;
                        }
                        // Find the item to check if it's multiselect
                        const itemDef = assetItemDefs.find(item => item.OID === itemRef.ItemOID);
                        clearedData[itemRef.ItemOID] = itemDef?.DataType === 'multiselect' ? [] : '';
                    }
                }
            });
        });

        return hasChanges ? clearedData : updatedData;
    }, [assetItemGroupDefs, assetItemDefs, assetConditionDefs, dynamicQuestionData]);

    // Helper to evaluate conditions with specific data (for clearDependentQuestions)
    const evaluateConditionExpressionWithData = (conditionText: string, dataToCheck: any): boolean => {
        if (!conditionText) return true;

        const checkValue = (itemOID: string, expectedValue: string): boolean => {
            const currentValue = dataToCheck[itemOID];

            if (currentValue !== null && currentValue !== undefined && currentValue !== '') {
                if (expectedValue === 'Yes' && (currentValue === 'Yes' || currentValue === '1' || currentValue === 'true')) {
                    return true;
                }
                if (expectedValue === 'No' && (currentValue === 'No' || currentValue === '2' || currentValue === 'false')) {
                    return true;
                }
                return String(currentValue) === String(expectedValue);
            }

            return false;
        };

        const isNegated = conditionText.startsWith('!');
        const cleanExpression = conditionText.replace(/[!()]/g, '').trim();

        if (cleanExpression.includes('||')) {
            const orParts = cleanExpression.split('||');
            let anyConditionMet = false;

            for (const part of orParts) {
                const [itemOID, expectedValue] = part.split('==').map(s => s.trim().replace(/"/g, ''));
                if (checkValue(itemOID, expectedValue)) {
                    anyConditionMet = true;
                    break;
                }
            }

            return isNegated ? !anyConditionMet : anyConditionMet;
        }

        if (cleanExpression.includes('&&')) {
            const andParts = cleanExpression.split('&&');
            let allConditionsMet = true;

            for (const part of andParts) {
                const [itemOID, expectedValue] = part.split('==').map(s => s.trim().replace(/"/g, ''));
                if (!checkValue(itemOID, expectedValue)) {
                    allConditionsMet = false;
                    break;
                }
            }

            return isNegated ? !allConditionsMet : allConditionsMet;
        }

        const [itemOID, expectedValue] = cleanExpression.split('==').map(s => s.trim().replace(/"/g, ''));
        const conditionMet = checkValue(itemOID, expectedValue);

        return isNegated ? !conditionMet : conditionMet;
    };

    // Calculate completion status for all sections
    const calculateCompletionStatus = useCallback(() => {
        const sectionStatus: Array<{ sectionIndex: number; completed: boolean; answeredCount: number; totalCount: number }> = [];

        assetItemGroupDefs.forEach((groupDef, index) => {
            const allQuestions: Array<{ itemDef: any; itemRef: any }> = [];
            groupDef.ItemRef?.forEach(itemRef => {
                const itemDef = assetItemDefs.find(item => item.OID === itemRef.ItemOID);
                if (itemDef) {
                    allQuestions.push({ itemDef, itemRef });
                }
            });

            // Filter to only visible questions (respecting conditional visibility)
            const visibleQuestions = allQuestions.filter(q => shouldShowQuestionWithData(q.itemDef, q.itemRef));

            // Count answered questions
            const answeredCount = visibleQuestions.filter(q => {
                const value = dynamicQuestionData[q.itemDef.OID];
                return value !== null && value !== undefined && value !== '' &&
                    (!Array.isArray(value) || value.length > 0);
            }).length;

            const isComplete = answeredCount === visibleQuestions.length && visibleQuestions.length > 0;

            sectionStatus.push({
                sectionIndex: index,
                completed: isComplete,
                answeredCount,
                totalCount: visibleQuestions.length
            });
        });

        return sectionStatus;
    }, [assetItemGroupDefs, assetItemDefs, dynamicQuestionData, shouldShowQuestionWithData]);

    // Get visible questions for current step
    // Calculate dynamic steps with completion status
    const dynamicSteps = useMemo(() => {
        if (assetItemGroupDefs.length === 0) {
            return [];
        }

        // Calculate completion status directly based on answered questions
        const completionStatus = calculateCompletionStatus();

        return assetItemGroupDefs.map((group, index) => {
            const status = completionStatus.find((s: any) => s.sectionIndex === index);
            const isSectionComplete = status?.completed || false;

            return {
                id: group.OID,
                title: group.Description?.TranslatedText?.text || group.Name || `Section ${index + 1}`,
                description: group.Description?.TranslatedText?.text || '',
                completed: isSectionComplete
            };
        });
    }, [assetItemGroupDefs, dynamicQuestionData, calculateCompletionStatus]);

    // Update steps whenever dynamicSteps changes
    useEffect(() => {
        if (dynamicSteps.length > 0 && JSON.stringify(steps) !== JSON.stringify(dynamicSteps)) {
            setSteps(dynamicSteps);
        }
    }, [dynamicSteps]);

    // Helper function to get display text for coded values
    const getDisplayTextForValue = (itemDef: any, value: any): string => {
        if (itemDef?.DataType === 'singleselect' || itemDef?.DataType === 'multiselect') {
            const codeListOID = itemDef.CodeListRef?.CodeListOID || itemDef.CodeListOID;
            const codeList = assetCodeListDefs.find(cl => cl.OID === codeListOID);

            if (codeList) {
                if (Array.isArray(value)) {
                    return value.map(v => {
                        const item = codeList.CodeListItem?.find(ci => ci.CodedValue === v);
                        return item?.Decode?.TranslatedText?.text || v;
                    }).join(', ');
                } else {
                    const item = codeList.CodeListItem?.find(ci => ci.CodedValue === value);
                    return item?.Decode?.TranslatedText?.text || value.toString();
                }
            }
        }
        return Array.isArray(value) ? value.join(', ') : value.toString();
    };

    const handleClose = (event?: React.SyntheticEvent | Event, reason?: string) => {
        if (reason === 'clickaway') {
            return;
        }

        setOpen(false);
    };



    const validation = () => {
        const errors: string[] = [];
    
        // CRITICAL: Always validate zipCode regardless of SDM template settings
        // The backend requires zipCode when creating bids (ProjectService.java:311)
        // Try multiple possible MetaKey variations for zipCode
        let zipCodeItemDef = assetItemDefs.find(item =>
            item.MetaKey === 'zipCode' ||
            item.MetaKey === 'zipcode' ||
            item.MetaKey === 'ZipCode' ||
            item.MetaKey === 'zip_code' ||
            item.MetaKey === 'Zip_Code' ||
            item.MetaKey?.toLowerCase() === 'zipcode' ||
            item.Name?.toLowerCase().includes('zip') ||
            item.Name?.toLowerCase().includes('postal')
        );


        let zipCodeValue = zipCodeItemDef ? dynamicQuestionData[zipCodeItemDef.OID] : assetData.zipCode;

        // If still not found, try to find by searching all dynamicQuestionData values
        if (!zipCodeValue) {
            // Check if any field in assetItemDefs maps to a zip-related field
            Object.keys(dynamicQuestionData).forEach(oid => {
                const itemDef = assetItemDefs.find(item => item.OID === oid);
                if (itemDef && (
                    itemDef.MetaKey?.toLowerCase().includes('zip') ||
                    itemDef.Name?.toLowerCase().includes('zip') ||
                    itemDef.Name?.toLowerCase().includes('postal')
                )) {
                    zipCodeValue = dynamicQuestionData[oid];
                    zipCodeItemDef = itemDef;
                }
            });
        }

        // FALLBACK: If we still can't find it, check if I.7 has a value (common zipcode OID)
        if (!zipCodeValue && dynamicQuestionData['I.7']) {
            zipCodeValue = dynamicQuestionData['I.7'];
            zipCodeItemDef = assetItemDefs.find(item => item.OID === 'I.7');
        }

        // Validate required fields from SDM ItemDefs
        assetItemGroupDefs.forEach(itemGroup => {
            itemGroup.ItemRef?.forEach((itemRef: any) => {
                // Check if field is mandatory
                if (itemRef.Mandatory === 'Yes') {
                    const itemDef = assetItemDefs.find(item => item.OID === itemRef.ItemOID);
                    if (!itemDef) return;

                    const value = dynamicQuestionData[itemDef.OID];
                    const fieldLabel = itemDef.Question?.TranslatedText?.text || itemDef.Name;

                    // Check if value is empty
                    if (value === undefined || value === null || value === '' ||
                        (Array.isArray(value) && value.length === 0)) {
                        errors.push(`${fieldLabel} is required`);
                    }

                    // Additional validation for numeric fields
                    if (itemDef.DataType === 'integer' || itemDef.DataType === 'float') {
                        if (value !== undefined && value !== null && value !== '') {
                            const numValue = Number(value);
                            if (isNaN(numValue) || numValue <= 0) {
                                errors.push(`${fieldLabel} must be a valid positive number`);
                            }
                        }
                    }
                }
            });
        });

        // Set validation errors
        setValidationErrors(errors);

        // Return true if no errors
        return errors.length === 0;
    }
    const handleAlertClose = (event?: React.SyntheticEvent | Event, reason?: string) => {
        setSuccess(false);
        setError(false);
        setNoChange(false)
        onNextPage();
    }
    const handleCancelBillingAddress = (event?: React.SyntheticEvent | Event, reason?: string) => {

        if (assetData.isBilling === null) {
            setAssetData(prevState => ({ ...prevState, isBilling: false }));
        }
        else {
            setAssetData(prevState => ({ ...prevState, isBilling: false }));

        }
        setIsBillingAdress(false);
        handleSubmit();

    }
    const handleBillingAddress = (event?: React.SyntheticEvent | Event, reason?: string) => {
        setUpdate(false);
        if (assetData.isBilling === null) {
            setAssetData(prevState => ({ ...prevState, isBilling: true }));
        }
        else if (assetData.isBilling === false) {
            setAssetData(prevState => ({ ...prevState, isBilling: true }));

        }
        else {
            setAssetData(prevState => ({ ...prevState, isBilling: true }));

        }
        setIsBillingAdress(false);
    }

    const handleCancelUpdateBillingAddress = (event?: React.SyntheticEvent | Event, reason?: string) => {
        if (assetData.isBilling === null) {
            setAssetData(prevState => ({ ...prevState, isBilling: false }));
        }
        else {
            setAssetData(prevState => ({ ...prevState, isBilling: false }));

        }
        setIsBillingAdress(false);
        updateHandleSubmit(assetIds.assetId);

    }
    const handleUpdateBillingAddress = (event?: React.SyntheticEvent | Event, reason?: string) => {
        setUpdate(true);
        // Immutable state update (both former branches did the same thing; dropped the
        // in-place Object.assign mutation that violated React's state contract).
        setAssetData(prevState => ({ ...prevState, isBilling: true }));
        setIsBillingAdress(false);
    }
    const handleNoChange = (event?: React.SyntheticEvent | Event, reason?: string) => {

        setNoChange(false)

    }


    // AB#28854 — final, server-confirmed gate before EITHER save path persists an address. Does
    // not just trust RoofLensAssetMap's picker-level check: a user can edit the address fields
    // directly (typed, not picked) without ever re-triggering the picker, and the OLDER
    // `handleSubmit` path below has no picker/geocode step at all today. Fail-open per the
    // confirmed decision — only a CONFIRMED invalid address or CONFIRMED absence of a rooftop
    // aborts the save; a check that couldn't complete (UNVERIFIED) lets the save proceed.
    const checkAddressAcceptedOrAbort = async (
        addr?: string, city?: string, state?: string, zipCode?: string,
    ): Promise<boolean> => {
        if (!addressValidationEnabled) return true;
        setAddressWarning(null);
        try {
            const res = await validatePropertyAddress(addr, city, state, zipCode, accessToken);
            const result: PropertyValidationResult | undefined = res?.ok ? res.data : undefined;
            if (!result) {
                // The validation CALL itself failed (network/5xx/auth) — fail open, but say so.
                setAddressWarning('Could not verify this address right now — it was accepted, but please double-check it.');
                return true;
            }
            if (!result.accepted) {
                setValidationErrors((prev) => [...prev, result.message || 'This address was rejected — no rooftop found here.']);
                setError(true);
                setErrorReason('Address validation failed. Please check the property address.');
                return false;
            }
            if (result.message) {
                // Accepted, but one of the two checks was UNVERIFIED — non-blocking warning.
                setAddressWarning(result.message);
            }
            return true;
        } catch (e) {
            console.warn('CreateAsset: address validation call failed; accepting (fail-open)', e);
            setAddressWarning('Could not verify this address right now — it was accepted, but please double-check it.');
            return true;
        }
    };

    const handleSubmit = async () => {

        // Extract property fields from dynamicQuestionData using MetaKeys from template
        const extractedData: any = {};

        Object.keys(dynamicQuestionData).forEach(itemOID => {
            const itemDef = assetItemDefs.find(item => item.OID === itemOID);
            const value = dynamicQuestionData[itemOID];

            if (itemDef && itemDef.MetaKey) {
                const fieldName = itemDef.MetaKey;
                const dataType = itemDef.DataType;

                // Convert value based on field type
                if (dataType === 'integer') {
                    extractedData[fieldName] = parseInt(value) || 0;
                } else if (dataType === 'float') {
                    extractedData[fieldName] = parseFloat(value) || 0;
                } else if (itemDef.CodeListRef) {
                    // For single-select/multi-select, keep as-is (text value or array)
                    extractedData[fieldName] = value;
                } else {
                    // Regular text field
                    extractedData[fieldName] = value;
                }
            }
        });


        // Update assetData with extracted fields
        const updatedAssetData = {
            ...assetData,
            ...extractedData,
            customerUid: (customerUid && customerUid !== '') ? customerUid : (assetData.customerUid || accessTokenPayload?.oid)
        };

        // Update state for future use
        setAssetData(updatedAssetData);

        setCount(count + 1);
        let formData = new FormData();
        formData.append("file", image);
        let validated = validation();
        if (validated === true) {
            // AB#28854 — this legacy path has no geocode/location step at all otherwise; without
            // this gate it's a complete bypass of the new validation.
            const addressOk = await checkAddressAcceptedOrAbort(
                updatedAssetData.address, updatedAssetData.city, updatedAssetData.state, updatedAssetData.zipCode,
            );
            if (!addressOk) return;

            // Convert using local helper function - use updatedAssetData instead of stale assetData
            const odmFormat = convertToODMFormat(updatedAssetData);
            let result = await saveAsset(odmFormat, accessToken);
            setCreatedAssetUid(result.data); // Store the newly created asset UID
            let uploadResult = await saveAssetDocument(formData, result.data, accessToken);

            if (result.status === 201) {

                // AUTOMATICALLY SAVE TO customer_asset (project) TABLE
                try {

                    // Prepare data for customer_asset table
                    const customerAssetData = {
                        assetUid: result.data.assetUid || result.data.uid || result.data.id,
                        assetName: assetData.name,
                        assetType: assetData.assetType || 'Residential',
                        assetAddress: JSON.stringify({
                            country: assetData.country || 'India',
                            zipCode: assetData.zipCode,
                            address: assetData.address,
                            city: assetData.city,
                            state: assetData.state
                        }),
                        billingAddress: assetData.isBilling ? JSON.stringify({
                            country: assetData.country || 'India',
                            zipCode: assetData.zipCode,
                            address: assetData.address,
                            city: assetData.city,
                            state: assetData.state
                        }) : JSON.stringify({
                            country: 'India',
                            zipCode: '',
                            address: '',
                            city: '',
                            state: ''
                        }),
                        customerUid: customerUid !== undefined ? customerUid : accessTokenPayload?.oid,
                        customerName: accessTokenPayload?.name || 'Unknown',
                        email: accessTokenPayload?.email || '',
                        mobileNo: accessTokenPayload?.phone || '',
                        accountNo: 1000
                    };

                } catch (customerAssetError) {
                    // console.error('Failed to save to customer_asset table (asset still saved to assets table):', customerAssetError);
                }

                setSuccessType('save');
                setSuccess(true);
            }
            else {
                // handleClick('Not Inserted');

                setError(true);
                setErrorCode(result?.errorCode);
                setErrorReason(result?.message);
            }
        }
        else {
            // handleClick('Not Inserted');
        }
    };

    const updateHandleSubmit = async (assetId: any) => {

        // Extract property fields from dynamicQuestionData using MetaKeys from template
        const extractedData: any = {};

        Object.keys(dynamicQuestionData).forEach(itemOID => {
            const itemDef = assetItemDefs.find(item => item.OID === itemOID);
            const value = dynamicQuestionData[itemOID];

            if (itemDef && itemDef.MetaKey) {
                const fieldName = itemDef.MetaKey;
                const dataType = itemDef.DataType;

                // Convert value based on field type
                if (dataType === 'integer') {
                    extractedData[fieldName] = parseInt(value) || 0;
                } else if (dataType === 'float') {
                    extractedData[fieldName] = parseFloat(value) || 0;
                } else if (itemDef.CodeListRef) {
                    // For single-select/multi-select, keep as-is (text value or array)
                    extractedData[fieldName] = value;
                } else {
                    // Regular text field
                    extractedData[fieldName] = value;
                }
            }
        });

        // Update assetData with extracted fields
        const updatedAssetData = {
            ...assetData,
            ...extractedData,
            customerUid: (customerUid && customerUid !== '') ? customerUid : (assetData.customerUid || accessTokenPayload?.oid)
        };

        // Update state for future use
        setAssetData(updatedAssetData);

        setCount(count + 1);
        let validated = validation();
        let formData = new FormData();
        formData.append("file", image);

        if (assetDataObject.address === updatedAssetData.address &&
            assetDataObject.name === updatedAssetData.name &&
            assetDataObject.country === updatedAssetData.country &&
            assetDataObject.zipCode === updatedAssetData.zipCode &&
            assetDataObject.state === updatedAssetData.state &&
            assetDataObject.city === updatedAssetData.city &&
            assetDataObject.stories === updatedAssetData.stories &&
            assetDataObject.area === updatedAssetData.area &&
            assetDataObject.checkbox === updatedAssetData.checkbox &&
            assetDataObject.assetType === updatedAssetData.assetType &&
            assetDataObject?.isBilling === updatedAssetData.isBilling

        ) {
            setNoChange(true);

        }
        else {
            if (validated === true) {
                if (typeof (updatedAssetData.location) != "string") {
                    let data = updatedAssetData;
                    data.location = Object.values(updatedAssetData.location).join(",")
                    setAssetData(data);
                }
                let result = await editAsset(assetId, updatedAssetData, accessToken);
                if (result.status === 200 || result.status === 201) {
                    setSuccessType('update');
                    setSuccess(true);
                }
                else {
                    setError(true);
                }
            }

        }
    };
    const onNextPage = (propertyName?: string) => {
        if (action === "addProperty") {
            const nameToUse = propertyName || createdAssetName || assetData.name;
            navigate("/createbid", {
                state: {
                    newPropertyCreated: true,
                    newPropertyUid: createdAssetUid,
                    newPropertyName: nameToUse,
                    customerUid: assetData.customerUid || customerUid,
                    // Echo back the customer name TaskSelector passed in, so the wizard
                    // can restore the selected customer (createBid* sessionStorage was
                    // cleared on unmount, so it's the only surviving source of the name).
                    customerName: location.state?.customerName || '',
                    timestamp: new Date().getTime()
                }
            });
        } else {
            // If the editor was opened from another page (e.g. Leads & Customers passes
            // returnTo=/leadscustomers/<id>?tab=assets), return there after save — otherwise
            // fall back to the My Assets list. Mirrors Goback()'s returnTo handling.
            const returnTo = location.state?.returnTo;
            if (returnTo) {
                navigate(returnTo, { state: { existingAssetUids: location.state?.existingAssetUids } }); // AB#31740
            } else {
                // Navigate with state to trigger refresh in ListAsset
                navigate("/assetList", { state: { version: 'v2', res: location.state?.res } });
            }
        }
    };

    const Goback = () => {
        const returnTo = location.state?.returnTo;
        navigate(returnTo ?? "/createbid");
    }

    const isSuccessMessage = toastMessage === 'Inserted Successfully' || toastMessage === 'Updated Successfully';

    const handleSaveFunctionality = async () => {
        if (isSaving) return;
        setIsSaving(true);
        try {
            // VALIDATION
            const isValid = validation();
            if (!isValid) {
                setError(true);
                setErrorReason('Validation failed. Please check required fields.');
                return;
            }

            const itemDataArray = Object.keys(dynamicQuestionData).map(itemOID => {
                const itemDef = assetItemDefs.find(item => item.OID === itemOID);
                const metaKey = itemDef?.MetaKey || itemDef?.Name?.toLowerCase().replace(/\s+/g, '') || itemOID;
                return {
                    ItemOID: itemOID,
                    Value: dynamicQuestionData[itemOID],
                    MetaKey: metaKey
                };
            });

            const itemGroupDataArray = assetItemGroupDefs.map(itemGroup => ({
                ItemGroupOID: itemGroup.OID,
                ItemData: itemGroup.ItemRef?.map((itemRef: any) => {
                    const itemData = itemDataArray.find(item => item.ItemOID === itemRef.ItemOID);
                    return itemData || { ItemOID: itemRef.ItemOID, Value: '', MetaKey: '' };
                }).filter(item => item.Value !== '') || []
            })).filter(group => group.ItemData.length > 0);

            const sdmStructure = {
                EventData: [{
                    EventOID: DEFAULT_EVENTOID,
                    FormData: [{
                        FormOID: DEFAULT_FORMOID,
                        ItemGroupData: itemGroupDataArray,
                        MetaDataVersionOID: assetQuestionData?.MetaDataVersionOID
                    }]
                }]
            };

            const savedData = JSON.stringify(sdmStructure);

            // If nothing to save
            if (!savedData) {
                setError(true);
                setErrorReason('Nothing to save.');
                return;
            }

            // Extract & map fields dynamically using MetaKey from template
            const allItemData = sdmStructure.EventData[0].FormData[0].ItemGroupData
                .flatMap(group => group.ItemData);

            const extractedData: any = {};
            allItemData.forEach(item => {
                // Use MetaKey directly - it was already set from assetItemDefs
                const fieldName = item.MetaKey;
                if (fieldName && item.Value !== undefined && item.Value !== null && item.Value !== '') {
                    // Find the itemDef to get DataType for proper type conversion
                    const itemDef = assetItemDefs.find(def => def.OID === item.ItemOID);
                    const dataType = itemDef?.DataType;

                    if (dataType === 'float' || fieldName === 'area') {
                        extractedData[fieldName] = parseFloat(item.Value) || 0;
                    } else if (dataType === 'integer' || fieldName === 'stories') {
                        extractedData[fieldName] = parseInt(item.Value) || 0;
                    } else {
                        extractedData[fieldName] = item.Value;
                    }
                }
            });

            const updatedAssetData = {
                ...assetData,
                ...extractedData,
                assetTemplateData: savedData,
                updatedAt: new Date(),
                customerUid: (customerUid && customerUid !== '') ? customerUid : (assetData.customerUid || accessTokenPayload?.oid),
                location: assetData.location || ""
            };

            setAssetData(updatedAssetData);

            // Prepare formData for image
            let formData = new FormData();
            if (image) formData.append("file", image);

            // Build complete address for accurate geocoding
            const addressParts = [
                updatedAssetData.address,
                updatedAssetData.city,
                updatedAssetData.state,
                updatedAssetData.country,
                updatedAssetData.zipCode
            ].filter(part => part && part.toString().trim() !== '');

            const fullAddress = addressParts.join(', ');

            // AB#28854 — server-confirmed address/property gate, right after validation() and
            // before location resolution — see checkAddressAcceptedOrAbort's own comment for why
            // this can't just trust the picker-level check.
            const addressOk = await checkAddressAcceptedOrAbort(
                updatedAssetData.address, updatedAssetData.city, updatedAssetData.state, updatedAssetData.zipCode,
            );
            if (!addressOk) return;

            // get location: prefer the user-pinned coordinate, else geocode the address
            const location = await resolveLocation(fullAddress);

            if (assetDataObject != null) {
                // UPDATE
                const finalAssetData = { ...updatedAssetData, location: location || updatedAssetData.location || null };
                // Persist the RoofLens measurement (tri-state): fresh measurement →
                // save it; panel emptied this session → send "" to clear the stored
                // column; untouched → preserve the stored value (send undefined so the
                // backend leaves the column as-is).
                if (roofLensMeasurementRef.current) {
                    finalAssetData.measurementData = JSON.stringify(roofLensMeasurementRef.current);
                } else if (measurementTouchedRef.current) {
                    finalAssetData.measurementData = ""; // user emptied the panel → clear the stored measurement
                } else {
                    finalAssetData.measurementData = typeof assetData?.measurementData === 'string' ? assetData.measurementData : undefined; // untouched → preserve
                }

                const result = await editAsset(assetDataObject.id, finalAssetData, accessToken);

                if (result.status === 200 || result.status === 201) {
                    if (image) await saveAssetDocument(formData, assetDataObject.id, accessToken);
                    setSuccessType('update');
                    setUpdateSuccess(true);
                    setOpenUpdateDialog(true);
                } else {
                    setUpdateError(true);
                    setOpenUpdateDialog(true);
                    setErrorCode(result?.errorCode);
                    setErrorReason(result?.message);
                    setErrorMessage(result?.debugMessage);
                    setTimestamp(result?.timestamp);
                }
            } else {
                // CREATE
                const finalAssetData = { ...updatedAssetData, location };
                // Persist the RoofLens measurement (tri-state): fresh measurement →
                // save it; panel emptied this session → send "" to clear the stored
                // column; untouched → preserve the stored value (send undefined so the
                // backend leaves the column as-is).
                if (roofLensMeasurementRef.current) {
                    finalAssetData.measurementData = JSON.stringify(roofLensMeasurementRef.current);
                } else if (measurementTouchedRef.current) {
                    finalAssetData.measurementData = ""; // user emptied the panel → clear the stored measurement
                } else {
                    finalAssetData.measurementData = typeof assetData?.measurementData === 'string' ? assetData.measurementData : undefined; // untouched → preserve
                }
                const odmPayload = convertToODMFormat(finalAssetData);

                const result = await saveAsset(odmPayload, accessToken);

                if (result.status === 201) {
                    setCreatedAssetUid(result.data);
                    setCreatedAssetName(finalAssetData.name);
                    setAssetData(finalAssetData);
                    if (image) await saveAssetDocument(formData, result.data, accessToken);
                    setSuccessType('save');
                    setCreateSuccess(true);
                    setOpenDialogs(true);
                } else {
                    setCreateError(true);
                    setOpenDialogs(true);
                    setErrorCode(result?.errorCode);
                    setErrorReason(result?.message);
                    setErrorMessage(result?.debugMessage || '');
                    setTimestamp(result?.timestamp);
                }
            }
        } catch (err) {
            setCreateError(true);
            setOpenDialogs(true);
            setErrorReason('An error occurred while saving the asset');
            setErrorMessage(String(err));
        } finally {
            setIsSaving(false);
        }
    };

    // Picker callback — record the exact pinned coordinate AND autofill the
    // questionnaire address fields from the parsed result, so the address is
    // entered once (single point of entry). Fields stay editable.
    const handleLocationPicked = (locStr: string, parsed?: ParsedAddress) => {
        const locationChanged =
            locStr !== (typeof assetData?.location === 'string' ? assetData.location : '');
        // When the RoofLens map is OFF (legacy AssetLocationPicker fallback), a genuine
        // location change invalidates any previously-saved roof measurement — it describes the
        // OLD property. Mark it cleared so save writes an empty measurement instead of
        // persisting stale roof geometry against the new pin. (When the RoofLens map is ON it
        // manages/rehydrates measurement itself, so this must not run there.)
        const clearStaleMeasurement =
            !roofLensMapEnabled &&
            typeof assetData?.measurementData === 'string' &&
            assetData.measurementData.trim() !== '' &&
            locationChanged;
        if (clearStaleMeasurement) {
            roofLensMeasurementRef.current = null;
            measurementTouchedRef.current = true;   // tri-state save → writes "" (cleared)
        }
        pickedLocationRef.current = locStr;
        // The map's address/country props and the save payload read assetData.address/city/state/zipCode/
        // country, so a pick writes them too, not only the questionnaire fields (null = no address: untouched).
        const pickedAddress = buildPickedAddress(parsed);
        setAssetData((prev: any) => ({
            ...prev,
            location: locStr,
            ...(pickedAddress ?? {}),
            ...(clearStaleMeasurement ? { measurementData: '' } : {}),
            // Human-correction-priority fix: a genuinely new address describes a DIFFERENT
            // building — the previous Auto-Detect-derived area (if any) no longer applies and
            // must not silently block the fresh Auto-Detect this pick triggers.
            // handleAreaComputed's isAutoDetect guard treats "field already has a value" as
            // "already answered", which was true for the OLD address, not this one.
            ...(locationChanged ? { area: undefined } : {}),
        }));

        // Reverse-map each address component to its questionnaire field via MetaKey.
        // Case-insensitive, matching getOIDByMetaKey/getFieldValueByMetaKey elsewhere in this
        // file — templates don't reliably use exact-case MetaKeys (e.g. "Address" vs "address"),
        // and an exact match here silently dropped every field when casing didn't line up.
        // Built unconditionally (even without `parsed`) since the stale-measurement clear below
        // needs it regardless of whether address components were resolved.
        const oidByMetaKey: Record<string, string> = {};
        assetItemDefs.forEach((def: any) => {
            if (def?.MetaKey) {
                oidByMetaKey[def.MetaKey.toLowerCase()] = def.OID;
            }
        });

        // Same fix as above, for the questionnaire-side mirror: clear every
        // measurement-derived OID (area + all roof-detail metrics) so the Auto-Detect run
        // this pick triggers sees them as genuinely blank again, instead of stale answers
        // describing the OLD address that would otherwise silently block the refill.
        const staleOids = new Set<string>();
        if (locationChanged) {
            Object.values(MEASUREMENT_METAKEY_ALIASES).flat().forEach((metaKey) => {
                const oid = oidByMetaKey[metaKey.toLowerCase()];
                if (oid) staleOids.add(oid);
            });
        }

        const updates: Record<string, any> = {};
        if (pickedAddress) {
            PICK_ADDRESS_KEYS.forEach((k) => {
                const oid = oidByMetaKey[k.toLowerCase()];
                if (oid) updates[oid] = pickedAddress[k]; // '' clears a part the pick did not supply
            });
        }

        if (staleOids.size > 0 || Object.keys(updates).length > 0) {
            // New reference → QuestionRenderer_v2 syncs via its existingData effect.
            setDynamicQuestionData((prev: any) => {
                const next = { ...prev };
                staleOids.forEach((oid) => { delete next[oid]; });
                return { ...next, ...updates };
            });
        }
    };

    // Live-update the "Area in square feet" field from the RoofLens measurement
    // (SAM2 result or manual facet add/adjust/delete/pitch). The area question's
    // MetaKey is 'area'; write the rounded total roof area into its OID.
    //
    // Human-correction-priority fix, CORRECTED — the original version of this guard only
    // filled a blank field, applied to EVERY recompute regardless of source. That silently
    // broke the legitimate case a real user hit live: manually editing a facet's own "Roof
    // area" in the Live Measurements panel recomputes the total via this exact callback too
    // (RoofLensAssetMap's single generic onAreaChange effect fires for BOTH an Auto-Detect
    // re-run AND a manual per-facet edit — there was no way to tell them apart), so once the
    // field had any value at all, a person's own direct facet correction stopped reaching it.
    // `opts.isAutoDetect` (RoofLensAssetMap.tsx) now distinguishes the two: only skip
    // overwriting an already-answered field when this recompute came from a fresh Auto-Detect
    // run. A manual facet edit is itself a human correction — of at least equal standing to
    // whatever was there before — so it always propagates live, exactly as it did before any
    // guard existed. A user who deliberately wants a fresh AUTO value can still clear the
    // field first.
    const handleAreaComputed = useCallback((totalSqft: number, opts?: { isAutoDetect?: boolean }) => {
        const rounded = Math.round(totalSqft);
        if (opts?.isAutoDetect) {
            setAssetData((prev: any) => (prev.area ? prev : { ...prev, area: rounded }));
            const oid = getOIDByMetaKey('area');
            if (oid) {
                setDynamicQuestionData((prev: any) => (prev[oid] ? prev : { ...prev, [oid]: String(rounded) }));
            }
            return;
        }
        setAssetData((prev: any) => ({ ...prev, area: rounded }));
        const oid = getOIDByMetaKey('area');
        if (oid) {
            setDynamicQuestionData((prev: any) => ({ ...prev, [oid]: String(rounded) }));
        }
    }, [getOIDByMetaKey]);

    // Best-effort auto-fill of the EagleView-style roof metrics (edges, attic,
    // stories, pitch, complexity) from an Auto-Detect run. Each metric is written
    // ONLY if the asset template actually has a question with the matching MetaKey
    // (getOIDByMetaKey returns null otherwise), so a template without these
    // questions is unaffected — nothing is created. Mirrors handleAreaComputed,
    // including its human-correction-priority guard: a metric already answered
    // (typed or previously saved) is never silently overwritten by a later
    // recompute — see the comment on handleAreaComputed for why.
    const handleMetricsComputed = useCallback((metrics: RoofMetrics) => {
        // metaKey candidates -> value (as string); first metaKey that resolves wins.
        const num = (v?: number) => (typeof v === 'number' ? String(Math.round(v)) : undefined);
        const bindings: Array<{ keys: string[]; value?: string }> = [
            { keys: MEASUREMENT_METAKEY_ALIASES.predominantPitch, value: metrics.predominant_pitch },
            { keys: MEASUREMENT_METAKEY_ALIASES.ridge, value: num(metrics.ridge_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.hip, value: num(metrics.hip_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.ridgeHip, value: num(metrics.ridge_hip_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.valley, value: num(metrics.valley_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.rake, value: num(metrics.rake_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.eave, value: num(metrics.eave_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.attic, value: num(metrics.estimated_attic_sqft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.stories, value: num(metrics.stories) },
            { keys: MEASUREMENT_METAKEY_ALIASES.height, value: num(metrics.building_height_ft) },
            { keys: MEASUREMENT_METAKEY_ALIASES.complexity, value: metrics.structure_complexity },
        ];
        const updates: Record<string, string> = {};
        bindings.forEach(({ keys, value }) => {
            if (value === undefined) return;
            for (const k of keys) {
                const oid = getOIDByMetaKey(k);
                if (oid) {
                    updates[oid] = value;
                    break;
                }
            }
        });
        if (Object.keys(updates).length > 0) {
            // Filtered inside the functional updater (against the LATEST prev, not a
            // possibly-stale closure) — only an oid that's still blank gets set, so an
            // already-answered metric is never silently overwritten by this recompute.
            setDynamicQuestionData((prev: any) => {
                const filtered: Record<string, string> = {};
                Object.entries(updates).forEach(([oid, value]) => {
                    if (!prev[oid]) {
                        filtered[oid] = value;
                    }
                });
                return Object.keys(filtered).length > 0 ? { ...prev, ...filtered } : prev;
            });
        }
    }, [getOIDByMetaKey]);

    // Use the user-pinned location when available; otherwise geocode the address.
    const resolveLocation = async (fullAddress: string): Promise<string | null> => {
        if (pickedLocationRef.current) {
            return pickedLocationRef.current;
        }
        return await handleGeocode(fullAddress);
    };

    const handleGeocode = (fullAddress: string): Promise<string | null> => {
        return new Promise((resolve, reject) => {
            if (!fullAddress || fullAddress.trim() === '') {
                return resolve(null);
            }

            // Guard against the Google Maps JS API not being loaded yet — avoid
            // "google.maps.Geocoder is not a constructor" if this runs before the map
            // has loaded the API. Degrade gracefully to the pinned location.
            if (!isGoogleMapsGeocoderReady()) {
                console.warn('Google Maps not loaded yet — skipping address geocode');
                return resolve(null);
            }

            const geocoder = new google.maps.Geocoder();
            geocoder.geocode({ address: fullAddress }, (results: any, status) => {
                if (status === 'OK' && results[0]) {
                    const { lat, lng } = results[0].geometry.location;
                    const formattedValue = `${lat()},${lng()}`;
                    // Don't update assetData here - return the value so caller can use it
                    resolve(formattedValue);
                } else {
                    // Don't reject - just return null so the save can proceed without location
                    console.warn(`Geocoding failed for address: ${fullAddress}, status: ${status}`);
                    resolve(null);
                }
            });
        });
    };

    const callParentComponent = () => {
        // Directly call handleSaveFunctionality since we're managing the data internally
        handleSaveFunctionality();
    };

    const closeDialog = () => {
        handleClose();
        setOpenDialogs(false);
        setCreateSuccess(false);
        setCreateError(false);
        setOpenUpdateDialog(false);
        setUpdateSuccess(false);
        setUpdateError(false);

        // Navigate back if successful
        if (createSuccess || updateSuccess) {
            onNextPage();
        }
    };

    // Stepper navigation handlers
    const handleNext = () => {
        if (activeStep === 0) {
            checkDuplicateForFirstSevenFields().then((isDuplicate) => {
                if (isDuplicate) {                    
                    setDuplicateDialogMessage('This asset has already been added.');
                    setDuplicateDialogOpen(true);
                    return;
                }                
                if (activeStep === steps.length - 1) {
                    // On final step, save/update the asset
                    callParentComponent();
                } else {
                    setActiveStep((prevActiveStep) => prevActiveStep + 1);
                }
            }).catch((err) => {
                if (activeStep === steps.length - 1) {
                    callParentComponent();
                } else {
                    setActiveStep((prevActiveStep) => prevActiveStep + 1);
                }
            });
            return;
        }

        if (activeStep === steps.length - 1) {
            // Check duplicate before saving on final step
            checkDuplicateForFirstSevenFields().then((isDuplicate) => {
                if (isDuplicate) {
                    setDuplicateDialogMessage('This asset has already been added.');
                    setDuplicateDialogOpen(true);
                    return;
                }
                callParentComponent();
            }).catch((err) => {
                callParentComponent();
            });
        } else {
            setActiveStep((prevActiveStep) => prevActiveStep + 1);
        }
    };

    const handlePrevious = () => {
        setActiveStep((prevActiveStep) => prevActiveStep - 1);
    };

    const handleStepClick = (step: number) => {
        setActiveStep(step);
    };

    const getFieldValueByMetaKey = (metaKey: string) => {
        // find itemDef with metaKey (case-insensitive)
        const item = assetItemDefs.find(i => (i.MetaKey || '').toString().toLowerCase() === metaKey.toString().toLowerCase());
        if (item) {
            const val = dynamicQuestionData[item.OID] ?? '';
            return val;
        }
        const fallback = assetData[metaKey] ?? '';
        return fallback;
    };

    const checkDuplicateForFirstSevenFields = async (): Promise<boolean> => {
        // Build current object from SDM fields / assetData
        const current = {
            name: getFieldValueByMetaKey('name'),
            area: getFieldValueByMetaKey('area'),
            city: getFieldValueByMetaKey('city'),
            state: getFieldValueByMetaKey('state'),
            country: getFieldValueByMetaKey('country'),
            zipCode: getFieldValueByMetaKey('zipCode'),
        };

        const customerId = (customerUid && customerUid !== '') ? customerUid : (assetData.customerUid || accessTokenPayload?.oid);


        try {
            const res = await getAsset(customerId, accessToken);
            const assets = res?.data || [];
            const ignoreUid = assetDataObject
                ? (assetDataObject.assetUid || assetDataObject.id)
                : undefined;

            const dup = findDuplicateAsset(current, assets, ignoreUid);
            return !!dup;
        } catch (err) {
            // Don't block user on API error
            return false;
        }

    };

    // Calculate completion stats
    const totalQuestions = assetItemDefs.length;
    const answeredQuestions = Object.keys(dynamicQuestionData).filter(key => {
        const value = dynamicQuestionData[key];
        return value !== undefined && value !== null && value !== '' &&
            (!Array.isArray(value) || value.length > 0);
    }).length;
    const completionPercentage = totalQuestions > 0 ? Math.round((answeredQuestions / totalQuestions) * 100) : 0;

    // Show loading state while fetching template
    if (isLoadingTemplate) {
        return (
            <Container maxWidth="xl" sx={{ py: 4 }}>
                <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 400 }}>
                    <CircularProgress />
                    <Typography sx={{ ml: 2 }}>Loading asset template...</Typography>
                </Box>
            </Container>
        );
    }

    return (
        <>
            {/* Header Section */}
            <Grid container justifyContent="center" alignItems="center" >
                <Grid item xs={12} sm={10}>
                    <Box
                        display={smScreen ? "flex" : "block"}
                        flexDirection={smScreen ? "row" : "column"}
                        alignItems={smScreen ? "center" : "start"}
                        m="1px 0"
                    >
                        <Grid item sm={6}>
                            {assetDataObject ? <Header title="Edit Asset (V3)" subtitle={""} /> : <Header title="Create New Asset (V3)" subtitle={""} />}
                        </Grid>
                        <Grid item sm={6} sx={{ display: 'flex', justifyContent: 'flex-end', gap: '15px' }}>
                            <Button
                                id="create-asset-v2-button-go-back"
                                className='p-btn p-btnPrimary'
                                size="medium"
                                onClick={Goback}
                                startIcon={<ArrowBackIcon className='personIcon' />}
                            >
                                Go Back
                            </Button>
                        </Grid>
                    </Box>
                </Grid>
            </Grid>

            {/* Main Wizard Container */}
            <Container maxWidth="xl" sx={{ py: 1, pb: 6 }}>
                <Paper
                    elevation={4}
                    sx={{
                        p: 3,
                        pb: 3,
                        borderRadius: '20px',
                        background: 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
                        border: '1px solid #e9ecef',
                        boxShadow: '0 8px 32px rgba(26, 54, 93, 0.1)',
                        position: 'relative',
                        overflow: 'visible',
                        minHeight: 'calc(100vh - 300px)'
                    }}
                >
                    {/* Custom Stepper */}
                    <CustomStepper
                        steps={steps}
                        activeStep={activeStep}
                        onStepClick={handleStepClick}
                        onNext={handleNext}
                        onBack={handlePrevious}
                        showNavigation={false}
                        nextButtonText={activeStep === steps.length - 1 ? (assetDataObject ? 'Update' : 'Save') : 'Continue'}
                        backButtonText="Previous"
                        nextButtonDisabled={false}
                        backButtonDisabled={activeStep === 0}
                    />

                    {/* Completion Stats */}
                    <Box sx={{
                        display: 'flex',
                        justifyContent: 'center',
                        alignItems: 'center',
                        mb: 3,
                        gap: 2
                    }}>
                        <Chip
                            label={`${answeredQuestions}/${totalQuestions} Questions Answered`}
                            sx={{
                                backgroundColor: 'primary.main',
                                color: 'white',
                                fontWeight: 600
                            }}
                        />
                        <Chip
                            label={`${completionPercentage}% Complete`}
                            sx={{
                                backgroundColor: completionPercentage === 100 ? '#4caf50' : '#ff9800',
                                color: 'white',
                                fontWeight: 600
                            }}
                        />
                    </Box>

                    {/* Image Upload Section */}
                    <Card sx={{
                        mb: 3,
                        border: '1px solid #e2e8f0',
                        borderRadius: '12px',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.08)'
                    }}>
                        <Box sx={{
                            backgroundColor: 'primary.main',
                            color: 'white',
                            p: 2,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1
                        }}>
                            <MapOutlinedIcon fontSize="small" />
                            <Typography variant="h6" sx={{ fontWeight: 600, fontSize: '1rem' }}>
                                Asset Map
                            </Typography>
                        </Box>
                        <CardContent sx={{ p: 3 }}>
                            {/* Map inside the v3 Asset page. RoofLens flag ON → the
                                roof-measurement map; OFF → the legacy v2 AssetLocationPicker
                                map, so v3 still works while RoofLens is disabled.
                                `key` pins this to React's KEYED reconciliation (by asset identity,
                                not position), so a sibling change elsewhere in this card can never
                                cause an unwanted remount that would lose whatever
                                RoofLensAssetMap's own address-triggered auto-detect effect
                                (module-level `_roofLensAutoDetectedKey` guard, keyed only by
                                assetKey) had already claimed. */}
                            {roofLensMapEnabled ? (
                                <RoofLensAssetMap
                                    key={String(assetData?.assetUid ?? assetData?.id ?? sessionAssetKey)}
                                    location={typeof assetData.location === 'string' ? assetData.location : undefined}
                                    address={[assetData.address, assetData.city, assetData.state, assetData.zipCode].filter(Boolean).join(', ')}
                                    country={assetData.country}
                                    assetKey={String(assetData?.assetUid ?? assetData?.id ?? sessionAssetKey)}
                                    onLocationPicked={handleLocationPicked}
                                    onAreaChange={handleAreaComputed}
                                    onMetricsComputed={handleMetricsComputed}
                                    onMeasurementChange={(d) => { roofLensMeasurementRef.current = d; measurementTouchedRef.current = true; }}
                                    savedMeasurement={typeof assetData?.measurementData === 'string' ? assetData.measurementData : undefined}
                                    accessToken={accessToken}
                                    onAddressValidated={(ok) => {
                                        if (!ok) return; // RoofLensAssetMap shows its own rejection banner
                                        // Any picker-level pick that gets this far RE-VALIDATED the address —
                                        // any stale blocking error from a PRIOR (rejected) address no longer
                                        // applies, regardless of exact wording (the earlier version tried to
                                        // filter by matching `reason` text, which could never actually match
                                        // what was stored — fieldcraft-reviewer finding). The save-time gate
                                        // re-validates server-side regardless, so this is just clearing stale UI.
                                        setValidationErrors([]);
                                        setAddressWarning(null);
                                    }}
                                />
                            ) : (
                                <AssetLocationPicker key={String(assetData?.assetUid ?? assetData?.id ?? sessionAssetKey)} value={typeof assetData.location === 'string' ? assetData.location : undefined} onChange={handleLocationPicked} />
                            )}
                        </CardContent>
                    </Card>

                    {/* AB#28854 — non-blocking; the save proceeded (fail-open UNVERIFIED), this just
                        says so instead of silently implying full validation happened. */}
                    {addressWarning && (
                        <Alert severity="warning" sx={{ mb: 3 }} onClose={() => setAddressWarning(null)}>
                            {addressWarning}
                        </Alert>
                    )}

                    {/* Validation Errors Display */}
                    {validationErrors.length > 0 && (
                        <Alert severity="error" sx={{ mb: 3 }}>
                            <Typography variant="body2" sx={{ fontWeight: 600, mb: 1 }}>
                                Please fix the following errors:
                            </Typography>
                            <ul style={{ margin: 0, paddingLeft: '20px' }}>
                                {validationErrors.map((error, index) => (
                                    <li key={index}>
                                        <Typography variant="body2">{error}</Typography>
                                    </li>
                                ))}
                            </ul>
                        </Alert>
                    )}

                    {/* Step Content */}
                    <Box sx={{
                        mt: 0.5,
                        minHeight: 150,
                        mb: 2
                    }}>
                        {/* Current Step Questions in Card Format */}
                        {assetItemGroupDefs.length > 0 && activeStep < assetItemGroupDefs.length && (
                            <Card
                                sx={{
                                    mb: 3,
                                    border: '1px solid #e2e8f0',
                                    borderRadius: '12px',
                                    boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
                                    overflow: 'visible'
                                }}
                            >
                                {/* Section Header */}
                                <Box sx={{
                                    backgroundColor: 'primary.main',
                                    color: 'white',
                                    p: 3,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'space-between'
                                }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                        <CheckCircleIcon sx={{ fontSize: '24px', mr: 2 }} />
                                        <Typography variant="h6" sx={{ fontWeight: 600 }}>
                                            {assetItemGroupDefs[activeStep]?.Description?.TranslatedText?.text ||
                                                assetItemGroupDefs[activeStep]?.Name ||
                                                `Section ${activeStep + 1}`}
                                        </Typography>
                                    </Box>
                                    <Chip
                                        label={`${assetItemGroupDefs[activeStep]?.ItemRef?.length || 0} Questions`}
                                        sx={{
                                            backgroundColor: 'rgba(255,255,255,0.2)',
                                            color: 'white',
                                            fontWeight: 600
                                        }}
                                    />
                                </Box>

                                {/* Questions Content */}
                                <CardContent sx={{ p: 3 }}>
                                    {/* Billing Address Copy Checkbox — only shown on Property Details step */}
                                    {activeStep === 0 && renderBillingAddressCheckbox('details', { mb: 2, ml: 0.5 })}
                                    {/* Old location-picker map removed (v3): the RoofLens map in the
                                        Asset Image card now handles address search + field auto-fill. */}

                                    <QuestionRenderer_v2
                                        ref={questionRendererRef}
                                        itemGroupDefs={assetItemGroupDefs}
                                        itemDefs={assetItemDefs}
                                        codeListDefs={assetCodeListDefs}
                                        conditionDefs={assetConditionDefs}
                                        methodDefs={assetMethodDefs}
                                        activeStep={activeStep}
                                        existingData={dynamicQuestionData}
                                        onDataChange={handleDataChange}
                                        isAssetForm
                                    />
                                </CardContent>
                            </Card>
                        )}
                    </Box>

                    {/* Navigation Section */}
                    <Box sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginTop: 3,
                        marginBottom: 2,
                        padding: 2,
                        backgroundColor: '#fafafa',
                        borderRadius: 2,
                        border: '1px solid #e0e0e0',
                        position: 'sticky',
                        bottom: 0,
                        zIndex: 1000,
                        boxShadow: '0 -2px 8px rgba(0,0,0,0.1)'
                    }}>
                        <Button
                            id="create-asset-v2-button-previous-step"
                            disabled={activeStep === 0}
                            onClick={handlePrevious}
                            variant="outlined"
                            size="small"
                            className="p-btn p-btnCancel"
                        >
                            Previous
                        </Button>

                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Typography variant="body2" sx={{ color: '#666', fontSize: '0.8rem' }}>
                                Step {activeStep + 1} of {steps.length}
                            </Typography>
                            <Box sx={{ width: 80, height: 4, backgroundColor: '#e0e0e0', borderRadius: 2 }}>
                                <Box
                                    sx={{
                                        width: `${(steps.length ? ((activeStep + 1) / steps.length) * 100 : 0)}%`,
                                        height: '100%',
                                        backgroundColor: '#1a365d',
                                        borderRadius: 2,
                                        transition: 'width 0.3s ease'
                                    }}
                                />
                            </Box>
                            <Typography variant="body2" sx={{ color: '#1a365d', fontSize: '0.8rem', fontWeight: 600 }}>
                                {Math.round((steps.length ? ((activeStep + 1) / steps.length) * 100 : 0))}%
                            </Typography>
                        </Box>

                        <Button
                            id="create-asset-v2-button-next-step"
                            className='p-btn p-btnPrimary'
                            size="small"
                            onClick={handleNext}
                            disabled={isSaving}
                            >
                            {isSaving ? 'Saving...' : (activeStep === steps.length - 1 ? (assetDataObject ? 'Update' : 'Save') : 'Continue')}
                        </Button>
                    </Box>
                </Paper>
            </Container>

            {/* Dialogs and Snackbars */}
            <Snackbar open={open} autoHideDuration={1000} onClose={handleClose} anchorOrigin={{ vertical: 'top', horizontal: 'center' }} sx={{ paddingTop: 6 }}>
                <Alert onClose={handleClose} severity={isSuccessMessage ? 'success' : 'warning'} sx={{ width: '100%' }} >
                    {toastMessage}
                </Alert>
            </Snackbar>

            <Dialog open={success} >
                <DialogTitle className='success'>
                    <Box paddingLeft={8} paddingRight={8}>
                        <Typography textAlign={'center'}>
                            <CheckCircleOutlinedIcon sx={{ fontSize: "50px", color: "white" }} /></Typography>
                        <Typography textAlign={'center'} color='white' fontSize={'20px'} fontWeight={'400'} >
                            Success
                        </Typography>
                    </Box>
                </DialogTitle>
                <Box paddingLeft={8} paddingRight={8}>
                    <DialogContent >
                        <DialogContentText textAlign={'center'} fontSize={'18px'} fontWeight={'500'}>   {successType === 'save' ? 'Asset added successfully' : 'Asset updated successfully'}</DialogContentText>
                    </DialogContent>
                    <DialogActions style={{ justifyContent: "center" }}>
                        <Button  id="create-asset-v2-button-ok" className="p-btn p-btnSuccess" onClick={handleAlertClose}>OK</Button>
                    </DialogActions>
                </Box>
            </Dialog>

            {/* CustomDialog for Create Operations */}
            <CustomDialog
                open={openDialogs}
                onClose={closeDialog}
                reload={() => null}
                conflictWarning={false}
                success={createSuccess}
                error={createError}
                errorCode={errorCode}
                detailedMessage={errorReason}
                debugMessage={errorMessage}
                timestamp={timestamp}
                Content={createSuccess ? "Asset created successfully" : errorMessage !== '' ? errorMessage : "Failed to create asset"}
            />

            {/* CustomDialog for Update Operations */}
            <CustomDialog
                open={openUpdateDialog}
                onClose={closeDialog}
                reload={() => null}
                conflictWarning={false}
                success={updateSuccess}
                error={updateError}
                errorCode={errorCode}
                debugMessage={errorMessage}
                timestamp={timestamp}
                detailedMessage={errorReason}
                Content={updateSuccess ? "Asset updated successfully" : errorMessage !== '' ? errorMessage : "Failed to update asset"}
            />

            {/* Duplicate Asset Dialog */}
            <Dialog open={duplicateDialogOpen} onClose={() => setDuplicateDialogOpen(false)}>
                <DialogTitle>Duplicate Asset</DialogTitle>
                <DialogContent>
                    <DialogContentText>{duplicateDialogMessage}</DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button id="create-asset-v2-warning-button" className="p-btn p-btnPrimary" onClick={() => setDuplicateDialogOpen(false)}>OK</Button>
                </DialogActions>
            </Dialog>

            {/* Basic Error Dialog for Validation Errors */}
            <Dialog open={error && !openDialogs && !openUpdateDialog} >
                <DialogTitle className='error'>
                    <Box paddingLeft={8} paddingRight={8}>
                        <Typography textAlign={'center'}>
                            <CancelOutlinedIcon sx={{ fontSize: "50px", color: "white" }} /></Typography>
                        <Typography textAlign={'center'} color='white' fontSize={'20px'} fontWeight={'400'} >
                            Validation Error
                        </Typography>
                    </Box>
                </DialogTitle>
                <Box paddingLeft={8} paddingRight={8}>
                    <DialogContent >
                        <DialogContentText textAlign={'center'} fontSize={'18px'} fontWeight={'500'}>
                            {errorReason || 'Please fix validation errors before saving'}
                        </DialogContentText>
                        {validationErrors.length > 0 && (
                            <Box sx={{ mt: 2, textAlign: 'left' }}>
                                <ul style={{ margin: 0 }}>
                                    {validationErrors.map((error, index) => (
                                        <li key={index}>
                                            <Typography variant="body2" color="error">{error}</Typography>
                                        </li>
                                    ))}
                                </ul>
                            </Box>
                        )}
                    </DialogContent>
                    <DialogActions style={{ justifyContent: "center" }}>
                        <Button id="create-asset-v2-button-close"  className="p-btn p-btnCancel" onClick={() => setError(false)}>Close</Button>
                    </DialogActions>
                </Box>
            </Dialog>

            <Dialog open={isBillingAddress} >
                <DialogTitle >
                </DialogTitle>
                <Box paddingLeft={8} paddingRight={8}>
                    <DialogContent >
                        <Box paddingLeft={8} paddingRight={8}>
                            <Typography textAlign={'center'} color='black' fontSize={'20px'} fontWeight={'400'} >
                                Your billing address is empty ,Set this as your billing address ?
                            </Typography>
                        </Box>
                    </DialogContent>
                    <DialogActions style={{ justifyContent: "center" }}>
                        {assetDataObject ? (
                            <>
                                <Button id="v2-billing-update-button-yes" className="p-btn p-btnPrimary" onClick={handleUpdateBillingAddress}>Yes</Button>
                                <Button id="v2-billing-update-button-no" className="p-btn p-btnCancel" onClick={handleCancelUpdateBillingAddress}>No</Button>
                            </>
                        ) : (
                            <>
                                <Button id="v2-billing-cancel-button-yes" className="p-btn p-btnPrimary" onClick={handleBillingAddress}>Yes</Button>
                                <Button id="v2-billing-cancel-button-no" className="p-btn p-btnCancel" onClick={handleCancelBillingAddress}>No</Button>
                            </>
                        )}
                    </DialogActions>
                </Box>
            </Dialog>

            <Dialog open={noChange} >
                <DialogTitle className='success' sx={{ backgroundColor: "rgb(255,193,0) !important" }}>
                    <Box paddingLeft={8} paddingRight={8}>
                        <Typography textAlign={'center'}>
                            <InfoOutlinedIcon sx={{ fontSize: "50px", color: "white" }} />
                        </Typography>
                    </Box>
                </DialogTitle>
                <Box paddingLeft={8} paddingRight={8}>
                    <DialogContent >
                        <DialogContentText textAlign={'center'} fontSize={'18px'} fontWeight={'500'}>No Value has been Changed</DialogContentText>
                    </DialogContent>
                    <DialogActions style={{ justifyContent: "center" }}>
                        <Button id="create-asset-v2-button-no-change" className="p-btn p-btnPrimary" onClick={handleNoChange}>OK</Button>
                    </DialogActions>
                </Box>
            </Dialog>
        </>
    );
}

export default CreateAsset_v3;