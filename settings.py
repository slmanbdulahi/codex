import ipaddress
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_HOSTS = ("localhost", "127.0.0.1")
HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*")


@dataclass(frozen=True)
class Settings:
    database: Path
    production: bool
    secret_key: str
    allowed_hosts: tuple[str, ...]
    trusted_proxies: str
    session_seconds: int = 8 * 60 * 60

    @classmethod
    def from_env(cls):
        environment = os.getenv("APP_ENV", "development").lower()
        if environment not in {"development", "production"}:
            raise RuntimeError("APP_ENV must be development or production")
        production = environment == "production"
        database = os.getenv("DATABASE_PATH")
        secret_key = os.getenv("SECRET_KEY", "").strip()
        hosts = tuple(
            host.strip().lower() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()
        )
        proxies = tuple(ip.strip() for ip in os.getenv("FORWARDED_ALLOW_IPS", "").split(",") if ip.strip())

        if production:
            if not database or not Path(database).is_absolute():
                raise RuntimeError("Production requires an absolute DATABASE_PATH")
            if len(secret_key) < 32 or len(set(secret_key)) < 12:
                raise RuntimeError("Production requires a randomly generated SECRET_KEY")
            if not hosts:
                raise RuntimeError("Production requires explicit ALLOWED_HOSTS")
            if os.getenv("COOKIE_SECURE", "1").lower() not in {"1", "true", "yes", "on"}:
                raise RuntimeError("Production requires secure cookies")
        elif any(host not in LOCAL_HOSTS for host in hosts) or proxies:
            raise RuntimeError("Development only supports local hosts and direct connections")

        if any(host not in LOCAL_HOSTS and not HOST_PATTERN.fullmatch(host) for host in hosts):
            raise RuntimeError("ALLOWED_HOSTS must contain exact hostnames")
        for ip in proxies:
            try:
                network = ipaddress.ip_network(ip)
            except ValueError:
                raise RuntimeError(
                    "FORWARDED_ALLOW_IPS must contain explicit IP addresses or networks"
                ) from None
            if network.prefixlen == 0:
                raise RuntimeError("FORWARDED_ALLOW_IPS cannot trust every address")

        return cls(
            Path(database or BASE_DIR / "data" / "zubrify.db").expanduser().resolve(),
            production,
            secret_key or secrets.token_urlsafe(48),
            hosts or LOCAL_HOSTS,
            ",".join(proxies),
        )
