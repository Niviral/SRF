from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from srf.config.models import SecretConfig
from srf.ownership.checker import OwnershipResult
from srf.rotation.rotator import RotationResult, SecretRotator

logger = logging.getLogger(__name__)


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

        logger.info("starting run: %d SP(s), max_workers=%d", len(secrets), self._max_workers)
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            rotation_futures = {pool.submit(self._rotator.rotate, s): s for s in secrets}
            ownership_futures = {}
            if self._ownership_checker is not None:
                ownership_futures = {
                    pool.submit(self._ownership_checker.check_and_update, s): s for s in secrets
                }
            logger.debug("submitted %d rotation + %d ownership task(s)", len(rotation_futures), len(ownership_futures))

            for future in as_completed(list(rotation_futures) + list(ownership_futures)):
                if future in rotation_futures:
                    secret = rotation_futures[future]
                    try:
                        rotation_results.append(future.result())
                        logger.debug("rotation task complete for [%s]", secret.name)
                    except Exception as exc:
                        logger.error("unexpected runner error for [%s]: %s", secret.name, type(exc).__name__)
                        rotation_results.append(
                            RotationResult(
                                name=secret.name,
                                obj_id=secret.obj_id,
                                rotated=False,
                                error=f"{type(exc).__name__}: unexpected runner error",
                            )
                        )
                else:
                    secret = ownership_futures[future]
                    try:
                        ownership_results.append(future.result())
                        logger.debug("ownership task complete for [%s]", secret.name)
                    except Exception as exc:
                        logger.error("unexpected runner error for [%s]: %s", secret.name, type(exc).__name__)
                        ownership_results.append(
                            OwnershipResult(
                                name=secret.name,
                                obj_id=secret.obj_id,
                                checked=True,
                                error=f"{type(exc).__name__}: unexpected runner error",
                            )
                        )

        logger.info("run complete: %d rotation result(s), %d ownership result(s)", len(rotation_results), len(ownership_results))
        return rotation_results, ownership_results
