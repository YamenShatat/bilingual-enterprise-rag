"""Guard the security-relevant lines of docker-compose.yml (no YAML library needed)."""

import re
from pathlib import Path

COMPOSE = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
ACTIVE = "\n".join(line for line in COMPOSE.splitlines() if not line.lstrip().startswith("#"))


def test_the_image_is_pinned_to_an_exact_version():
    (image,) = re.findall(r"^\s*image:\s*(\S+)", ACTIVE, flags=re.MULTILINE)
    assert image.startswith("pgvector/pgvector:")
    tag = image.split(":", 1)[1]
    assert tag != "latest"
    assert re.fullmatch(r"\d+\.\d+\.\d+-pg\d+", tag)


def test_the_port_is_published_on_loopback_only():
    ports = re.findall(r'^\s*-\s*"([^"]*:5432)"', ACTIVE, flags=re.MULTILINE)
    assert ports, "no port mapping to 5432 found"
    assert all(port.startswith("127.0.0.1:") for port in ports)


def test_the_password_is_required_and_has_no_default_in_the_file():
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in ACTIVE
    assert not re.search(r"POSTGRES_PASSWORD:\s*\$\{POSTGRES_PASSWORD:-", ACTIVE)


def test_the_database_has_a_healthcheck_and_a_named_volume():
    assert "pg_isready" in ACTIVE
    assert "pgdata:/var/lib/postgresql/data" in ACTIVE
