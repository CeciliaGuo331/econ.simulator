import sys, os

sys.path.insert(0, os.getcwd())
from concurrent.futures import ProcessPoolExecutor
from econ_sim.script_engine import sandbox

code = """
def generate_decisions(context):
    while True:
        pass
"""
print("creating pool")
with ProcessPoolExecutor(max_workers=1) as p:
    print("submitting")
    fut = p.submit(
        sandbox._pool_worker,
        code,
        {"world_state": {}, "config": {}},
        set(sandbox.ALLOWED_MODULES),
        0.2,
    )
    try:
        res = fut.result(timeout=1.0)
        print("RES:", res)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print("EXC", type(e), e)
print("done")
