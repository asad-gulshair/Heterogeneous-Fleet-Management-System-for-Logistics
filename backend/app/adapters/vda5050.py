"""VDA 5050 (v2.0) over MQTT - used by the Tote AMRs. Topic: uagv/v2/<manufacturer>/<serial>/<order|state>."""
from .base import VendorAdapter, Wire, compress, expand, iso


class Vda5050Adapter(VendorAdapter):
    kind, protocol = "vda5050", "VDA 5050 / MQTT"

    def _topic(self, robot, leaf):
        return f"uagv/v2/{self.vendor.replace(' ', '')}/{robot.id}/{leaf}"

    def encode_order(self, now, robot, path, target, label, task_id):
        nodes = compress(robot.cell, path)
        order = {
            "headerId": self.next_header(), "timestamp": iso(now), "version": "2.0.0",
            "manufacturer": self.vendor, "serialNumber": robot.id,
            "orderId": f"T{task_id}" if task_id else "manual", "orderUpdateId": self.next_header(),
            "nodes": [{"nodeId": f"n{i}", "sequenceId": 2 * i, "released": True,
                       "nodePosition": {"x": float(x), "y": float(y), "mapId": "warehouse"},
                       "actions": ([{"actionType": label.replace(" ", "_"), "actionId": f"a{i}", "blockingType": "HARD"}]
                                   if i == len(nodes) - 1 else [])}
                      for i, (x, y) in enumerate(nodes)],
            "edges": [{"edgeId": f"e{i}", "sequenceId": 2 * i + 1, "startNodeId": f"n{i}", "endNodeId": f"n{i + 1}",
                       "released": True, "actions": []} for i in range(len(nodes) - 1)],
        }
        return [Wire(self.protocol, self._topic(robot, "order"), order, "out")]

    def decode_order(self, msgs, start):
        nodes = sorted(msgs[0].payload["nodes"], key=lambda n: n["sequenceId"])
        pts = [(int(n["nodePosition"]["x"]), int(n["nodePosition"]["y"])) for n in nodes]
        path = expand(pts)
        return path, (pts[-1] if path else None)

    def encode_state(self, now, robot):
        x, y = robot.pos
        st = {"headerId": self.next_header(), "timestamp": iso(now), "version": "2.0.0",
              "manufacturer": self.vendor, "serialNumber": robot.id,
              "orderId": f"T{robot.task_id}" if robot.task_id else "", "driving": robot.state in ("moving", "to_charge"),
              "operatingMode": "AUTOMATIC", "agvPosition": {"x": round(x, 2), "y": round(y, 2), "mapId": "warehouse", "positionInitialized": True},
              "batteryState": {"batteryCharge": round(robot.battery, 1), "charging": robot.state == "charging"},
              "errors": [{"errorType": "robotFault", "errorLevel": "FATAL"}] if robot.state == "fault" else []}
        return [Wire(self.protocol, self._topic(robot, "state"), st, "in")]

    def decode_state(self, msgs):
        p = msgs[0].payload
        return {"x": p["agvPosition"]["x"], "y": p["agvPosition"]["y"], "battery": p["batteryState"]["batteryCharge"],
                "driving": p["driving"], "charging": p["batteryState"]["charging"], "error": bool(p["errors"])}
