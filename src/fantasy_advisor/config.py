"""URLs and settings for the read-only connectors."""

COMPETITION_ID = "1"
LANGUAGE = "es"

LALIGA_API_BASE = "https://fantasy-api.llt-services.com/api"
LALIGA_OAUTH_BASE = (
    "https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0"
)
LALIGA_AUTHORIZE_URL = f"{LALIGA_OAUTH_BASE}/authorize"
LALIGA_TOKEN_URL = f"{LALIGA_OAUTH_BASE}/token"
LALIGA_SIGNIN_POLICY = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
LALIGA_CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"
LALIGA_REDIRECT_URI = "authredirect://com.lfp.laligafantasy"
LALIGA_SCOPE = "openid offline_access"

FF_MARKET_URL = "https://www.futbolfantasy.com/analytics/laliga-fantasy/mercado"
FF_LINEUPS_INDEX_URL = "https://www.futbolfantasy.com/laliga/posibles-alineaciones"
FF_TEAM_URL = "https://www.futbolfantasy.com/laliga/equipos/{slug}"
FF_BASE_URL = "https://www.futbolfantasy.com"
FF_CACHE_SECONDS = 30 * 60
FF_REQUEST_GAP_SECONDS = 1.2
FF_MAX_RESPONSE_BYTES = 6_000_000

KEYRING_SERVICE = "laliga-fantasy-advisor"
