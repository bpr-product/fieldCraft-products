package com.fieldcraft.provider.server.api;


import com.fieldcraft.external.data.domain.ServiceSubscriber;
import com.fieldcraft.external.data.model.ServiceConfigurationDTO;
import com.fieldcraft.external.data.model.ServiceMasterDTO;
import com.fieldcraft.external.data.model.ServiceSubscriberDTO;
import com.fieldcraft.provider.services.SubscriberService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.media.ArraySchema;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/subscriber")
public class SubscriberApi extends ApiBase {

    private final SubscriberService subscriberService;

    public SubscriberApi(SubscriberService subscriberService) {
        this.subscriberService = subscriberService;
    }

    @Operation(method = "PUT", summary = "Create a External Service")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "201", description = "Service Saved",
                    content = {@Content(mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = Integer.class))}),
            @ApiResponse(responseCode = "400", description = "Invalid Service ", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)})
    @PutMapping(value = "/updateSubscriber", produces = MediaType.APPLICATION_JSON_VALUE)
    public Boolean updateSubscriber(@RequestBody @Valid final ServiceSubscriberDTO serviceSubscriberDTO) {
        final Boolean subscriber =  subscriberService.createOrUpdateSubscriberDetails(serviceSubscriberDTO);
        return subscriber;
    }

    @Operation(method = "GET", summary = "Returns a list of External Service  for the specified search conditions")
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "External Service found",
                    content = {@Content(mediaType = MediaType.APPLICATION_JSON_VALUE, array = @ArraySchema(schema = @Schema(implementation = List.class)))}),
            @ApiResponse(responseCode = "400", description = "Invalid param supplied", content = @Content),
            @ApiResponse(responseCode = "401", description = "Unauthorized Access", content = @Content),
            @ApiResponse(responseCode = "403", description = "Access Forbidden", content = @Content),
            @ApiResponse(responseCode = "500", description = "Internal Server Error", content = @Content)
    })
    @GetMapping(value = "/getSubscriber/{subscriberId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<List<ServiceSubscriberDTO>> getSubscriberById(@PathVariable(name = "subscriberId") String subscriberId) {
        return buildGetResult(subscriberService.getSubscriberById(subscriberId));
    }
}
