from kef_app.appdata import AppConfig, UserConfigStore, SpeakerStateStore
from kef_app.controller import KefPowerController
from kef_app.headless_runtime import run_headless
from kef_app.logging_setup import build_logger
from kef_app.platform_windows import (
    ensure_single_instance,
    ensure_startup_registration,
    maybe_handle_startup_task_repair,
    register_application_restart,
)


def main():
    repair_exit_code = maybe_handle_startup_task_repair()
    if repair_exit_code is not None:
        raise SystemExit(repair_exit_code)

    base_config = AppConfig()
    user_config_store = UserConfigStore(base_config)
    config = user_config_store.load_or_create()

    log = build_logger(config)
    for message in user_config_store.drain_startup_messages():
        log.info(message)

    ensure_startup_registration(config.app_name, log, mode=config.startup_registration_mode)

    _mutex_handle = ensure_single_instance(log, config.single_instance_mutex_name)
    _ = _mutex_handle

    state_store = SpeakerStateStore(config, log)
    controller = KefPowerController(config, log, state_store=state_store)
    register_application_restart(log, config.enable_application_restart, config.application_restart_flags)
    run_headless(config, controller, log)


if __name__ == "__main__":
    main()
