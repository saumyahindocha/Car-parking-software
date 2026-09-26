"""Relay configuration from environment variables (prefix RELAY_)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RELAY_", extra="ignore")

    database_url: str = "sqlite:///./relay.db"
    # shared with the edge server (PARK_RELAY_API_KEY): authenticates /sync/* and keys the phone hash
    relay_api_key: str = "dev-relay-key"
    # signs cookies and hashes OTPs / rate-limit keys; long random string in production
    secret_key: str = "change-me-relay-secret"
    public_base_url: str = "http://localhost:8080"
    site_timezone: str = "Asia/Kolkata"

    # optional direct path to the edge (e.g. over a VPN); the pull queue is always the fallback
    edge_url: str = ""

    # payments: "mock" | "razorpay"
    gateway: str = "mock"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    qr_expiry_s: int = 900
    status_poll_min_s: float = 3.0

    # SMS for OTP: "noop" | "msg91"
    sms_provider: str = "noop"
    msg91_auth_key: str = ""
    msg91_template_otp: str = ""  # DLT-approved template id, variable ##otp##

    # OTP policy
    otp_ttl_s: int = 300
    otp_max_attempts: int = 5
    otp_per_phone: int = 3
    otp_per_phone_window_s: int = 900
    otp_per_ip: int = 10
    otp_per_ip_window_s: int = 3600
    auth_ttl_s: int = 1800

    # public pages
    recent_hours: int = 4
    recent_limit: int = 40
    search_per_ip: int = 30
    search_per_ip_window_s: int = 600
    stale_push_warn_s: int = 300
    grievance_contact: str = "Parking office, Station Road (grievance officer) - privacy@example.in"

    # web
    cookie_secure: bool = True
    trust_proxy_headers: bool = False  # true behind Caddy/NGINX (uses X-Forwarded-For)
    demo_mode: bool = False
    phone_retention_days: int = 30  # phone numbers on payment intents are wiped after this


@lru_cache
def get_settings() -> Settings:
    return Settings()
