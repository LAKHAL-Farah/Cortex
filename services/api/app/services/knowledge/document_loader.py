from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}


@dataclass(frozen=True)
class LoadedDocument:
    document_id: str
    title: str
    text: str

    source_path: str
    source_name: str

    document_checksum: str

    metadata: dict[str, Any]


def normalize_text(text: str) -> str:
    """
    Nettoie les retours à la ligne et les espaces inutiles
    tout en conservant la structure logique du document.
    """

    normalized = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    # Supprimer les espaces inutiles en fin de ligne.
    normalized = re.sub(
        r"[ \t]+\n",
        "\n",
        normalized,
    )

    # Éviter plusieurs lignes vides successives.
    normalized = re.sub(
        r"\n{3,}",
        "\n\n",
        normalized,
    )

    return normalized.strip()


def extract_title(
    text: str,
    extension: str,
    fallback: str,
) -> str:
    """
    Extrait le titre du document.

    Pour Markdown :
        utilise le premier titre de niveau 1 (# Titre).

    Pour TXT/PDF :
        utilise la première ligne non vide.

    Si aucun titre n'est trouvé :
        utilise le nom du fichier.
    """

    for line in text.splitlines():
        value = line.strip()

        if not value:
            continue

        if extension == ".md" and value.startswith("# "):
            title = value[2:].strip()

            if title:
                return title

        if extension != ".md":
            return value

    return fallback


def extract_pdf_text(
    path: Path,
) -> tuple[str, int]:
    """
    Extrait le texte de toutes les pages textuelles d'un PDF.

    Retourne :
        (texte_normalise, nombre_de_pages)
    """

    reader = PdfReader(str(path))

    pages: list[str] = []

    for page in reader.pages:
        page_text = page.extract_text() or ""

        if page_text.strip():
            pages.append(page_text)

    text = normalize_text(
        "\n\n".join(pages)
    )

    return text, len(reader.pages)


def build_document_id(
    relative_path: Path,
) -> str:
    """
    Construit un identifiant stable à partir du chemin relatif.

    Exemple :

        prometheus/node-down.md

    devient :

        prometheus-node-down
    """

    path_without_extension = (
        relative_path
        .with_suffix("")
        .as_posix()
    )

    document_id = re.sub(
        r"[^a-zA-Z0-9]+",
        "-",
        path_without_extension,
    )

    return document_id.strip("-").lower()


def build_checksum(text: str) -> str:
    """
    Calcule le SHA256 du contenu normalisé.

    Il permettra de détecter si un document a changé
    depuis la dernière ingestion.
    """

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def load_document(
    path: Path,
    root: Path,
    *,
    environment: str = "production",
    criticality: str = "medium",
    document_type: str = "runbook",
    language: str = "fr",
) -> LoadedDocument:
    """
    Charge un seul document et construit les métadonnées
    utilisées ensuite par le pipeline Knowledge.
    """

    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Extension non supportée : {extension}"
        )

    relative_path = path.relative_to(root)

    fallback_title = (
        path.stem
        .replace("-", " ")
        .replace("_", " ")
        .strip()
        .title()
    )

    page_count: int | None = None

    if extension == ".pdf":
        text, page_count = extract_pdf_text(path)

    else:
        text = normalize_text(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

    if not text:
        raise ValueError(
            f"Aucun texte exploitable dans le document : {path}"
        )

    title = extract_title(
        text=text,
        extension=extension,
        fallback=fallback_title,
    )

    service = (
        relative_path.parts[0]
        if len(relative_path.parts) > 1
        else "general"
    )

    metadata: dict[str, Any] = {
        "service": service.lower(),
        "environment": environment.lower(),
        "criticality": criticality.lower(),
        "document_type": document_type.lower(),
        "language": language.lower(),
        "extension": extension.lstrip("."),
    }

    if page_count is not None:
        metadata["page_count"] = page_count

    return LoadedDocument(
        document_id=build_document_id(
            relative_path
        ),
        title=title,
        text=text,
        source_path=relative_path.as_posix(),
        source_name=path.name,
        document_checksum=build_checksum(text),
        metadata=metadata,
    )


def load_documents(
    root_directory: str | Path,
    *,
    environment: str = "production",
    criticality: str = "medium",
    document_type: str = "runbook",
    language: str = "fr",
) -> list[LoadedDocument]:
    """
    Charge tous les documents supportés présents dans
    un dossier et ses sous-dossiers.
    """

    root = (
        Path(root_directory)
        .expanduser()
        .resolve()
    )

    if not root.exists():
        raise FileNotFoundError(
            f"Le dossier n'existe pas : {root}"
        )

    if not root.is_dir():
        raise NotADirectoryError(
            f"Le chemin n'est pas un dossier : {root}"
        )

    document_paths = sorted(
        path
        for path in root.rglob("*")
        if (
            path.is_file()
            and path.suffix.lower()
            in SUPPORTED_EXTENSIONS
        )
    )

    documents: list[LoadedDocument] = []

    for path in document_paths:
        try:
            document = load_document(
                path=path,
                root=root,
                environment=environment,
                criticality=criticality,
                document_type=document_type,
                language=language,
            )

            documents.append(document)

        except Exception as exc:
            raise RuntimeError(
                f"Échec du chargement de {path}: {exc}"
            ) from exc

    return documents