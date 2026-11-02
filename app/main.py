from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.analyzer import CatAnalyzer, create_analyzer
from app.config import Settings
from app.images import ImageValidationError, save_image
from app.jobs import AnalysisService
from app.models import ModelCatalog
from app.schemas import (
    AnalysisCreated,
    AnalysisListResponse,
    AnalysisLogs,
    AnalysisStatus,
    AnalysisSummary,
    ChatOptions,
    HealthResponse,
    ModelsResponse,
    ReasoningEffort,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_TOKENS_LIMIT = 200_000


def _parse_chat_options(
    reasoning_effort: str | None,
    temperature: str | None,
    max_tokens: str | None,
) -> ChatOptions:
    """Validate optional form values and convert them to the job options."""
    effort_value = (reasoning_effort or "").strip()
    effort: ReasoningEffort | None = None
    if effort_value:
        try:
            effort = ReasoningEffort(effort_value)
        except ValueError as exc:
            allowed = " / ".join(item.value for item in ReasoningEffort)
            raise HTTPException(
                status_code=400,
                detail=f"推論の強さは {allowed} から選んでください。",
            ) from exc

    temperature_value: float | None = None
    raw_temperature = (temperature or "").strip()
    if raw_temperature:
        try:
            temperature_value = float(raw_temperature)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="temperature は 0〜2 の数値で指定してください。",
            ) from exc
        if not 0 <= temperature_value <= 2:
            raise HTTPException(
                status_code=400,
                detail="temperature は 0〜2 の範囲で指定してください。",
            )

    max_tokens_value: int | None = None
    raw_max_tokens = (max_tokens or "").strip()
    if raw_max_tokens:
        try:
            max_tokens_value = int(raw_max_tokens)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"max_tokens は 1〜{MAX_TOKENS_LIMIT} の整数で指定してください。",
            ) from exc
        if not 1 <= max_tokens_value <= MAX_TOKENS_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"max_tokens は 1〜{MAX_TOKENS_LIMIT} の整数で指定してください。",
            )

    return ChatOptions(
        reasoning_effort=effort,
        temperature=temperature_value,
        max_tokens=max_tokens_value,
    )


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
        await service.initialize()
        app.state.settings = app_settings
        app.state.service = service
        app.state.catalog = app_catalog
        try:
            yield
        finally:
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
    reasoning_effort: Annotated[
        str | None,
        Form(description="Optional reasoning effort: none/minimal/low/medium/high"),
    ] = None,
    temperature: Annotated[
        str | None,
        Form(description="Optional sampling temperature between 0 and 2"),
    ] = None,
    max_tokens: Annotated[
        str | None,
        Form(description="Optional maximum number of output tokens"),
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
        # Blank or omitted means "use the catalog default", which follows the
        # configured model unless it is not structured-output capable.
        model = (await catalog.list()).default
        if not model:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Structured Output に対応した vision モデルがありません。"
                    "LLM_MODEL を変更してください。"
                ),
            )
    elif not await catalog.is_allowed(model):
        raise HTTPException(
            status_code=400,
            detail=(
                "対応していないモデルです。"
                "Structured Output に対応した vision モデルを選択してください。"
            ),
        )
    if len(selected) > settings.max_files:
        raise HTTPException(
            status_code=400,
            detail=f"一度に送信できる画像は {settings.max_files} 枚までです。",
        )

    options = _parse_chat_options(reasoning_effort, temperature, max_tokens)
    job = await service.new_job(model, options)
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
            await service.attach_image(job, image)
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


@router.get("/analyses", response_model=AnalysisListResponse)
async def list_analyses(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnalysisListResponse:
    service = get_service(request)
    return AnalysisListResponse(analyses=await service.history(limit=limit, offset=offset))


@router.get("/analyses/{job_id}", response_model=AnalysisSummary)
async def get_analysis(request: Request, job_id: str) -> AnalysisSummary:
    service = get_service(request)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")
    return service.summary(job)


@router.get("/analyses/{job_id}/logs", response_model=AnalysisLogs)
async def get_analysis_logs(request: Request, job_id: str) -> AnalysisLogs:
    service = get_service(request)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")
    return AnalysisLogs(id=job.id, logs=await service.logs(job.id))


@router.get("/analyses/{job_id}/images/{image_id}")
async def get_analysis_image(request: Request, job_id: str, image_id: str) -> Response:
    service = get_service(request)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")

    image = await service.image_bytes(job_id, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    data, media_type = image
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.delete("/analyses/{job_id}", status_code=204)
async def delete_analysis(request: Request, job_id: str) -> Response:
    service = get_service(request)
    if not await service.delete(job_id):
        raise HTTPException(status_code=404, detail="解析ジョブが見つかりません。")
    return Response(status_code=204)


app = create_app()
