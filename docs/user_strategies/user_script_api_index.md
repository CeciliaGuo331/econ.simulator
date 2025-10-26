# 用户脚本文档索引

本目录包含为玩家编写脚本所需的 API 文档，按主体类型拆分：

- `user_script_api_household.md` — 家户脚本指南（消费/劳动/教育，含示例与 LLM 使用）
- `user_script_api_firm.md` — 企业脚本指南（定价、生产、招聘）
- `user_script_api_bank.md` — 商业银行脚本指南（利率、贷款）
- `user_script_api_government.md` — 政府脚本指南（税收、岗位、发行计划）
- `user_script_api_central_bank.md` — 央行脚本指南（政策利率、OMO）

使用建议：
1. 先阅读与你身份对应的文档（例如你要写 household 脚本就读 household 文档）；
2. 在本地或测试仿真中先用小型 `context` 调试 `generate_decisions(context)` 的返回值结构；
3. 若需要 LLM，优先使用 `/llm/completions` HTTP 接口或在管理员已开启注入时使用注入的 `llm` 对象（请参阅各文档中的 LLM 小节）。

