package com.fieldcraft.tenant.server.api.quickbooks;

import com.fieldcraft.tenant.server.api.ApiBase;
import com.fieldcraft.tenant.service.quickbooks.QuickBooksCredentialService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/quickbooks")
public class QuickBooksCredentialApi extends ApiBase { // use your actual base controller class

    private final QuickBooksCredentialService quickBooksCredentialService;

    public QuickBooksCredentialApi(QuickBooksCredentialService quickBooksCredentialService) {
        this.quickBooksCredentialService = quickBooksCredentialService;
    }

    @Operation(method = "GET", summary = "Returns encrypted QuickBooks credential token")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "",
                    content = {
                            @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                    schema = @Schema(implementation = Map.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/credential", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, String>> getQuickBooksCredential() {
        return buildGetResult(
                quickBooksCredentialService.getQuickBooksCredential(getUserObjectId())
        );
    }
}
