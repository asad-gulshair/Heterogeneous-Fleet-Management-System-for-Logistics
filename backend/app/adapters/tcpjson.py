"""Custom TCP + JSON framing (pallet stacker AGV). One JSON object per line, protected by a CRC32."""
import json
import zlib
from .base import VendorAdapter, Wire, compress, expand


def _crc(body: dict) -> str:
    return format(zlib.crc32(json.dumps(body, separators=(",", ":"), sort_keys=True).encode()) & 0xFFFFFFFF, "08x")


def frame(body: dict) -> dict:
    return {**body, "crc": _crc(body)}


def unframe(msg: dict) -> dict:
    body = {k: v for k, v in msg.items() if k != "crc"}
    if msg.get("crc") != _crc(body):
        raise ValueError("bad CRC")           # a real driver would drop the frame and ask for a resend
    return body


class TcpJsonAdapter(VendorAdapter):
    kind, protocol = "tcp_json", "TCP / JSON"

    def encode_order(self, now, robot, path, target, label, task_id):
        pts = compress(robot.cell, path)
        body = {"seq": self.next_header(), "cmd": "MOVE_PATH", "agv": robot.id, "pts": [list(p) for p in pts], "act": label}
        return [Wire(self.protocol, f"tcp://{robot.id}:7000 MOVE_PATH", frame(body), "out")]

    def decode_order(self, msgs, start):
        body = unframe(msgs[0].payload)
        pts = [tuple(p) for p in body["pts"]]
        path = expand(pts)
        return path, (pts[-1] if path else None)

    def encode_state(self, now, robot):
        x, y = robot.pos
        mode = {"moving": "RUN", "to_charge": "RUN", "working": "WORK", "waiting": "HOLD", "charging": "CHG", "fault": "ERR"}.get(robot.state, "IDLE")
        body = {"seq": self.next_header(), "evt": "STATUS", "agv": robot.id, "x": round(x, 2), "y": round(y, 2),
                "soc": round(robot.battery, 1), "mode": mode}
        return [Wire(self.protocol, f"tcp://{robot.id}:7000 STATUS", frame(body), "in")]

    def decode_state(self, msgs):
        b = unframe(msgs[0].payload)
        return {"x": b["x"], "y": b["y"], "battery": b["soc"], "driving": b["mode"] == "RUN",
                "charging": b["mode"] == "CHG", "error": b["mode"] == "ERR"}
