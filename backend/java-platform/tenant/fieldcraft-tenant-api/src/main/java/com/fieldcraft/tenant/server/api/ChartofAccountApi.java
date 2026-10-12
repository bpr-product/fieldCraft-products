package com.fieldcraft.tenant.server.api;

import com.fieldcraft.tenant.data.domain.accounts.ChartOfAccount;
import com.fieldcraft.tenant.data.model.CreateChartOfAccountRequest;
import com.fieldcraft.tenant.data.rbac.TenantPermissions;
import com.fieldcraft.tenant.service.ChartofAccountService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.ArraySchema;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;


import java.util.List;

@RestController
@RequestMapping("/api/v2/chartofaccount")
public class ChartofAccountApi extends ApiBase {

    @Autowired
    ChartofAccountService chartofAccountService;

    /**
     * @param page
     * @param size
     * @param sortDir
     * @return
     */
    @Operation(method = "GET", summary = "Returns a list of Chartofaccount for the specified search conditions")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Materials found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    array = @ArraySchema(schema = @Schema(implementation = ChartOfAccount.class)))
                    }),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<ChartOfAccount>> getAllChartofAccounts(
            @Parameter(description = "Page number (0-based)", example = "0")
            @RequestParam(defaultValue = "0") int page,
            @Parameter(description = "Number of items per page", example = "10")
            @RequestParam(defaultValue = "10") int size,
            @Parameter(description = "Sorting direction (ASC or DESC)", example = "ASC")
            @RequestParam(defaultValue = "ASC") String sortDir) {
        return buildListResult(chartofAccountService.findAll());
    }

    @Operation(method = "GET", summary = "Returns a list of Account Types ")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Trade found",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = ChartOfAccount.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/getallaccounttypes", produces = MediaType.APPLICATION_JSON_VALUE)
    //    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<String>> getAllAccountTypes() {
        return buildGetResult(chartofAccountService.getAllAccountTypes());
    }

    @GetMapping(value = "/active", produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAnyAuthority('" + TenantPermissions.USER_MANAGE + "', "
//            + "'" + TenantPermissions.USER_READ + "')")
    public ResponseEntity<List<ChartOfAccount>> getActiveChartOfAccounts() {
        return buildListResult(chartofAccountService.findAllActive());
    }

    @PostMapping(produces = MediaType.APPLICATION_JSON_VALUE)
//    @PreAuthorize("hasAuthority('" + TenantPermissions.FINANCE_MANAGE + "')")
    public ResponseEntity<Long> createChartOfAccount(
            @RequestBody CreateChartOfAccountRequest request) {
        return buildGetResult(chartofAccountService.create(request));
    }

}
