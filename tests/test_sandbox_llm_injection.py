import os
import json
import time

from econ_sim.script_engine import sandbox


SCRIPT = """
def generate_decisions(context):
    # attempt to call llm if available
    if llm is None:
        return {'used_llm': False, 'context': context}
    try:
        # some llm adapters may be sync or have generate method; try safe calls
        if hasattr(llm, 'generate'):
            r = llm.generate(type('Req', (), {'model':'m','prompt':'x','max_tokens':1}), user_id='test')
            return {'used_llm': True, 'content': getattr(r, 'content', None)}
        elif hasattr(llm, 'complete'):
            import asyncio
            c = asyncio.get_event_loop().run_until_complete(llm.complete('x'))
            return {'used_llm': True, 'content': c}
    except Exception as e:
        return {'error': str(e)}
    return {'used_llm': False}
"""


def test_inline_llm_session():
    class Dummy:
        def __init__(self):
            self.calls = []

        def generate(self, req, *, user_id=None):
            self.calls.append((req, user_id))
            return type("R", (), {"content": "inline-mock"})()

    session = Dummy()
    # call worker inline to avoid pickling local Dummy class
    out = sandbox._pool_worker(
        SCRIPT, {"x": 1}, set(sandbox.ALLOWED_MODULES), 1.0, llm_session=session
    )
    assert isinstance(out, tuple) and out[0] == "__ok__"
    res = out[1]
    assert res["used_llm"] is True
    assert res["content"] == "inline-mock"


def test_pool_llm_factory_path():
    # point factory to tests.mocks.llm_factories.get_test_llm_session
    factory_path = "tests.mocks.llm_factories.get_test_llm_session"
    # force per-call subprocess so the subprocess runner reads ECON_SIM_LLM_FACTORY
    res = sandbox.execute_script(
        SCRIPT,
        {"y": 2},
        timeout=2.0,
        llm_factory_path=factory_path,
        force_per_call=True,
    )
    assert res["used_llm"] is True
    assert res["content"] == "mocked"
