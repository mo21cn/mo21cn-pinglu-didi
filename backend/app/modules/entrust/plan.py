"""运输计划（三段航段）与必需任务前置的**读模型**（BP-03 第 1 条 / 合同 §10.1 第 4 步）。

合同 §10.1 第 4 步的原文：

    Show the road–water–road plan and required task prerequisites.

开工前的反向审计（按技能 `entrust-backend-slice` §11.1 的纪律先查一遍）
--------------------------------------------------------------------
| 问题 | 结论 |
| --- | --- |
| 三段计划在库里有载体吗？ | **有**。`ent_leg`（最小结构化航段对象，`ent_commitment.py` id=1）已落，`seed_entrust_canonical.py` 铺了 3 段（厂区→南宁港→贵港港→卸货地） |
| 有读路径吗？ | **没有**。全仓 `ent_leg` 的 `SELECT` 只出现在 `contracts._list_legs`（**私有**，只为拼合同载荷）以及用例/种子里 ⇒ 界面上"三段计划"**无处可读** |
| 「必需任务前置」有载体吗？ | **齐备**。`ent_workflow_task.precondition_task_id` 可读可写（`tasks.set_precondition`），`GET /assignments/{id}/tasks` 与工作台 `plan_tasks` 槽位都已带上它 |

⇒ 本模块只补**缺的那一半**：航段的读模型，并把两者合成一份 `plan` 读模型。

⛔ 本模块**不发明口径**
------------------------
不判"是否必须三段"、不判"段顺序是否合法"、不判"谁可以建段" —— 那些属于**建段命令**，
而建段命令本轮**未做**（需口径裁定：谁能建 / 是否强制 公—水—公 / 改段是否留版本）。
演示脚本（§10.1 第 4 步）是 `Show ...`，计划来自**部署阶段的种子**；
未做的那半边如实登记在 `DEMO-1-interface-delta.md` §7.21，不在这里夹带。

「只搬运、不推导」
------------------
* `mode_label` 是一张**展示标签表**，不是判定：未登记的取值**原样回** `mode` 本身
  （界面上出现一个陌生的英文标识，比硬塞进"公路/内河"里好 —— 未知保持未知）。
* 模块**不产出**"三段""公路—内河—公路"这类**结论性文案**：段数是数据的属性，
  由界面按行渲染；在服务端把它拼成一句话，就多了一个会与数据脱节的落点
  （与 `contracts._assert_every_field_has_source` 防的是同类病）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.entrust import tasks as task_svc

#: 运输方式的**展示标签**（`ent_leg.mode` 的取值域由建段命令约束，本模块不校验）。
#: ⚠️ 这是**全仓唯一**一份：`contracts._MODE_LABELS` 曾自己写了一份（含 `rail`），
#: 两处各存一份的结果是"改一处、另一处静默留下旧口径" —— 已合并到这里。
#: `rail` 本轮没有数据产生它，但**保留**：它原本就在合同那份表里，
#: 删掉等于顺手改掉合同的既有行为（不属于本切片的目的）。
MODE_LABELS: dict[str, str] = {
    "road": "公路",
    "water": "内河",
    "rail": "铁路",
}

#: `plan` 读模型里回任务的**上限**。一张委托上的任务是"个位数"量级（七个槽位），
#: 显式给上限是为了让"截断"这件事可被看见 —— 不传 `size` 会静默落到
#: `tasks.list_tasks` 的默认值 20，而调用方从响应里看不出被截过。
TASK_LIMIT = 100


def mode_label(mode: str) -> str:
    """运输方式的展示标签；未登记的值**原样回**（不猜、不兜底成"公路"）。"""
    return MODE_LABELS.get(mode, mode)


def list_legs(session: Session, assignment_id: int) -> list[dict[str, Any]]:
    """按 `seq` 列航段。

    ⚠️ **空列表是合法的**：不是每张委托都有结构化计划（`ent_leg` 只被最小化落地，
    没有回溯填充的历史数据）。空列表与"计划读取失败"是两件事，前者回 `[]`、
    后者应当是异常 —— 不要在调用方用"空 ⇒ 报错"把两者混起来。
    """
    rows = (
        session.execute(
            text(
                "SELECT id, seq, mode, from_name, to_name FROM ent_leg "
                "WHERE assignment_id = :aid ORDER BY seq"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "leg_id": int(row["id"]),
            "seq": int(row["seq"]),
            "mode": str(row["mode"]),
            "mode_label": mode_label(str(row["mode"])),
            "from_name": str(row["from_name"]),
            "to_name": str(row["to_name"]),
        }
        for row in rows
    ]


def list_task_prerequisites(
    session: Session, assignment_id: int
) -> tuple[list[dict[str, Any]], int]:
    """该委托的必需任务与其**固定前置**。

    复用 `tasks.list_tasks` —— 任务是任务模块的事实，本模块**不重写那份 SQL**：
    两处各写一遍，改动只会落到其中一处（技能 §11.2「同一份数据有两个入口时，
    逐个入口比判据」讲的是同一条病）。

    字段面**照抄 `workbench._load_tasks` 的先例**（`task_type` / `title` / `status` /
    `precondition_task_id`），**不夹带** `required_evidence` 之类别处已有归属的事实：
    多带的每一个字段都会在界面之外多一个"会与任务页各自演化"的落点，
    而本读模型只回答 §10.1 第 4 步问的那件事 —— 计划**与前置**。
    任务页要"这个任务要求交什么"，走它自己的端点。

    ⚠️ **`total` 必须原样回传**（开放项 O-9，2026-09-18 裁定）：本函数此前写成
    `_total, items = …`，把 `list_tasks` 已经算出来的总数**丢掉**了 —— 于是
    "这张委托的任务超过 `TASK_LIMIT`"这件事在响应里**没有任何痕迹**，
    下游看到的是"前置解析不出来"，看起来像界面缺陷（2026-09-18 的 ⑯-e 就是这么被
    咬了一口的）。`TASK_LIMIT` 的注释早就写着"显式给上限是为了让截断可被看见"，
    但真正让它可被看见的是**这个回传的总数**，不是那个上限本身。
    """
    total, items = task_svc.list_tasks(
        session, assignment_id=assignment_id, page=1, size=TASK_LIMIT
    )
    rows = [
        {
            "task_id": int(item["task_id"]),
            "task_type": str(item["task_type"]),
            "title": str(item["title"]),
            "status": str(item["status"]),
            "precondition_task_id": item["precondition_task_id"],
        }
        for item in items
    ]
    return rows, total


def build_plan(session: Session, *, assignment: dict[str, Any]) -> dict[str, Any]:
    """§10.1 第 4 步的读模型：三段计划 + 必需任务前置。

    调用方负责可见性判定（端点层 `assert_can_view_assignment`）；本函数只组装。

    ⭐ 两个"截断事实"字段（O-9）：`task_prerequisites_total` 与
    `task_prerequisites_truncated`。**不把本读模型改成分页的** —— 分页是"列表"的语义，
    这里问的是"这张委托的必需任务与固定前置有哪些"，没有人会翻到第 2 页去读计划；
    给它分页只是把"我这个消费者有没有取全"的责任推给每一个下游。
    反过来，把"被截了"当**事实**由服务端给出（六机制：事实有来源），
    下游无论怎么消费都能据此判断，不必各自猜。
    `truncated` 由"读到的行数 < 总数"推导，**不引入第二个阈值** ——
    再多一个阈值就会与 `TASK_LIMIT` 各自演化（本模块 `MODE_LABELS` 的注释里
    记着"两处各存一份口径"的代价）。
    """
    assignment_id = int(assignment["id"])
    tasks, tasks_total = list_task_prerequisites(session, assignment_id)
    return {
        "assignment_id": assignment_id,
        "legs": list_legs(session, assignment_id),
        "task_prerequisites": tasks,
        "task_prerequisites_total": tasks_total,
        "task_prerequisites_truncated": len(tasks) < tasks_total,
    }


__all__ = [
    "MODE_LABELS",
    "TASK_LIMIT",
    "build_plan",
    "list_legs",
    "list_task_prerequisites",
    "mode_label",
]
