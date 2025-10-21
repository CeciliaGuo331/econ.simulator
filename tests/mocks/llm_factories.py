"""测试用 LLM 工厂，供 sandbox 注入使用。"""


class TestLLMSession:
    def __init__(self):
        self.calls = []

    def generate(self, req, *, user_id=None):
        # simple synchronous-style return compatible with LLMSession.generate
        self.calls.append((req, user_id))
        return type(
            "R",
            (),
            {
                "model": getattr(req, "model", "test"),
                "content": "mocked",
                "usage_tokens": 0,
            },
        )()


def get_test_llm_session():
    return TestLLMSession()
