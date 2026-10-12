/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 20-Nov-2023
 * @author      : Mala.K/Pavithra.P
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Rest Api for Material
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE     |    AUTHOR   |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *     726        | 29-Nov-2023 |  Mala.K     | Search Implementation
 *    2399        | 06-Aug-2024 |Shiyam kumar | Change search material api                          
 *
 */
package com.fieldcraft.tenant.server.api;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fieldcraft.shared.search.PagingInfo;
import com.fieldcraft.tenant.data.model.*;
import com.fieldcraft.tenant.data.param.MaterialSearchFilter;
import com.fieldcraft.tenant.service.MaterialService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.ArraySchema;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v2/materials")
public class MaterialApi extends ApiBase {

    @Autowired
    MaterialService materialService;

    /**
     * @param page
     * @param size
     * @param sortDir
     * @return
     */
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
    public ResponseEntity<List<MaterialDTO>> getAllMaterials(
            @Parameter(description = "Page number (0-based)", example = "0")
            @RequestParam(defaultValue = "0") int page,
            @Parameter(description = "Number of items per page", example = "10")
            @RequestParam(defaultValue = "10") int size,
            @Parameter(description = "Sorting direction (ASC or DESC)", example = "ASC")
            @RequestParam(defaultValue = "ASC") String sortDir) {
        return buildListResult(materialService.findAll());
    }

    /**
     * @param id
     * @return
     */
    @Operation(method = "GET", summary = "Returns a list of Materials for the specified id")
    @ApiResponses(value = {
        @ApiResponse(responseCode = "200", description = "Material found",
                content = {
                    @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                            schema = @Schema(implementation = MaterialDTO.class))}),
        @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
        @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
        @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
        @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<MaterialDTO> getMaterial(@PathVariable(name = "id") final Long id) {
        return buildGetResult(materialService.get(id));
    }

    /**
     * @param id
     * @return
     */
    @Operation(method = "GET", summary = "Returns a list of Materials tags")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Material found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = MaterialDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/tag", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<String>> getMaterialTags() {
//        List<String> tags = materialService.getMaterialTags();
//        return ResponseEntity.ok(tags);
        return buildGetResult(materialService.getMaterialTags());
    }
    /**
     * @param materialDTO
     * @return
     */
    @Operation(method = "POST", summary = "Create a Material")
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
    @PostMapping(produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_CREATE + "')")
    public ResponseEntity<Long> createMaterial(@RequestBody @Valid final MaterialDTO materialDTO) {
        final Long createdId = materialService.create(materialDTO);
        return buildCreatedResult(createdId);
    }

    /**
     * @param id
     * @param materialDTO
     * @return
     */
    @Operation(method = "PUT", summary = "Update Material")
    @ApiResponses(value = {
        @ApiResponse(responseCode = "201", description = "Material Updates",
                content = {
                    @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                            schema = @Schema(implementation = Integer.class))}),
        @ApiResponse(responseCode = "400", description = "Invalid param supplied",
                content = @Content),
        @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
        @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
        @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    //     @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_CREATE + "')")
    @PutMapping(value = "/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<MaterialDTO> updateMaterial(@PathVariable(name = "id") final Long id,
            @RequestBody @Valid final MaterialDTO materialDTO) throws JsonProcessingException {
        materialService.update(id, materialDTO);
        return buildUpdatedResult(materialDTO);
    }

    /**
     * @param id
     * @return
     */
    @Operation(method = "DELETE", summary = "Delete Material")
    @ApiResponses(value = {
        @ApiResponse(responseCode = "204", description = "Material Deleted",
                content = {
                    @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                            schema = @Schema(implementation = Integer.class))}),
        @ApiResponse(responseCode = "400", description = "Invalid Material supplied",
                content = @Content),
        @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
        @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
        @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteMaterial(@PathVariable(name = "id") final Long id) {
        materialService.delete(id);
        return buildDeletedResult();
    }

 
    /**
     * @param filters
     * @param pagingInfo
     * @return
     */
    @Operation(method = "GET", summary = "Returns a list of Materials for the specified search")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Material found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})

    @GetMapping(value = "/search/materialName/{materialName}/sortField/{sortField}/sortOrder/{sortOrder}/trade/{trade}/grade/{grade}", produces = MediaType.APPLICATION_JSON_VALUE)
//   @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//           + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<MaterialInfo>> searchMaterial(@PathVariable(name = "materialName", required = false) final String materialName,@PathVariable(name = "sortField", required = false) final String sortField,@PathVariable(name = "sortOrder", required = false) final String sortOrder, @PathVariable(name = "trade", required = false) final String trade,
                                                             @PathVariable(name = "grade", required = false) final String grade,
                                                             @RequestParam(name = "inStockOnly", required = false, defaultValue = "false") final boolean inStockOnly,
                                                             @RequestParam(name = "vendorId", required = false) final Long vendorId,
                                                             PagingInfo pagingInfo) {
        return buildListResult(materialService.findBySearchCriteria(materialName,sortField,sortOrder, trade, grade, PageRequest.of(pagingInfo.getPage(),
                pagingInfo.getSize()), inStockOnly, vendorId));
    }

    /**
     * @param vendorId
     * @param materialName
     * @param pagingInfo
     * @return
     */
    @Operation(method = "GET", summary = "Returns a vendor's mapped materials, no in-stock requirement")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Material found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @GetMapping(value = "/search/vendor/{vendorId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<List<MaterialInfo>> searchMaterialByVendor(@PathVariable(name = "vendorId") final Long vendorId,
                                                                      @RequestParam(name = "materialName", required = false) final String materialName,
                                                                      PagingInfo pagingInfo) {
        return buildListResult(materialService.findByVendorId(vendorId, materialName, PageRequest.of(pagingInfo.getPage(),
                pagingInfo.getSize())));
    }



    @Operation(method = "GET", summary = "Returns a list of Customers for the specified count")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Material found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = ProjectDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/count/{materialName}/trade/{trade}/grade/{grade}", produces = MediaType.APPLICATION_JSON_VALUE)
    public Integer getTotalMaterialCount(@PathVariable(name = "materialName", required = false) final String materialName, @PathVariable(name = "trade", required = false) final String trade,
                                         @PathVariable(name = "grade", required = false) final String grade) {
        return materialService.getTotalMaterialCount(materialName, trade, grade);
    }

    /**
     * @param id
     * @param materialDTO
     * @return
     */
    @Operation(method = "PUT", summary = "Update Material Cost")
    @ApiResponses(value = {@ApiResponse(responseCode = "201", description = "Resource Updates", content = {@Content(mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = Integer.class))}), @ApiResponse(responseCode = "400", description = "Invalid Resource supplied", content = @Content), @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content), @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content), @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})

//     @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_CREATE + "')")
    @PutMapping(value = "/updateMaterialCost", produces = MediaType.APPLICATION_JSON_VALUE)
    public String updateResourceCost(@RequestBody @Valid final List<MaterialDTO> materialDTO) {
        return materialService.updateMaterialCost(materialDTO);
    }
    /**
     * @param id
     * @return
     */
    @Operation(method = "GET", summary = "Returns a list of Materials for the specified id")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Material found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = MaterialDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/getMaterials", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<MaterialDTO>> getMaterial() {
        return buildListResult(materialService.getMaterials());
    }
    @Operation(method = "GET", summary = "Returns a list of Material Names")
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

    @GetMapping(value ="/getMaterialNames", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<String>> getMaterialNames(){
        return buildListResult(materialService.getMaterialNames());
    }
    @Operation(method = "POST", summary = "Create a Material")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Material Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Tool supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @PostMapping(value = "/addMaterials",produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_CREATE + "')")
    public ResponseEntity<String> create(@RequestBody  final List<MaterialDTO> materialDTO) {
        return buildCreatedResult(materialService.createMaterial(materialDTO));
    }

    @Operation(method = "GET", summary = "Get Material Name")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Get Material Name",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = UserAccountDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/getMaterialName/tradeName/{tradeName}", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.TUSER_MANAGE + "', "
//            + "'" + TenantPermissions.TUSER_READ + "')")
    public ResponseEntity<List<MaterialDTO>> getResourceType(@PathVariable(name = "tradeName") final String tradeName) {
        return buildGetResult(materialService.getMaterials(tradeName));
    }

    @Operation(method = "Post", summary = "Get Material Name")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Get Material Name",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = MaterialDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @PostMapping(value = "/getVendorMaterials", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.TUSER_MANAGE + "', "
//            + "'" + TenantPermissions.TUSER_READ + "')")
    public ResponseEntity<MaterialInfo> getMaterials(@RequestBody @Valid final MaterialDTO materialDTO) {
        return buildGetResult(materialService.getmaterialsByNameAndGrade(materialDTO));
    }

    @Operation(method = "Post", summary = "Get Material Grades")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Get Material Grades",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = MaterialDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @PostMapping(value = "/getMaterialGrade", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.TUSER_MANAGE + "', "
//            + "'" + TenantPermissions.TUSER_READ + "')")
    public ResponseEntity<List<String>> getGrades(@RequestBody final MaterialDTO materialDTO) {
        return buildGetResult(materialService.getGradesByMaterialName(materialDTO.getName(), materialDTO.getVendorId()));
    }
}



