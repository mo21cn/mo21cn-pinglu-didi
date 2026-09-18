"""行锁的**方言守卫**与"锁住委托行"这一件事的唯一定义。

为什么要单独一个模块
--------------------
`SELECT ... FOR UPDATE` 在 **SQLite 上是语法错**（开发/测试库正是 SQLite），
在 **MySQL 上**却是"评估与迁移原子"这类要求的落点。两处各写一遍判断，
迟早出现"一边锁了、一边没锁" —— 而那种差异**只在真实并发下才看得见**，
静态检查与单线程用例都抓不到。

⚠️ SQLite 走的是**降级路径**（整库一把写锁、单写者），所以它的绿灯**不是**
并发正确性的证据；证据在 CI 的 `pytest -m mysql`（合同 §6.4：
*An exception query existing in the code is not this proof*）。

锁顺序（**全局约定，别改**）
---------------------------
凡是需要"先看委托状态、再写与它相关的行"的命令，都**先锁 `ent_assignment` 行**。
目前两个调用方是 `closure.complete_assignment`（结案）与 `exceptions.raise_case`
（登记案件）—— 它们正是合同点名的 close-versus-new-blocking-case 竞态两侧。
顺序一致 ⇒ 不会互相死锁；任一方先拿到锁 ⇒ 另一方的状态复核一定看得见它的结果。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def for_update_clause(session: Session) -> str:
    """`FOR UPDATE` 子句 —— **只在支持它的方言上加**。

    ⛔ 不写成"try 一下再回退"：那种写法会让 SQLite 上静默走另一条路径，
    而失败原因永远看不到。
    """
    bind = session.get_bind()
    return " FOR UPDATE" if bind is not None and bind.dialect.name == "mysql" else ""


def lock_assignment_row(session: Session, *, assignment_id: int) -> None:
    """取这张委托的**行锁**（MySQL 生效；SQLite 上是空操作）。

    调用方拿到锁之后**必须重读**状态：锁之前读到的可能是旧快照
    （MySQL 默认 REPEATABLE READ 下，普通 `SELECT` 返回事务开始时的版本）。
    """
    session.execute(
        text("SELECT id FROM ent_assignment WHERE id = :aid" + for_update_clause(session)),
        {"aid": assignment_id},
    ).first()


__all__ = [
    "for_update_clause",
    "lock_assignment_row",
]
