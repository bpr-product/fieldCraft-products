/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : RoofLens measure toolbar — SAM2 + 3D Facets, Add Area (manual
 *                draw), Adjust (edit corners), Delete facet, Clear all.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import * as React from "react";
import { Box, Button, Divider, IconButton, ToggleButton, ToggleButtonGroup, Tooltip } from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import AddLocationAltOutlinedIcon from "@mui/icons-material/AddLocationAltOutlined";
import OpenWithIcon from "@mui/icons-material/OpenWith";
import PushPinOutlinedIcon from "@mui/icons-material/PushPinOutlined";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import LayersClearOutlinedIcon from "@mui/icons-material/LayersClearOutlined";
import UndoIcon from "@mui/icons-material/Undo";
import RedoIcon from "@mui/icons-material/Redo";
import { useTranslation } from "react-i18next";
import { useRoofLensStore } from "./store/rooflensStore";

interface Props {
  onSam2: () => void;
  busy: boolean;
}

const RoofLensToolbar: React.FC<Props> = ({ onSam2, busy }) => {
  const { t } = useTranslation();
  const tool = useRoofLensStore((s) => s.tool);
  const setTool = useRoofLensStore((s) => s.setTool);
  const facets = useRoofLensStore((s) => s.facets);
  const selectedId = useRoofLensStore((s) => s.selectedId);
  const deleteFacet = useRoofLensStore((s) => s.deleteFacet);
  const clearAll = useRoofLensStore((s) => s.clearAll);
  const canUndo = useRoofLensStore((s) => s.canUndo);
  const canRedo = useRoofLensStore((s) => s.canRedo);
  const undo = useRoofLensStore((s) => s.undo);
  const redo = useRoofLensStore((s) => s.redo);

  const handleMode = (_e: React.MouseEvent<HTMLElement>, next: "draw" | "edit" | "pin" | null) => {
    setTool(next ?? "view");
  };

  // Ctrl+Z / Ctrl+Y (or Cmd on macOS) — mirrors projectScheduling/hooks/useKeyboardShortcuts.ts's
  // exact convention: an input/textarea/select in focus is left alone (native undo in a text
  // field must never be hijacked), and this listener lives only as long as the toolbar itself is
  // mounted — i.e. only while the 2D edit view is actually visible (never in 3D view or the
  // empty "search an address first" placeholder, since both skip mounting this component).
  React.useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "z" || e.key === "Z") {
        e.preventDefault();
        undo();
      } else if (e.key === "y" || e.key === "Y") {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undo, redo]);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        flexWrap: "wrap",
        p: 1,
        borderBottom: "1px solid #e2e8f0",
        bgcolor: "#fafbff",
      }}
    >
      <Tooltip title={t("asset.rooflens.undoTooltip")}>
        <span>
          <IconButton size="small" disabled={!canUndo} onClick={() => undo()}>
            <UndoIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title={t("asset.rooflens.redoTooltip")}>
        <span>
          <IconButton size="small" disabled={!canRedo} onClick={() => redo()}>
            <RedoIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>

      <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />

      <Button
        variant="contained"
        size="small"
        startIcon={<AutoAwesomeIcon />}
        onClick={onSam2}
        disabled={busy}
        sx={{ textTransform: "none", fontWeight: 600 }}
      >
        {t("asset.rooflens.autoDetectRoof")}
      </Button>

      <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />

      <ToggleButtonGroup
        size="small"
        exclusive
        value={tool === "view" ? null : tool}
        onChange={handleMode}
      >
        <ToggleButton value="draw" sx={{ textTransform: "none", gap: 0.5 }}>
          <AddLocationAltOutlinedIcon fontSize="small" /> {t("asset.rooflens.addArea")}
        </ToggleButton>
        <ToggleButton value="edit" sx={{ textTransform: "none", gap: 0.5 }}>
          <OpenWithIcon fontSize="small" /> {t("asset.rooflens.adjust")}
        </ToggleButton>
        <ToggleButton value="pin" sx={{ textTransform: "none", gap: 0.5 }}>
          <PushPinOutlinedIcon fontSize="small" /> {t("asset.rooflens.movePin")}
        </ToggleButton>
      </ToggleButtonGroup>

      <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />

      <Tooltip title={t("asset.rooflens.deleteFacetTooltip")}>
        <span>
          <IconButton size="small" color="error" disabled={!selectedId} onClick={() => selectedId && deleteFacet(selectedId)}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title={t("asset.rooflens.clearAllFacetsTooltip")}>
        <span>
          <IconButton size="small" disabled={facets.length === 0} onClick={() => clearAll()}>
            <LayersClearOutlinedIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
    </Box>
  );
};

export default RoofLensToolbar;
