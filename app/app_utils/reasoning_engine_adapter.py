# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Serve the reasoning_engine ``{class_method, input}`` contract over HTTP.

Exists to guarantee support for the Vertex AI Console Playground and Gemini
Enterprise (via ADK registration), which both invoke the engine through this
contract. Agent Engine forwards calls to ``/api/reasoning_engine`` (sync) and
``/api/stream_reasoning_engine`` (streaming); dispatch is limited to the
:class:`AdkApp` ``register_operations()`` methods so the wire output matches a
packaged Agent Engine.

Local-dev mode
--------------
When no ``GOOGLE_CLOUD_PROJECT`` is set or Vertex AI is explicitly disabled,
:class:`AdkApp` is **not** used. ``AdkApp`` is a Vertex AI construct whose
streaming methods call the Agent Platform API at runtime (not just at
initialisation), which requires GCP credentials even when only a Gemini API
key is present. In local mode the routes are served directly by the ADK
:class:`Runner` — the same runner used by every other route.

Production mode
---------------
When ``GOOGLE_CLOUD_PROJECT`` is set and Vertex AI is enabled, :class:`AdkApp`
is used unchanged so the wire format exactly matches what Vertex AI Agent Engine
expects.
"""

import inspect
import json
import logging
import os
import uuid

from fastapi import FastAPI, HTTPException, Request, encoders, responses

from app.app_utils import services

logger = logging.getLogger(__name__)


def _is_local_mode() -> bool:
    """Return True when running without a real GCP project / Vertex AI backend."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    enterprise = os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE", "")
    vertexai = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "")
    return (
        not project
        or enterprise.lower() == "false"
        or vertexai.lower() == "false"
    )


# ---------------------------------------------------------------------------
# Local-mode: serve routes with the process-wide ADK Runner
# ---------------------------------------------------------------------------

def _attach_local_routes(app: FastAPI) -> None:
    """Register reasoning-engine routes backed by the ADK Runner (no GCP needed)."""

    @app.post("/api/stream_reasoning_engine")
    async def stream_query(request: Request) -> responses.StreamingResponse:
        """Stream agent events in reasoning-engine JSONL format."""
        body = await request.json()
        class_method: str = body.get("class_method", "")
        if class_method not in {"async_stream_query", "stream_query"}:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported reasoning_engine method: {class_method!r}",
            )

        inp: dict = body.get("input") or {}
        user_id: str = inp.get("user_id") or f"local-{uuid.uuid4()}"
        message_text: str = inp.get("message") or ""

        from google.genai import types as genai_types  # noqa: PLC0415
        from app.agent import app as adk_app  # noqa: PLC0415

        new_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=message_text)],
        )

        # Prefer the runner already created by fast_api_app.lifespan (shared sessions).
        try:
            runner = request.app.state.runner
        except AttributeError:
            from google.adk.runners import Runner  # noqa: PLC0415
            runner = Runner(
                app=adk_app,
                session_service=services.get_session_service(),
                artifact_service=services.get_artifact_service(),
                auto_create_session=True,
            )

        session_service = services.get_session_service()
        session = await session_service.create_session(
            app_name=adk_app.name,
            user_id=user_id,
        )

        async def generator():
            async for event in runner.run_async(
                new_message=new_message,
                user_id=user_id,
                session_id=session.id,
            ):
                if not event.content or not event.content.parts:
                    continue
                text_parts = [
                    p for p in event.content.parts if getattr(p, "text", None)
                ]
                if not text_parts:
                    continue
                payload = {
                    "content": {
                        "role": event.content.role,
                        "parts": [{"text": p.text} for p in text_parts],
                    }
                }
                yield json.dumps(payload) + "\n"

        return responses.StreamingResponse(
            content=generator(), media_type="application/json"
        )

    @app.post("/api/reasoning_engine")
    async def query(request: Request) -> responses.JSONResponse:
        """Run agent synchronously and return combined text."""
        body = await request.json()
        class_method: str = body.get("class_method", "")
        if class_method not in {"async_query", "query"}:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported reasoning_engine method: {class_method!r}",
            )

        inp: dict = body.get("input") or {}
        user_id: str = inp.get("user_id") or f"local-{uuid.uuid4()}"
        message_text: str = inp.get("message") or ""

        from google.genai import types as genai_types  # noqa: PLC0415
        from app.agent import app as adk_app  # noqa: PLC0415

        new_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=message_text)],
        )

        try:
            runner = request.app.state.runner
        except AttributeError:
            from google.adk.runners import Runner  # noqa: PLC0415
            runner = Runner(
                app=adk_app,
                session_service=services.get_session_service(),
                artifact_service=services.get_artifact_service(),
                auto_create_session=True,
            )

        session_service = services.get_session_service()
        from app.agent import app as adk_app  # noqa: PLC0415
        session = await session_service.create_session(
            app_name=adk_app.name,
            user_id=user_id,
        )

        texts: list[str] = []
        async for event in runner.run_async(
            new_message=new_message,
            user_id=user_id,
            session_id=session.id,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        texts.append(part.text)

        return responses.JSONResponse(
            content=encoders.jsonable_encoder({"output": " ".join(texts)})
        )

    logger.info("reasoning_engine adapter: local-mode routes registered (using Runner)")


# ---------------------------------------------------------------------------
# Production-mode: serve routes with AdkApp (Vertex AI Agent Engine)
# ---------------------------------------------------------------------------

def _attach_production_routes(app: FastAPI) -> None:
    """Register routes backed by AdkApp for Vertex AI Agent Engine deployments."""
    from vertexai.agent_engines.templates.adk import AdkApp  # noqa: PLC0415
    from google.cloud.aiplatform import initializer  # noqa: PLC0415
    from app.agent import app as adk_app  # noqa: PLC0415

    runtime: AdkApp | None = None
    streaming_methods: set[str] = set()
    sync_methods: set[str] = set()

    def get_runtime() -> AdkApp:
        nonlocal runtime, streaming_methods, sync_methods
        if runtime is None:
            if not getattr(initializer.global_config, "_project", None):
                initializer.global_config._project = os.environ.get(
                    "GOOGLE_CLOUD_PROJECT", "dummy-project"
                )
            if not getattr(initializer.global_config, "_location", None):
                initializer.global_config._location = os.environ.get(
                    "GOOGLE_CLOUD_LOCATION", "us-central1"
                )
            runtime = AdkApp(
                app=adk_app,
                session_service_builder=services.get_session_service,
                artifact_service_builder=services.get_artifact_service,
            )
            runtime.set_up()
            operations = runtime.register_operations()
            streaming_methods = set(operations.get("stream", [])) | set(
                operations.get("async_stream", [])
            )
            sync_methods = set(operations.get("", [])) | set(
                operations.get("async", [])
            )
        return runtime

    def resolve_method(class_method: str, *, streaming: bool):
        rt = get_runtime()
        allowed = streaming_methods if streaming else sync_methods
        if class_method not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported reasoning_engine method: {class_method!r}",
            )
        return getattr(rt, class_method)

    @app.post("/api/stream_reasoning_engine")
    async def stream_query(request: Request) -> responses.StreamingResponse:
        body = await request.json()
        method = resolve_method(body["class_method"], streaming=True)

        async def generator():
            async for event in method(**(body.get("input") or {})):
                yield json.dumps(event) + "\n"

        return responses.StreamingResponse(
            content=generator(), media_type="application/json"
        )

    @app.post("/api/reasoning_engine")
    async def query(request: Request) -> responses.JSONResponse:
        body = await request.json()
        method = resolve_method(body["class_method"], streaming=False)
        kwargs = body.get("input") or {}
        output = (
            await method(**kwargs)
            if inspect.iscoroutinefunction(method)
            else method(**kwargs)
        )
        return responses.JSONResponse(
            content=encoders.jsonable_encoder({"output": output})
        )

    logger.info("reasoning_engine adapter: production (AdkApp) routes registered")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def attach_reasoning_engine_routes(app: FastAPI) -> None:
    """Register reasoning_engine routes, choosing the right backend automatically."""
    if _is_local_mode():
        _attach_local_routes(app)
    else:
        _attach_production_routes(app)
