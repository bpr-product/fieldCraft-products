/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Live per-facet measurements table (editable pitch, select, delete)
 *                + totals header + JSON/PDF export for the RoofLens editor.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  Box, Button, Divider, IconButton, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  TextField, Tooltip, Typography,
} from "@mui/material";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import DownloadOutlinedIcon from "@mui/icons-material/DownloadOutlined";
import PictureAsPdfOutlinedIcon from "@mui/icons-material/PictureAsPdfOutlined";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { computeTotals, useRoofLensStore } from "./store/rooflensStore";
import { RoofMetrics } from "./types";

interface Props {
  onExportJson: () => void;
  onExportPdf: () => void;
}

/** A compact editable numeric field for a table cell (integer, >= 0), with a unit suffix. */
const NumCell: React.FC<{
  value: number;
  suffix?: string;
  width?: number;
  onCommit: (n: number) => void;
}> = ({ value, suffix, width = 56, onCommit }) => {
  // Local text state so the user can clear/retype freely; commit rounded value.
  const [text, setText] = React.useState<string>(String(Math.round(value)));
  React.useEffect(() => {
    setText(String(Math.round(value)));
  }, [value]);
  const commit = () => {
    const n = Number(text);
    onCommit(Number.isNaN(n) || n < 0 ? 0 : Math.round(n));
  };
  return (
    <Stack direction="row" alignItems="center" spacing={0.4} justifyContent="flex-end" onClick={(e) => e.stopPropagation()}>
      <TextField
        type="number"
        size="small"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        inputProps={{ min: 0, step: 1, style: { width, padding: "4px 6px", textAlign: "right" } }}
      />
      {suffix && <Typography variant="caption" color="text.secondary">{suffix}</Typography>}
    </Stack>
  );
};

/** A single label/value cell in the EagleView-style summary grid. */
const MetricItem: React.FC<{ label: string; value: string; note?: string }> = ({ label, value, note }) => (
  <Box sx={{ minWidth: 96 }}>
    <Stack direction="row" alignItems="center" spacing={0.3}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase", letterSpacing: 0.3 }}>
        {label}
      </Typography>
      {note && (
        <Tooltip title={note} arrow>
          <InfoOutlinedIcon sx={{ fontSize: 13, color: "text.disabled" }} />
        </Tooltip>
      )}
    </Stack>
    <Typography variant="body2" sx={{ fontWeight: 700 }}>
      {value}
    </Typography>
  </Box>
);

/** EagleView-style whole-structure metrics from the last Auto-Detect run. */
const MetricsSummary: React.FC<{ metrics: RoofMetrics }> = ({ metrics: m }) => {
  const { t } = useTranslation();
  const ft = (v?: number) => (typeof v === "number" ? `${Math.round(v).toLocaleString()} ft` : "—");
  const sqft = (v?: number) => (typeof v === "number" ? `${Math.round(v).toLocaleString()} sq ft` : "—");
  const lenWithCount = (len?: number, count?: number) =>
    typeof len === "number" ? `${Math.round(len).toLocaleString()} ft${count ? ` · ${count}` : ""}` : "—";

  return (
    <Box sx={{ mt: 1.5, p: 1.5, bgcolor: "grey.50", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        {t("asset.rooflens.measurements.roofReport")} <Typography component="span" variant="caption" color="text.secondary">{t("asset.rooflens.measurements.fromAutoDetect")}</Typography>
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2, rowGap: 1.5 }}>
        <MetricItem label={t("asset.rooflens.measurements.labelPredominantPitch")} value={m.predominant_pitch ?? "—"} />
        <MetricItem label={t("asset.rooflens.measurements.labelRidgesHips")} value={lenWithCount(m.ridge_hip_ft, (m.ridge_count ?? 0) + (m.hip_count ?? 0))} />
        <MetricItem label={t("asset.rooflens.measurements.labelValleys")} value={lenWithCount(m.valley_ft, m.valley_count)} />
        <MetricItem label={t("asset.rooflens.measurements.labelRakes")} value={lenWithCount(m.rake_ft, m.rake_count)} />
        <MetricItem label={t("asset.rooflens.measurements.labelEaves")} value={lenWithCount(m.eave_ft, m.eave_count)} />
        {(m.parapet_ft ?? 0) > 0 && (
          <MetricItem label={t("asset.rooflens.measurements.labelParapets")} value={lenWithCount(m.parapet_ft, m.parapet_count)} />
        )}
        <MetricItem label={t("asset.rooflens.measurements.labelEstAttic")} value={sqft(m.estimated_attic_sqft)} note={m.estimated_attic_confidence_note} />
        <MetricItem
          label={t("asset.rooflens.measurements.labelStories")}
          value={typeof m.stories === "number" ? String(m.stories) : m.number_of_stories ?? "—"}
          note={m.number_of_stories_confidence_note}
        />
        <MetricItem label={t("asset.rooflens.measurements.labelBuildingHeight")} value={ft(m.building_height_ft)} note={m.number_of_stories_confidence_note} />
        <MetricItem label={t("asset.rooflens.measurements.labelComplexity")} value={m.structure_complexity ?? "—"} note={m.structure_complexity_confidence_note} />
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.5 }}>
        {m.metrics_confidence_note ?? t("asset.rooflens.measurements.metricsConfidenceDefault")}
      </Typography>
    </Box>
  );
};

/** Combined roof report across ALL buildings (shown when there's more than one). */
const CombinedBuildingsSummary: React.FC = () => {
  const { t } = useTranslation();
  const allFacets = useRoofLensStore((s) => s.facets);
  const buildings = useRoofLensStore((s) => s.buildings);
  const metricsByBuilding = useRoofLensStore((s) => s.metricsByBuilding);

  const totalArea = Math.round(computeTotals(allFacets).totalRoofAreaSqft);
  const ms = buildings
    .map((b) => metricsByBuilding[b.id])
    .filter((m): m is RoofMetrics => !!m);
  const sum = (k: keyof RoofMetrics) => ms.reduce((a, m) => a + ((m[k] as number) ?? 0), 0);
  const ridgeHip = sum("ridge_ft") + sum("hip_ft");
  const parapet = sum("parapet_ft");
  const ln = (v: number) => (v > 0 ? `${Math.round(v).toLocaleString()} ft` : "—");

  return (
    <Box sx={{ p: 1.5, mb: 1.5, bgcolor: "primary.50", borderRadius: "8px", border: "1px solid", borderColor: "primary.100" }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        {t("asset.rooflens.measurements.allBuildingsCombined")} <Typography component="span" variant="caption" color="text.secondary">{t("asset.rooflens.measurements.buildingsCount", { n: buildings.length })}</Typography>
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2, rowGap: 1.5 }}>
        <MetricItem label={t("asset.rooflens.measurements.labelTotalRoofArea")} value={`${totalArea.toLocaleString()} sq ft`} />
        <MetricItem label={t("asset.rooflens.measurements.labelFacets")} value={String(allFacets.length)} />
        <MetricItem label={t("asset.rooflens.measurements.labelRidgesHips")} value={ln(ridgeHip)} />
        <MetricItem label={t("asset.rooflens.measurements.labelValleys")} value={ln(sum("valley_ft"))} />
        <MetricItem label={t("asset.rooflens.measurements.labelRakes")} value={ln(sum("rake_ft"))} />
        <MetricItem label={t("asset.rooflens.measurements.labelEaves")} value={ln(sum("eave_ft"))} />
        {parapet > 0 && <MetricItem label={t("asset.rooflens.measurements.labelParapets")} value={ln(parapet)} />}
        <MetricItem label={t("asset.rooflens.measurements.labelEstAttic")} value={`${Math.round(sum("estimated_attic_sqft")).toLocaleString()} sq ft`} />
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
        {t("asset.rooflens.measurements.combinedNote")}
      </Typography>
    </Box>
  );
};

const RoofLensMeasurements: React.FC<Props> = ({ onExportJson, onExportPdf }) => {
  const { t } = useTranslation();
  const allFacets = useRoofLensStore((s) => s.facets);
  const buildings = useRoofLensStore((s) => s.buildings);
  const activeBuildingId = useRoofLensStore((s) => s.activeBuildingId);
  const selectedId = useRoofLensStore((s) => s.selectedId);
  const selectFacet = useRoofLensStore((s) => s.selectFacet);
  const deleteFacet = useRoofLensStore((s) => s.deleteFacet);
  const updateFacetPitch = useRoofLensStore((s) => s.updateFacetPitch);
  const updateFacetArea = useRoofLensStore((s) => s.updateFacetArea);
  const updateFacetEdge = useRoofLensStore((s) => s.updateFacetEdge);
  const metricsByBuilding = useRoofLensStore((s) => s.metricsByBuilding);

  // The panel is scoped to the ACTIVE building; a combined line covers the rest.
  const facets = React.useMemo(
    () => allFacets.filter((f) => f.buildingId === activeBuildingId),
    [allFacets, activeBuildingId]
  );
  const metrics = metricsByBuilding[activeBuildingId] ?? null;
  const activeBuilding = buildings.find((b) => b.id === activeBuildingId);
  const grandTotalSqft = React.useMemo(
    () => Math.round(computeTotals(allFacets).totalRoofAreaSqft),
    [allFacets]
  );

  const totals = React.useMemo(() => computeTotals(facets), [facets]);
  // Per-facet edge breakdown is attached to each facet at Auto-Detect time, so it
  // stays with its own row when other facets are deleted or adjusted. Show the
  // columns whenever at least one facet carries a breakdown.
  const showEdges = facets.some((f) => f.edgeMetrics);
  // Parapets only exist on flat roofs — only add the column when some facet has one.
  const showParapet = facets.some((f) => (f.edgeMetrics?.parapet_ft ?? 0) > 0);

  const multiBuilding = buildings.length > 1;
  const buildingLabel = activeBuilding?.name ?? t("asset.rooflens.measurements.buildingFallback");

  if (facets.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        {multiBuilding && <CombinedBuildingsSummary />}
        <Typography variant="body2" color="text.secondary">
          {multiBuilding && <b>{buildingLabel}: </b>}
          {t("asset.rooflens.measurements.emptyRunPre")}<b>{t("asset.rooflens.autoDetectRoof")}</b>{t("asset.rooflens.measurements.emptyMid")}<b>{t("asset.rooflens.addArea")}</b>{t("asset.rooflens.measurements.emptyPost")}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      {multiBuilding && <CombinedBuildingsSummary />}
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
        {t("asset.rooflens.measurements.liveMeasurements")}{multiBuilding ? ` — ${buildingLabel}` : ""}
      </Typography>
      <Typography variant="h6" sx={{ color: "primary.main", fontWeight: 700, lineHeight: 1.2 }}>
        {Math.round(totals.totalRoofAreaSqft).toLocaleString()}{" "}
        <Typography component="span" variant="body2" color="text.secondary">
          {t("asset.rooflens.measurements.totalRoofAreaCaption")}
        </Typography>
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {t("asset.rooflens.measurements.summaryCaption", {
          facets: totals.facetCount,
          perimeter: Math.round(totals.perimeterFt).toLocaleString(),
          footprint: Math.round(totals.footprintSqft).toLocaleString(),
        })}
      </Typography>
      {multiBuilding && (
        <Typography variant="caption" sx={{ display: "block", fontWeight: 700, color: "text.primary", mt: 0.25 }}>
          {t("asset.rooflens.measurements.allBuildingsGrandTotal", { area: grandTotalSqft.toLocaleString(), n: buildings.length })}
        </Typography>
      )}

      {metrics && <MetricsSummary metrics={metrics} />}

      <Divider sx={{ mt: 1.5 }} />

      <Box sx={{ overflowX: "auto", mt: 1.5 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colFacet")}</TableCell>
              <TableCell sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colRoofArea")}</TableCell>
              <TableCell sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colPitch")}</TableCell>
              {showEdges && (
                <>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colRidge")}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colHip")}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colValley")}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colRake")}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colEave")}</TableCell>
                  {showParapet && <TableCell align="right" sx={{ fontWeight: 700 }}>{t("asset.rooflens.measurements.colParapet")}</TableCell>}
                </>
              )}
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {totals.facets.map((m) => {
              const pf = facets.find((f) => f.id === m.id)?.edgeMetrics;
              return (
                <TableRow
                  key={m.id}
                  hover
                  selected={m.id === selectedId}
                  onClick={() => selectFacet(m.id)}
                  sx={{ cursor: "pointer" }}
                >
                  <TableCell>{m.label}</TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <NumCell
                      value={m.roofAreaSqft}
                      suffix="sq ft"
                      width={64}
                      onCommit={(n) => updateFacetArea(m.id, n)}
                    />
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Stack direction="row" alignItems="center" spacing={0.5}>
                      <TextField
                        type="number"
                        size="small"
                        value={String(facets.find((f) => f.id === m.id)?.pitchRise ?? 6)}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (!Number.isNaN(v)) updateFacetPitch(m.id, Math.max(0, Math.min(24, v)));
                        }}
                        inputProps={{ min: 0, max: 24, step: 1, style: { width: 48, padding: "4px 6px" } }}
                      />
                      <Typography variant="caption" color="text.secondary">/12</Typography>
                    </Stack>
                  </TableCell>
                  {showEdges && (
                    <>
                      <TableCell align="right">
                        <NumCell value={pf?.ridge_ft ?? 0} suffix="ft" onCommit={(n) => updateFacetEdge(m.id, "ridge_ft", n)} />
                      </TableCell>
                      <TableCell align="right">
                        <NumCell value={pf?.hip_ft ?? 0} suffix="ft" onCommit={(n) => updateFacetEdge(m.id, "hip_ft", n)} />
                      </TableCell>
                      <TableCell align="right">
                        <NumCell value={pf?.valley_ft ?? 0} suffix="ft" onCommit={(n) => updateFacetEdge(m.id, "valley_ft", n)} />
                      </TableCell>
                      <TableCell align="right">
                        <NumCell value={pf?.rake_ft ?? 0} suffix="ft" onCommit={(n) => updateFacetEdge(m.id, "rake_ft", n)} />
                      </TableCell>
                      <TableCell align="right">
                        <NumCell value={pf?.eave_ft ?? 0} suffix="ft" onCommit={(n) => updateFacetEdge(m.id, "eave_ft", n)} />
                      </TableCell>
                      {showParapet && (
                        <TableCell align="right">
                          <NumCell value={pf?.parapet_ft ?? 0} suffix="ft" onCommit={(n) => updateFacetEdge(m.id, "parapet_ft", n)} />
                        </TableCell>
                      )}
                    </>
                  )}
                  <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                    <IconButton size="small" color="error" onClick={() => deleteFacet(m.id)}>
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>

      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
        {t("asset.rooflens.measurements.editableCaptionBase")}{showEdges ? t("asset.rooflens.measurements.editableCaptionEdges") : ""}{t("asset.rooflens.measurements.editableCaptionMid")}
        {showEdges && t("asset.rooflens.measurements.editableCaptionShared")}
      </Typography>

      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
        <Button size="small" variant="outlined" startIcon={<DownloadOutlinedIcon />} onClick={onExportJson} sx={{ textTransform: "none" }}>
          {t("asset.rooflens.measurements.exportJson")}
        </Button>
        <Button size="small" variant="outlined" startIcon={<PictureAsPdfOutlinedIcon />} onClick={onExportPdf} sx={{ textTransform: "none" }}>
          {t("asset.rooflens.measurements.exportPdf")}
        </Button>
      </Stack>
    </Box>
  );
};

export default RoofLensMeasurements;
