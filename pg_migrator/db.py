"""PostgreSQL connection helper that prefers IPv4.

`psql` and `pg_dump` use the system libpq, which can fall back to IPv4 when a
hostname also resolves to an unreachable IPv6 address. `psycopg2` may stop at
the first (IPv6) address instead, which surfaces as "Network is unreachable".

Resolving the host to IPv4 and passing it as `hostaddr` makes psycopg2 use the
same route as the command-line tools, while `host` still drives TLS.
"""

import ipaddress
import socket
from urllib.parse import parse_qs, urlsplit

import psycopg2


def ipv4_hostaddr(db_url: str) -> str | None:
    """Return an IPv4 address for the URL's host, or None if there isn't one."""
    if "://" not in db_url:
        return None

    try:
        parts = urlsplit(db_url)
    except ValueError:
        return None

    if not parts.hostname or "hostaddr" in parse_qs(parts.query):
        return None

    try:
        return parts.hostname if ipaddress.ip_address(parts.hostname).version == 4 else None
    except ValueError:
        pass  # not an IP literal - resolve it

    try:
        infos = socket.getaddrinfo(parts.hostname, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return None

    return infos[0][4][0] if infos else None


def connect(db_url: str, **kwargs):
    """psycopg2.connect() that prefers IPv4, like psql/pg_dump do."""
    hostaddr = ipv4_hostaddr(db_url)
    if hostaddr:
        kwargs.setdefault("hostaddr", hostaddr)
    return psycopg2.connect(db_url, **kwargs)


def asyncpg_kwargs(db_url: str) -> dict:
    """Extra asyncpg.connect() kwargs that route over IPv4, like psql.

    asyncpg discards the port from the DSN when `host` is overridden, so the
    port has to be passed along explicitly.
    """
    hostaddr = ipv4_hostaddr(db_url)
    if not hostaddr:
        return {}

    parts = urlsplit(db_url)
    if parts.hostname == hostaddr:
        return {}  # already an IPv4 literal

    kwargs = {"host": hostaddr}
    try:
        if parts.port:
            kwargs["port"] = parts.port
    except ValueError:
        pass
    return kwargs
