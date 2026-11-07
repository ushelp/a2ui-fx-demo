"""用户模块路由 —— 提供用户相关的 API（当前为 mock 数据）。"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class User(BaseModel):
    """用户响应模型。"""

    id: int
    name: str
    email: str


# mock 数据，后续可替换为数据库查询
_MOCK_USERS = [
    User(id=1, name="张三", email="zhangsan@example.com"),
    User(id=2, name="李四", email="lisi@example.com"),
    User(id=3, name="王五", email="wangwu@example.com"),
]


@router.get("/users", response_model=list[User], summary="获取用户列表")
async def list_users() -> list[User]:
    """返回所有用户（mock 数据）。"""
    return _MOCK_USERS
