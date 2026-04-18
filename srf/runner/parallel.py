from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from srf.config.models import SecretConfig
from srf.rotation.rotator import RotationResult, SecretRotator


class ParallelRunner:
    """Execute secret rotation for multiple SPs concurrently.

    Each SP is processed in its own thread. Exceptions are caught at the
    rotator level (see SecretRotator.rotate), so all results are always
    returned — no SP silently disappears from the output.
    """

    def __init__(self, rotator: SecretRotator, max_workers: int = 5) -> None:
        self._rotator = rotator
        self._max_workers = max_workers

    def run(self, secrets: list[SecretConfig]) -> list[RotationResult]:
        results: list[RotationResult] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {pool.submit(self._rotator.rotate, s): s for s in secrets}
            for future in as_completed(futures):
                secret = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    # Safety net — SecretRotator.rotate should never raise,
                    # but guard here too.
                    results.append(
                        RotationResult(
                            name=secret.name,
                            app_id=secret.app_id,
                            rotated=False,
                            error=f"Unexpected runner error: {exc}",
                        )
                    )
        return results
