/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Loading overlay for the RoofLens map (SAM2 + 3D-mesh runs take
 *                ~60-90s); shows a spinner, label, and a live elapsed timer.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import * as React from "react";
import { Box, CircularProgress, LinearProgress, Typography } from "@mui/material";
import { useTranslation } from "react-i18next";

interface Props {
  active: boolean;
  label: string;
}

const RoofLensLoadingOverlay: React.FC<Props> = ({ active, label }) => {
  const { t } = useTranslation();
  const [elapsed, setElapsed] = React.useState(0);

  React.useEffect(() => {
    if (!active) {
      setElapsed(0);
      return;
    }
    const start = Date.now();
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [active]);

  if (!active) return null;

  return (
    <Box
      sx={{
        position: "absolute",
        inset: 0,
        zIndex: 5,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1.5,
        bgcolor: "rgba(255,255,255,0.82)",
        borderRadius: "8px",
      }}
    >
      <CircularProgress size={40} />
      <Typography variant="subtitle2" sx={{ fontWeight: 600, color: "primary.main" }}>
        {label || t("asset.rooflens.analyzing")}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {t("asset.rooflens.detecting", { elapsed })}
      </Typography>
      <Box sx={{ width: "60%", maxWidth: 260 }}>
        <LinearProgress />
      </Box>
    </Box>
  );
};

export default RoofLensLoadingOverlay;
