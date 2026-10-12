export interface OidcConfiguration {
    client_id: string;
    redirect_uri: string;
    silent_redirect_uri: string;
    scope: string;
    authority: string;
}

const oidcConfiguration: OidcConfiguration = {
    client_id: process.env.REACT_APP_OIDC_CLIENT_ID || "",
  redirect_uri: `${window.location.origin}/callback`,
  silent_redirect_uri: `${window.location.origin}/silent-callback`,
    scope: process.env.REACT_APP_OIDC_SCOPE || "openid offline_access profile",
    authority: process.env.REACT_APP_OIDC_AUTHORITY || ""
};


export const ConfigUrl = {
    apiUrl: process.env.REACT_APP_API_URL || "http://localhost:8080/fieldcraft-provider/api",
    tenantapi_graphql_baseurl: process.env.REACT_APP_GRAPHQL_URL || "http://localhost:8080/fieldcraft-provider/graphql",
    //apiUrl:"http://localhost:8080/fieldcraft-provider/api",
    //tenantapi_graphql_baseurl: "http://localhost:8080/fieldcraft-provider/graphql",
    // ai_chat_URL: "http://localhost:5004",
    ai_chat_URL: process.env.REACT_APP_AI_CHAT_URL || "http://localhost:8020",
    extservice_base_url: process.env.REACT_APP_EXTERNAL_SERVICE_URL || "http://localhost:5001",
    bpe_url: process.env.REACT_APP_BPE_BASE_URL || "http://localhost:15001",
    recommendation_url: process.env.REACT_APP_RECOMMENDATION_URL || "http://localhost:15001/api/recommendation",
    vllm_url: process.env.REACT_APP_VLLM_URL || "http://localhost:15001/api/vllm/v1/chat/completions",
    next_gen_ase_url: process.env.REACT_APP_ASE_URL || "http://localhost:8021/ase/api",
    // bpe_url: process.env.REACT_APP_BPE_BASE_URL || "http://127.0.0.1",
    // recommendation_url: process.env.REACT_APP_RECOMMENDATION_URL || "http://127.0.0.1/api/recommendation",
    // vllm_url: process.env.REACT_APP_VLLM_URL || "http://127.0.0.1/api/vllm/v1/chat/completions",

    // ── local direct ports (no nginx; CORS/auth caveats) ──
    // bpe_url: process.env.REACT_APP_BPE_BASE_URL || "http://localhost:15001",
    // recommendation_url: process.env.REACT_APP_RECOMMENDATION_URL || "http://localhost:18085",
    chatApiUrl: process.env.REACT_APP_CHAT_API_URL || "http://localhost:8086/chat",
    chatWsUrl: process.env.REACT_APP_CHAT_WS_URL || "ws://localhost:8086/chat/ws",
    // chatApiUrl: "http://localhost:8086/chat",
    // chatWsUrl:  "ws://localhost:8086/chat/ws",
    rooflens_url: process.env.REACT_APP_ROOFLENS_URL || "http://localhost:15001/api/rooflens"
};




export default oidcConfiguration;


 