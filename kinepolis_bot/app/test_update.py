"""Test bijwerken via een nagemaakte Home Assistant Supervisor."""
import os

os.environ["SUPERVISOR_TOKEN"] = "sup-token"
import core  # noqa: E402

calls, state = [], {"latest": "0.6.0"}


class R:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return {"result": "ok", "data": self._d}


def fake_request(method, url, timeout=None, headers=None):
    assert headers["Authorization"] == "Bearer sup-token"
    path = url.replace("http://supervisor", "")
    calls.append(f"{method} {path}")
    if path == "/store/reload":
        state["latest"] = "0.6.1"                                  # HA ziet nu de nieuwe versie
        return R({})
    if path == "/addons/self/info":
        return R({"version": "0.6.0", "version_latest": state["latest"], "slug": "16a8a2cf_kinepolis_bot",
                  "update_available": state["latest"] != "0.6.0"})
    return R({})


core.requests.request = fake_request
sup = core.Supervisor()
assert sup.version() == {"installed": "0.6.0", "latest": "0.6.0", "update": False, "slug": "16a8a2cf_kinepolis_bot"}
v = sup.check()
assert v["update"] and v["latest"] == "0.6.1", v
sup.update()
assert calls[-1] == "POST /store/addons/16a8a2cf_kinepolis_bot/update", calls
print(calls)
print("OK")
