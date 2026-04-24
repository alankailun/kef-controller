from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from ..config import AppConfig
from ..storage import SpeakerStateStore, UserConfigStore
from ..controller import KefPowerController
from .logging_setup import build_logger
from ..platform.windows import (
    ensure_single_instance,
    ensure_startup_registration,
    maybe_handle_startup_task_repair,
    register_application_restart,
)


@dataclass(slots=True)
class RuntimeContext:
    config: AppConfig
    user_config_store: UserConfigStore
    log: logging.Logger
    controller: KefPowerController
    single_instance_mutex: Optional[int]


def exit_if_startup_task_repair_requested() -> None:
    repair_exit_code = maybe_handle_startup_task_repair()
    if repair_exit_code is not None:
        raise SystemExit(repair_exit_code)


def build_runtime_context(*, extra_log_handlers: Iterable[logging.Handler] = ()) -> RuntimeContext:
    base_config = AppConfig()
    user_config_store = UserConfigStore(base_config)
    config = user_config_store.load_or_create()

    log = build_logger(config)
    for handler in extra_log_handlers:
        log.addHandler(handler)

    for message in user_config_store.drain_startup_messages():
        log.info(message)

    ensure_startup_registration(config.app_name, log, mode=config.startup_registration_mode)

    single_instance_mutex = ensure_single_instance(log, config.single_instance_mutex_name)
    state_store = SpeakerStateStore(config, log)
    controller = KefPowerController(config, log, state_store=state_store)
    register_application_restart(log, config.enable_application_restart, config.application_restart_flags)

    return RuntimeContext(
        config=config,
        user_config_store=user_config_store,
        log=log,
        controller=controller,
        single_instance_mutex=single_instance_mutex,
    )
