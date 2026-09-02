from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.platform.windows.startup import launch as startup_launch
from kef_app.platform.windows.startup import service as startup_service
from kef_app.platform.windows.startup.common import NullLogger, StartupLaunchSpec
from kef_app.platform.windows.startup.reconcile import (
    StartupRegistrationState,
    read_startup_registration_state,
)
from kef_app.platform.windows.startup.registry import RegistryStartupEntry
from kef_app.platform.windows.startup.status import StartupRegistrationSnapshot
from kef_app.platform.windows.startup.task_scheduler import ScheduledTaskEntry
from kef_app.ui.settings.settings_service import save_settings_and_sync_startup, startup_mode_for_ui

TASK_NAME = "KEF Controller"
DESIRED = StartupLaunchSpec(r"C:\Users\alan\AppData\Local\Programs\KEF Controller\KEF Controller.exe")
OLD = StartupLaunchSpec(r"F:\KEF Controller\KEF Controller.exe")


def make_state(**updates) -> StartupRegistrationState:
    values = {
        "task_name": TASK_NAME,
        "desired": DESIRED,
        "registry_command": "",
        "registry_entries": (),
        "registry_is_current": False,
        "task_present": False,
        "task_spec": None,
        "task_is_current": False,
        "task_entries": (),
        "stale_registry_entries": (),
        "stale_task_entries": (),
    }
    values.update(updates)
    return StartupRegistrationState(**values)


class StartupReconcileTests(unittest.TestCase):
    @staticmethod
    def startup_snapshot(
        registered: bool,
        mode: str,
        *,
        cleanup_needed: bool = False,
        healthy: bool = True,
    ) -> StartupRegistrationSnapshot:
        return StartupRegistrationSnapshot(registered, mode, "Startup", "test", cleanup_needed, healthy)

    def test_interactive_startup_update_skips_global_task_enumeration(self):
        with (
            patch(
                "kef_app.platform.windows.startup.service.read_startup_registration_state",
                return_value=make_state(),
            ) as read_state,
            patch("kef_app.platform.windows.startup.service.write_registry_command", return_value=(True, "")),
            patch("kef_app.platform.windows.startup.service.delete_task", return_value=(True, "")),
        ):
            ok = startup_service.set_startup_registered(
                True,
                task_name=TASK_NAME,
                launch_spec=DESIRED,
                mode="registry",
            )

        self.assertTrue(ok)
        self.assertFalse(read_state.call_args.kwargs["include_related_tasks"])

    def test_failed_disable_preserves_task_before_registry_entries(self):
        state = make_state(
            registry_command=DESIRED.run_value,
            registry_entries=(RegistryStartupEntry(TASK_NAME, DESIRED.run_value),),
            task_present=True,
            task_spec=DESIRED,
            task_entries=(ScheduledTaskEntry(TASK_NAME, DESIRED),),
        )
        with (
            patch(
                "kef_app.platform.windows.startup.service.read_startup_registration_state",
                return_value=state,
            ),
            patch("kef_app.platform.windows.startup.service.delete_task", return_value=(False, "Access is denied.")),
            patch("kef_app.platform.windows.startup.service.delete_registry_commands") as delete_registry,
        ):
            ok = startup_service.set_startup_registered(
                False,
                task_name=TASK_NAME,
                launch_spec=DESIRED,
                mode="off",
            )

        self.assertFalse(ok)
        delete_registry.assert_not_called()

    def test_cancelled_registry_change_removes_the_new_registry_entry(self):
        state = make_state(
            task_present=True,
            task_spec=DESIRED,
            task_entries=(ScheduledTaskEntry(TASK_NAME, DESIRED),),
            task_is_current=True,
        )
        with (
            patch(
                "kef_app.platform.windows.startup.service.read_startup_registration_state",
                return_value=state,
            ),
            patch("kef_app.platform.windows.startup.service.write_registry_command", return_value=(True, "")),
            patch("kef_app.platform.windows.startup.service.delete_task", return_value=(False, "Access is denied.")),
            patch("kef_app.platform.windows.startup.service.delete_registry_commands") as delete_registry,
        ):
            ok = startup_service.set_startup_registered(
                True,
                task_name=TASK_NAME,
                launch_spec=DESIRED,
                mode="registry",
            )

        self.assertFalse(ok)
        delete_registry.assert_called_once_with((TASK_NAME,))

    def test_onedir_launch_on_another_drive_is_used_without_copying(self):
        logger = Mock()
        source = r"F:\Downloads\KEF Controller.exe"
        with (
            patch("kef_app.platform.windows.startup.launch.is_frozen_runtime", return_value=True),
            patch("kef_app.platform.windows.startup.launch.sys.executable", source),
        ):
            spec = startup_launch.ensure_preferred_executable(TASK_NAME, logger)

        self.assertEqual(spec.command, source)
        logger.info.assert_not_called()

    def test_onedir_launch_on_another_drive_accepts_null_logger(self):
        with (
            patch("kef_app.platform.windows.startup.launch.is_frozen_runtime", return_value=True),
            patch("kef_app.platform.windows.startup.launch.sys.executable", r"F:\Downloads\KEF Controller.exe"),
        ):
            spec = startup_launch.ensure_preferred_executable(TASK_NAME, NullLogger())

        self.assertEqual(spec.command, r"F:\Downloads\KEF Controller.exe")

    def test_state_finds_old_registry_entry_next_to_healthy_task(self):
        with (
            patch(
                "kef_app.platform.windows.startup.reconcile.read_registry_command",
                return_value="",
            ),
            patch(
                "kef_app.platform.windows.startup.reconcile.read_registry_commands",
                return_value=(RegistryStartupEntry("Old KEF Controller", OLD.run_value),),
            ),
            patch("kef_app.platform.windows.startup.reconcile.task_exists", return_value=True),
            patch("kef_app.platform.windows.startup.reconcile.read_task_launch_spec", return_value=DESIRED),
            patch(
                "kef_app.platform.windows.startup.reconcile.list_task_launch_specs",
                return_value=(ScheduledTaskEntry(TASK_NAME, DESIRED),),
            ),
            patch("os.path.exists", return_value=True),
        ):
            state = read_startup_registration_state(TASK_NAME, DESIRED)

        self.assertTrue(state.task_is_current)
        self.assertEqual([entry.name for entry in state.stale_registry_entries], ["Old KEF Controller"])

    def test_explicit_task_mode_does_not_fallback_to_registry(self):
        with (
            patch(
                "kef_app.platform.windows.startup.service.read_startup_registration_state",
                return_value=make_state(),
            ),
            patch("kef_app.platform.windows.startup.service.create_task", return_value=(False, "denied")),
            patch("kef_app.platform.windows.startup.service.write_registry_command") as write_registry,
        ):
            ok = startup_service.set_startup_registered(
                True,
                task_name=TASK_NAME,
                launch_spec=DESIRED,
                mode="task",
            )

        self.assertFalse(ok)
        write_registry.assert_not_called()

    def test_legacy_auto_mode_is_treated_as_task_without_registry_fallback(self):
        with (
            patch(
                "kef_app.platform.windows.startup.service.read_startup_registration_state",
                return_value=make_state(task_present=True, task_entries=(ScheduledTaskEntry(TASK_NAME, OLD),)),
            ),
            patch("kef_app.platform.windows.startup.service.create_task", return_value=(False, "denied")),
            patch("kef_app.platform.windows.startup.service.write_registry_command") as write_registry,
        ):
            ok = startup_service.set_startup_registered(
                True,
                task_name=TASK_NAME,
                launch_spec=DESIRED,
                mode="auto",
            )

        self.assertFalse(ok)
        write_registry.assert_not_called()

    def test_startup_mode_for_ui_maps_legacy_auto_to_task(self):
        self.assertEqual(startup_mode_for_ui("auto"), "task")
        self.assertEqual(startup_mode_for_ui("none"), "off")
        self.assertEqual(startup_mode_for_ui("off"), "off")

    def test_healthy_task_only_cleans_registry_without_recreating_task(self):
        state = make_state(
            registry_command=DESIRED.run_value,
            registry_entries=(RegistryStartupEntry(TASK_NAME, DESIRED.run_value),),
            registry_is_current=True,
            task_present=True,
            task_spec=DESIRED,
            task_is_current=True,
            task_entries=(ScheduledTaskEntry(TASK_NAME, DESIRED),),
        )
        with (
            patch(
                "kef_app.platform.windows.startup.service.read_startup_registration_state",
                return_value=state,
            ),
            patch("kef_app.platform.windows.startup.service.create_task") as create_task,
            patch("kef_app.platform.windows.startup.service.delete_registry_commands") as delete_registry,
        ):
            ok = startup_service.set_startup_registered(
                True,
                task_name=TASK_NAME,
                launch_spec=DESIRED,
                mode="task",
            )

        self.assertTrue(ok)
        create_task.assert_not_called()
        delete_registry.assert_called_once()

    def test_settings_save_reconciles_when_startup_method_changes(self):
        config = AppConfig().with_updates(startup_registration_mode="task")
        config_store = Mock()
        config_store.save.return_value = True

        with (
            patch("kef_app.ui.settings.settings_service.set_startup_registered", return_value=True) as set_startup,
            patch(
                "kef_app.ui.settings.settings_service.read_startup_registration_snapshot",
                return_value=self.startup_snapshot(True, "task"),
            ),
        ):
            result = save_settings_and_sync_startup(
                config,
                config_store=config_store,
                desired_startup=True,
                startup_mode_changed=True,
                log=Mock(),
            )

        self.assertTrue(result.startup_ok)
        set_startup.assert_called_once()
        self.assertEqual(set_startup.call_args.kwargs["mode"], "task")

    def test_settings_save_reconciles_when_actual_method_differs_from_selected(self):
        config = AppConfig().with_updates(startup_registration_mode="task")
        config_store = Mock()
        config_store.save.return_value = True

        with (
            patch("kef_app.ui.settings.settings_service.set_startup_registered", return_value=True) as set_startup,
            patch(
                "kef_app.ui.settings.settings_service.read_startup_registration_snapshot",
                side_effect=[self.startup_snapshot(True, "registry"), self.startup_snapshot(True, "task")],
            ),
        ):
            result = save_settings_and_sync_startup(
                config,
                config_store=config_store,
                desired_startup=True,
                startup_mode_changed=False,
                log=Mock(),
            )

        self.assertTrue(result.startup_ok)
        set_startup.assert_called_once()
        self.assertEqual(set_startup.call_args.kwargs["mode"], "task")
        self.assertEqual(result.actual_startup_mode, "task")

    def test_settings_save_off_removes_startup_entries(self):
        config = AppConfig().with_updates(startup_registration_mode="off")
        config_store = Mock()
        config_store.save.return_value = True

        with (
            patch("kef_app.ui.settings.settings_service.set_startup_registered", return_value=True) as set_startup,
            patch(
                "kef_app.ui.settings.settings_service.read_startup_registration_snapshot",
                side_effect=[self.startup_snapshot(True, "registry"), self.startup_snapshot(False, "none", healthy=False)],
            ),
        ):
            result = save_settings_and_sync_startup(
                config,
                config_store=config_store,
                desired_startup=False,
                startup_mode_changed=True,
                log=Mock(),
            )

        self.assertTrue(result.startup_ok)
        set_startup.assert_called_once()
        self.assertFalse(set_startup.call_args.args[0])
        self.assertEqual(set_startup.call_args.kwargs["mode"], "off")
        self.assertEqual(result.actual_startup_mode, "none")

    def test_cancelled_disable_restores_the_actual_task_scheduler_mode(self):
        config = AppConfig().with_updates(startup_registration_mode="registry")
        config_store = Mock()
        config_store.save.return_value = True
        retry_disable = Mock(return_value=False)

        with (
            patch("kef_app.ui.settings.settings_service.set_startup_registered", return_value=False),
            patch("kef_app.ui.settings.settings_service.get_last_startup_error", return_value="ERROR: Access is denied."),
            patch(
                "kef_app.ui.settings.settings_service.read_startup_registration_snapshot",
                return_value=self.startup_snapshot(True, "task"),
            ),
        ):
            result = save_settings_and_sync_startup(
                config,
                config_store=config_store,
                desired_startup=False,
                startup_mode_changed=True,
                log=Mock(),
                retry_disable_with_uac=retry_disable,
            )

        self.assertFalse(result.startup_ok)
        self.assertTrue(result.actual_startup_registered)
        self.assertEqual(result.actual_startup_mode, "task")
        self.assertEqual(result.updated.startup_registration_mode, "task")
        retry_disable.assert_called_once()

    def test_settings_save_task_mode_retries_with_uac_when_access_denied(self):
        config = AppConfig().with_updates(startup_registration_mode="task")
        config_store = Mock()
        config_store.save.return_value = True
        retry_enable = Mock(return_value=True)

        with (
            patch("kef_app.ui.settings.settings_service.set_startup_registered", return_value=False) as set_startup,
            patch("kef_app.ui.settings.settings_service.get_last_startup_error", return_value="ERROR: Access is denied."),
            patch(
                "kef_app.ui.settings.settings_service.read_startup_registration_snapshot",
                side_effect=[self.startup_snapshot(False, "none", healthy=False), self.startup_snapshot(True, "task")],
            ),
        ):
            result = save_settings_and_sync_startup(
                config,
                config_store=config_store,
                desired_startup=True,
                startup_mode_changed=True,
                log=Mock(),
                retry_enable_task_with_uac=retry_enable,
            )

        self.assertTrue(result.startup_ok)
        set_startup.assert_called_once()
        retry_enable.assert_called_once()
        self.assertEqual(result.actual_startup_mode, "task")

    def test_settings_save_registry_mode_retries_after_elevated_task_cleanup(self):
        config = AppConfig().with_updates(startup_registration_mode="registry")
        config_store = Mock()
        config_store.save.return_value = True
        retry_cleanup = Mock(return_value=True)

        with (
            patch(
                "kef_app.ui.settings.settings_service.set_startup_registered",
                side_effect=[False, True],
            ) as set_startup,
            patch("kef_app.ui.settings.settings_service.get_last_startup_error", side_effect=["ERROR: Access is denied.", ""]),
            patch(
                "kef_app.ui.settings.settings_service.read_startup_registration_snapshot",
                side_effect=[self.startup_snapshot(False, "none", healthy=False), self.startup_snapshot(True, "registry")],
            ),
        ):
            result = save_settings_and_sync_startup(
                config,
                config_store=config_store,
                desired_startup=True,
                startup_mode_changed=True,
                log=Mock(),
                retry_enable_registry_with_uac=retry_cleanup,
            )

        self.assertTrue(result.startup_ok)
        self.assertEqual(set_startup.call_count, 2)
        self.assertEqual(set_startup.call_args.kwargs["mode"], "registry")
        retry_cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
