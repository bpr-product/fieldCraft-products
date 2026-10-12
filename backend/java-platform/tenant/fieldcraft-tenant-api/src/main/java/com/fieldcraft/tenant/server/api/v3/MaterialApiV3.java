package com.fieldcraft.tenant.server.api.v3;

import com.fieldcraft.tenant.dal.material.MaterialFilterInput;
import com.fieldcraft.tenant.data.model.MaterialInfo;
import com.fieldcraft.tenant.data.rbac.TenantPermissions;
import com.fieldcraft.tenant.data.resource.MaterialDetail;
import com.fieldcraft.tenant.feature.material.MaterialPriceService;
import com.fieldcraft.tenant.server.api.ApiBase;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v3/materials")
public class MaterialApiV3 extends ApiBase {

    private final MaterialPriceService materialPriceService;

    public MaterialApiV3(MaterialPriceService materialPriceService){
        this.materialPriceService = materialPriceService;
    }

    /**
     * @param materialName
     * @return
     */
    @Operation(method = "GET", summary = "Returns a list of Materials for the specified id")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Material found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = MaterialDetail.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/name/{materialName}", produces = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize(TenantPermissions.MATERIAL_READ_AUTHORITIES)
    public ResponseEntity<List<MaterialDetail>> getMaterial(@PathVariable(name = "materialName") final String materialName) {
        var filter = new MaterialFilterInput();
        filter.setName(materialName);
        return buildGetResult(materialPriceService.searchMaterials(filter));
    }

    /**
     * @param data
     * @return
     */
    @Operation(method = "POST", summary = "Create a Material")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Material Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Long.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Material supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @PostMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize(TenantPermissions.MATERIAL_WRITE_AUTHORITIES)
    public ResponseEntity<Long> create(@RequestBody String data) {
        return buildGetResult(1l);
    }
}
