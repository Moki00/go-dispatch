from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    bedrock_kb_id: str = ""
    dynamodb_tickets_table: str = "GoDispatch_Tickets"
    dynamodb_clients_table: str = "GoDispatch_Clients"
    sns_dispatch_topic_arn: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache()
def get_settings() -> Settings:
    return Settings()