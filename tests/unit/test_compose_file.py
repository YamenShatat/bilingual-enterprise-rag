"""Guard the security-relevant lines of docker-compose.yml (no YAML library needed)."""

import re
from pathlib import Path

COMPOSE = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
ACTIVE = "\n".join(line for line in COMPOSE.splitlines() if not line.lstrip().startswith("#"))


LOCAL_IMAGE = "bilingual-enterprise-rag:local"  # built from the Dockerfile, never pulled


def test_the_pulled_image_is_pinned_to_an_exact_version():
    images = re.findall(r"^\s*image:\s*(\S+)", ACTIVE, flags=re.MULTILINE)
    (image,) = [i for i in images if i != LOCAL_IMAGE]
    assert image.startswith("pgvector/pgvector:")
    tag = image.split(":", 1)[1]
    assert tag != "latest"
    assert re.fullmatch(r"\d+\.\d+\.\d+-pg\d+", tag)


def test_the_api_and_the_ui_run_the_locally_built_image():
    assert re.findall(r"^\s*image:\s*(\S+)", ACTIVE, flags=re.MULTILINE).count(LOCAL_IMAGE) == 2
    assert re.search(r"^\s*build:\s*\.\s*$", ACTIVE, flags=re.MULTILINE)


def test_every_port_is_published_on_loopback_only():
    ports = re.findall(r'^\s*-\s*"([^"]*:\d+)"\s*$', ACTIVE, flags=re.MULTILINE)
    assert len(ports) == 3, ports  # the database, the API and the UI
    assert all(port.startswith("127.0.0.1:") for port in ports), ports


def test_the_ui_does_not_look_up_the_public_ip():
    # With --server.address=0.0.0.0 alone, Streamlit asks an outside service for the public IP.
    assert "- --browser.serverAddress=127.0.0.1" in ACTIVE


def test_the_model_cache_is_mounted_read_only():
    (mount,) = re.findall(r"^\s*-\s*(\S*:/models/huggingface\S*)", ACTIVE, flags=re.MULTILINE)
    assert mount.endswith(":ro")


def test_the_password_is_required_and_has_no_default_in_the_file():
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in ACTIVE
    assert not re.search(r"POSTGRES_PASSWORD:\s*\$\{POSTGRES_PASSWORD:-", ACTIVE)


def test_the_database_has_a_healthcheck_and_a_named_volume():
    assert "pg_isready" in ACTIVE
    assert "pgdata:/var/lib/postgresql/data" in ACTIVE
