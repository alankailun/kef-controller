from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_APP_NAME = "KEF Controller"


def default_local_appdata_dir(app_name: str) -> str:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return os.path.join(local_appdata, app_name)
    return os.path.join(os.path.expanduser("~"), ".config", app_name)


@dataclass(slots=True)
class SystemConfig:
    app_name: str = DEFAULT_APP_NAME
    speaker_model_label: str = "KEF Wireless Speaker"
    app_data_dir: str = field(default_factory=lambda: default_local_appdata_dir(DEFAULT_APP_NAME))
    config_file_name: str = "config.json"
    state_file_name: str = "state.json"
    log_dir: str = field(default_factory=lambda: os.path.join(default_local_appdata_dir(DEFAULT_APP_NAME), "logs"))
    log_file_name: str = "kef_controller.log"
    single_instance_mutex_name: str = "KEFController_SingleInstance_Mutex"
