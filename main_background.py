from kef_app.runtime.bootstrap import build_runtime_context, exit_if_startup_task_repair_requested
from kef_app.runtime.headless_service import run_headless


def main():
    exit_if_startup_task_repair_requested()
    runtime_context = build_runtime_context()
    run_headless(runtime_context.config, runtime_context.controller, runtime_context.log)


if __name__ == "__main__":
    main()
