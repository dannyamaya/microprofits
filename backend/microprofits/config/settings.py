from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Capital.com
    capital_api_key: str = ""
    capital_email: str = ""
    capital_password: str = ""
    capital_demo: bool = True
    capital_account_id: str = ""

    # X/Twitter API
    x_bearer_token: str = ""

    # Discord
    discord_webhook_url: str = ""

    # Database
    database_url: str = "postgresql://microprofits:microprofits_dev@db:5432/microprofits"

    @property
    def base_url(self) -> str:
        if self.capital_demo:
            return "https://demo-api-capital.backend-capital.com"
        return "https://api-capital.backend-capital.com"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
