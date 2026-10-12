package com.fieldcraft.tenant.service.quickbooks;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.tenant.dal.repos.*;
import com.fieldcraft.tenant.data.domain.RolePermission;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.UserAccount;
import com.fieldcraft.tenant.data.model.quickbooks.QuickBooksCredentialInfo;
import com.fieldcraft.utils.CryptUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.*;

import static com.fieldcraft.tenant.data.constant.DomainConstant.*;

@Service
public class QuickBooksCredentialService {

    private final TenantRepository tenantRepository;
    private final UserAccountRepository userAccountRepository;
    private final UserRoleMapRepository userRoleMapRepository;
    private final RolesRepository rolesRepository;
    private final RolePermissionRepository rolePermissionRepository;

    private final ObjectMapper mapper = new ObjectMapper();

    /**
     * From application.yml -> fieldcraft.quickbooks.secret-key
     */
    @Value("${fieldcraft.quickbooks.secret-key}")
    private String quickBooksSecretKey;

    /**
     * NOTE: You are hardcoding the encryption KEY in AIChatService.
     * Keeping same style here for consistency.
     * Recommended: move this to yml as well.
     */
    private static final String ENCRYPT_KEY = "2025teNaNtIdDECODE";

    public QuickBooksCredentialService(
            TenantRepository tenantRepository,
            UserAccountRepository userAccountRepository,
            UserRoleMapRepository userRoleMapRepository,
            RolesRepository rolesRepository,
            RolePermissionRepository rolePermissionRepository
    ) {
        this.tenantRepository = tenantRepository;
        this.userAccountRepository = userAccountRepository;
        this.userRoleMapRepository = userRoleMapRepository;
        this.rolesRepository = rolesRepository;
        this.rolePermissionRepository = rolePermissionRepository;
    }

    public Map<String, String> getQuickBooksCredential(String objectId) {
        // If you have a permission constant for QuickBooks, use it:
        // super.hasPermissionEx(QUICKBOOKS);
        // (If this class doesn't extend ServiceBase, enforce permission at controller/service caller)

        Map<String, String> response = new HashMap<>();

        try {
            Tenant tenant = tenantRepository.findTenant();
            if (tenant == null) {
                throw new ServerException("TENANT_NOT_FOUND", "Tenant could not be resolved from current context");
            }

            UserAccount userAccount = userAccountRepository.findByObjectId(objectId);
            if (userAccount == null) {
                throw new ServerException("USER_NOT_FOUND", "User not found for objectId: " + objectId);
            }

            List<String> roles = userRoleMapRepository.findRoleByUserId(userAccount.getId());

            List<RolePermission> rolePermissionList = new ArrayList<>();
            if (roles != null && !roles.isEmpty()) {
                for (String role : roles) {
                    Integer roleId = rolesRepository.findByRoleName(role);
                    if (roleId != null) {
                        rolePermissionList.addAll(rolePermissionRepository.getPermissionsByRoleId(roleId));
                    }
                }
            }

            List<String> permissions = rolePermissionList.stream()
                    .map(RolePermission::getPermission)
                    .distinct()
                    .toList();

            QuickBooksCredentialInfo tokenInfo = new QuickBooksCredentialInfo();
            tokenInfo.setTenantId(String.valueOf(tenant.getTenantid()));
            tokenInfo.setUserUid(objectId);
            tokenInfo.setRoles(roles == null ? List.of() : roles);
            tokenInfo.setRolePermission(permissions);

            String json = mapper.writeValueAsString(tokenInfo);

            String encryptedToken = CryptUtils.encrypt(json, ENCRYPT_KEY);

            response.put("token", encryptedToken);
            response.put("secretKey", quickBooksSecretKey);

            return response;

        } catch (ServerException se) {
            throw se;
        } catch (Exception e) {
            throw new ServerException("QUICKBOOKS_CREDENTIAL_ERROR", "Failed to generate QuickBooks credential", e);
        }
    }
}
