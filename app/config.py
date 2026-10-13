from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ad_domain: str = "unetrans.com.co"
    ad_server: str = "SIMM-PDC1.unetrans.com.co"

    api_key: str

    tenant_id: str
    client_id: str
    client_secret: str
    ad_approvers: str = ""
    ad_approval_group_id: str
    approval_account: str = ""
    approval_account_username: str = ""
    approval_account_password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()