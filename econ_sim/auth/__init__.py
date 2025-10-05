"""用户认证相关功能入口。"""

from .user_manager import InMemoryUserStore, UserManager

user_manager = UserManager(InMemoryUserStore())

__all__ = ["user_manager", "UserManager"]
