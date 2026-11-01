from typing import Literal
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from .passwords import valid_password_hash


class Settings(BaseSettings):
    # Loading credentials must not depend on the shell/IDE working directory.
    # Explicit environment variables and Settings(_env_file=...) still override.
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / ".env", extra="ignore", case_sensitive=False, hide_input_in_errors=True)
    environment: Literal["development","test","production"] = "development"
    operation_mode: Literal["live", "simulation"] = "live"
    database_path: str = "data/recovery.sqlite3"
    operator_token: str = Field(default="", repr=False)
    operator_username: str = Field(default="", max_length=128)
    operator_password_hash: SecretStr = Field(default=SecretStr(""), repr=False)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    public_base_url: str = "http://localhost:5173"
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    hold_ttl_seconds: int = Field(default=600, ge=10, le=3600)
    session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-6.1-sol"
    openai_decision_model: str = "gpt-6-luna"
    openai_voice_model: str = "gpt-live-1"
    openai_service_tier: str = "ultrafast"
    fr24_api_key: str = ""
    fr24_base_url: str = "https://fr24api.flightradar24.com/api"
    fr24_bounds: str = "46.0,43.8,23.0,26.5"
    fr24_result_limit: int = Field(default=5,ge=1,le=20)
    fr24_max_requests: int = Field(default=80,ge=0)
    fr24_max_credits: int = Field(default=4000,ge=0)
    fr24_environment: Literal["live","sandbox"] = "live"
    observation_retention_seconds: int = Field(default=3600,ge=60,le=86400)
    voice_max_seconds: int = Field(default=180,ge=30,le=600)
    google_maps_api_key: str = ""
    google_maps_server_api_key: str = Field(default="", repr=False)
    booking_demand_api_token: str = Field(default="", repr=False)
    booking_affiliate_id: str = ""
    booking_booker_country: str = Field(default="ro", pattern=r"^[a-z]{2}$")
    liteapi_api_key: str = Field(default="", repr=False)
    liteapi_environment: Literal["sandbox", "live"] = "sandbox"
    liteapi_guest_nationality: str = Field(default="RO", pattern=r"^[A-Z]{2}$")
    hotel_offers_provider: Literal["auto", "booking", "liteapi"] = "auto"
    real_data_overpass_url: str = "https://overpass-api.de/api/interpreter"
    real_data_osrm_url: str = "https://router.project-osrm.org"
    flight_poll_seconds: int = Field(default=30, ge=15)
    request_timeout_seconds: int = Field(default=45, ge=1, le=180)
    max_request_bytes: int = 1_000_000
    duffel_access_token: str = Field(default="", repr=False)
    duffel_environment: Literal["sandbox", "live"] = "sandbox"
    flight_offers_provider: Literal["auto", "nuitee", "duffel"] = "auto"
    distribusion_api_key: str = Field(default="", repr=False)
    distribusion_environment: Literal["sandbox", "live"] = "sandbox"
    distribusion_offers_enabled: bool = False
    apl_api_key: SecretStr = Field(default=SecretStr(""), repr=False)

    @property
    def production(self):
        return self.environment == "production"

    @property
    def password_auth(self):
        return bool(self.operator_username and self.operator_password_hash.get_secret_value())

    @property
    def auth_required(self):
        return self.password_auth or bool(self.operator_token)

    @property
    def origins(self):
        return list(dict.fromkeys([s.strip().rstrip("/") for s in self.cors_origins.split(",") if s.strip()] + [self.public_base_url.rstrip("/")]))

    @model_validator(mode="after")
    def secure_production(self):
        password_hash = self.operator_password_hash.get_secret_value()
        if bool(self.operator_username) != bool(password_hash):
            raise ValueError("Set both OPERATOR_USERNAME and OPERATOR_PASSWORD_HASH")
        if self.operator_username and (self.operator_username != self.operator_username.strip() or any(c in self.operator_username for c in "\r\n")):
            raise ValueError("OPERATOR_USERNAME must not have surrounding whitespace or line breaks")
        if password_hash and not valid_password_hash(password_hash):
            raise ValueError("Invalid OPERATOR_PASSWORD_HASH; use backend/scripts/configure_operator.py")
        if self.production:
            if not self.password_auth:
                raise ValueError("Production requires OPERATOR_USERNAME and OPERATOR_PASSWORD_HASH")
            if self.operator_token and len(self.operator_token.strip()) < 32:
                raise ValueError("Optional OPERATOR_TOKEN must have at least 32 characters in production")
            public=urlsplit(self.public_base_url)
            if public.scheme!="https" or not public.hostname or public.username or public.password or public.query or public.fragment:
                raise ValueError("Production requires a valid HTTPS PUBLIC_BASE_URL without credentials, query or fragment")
            if "*" in self.allowed_hosts or "*" in self.cors_origins:
                raise ValueError("Production requires explicit hosts and origins")
            if public.hostname not in {host.strip() for host in self.allowed_hosts.split(",")}:
                raise ValueError("ALLOWED_HOSTS must include the public hostname")
            for origin in self.origins:
                parsed=urlsplit(origin)
                if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("","/") or parsed.query or parsed.fragment:
                    raise ValueError("Production CORS origins must be explicit HTTPS origins")
        return self
