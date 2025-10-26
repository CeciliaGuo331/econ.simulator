
# 用户脚本文档索引

本目录包含为玩家编写脚本所需的 API 文档，按主体类型拆分：

- `user_script_api_household.md` — 家户脚本指南（消费/劳动/教育，含示例与 LLM 使用）
- `user_script_api_firm.md` — 企业脚本指南（定价、生产、招聘）
- `user_script_api_bank.md` — 商业银行脚本指南（利率、贷款）
- `user_script_api_government.md` — 政府脚本指南（税收、岗位、发行计划）
- `user_script_api_central_bank.md` — 央行脚本指南（政策利率、OMO）

快速上手建议：

1. 先阅读与你身份对应的文档（例如你要写 household 脚本就读 `user_script_api_household.md`）。
2. 在本地或测试仿真中先用小型 `context` 调试 `generate_decisions(context)` 的返回值结构（参考各文档中的示例与 `dry_run_generate` 模板）。
3. 关于 LLM 调用：平台在脚本沙箱内对 LLM 的支持有统一约定——脚本内仅应使用下面这行创建 LLM 会话：

	from econ_sim.utils.llm_session import create_llm_session_from_env

   然后使用返回的 `session`（例如 `session.generate(prompt, ...)`）进行调用。所有示例文档均采用这一方式。

4. 对 LLM 返回做严格解析、类型与范围校验。若解析失败、超时或抛异常，应使用稳健回退值或直接返回 `None` 以触发 baseline 策略。

5. 本地测试与 CI：在提交脚本前，使用 `dry_run_generate` 或项目内 tests（参见 `tests/`）对脚本做快速验证。

更多示例、测试模板与平台端点说明请参阅各主体文档。

