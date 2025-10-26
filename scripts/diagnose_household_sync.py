"""
诊断脚本：检查脚本注册表与世界状态中家户数据的同步性

此脚本用于检测和分析 Redis/Postgres 数据不一致问题，特别关注：
1. 挂载家户脚本数（来自 PostgreSQL scripts 表）
2. 家户实体数量（来自 Redis/PostgreSQL world_state）
3. 两者之间的差异与不一致项

使用方法：
    python -m scripts.diagnose_household_sync [simulation_id]
    python -m scripts.diagnose_household_sync --all  # 检查所有仿真实例
"""

import sys
import os
import asyncio
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime
import json

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.script_engine import script_registry
from econ_sim.data_access.models import WorldState


class HouseholdSyncDiagnostics:
    """家户数据同步诊断工具"""
    
    def __init__(self):
        self.orchestrator = SimulationOrchestrator()
        self.results = []
    
    async def diagnose_simulation(self, sim_id: str) -> Dict:
        """诊断单个仿真实例的数据一致性"""
        print(f"\n{'='*80}")
        print(f"诊断仿真实例: {sim_id}")
        print(f"{'='*80}\n")
        
        result = {
            "simulation_id": sim_id,
            "timestamp": datetime.now().isoformat(),
            "status": "unknown",
            "errors": [],
            "warnings": [],
            "details": {}
        }
        
        try:
            # 1. 从 ScriptRegistry 获取挂载的家户脚本信息
            print("📋 步骤 1: 从脚本注册表获取数据...")
            scripts_data = await self._get_scripts_data(sim_id)
            result["details"]["scripts"] = scripts_data
            
            # 2. 从 WorldState 获取家户实体信息
            print("🌍 步骤 2: 从世界状态获取数据...")
            world_data = await self._get_world_data(sim_id)
            result["details"]["world_state"] = world_data
            
            # 3. 对比分析
            print("🔍 步骤 3: 对比分析数据差异...")
            comparison = self._compare_data(scripts_data, world_data)
            result["details"]["comparison"] = comparison
            
            # 4. 生成诊断报告
            self._generate_report(sim_id, scripts_data, world_data, comparison)
            
            # 5. 判断状态
            if comparison["is_consistent"]:
                result["status"] = "✅ 一致"
                print(f"\n✅ 结论: 数据一致")
            else:
                result["status"] = "❌ 不一致"
                result["errors"].append(f"发现 {len(comparison['inconsistencies'])} 个不一致项")
                print(f"\n❌ 结论: 发现数据不一致")
            
        except Exception as e:
            result["status"] = "❌ 错误"
            result["errors"].append(str(e))
            print(f"\n❌ 诊断时发生错误: {e}")
            import traceback
            traceback.print_exc()
        
        self.results.append(result)
        return result
    
    async def _get_scripts_data(self, sim_id: str) -> Dict:
        """获取脚本注册表中的家户脚本数据"""
        try:
            scripts = await script_registry.list_scripts(sim_id)
            
            # 过滤出家户类型的脚本
            household_scripts = [
                s for s in scripts 
                if s.agent_kind and s.agent_kind.value == 'household'
            ]
            
            # 统计不同维度
            owners = {s.user_id for s in household_scripts}
            entity_ids = {s.entity_id for s in household_scripts if s.entity_id}
            
            # 构建详细信息
            scripts_detail = []
            for script in household_scripts:
                scripts_detail.append({
                    "script_id": script.script_id,
                    "user_id": script.user_id,
                    "entity_id": script.entity_id,
                    "created_at": script.created_at.isoformat() if script.created_at else None
                })
            
            return {
                "total_scripts": len(household_scripts),
                "unique_owners": len(owners),
                "unique_entity_ids": len(entity_ids),
                "owners_list": sorted(list(owners)),
                "entity_ids_list": sorted(list(entity_ids)),
                "scripts_detail": scripts_detail
            }
        except Exception as e:
            return {
                "error": str(e),
                "total_scripts": 0,
                "unique_owners": 0,
                "unique_entity_ids": 0
            }
    
    async def _get_world_data(self, sim_id: str) -> Dict:
        """获取世界状态中的家户实体数据"""
        try:
            # 尝试获取世界状态
            world_state = await self.orchestrator.get_state(sim_id)
            
            if not world_state:
                return {
                    "error": "世界状态不存在或为空",
                    "total_households": 0,
                    "household_ids": []
                }
            
            # 提取家户信息
            households = world_state.households or {}
            household_ids = sorted(list(households.keys()))
            
            # 构建详细信息
            households_detail = []
            for hh_id, hh_state in households.items():
                households_detail.append({
                    "household_id": hh_id,
                    "cash": hh_state.balance_sheet.cash,
                    "deposits": hh_state.balance_sheet.deposits,
                    "employment_status": hh_state.employment_status.value if hh_state.employment_status else None,
                    "employer_id": hh_state.employer_id
                })
            
            return {
                "total_households": len(households),
                "household_ids": household_ids,
                "households_detail": households_detail,
                "tick": world_state.tick,
                "day": world_state.day
            }
        except Exception as e:
            return {
                "error": str(e),
                "total_households": 0,
                "household_ids": []
            }
    
    def _compare_data(self, scripts_data: Dict, world_data: Dict) -> Dict:
        """对比脚本数据和世界状态数据"""
        comparison = {
            "is_consistent": True,
            "inconsistencies": [],
            "summary": {}
        }
        
        # 检查是否有错误
        if "error" in scripts_data or "error" in world_data:
            comparison["is_consistent"] = False
            if "error" in scripts_data:
                comparison["inconsistencies"].append({
                    "type": "数据源错误",
                    "source": "脚本注册表",
                    "message": scripts_data["error"]
                })
            if "error" in world_data:
                comparison["inconsistencies"].append({
                    "type": "数据源错误",
                    "source": "世界状态",
                    "message": world_data["error"]
                })
            return comparison
        
        # 数量对比
        scripts_count = scripts_data.get("unique_entity_ids", 0)
        world_count = world_data.get("total_households", 0)
        
        comparison["summary"]["挂载脚本数（不同entity_id）"] = scripts_count
        comparison["summary"]["世界状态中家户数"] = world_count
        comparison["summary"]["差值"] = scripts_count - world_count
        
        if scripts_count != world_count:
            comparison["is_consistent"] = False
            comparison["inconsistencies"].append({
                "type": "数量不匹配",
                "scripts_count": scripts_count,
                "world_count": world_count,
                "difference": scripts_count - world_count
            })
        
        # ID 集合对比
        script_entity_ids_raw = scripts_data.get("entity_ids_list", [])
        world_household_ids_raw = world_data.get("household_ids", [])
        
        # 统一转换为字符串进行对比
        script_entity_ids = {str(id) for id in script_entity_ids_raw if id is not None}
        world_household_ids = {str(id) for id in world_household_ids_raw if id is not None}
        
        # 找出只在脚本中存在的 ID（悬空脚本）
        orphaned_scripts = script_entity_ids - world_household_ids
        if orphaned_scripts:
            comparison["is_consistent"] = False
            comparison["inconsistencies"].append({
                "type": "悬空脚本",
                "message": "这些entity_id有脚本但在世界状态中不存在",
                "entity_ids": sorted(list(orphaned_scripts)),
                "count": len(orphaned_scripts)
            })
        
        # 找出只在世界状态中存在的 ID（缺失脚本）
        missing_scripts = world_household_ids - script_entity_ids
        if missing_scripts:
            comparison["is_consistent"] = False
            comparison["inconsistencies"].append({
                "type": "缺失脚本",
                "message": "这些家户实体存在但没有挂载脚本",
                "household_ids": sorted(list(missing_scripts)),
                "count": len(missing_scripts)
            })
        
        return comparison
    
    def _generate_report(self, sim_id: str, scripts_data: Dict, 
                        world_data: Dict, comparison: Dict):
        """生成详细的诊断报告"""
        print(f"\n📊 详细报告")
        print(f"{'-'*80}")
        
        # 脚本注册表统计
        print(f"\n📋 脚本注册表 (PostgreSQL scripts 表):")
        print(f"   总脚本数: {scripts_data.get('total_scripts', 0)}")
        print(f"   不同用户数 (owners): {scripts_data.get('unique_owners', 0)}")
        print(f"   不同实体ID数 (entity_ids): {scripts_data.get('unique_entity_ids', 0)}")
        
        if scripts_data.get('owners_list'):
            print(f"   用户列表: {', '.join(scripts_data['owners_list'][:5])}" + 
                  (f" ... (共{len(scripts_data['owners_list'])}个)" if len(scripts_data['owners_list']) > 5 else ""))
        
        # 世界状态统计
        print(f"\n🌍 世界状态 (Redis/PostgreSQL world_state):")
        print(f"   家户实体数: {world_data.get('total_households', 0)}")
        print(f"   当前 Tick: {world_data.get('tick', 'N/A')}")
        print(f"   当前 Day: {world_data.get('day', 'N/A')}")
        
        # 对比结果
        print(f"\n🔍 对比分析:")
        for key, value in comparison["summary"].items():
            print(f"   {key}: {value}")
        
        # 不一致项详情
        if comparison["inconsistencies"]:
            print(f"\n⚠️  发现 {len(comparison['inconsistencies'])} 个不一致项:")
            for i, issue in enumerate(comparison["inconsistencies"], 1):
                print(f"\n   [{i}] {issue['type']}")
                if issue['type'] == "数量不匹配":
                    print(f"       脚本数: {issue['scripts_count']}")
                    print(f"       家户数: {issue['world_count']}")
                    print(f"       差值: {issue['difference']}")
                elif issue['type'] in ["悬空脚本", "缺失脚本"]:
                    print(f"       {issue['message']}")
                    print(f"       数量: {issue['count']}")
                    id_key = 'entity_ids' if 'entity_ids' in issue else 'household_ids'
                    ids = issue[id_key]
                    if len(ids) <= 10:
                        print(f"       ID列表: {ids}")
                    else:
                        print(f"       ID列表: {ids[:10]} ... (共{len(ids)}个)")
                elif issue['type'] == "数据源错误":
                    print(f"       来源: {issue['source']}")
                    print(f"       错误: {issue['message']}")
        else:
            print(f"   ✅ 未发现不一致")
    
    async def diagnose_all_simulations(self) -> List[Dict]:
        """诊断所有仿真实例"""
        print(f"\n{'='*80}")
        print(f"开始诊断所有仿真实例")
        print(f"{'='*80}")
        
        # 获取所有仿真实例ID
        try:
            # 通过查询 PostgreSQL scripts 表获取所有 simulation_id
            scripts = await script_registry.list_scripts(None)
            sim_ids = {s.simulation_id for s in scripts if s.simulation_id}
            
            if not sim_ids:
                print("\n⚠️  未找到任何仿真实例")
                return []
            
            print(f"\n发现 {len(sim_ids)} 个仿真实例: {', '.join(sorted(sim_ids))}\n")
            
            # 逐个诊断
            for sim_id in sorted(sim_ids):
                await self.diagnose_simulation(sim_id)
            
            return self.results
            
        except Exception as e:
            print(f"\n❌ 获取仿真实例列表时发生错误: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def save_results(self, filename: str = "household_sync_diagnostics.json"):
        """保存诊断结果到文件"""
        filepath = os.path.join(os.path.dirname(__file__), "..", filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        print(f"\n💾 诊断结果已保存到: {filepath}")


async def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="诊断脚本注册表与世界状态之间的家户数据同步性"
    )
    parser.add_argument(
        'simulation_id',
        nargs='?',
        default=None,
        help='要诊断的仿真实例ID（不指定则诊断所有实例）'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='诊断所有仿真实例'
    )
    parser.add_argument(
        '--save',
        action='store_true',
        help='将结果保存到JSON文件'
    )
    
    args = parser.parse_args()
    
    diagnostics = HouseholdSyncDiagnostics()
    
    if args.all or args.simulation_id is None:
        # 诊断所有实例
        await diagnostics.diagnose_all_simulations()
    else:
        # 诊断指定实例
        await diagnostics.diagnose_simulation(args.simulation_id)
    
    # 总结
    print(f"\n{'='*80}")
    print(f"诊断总结")
    print(f"{'='*80}")
    
    total = len(diagnostics.results)
    consistent = sum(1 for r in diagnostics.results if r["status"] == "✅ 一致")
    inconsistent = sum(1 for r in diagnostics.results if r["status"] == "❌ 不一致")
    errors = sum(1 for r in diagnostics.results if r["status"] == "❌ 错误")
    
    print(f"\n总计诊断: {total} 个仿真实例")
    print(f"  ✅ 数据一致: {consistent}")
    print(f"  ❌ 数据不一致: {inconsistent}")
    print(f"  ❌ 诊断错误: {errors}")
    
    if args.save:
        diagnostics.save_results()


if __name__ == "__main__":
    asyncio.run(main())