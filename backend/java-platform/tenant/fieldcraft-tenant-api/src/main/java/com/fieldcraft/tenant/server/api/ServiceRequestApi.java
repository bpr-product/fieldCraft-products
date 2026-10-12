
/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 15-MAY-2025
 * @author      : Kanimozhi
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Rest Api for ServiceRequestApi
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *
 *
 *
 */


package com.fieldcraft.tenant.server.api;

import com.fieldcraft.customer.data.domain.AssetDocument;
import com.fieldcraft.customer.data.model.AssetDocumentDTO;
import com.fieldcraft.external.data.model.ServiceRequestDTO;
import com.fieldcraft.tenant.service.RequestService;
import com.fieldcraft.tenantsubscription.service.TenantSubscriptionService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Arrays;
import java.util.List;
import java.util.UUID;

import static com.fieldcraft.tenant.data.constant.DomainConstant.EAGLEVIEW;

@RestController
@RequestMapping("/api/v1/request")
public class ServiceRequestApi extends ApiBase {


    private final RequestService requestService;

    public ServiceRequestApi(RequestService requestService) {
        this.requestService = requestService;
    }

    @Operation(method = "GET", summary = "Get a Report")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Bid Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Bid supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @GetMapping("/getReport/{assetId}")
    public List<AssetDocument> getReport(@PathVariable Long assetId) {
        return requestService.getReport(assetId);
    }

    @Operation(method = "GET", summary = "Returns a list of Asset Documents for the specified id")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Asset Document found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = AssetDocumentDTO.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/assetId/{assetId}", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.ASSETDOC_MANAGE + "', "
//            + "'" + TenantPermissions.ASSETDOC_READ + "')")
    public ResponseEntity<String> getAssetDocumentByAssetId(@PathVariable(name = "assetId") final Long assetId) throws Exception {
        return buildGetResult(requestService.getAssetDocument(assetId));
    }


    @Operation(method = "POST", summary = "Place Order Request")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Bid Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Bid supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @PostMapping(value = "/placeOrderRequest", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Boolean> placeOrderRequest(@RequestBody final ServiceRequestDTO serviceRequestDTO) throws Exception {
        return buildGetResult(requestService.placeOrderRequest(serviceRequestDTO));
    }

    @Operation(method = "POST", summary = "Get a Status Track")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Bid Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Bid supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})

    @PostMapping(
            value = "/statusTrack",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public List<ServiceRequestDTO> trackRequestStatus(@RequestBody List<Long> assetIds) throws Exception {
        return requestService.trackRequestStatus(assetIds);
    }

    @Operation(method = "GET", summary = "Get a ServiceRequest")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Bid Saved",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Bid supplied",
                    content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @PostMapping(
            value = "/getServiceRequest",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public List<ServiceRequestDTO> getServiceRequest(@RequestBody List<Long> assetIds) throws Exception {
        return requestService.getServiceRequest(assetIds);
    }
}
