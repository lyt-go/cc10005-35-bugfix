"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

并发口径：所有跨模块的生命周期联动（例如面板归档同步更新限速规则）都必须在
``store.transaction()`` 内完成，配合可重入锁保证「同一提交」语义——要么全部生效，
要么全部回滚；并发请求只允许一个提交成功。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """恢复种子数据；仅用于测试隔离，业务代码不要调用。"""
        with self._lock:
            self._tables = {
                name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
            }

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """单进程事务：提交前对全部表做快照，异常时整体回滚。

        锁是可重入的，service 层已持锁时联动调用其它 service 不会自死锁。
        """
        with self._lock:
            snapshot = {name: [dict(row) for row in rows] for name, rows in self._tables.items()}
            try:
                yield
            except Exception:
                self._tables = snapshot
                raise

    def module_names(self) -> list[str]:
        with self._lock:
            return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        # 注意：调用方在事务内拿到的是活动列表引用，事务回滚会整体替换表对象，
        # 因此长生命周期的引用不应跨事务持有。
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_code(self, module: str, code_field: str, code: str) -> dict[str, Any] | None:
        for row in self.rows(module):
            if str(row.get(code_field) or "") == code:
                return row
        return None

    def lock(self) -> threading.RLock:
        """暴露锁给 service 层做读-判-写的原子区间。"""
        return self._lock

    # 内部表（如不可变决策历史）不参与运营概览的业务模块统计
    INTERNAL_TABLES = frozenset({"throttle_decision"})

    def overview(self) -> dict[str, object]:
        with self._lock:
            modules: list[dict[str, object]] = []
            for name in self.module_names():
                if name in self.INTERNAL_TABLES:
                    continue
                rows = self.rows(name)
                modules.append({
                    "name": name,
                    "created": len(rows),
                    "pending": sum(1 for row in rows if row.get("pending")),
                    "abnormal": sum(1 for row in rows if row.get("abnormal")),
                })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
