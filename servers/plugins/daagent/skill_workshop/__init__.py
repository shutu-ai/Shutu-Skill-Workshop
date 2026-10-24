"""功能等价的 DAAgent 技能工坊核心实现。

本包只负责本地技能包的快照、提案、审核、应用、版本和回滚；
远端 Skill Hub 与宿主 Agent 审批由上层自行接入。
"""

from .engine import SkillWorkshop, SkillWorkshopError, StaleSkillError
from .models import EvolutionIssue, Patch, Proposal, SkillSnapshot, Version

__all__ = [
    "EvolutionIssue",
    "Patch",
    "Proposal",
    "SkillSnapshot",
    "SkillWorkshop",
    "SkillWorkshopError",
    "StaleSkillError",
    "Version",
]
