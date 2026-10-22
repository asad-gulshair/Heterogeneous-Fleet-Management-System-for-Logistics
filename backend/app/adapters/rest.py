"""REST/JSON vendor (Tusmec TL-65 scissor lift): missions are POSTed, status is a GET."""
from .base import VendorAdapter, Wire, compress, expand


class RestAdapter(VendorAdapter):
    kind, protocol = "rest", "REST API / HTTP"

    def encode_order(self, now, robot, path, target, label, task_id):
        nodes = compress(robot.cell, path)
        body = {"mission_id": f"M{self.next_header()}", "order_ref": task_id, "type": "NAVIGATE",
                "waypoints": [{"x": x, "y": y} for x, y in nodes], "on_arrival": label}
        return [Wire(self.protocol, f"POST /api/v1/robots/{robot.id}/missions", body, "out"),
                Wire(self.protocol, "HTTP 202 Accepted", {"accepted": True, "mission_id": body["mission_id"]}, "in")]

    def decode_order(self, msgs, start):
        pts = [(w["x"], w["y"]) for w in msgs[0].payload["waypoints"]]
        path = expand(pts)
        return path, (pts[-1] if path else None)

    def encode_state(self, now, robot):
        x, y = robot.pos
        mode = {"moving": "MOVING", "to_charge": "MOVING", "working": "LIFTING", "waiting": "HOLDING",
                "charging": "CHARGING", "fault": "ERROR"}.get(robot.state, "IDLE")
        body = {"pose": {"x": round(x, 2), "y": round(y, 2)}, "battery_pct": round(robot.battery, 1), "status": mode}
        return [Wire(self.protocol, f"GET /api/v1/robots/{robot.id}/status -> 200", body, "in")]

    def decode_state(self, msgs):
        p = msgs[0].payload
        return {"x": p["pose"]["x"], "y": p["pose"]["y"], "battery": p["battery_pct"],
                "driving": p["status"] == "MOVING", "charging": p["status"] == "CHARGING", "error": p["status"] == "ERROR"}
