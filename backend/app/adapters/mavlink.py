"""MAVLink-style mission protocol (inventory drone). Uses MISSION_COUNT / MISSION_ITEM_INT / HEARTBEAT naming."""
from .base import VendorAdapter, Wire, compress, expand

MAV_CMD_NAV_WAYPOINT = 16
SCALE = 1000        # local coordinates in millimetres (1 cell = 1 m)
ALT = 3.0           # flight height above the racks, metres


class MavlinkAdapter(VendorAdapter):
    kind, protocol = "mavlink", "MAVLink-style"

    def encode_order(self, now, robot, path, target, label, task_id):
        pts = compress(robot.cell, path)[1:]            # MAVLink missions list targets, not the start
        sysid = int(robot.id.rsplit("_", 1)[1])
        msgs = [Wire(self.protocol, "MISSION_COUNT", {"target_system": sysid, "count": len(pts), "label": label}, "out")]
        for i, (x, y) in enumerate(pts):
            msgs.append(Wire(self.protocol, "MISSION_ITEM_INT",
                             {"target_system": sysid, "seq": i, "frame": 1, "command": MAV_CMD_NAV_WAYPOINT,
                              "x": x * SCALE, "y": y * SCALE, "z": ALT}, "out"))
        msgs.append(Wire(self.protocol, "MISSION_ACK", {"type": 0}, "in"))
        return msgs

    def decode_order(self, msgs, start):
        items = sorted((m.payload for m in msgs if m.topic == "MISSION_ITEM_INT"), key=lambda p: p["seq"])
        pts = [tuple(start)] + [(p["x"] // SCALE, p["y"] // SCALE) for p in items]
        path = expand(pts)
        return path, (pts[-1] if path else None)

    def encode_state(self, now, robot):
        x, y = robot.pos
        sysid = int(robot.id.rsplit("_", 1)[1])
        return [
            Wire(self.protocol, "HEARTBEAT", {"system_id": sysid, "type": 2, "base_mode": 209 if robot.state != "fault" else 0,
                                              "system_status": 4 if robot.state != "fault" else 5}, "in"),
            Wire(self.protocol, "LOCAL_POSITION_NED", {"x": round(x, 2), "y": round(y, 2), "z": -ALT}, "in"),
            Wire(self.protocol, "BATTERY_STATUS", {"battery_remaining": int(robot.battery)}, "in"),
        ]

    def decode_state(self, msgs):
        by = {m.topic: m.payload for m in msgs}
        return {"x": by["LOCAL_POSITION_NED"]["x"], "y": by["LOCAL_POSITION_NED"]["y"],
                "battery": by["BATTERY_STATUS"]["battery_remaining"], "driving": by["HEARTBEAT"]["system_status"] == 4,
                "charging": False, "error": by["HEARTBEAT"]["system_status"] == 5}
