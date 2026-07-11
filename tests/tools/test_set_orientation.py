from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.set_orientation import (
    ANTENNA_RANGE_DEG,
    BODY_YAW_RANGE_DEG,
    HEAD_YAW_RANGE_DEG,
    HEAD_PITCH_RANGE_DEG,
    SetOrientation,
)


def _deps(
    *,
    head_pose: np.ndarray | None = None,
    head_joints: list[float] | None = None,
    antenna_joints: list[float] | None = None,
) -> tuple[ToolDependencies, MagicMock]:
    """Build a ToolDependencies wired to a MagicMock robot/movement manager."""
    reachy_mini = MagicMock()
    reachy_mini.get_current_head_pose.return_value = (
        head_pose if head_pose is not None else create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
    )
    reachy_mini.get_current_joint_positions.return_value = (
        head_joints or [0.0] * 7,
        antenna_joints or [0.0, 0.0],
    )
    movement_manager = MagicMock()
    deps = ToolDependencies(reachy_mini=reachy_mini, movement_manager=movement_manager, motion_duration_s=1.0)
    return deps, movement_manager


@pytest.mark.asyncio
async def test_set_orientation_sets_requested_axes() -> None:
    """Explicit axes should be applied and reflected in the result."""
    deps, movement_manager = _deps()

    result = await SetOrientation()(deps, head_yaw=20, body_yaw=-10, left_antenna=45)

    assert result["head_yaw"] == 20.0
    assert result["body_yaw"] == -10.0
    assert result["left_antenna"] == 45.0
    assert "notes" not in result

    movement_manager.clear_move_queue.assert_called_once()
    queued_move = movement_manager.queue_move.call_args.args[0]
    assert queued_move.target_body_yaw == pytest.approx(np.deg2rad(-10))
    assert queued_move.target_antennas[0] == pytest.approx(np.deg2rad(45))


@pytest.mark.asyncio
async def test_set_orientation_keeps_omitted_axes_at_current_value() -> None:
    """Axes not passed in kwargs should retain the robot's current angle."""
    current_pose = create_head_pose(0, 0, 0, 0, 15, 0, degrees=True)  # current pitch = 15deg
    deps, _ = _deps(head_pose=current_pose, head_joints=[np.deg2rad(30), 0, 0, 0, 0, 0, 0])

    result = await SetOrientation()(deps, head_yaw=10)

    assert result["head_pitch"] == pytest.approx(15.0)
    assert result["body_yaw"] == pytest.approx(30.0)
    assert result["head_yaw"] == 10.0


@pytest.mark.asyncio
async def test_set_orientation_clamps_out_of_range_values() -> None:
    """Requests beyond the safe range should clamp instead of erroring out."""
    deps, _ = _deps()

    over_pitch = HEAD_PITCH_RANGE_DEG[1] + 50
    over_yaw = HEAD_YAW_RANGE_DEG[0] - 50
    over_body_yaw = BODY_YAW_RANGE_DEG[1] + 50
    over_antenna = ANTENNA_RANGE_DEG[1] + 50

    result = await SetOrientation()(
        deps,
        head_pitch=over_pitch,
        head_yaw=over_yaw,
        body_yaw=over_body_yaw,
        right_antenna=over_antenna,
    )

    assert result["head_pitch"] == HEAD_PITCH_RANGE_DEG[1]
    assert result["head_yaw"] == HEAD_YAW_RANGE_DEG[0]
    assert result["body_yaw"] == BODY_YAW_RANGE_DEG[1]
    assert result["right_antenna"] == ANTENNA_RANGE_DEG[1]
    assert len(result["notes"]) == 4


@pytest.mark.asyncio
async def test_set_orientation_uses_default_motion_duration_when_omitted() -> None:
    """No duration kwarg should fall back to deps.motion_duration_s."""
    deps, movement_manager = _deps()
    deps.motion_duration_s = 2.5

    result = await SetOrientation()(deps, head_yaw=5)

    assert result["duration"] == 2.5
    queued_move = movement_manager.queue_move.call_args.args[0]
    assert queued_move.duration == 2.5
    movement_manager.set_moving_state.assert_called_once_with(2.5)


@pytest.mark.asyncio
async def test_set_orientation_clamps_duration() -> None:
    """Requested duration outside the safe range should clamp, not error."""
    deps, _ = _deps()

    result = await SetOrientation()(deps, duration=100)

    assert result["duration"] == 5.0
    assert any("duration clamped" in note for note in result["notes"])


@pytest.mark.asyncio
async def test_set_orientation_ignores_non_numeric_value() -> None:
    """A non-numeric axis value should be ignored (keep current) with a note, not raise."""
    deps, _ = _deps()

    result = await SetOrientation()(deps, head_yaw="not-a-number")

    assert result["head_yaw"] == 0.0
    assert any("head_yaw ignored" in note for note in result["notes"])


@pytest.mark.asyncio
async def test_set_orientation_returns_error_on_hardware_failure() -> None:
    """Robot/movement failures should be reported as an error dict, not raise."""
    reachy_mini = MagicMock()
    reachy_mini.get_current_head_pose.side_effect = RuntimeError("daemon unreachable")
    deps = ToolDependencies(reachy_mini=reachy_mini, movement_manager=MagicMock())

    result = await SetOrientation()(deps, head_yaw=10)

    assert "error" in result
    assert "daemon unreachable" in result["error"]
