"""Package whitelisted source and synthetic evidence; never include private backups."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".local-web-lab"
CODE_FOLDERS = ("operations", "smartorder", "vitali_web", "tests", "odoo_addons", "scripts", "docs")
CODE_SUFFIXES = {".py", ".html", ".css", ".js", ".scss", ".xml", ".json", ".toml", ".md", ".txt",
                 ".yaml", ".yml", ".ps1", ".cmd", ".svg", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".ttf"}
ROOT_FILES = ("manage.py", "streamlit_app.py", "README.md", "AGENTS.md", ".gitignore", ".streamlit/config.toml")
SMARTORDER_EVIDENCE = ("comprobacion.json", "resultados-integrados.json", "comprobacion-respaldo.json")
TEST_LOGS = ("pruebas-finales.log", "pantallas-finales.log")
LAB_LOGS = ("comprobacion-integrada.log", "comprobacion-ampliada.log", "comprobar-integrado-final.log")
README_PACKAGE = """# Entrega SmartOrder Vitali: código y evidencia del laboratorio

Este paquete contiene las versiones Django y Streamlit, sus reglas compartidas,
el addon propio de Odoo Community y las comprobaciones reproducibles. Las pruebas,
Excel, facturas de ejemplo y capturas incluidas corresponden exclusivamente a un
laboratorio con datos simulados. No son registros operativos ni fiscales de Vitali.

Consulta README.md, docs/PARIDAD_Y_SIGUIENTES_FASES.md y
docs/ODOO_COMMUNITY_LOCAL.md para entender el alcance, iniciar las aplicaciones y
conocer los límites fiscales. MANIFIESTO_SHA256.json permite verificar cada archivo.

Este es un paquete de código y evidencia; no es un instalador universal. No incluye
Python, PostgreSQL, el núcleo descargado de Odoo, los entornos privados preparados,
contraseñas, claves, bases de datos ni respaldos privados. Las dependencias y la
instancia de laboratorio deben prepararse y configurarse en el equipo de destino.

Los respaldos privados comprobados permanecen en la instalación local original:
.local-web-lab/backups/ para SmartOrder y .local-odoo/backups/ para Odoo. Conserva
las bases y adjuntos juntos; esos respaldos y sus credenciales no forman parte de
este ZIP compartible. Las fuentes recibidas de la empresa tampoco se incluyen.

La carpeta evidencia/ conserva los resultados de pruebas, las fuentes sintéticas
y las capturas disponibles al generar esta entrega. Las rutas internas que puedan
figurar en un resultado identifican la instalación donde se verificó el recorrido.
"""


def known_secrets():
    """Detect accidental inclusion without ever displaying local secret values."""
    values = []
    for file in (ROOT / ".local-odoo/credentials.json", LAB / "credentials.json"):
        if file.is_file():
            values.extend(value for value in json.loads(file.read_text(encoding="utf-8-sig")).values()
                          if isinstance(value, str) and len(value) >= 16)
    config = ROOT / ".local-odoo/smartorder.json"
    if config.is_file():
        value = json.loads(config.read_text(encoding="utf-8-sig")).get("key")
        if isinstance(value, str) and len(value) >= 16:
            values.append(value)
    for file in (LAB / "secret.key", ROOT / ".local-web/secret.key"):
        if file.is_file():
            values.append(file.read_text(encoding="utf-8").strip())
    return tuple(value.encode("utf-8") for value in set(values) if len(value) >= 16)


def package():
    selected = {}
    forbidden = {"__pycache__", ".git", ".venv", ".venv-web", "backups", "restauraciones"}

    def add(file, archive_name):
        if file.is_symlink():
            raise RuntimeError("A packaged file is a symbolic link")
        file = file.resolve()
        if not file.is_relative_to(ROOT):
            raise RuntimeError("A packaged file points outside the project")
        assert not any(part in forbidden for part in file.parts)
        assert file.name not in {"credentials.json", "secret.key", "secrets.toml"}
        assert not file.name.startswith(".env") and file.suffix not in {".sqlite3", ".sqlite", ".db", ".dump", ".conf"}
        assert archive_name not in selected
        selected[archive_name] = file

    for folder in CODE_FOLDERS:
        for file in sorted((ROOT / folder).rglob("*")):
            if file.is_file() and not any(part in forbidden for part in file.parts) and file.suffix.lower() in CODE_SUFFIXES:
                add(file, file.relative_to(ROOT).as_posix())
    for name in ROOT_FILES:
        file = ROOT / name
        if not file.is_file():
            raise RuntimeError(f"Required source file missing: {name}")
        add(file, name)
    for file in sorted(ROOT.iterdir()):
        if file.is_file() and (file.suffix.lower() == ".cmd" or file.match("requirements*.txt")):
            add(file, file.name)

    odoo_evidence = ROOT / ".local-odoo/evidence"
    for file in sorted(odoo_evidence.rglob("*")):
        if file.is_file() and file.suffix.lower() in {".json", ".pdf", ".png", ".csv", ".xlsx"}:
            add(file, "evidencia/odoo/" + file.relative_to(odoo_evidence).as_posix())
    for name in SMARTORDER_EVIDENCE:
        file = LAB / name
        if not file.is_file():
            raise RuntimeError(f"Required SmartOrder verification missing: {name}")
        assert json.loads(file.read_text(encoding="utf-8")).get("status") == "passed"
        add(file, "evidencia/smartorder/" + name)
    for name in TEST_LOGS:
        file = LAB / name
        raw = file.read_bytes()
        # Expected permission denials can log a traceback while their tests pass.
        assert b"\nOK" in raw and b"FAILED" not in raw, "The final test log must contain a successful run"
        add(file, "evidencia/smartorder/" + name)
    for name in LAB_LOGS:
        file = LAB / name
        raw = file.read_bytes()
        assert b"PASS:" in raw and b"AssertionError" not in raw, "Laboratory verification must have passed"
        add(file, "evidencia/smartorder/" + name)

    marker = json.loads((LAB / "laboratorio.json").read_text(encoding="utf-8"))
    assert marker.get("kind") == "SmartOrder synthetic laboratory"
    for source in marker["sources"]:
        file = Path(source["path"]).resolve()
        assert file.is_relative_to((LAB / "fuentes_simuladas").resolve()) and "SIMULAD" in file.name
        assert file.suffix.lower() == ".xlsx" and sha256(file.read_bytes()).hexdigest() == source["sha256"]
        add(file, "evidencia/fuentes_simuladas/" + file.name)
    captures = LAB / "capturas"
    for file in sorted(captures.iterdir()):
        if file.is_file() and file.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            add(file, "evidencia/capturas/" + file.name)
    if not any(name.startswith("evidencia/capturas/") for name in selected):
        raise RuntimeError("Add the verified interface captures before packaging the final delivery")
    if not any(name.startswith("evidencia/odoo/") and name.endswith(".pdf") for name in selected):
        raise RuntimeError("Place the verified synthetic PDF under .local-odoo/evidence before packaging")

    secrets = known_secrets()
    entries = []
    output = LAB / "entrega"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ("SmartOrder_Vitali_Entrega_Laboratorio_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + ".zip")
    # ponytail: explicit code/evidence roots instead of a repository-wide archive that could collect private state.
    with ZipFile(archive, "x", compression=ZIP_DEFLATED, compresslevel=6) as delivery:
        for name, file in sorted(selected.items()):
            raw = file.read_bytes()
            if any(secret in raw for secret in secrets):
                raise RuntimeError(f"A local secret appeared in {name}; the package must not be shared")
            if file.suffix.lower() == '.xlsx':
                with ZipFile(file) as workbook:
                    if any(any(secret in part_raw for secret in secrets)
                           for part_raw in (workbook.read(part) for part in workbook.namelist())):
                        raise RuntimeError(f"A local secret appeared inside {name}; the package must not be shared")
            entries.append({"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
            delivery.writestr(name, raw)
        raw = README_PACKAGE.encode("utf-8")
        delivery.writestr("README_ENTREGA.md", raw)
        entries.append({"path": "README_ENTREGA.md", "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
        manifest = {"kind": "SmartOrder source and synthetic evidence delivery", "files": entries,
            "notice": "No credentials, databases, private backups, received business files or downloaded Odoo core are included."}
        manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        delivery.writestr("MANIFIESTO_SHA256.json", manifest_raw)

    with ZipFile(archive) as delivery:
        assert delivery.testzip() is None
        assert delivery.read("MANIFIESTO_SHA256.json") == manifest_raw
        assert set(delivery.namelist()) == {entry["path"] for entry in entries} | {"MANIFIESTO_SHA256.json"}
        for entry in entries:
            raw = delivery.read(entry["path"])
            assert len(raw) == entry["bytes"] and sha256(raw).hexdigest() == entry["sha256"]
        assert all(not any(part in {".local", ".local-web", ".local-odoo", ".local-web-lab", ".git", "__pycache__"}
                           for part in Path(name).parts) for name in delivery.namelist())
        assert not any(Path(name).name in {"credentials.json", "secret.key", "secrets.toml"}
                       or Path(name).suffix in {".sqlite3", ".db", ".dump", ".conf"} for name in delivery.namelist())
    archive_digest = sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(archive_digest + "  " + archive.name + "\n", encoding="utf-8")
    evidence = {"status": "passed", "archive": str(archive), "sha256": archive_digest,
        "manifest_sha256": sha256(manifest_raw).hexdigest(),
        "files": len(entries) + 1, "size_bytes": archive.stat().st_size,
        "checks": ["Whitelist", "No local known secrets", "No databases or private backups", "Synthetic source hashes", "ZIP integrity and every SHA-256"]}
    (output / "comprobacion-entrega.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: delivery ZIP verified; {evidence['files']} files; {evidence['size_bytes']} bytes")
    print(f"Archive: {archive}")
    return evidence


if __name__ == "__main__":
    package()
