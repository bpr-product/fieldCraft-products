/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : List Asset Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import "./ListAsset.scss";
import React, { useContext, useState } from "react";
import {
  Backdrop,
  Box,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Fade,
  FormControl,
  FormControlLabel,
  FormLabel,
  Grid,
  Grow,
  IconButton,
  InputLabel,
  Menu,
  MenuItem,
  Paper,
  Radio,
  RadioGroup,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  useMediaQuery,
  Zoom,
} from "@mui/material";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import MoreVertOutlinedIcon from "@mui/icons-material/MoreVertOutlined";
import TelegramIcon from "@mui/icons-material/Telegram";
import RemoveRedEyeIcon from "@mui/icons-material/RemoveRedEye";
import CheckCircleOutlinedIcon from "@mui/icons-material/CheckCircleOutlined";
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded";
import FileDownloadOutlinedIcon from "@mui/icons-material/FileDownloadOutlined";
import RefreshIcon from "@mui/icons-material/Refresh";
import AssignmentIcon from "@mui/icons-material/Assignment";
import HourglassTopIcon from "@mui/icons-material/HourglassTop";
import QuestionAnswerIcon from "@mui/icons-material/QuestionAnswer";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import StraightenIcon from "@mui/icons-material/Straighten";
import CloseIcon from "@mui/icons-material/Close";
import { useNavigate } from "react-router";
import { useLocation } from "react-router-dom";
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import { useTheme } from "@mui/material/styles";
import CircularProgress from "@mui/material/CircularProgress";
import { GoogleMap, MarkerF } from "@react-google-maps/api";
import { useGoogleMaps } from "../../context/GoogleMapsContext";
import { getAsset, deleteAsset } from "../../services/AssetService";
import { getAllAssetDocument, getAssetDocumentById } from "../../services/AssetDocumentService";
import CustomDialog from "../../pages/Dialog";
import { TenantContext, UserContext, useHandleUnauthorized } from "../../hooks/UserSession";
import { getRequest, getServiceRequest, orderRequest, statusTrack } from "../../services/MeasurementService";
import { getAllServices, getSubscriberById } from "../../services/FeaturesService";
import { getFeatureAccess } from "../../utils/getFeatureAccess";
import { map } from "lodash";
import DeleteForeverRoundedIcon from "@mui/icons-material/DeleteForeverRounded";
import fieldcraftlogo from "../../assets/logo.gif";
import { getValidTradeData } from "../../services/BidService";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import AccessTimeRoundedIcon from "@mui/icons-material/AccessTimeRounded";
import TrackChangesRoundedIcon from "@mui/icons-material/TrackChangesRounded";
import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import AutoEstimateButton from "./autoEstimate/AutoEstimateButton";

const containerStyle = {
  width: "100%",
  height: "180px",
};

const libraries: any = ["places"];

const ListAsset: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const theme = useTheme();
  const { accessToken, accessTokenPayload } = useOidcAccessToken();
  const { handleUnauthorized } = useHandleUnauthorized();
  const { tenantSessionDetails } = useContext(TenantContext);
  const { userSessionDetails } = useContext(UserContext);
  const isSmDown = useMediaQuery(theme.breakpoints.down("sm"));
  const [assetData, setAssetData] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [openDialog, setOpenDialog] = React.useState(false);
  const [openReportData, setOpenReportData] = React.useState(false);
  const [openDialogs, setOpenDialogs] = React.useState(false);
  const [openRequestDialogs, setOpenRequestDialogs] = React.useState(false);
  const [deleteSuccess, setDeleteSuccess] = React.useState(false);
  const [deleteError, setDeleteError] = React.useState(false);
  const [requestError, setRequestError] = React.useState(false);
  const [role, setRole] = React.useState("");
  const [statusData, setStatusData] = React.useState<any[]>([]);
  const [assetId, setAssetId] = React.useState<any>([]);
  const [isLoading, setIsLoading] = React.useState(false);
  const [openRequest, setOpenRequest] = React.useState(false);
  const [reportData, setReportData] = React.useState<any>([]);
  const [asset, setAsset] = React.useState<any>([]);
  const [matchedRequest, setMatchedRequest] = React.useState<any>([]);
  const [step, setStep] = useState(1);
  const [selectedTradeName, setSelectedTradeName] = useState("");
  const [selectedService, setSelectedService] = useState("");
  const [isModifiedRecent, setIsModifiedRecent] = useState("");
  const [requestAssetId, setRequestAssetId] = React.useState<any>([]);
  const [requestAsset, setRequestAsset] = React.useState<any>(null);
  const serviceName = "EagleView";
  const [service, setService] = React.useState<any>([]);
  const [subscriber, setSubscriber] = React.useState<any>([]);
  const [errorCode, setErrorCode] = React.useState<any>(undefined);
  const [errorReason, setErrorReason] = React.useState<any>(undefined);
  const [errorMessage, setErrorMessage] = React.useState<any>(undefined);
  const [timestamp, setTimestamp] = React.useState<any>(undefined);
  const [tenantTrades, setTenantTrades] = useState<any[]>([]);
  const [requestSuccess, setRequestSuccess] = useState<boolean>(false);
  const [dialogContent, setDialogContent] = useState<string>('');
  const [anchorEl, setAnchorEl] = useState<any>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [assetIndex, setAssetIndex] = React.useState<any>();
  const [selectedData, setSelectedData] = React.useState<any>(null);
  const onUnmount = React.useCallback(function callback(mapInstance: any) { }, []);

  const { isLoaded } = useGoogleMaps();

  const trades = React.useMemo(() => {
    if (Array.isArray(tenantTrades) && tenantTrades.length > 0) {
      return tenantTrades.map(trade => ({
        id: trade.tradeOid || trade.id,
        name: trade.name
      }));
    }
    return [
    ];
  }, [tenantTrades]);

  const statusMap: any = {
    "1": {
      label: "The order has been created within the EagleView system.",
      color: "#757575",
      icon: <AssignmentIcon sx={{ color: "#757575", mr: 1 }} />,
    },
    "2": {
      label: "The report has been created and is progressing through the workflow.",
      color: "#FFA000",
      icon: <HourglassTopIcon sx={{ color: "#FFA000", mr: 1 }} />,
    },
    "3": {
      label: "Awaiting customer response.",
      color: "#FF8A65",
      icon: <QuestionAnswerIcon sx={{ color: "#FF8A65", mr: 1 }} />,
    },
    "4": {
      label: "The report is closed.",
      color: "#E91E63",
      icon: <CheckCircleIcon sx={{ color: "#E91E63", mr: 1 }} />,
    },
    "5": {
      label: "The measurement data is ready.",
      color: "#4CAF50",
      icon: <CheckCircleOutlinedIcon sx={{ color: "#4CAF50", mr: 1 }} />,
    },
  };

  React.useEffect(() => {
    setRole(userSessionDetails?.role?.name);
    getAssets();
    fetchData();
    fetchTenantTradeData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const getAssets = async () => {
    if (location.state === null || (location.state as any)?.res === undefined) {
      const assetDatas = await getAsset(accessTokenPayload.oid, accessToken);
      await getAllAssetDocument(accessToken);
      handleUnauthorized(assetDatas);
      setAssetData(assetDatas?.data);

      const payload = map(assetDatas?.data, (a: any) => a.id);
      handleStatusTrack(payload);
      setLoading(false);
    } else {
      const resState: any = (location.state as any)?.res;
      if (resState?.status === 200 || resState?.status === 201) {
        const assetDatas = await getAsset(resState?.data, accessToken);
        handleUnauthorized(assetDatas);
        setAssetData(assetDatas?.data);

        const payload = map(assetDatas?.data, (a: any) => a.id);
        handleStatusTrack(payload);
        setLoading(false);
      } else {
        setAssetData([]);
        setLoading(false);
      }
    }
  };

  const fetchData = async () => {
    try {
      const featureAccess = await getFeatureAccess(accessToken);

      if (featureAccess.eagleViewAllowed || featureAccess.projectHubAllowed) {
        const tenantIdValue = tenantSessionDetails?.tenantid;

        const response = await getSubscriberById(accessToken, tenantIdValue);
        const allSubscribers = response.data || [];

        const activeSubscriberServiceIds = allSubscribers
          .filter((sub: any) => sub?.status === "Active")
          .map((sub: any) => sub.serviceMaster);

        setSubscriber(allSubscribers);

        const res = await getAllServices(accessToken);
        const allServices = res.data || [];

        const filteredServices = allServices.filter(
          (svc: any) => svc?.status === "Active" && activeSubscriberServiceIds.includes(svc?.id)
        );

        setService(filteredServices);
      }
    } catch (error) { }
  };

  const fetchTenantTradeData = async () => {
    try {
      const result = await getValidTradeData(accessToken);
      if (result && result.data) {
        if (result.data.trades && Array.isArray(result.data.trades)) {
          setTenantTrades(result.data.trades);
        } else if (Array.isArray(result.data)) {
          setTenantTrades(result.data);
        } else {
          setTenantTrades([]);
        }
      }
    } catch (error) {
      console.error('Error fetching trades:', error);
      setTenantTrades([]);
    } finally {
      setLoading(false);
    }
  };

  const handleMenuOpen = async (event: any, data: any, index: any) => {
    setAnchorEl(event.currentTarget);
    setSelectedData(data);
    const res = await getRequest(data?.id, accessToken);
    setAssetIndex(index);
    setReportData(res?.ok ? res?.data : null);
    setMenuOpen(true);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setSelectedData(null);
    setMenuOpen(false);
  };

  const handleDialogOpen = (assetIdValue: any) => {
    setAssetId(assetIdValue);
    setOpenDialog(true);
  };

  const handleDialogClose = () => {
    setOpenDialog(false);
    setOpenRequestDialogs(false);
    setOpenReportData(false);
    setRequestError(false);
    setOpenRequest(false);
    navigate("/assetList");
    setRequestSuccess(false);

  };

  const handleBack = () => {
    if (step > 1) setStep(step - 1);
  };

  const onNextPage = () => {
    // Version is decided by FEATURE_FLAGS.CreateAsset_version in CreateAssetCommon.
    navigate("/asset");
  };

  const handleTradeSelectName = async (event: any) => {
    setSelectedTradeName(event.target.value);
    setStep(2);
  };

  const handleServiceSelect = (event) => {
    setSelectedService(event.target.value);
  };

  const convertLocation = (locationString: any) => {
    const st = (locationString ?? "").split(",");
    return {
      lat: Number(st?.[0]),
      lng: Number(st?.[1]),
    };
  };

  const onDeleteAsset = async (id: any) => {
    await setOpenDialog(false);
    setOpenDialogs(true);

    const result: any = await deleteAsset(id, accessToken);

    if (result.status === 204) {
      setDeleteSuccess(true);
      getAssets();
    } else {
      setDeleteError(true);
      setErrorCode(result?.errorCode);
      setErrorReason(result?.message);
      setErrorMessage(result?.debugMessage);
      setTimestamp(result?.timestamp);
    }
  };

  const onEditPage = (assetObjectData: any) => {
    const res = (location.state as any)?.res;
    // Version is decided by FEATURE_FLAGS.CreateAsset_version in CreateAssetCommon; the
    // asset object is what the editor needs to load for edit.
    navigate("/asset", { state: { assetObjectData, res } });
  };

  const RequestOrder = async (assetObjectData: any) => {
    setStep(1);
    setSelectedTradeName("");
    setSelectedService("");
    setIsModifiedRecent("");
    setRequestAsset(assetObjectData);
    setRequestAssetId(assetObjectData.id);
    setOpenRequest(true);

    const payload = [assetObjectData.id];
    const request = await getServiceRequest(payload, accessToken);

    const existingServiceRequests = request.data?.filter((req: any) => req?.request?.assetId === assetObjectData.id);
    setMatchedRequest(existingServiceRequests);
  };

  const handleModifiedRecentSelect = (event: any) => {
    setIsModifiedRecent(event.target.value);
  };

  const PlaceOrder = async (assetUid: any) => {
    const selected = service.find((s: any) => s?.name === selectedService);
    const serviceId = selected?.id;

    const matchingSubscriber = Array.isArray(subscriber)
      ? subscriber.find((sub: any) => sub?.serviceMaster === serviceId)
      : null;

    const subscriberId = matchingSubscriber?.id;
    setOpenRequest(false);
     setIsLoading(true);

    try {
      const param: any = {
        requestedBy: accessTokenPayload.oid,
        status: "Active",
        invalid: false,
        serviceSubscriber: subscriberId,
        request: {
          assetId: assetUid,
          isModified: isModifiedRecent === "yes",
        },
        requestedByType: "Customer",
      };

      const orderRes: any = await orderRequest(param, accessToken);

      if (orderRes?.data === true) {
        const payload = [assetUid];
        await handleStatusTrack(payload);
        setOpenRequestDialogs(true);
        setRequestSuccess(true);

      } else {

        setOpenRequest(false);  // Close main dialog on error
        setOpenRequestDialogs(true);
        setRequestError(true);
        setErrorCode(orderRes?.errorCode);
        setErrorReason(orderRes?.message);
        setErrorMessage(orderRes?.debugMessage);
        setTimestamp(orderRes?.timestamp);
        setDialogContent("Place order request failed. Please try again.");
      }
    } catch (err) {
      setRequestError(true);
    } finally {
      setIsLoading(false);
      navigate("/assetList");
    }
  };

  const handleStatusTrack = async (assetIds: any) => {
    try {
      const res = await statusTrack(assetIds, accessToken);
      const dataArray = res?.data;
      setStatusData(dataArray);

      dataArray?.forEach((item: any) => {
        const aId = item.request?.assetId;
        setAssetId(aId);
      });
    } catch (err) {
      console.error("Error fetching statusTrack:", err);
    }
  };

  const handleGetReport = async (id: any) => {
    const res = await getRequest(id, accessToken);
    const assetDocs = await getAssetDocumentById(id, accessToken);

    setReportData(res?.data);
    setAsset(assetDocs?.data);

    if (res?.data != null && assetDocs?.data !== null) {
      setOpenReportData(true);
    }
  };

  const handleDownload = (dataString: any) => {
    try {
      const url = dataString?.ReportDownloadLink;
      if (url) {
        const link = document.createElement("a");
        link.href = url;
        link.download = `Report_${dataString.ReportId || "file"}.pdf`;
        link.target = "_blank";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      }
    } catch (error) { }
  };


  const handleRefresh = async (assetIdValue: any) => {
    try {
      const payload = [assetIdValue];
      await handleStatusTrack(payload);
    } catch { }
  };

  return (
    <Box className="listAssetRoot">
      {/* Header */}
      <Box className="headerOuter">
        <Box className="headerCard">
          <Box className="headerGlow" />

          <Box className="headerRow">
            <Box className="headerLeft">
              <Typography className="headerTitle">{location?.state?.res?.url !== "/admin" ? "My Assets" : `${location?.state?.res?.customerName} Asset List`}</Typography>
              <Typography className="headerSubtitle">
                Manage assets, request measurements, and download reports.
              </Typography>

              <Box className="headerChipsRow">
                <Box className="headerChip headerChipNeutral">Total Assets: {assetData?.length ?? 0}</Box>
                <Box className="headerChip headerChipBlue">Active view</Box>
              </Box>
            </Box>

            {role !== "Admin" && role !== "admin" && location?.state?.res?.url !== "/admin" && (
              <Box className="ctaRow">
                <Box className="versionWrap">
                  <Button
                    id="list-asset-button-add-new-asset"
                    size="medium"
                    onClick={onNextPage}
                    className="p-btn p-btnPrimary"
                    startIcon={<AddOutlinedIcon className="personIcon" />}
                  >
                    Add New Asset
                  </Button>
                  {/* Version Badge removed: the Asset page version is now driven solely by
                      FEATURE_FLAGS.CreateAsset_version (CreateAssetCommon no longer reads
                      location.state.version), so a manual v1/v2 picker here was dead UI. */}
                </Box>
              </Box>
            )}
          </Box>
        </Box>
      </Box>
      {loading ? (
        <Box className="loadingBox">
          <Box className="loadingTile">
            <CircularProgress />
          </Box>
          <Typography className="loadingText">Loading assets…</Typography>
        </Box>
      ) : (
        <Fade in timeout={350}>
          <Box className="contentOuter">
            {assetData?.length === 0 ? (
              <Box className="emptyState">
                <Typography className="emptyTitle">No Assets</Typography>
                <Typography className="emptySubtitle">Please create an asset to get started.</Typography>
              </Box>
            ) : (
              <Grid container spacing={2.5}>
                {assetData?.map((data: any, index: any) => {
                  const statusItem = Array.isArray(statusData)
                    ? statusData.find((item: any) => item.request?.assetId === data.id)
                    : null;

                  let statusMessage = "";
                  let statusColor = "#f5f5f5";
                  let statusIcon: any = "";

                  if (statusItem?.statusHistory) {
                    try {
                      const history = JSON.parse(statusItem.statusHistory || "[]");
                      const validEntry = history.find((entry: any) => entry.invalid === false);
                      if (validEntry?.status_id && statusMap[validEntry.status_id]) {
                        statusMessage = statusMap[validEntry.status_id].label;
                        statusColor = statusMap[validEntry.status_id].color;
                        statusIcon = statusMap[validEntry.status_id].icon;
                      }
                    } catch { }
                  }

                  return (
                    <Grid item xs={12} md={6} lg={6} xl={4} key={data?.id ?? index}>
                      <Grow in timeout={260 + index * 70}>
                        <Card variant="outlined" className="assetCard">
                          <Box className="assetCardInner">
                            {/* Top row: title + actions */}
                            <Box className="assetTopRow">
                              <Box className="assetTopLeft">
                                <Typography className="assetName" title={data.name}>
                                  {data.name}
                                </Typography>

                                <Typography className="assetAddress" title={data.address}>
                                  {data.address}
                                </Typography>

                                <Typography className="assetMetaLine">
                                  {data.city} , {data.state} • {data.country} , {data.zipCode}
                                </Typography>
                              </Box>

                              <Box className="assetTopRight">
                                <Tooltip title="Actions">
                                  <IconButton
                                    className="actionIconBtn"
                                    onClick={(e) => handleMenuOpen(e, data, index)}
                                  >
                                    <MoreVertOutlinedIcon />
                                  </IconButton>
                                </Tooltip>

                                <Menu
                                  anchorEl={anchorEl}
                                  open={menuOpen && assetIndex === index ? true : false}
                                  onClose={handleMenuClose}
                                  anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                                  transformOrigin={{ vertical: "top", horizontal: "right" }}
                                  MenuListProps={{ className: "assetMenuList" }}
                                  PaperProps={{ className: "assetMenuPaper" }}
                                >
                                  <Box className="menuHeader">
                                    <Typography className="menuHeaderTitle">Actions</Typography>
                                    <Typography className="menuHeaderSub">
                                      Choose what you want to do with this asset
                                    </Typography>
                                  </Box>

                                  <Divider className="thinDivider" />

                                  <Box className="menuItemsWrap">
                                    {/* Edit */}
                                    <MenuItem
                                      className="menuItemRow"
                                      onClick={() => {
                                        onEditPage(selectedData);
                                        handleMenuClose();
                                      }}
                                    >
                                      <Box className="menuItemLeft">
                                        <Box className="menuIconBox menuIconBlue">
                                          <EditIcon fontSize="small" />
                                        </Box>
                                        <Typography className="menuItemTitle">Edit</Typography>
                                      </Box>
                                      <Typography className="menuItemHint">View & Edit</Typography>
                                    </MenuItem>

                                    {/* Delete */}
                                    <MenuItem
                                      className="menuItemRow"
                                      onClick={() => {
                                        handleDialogOpen(selectedData?.id);
                                        handleMenuClose();
                                      }}
                                    >
                                      <Box className="menuItemLeft">
                                        <Box className="menuIconBox menuIconRed">
                                          <DeleteIcon fontSize="small" />
                                        </Box>
                                        <Typography className="menuItemTitle">Delete</Typography>
                                      </Box>
                                      <Typography className="menuItemHint">Remove</Typography>
                                    </MenuItem>

                                    {/* Request Measurement */}
                                    {!statusData?.find((item: any) => item?.assetUid === selectedData?.assetUid) && (
                                      <MenuItem
                                        className="menuItemRow"
                                        onClick={() => {
                                          RequestOrder(selectedData);
                                          handleMenuClose();
                                        }}
                                      >
                                        <Box className="menuItemLeft">
                                          <Box className="menuIconBox menuIconGreen">
                                            <TelegramIcon fontSize="small" />
                                          </Box>
                                          <Typography className="menuItemTitle">Request Measurement</Typography>
                                        </Box>
                                        <Typography className="menuItemHint">Send request</Typography>
                                      </MenuItem>
                                    )}

                                    {/* View Measurement */}
                                    {reportData != null && (!Array.isArray(reportData) || reportData.length > 0) && (
                                      <MenuItem
                                        className="menuItemRow"
                                        onClick={() => {
                                          handleGetReport(selectedData?.id);
                                          handleMenuClose();
                                        }}
                                      >
                                        <Box className="menuItemLeft">
                                          <Box className="menuIconBox menuIconIndigo">
                                            <RemoveRedEyeIcon fontSize="small" />
                                          </Box>
                                          <Typography className="menuItemTitle">View Measurement</Typography>
                                        </Box>
                                        <Typography className="menuItemHint">Open</Typography>
                                      </MenuItem>
                                    )}
                                  </Box>
                                </Menu>
                              </Box>
                            </Box>

                            {/* Map + Meta */}
                            <Divider className="cardDivider" />

                            <Grid container spacing={2}>
                              <Grid item xs={12} sm={5}>
                                <Box className="mapBox">
                                  <Box className="mapOverlay" />

                                  {isLoaded ? (
                                    <GoogleMap
                                      mapContainerStyle={containerStyle}
                                      center={convertLocation(data.location)}
                                      zoom={20}
                                      onUnmount={onUnmount}
                                      options={{
                                        zoomControl: false,
                                        streetViewControl: false,
                                        mapTypeControl: false,
                                        fullscreenControl: false,
                                        keyboardShortcuts: false,
                                      }}
                                    >
                                      <MarkerF position={convertLocation(data.location)} />
                                      <></>
                                    </GoogleMap>
                                  ) : (
                                    <Box className="mapLoading">
                                      <CircularProgress size={22} />
                                    </Box>
                                  )}
                                </Box>
                              </Grid>

                              <Grid item xs={12} sm={7}>
                                <Box className="metaGrid">
                                  <Box className="metaTile">
                                    <Typography className="metaLabel">Stories</Typography>
                                    <Typography className="metaValue">{data.stories}</Typography>
                                  </Box>

                                  <Box className="metaTile">
                                    <Typography className="metaLabel">Area (sq)</Typography>
                                    <Typography className="metaValue">{data.area}</Typography>
                                  </Box>

                                  {/* ✅ Asset Type display flex */}
                                  <Box className="metaTile metaRow">
                                    <Typography className="metaLabel">Asset Type</Typography>
                                    <Typography className="metaValue metaValueRight" title={data.assetType}>
                                      {data.assetType}
                                    </Typography>
                                  </Box>
                                </Box>
                              </Grid>
                            </Grid>


                            {statusMessage ? (
                              <Box className="statusStrip">
                                {statusIcon && <Box className="statusIconWrap">{statusIcon}</Box>}

                                <Box className="statusTextWrap">
                                  <Typography className="statusMain" style={{ color: statusColor as any }}>
                                    {statusMessage}
                                  </Typography>
                                  <Typography className="statusSubtext">Latest status for this asset</Typography>
                                </Box>

                                <Tooltip title="Refresh Status">
                                  <IconButton className="statusActionBtn" onClick={() => handleRefresh(data?.id)}>
                                    <RefreshIcon />
                                  </IconButton>
                                </Tooltip>
                              </Box>
                            ) : (
                              <Box className="statusStrip tipStrip">
                                <Box className="statusIconWrap">
                                  <StraightenIcon className="tipIcon" />
                                </Box>

                                <Box className="statusTextWrap">
                                  <Typography className="statusMain tipTitle">No measurement status yet</Typography>
                                  <Typography className="statusSubtext">
                                    Try <b>Request Measurement</b> to get accurate data.
                                  </Typography>
                                </Box>

                                <Tooltip title="Request Measurement">
                                  <Button
                                    id="list-asset-button-request"
                                    className="statusCtaBtn"
                                    onClick={() => RequestOrder(data)}
                                    startIcon={<TelegramIcon />}
                                  >
                                    Request
                                  </Button>
                                </Tooltip>
                              </Box>
                            )}

                            {/* Asset-triggered Auto Estimate (step 5) — one trigger per asset
                                card. Deliberately NOT `.statusStrip`: the ternary above always
                                renders exactly one of those panels, so reusing the class stacked
                                a second, identical-looking panel on every card that held only a
                                button and none of the icon/text/action a status strip implies. */}
                            <Box sx={{ mt: 1 }}>
                              <AutoEstimateButton
                                assetUid={data.assetUid}
                                assetStatus={data.status}
                              />
                            </Box>
                          </Box>
                        </Card>
                      </Grow>
                    </Grid>
                  );
                })}
              </Grid>
            )}
          </Box>
        </Fade>
      )}

      {/* Loading */}
      <Dialog
        open={openDialog}
        onClose={() => { }} disableEscapeKeyDown
        maxWidth="xs"
        fullWidth
        TransitionComponent={Zoom}
        TransitionProps={{ timeout: 260 }}
        keepMounted
        BackdropComponent={Backdrop}
        BackdropProps={{
          timeout: 250,
          sx: { backgroundColor: "rgba(2, 6, 23, 0.62)", backdropFilter: "blur(6px)" },
        }}
        PaperProps={{
          className: "delDialogPaper",
        }}
      >
        {/* Header */}
        <Box className="delDialogHeader">
          <Box className="delDialogBadge">
            <ErrorOutlineRoundedIcon className="delDialogWarnIcon" />
          </Box>

          <IconButton onClick={handleDialogClose} className="delDialogCloseBtn">
            <CloseIcon />
          </IconButton>
        </Box>

        <DialogContent className="delDialogBody">
          <Fade in={openDialog} timeout={260}>
            <Box>
              <Typography className="delDialogTitle">Delete this asset?</Typography>

              <Typography className="delDialogSubtitle">
                This action cannot be undone. The asset and related data will be removed permanently.
              </Typography>
            </Box>
          </Fade>
        </DialogContent>

        <DialogActions className="delDialogActions">
          <Button id="list-asset-delete-button-cancel" onClick={handleDialogClose} className="delBtnCancel">
            Cancel
          </Button>

          <Button
            id="list-asset-button-delete"
            onClick={() => onDeleteAsset(assetId)}
            autoFocus
            className="delBtnDanger"
            startIcon={<DeleteForeverRoundedIcon />}
          >
            Delete Asset
          </Button>
        </DialogActions>
      </Dialog>

      {/* Request Dialog */}

     <Dialog
  open={requestSuccess}
  onClose={() => {}}
  disableEscapeKeyDown
  fullWidth
  maxWidth="sm"
  PaperProps={{ className: "requestSuccessPaper" }}
>
  {/* HEADER */}
  <Box className="requestSuccessHeader">
    <Box className="requestSuccessHeaderGlow" />

    <Stack direction="row" alignItems="center" spacing={1.4} className="requestSuccessHeaderRow">
      {/* icon bubble */}
      <Box className="requestSuccessIconBubble">
        <CheckCircleRoundedIcon sx={{ fontSize: 30 }} />
      </Box>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography className="requestSuccessTitle">
          Measurement request submitted!
        </Typography>
        <Typography className="requestSuccessSubtitle">
          Your request has been sent to our measurement partner.
        </Typography>
      </Box>

      <IconButton onClick={handleDialogClose} className="requestSuccessCloseBtn">
        <CloseRoundedIcon />
      </IconButton>
    </Stack>
  </Box>

  {/* CONTENT */}
  <DialogContent className="requestSuccessContent">
    <Paper variant="outlined" className="requestSuccessNextCard">
      <Typography className="requestSuccessNextTitle">
        What happens next?
      </Typography>

      <Stack spacing={1.2}>
        <Stack direction="row" spacing={1.2} alignItems="flex-start">
          <Box className="requestSuccessInfoIcon">
            <AccessTimeRoundedIcon sx={{ fontSize: 20 }} />
          </Box>

          <Box sx={{ flex: 1 }}>
            <Typography className="requestSuccessInfoTitle">ETA: 2-3 hours</Typography>
            <Typography className="requestSuccessInfoText" color="text.secondary">
              The measurement report will be generated and attached automatically.
            </Typography>
          </Box>
        </Stack>

        <Stack direction="row" spacing={1.2} alignItems="flex-start">
          <Box className="requestSuccessInfoIcon">
            <TrackChangesRoundedIcon sx={{ fontSize: 20 }} />
          </Box>

          <Box sx={{ flex: 1 }}>
            <Typography className="requestSuccessInfoTitle">
              Track status in Requests
            </Typography>
            <Typography className="requestSuccessInfoText" color="text.secondary">
              You can view progress and download the report once it’s ready.
            </Typography>
          </Box>
        </Stack>
      </Stack>
    </Paper>

    <Box className="requestSuccessOkWrap">
      <Button
        id="list-asset-button-ok"
        onClick={handleDialogClose}
        variant="contained"
        className="requestSuccessOkBtn"
      >
        OK, got it
      </Button>
    </Box>
  </DialogContent>
</Dialog>
      {/* Request Flow Dialog */}
      <Dialog
        open={openRequest}
        onClose={() => { }}
        disableEscapeKeyDown
        maxWidth="sm"
        fullWidth
        PaperProps={{ className: "requestFlowPaper" }}
      >
        <DialogTitle className="requestFlowHeader">
          <Box className="requestFlowHeaderLeft">
            <Box className="requestFlowIconBox">
              <StraightenIcon className="requestFlowIcon" />
            </Box>
            Request Measurement Service
          </Box>

          <Box className="stepBadge">Step {step} / 3</Box>
        </DialogTitle>

        <DialogContent className="requestFlowContent">
          {(requestAsset?.name || requestAsset?.address) && (
            <Card variant="outlined" className="propertyInfoCard">
              <CardContent className="propertyInfoContent">
                <Typography variant="subtitle2" className="propertyInfoTitle">
                  Property Information
                </Typography>

                <Box className="propertyInfoGrid">
                  {requestAsset?.name && (
                    <Typography variant="body2">
                      <Box component="span" className="boldLabel">Property:</Box>{" "}
                      {requestAsset.name}
                    </Typography>
                  )}

                  <Typography variant="body2" className="addressLine">
                    <Box component="span" className="boldLabel">Address:</Box>{" "}
                    <Box component="span">
                      {[
                        requestAsset?.address,
                        requestAsset?.city,
                        requestAsset?.state,
                        requestAsset?.zipCode,
                      ]
                        .filter((v) => (v ?? "").toString().trim().length > 0)
                        .join(", ")}
                    </Box>
                  </Typography>
                </Box>
              </CardContent>
            </Card>
          )}

          {/* Step 1 */}
          {step === 1 && (
            <Card variant="outlined" className="stepCard">
              <CardContent className="stepCardContent">
                <Typography className="stepTitle">Trade Selection</Typography>

                <FormControl fullWidth size="small">
                  <InputLabel id="trade-select-label">Select Trade</InputLabel>
                  <Select
                    labelId="trade-select-label"
                    id="trade-select"
                    value={selectedTradeName}
                    onChange={handleTradeSelectName}
                    label="Select Trade"
                    className="selectRounded"
                    MenuProps={{
                      disablePortal: true,
                      anchorOrigin: { vertical: "bottom", horizontal: "left" },
                      transformOrigin: { vertical: "top", horizontal: "left" },
                      PaperProps: { className: "selectMenuPaper" },
                    }}
                  >
                    {trades.map((trade: any) => (
                      <MenuItem key={trade.id} value={String(trade.id)}>
                        {trade.name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </CardContent>
            </Card>
          )}

          {/* Step 2 */}
          {step === 2 && (
            <Card variant="outlined" className="stepCard">
              <CardContent className="stepCardContent">
                <Typography className="stepTitle">Service Selection</Typography>

                <FormControl fullWidth size="small">
                  <InputLabel id="service-select-label">Select Service</InputLabel>
                  <Select
                    labelId="service-select-label"
                    id="service-select"
                    value={selectedService || ""}
                    onChange={handleServiceSelect}
                    label="Select Service"
                    className="selectRounded"
                    MenuProps={{
                      disablePortal: true,
                      anchorOrigin: { vertical: "bottom", horizontal: "left" },
                      transformOrigin: { vertical: "top", horizontal: "left" },
                      PaperProps: { className: "selectMenuPaper" },
                    }}
                  >
                    {service.map((s: any) => (
                      <MenuItem key={s.id} value={s.name}>
                        {s.name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </CardContent>
            </Card>
          )}

          {/* Step 3 */}
          {selectedService === serviceName && step === 3 && (
            <Card variant="outlined" className="stepCard">
              <CardContent className="stepCardContent">
                <Typography className="stepTitle">Property Modification</Typography>

                <FormControl component="fieldset" fullWidth>
                  <FormLabel className="formLabel">
                    Has the roof been modified recently?
                  </FormLabel>

                  <RadioGroup value={isModifiedRecent} onChange={handleModifiedRecentSelect} row>
                    <FormControlLabel value="yes" control={<Radio />} label="Yes" />
                    <FormControlLabel value="no" control={<Radio />} label="No" />
                  </RadioGroup>
                </FormControl>

                {matchedRequest && matchedRequest?.length > 0 && (
                  <Box className="existingRequestWarn">
                    <Typography variant="body2" className="existingRequestWarnText">
                      Existing service request found for this property.
                    </Typography>
                  </Box>
                )}
              </CardContent>
            </Card>
          )}
        </DialogContent>

        <DialogActions className="requestFlowFooter">
          <Button id="list-asset-button-cancel" onClick={handleDialogClose} className="p-btn p-btnCancel requestFooterBtn">
            Cancel
          </Button>

          <Box className="footerRightBtns">
            {step > 1 && (
              <Button id="list-asset-button-back-step" onClick={handleBack} className="p-btn p-btnCancel requestFooterBtn">
                Back
              </Button>
            )}

            {step < 3 && (
              <Button
                id="list-asset-button-next-step"
                onClick={() => setStep((s) => s + 1)}
                className="p-btn p-btnPrimary requestFooterBtn"
                disabled={(step === 1 && !selectedTradeName) || (step === 2 && !selectedService)}
              >
                Next
              </Button>
            )}

            {step === 3 && (
              <Button
                id="list-asset-button-submit-request"
                onClick={() => PlaceOrder(requestAssetId)}
                className="p-btn p-btnPrimary requestFooterBtn"
                disabled={!isModifiedRecent || isLoading}
              >
                Submit Request
              </Button>
            )}
          </Box>
        </DialogActions>
      </Dialog>

      {/* Measurement Dialog */}
      <Dialog
        open={openReportData}
        onClose={handleDialogClose}
        fullScreen={isSmDown}
        maxWidth="md"
        fullWidth
        keepMounted
        BackdropComponent={Backdrop}
        BackdropProps={{
          timeout: 220,
          sx: { backgroundColor: "rgba(2,6,23,0.55)" },
        }}
        className="dialogRoot"
      >
        <DialogTitle className="dialogHeader">
          <Stack spacing={0.25}>
            <Typography className="dialogTitleText">Measurement</Typography>
            <Typography className="dialogSubText">View and download measurement reports</Typography>
          </Stack>

          <IconButton onClick={handleDialogClose} className="roundBtn">
            <CloseIcon />
          </IconButton>
        </DialogTitle>

        <DialogContent className="dialogContentPad">
          <Card elevation={0} className="dialogCard">
            <CardContent className="dialogCardContent">
              <TableContainer component={Paper} elevation={0} className="tableWrap">
                <Table stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell className="tableHeadCell">Date and Time</TableCell>
                      <TableCell className="tableHeadCell">Trade</TableCell>
                      <TableCell className="tableHeadCell">Service Used</TableCell>
                      <TableCell className="tableHeadCell" align="right">
                        Action
                      </TableCell>
                    </TableRow>
                  </TableHead>

                  <TableBody>
                    {asset?.map((row: any, i: number) => (
                      <TableRow key={i} className="tableRowHover">
                        <TableCell className="tableCellStrong">
                          {new Date(row?.createdAt).toLocaleString()}
                        </TableCell>
                        <TableCell>Roofing</TableCell>
                        <TableCell>EagleVIew</TableCell>
                        <TableCell align="right">
                          <IconButton className="roundBtn">
                            <FileDownloadOutlinedIcon fontSize="medium" onClick={() => handleDownload(reportData)} />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}

                    {!asset?.length && (
                      <TableRow>
                        <TableCell colSpan={4} className="emptyTableCell">
                          <Box className="emptyTableBox">
                            <Typography className="emptyTableTitle">No measurement data found</Typography>
                            <Typography className="emptyTableSub">
                              Once a measurement is generated, it will appear here.
                            </Typography>
                          </Box>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </TableContainer>

              <Divider className="thinDivider dialogDivider" />

              <Typography className="tipText">Tip: Click the download icon to save the report.</Typography>
            </CardContent>
          </Card>
        </DialogContent>

        <DialogActions className="dialogFooter">
          <Button id="list-asset-button-report-close" className="p-btn p-btnCancel dialogCloseBtn" onClick={handleDialogClose}>
            Close
          </Button>
        </DialogActions>
      </Dialog>

      {/* delete success dialog box */}
      <CustomDialog
        open={openDialogs}
        onClose={() => setOpenDialogs(false)}
        reload={() => null}
        conflictWarning={false}
        success={deleteSuccess}
        error={deleteError}
        errorCode={errorCode}
        debugMessage={errorMessage}
        timestamp={timestamp}
        detailedMessage={errorReason}
        Content={deleteSuccess ? "Deleted Successfully" : "Not Deleted Successfully"}
      />

      <CustomDialog
        open={requestError}
        onClose={() => setRequestError(false)}
        reload={() => null}
        conflictWarning={false}
        success={requestError}
        error={requestError}
        errorCode={errorCode}
        debugMessage={errorMessage}
        timestamp={timestamp}
        detailedMessage={errorReason}
        Content={requestError ? "Place order request failed try again!" : "Place order request failed try again!"}
      />


      <CustomDialog
        open={openRequestDialogs}
        onClose={handleDialogClose}
        reload={() => null}
        conflictWarning={false}
        success={""}
        error={requestError}
        errorCode={errorCode}
        debugMessage={errorMessage}
        timestamp={timestamp}
        detailedMessage={errorReason}
        Content={dialogContent}
      />

      {/* Backdrop */}
      <Backdrop sx={{ color: "#fff", zIndex: (t) => t.zIndex.drawer + 1 }} open={isLoading}>
        <Grid container alignItems="center" justifyContent="center">
          <Grid item md={12} style={{ textAlign: "center" }}>
            <img src={fieldcraftlogo} alt="Image" className="topBarLogo" />
          </Grid>
          <Grid item md={12} style={{ textAlign: "center" }}>
            <Typography className="loading" variant="h4" color="blue">
              Please Wait . . .
            </Typography>
          </Grid>
        </Grid>
      </Backdrop>
    </Box>
  );
};

export default ListAsset;