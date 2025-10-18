from econ_sim.script_engine import sandbox
import os

os.environ["ECON_SIM_TEST_FORCE_POOL"] = "1"
code = """
def generate_decisions(context):
    while True:
        pass
"""
print("CALL execute_script with timeout 0.2")
try:
    res = sandbox.execute_script(
        code, {"world_state": {}, "config": {}}, timeout=0.2, script_id="runner"
    )
    print("RETURNED:", res)
except Exception as e:
    import traceback

    traceback.print_exc()
    print("EXC:", type(e), e)
