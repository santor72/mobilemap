from typing import Any, Protocol


class NetworkReader(Protocol):
    async def list_maps(self) -> list[dict[str, Any]]: ...

    async def list_layers(self, map_id: str) -> list[dict[str, Any]]: ...

    async def get_bounds(self, map_id: str) -> dict[str, float]: ...

    async def get_features(
        self,
        map_id: str,
        bbox: tuple[float, float, float, float],
        layers: list[str] | None,
        zoom: int,
    ) -> dict[str, Any]: ...

    async def get_feature(self, feature_id: str) -> dict[str, Any]: ...

    async def get_asset(self, asset_id: str) -> bytes: ...
