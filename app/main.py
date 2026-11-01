from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app.analyzer import CatAnalyzer, create_analyzer
from app.config import Settings
from app.images import ImageValidationError, save_image
from app.jobs import AnalysisService
from app.models import ModelCatalog
from app.schemas import (
    AnalysisCreated,
    AnalysisStatus,
    AnalysisSummary,
    HealthResponse,
    ModelsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def create_app(
    settings: Settings | None = None,
    analyzer: CatAnalyzer | None = None,
    catalog: ModelCatalog | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    app_analyzer = analyzer if analyzer is not None else create_analyzer(app_settings)
    app_catalog = catalog if catalog is not None else ModelCatalog(app_settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = AnalysisService(app_settings, app_analyzer)
        app.state.settings = app_settings
        app.state.service = service
        app.state.catalog = app_catalog
        cleanup_task = asyncio.create_task(service.cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            await service.shutdown()

    app = FastAPI(
        title="catfinder",
        description="Find cat images with an LLM-backed classifier.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


def get_service(request: Request) -> AnalysisService:
    return request.app.state.service


def get_catalog(request: Request) -> ModelCatalog:
    return request.app.state.catalog


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    service = get_service(request)
    return HealthResponse(
        status="ok",
        llm_configured=service.analyzer_available,
        model=settings.llm_model if service.analyzer_available else "",
    )


@router.get("/models", response_model=ModelsResponse)
async def list_models(request: Request) -> ModelsResponse:
    return await get_catalog(request).list()


@router.post(
    "/analyses",
    response_model=AnalysisCreated,
    status_code=202,
)
async def create_analysis(
    request: Request,
    files: Annotated[list[UploadFile] | None, File(description="Images to classify")] = None,
    model: Annotated[
        str | None,
        Form(description="Optional vision model id; defaults to LLM_MODEL"),
    ] = None,
) -> AnalysisCreated:
    settings: Settings = request.app.state.settings
    service = get_service(request)
    catalog = get_catalog(request)
    selected = files or []

    if not service.analyzer_available:
        raise HTTPException(
            status_code=503,
            detail="LLM が設定されていません。LLM_MODEL を設定してください。",
        )
    if not selected:
        raise HTTPException(status_code=400, detail="画像を1枚以上選択してください。")
    if model is not None:
        model = model.strip()
    if not model:
        # Blank or omitted means "use the configured model."
        model = settings.llm_model.strip()
    elif not await catalog.is_allowed(model):
        raise HTTPException(
            status_code=400,
            detail="対応していないモデルです。選択肢から選んでください。",
        )
    if len(selected) > settings.max_files:
        raise HTTPException(
            status_code=400,
            detail=f"一度に送信できる画像は {settings.max_files} 枚までです。",
        )

    job = service.new_job(model)
    try:
        for upload in selected:
            try:
                image = await save_image(
                    upload,
                    job.directory,
                    max_bytes=settings.max_file_bytes,
                )
            except ImageValidationError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
            service.attach_image(job, image)
    except Exception:
        await service.delete(job.id)
        raise
    finally:
        for upload in selected:
            await upload.close()

    service.start(job)
    return AnalysisCreated(
        id=job.id,
        status=AnalysisStatus.QUEUED,
        total=len(job.images),
    )


@router.get("/analyses/{job_id}", response_model=AnalysisSummary)
async def get_analysis(request: Request, job_id: str) -> AnalysisSummary:
    service = get_service(request)
    job = service.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")
    return service.summary(job)


@router.get("/analyses/{job_id}/images/{image_id}")
async def get_analysis_image(request: Request, job_id: str, image_id: str) -> FileResponse:
    service = get_service(request)
    job = service.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")

    image = next((item for item in job.images if item.id == image_id), None)
    if image is None or not image.path.is_file():
        raise HTTPException(status_code=404, detail="画像が見つかりません。")

    return FileResponse(
        image.path,
        media_type=image.media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.delete("/analyses/{job_id}", status_code=204)
async def delete_analysis(request: Request, job_id: str) -> Response:
    service = get_service(request)
    if not await service.delete(job_id):
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")
    return Response(status_code=204)


app = create_app()
