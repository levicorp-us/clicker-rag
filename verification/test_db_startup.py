from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from app.db_startup import connect_or_start


@pytest.mark.parametrize("states,expected_commands", [
    (["ready"], []),
    (["starting", "ready"], []),
    (["offline", "starting", "ready"], ["status"]),
])
def test_existing_cluster_never_receives_start(states, expected_commands):
    with (
        patch("app.db_startup.connection_state", side_effect=states),
        patch("app.db_startup.time.sleep"),
        patch("app.db_startup.subprocess.run", return_value=CompletedProcess([], 0)) as command,
    ):
        assert connect_or_start(Path("cluster"), Path("secret"), 55438, Path("pg_ctl"))
        assert [call.args[0][-1] for call in command.call_args_list] == expected_commands


def test_connection_failure_does_not_start_a_second_server():
    with (
        patch("app.db_startup.connection_state", side_effect=RuntimeError("wrong cluster")),
        patch("app.db_startup.subprocess.run") as command,
        pytest.raises(RuntimeError, match="wrong cluster"),
    ):
        connect_or_start(Path("cluster"), Path("secret"), 55438, Path("pg_ctl"))
    command.assert_not_called()
