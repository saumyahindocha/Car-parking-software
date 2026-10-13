"""Process configuration from environment variables (see deploy/.env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PARK_", extra="ignore")

    database_url: str = "sqlite:///./parking.db"
    secret_key: str = "change-me-in-production"
    token_ttl_hours: int = 14
    site_timezone: str = "Asia/Kolkata"
    image_root: str = "./data/images"
    upload_root: str = "./data/uploads"
    dashboard_dist: str = "../dashboard/dist"
    worker_web_dist: str = "../worker_app/build/web"  # phone app (browser build), served at /app/

    # device keys (ANPR service, alert units, cloud relay)
    anpr_api_key: str = "dev-anpr-key"
    device_api_key: str = "dev-device-key"
    relay_url: str = ""
    relay_api_key: str = "dev-relay-key"
    # Optional customer website (customer_web/). Empty = not deployed: receipts are self-contained text
    # (SMS + on-screen QR) and pass renewals happen with a worker.
    public_receipt_base: str = ""
    public_site_url: str = ""

    # payment gateway: "mock" | "razorpay"
    gateway: str = "mock"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # messaging: "noop" | "msg91" / "noop" | "meta"
    sms_provider: str = "noop"
    msg91_auth_key: str = ""
    msg91_sender: str = "PARKNG"
    msg91_template_receipt: str = ""
    msg91_template_settlement: str = ""
    msg91_template_pass_reminder: str = ""
    msg91_template_otp: str = ""
    whatsapp_provider: str = "noop"
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""

    run_background_jobs: bool = True
    demo_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
