/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-18
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Building selector for the RoofLens editor. A property can have
 *                more than one structure (house + detached garage, etc., which
 *                EagleView reports separately). Each building holds its own
 *                facets/measurements; Auto-Detect and Add Area target the active
 *                building. "+ Add Building" creates and activates a new one.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-18 | FIELDCRAFT Web | Initial version
 */

import * as React from "react";
import { Box, Chip, Stack, Tooltip, Typography } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import HomeWorkOutlinedIcon from "@mui/icons-material/HomeWorkOutlined";
import { useTranslation } from "react-i18next";
import { computeTotals, useRoofLensStore } from "./store/rooflensStore";

const RoofLensBuildingBar: React.FC = () => {
  const { t } = useTranslation();
  const buildings = useRoofLensStore((s) => s.buildings);
  const activeBuildingId = useRoofLensStore((s) => s.activeBuildingId);
  const facets = useRoofLensStore((s) => s.facets);
  const setActiveBuilding = useRoofLensStore((s) => s.setActiveBuilding);
  const addBuilding = useRoofLensStore((s) => s.addBuilding);
  const removeBuilding = useRoofLensStore((s) => s.removeBuilding);

  const areaFor = (id: string) =>
    Math.round(computeTotals(facets.filter((f) => f.buildingId === id)).totalRoofAreaSqft);

  return (
    <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 0.75, mb: 1 }}>
      <HomeWorkOutlinedIcon fontSize="small" sx={{ color: "text.secondary" }} />
      <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
        {t("asset.rooflens.buildings")}
      </Typography>
      {buildings.map((b) => {
        const active = b.id === activeBuildingId;
        const area = areaFor(b.id);
        return (
          <Chip
            key={b.id}
            size="small"
            color={active ? "primary" : "default"}
            variant={active ? "filled" : "outlined"}
            label={area > 0 ? `${b.name} · ${area.toLocaleString()} sq ft` : b.name}
            onClick={() => setActiveBuilding(b.id)}
            onDelete={buildings.length > 1 ? () => removeBuilding(b.id) : undefined}
            sx={{ fontWeight: active ? 700 : 400 }}
          />
        );
      })}
      <Tooltip title={t("asset.rooflens.addBuildingTooltip")}>
        <Chip
          size="small"
          variant="outlined"
          color="primary"
          icon={<AddIcon />}
          label={t("asset.rooflens.addBuilding")}
          onClick={addBuilding}
          sx={{ textTransform: "none" }}
        />
      </Tooltip>
    </Box>
  );
};

export default RoofLensBuildingBar;
