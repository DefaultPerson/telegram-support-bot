from dataclasses import dataclass

from environs import Env


@dataclass
class BotConfig:
    """
    Data class representing the configuration for the bot.

    Attributes:
    - TOKEN (str): The bot token.
    - DEV_ID (int): The developer's user ID.
    - GROUP_ID (int): The group chat ID.
    - BOT_EMOJI_ID (str): The custom emoji ID for the group's topic.
    """
    TOKEN: str
    DEV_ID: int
    GROUP_ID: int
    BOT_EMOJI_ID: str


@dataclass
class RedisConfig:
    """
    Data class representing the configuration for Redis.

    Attributes:
    - HOST (str): The Redis host.
    - PORT (int): The Redis port.
    - DB (int): The Redis database number.
    """
    HOST: str
    PORT: int
    DB: int

    def dsn(self) -> str:
        """
        Generates a Redis connection DSN (Data Source Name) using the provided host, port, and database.

        :return: The generated DSN.
        """
        return f"redis://{self.HOST}:{self.PORT}/{self.DB}"


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
    """
    PROVIDER: str
    BASE_URL: str
    API_KEY: str
    MODEL: str
    SYSTEM_PROMPT_PATH: str
    TIMEOUT_S: int
    SYSTEM_PROMPT_B64: str = ""


@dataclass
class Config:
    """
    Data class representing the overall configuration for the application.

    Attributes:
    - bot (BotConfig): The bot configuration.
    - redis (RedisConfig): The Redis configuration.
    - policy (PolicyConfig): The policy engine configuration.
    - ai (AIConfig): The LLM provider configuration.
    """
    bot: BotConfig
    redis: RedisConfig
    policy: PolicyConfig
    ai: AIConfig


def load_config() -> Config:
    """
    Load the configuration from environment variables and return a Config object.

    :return: The Config object with loaded configuration.
    """
    env = Env()
    env.read_env()

    return Config(
        bot=BotConfig(
            TOKEN=env.str("BOT_TOKEN"),
            DEV_ID=env.int("BOT_DEV_ID"),
            GROUP_ID=env.int("BOT_GROUP_ID"),
            BOT_EMOJI_ID=env.str("BOT_EMOJI_ID"),
        ),
        redis=RedisConfig(
            HOST=env.str("REDIS_HOST"),
            PORT=env.int("REDIS_PORT"),
            DB=env.int("REDIS_DB"),
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
        ),
    )
