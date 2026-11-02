from urllib.parse import urlparse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY", repr=False)
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    # ── MongoDB ──────────────────────────────────────────────────────────────
    # The agents read the same MongoDB the Node backend writes, so there is one
    # source of truth for patients, admissions, medications and appointments.
    mongodb_uri: str = Field(default="", alias="MONGODB_URI", repr=False)
    # Mongoose connects to the database named in the URI path; a +srv URI with
    # no path lands on "test", which is where the seeded data actually lives.
    mongodb_db: str = Field(default="test", alias="MONGODB_DB")
    mongodb_timeout_ms: int = Field(default=8000, alias="MONGODB_TIMEOUT_MS")

    hospital_timezone: str = Field(default="UTC", alias="HOSPITAL_TIMEZONE")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="Hospital Multi-Agent System", alias="APP_NAME")

    @property
    def effective_groq_model(self) -> str:
        """Returns the configured model or the default model if blank."""
        model = self.groq_model.strip() if self.groq_model else ""
        return model if model else "llama-3.3-70b-versatile"

    @property
    def adk_groq_model(self) -> str:
        """Returns the model string formatted for LiteLLM/ADK (e.g. groq/llama-3.3-70b-versatile)."""
        model = self.effective_groq_model
        if not model.startswith("groq/"):
            return f"groq/{model}"
        return model

    @property
    def effective_mongodb_db(self) -> str:
        """Resolves the database name: explicit setting, else the URI path, else 'test'."""
        if self.mongodb_db and self.mongodb_db.strip():
            return self.mongodb_db.strip()
        try:
            path = urlparse(self.mongodb_uri).path.lstrip("/")
            # Strip any query string that survived the path split.
            path = path.split("?")[0].strip()
            if path:
                return path
        except Exception:
            pass
        return "test"

    def validate_groq_configuration(self) -> None:
        """Validates that Groq API key and model are properly configured."""
        if not self.groq_api_key or not self.groq_api_key.strip():
            raise ValueError(
                "GROQ_API_KEY environment variable is missing or empty. "
                "Please configure a valid Groq API key in your .env file."
            )
        if not self.effective_groq_model:
            raise ValueError(
                "GROQ_MODEL environment variable is missing or empty. "
                "Please specify a valid Groq model in your .env file."
            )

    def validate_database_configuration(self) -> None:
        """Validates that MONGODB_URI is configured."""
        if not self.mongodb_uri or not self.mongodb_uri.strip():
            raise ValueError(
                "MONGODB_URI environment variable is missing or empty. "
                "Please configure your MongoDB connection URI in your .env file."
            )

    def get_masked_database_url(self) -> str:
        """Returns the Mongo URI with the password masked for safe logging/display."""
        if not self.mongodb_uri:
            return "<empty>"
        try:
            parsed = urlparse(self.mongodb_uri)
            if parsed.password:
                netloc = f"{parsed.username}:***@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                return parsed._replace(netloc=netloc).geturl()
            return self.mongodb_uri
        except Exception:
            return "<masked_url>"


settings = Settings()
