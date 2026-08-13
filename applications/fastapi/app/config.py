from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_version: str
    app_env: str

    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str = field(repr=False)

    redis_host: str
    redis_port: int
    redis_password: str = field(repr=False)

    readiness_dependency_timeout_seconds: float
    dependency_timeout_seconds: float
    redis_cache_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("APP_NAME", "opslab-api"),
            app_version=os.getenv("APP_VERSION", "v0.2.0"),
            app_env=os.getenv("APP_ENV", "development"),

            mysql_host=os.getenv("MYSQL_HOST", "opslab-mysql"),
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_database=os.getenv("MYSQL_DATABASE", "opslab"),
            mysql_user=os.getenv("MYSQL_USER", ""),
            mysql_password=os.getenv("MYSQL_PASSWORD", ""),

            redis_host=os.getenv("REDIS_HOST", "opslab-redis"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            redis_password=os.getenv("REDIS_PASSWORD", ""),

            readiness_dependency_timeout_seconds=float(
                os.getenv("READINESS_DEPENDENCY_TIMEOUT_SECONDS", "1")
            ),
            dependency_timeout_seconds=float(
                os.getenv("DEPENDENCY_TIMEOUT_SECONDS", "2")
            ),
            redis_cache_ttl_seconds=int(
                os.getenv("REDIS_CACHE_TTL_SECONDS", "300")
            ),
        )


settings = Settings.from_env()
