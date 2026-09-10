"""Stateless REST facade for extraction and structure-aware chunking."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from table_aware_chunker import build_chunks, extract_corpus


def _positive_environment_integer(name: str, default: int) -> int:
    """Read one positive integer service limit from the environment."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def _environment_flag(name: str, default: bool = False) -> bool:
    """Read a conventional true/false environment flag."""
    raw = os.getenv(name, str(default)).strip().casefold()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _positive_environment_float(name: str, default: float) -> float:
    """Read one positive floating-point service limit from the environment."""
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


MAX_PDF_FILES = _positive_environment_integer("TAC_MAX_PDF_FILES", 10)
MAX_PDF_FILE_BYTES = _positive_environment_integer(
    "TAC_MAX_PDF_FILE_BYTES", 50_000_000
)
MAX_URL_PAGES = _positive_environment_integer("TAC_MAX_URL_PAGES", 20)
MAX_URL_PAGE_BYTES = _positive_environment_integer(
    "TAC_MAX_URL_PAGE_BYTES", 5_000_000
)
URL_TIMEOUT_SECONDS = _positive_environment_float("TAC_URL_TIMEOUT_SECONDS", 20.0)
ALLOW_URL_SOURCES = _environment_flag("TAC_ALLOW_URL_SOURCES", False)


def _load_build_info(path: Path) -> dict[str, Any]:
    """Load validated build metadata, with an explicit local-development fallback."""
    if not path.is_file():
        return {
            "git": {
                "commit": {
                    "id": {"abbrev": "unknown"},
                    "time": "unknown",
                }
            }
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        commit = value["git"]["commit"]
        abbreviation = commit["id"]["abbrev"]
        commit_time = commit["time"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Invalid build information in {path}") from exc
    if not isinstance(abbreviation, str) or not abbreviation:
        raise RuntimeError(f"Invalid Git commit abbreviation in {path}")
    if not isinstance(commit_time, str) or not commit_time:
        raise RuntimeError(f"Invalid Git commit time in {path}")
    return {
        "git": {
            "commit": {
                "id": {"abbrev": abbreviation},
                "time": commit_time,
            }
        }
    }


BUILD_INFO = _load_build_info(Path(__file__).with_name("build-info.json"))


class ExtractionResponse(BaseModel):
    """Portable extraction artifacts returned without server-side state."""

    corpus_id: str
    request_key: str
    content_key: str
    source_count: int
    blocks: list[dict[str, Any]]
    corpus_text: str
    sources: list[dict[str, Any]]


class ChunkRequest(BaseModel):
    """Blocks and word-chunking options accepted by the chunk endpoint."""

    blocks: list[dict[str, Any]]
    strategy: str = "words"
    chunk_size: int = Field(default=450, gt=0)
    chunk_overlap: int = Field(default=60, ge=0)
    min_text_chunk_size: int = Field(default=100, ge=0)
    max_text_page_span: int = Field(default=2, gt=0)


class ChunkResponse(BaseModel):
    """Structure-aware chunks in retrieval order."""

    count: int
    chunks: list[dict[str, Any]]


class HealthResponse(BaseModel):
    """Readiness response used by containers and Kubernetes probes."""

    status: str


class GitCommitId(BaseModel):
    """Abbreviated Git identity for the source revision in the image."""

    abbrev: str


class GitCommit(BaseModel):
    """Git revision and commit time captured while building the image."""

    id: GitCommitId
    time: str


class GitInformation(BaseModel):
    """Git metadata included in the service build."""

    commit: GitCommit


class StatusResponse(BaseModel):
    """Service status together with immutable build information."""

    git: GitInformation
    status: str


app = FastAPI(
    title="Table-Aware Chunker REST API",
    version="1.0.0",
    description=(
        "Stateless HTTP access to table-aware PDF/HTML extraction and "
        "structure-preserving chunking."
    ),
)


async def _save_pdf_upload(upload: UploadFile, target: Path) -> None:
    """Stream one upload to disk while enforcing the configured byte limit."""
    total = 0
    try:
        with target.open("wb") as stream:
            while data := await upload.read(1024 * 1024):
                total += len(data)
                if total > MAX_PDF_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"{upload.filename or 'PDF upload'} exceeds the "
                            f"{MAX_PDF_FILE_BYTES}-byte limit"
                        ),
                    )
                stream.write(data)
    finally:
        await upload.close()


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Report that the process is ready to accept requests."""
    return HealthResponse(status="ok")


@app.get("/status", response_model=StatusResponse, tags=["operations"])
async def status() -> StatusResponse:
    """Report service availability and the Git revision used for this build."""
    return StatusResponse(git=BUILD_INFO["git"], status="UP")


@app.post("/v1/extract", response_model=ExtractionResponse, tags=["library"])
async def extract_endpoint(
    files: Annotated[
        list[UploadFile] | None,
        File(description="One or more text-based PDF documents"),
    ] = None,
    urls: Annotated[
        list[str] | None,
        Form(description="Repeat this field for each static HTML URL"),
    ] = None,
) -> ExtractionResponse:
    """Extract uploaded PDFs or enabled HTML URLs into common structured blocks."""
    uploads = files or []
    url_sources = [value.strip() for value in (urls or []) if value.strip()]
    if bool(uploads) == bool(url_sources):
        raise HTTPException(
            status_code=422,
            detail="Provide either PDF files or HTML URLs, but not both",
        )
    if len(uploads) > MAX_PDF_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"At most {MAX_PDF_FILES} PDF files are accepted",
        )
    if len(url_sources) > MAX_URL_PAGES:
        raise HTTPException(
            status_code=413,
            detail=f"At most {MAX_URL_PAGES} HTML URLs are accepted",
        )
    if url_sources and not ALLOW_URL_SOURCES:
        raise HTTPException(
            status_code=403,
            detail=(
                "HTML URL extraction is disabled; set "
                "TAC_ALLOW_URL_SOURCES=true only in a suitably restricted network"
            ),
        )

    with tempfile.TemporaryDirectory(prefix="table-aware-rest-") as temporary:
        temporary_root = Path(temporary)
        if uploads:
            upload_root = temporary_root / "uploads"
            upload_root.mkdir()
            source_values: list[str | Path] = []
            for number, upload in enumerate(uploads, start=1):
                filename = Path(upload.filename or f"upload-{number}.pdf").name
                if Path(filename).suffix.casefold() != ".pdf":
                    await upload.close()
                    raise HTTPException(
                        status_code=422,
                        detail=f"Only PDF uploads are accepted: {filename}",
                    )
                # Separate directories allow duplicate upload names while the
                # basename retained in block provenance remains unchanged.
                target = upload_root / f"{number:02d}" / filename
                target.parent.mkdir()
                await _save_pdf_upload(upload, target)
                source_values.append(target)
            source_type = "pdf"
        else:
            source_values = url_sources
            source_type = "html"

        try:
            saved = await run_in_threadpool(
                lambda: extract_corpus(
                    source_values,
                    output_directory=temporary_root / "corpora",
                    source_type=source_type,
                    max_pdf_files=MAX_PDF_FILES,
                    max_pdf_file_bytes=MAX_PDF_FILE_BYTES,
                    max_url_pages=MAX_URL_PAGES,
                    url_timeout_seconds=URL_TIMEOUT_SECONDS,
                    max_url_page_bytes=MAX_URL_PAGE_BYTES,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        blocks = json.loads(saved.blocks_path.read_text(encoding="utf-8"))
        corpus_text = saved.corpus_path.read_text(encoding="utf-8")
        sources = json.loads(saved.sources_path.read_text(encoding="utf-8"))
        return ExtractionResponse(
            corpus_id=saved.blocks_path.parent.name,
            request_key=saved.request_key,
            content_key=saved.content_key,
            source_count=saved.source_count,
            blocks=blocks,
            corpus_text=corpus_text,
            sources=sources,
        )


@app.post("/v1/chunks", response_model=ChunkResponse, tags=["library"])
async def chunks_endpoint(request: ChunkRequest) -> ChunkResponse:
    """Build structure-aware chunks from blocks returned by the extract endpoint."""
    if request.chunk_overlap >= request.chunk_size:
        raise HTTPException(
            status_code=422,
            detail="chunk_overlap must be smaller than chunk_size",
        )
    try:
        chunks = await run_in_threadpool(
            lambda: build_chunks(
                request.blocks,
                strategy=request.strategy,
                chunk_size=request.chunk_size,
                chunk_overlap=request.chunk_overlap,
                min_text_chunk_size=request.min_text_chunk_size,
                max_text_page_span=request.max_text_page_span,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ChunkResponse(count=len(chunks), chunks=chunks)
