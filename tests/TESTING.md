测试说明与 fixtures 约定
======================

概览
----
本文件说明 `tests/` 目录中约定的 pytest fixtures、何时使用它们以及如何运行测试。

主要 fixtures（在 `tests/conftest.py` 中定义）
---------------------------------

- `client`
  - 类型：session-scoped `fastapi.testclient.TestClient`
  - 用途：用于同步调用 FastAPI 应用的端点，适合渲染页面和表单提交的测试。
  - 示例：
    ```py
    def test_homepage(client):
        resp = client.get("/")
        assert resp.status_code == 200
    ```

- `override_user`
  - 类型：返回一个函数的 fixture
  - 用途：临时覆盖请求中的用户（代替真实认证），常用于 web 视图测试中。
  - 示例：
    ```py
    def test_dashboard(client, override_user):
        override_user({"email": "player@example.com", "user_type": "individual"})
        resp = client.get("/web/dashboard")
        assert resp.status_code == 200
    ```
  - 注意：测试结束时会自动恢复依赖覆盖，避免影响其它测试。

- `patch`（通用 patch）
  - 类型：`pytest` 的 `monkeypatch` 的别名
  - 用途：在测试中替换函数、模块或对象的属性。用于没有明显语义归属的打桩。
  - 示例：
    ```py
    def test_something(patch):
        patch.setattr(target_module, "fn", fake_fn)
    ```

- `patch_orchestrator`（语义化）
  - 类型：返回 `monkeypatch`
  - 用途：通常用于替换 `views._orchestrator` 或与 orchestrator 相关的全局对象。
  - 示例：
    ```py
    def test_logs(patch_orchestrator):
        patch_orchestrator.setattr(views, "_orchestrator", DummyOrchestrator())
    ```

- `patch_script_registry`（语义化）
  - 类型：返回 `monkeypatch`
  - 用途：替换 `econ_sim.script_engine.script_registry` 中的函数（例如 `list_scripts`、`list_all_scripts` 等）。
  - 示例：
    ```py
    def test_list(patch_script_registry):
        patch_script_registry.setattr(script_registry, "list_scripts", fake_list)
    ```

- `patch_views_orchestrator`（语义化）
  - 类型：返回 `monkeypatch`
  - 用途：强调替换目标是 `econ_sim.web.views` 模块中的对象（尤其是 `_orchestrator`）。
  - 示例：
    ```py
    def test_view_behavior(patch_views_orchestrator):
        patch_views_orchestrator.setattr(views, "_orchestrator", DummyOrchestrator())
    ```

何时使用语义化 fixtures vs 通用 `patch`
---------------------------------
- 若测试明显针对 orchestrator 或 script registry，请使用语义化 fixtures（`patch_orchestrator`, `patch_script_registry`, `patch_views_orchestrator`）以提升可读性。
- 若测试只是临时替换某个零散函数或模块，使用通用 `patch` 即可。

运行测试
-------
- 运行全部测试：

```bash
env PYTHONPATH=. pytest -q
```

- 运行单个测试文件：

```bash
pytest tests/test_web.py -q
```

- 运行单个测试用例：

```bash
pytest tests/test_seed.py::test_seed_test_world_can_execute_tick -q
```

注意事项
-----
- 避免在测试中全局修改 `app` 的状态而不恢复；优先使用 `override_user` 等已有 fixture。
- 如果要在多个测试间共享复杂的 fake 对象，考虑把该 fake 对象封装成一个 fixture。

如果你希望我把这份文档或 fixtures 进一步扩展为更详细的风格指南或 linter 校验规则，我可以继续实现并运行测试来验证更改。