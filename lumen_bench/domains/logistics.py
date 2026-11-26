"""晦湾速运：派送、轨迹和账单分别建表，不同团队保留了旧口径。"""

from datetime import date

from ..cases import metric, modeling_cases, month, negatives, quarter, ratio, singles, state, tally
from ..model import Defect, Domain, amount, count, entity, process
from ..oracle import case, observe, review, turn

ENTITIES = (
    entity(
        "service", "产品时效", "城际次日|电商标准|同城急送|经济隔日|仓配定时|重货到家", "时效件|经济件", history=True
    ),
    entity(
        "client", "寄件客户", "松园书业|吟桥食品|东苇日用|禾田器械|本末文创|墨港灯饰|树谷商贸|小渡零售", "企业|小商"
    ),
    entity(
        "hub",
        "分拨站",
        "黄桥中心|长港中心|星东中心|南河中心|北道站|谷湾仓|滨桥站|双林站",
        "干线枢纽|末端节点",
        "site",
        history=True,
    ),
    entity(
        "route",
        "配送线路",
        "滨河线|中环线|科创线|明港线|临山线|西原线|新城线|东园线",
        "干线|末端",
        "hub",
        history=True,
    ),
    entity("fleet", "运输工具", "轻型厢车|冷链厢车|高栏车|新能源小车|同城两轮|重型拖车", "自有|合作"),
    entity("courier", "派送员匿名编号", "员甲|员乙|员丙|员丁|员戊|员己|员庚|员辛", "自营|外包", "hub"),
    entity("package_type", "包裹类型", "文件|普货|温控|易碎|超长|重件", "普通|特别处理"),
    entity("partner", "合作方", "岩湾车队|喜林快运|朝北配送|花岛集运|渡河驿站", "干线|末端"),
    entity("reason", "异常归类", "无人收件|地址待核|运力不足|天气|包装破损|班次缺失", "客户侧|承运侧|不可控"),
    entity("locker", "末端收件点", "翠园自提|晨星驿站|淀口柜|佳禾柜|明水站|秋浦自提", "柜|驿站", "hub"),
    entity("district", "配送区", "城中|城西|高新|机场|江南|老城|东郊|远郊", "核心|外围", "site"),
    entity("shift", "运力班次", "昼班|晚班|跨夜|加班", "常规|临时"),
    entity("fee_rule", "计费方案", "标准价|协议价|阶梯价|季节附加|临时改价", "合同|临时"),
    entity("warehouse", "仓内交接点", "收运门|查验区|落地仓|临时暂存|不合格隔离|装笼区", "入库|出库", "hub"),
)

WEIGHT = count("weight_gram", "包裹重量克", 40, 18000)
MINUTES = count("work_min", "作业分钟", 3, 240)


def delivery_sla(row, parent, rng):
    elapsed_days = (date.fromisoformat(row["business_day"]) - date.fromisoformat(parent["business_day"])).days
    row["actual_hours"] = max(1, 24 * elapsed_days - rng.randrange(12))
    row["target_hours"] = {1: 36, 2: 72, 3: 24, 4: 96, 5: 48, 6: 120}[row["service_id"]]
    if row["state"] in {"准点签收", "逾期签收"}:
        row["state"] = "准点签收" if row["actual_hours"] <= row["target_hours"] else "逾期签收"


SLA = (count("actual_hours", "实际耗时"), count("target_hours", "承诺时效"))
SIGNED = ("state", "in", ("准点签收", "逾期签收"))
PROCESSES = (
    process(
        "tariff_quote",
        "运费报价",
        "client service fee_rule",
        [amount("quote_yuan", "运费报价")],
        "已报价|无效|失效",
        "billing",
    ),
    process(
        "pickup_request",
        "揽件预约",
        "client service district",
        [count("planned_parcels", "计划件数")],
        "已预约|取消|改约",
        "ops",
    ),
    process(
        "parcel",
        "运单",
        "client service district package_type route partner",
        [WEIGHT, amount("declared_value", "申报价值")],
        "已揽件|已取消|仅录单",
        "ops",
        rows=620,
        scope="service",
        note="一个运单 ID 对应一票。",
    ),
    process(
        "pickup_scan",
        "取件扫描",
        "client service district courier",
        [MINUTES],
        "有效|重扫|取消",
        "tracking",
        rows=420,
        after="parcel",
        parent_states="已揽件",
    ),
    process(
        "route_assignment",
        "路由分拨",
        "service route hub partner",
        [count("planned_km", "计划公里")],
        "已分派|待分派|替换",
        "dispatch",
        after="pickup_scan",
    ),
    process(
        "hub_inbound",
        "分拨入港",
        "hub route warehouse shift",
        [MINUTES, WEIGHT],
        "已入港|漏扫|撤回",
        "tracking",
        after="route_assignment",
    ),
    process(
        "sort_scan",
        "分拣轨迹",
        "hub warehouse route shift",
        [MINUTES],
        "正常|误分|串件",
        "tracking",
        after="hub_inbound",
    ),
    process(
        "linehaul_dispatch",
        "干线发车",
        "hub route fleet partner shift",
        [count("loaded_parcels", "装载件数")],
        "发车|甩货|取消",
        "dispatch",
        after="sort_scan",
    ),
    process(
        "linehaul_arrive",
        "干线到车",
        "hub route fleet partner",
        [MINUTES],
        "到车|折返|漏扫",
        "tracking",
        after="linehaul_dispatch",
    ),
    process(
        "hub_outbound",
        "分拨出港",
        "hub route shift warehouse",
        [WEIGHT],
        "已出港|滞留|撤回",
        "tracking",
        after="linehaul_arrive",
    ),
    process(
        "locker_drop",
        "驿站入柜",
        "locker hub district courier",
        [MINUTES],
        "在柜|取出|柜满",
        "tracking",
        after="hub_outbound",
    ),
    process(
        "delivery_attempt",
        "末端投递尝试",
        "service route district courier reason",
        SLA,
        "准点签收|逾期签收|联系失败|拒收",
        "delivery",
        rows=820,
        after="parcel",
        parent_states="已揽件",
        enrich=delivery_sla,
        scope="route",
        note="一次派送尝试一行，同一票可多次出现。",
    ),
    process(
        "delivery_confirmation",
        "签收确认",
        "service route district courier partner",
        SLA,
        "准点签收|逾期签收|拒收",
        "ops",
        rows=380,
        after="parcel",
        parent_states="已揽件",
        enrich=delivery_sla,
        scope="service",
        note="每票至多一次签收确认，拒收单独统计。",
    ),
    process(
        "proof_delivery",
        "签收凭证核验",
        "service route courier locker",
        [count("verified_fields", "核验项数", 1, 12)],
        "完成|凭证缺失|待验",
        "delivery",
        after="delivery_confirmation",
        parent_states="准点签收|逾期签收",
    ),
    process(
        "reschedule",
        "客户改约",
        "service district courier reason",
        [count("moved_hours", "顺延小时")],
        "改约成功|撤销|待沟通",
        "delivery",
        after="delivery_attempt",
    ),
    process(
        "return_to_sender",
        "退回寄件方",
        "client service reason route",
        [WEIGHT],
        "已退回|途中|等待",
        "ops",
        after="delivery_attempt",
        parent_states="拒收|联系失败",
    ),
    process(
        "exception_signal",
        "异常签标",
        "service route hub reason",
        [MINUTES],
        "已登记|误删补记|误报",
        "tracking",
        rows=250,
    ),
    process(
        "damage_record",
        "到站破损",
        "package_type warehouse hub reason",
        [amount("damage_yuan", "破损估值")],
        "确认|存疑|撤回",
        "ops",
        after="exception_signal",
    ),
    process(
        "compensation_claim",
        "赔偿申请",
        "client service partner reason",
        [amount("claim_yuan", "赔偿申请额")],
        "核准|待审|驳回",
        "care",
        after="damage_record",
        parent_states="确认",
    ),
    process(
        "compensation_settle",
        "赔付入账",
        "client service partner reason",
        [amount("paid_yuan", "赔偿入账额", parent="claim_yuan")],
        "已入账|处理中|失败",
        "billing",
        rows=120,
        after="compensation_claim",
        parent_states="核准",
    ),
    process(
        "billing_line",
        "计费流水",
        "client service fee_rule partner",
        [amount("net_fee", "净计费")],
        "已计费|试算|已冲正",
        "billing",
        rows=360,
        after="parcel",
        parent_states="已揽件",
    ),
    process(
        "invoice",
        "运输账单",
        "client service fee_rule",
        [amount("invoice_yuan", "开票金额")],
        "已开票|待开票|红冲",
        "billing",
        after="billing_line",
        parent_states="已计费",
    ),
    process(
        "remittance",
        "客户到账",
        "client service fee_rule",
        [amount("receipt_yuan", "到账金额", parent="invoice_yuan")],
        "已到账|未确认|冲回",
        "billing",
        after="invoice",
        parent_states="已开票",
    ),
    process(
        "fuel_usage",
        "车辆能耗",
        "fleet route partner shift",
        [count("energy_units", "账面能耗量", 1, 270)],
        "确认|估记|漏记",
        "dispatch",
    ),
    process(
        "facility_load",
        "场站负荷",
        "hub warehouse shift",
        [count("units_handled", "处理件数", 80, 1300)],
        "报告|试采|无数据",
        "ops",
    ),
    process(
        "route_utilization",
        "装载率台账",
        "fleet route shift",
        [count("capacity_pct", "装载百分比", 8, 100)],
        "实测|估算|撤回",
        "dispatch",
    ),
    process(
        "fleet_maintenance",
        "车辆例保",
        "fleet partner hub",
        [MINUTES, amount("cost_yuan", "维修费")],
        "完工|排期|返修",
        "dispatch",
    ),
    process(
        "container_move",
        "周转箱调拨",
        "hub route warehouse",
        [count("container_units", "周转箱数")],
        "已调拨|取消|途中",
        "dispatch",
    ),
    process(
        "customer_feedback",
        "客户评价",
        "client service reason courier",
        [count("rating", "评分", 1, 5)],
        "有效|待审|人工作废",
        "care",
        after="delivery_confirmation",
    ),
    process(
        "lost_identification",
        "走失件认定",
        "client service hub reason",
        [amount("lost_yuan", "认定货值")],
        "已认定|排查中|已找回",
        "care",
        rows=100,
        after="exception_signal",
    ),
    process(
        "inspection",
        "末端抽查",
        "courier district locker route",
        [count("defects", "问题数", 0, 12)],
        "已完成|待查|重新派发",
        "care",
    ),
    process("operator_shift", "工时与排班", "courier shift hub", [MINUTES], "已签|补签|撤销", "ops"),
    process(
        "partner_scorecard",
        "合作月度考核",
        "partner route fleet",
        [count("service_score", "服务评分", 35, 100)],
        "已发布|待复核|撤回",
        "care",
        panel="partner",
    ),
    process(
        "label_revision",
        "运单补打标签",
        "client service package_type reason",
        [count("reprint_units", "补打张数", 1, 4)],
        "有效|销毁|补记录",
        "tracking",
        after="parcel",
    ),
)

DOMAIN = Domain(
    "logistics",
    "晦湾速运",
    "城际运单、分拨轨迹、末端派送及客户账单分别由不同团队维护。",
    ("杭州分拨", "广州分拨", "天津分拨", "成都分拨", "苏州分拨", "深圳分拨", "北京分拨", "重庆分拨"),
    ENTITIES,
    PROCESSES,
    (
        Defect("last_mile_monthly_kpi_old", "delivery_attempt", "replay", "末端历史报数", "采集行被直接汇总"),
        Defect("rpt_delivery_confirm_north", "delivery_confirmation", "scope_loss", "场站月报", "只保留部分站点"),
        Defect("dm_fin_receipt_cents", "remittance", "unit_scale", "账单汇总历史", "金额以分显示"),
        Defect("tracking_daily_scope_legacy", "hub_inbound", "wrong_day", "轨迹月报 SQL", "粒度仍是日期"),
        Defect("warehouse_delivery_historical", "delivery_attempt", "stale_snapshot", "交接班存档", "只更新到三季度"),
        Defect("partner_score_old", "partner_scorecard", "state_filter", "外包团队看板", "只统计原发布态"),
        Defect("settlement_legacy_site", "compensation_settle", "fanout", "结算旧报表", "关联站点历史时未限有效期"),
        Defect("ops_parcels_mart_replay", "parcel", "replay", "早期揽收 看板", "新旧源采集未归并"),
    ),
    {
        "dispatch-notes.md": """# 调度和历史交接说明
一条运单代表一票。末端派送尝试可能有重派和失败，签收确认每票最多一条，拒收另列。公司考核以签收确认日和签收成功两种状态作为时效分母，准点为分子。要解释同一批揽件的表现需要关联原运单。
新旧分拨站名存在有效期。历史调度看板无统一归档流程，遇到口径冲突需要对比原始数据范围。
""",
        "billing-note.md": """# 财务接手指南
运费入账按已计费流水。客户到账按到账记录和实际到账日期。赔款按实际入账日。财务有部分历史汇总按分保存，使用前核对金额单位、是否冲正和范围。
""",
    },
    "寄件客户和收件信息",
)


def scenarios(domain, warehouse):
    q4 = quarter()
    shipped = state("已揽件")
    ontime = state("准点签收")
    bills = state("已计费")
    parcels = tally("parcel", *q4, shipped)
    signed = tally("delivery_confirmation", *q4, SIGNED)
    timely = tally("delivery_confirmation", *q4, ontime)
    fees = metric("billing_line", "net_fee", *q4, bills)
    out = modeling_cases(
        domain,
        warehouse,
        "parcel",
        "delivery_attempt",
        "service",
        {
            "dimension": "产品时效",
            "metric": "已揽运单票数",
            "alternative": "准点签收率",
            "definition": "已揽运单按揽收日和运单 ID 计票。准点签收率按签收确认日，准点签收数除以成功签收数。",
        },
    )
    out += singles(
        ("accuracy", "2025 年第四季度已揽的运单有多少票？", observe("accepted_parcels", parcels)),
        (
            "accuracy",
            "2025 年 11 月各时效产品的已揽票数各多少？",
            observe("by_service", tally("parcel", month(11), shipped, by=("service.name",))),
        ),
        (
            "accuracy",
            "2025 年第四季度，企业按签收确认口径的准点签收率是多少？",
            observe("sla_pct", ratio(timely, signed)),
        ),
        (
            "accuracy",
            "第四季度各分拨的已计费运费合计是多少？",
            observe("by_hub", metric("billing_line", "net_fee", *q4, bills, by=("site.name",))),
        ),
        (
            "time_semantics",
            "第 4 季度按入账日期实际赔付多少元？",
            observe("cash_paid", metric("compensation_settle", "paid_yuan", *q4, state("已入账"))),
        ),
        (
            "grain",
            "2025 年 12 月有多少不同运单发生投递尝试？重派只算一票。",
            observe("unique_parcels", tally("delivery_attempt", month(12), distinct="parent_record_id")),
        ),
        (
            "accuracy",
            "去年 11 月客户确认到账的运费共多少？",
            observe("receipts", metric("remittance", "receipt_yuan", month(11), state("已到账"))),
        ),
        (
            "accuracy",
            "第四季度哪个异常原因的已登记录数最多？给原因和件数。",
            observe("top_reason", tally("exception_signal", *q4, state("已登记"), by=("reason.name",), top=1)),
        ),
    )
    out += [
        case(
            "MULTI-01",
            "multi_turn",
            "context_memory",
            turn("先给我 2025 四季度运单的已揽票数。", observe("total", parcels)),
            turn(
                "沿用这段时期，按产品时效拆分。",
                observe("service", tally("parcel", *q4, shipped, by=("service.name",))),
            ),
            turn(
                "只留 12 月，产品维度不变，改给已揽货的重量合计（克）。",
                observe("weight", metric("parcel", "weight_gram", month(12), shipped, by=("service.name",))),
            ),
        ),
        case(
            "MULTI-02",
            "multi_turn",
            "context_memory",
            turn("四季度先看确认的准点签收率。", observe("sla_pct", ratio(timely, signed))),
            turn(
                "同样的确认口径，给 11 月和 12 月各自的准点率。",
                *[
                    observe(
                        f"pct_{m}",
                        ratio(
                            tally("delivery_confirmation", month(m), ontime),
                            tally("delivery_confirmation", month(m), SIGNED),
                        ),
                    )
                    for m in (11, 12)
                ],
            ),
            turn(
                "接下来给上一步 12 月的分母票数。",
                observe("denominator", tally("delivery_confirmation", month(12), SIGNED)),
            ),
        ),
        case(
            "MULTI-03",
            "multi_turn",
            "context_memory",
            turn(
                "四季度赔付入账金额按异常原因列一下。",
                observe(
                    "reason_pay",
                    metric("compensation_settle", "paid_yuan", *q4, state("已入账"), by=("reason.name",)),
                ),
            ),
            turn(
                "范围和状态沿用，指标换成入账笔数。",
                observe("reason_count", tally("compensation_settle", *q4, state("已入账"), by=("reason.name",))),
            ),
            turn(
                "去掉异常原因分组，给季度笔数合计。",
                observe("total", tally("compensation_settle", *q4, state("已入账"))),
            ),
        ),
        case(
            "EXP-01",
            "explore",
            "diagnosis",
            turn(
                "这季度末端是不是在重派上花了力气？先列投递尝试总数和涉及的独立运单数。",
                observe("attempts", tally("delivery_attempt", *q4)),
                observe("parcels", tally("delivery_attempt", *q4, distinct="parent_record_id")),
            ),
            turn(
                "分配送线路列尝试次数，看负荷集中在哪。",
                observe("routes", tally("delivery_attempt", *q4, by=("route.name",))),
            ),
            turn(
                "把联系失败和拒收的次数分别给我。结合这两个数给排查建议。",
                observe(
                    "failures", tally("delivery_attempt", *q4, ("state", "in", ("联系失败", "拒收")), by=("state",))
                ),
                review("diagnosis", "区分尝试与运单，避免将全部重复尝试判成失败。"),
            ),
        ),
        case(
            "EXP-02",
            "explore",
            "reasoning",
            turn(
                "查去年四季度按月的计费运费，先把已计费口径做实。",
                observe("fee", metric("billing_line", "net_fee", *q4, bills, by=("business_month",))),
            ),
            turn(
                "补上同一时期的实际到账月额。",
                observe(
                    "receipt", metric("remittance", "receipt_yuan", *q4, state("已到账"), by=("business_month",))
                ),
            ),
            turn(
                "给两种口径各自季度合计，解释为什么账单和到账可能不在同月。",
                observe("fee_total", fees),
                observe("receipt_total", metric("remittance", "receipt_yuan", *q4, state("已到账"))),
                review("reconciliation", "不把时点差额直接等同于坏账，说明结算和冲正。"),
            ),
        ),
        case(
            "EXP-03",
            "explore",
            "reasoning",
            turn(
                "四季度有哪些异常频发？按原因看已定异常记录。",
                observe("reason_counts", tally("exception_signal", *q4, state("已登记"), by=("reason.name",))),
            ),
            turn(
                "同一季度改看已经入账的赔偿，不把申报金额加进去。",
                observe(
                    "paid", metric("compensation_settle", "paid_yuan", *q4, state("已入账"), by=("reason.name",))
                ),
            ),
            turn(
                "只看 12 月已入账的所有赔付，说明这些金额能不能拿来给所有异常排序。",
                observe("dec_paid", metric("compensation_settle", "paid_yuan", month(12), state("已入账"))),
                review("limits", "区分支付与事故时间，指出需按源事件关联和核对未结案件。"),
            ),
        ),
        case(
            "RPT-01",
            "report",
            "reporting",
            turn(
                "帮我写四季度配送质量复盘，开头列已揽票数、准点票数、已签收票数。",
                observe("accepted", parcels),
                observe("timely", timely),
                observe("signed", signed),
                review("draft", "三项各自标清业务日期和分母。"),
            ),
            turn(
                "加上准点率和 12 月各产品已揽票量。",
                observe("sla_pct", ratio(timely, signed)),
                observe("dec_product", tally("parcel", month(12), shipped, by=("service.name",))),
            ),
            turn(
                "补充一段重派影响，引用四季度投递尝试中涉及的不同运单数，再交终稿。",
                observe("unique_attempts", tally("delivery_attempt", *q4, distinct="parent_record_id")),
                review("final_report", "给可追溯分组表，区分尝试和确认，不许捏造事实。"),
            ),
        ),
        case(
            "RPT-02",
            "report",
            "reporting",
            turn(
                "写一份四季度商业结算小结，先给已计费运费总额。",
                observe("fee_total", fees),
                review("draft", "先交有日期和状态口径的底稿。"),
            ),
            turn(
                "加上客户实际到账和赔付实际入账，两项单列。",
                observe("received", metric("remittance", "receipt_yuan", *q4, state("已到账"))),
                observe("paid", metric("compensation_settle", "paid_yuan", *q4, state("已入账"))),
            ),
            turn(
                "终稿再按分拨加一张已计费运费明细表，留核对清单。",
                observe("fees_by_sites", metric("billing_line", "net_fee", *q4, bills, by=("site.name",))),
                review("final_report", "交净计费分拨表、入账时间差限制和复核回执。"),
            ),
        ),
    ]
    out += negatives(
        (
            "attempt_inflation",
            "把四季度末端尝试次数用作实际派送的不同运单数。两者应该一致吧？",
            "correct",
            observe("attempts", tally("delivery_attempt", *q4)),
            observe("parcels", tally("delivery_attempt", *q4, distinct="parent_record_id")),
        ),
        (
            "scope_loss",
            "只拿前几个分拨的确认月报当全网签收数，运营会上这样报四季度行吗？",
            "correct",
            observe("network_total", signed),
            review("site_coverage", "核查是否遗漏其他场站。"),
        ),
        (
            "cent_conversion",
            "历史账单有一张以分统计的表，直接当元使用。请给我 11 月到账的元口径。",
            "correct",
            observe("actual_yuan", metric("remittance", "receipt_yuan", month(11), state("已到账"))),
        ),
        (
            "mix_failed_sla",
            "把拒收也放进准点签收率分母，我想跟管理层的成功签收率同口径比较。",
            "correct",
            observe("sla", ratio(timely, signed)),
            observe("signed_count", signed),
        ),
        (
            "causality",
            "末端拒收比较多，就说明那个片区商业策略不好。这是否可以直接定因？",
            "insufficient_evidence",
            review("needed_data", "考虑产品、体量、可达性和客户标签，区分相关性和原因。"),
        ),
        (
            "ambiguity",
            "帮我汇报一下最近运输效率，我还没定是看承诺还是实际、揽件还是确认。",
            "clarify",
            review("clarify", "要求明确事件、时间范围、时效产品和分母。"),
        ),
    )
    return out
