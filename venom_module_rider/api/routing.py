"""Shared module error response for operator and device endpoints."""

from fastapi import HTTPException
from fastapi.routing import APIRoute

from ..errors import RiderError


class RiderRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RiderError as exc:
                raise HTTPException(
                    exc.status, detail={"code": exc.code, "details": exc.details}
                ) from exc

        return handler
