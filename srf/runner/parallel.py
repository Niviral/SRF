from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from srf.config.models import SecretConfig
from srf.ownership.checker import OwnershipResult
from srf.rotation.rotator import RotationResult, SecretRotator


class ParallelRunner:
    """Execute secret rotation and ownership checks for multiple SPs concurrently.

    Each SP is processed in its own thread. Exceptions are caught at the
    rotator/checker level, so all results are always returned — no SP
    silently disappears from the output.
    """

    def __init__(self, rotator: SecretRotator, ownership_checker=None, max_workers: int = 5) -> None:
        self._rotator = rotator
        self._ownership_checker = ownership_checker
        self._max_workers = max_workers

    def run(self, secrets: list[SecretConfig]) -> tuple[list[RotationResult], list[OwnershipResult]]:
        rotation_results: list[RotationResult] = []
        ownership_results: list[OwnershipResult] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            rotation_futures = {pool.submit(self._rotator.rotate, s): s for s in secrets}
            ownership_futures = {}
            if self._ownership_checker is not None:
                ownership_futures = {
                    pool.submit(self._ownership_checker.check_and_update, s): s for s in secrets
                }

            for future in as_completed(list(rotation_futures) + list(ownership_futures)):
                if future in rotation_futures:
                    secret = rotation_futures[future]
                    try:
                        rotation_results.append(future.result())
                    except Exception as exc:
                        rotation_results.append(
                            RotationResult(
                                name=secret.name,
                                app_id=secret.app_id,
                                rotated=False,
                                error=f"{type(exc).__name__}: unexpected runner error",
                            )
                        )
                else:
                    secret = ownership_futures[future]
                    try:
                        ownership_results.append(future.result())
                    except Exception as exc:
                        ownership_results.append(
                            OwnershipResult(
                                name=secret.name,
                                app_id=secret.app_id,
                                checked=True,
                                error=f"{type(exc).__name__}: unexpected runner error",
                            )
                        )

        return rotation_results, ownership_results
