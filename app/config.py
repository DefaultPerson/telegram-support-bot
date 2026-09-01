from dataclasses import dataclass

from environs import Env


@dataclass
class BotConfig:
    """
    Data class representing the configuration for the bot.

    Attributes:
    - TOKEN (str): The bot token.
    - DEV_IDS (list[int]): The developer/admin user IDs (first one is primary).
    - GROUP_ID (int): The group chat ID.
    - BOT_EMOJI_ID (str): The custom emoji ID for the group's topic.
    """
    TOKEN: str
    DEV_IDS: list[int]
    GROUP_ID: int
    BOT_EMOJI_ID: str

    @property
    def DEV_ID(self) -> int:
        """Primary developer ID (kept for single-recipient notifications)."""
        return self.DEV_IDS[0]


@dataclass
class RedisConfig:
    """
    Data class representing the configuration for Redis (FSM + apscheduler only).

    Attributes:
    - HOST (str): The Redis host.
    - PORT (int): The Redis port.
    - DB (int): The Redis database number.
    - PASSWORD (str): The Redis password (empty when the instance has no auth).
    """
    HOST: str
    PORT: int
    DB: int
    PASSWORD: str = ""

    def dsn(self) -> str:
        """
        Generates a Redis connection DSN using host, port, db and optional password.

        :return: The generated DSN.
        """
        auth = f":{self.PASSWORD}@" if self.PASSWORD else ""
        return f"redis://{auth}{self.HOST}:{self.PORT}/{self.DB}"


@dataclass
class DatabaseConfig:
    """
    Data class representing the configuration for PostgreSQL (user layer + subscribers).

    Attributes:
    - URL (str): asyncpg DSN, e.g. ``postgresql://user:pass@host:5432/db``.
    """
    URL: str


@dataclass
class PolicyConfig:
    """
    Data class representing the configuration for the optional policy engine.

    Attributes:
    - ENABLED (bool): Whether the declarative policy engine is active.
    - PATH (str): Path to the policy YAML file.
    - INLINE_B64 (str): Base64-encoded YAML used when PATH does not exist
      (handy for platforms where mounting a file is awkward).
    """
    ENABLED: bool
    PATH: str
    INLINE_B64: str = ""


@dataclass
class AIConfig:
    """
    Data class representing the configuration for the optional LLM provider.

    Attributes:
    - PROVIDER (str): "none" disables the LLM; "openai_compatible" enables it.
    - BASE_URL (str): OpenAI-compatible base URL (OpenRouter, OpenAI, local, ...).
    - API_KEY (str): API key; empty disables the provider.
    - MODEL (str): Model identifier.
    - SYSTEM_PROMPT_PATH (str): Path to the system prompt file.
    - TIMEOUT_S (int): Per-request timeout in seconds.
    - MAX_TOKENS (int): Cap on the drafted reply length. Without it providers
      reserve the model's full output window up front, which makes pay-as-you-go
      backends reject the request once the remaining balance is smaller than
      that reservation.
    """
    PROVIDER: str
    BASE_URL: str
    API_KEY: str
    MODEL: str
    SYSTEM_PROMPT_PATH: str
    TIMEOUT_S: int
    SYSTEM_PROMPT_B64: str = ""
    MAX_TOKENS: int = 1024


@dataclass
class Config:
    """
    Data class representing the overall configuration for the application.

    Attributes:
    - bot (BotConfig): The bot configuration.
    - redis (RedisConfig): The Redis configuration (FSM + scheduler).
    - db (DatabaseConfig): The PostgreSQL configuration (user layer + subscribers).
    - policy (PolicyConfig): The policy engine configuration.
    - ai (AIConfig): The LLM provider configuration.
    """
    bot: BotConfig
    redis: RedisConfig
    db: DatabaseConfig
    policy: PolicyConfig
    ai: AIConfig


def load_config() -> Config:
    """
    Load the configuration from environment variables and return a Config object.

    :return: The Config object with loaded configuration.
    """
    env = Env()
    env.read_env()

    # Admin IDs: prefer BOT_DEV_IDS (CSV); fall back to legacy single BOT_DEV_ID.
    dev_ids = env.list("BOT_DEV_IDS", subcast=int, default=None)
    if not dev_ids:
        dev_ids = [env.int("BOT_DEV_ID")]

    return Config(
        bot=BotConfig(
            TOKEN=env.str("BOT_TOKEN"),
            DEV_IDS=dev_ids,
            GROUP_ID=env.int("BOT_GROUP_ID"),
            BOT_EMOJI_ID=env.str("BOT_EMOJI_ID"),
        ),
        redis=RedisConfig(
            HOST=env.str("REDIS_HOST"),
            PORT=env.int("REDIS_PORT"),
            DB=env.int("REDIS_DB"),
            PASSWORD=env.str("REDIS_PASSWORD", ""),
        ),
        db=DatabaseConfig(
            URL=env.str("DATABASE_URL"),
        ),
        policy=PolicyConfig(
            ENABLED=env.bool("POLICY_ENABLED", False),
            PATH=env.str("POLICY_CONFIG_PATH", "config/policy.yaml"),
            INLINE_B64=env.str("POLICY_YAML_B64", ""),
        ),
        ai=AIConfig(
            PROVIDER=env.str("AI_PROVIDER", "none"),
            BASE_URL=env.str("AI_BASE_URL", "https://openrouter.ai/api/v1"),
            API_KEY=env.str("AI_API_KEY", ""),
            MODEL=env.str("AI_MODEL", "openai/gpt-5.4-nano"),
            SYSTEM_PROMPT_PATH=env.str("AI_SYSTEM_PROMPT_PATH", "config/system_prompt.txt"),
            TIMEOUT_S=env.int("AI_TIMEOUT_S", 8),
            SYSTEM_PROMPT_B64=env.str("AI_SYSTEM_PROMPT_B64", ""),
            MAX_TOKENS=env.int("AI_MAX_TOKENS", 1024),
        ),
    )
