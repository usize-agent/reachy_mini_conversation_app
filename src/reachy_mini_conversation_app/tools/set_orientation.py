import logging
from typing import Any, Dict, Tuple

import numpy as np
from scipy.spatial.transform import Rotation as R

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove


logger = logging.getLogger(__name__)

# Conservative safe ranges (degrees), inside the mechanical limits enforced by
# the SDK's analytical kinematics (max_relative_yaw=65deg, max_body_yaw=160deg)
# and the antenna range used at INIT/SLEEP (~175deg), leaving IK solver margin.
HEAD_PITCH_RANGE_DEG: Tuple[float, float] = (-35.0, 35.0)
HEAD_ROLL_RANGE_DEG: Tuple[float, float] = (-20.0, 20.0)
HEAD_YAW_RANGE_DEG: Tuple[float, float] = (-60.0, 60.0)
BODY_YAW_RANGE_DEG: Tuple[float, float] = (-150.0, 150.0)
ANTENNA_RANGE_DEG: Tuple[float, float] = (-170.0, 170.0)
DURATION_RANGE_S: Tuple[float, float] = (0.1, 5.0)

_AXIS_BOUNDS: Dict[str, Tuple[float, float]] = {
    "head_pitch": HEAD_PITCH_RANGE_DEG,
    "head_roll": HEAD_ROLL_RANGE_DEG,
    "head_yaw": HEAD_YAW_RANGE_DEG,
    "body_yaw": BODY_YAW_RANGE_DEG,
    "left_antenna": ANTENNA_RANGE_DEG,
    "right_antenna": ANTENNA_RANGE_DEG,
}


def _clamp(value: float, bounds: Tuple[float, float]) -> Tuple[float, bool]:
    """Clamp value into bounds; return (clamped_value, was_clamped)."""
    lo, hi = bounds
    clamped = max(lo, min(hi, value))
    return clamped, clamped != value


class SetOrientation(Tool):
    """Precisely set head roll/pitch/yaw, body yaw, and antenna angles."""

    name = "set_orientation"
    description = (
        "Precisely set the robot's orientation in degrees: head_pitch (positive looks down), "
        "head_roll (head tilt), head_yaw (positive turns left), body_yaw (positive turns left), "
        "and/or left_antenna/right_antenna. Any axis you omit keeps its current value, so you can "
        "set just one axis or several together. Out-of-range values are clamped to a safe limit. "
        f"Ranges: head_pitch {HEAD_PITCH_RANGE_DEG}, head_roll {HEAD_ROLL_RANGE_DEG}, "
        f"head_yaw {HEAD_YAW_RANGE_DEG}, body_yaw {BODY_YAW_RANGE_DEG}, antennas {ANTENNA_RANGE_DEG}. "
        "Use this instead of move_head when you need an exact angle rather than a coarse look direction."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {
            "head_pitch": {"type": "number", "description": "Head pitch in degrees, positive looks down."},
            "head_roll": {"type": "number", "description": "Head tilt/roll in degrees."},
            "head_yaw": {"type": "number", "description": "Head yaw in degrees, positive turns left."},
            "body_yaw": {"type": "number", "description": "Body rotation in degrees, positive turns left."},
            "left_antenna": {"type": "number", "description": "Left antenna angle in degrees."},
            "right_antenna": {"type": "number", "description": "Right antenna angle in degrees."},
            "duration": {
                "type": "number",
                "description": f"Seconds to smoothly transition, range {DURATION_RANGE_S}.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Move head/body/antennae to the requested precise orientation."""
        logger.info("Tool call: set_orientation kwargs=%s", kwargs)

        clamp_notes: list[str] = []

        def resolve(key: str, current_deg: float) -> float:
            raw = kwargs.get(key)
            if raw is None:
                return current_deg
            try:
                value = float(raw)
            except (TypeError, ValueError):
                clamp_notes.append(f"{key} ignored (not a number)")
                return current_deg
            clamped, was_clamped = _clamp(value, _AXIS_BOUNDS[key])
            if was_clamped:
                clamp_notes.append(f"{key} clamped to {clamped:g}")
            return clamped

        try:
            current_head_pose = deps.reachy_mini.get_current_head_pose()
            current_roll, current_pitch, current_yaw = R.from_matrix(np.asarray(current_head_pose)[:3, :3]).as_euler(
                "xyz", degrees=True
            )
            head_joints, antenna_joints = deps.reachy_mini.get_current_joint_positions()
            current_body_yaw_deg = float(np.rad2deg(head_joints[0]))
            current_left_antenna_deg = float(np.rad2deg(antenna_joints[0]))
            current_right_antenna_deg = float(np.rad2deg(antenna_joints[1]))

            head_pitch = resolve("head_pitch", float(current_pitch))
            head_roll = resolve("head_roll", float(current_roll))
            head_yaw = resolve("head_yaw", float(current_yaw))
            body_yaw = resolve("body_yaw", current_body_yaw_deg)
            left_antenna = resolve("left_antenna", current_left_antenna_deg)
            right_antenna = resolve("right_antenna", current_right_antenna_deg)

            duration_raw = kwargs.get("duration")
            duration = deps.motion_duration_s if duration_raw is None else float(duration_raw)
            duration, duration_clamped = _clamp(duration, DURATION_RANGE_S)
            if duration_clamped:
                clamp_notes.append(f"duration clamped to {duration:g}")

            target_head_pose = create_head_pose(0, 0, 0, head_roll, head_pitch, head_yaw, degrees=True)

            movement_manager = deps.movement_manager
            movement_manager.clear_move_queue()

            goto_move = GotoQueueMove(
                target_head_pose=target_head_pose,
                start_head_pose=current_head_pose,
                target_antennas=(np.deg2rad(left_antenna), np.deg2rad(right_antenna)),
                start_antennas=(antenna_joints[0], antenna_joints[1]),
                target_body_yaw=np.deg2rad(body_yaw),
                start_body_yaw=head_joints[0],
                duration=duration,
            )

            movement_manager.queue_move(goto_move)
            movement_manager.set_moving_state(duration)

        except Exception as e:
            logger.error("set_orientation failed")
            return {"error": f"set_orientation failed: {type(e).__name__}: {e}"}

        result: Dict[str, Any] = {
            "status": "set_orientation",
            "head_pitch": round(head_pitch, 1),
            "head_roll": round(head_roll, 1),
            "head_yaw": round(head_yaw, 1),
            "body_yaw": round(body_yaw, 1),
            "left_antenna": round(left_antenna, 1),
            "right_antenna": round(right_antenna, 1),
            "duration": round(duration, 2),
        }
        if clamp_notes:
            result["notes"] = clamp_notes
        return result
