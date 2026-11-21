"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

质检批次的动作接口（登记、提交、调度、补检、报告）比通用状态流转复杂，
额外挂一个专用路由；它必须先于通用资源路由注册，
否则 /api/quality_batch/register 会被 /api/{module}/{entry_id} 抢先匹配。
"""
from __future__ import annotations

from app.routers import quality_batch, resource

ROUTERS = [quality_batch, resource]
