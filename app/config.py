from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ad_domain: str = "unetrans.com.co"
    ad_server: str = "SIMM-PDC1.unetrans.com.co"

    # Cuenta de servicio LDAP (migración a Linux, ver docs/06).
    # Vacía por defecto: sin ellas, las operaciones de AD fallan con un
    # error explícito en lugar de impedir el arranque del proceso.
    ad_bind_user: str = ""
    ad_bind_password: str = ""

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