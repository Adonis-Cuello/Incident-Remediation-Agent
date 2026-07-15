from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    dashscope_api_key: str = Field(..., env="DASHSCOPE_API_KEY")
    qwen_model: str = Field("qwen-plus", env="QWEN_MODEL")
    qwen_base_url: str = Field(
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        env="QWEN_BASE_URL",
    )

    agent_poll_interval_seconds: int = Field(15, env="AGENT_POLL_INTERVAL_SECONDS")
    agent_max_auto_remediations_per_hour: int = Field(
        10, env="AGENT_MAX_AUTO_REMEDIATIONS_PER_HOUR"
    )

    approval_ui_port: int = Field(8080, env="APPROVAL_UI_PORT")
    approval_ui_secret: str = Field("changeme", env="APPROVAL_UI_SECRET")

    audit_log_path: str = Field("./audit/audit.jsonl", env="AUDIT_LOG_PATH")
    policy_path: str = Field("./policy.yaml", env="POLICY_PATH")

    docker_host: str = Field("unix:///var/run/docker.sock", env="DOCKER_HOST")
    mcp_server_port: int = Field(8090, env="MCP_SERVER_PORT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
