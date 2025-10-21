from tests.test_sandbox_llm_injection import (
    test_inline_llm_session,
    test_pool_llm_factory_path,
)

if __name__ == "__main__":
    print("running inline test")
    test_inline_llm_session()
    print("inline passed")
    print("running pool factory test")
    test_pool_llm_factory_path()
    print("pool factory passed")
