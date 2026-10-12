/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 2026-10-05
* @author      : Steeve
* @since       : 1.5.4.0
* Purpose      : The RoofLens path must keep resolving to the shared 3D view (it moved to components/common/).
* -----------------------------------------------------------------------------
* AB#32816 | 2026-10-05 | Steeve | Initial creation
*/
import RoofLensPathView from "./RoofLens3DView";
import SharedView from "../../common/RoofLens3DView";

describe("asset/rooflens/RoofLens3DView re-export", () => {
  it("is the very same component as the shared one, so RoofLens and Create Asset keep working unchanged", () => {
    expect(RoofLensPathView).toBe(SharedView);
    expect(typeof RoofLensPathView).toBe("function");
  });
});
