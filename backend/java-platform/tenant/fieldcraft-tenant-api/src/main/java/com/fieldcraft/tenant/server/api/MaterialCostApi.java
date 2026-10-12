/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 30-Nov-2023
 * @author      : Ananth Kumar L
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Api Service for MaterialCost
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *   2858         | 04-11-2024 | shiyam kumar  | API created For get material minimum cost
 *
 *
 */

package com.fieldcraft.tenant.server.api;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fieldcraft.tenant.data.model.MaterialCostDTO;
import com.fieldcraft.tenant.data.model.MaterialDTO;
import com.fieldcraft.tenant.data.model.MaterialInfo;
import com.fieldcraft.tenant.service.MaterialCostService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.media.ArraySchema;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;

import java.time.OffsetDateTime;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v2/materialcost")
public class MaterialCostApi extends ApiBase{
    @Autowired
    MaterialCostService materialCostService;


    @Operation(method = "GET", summary = "Returns a list of Materials for the specified search conditions")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Materials found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    array = @ArraySchema(schema = @Schema(implementation = MaterialDTO.class)))
                    }),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    //    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
    //            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<MaterialCostDTO>> getAllMaterialCosts()
    {
        return buildListResult(materialCostService.findAll());
    }

    @Operation(method = "POST", summary = "Update Material COst")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Material Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Material supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @PostMapping(value = "/updateMaterialCost", produces = MediaType.APPLICATION_JSON_VALUE)
    //    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
    //            + "'" + TenantPermissions.USER_CREATE + "')")
    public ResponseEntity<String> updateMaterialCost(@RequestBody final MaterialCostDTO materialCostDTO) throws JsonProcessingException {
        final String createdId = materialCostService.insertMaterialCost(materialCostDTO);
        return buildCreatedResult(createdId);
    }
    @Operation(method = "GET", summary = "GET Material Cost")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Material Cost Found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Material Id supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @GetMapping(value = "/getMaterialCostByDateAndMapId/selectedDate/{selectedDate}/vendorMaterialMapId/{vendorMaterialMapId}", produces = MediaType.APPLICATION_JSON_VALUE)
    //    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
    //            + "'" + TenantPermissions.USER_CREATE + "')")
    public MaterialInfo updateToolCost(@PathVariable(name = "selectedDate") final OffsetDateTime selectedDate,
                                       @PathVariable(name = "vendorMaterialMapId") final Long vendorMaterialMapId) {
        return materialCostService.getMaterialsBySelectedDateAndMapId(selectedDate, vendorMaterialMapId);
    }

    @Operation(method = "GET", summary = "GET Material Cost")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Material Cost Found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Material Id supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @GetMapping(value = "/getMinimumMaterialCost/materials/{materials}/pinCode/{pinCode}/constructionDate/{constructionDate}", produces = MediaType.APPLICATION_JSON_VALUE)
    //    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
    //            + "'" + TenantPermissions.USER_CREATE + "')")
    public Object getMinimumCostMaterial(@PathVariable(name = "materials") final String[] materials, @PathVariable(name = "pinCode") final String pinCode,
                                         @PathVariable(name = "constructionDate") final String constructionDate) throws Exception {
        return materialCostService.getMinimumMaterialCost(materials, pinCode, constructionDate);
    }

}
