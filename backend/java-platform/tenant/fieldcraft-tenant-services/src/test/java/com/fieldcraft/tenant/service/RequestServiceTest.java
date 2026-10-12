package com.fieldcraft.tenant.service;

import com.fieldcraft.customer.data.domain.Asset;
import com.fieldcraft.customer.data.domain.AssetDocument;
import com.fieldcraft.customer.data.domain.AssetServiceRequest;
import com.fieldcraft.external.dal.repos.ServiceRequestRepository;
import com.fieldcraft.external.data.domain.ServiceRequest;
import com.fieldcraft.external.data.domain.ServiceSubscriber;
import com.fieldcraft.external.data.model.ServiceRequestDTO;
import com.fieldcraft.tenant.saas.ExternalServices;
import com.fieldcraft.tenantcustomer.dal.repos.AssetDocumentRepository;
import com.fieldcraft.tenantcustomer.dal.repos.AssetServiceRequestRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.*;

import java.util.*;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class RequestServiceTest {

    @Mock private ServiceRequestRepository serviceRequestRepository;
    @Mock private AssetDocumentRepository assetDocumentRepository;
    @Mock private ExternalServices externalServices;
    @Mock private AssetServiceRequestRepository assetServiceRequestRepository;

    @InjectMocks private RequestService requestService;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
    }

    @Test
    void trackRequestStatus_throws_whenAssetIdsNull() {
        Exception ex = assertThrows(Exception.class, () -> requestService.trackRequestStatus(null));
        assertTrue(ex.getCause() instanceof IllegalArgumentException
                || ex instanceof IllegalArgumentException);
    }

    @Test
    void trackRequestStatus_throws_whenAssetIdsEmpty() {
        Exception ex = assertThrows(Exception.class, () -> requestService.trackRequestStatus(Collections.emptyList()));
        assertTrue(ex.getCause() instanceof IllegalArgumentException
                || ex instanceof IllegalArgumentException);
    }

    @Test
    void trackRequestStatus_returnsEmptyList_whenNoMappings() throws Exception {
        when(assetServiceRequestRepository.findByAssetIds(anyList()))
                .thenReturn(Collections.emptyList());

        List<ServiceRequestDTO> result = requestService.trackRequestStatus(List.of(1L, 2L));
        assertNotNull(result);
        assertTrue(result.isEmpty());
    }

    @Test
    void trackRequestStatus_returnsEmpty_whenNoOrders() throws Exception {
        AssetServiceRequest mapping = new AssetServiceRequest();
        mapping.setRequestUid(UUID.randomUUID());

        when(assetServiceRequestRepository.findByAssetIds(anyList()))
                .thenReturn(List.of(mapping));

        when(serviceRequestRepository.getRequest(anyList()))
                .thenReturn(Collections.emptyList());

        List<ServiceRequestDTO> result = requestService.trackRequestStatus(List.of(1L));
        assertNotNull(result);
        assertTrue(result.isEmpty());
    }

    @Test
    void trackRequestStatus_returnsDTOs_whenOrdersExist() throws Exception {
        UUID requestUid = UUID.randomUUID();

        // mapping must have asset + requestBy to avoid NPE in your service
        Asset asset = new Asset();
        asset.setId(10145L);

        AssetServiceRequest mapping = new AssetServiceRequest();
        mapping.setRequestUid(requestUid);
        mapping.setAsset(asset);
        mapping.setRequestBy("user-1");

        ServiceSubscriber subscriber = new ServiceSubscriber();
        subscriber.setSubscriberId("tenant1");

        ServiceRequest order = new ServiceRequest();
        order.setId(31L);
        order.setRequestUid(requestUid);
        order.setRequest("{\"assetId\":10145}");
        order.setStatusHistory("[{\"status_id\":\"5\"}]");
        order.setResponse("{\"ReportIds\":[69417822]}");
        order.setServiceSubscriber(subscriber);

        when(assetServiceRequestRepository.findByAssetIds(anyList()))
                .thenReturn(List.of(mapping));

        // IMPORTANT: match List<UUID> precisely (safer than anyList())
        when(serviceRequestRepository.getRequest(argThat(list ->
                list != null && !list.isEmpty() && list.get(0) instanceof UUID
        ))).thenReturn(List.of(order));

        when(assetServiceRequestRepository.findByRequestUid(eq(requestUid)))
                .thenReturn(Optional.of(mapping));

        when(assetDocumentRepository.findByAssetIdAndRequestId(anyString(), anyLong()))
                .thenReturn(List.of(new AssetDocument()));

        // external call should not throw — getReport returns Object, so use doReturn (not doNothing)
        doReturn(null).when(externalServices).getReport(any(), any());

        List<ServiceRequestDTO> result = requestService.trackRequestStatus(List.of(10145L));

        assertNotNull(result);
        assertEquals(1, result.size());

        // If your DTO id maps to ServiceRequest.id
        assertEquals(31L, result.get(0).getId());
    }
}