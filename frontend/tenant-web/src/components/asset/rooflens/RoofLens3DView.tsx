/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Purpose      : The 3D view now lives in components/common/ (shared with the address pickers);
 *                re-exported here so RoofLens and Create Asset imports and mocks are unchanged.
 * -----------------------------------------------------------------------------
 * AB#32816 | 2026-10-05 | Steeve | Re-export of the moved component
 */
export { default } from "../../common/RoofLens3DView";
