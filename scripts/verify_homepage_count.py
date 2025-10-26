"""验证首页显示的数字来源"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from econ_sim.script_engine import script_registry
from econ_sim.core.orchestrator import SimulationOrchestrator


async def verify_counts(sim_id: str = "test_world"):
    print(f"🔍 验证仿真实例 {sim_id} 的统计数据\n")
    
    # 1. 脚本注册表统计（模拟首页逻辑）
    scripts = await script_registry.list_scripts(sim_id)
    
    household_scripts = [
        s for s in scripts
        if s.agent_kind and s.agent_kind.value == "household"
    ]
    
    owners = {s.user_id.lower() for s in household_scripts}
    
    print("📋 脚本注册表统计:")
    print(f"   总脚本数: {len(household_scripts)}")
    print(f"   不同用户数: {len(owners)}")
    
    # 检查是否有 baseline
    baseline_users = [u for u in owners if "baseline" in u]
    print(f"   包含 baseline 用户: {baseline_users}")
    
    # 2. 世界状态统计
    orch = SimulationOrchestrator()
    state = await orch.get_state(sim_id)
    
    print(f"\n🌍 世界状态统计:")
    print(f"   家户实体数: {len(state.households)}")
    print(f"   是否包含 900000: {'900000' in state.households}")
    
    # 3. 对比
    print(f"\n📊 对比:")
    print(f"   首页应该显示的数字（如果统计用户数）: {len(owners)}")
    print(f"   首页应该显示的数字（如果统计实体数）: {len(state.households)}")


if __name__ == "__main__":
    asyncio.run(verify_counts())