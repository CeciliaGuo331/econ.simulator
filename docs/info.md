先梳理脚本上下文里的世界状态，再对照前端 `role_state` 的构造逻辑，最后总结两者的重合范围与差异点。

## 对比结果
- **脚本侧数据来源**：`ScriptRegistry._execute_script`（registry.py 约第 728 行）把当期 `WorldState` 的 `model_dump(mode="json")` 以及 config 传入沙箱，脚本从 `context["world_state"]` 读取全部主体字段（含 households、firm、bank 等）并可自行聚合。
- **面板侧数据来源**：views.py 中 `_extract_view_data`（约第 280 行起）从同一份 `world_state` 抽取字段，组装成 `role_state.macro_rows` / `market_rows` 等表格；模板 dashboard.html 直接渲染这些行。

因此，前端展示值全部来自脚本同权能读取的字段，只是为了隐私和可读性在界面上做了聚合或重命名：

| 角色 | 面板展示字段 | 脚本可读取的原字段 |
| --- | --- | --- |
| 家户 | 宏观指标、利率税率、家户均值（数量/平均现金/就业率等） | `world_state["macro"]`, `world_state["firm"]["price"]`, `world_state["bank"]["deposit_rate"]`, 以及 `world_state["households"]`（脚本可自行按 ID 取本户信息，再计算均值） |
| 企业 | 价格、产出、工资、库存、雇员数、最近销售，劳动力市场与金融环境指标 | `world_state["firm"][...]`, `world_state["macro"]["unemployment_rate"]`, `world_state["government"]["wage_offer"]`, `world_state["bank"]["loan_rate"]` 等 |
| 政府 | 税率、支出、失业补贴、资产负债、劳动力/政策信息 | `world_state["government"][...]`, `world_state["macro"]`, `world_state["central_bank"]`, `world_state["bank"]["loan_rate"]` |
| 商业银行 | 存款/现金/贷款/库存资产、利率、政策率 | `world_state["bank"][...]`, `world_state["central_bank"][...]` |
| 央行 | 基准利率、准备金率、通胀/失业目标、银行贷款规模/现金 | `world_state["central_bank"][...]`, `_as_dict(world.get("bank").get("balance_sheet"))` |
| 管理员面板 | 额外展示全部主体明细、功能开关、家户样本 | 属于管理员特权，可直接查看完整 `world_state`；脚本无须依赖 |

## 差异与注意
- **聚合 vs 原始**：界面中的均值、就业率等由 `_summarize_households_data` 现算，不会暴露单个家户；脚本仍掌握原始字典（可按需聚合），因此不存在“界面显示但脚本拿不到”的字段。
- **脚本额外数据**：脚本还能读取 `context["config"]`（面板未展示）。若需要把配置也在 UI 体现，可单独补充。
- **管理员视角**：管理员面板故意展示完整表格，超出了普通脚本权限，这是为了运营监控，和用户脚本所需数据范围无冲突。

结论：普通角色的网页面板展示值完全源自脚本可访问的 `world_state` 字段，没有前端独占或脚本缺失的数据；差别仅在于 UI 做了聚合与命名优化。