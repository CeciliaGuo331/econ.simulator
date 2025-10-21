"""轻量压力测试：反复提交短任务到 sandbox 的进程池，触发 worker 达到 WORKER_MAX_TASKS，从而验证 _ensure_pool_health/_recreate_process_pool 的行为。

用法：在开发环境中运行：
    conda activate econsim
    python scripts/_test_pool_recreate.py

注意：脚本将临时把环境变量 WORKER_MAX_TASKS 设得很低以便快速触发 worker 退出。
"""

import os
import time
import logging
from concurrent.futures import as_completed

# 确保我们使用较小的阈值以快速触发回收
os.environ["ECON_SIM_WORKER_MAX_TASKS"] = "3"

# 调整模块级常量（sandbox 会在导入时读取 env 到 WORKER_MAX_TASKS，
# 但为了保证我们可以让新的 setting 生效，我们可以在导入后直接覆盖）

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def main() -> None:
    # 导入 sandbox
    from econ_sim.script_engine import sandbox

    # 尝试覆盖模块内常量（如果存在）
    try:
        sandbox.WORKER_MAX_TASKS = 3
        logger.info("set sandbox.WORKER_MAX_TASKS=3")
    except Exception:
        logger.exception("failed to set WORKER_MAX_TASKS")

    # 简单脚本：让 worker 执行一个短 sleep，然后返回
    # 注意：sandbox 的 _pool_worker 期望脚本定义 generate_decisions(context)
    CODE = (
        "def generate_decisions(context):\n"
        "    import time\n"
        "    time.sleep(0.1)\n"
        "    return {'ok': context.get('i')}\n"
    )

    SUBMISSIONS = 50

    results = []

    start = time.time()
    print("starting submissions: ", SUBMISSIONS)
    logger.info("test_pid=%s", os.getpid())

    for i in range(SUBMISSIONS):
        try:
            res = sandbox.execute_script(
                CODE, {"i": i}, timeout=2.0, script_id=f"test-{i}"
            )
            results.append((i, True, res))
            if i % 10 == 0:
                logger.info(
                    "submission progress: %s/%s (pid=%s)", i, SUBMISSIONS, os.getpid()
                )
            else:
                logger.debug("submission %s ok", i)
        except Exception as e:
            results.append((i, False, repr(e)))
            logger.exception("submission %s failed", i)
        time.sleep(0.02)

    end = time.time()

    success = sum(1 for r in results if r[1])
    print(f"done: submitted={SUBMISSIONS} success={success} elapsed={end-start:.2f}s")

    # 打印部分失败的例子
    fails = [r for r in results if not r[1]]
    if fails:
        print(f"failures: {len(fails)} examples:")
        for f in fails[:10]:
            print(f)
    else:
        print("no failures observed")


if __name__ == "__main__":
    main()
