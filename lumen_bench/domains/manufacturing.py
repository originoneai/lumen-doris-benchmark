"""澄栈精工：工厂主数据完善，MES 重放与旧版质检报表并存。"""

from ..cases import metric, modeling_cases, month, negatives, quarter, ratio, singles, state, tally
from ..model import Defect, Domain, amount, count, entity, process
from ..oracle import case, observe, review, turn

ENTITIES = (
    entity(
        "line",
        "生产线",
        "压铸一线|机加二线|精抛三线|整装四线|注塑五线|测试六线|装配七线|复检八线",
        "柔性|专用",
        "site",
        history=True,
    ),
    entity(
        "machine",
        "设备",
        "冲床甲|龙门机乙|磨床丙|流转机丁|工控台戊|固化炉己|扭矩台庚|探伤台辛",
        "自动|半自动",
        "line",
        history=True,
    ),
    entity("shift", "班次", "早班|中班|夜班|周末班", "常规|加班"),
    entity(
        "product", "成品型号", "轻型壳体|密封端盖|高压底座|柔性法兰|双槽连接器|控制盘", "装配件|加工件", history=True
    ),
    entity("vendor", "原料供应商", "新渡金属|瓦树料业|潭谷机电|捷桩新材|瓯枝材料", "核心|备选"),
    entity("material", "原料", "滤网胚|优级铝|焊接丝|预制片|液压胶|合金棒", "金属|辅料", "vendor"),
    entity(
        "lot",
        "物料批号",
        "合金早批|焊丝一批|铝胚试产|新材一期|液压胶常规|涂层再检",
        "已标识|待复检",
        "material",
        history=True,
    ),
    entity("defect", "缺陷类型", "表面划伤|尺寸超差|密封不良|扭矩偏低|污染|标签错误", "材料|工艺|物流"),
    entity("tool", "工装", "压铸模具|焊接治具|检测夹具|旋转台|定位件|防护罩", "自制|采购", "line"),
    entity("team", "班组", "山组|水组|火组|石组|羽组|木组|风组|云组", "生产|维护", "site"),
    entity("operator", "员工匿名号", "技师一|技师二|技师三|技师四|技师五|技师六|技师七|技师八", "技师|复检", "team"),
    entity("stock_area", "存储分区", "投料暂存|预检区|在制区|待返修|合格库|成品口", "在制|成品", "site"),
    entity("buyer", "下游客户", "柏原设备|正谷汽配|澄海工具|万林器材|湾港控制", "主机厂|零部件厂"),
    entity("routing", "工艺路线", "高速铣|固化|研磨|防锈|低温检测|精装", "标准|工程试验", "product"),
)

Q = count("quantity", "件数", 8, 140)
TIME = count("duration_min", "记录分钟数", 5, 450)
SCRAP = count("rejected_units", "不合格件数", 0, 13)
TEST = count("tested_units", "抽检件数", 18, 150)
PROCESSES = (
    process("demand_signal", "客户需求单", "buyer product", [Q], "生效|待定|延后", "sales"),
    process(
        "forecast_review",
        "需求评审",
        "buyer product",
        [count("approved_units", "批准计划量")],
        "确认|退回|待排产",
        "planning",
        after="demand_signal",
    ),
    process(
        "capacity_plan",
        "产线月末产能计划",
        "line shift team",
        [count("capacity_hours", "可排小时", 200, 640)],
        "发布|草稿|撤回",
        "planning",
        panel="line",
    ),
    process(
        "sales_release",
        "客户交期承诺",
        "buyer product",
        [count("promised_days", "承诺天数")],
        "已承诺|延期|取消",
        "sales",
        after="forecast_review",
    ),
    process(
        "production_order",
        "生产工单",
        "product line shift team routing",
        [Q, amount("planned_cost", "计划成本")],
        "已下达|锁定|撤销",
        "erp",
        rows=520,
        scope="line",
    ),
    process(
        "material_reservation",
        "工单用料预留",
        "product material lot",
        [Q],
        "已预留|已释放|缺料",
        "erp",
        after="production_order",
        parent_states="已下达",
    ),
    process(
        "component_issue",
        "物料投产领料",
        "material lot stock_area product",
        [Q, amount("issued_cost", "领料成本")],
        "已出库|挂起|冲销",
        "wms",
        after="material_reservation",
        parent_states="已预留",
    ),
    process(
        "setup_change",
        "换线准备",
        "line machine product tool shift",
        [TIME],
        "已换线|等待|作废",
        "mes",
        after="production_order",
    ),
    process(
        "machine_cycle",
        "工序采集",
        "line machine product shift operator routing",
        [TIME, Q],
        "有效|设备重传|缺传",
        "mes",
        rows=390,
        after="production_order",
    ),
    process(
        "run_completion",
        "完工报工",
        "line machine product shift operator team",
        [count("good_units", "合格入段量", 12, 280), TIME],
        "确认|撤销|挂账",
        "mes",
        rows=500,
        after="production_order",
        parent_states="已下达",
        note="制造产出以确认报工为准，单位为件。",
        scope="line",
    ),
    process(
        "quality_inspection",
        "质量抽检",
        "line machine product shift defect routing",
        [TEST, SCRAP],
        "已复核|待复核|作废",
        "qms",
        rows=520,
        after="run_completion",
        parent_states="确认",
        scope="product",
    ),
    process(
        "defect_record",
        "缺陷记录",
        "line product defect operator",
        [SCRAP],
        "已登记|重复|待归类",
        "qms",
        after="quality_inspection",
        parent_states="已复核",
    ),
    process(
        "rework_order",
        "返工指令",
        "line product defect team",
        [Q, TIME],
        "收工|返工中|取消",
        "mes",
        after="quality_inspection",
        parent_states="已复核",
    ),
    process(
        "reinspection",
        "返工复检",
        "line product defect operator",
        [TEST, SCRAP],
        "已复核|待复核|作废",
        "qms",
        rows=125,
        after="rework_order",
        parent_states="收工",
    ),
    process(
        "scrap_posting",
        "废品过账",
        "line product defect stock_area",
        [Q, amount("loss_value", "废品损失")],
        "已过账|待复核|撤销",
        "erp",
        rows=160,
        after="defect_record",
    ),
    process(
        "downtime",
        "设备停机事件",
        "line machine shift team",
        [TIME],
        "故障|计划维护|待确认",
        "iot",
        rows=310,
        scope="machine",
    ),
    process(
        "maintenance_request",
        "维修申请",
        "line machine team",
        [count("priority_score", "紧急程度", 1, 5)],
        "已派工|取消|排队",
        "maint",
        after="downtime",
    ),
    process(
        "maintenance_work",
        "维修作业",
        "line machine team tool",
        [TIME, amount("material_cost", "维修物料费")],
        "已完成|作业中|暂停",
        "maint",
        after="maintenance_request",
        parent_states="已派工",
    ),
    process(
        "calibration",
        "仪表校准",
        "line machine tool operator",
        [count("error_um", "偏差微米", 0, 180)],
        "通过|失败|待定",
        "qms",
    ),
    process("tooling_change", "工装替换", "line machine tool shift", [TIME], "完成|待换|取消", "maint"),
    process(
        "energy_meter",
        "能耗抄表",
        "line machine shift",
        [count("kwh", "耗电千瓦时", 12, 850)],
        "审核|估算|缺数",
        "iot",
        rows=290,
    ),
    process(
        "packaging",
        "成品包装",
        "line product stock_area",
        [Q],
        "封箱|拆箱|隔离",
        "wms",
        after="run_completion",
        parent_states="确认",
    ),
    process(
        "lot_release",
        "成品批次放行",
        "product line lot operator",
        [Q],
        "放行|暂扣|退回",
        "qms",
        after="packaging",
        parent_states="封箱",
    ),
    process(
        "finished_receipt",
        "成品入库",
        "product line stock_area lot",
        [Q],
        "已入库|待收|退回",
        "wms",
        after="lot_release",
        parent_states="放行",
    ),
    process(
        "shipping",
        "客户发运",
        "product stock_area buyer lot",
        [Q, amount("outbound_value", "发运货值")],
        "出库|取消|待发",
        "logistics",
        after="finished_receipt",
        parent_states="已入库",
    ),
    process(
        "customer_complaint",
        "客户质量投诉",
        "buyer product lot defect",
        [count("reply_hours", "答复小时", 1, 200)],
        "结案|待答复|升级",
        "sales",
        rows=88,
        after="shipping",
        parent_states="出库",
    ),
    process(
        "supplier_audit",
        "供方审核",
        "vendor material lot",
        [count("audit_score", "审核得分", 30, 100)],
        "已评分|未完成|停用",
        "qms",
    ),
    process(
        "purchase_receipt",
        "采购来料",
        "vendor material lot stock_area",
        [Q, amount("received_value", "到货价值")],
        "已入暂存|待收|拒收",
        "wms",
    ),
    process(
        "incoming_inspection",
        "来料检验",
        "vendor material lot defect",
        [TEST, SCRAP],
        "判定|复核|驳回",
        "qms",
        after="purchase_receipt",
        parent_states="已入暂存",
    ),
    process(
        "cost_close",
        "成本结转",
        "product line stock_area",
        [amount("closed_cost", "结转成本")],
        "关账|在算|重开",
        "fin",
        after="finished_receipt",
    ),
    process(
        "labor_booking",
        "人员工时记账",
        "line operator shift team",
        [TIME],
        "确认|缺签|驳回",
        "hr",
        after="run_completion",
    ),
    process(
        "shift_handover",
        "班次交接",
        "line shift team machine",
        [count("open_items", "未交接事项", 0, 10)],
        "已签|缺签|挂起",
        "mes",
    ),
    process(
        "safety_observation",
        "现场安全巡查",
        "line team shift",
        [count("open_issues", "整改项数", 0, 8)],
        "完成|整改中|待巡",
        "ehs",
    ),
    process(
        "traceability_check",
        "物料逆向追溯",
        "line product lot material",
        [count("verified_links", "已核对环节", 0, 12)],
        "已追溯|链缺失|待验",
        "qms",
        after="lot_release",
    ),
)

DOMAIN = Domain(
    "manufacturing",
    "澄栈精工",
    "多工厂离散制造，覆盖工单、计件、质检、返工与供方。",
    ("杭州一厂", "广州二厂", "天津三厂", "成都四厂", "苏州五厂", "深圳六厂", "北京试制厂", "重庆八厂"),
    ENTITIES,
    PROCESSES,
    (
        Defect("mes_report_qty_legacy", "run_completion", "replay", "老 MES 节拍看板", "旧报表保留采集重播"),
        Defect("qms_monthly_inspection_old", "quality_inspection", "wrong_day", "质检班组旧指标", "旧月报仍为日粒度"),
        Defect("fin_shift_cost_q3", "cost_close", "stale_snapshot", "财务旧报表", "历史归档止于三季度"),
    ),
    {
        "01-process.md": """# 工厂数仓落地
工单表示计划执行，完工报工使用确认状态计合格产出。合格件数和工序采集数量来自不同事实。抽检能多次引用同一条报工。一张返工指令及其复检属于后续事件，复检不能替代原始抽检。
生产线、设备、班组与工厂分别维护。机台断点可造成 MES 采集重放，正式报工应去重后使用。新旧工厂名随生效日期维护。
""",
        "02-quality-metrics.md": """# 质量月报口径
按抽检日期和已复核状态汇总。抽检不合格率的单位是百分比，公式为不合格件数合计除以抽检件数合计。返工复检另列，产量与质量样本量分别报告。设备相关指标仅能支持观察，停机影响因素需确认设备与时段。
""",
        "03-maintenance.md": """# 维修交接
停机事件按记录时点归属，故障与计划维护分别看。维修作业记录耗时和物料成本，可追溯到维修申请与停机。旧班报仍供历史对照。
""",
    },
    "班组员工",
)


def scenarios(domain, warehouse):
    q4 = quarter()
    ok = state("确认")
    checked = state("已复核")
    inspected = metric("quality_inspection", "tested_units", *q4, checked)
    rejected = metric("quality_inspection", "rejected_units", *q4, checked)
    produced = metric("run_completion", "good_units", *q4, ok)
    out = modeling_cases(
        domain,
        warehouse,
        "run_completion",
        "quality_inspection",
        "line",
        {
            "dimension": "生产线",
            "metric": "确认完工合格量",
            "alternative": "抽检不合格率",
            "definition": "确认完工按报工日汇总 good_units。抽检不合格率用已复核抽检的不合格件数除以抽检件数。",
        },
    )
    out += singles(
        ("accuracy", "2025 年第四季度，确认完工的合格产量有多少件？", observe("good_units", produced)),
        (
            "accuracy",
            "2025 年 12 月确认产量最高的是哪条生产线？给出产量。",
            observe("top_line", metric("run_completion", "good_units", month(12), ok, by=("line.name",), top=1)),
        ),
        (
            "weighted_rate",
            "第四季度已复核抽检的不合格率是多少？请用汇总样本量作分母。",
            observe("reject_pct", ratio(rejected, inspected)),
        ),
        (
            "accuracy",
            "2025 年 10 月，设备故障停机总计多少分钟？",
            observe("fault_minutes", metric("downtime", "duration_min", month(10), state("故障"))),
        ),
        (
            "accuracy",
            "第四季度已领出的物料成本，按原料分别合计一下。",
            observe(
                "issue_cost", metric("component_issue", "issued_cost", *q4, state("已出库"), by=("material.name",))
            ),
        ),
        (
            "grain",
            "第四季度，有多少不同报工事件被已复核抽检覆盖？同一报工抽两次也只算一次。",
            observe("distinct_runs", tally("quality_inspection", *q4, checked, distinct="parent_record_id")),
        ),
        (
            "accuracy",
            "去年 11 月已过账废品造成的损失是多少？",
            observe("scrap_loss", metric("scrap_posting", "loss_value", month(11), state("已过账"))),
        ),
        (
            "accuracy",
            "第四季度已完成维修作业的平均作业分钟数是多少？",
            observe("repair_mean", metric("maintenance_work", "duration_min", *q4, state("已完成"), op="avg")),
        ),
    )
    out += [
        case(
            "MULTI-01",
            "multi_turn",
            "context_memory",
            turn(
                "盘点第四季度确认完工，按月给合格量。",
                observe("output", metric("run_completion", "good_units", *q4, ok, by=("business_month",))),
            ),
            turn(
                "把月份换为生产线，时期和口径照旧。",
                observe("by_line", metric("run_completion", "good_units", *q4, ok, by=("line.name",))),
            ),
            turn(
                "只留下 12 月，找合格量最高的前三条线。",
                observe("dec_top", metric("run_completion", "good_units", month(12), ok, by=("line.name",), top=3)),
            ),
        ),
        case(
            "MULTI-02",
            "multi_turn",
            "context_memory",
            turn(
                "四季度废品过账损失按缺陷类型列一下。",
                observe("losses", metric("scrap_posting", "loss_value", *q4, state("已过账"), by=("defect.name",))),
            ),
            turn(
                "这些缺陷损失先不用金额，改成过账行数。",
                observe("counts", tally("scrap_posting", *q4, state("已过账"), by=("defect.name",))),
            ),
            turn(
                "条件保持，去掉缺陷维度，只给一个季度总行数。",
                observe("total", tally("scrap_posting", *q4, state("已过账"))),
            ),
        ),
        case(
            "MULTI-03",
            "multi_turn",
            "context_memory",
            turn(
                "第四季度设备故障停机分钟，按各工厂看。",
                observe("faults", metric("downtime", "duration_min", *q4, state("故障"), by=("site.name",))),
            ),
            turn(
                "这一期改查计划维护，不要把故障混进来。",
                observe("planned", metric("downtime", "duration_min", *q4, state("计划维护"), by=("site.name",))),
            ),
            turn(
                "再把计划维护时长合计到全公司。",
                observe("planned_total", metric("downtime", "duration_min", *q4, state("计划维护"))),
            ),
        ),
        case(
            "EXP-01",
            "explore",
            "diagnosis",
            turn(
                "四季度有没有样本量很大、同时不合格也多的成品？先给成品的抽检量。",
                observe(
                    "quantities", metric("quality_inspection", "tested_units", *q4, checked, by=("product.name",))
                ),
            ),
            turn(
                "同样的成品加不合格量，供我分清规模和质量。",
                observe(
                    "rejections", metric("quality_inspection", "rejected_units", *q4, checked, by=("product.name",))
                ),
            ),
            turn(
                "给总体不合格率并说下一步怎样核对。",
                observe("weighted_pct", ratio(rejected, inspected)),
                review("recommendation", "区分抽样率与总体不良率，建议分工艺或批次复核。"),
            ),
        ),
        case(
            "EXP-02",
            "explore",
            "reasoning",
            turn(
                "设备停机报表看起来不稳，按月拆第四季度故障分钟。",
                observe("fault_mins", metric("downtime", "duration_min", *q4, state("故障"), by=("business_month",))),
            ),
            turn(
                "把计划维护按相同月份一起看。",
                observe(
                    "planned_mins", metric("downtime", "duration_min", *q4, state("计划维护"), by=("business_month",))
                ),
            ),
            turn(
                "12 月故障分钟最高的两个设备有哪些？下一步去找哪些工单？",
                observe(
                    "top_machines",
                    metric("downtime", "duration_min", month(12), state("故障"), by=("machine.name",), top=2),
                ),
                review("closure", "保留事件链，使用跨系统追溯，无凭据时不判断根因。"),
            ),
        ),
        case(
            "EXP-03",
            "explore",
            "reasoning",
            turn(
                "四季度来料检验的判定样本总数和不合格件数各多少？",
                observe("inspected", metric("incoming_inspection", "tested_units", *q4, state("判定"))),
                observe("rejects", metric("incoming_inspection", "rejected_units", *q4, state("判定"))),
            ),
            turn(
                "看看哪些供应商来料抽检量大。",
                observe(
                    "by_vendor",
                    metric("incoming_inspection", "tested_units", *q4, state("判定"), by=("vendor.name",)),
                ),
            ),
            turn(
                "同样按供应商列不合格件数，并写清还差哪些材料才能归责。",
                observe(
                    "by_vendor_defects",
                    metric("incoming_inspection", "rejected_units", *q4, state("判定"), by=("vendor.name",)),
                ),
                review("limits", "不从简单数量归责，检视抽样方案、物料构成和批号。"),
            ),
        ),
        case(
            "RPT-01",
            "report",
            "reporting",
            turn(
                "写一份四季度生产质量复盘，先列确认产出、抽检量和不合格量。",
                observe("production", produced),
                observe("tested", inspected),
                observe("rejects", rejected),
                review("draft", "交付三项指标、各自来源和统计时期。"),
            ),
            turn(
                "把不合格率补上，并把抽检与返工后的复检分开。",
                observe("reject_pct", ratio(rejected, inspected)),
                observe("retested", metric("reinspection", "tested_units", *q4, checked)),
            ),
            turn(
                "终稿加 12 月确认完工的产量，明确交给质量经理复核什么。",
                observe("dec_output", metric("run_completion", "good_units", month(12), ok)),
                review("final_report", "保留核对表、查询来源、样本说明和复核事项。"),
            ),
        ),
        case(
            "RPT-02",
            "report",
            "reporting",
            turn(
                "请写四季度工厂维护小结，先对比各厂故障停机时长。",
                observe("faults", metric("downtime", "duration_min", *q4, state("故障"), by=("site.name",))),
                review("draft", "按故障口径和来源交付表格。"),
            ),
            turn(
                "加入该季度已完成作业的维修物料费，按厂拆。",
                observe(
                    "repairs", metric("maintenance_work", "material_cost", *q4, state("已完成"), by=("site.name",))
                ),
            ),
            turn(
                "把 12 月计划维护时长单列，出一个可发给设备负责人的复核版。",
                observe("maintenance", metric("downtime", "duration_min", month(12), state("计划维护"))),
                review("final_report", "区分故障和维护，保留任何未验证因果的表述。"),
            ),
        ),
    ]
    out += negatives(
        (
            "mes_replay",
            "旧 MES 探针行数就当确认完工次数行不行？给四季度可核对的报工数。",
            "correct",
            observe("confirmed_events", tally("run_completion", *q4, ok)),
            review("replay", "解释源重放和确认状态。"),
        ),
        (
            "sample_denominator",
            "抽检不合格件除以全部合格产量，说这是抽检不合格率。请核对第四季度。",
            "correct",
            observe("sample_pct", ratio(rejected, inspected)),
            observe("wrong_denominator_pct", ratio(rejected, produced)),
        ),
        (
            "cross_grain",
            "我用质检明细行数统计被抽检的不同报工，反正一行就一笔。四季度是多少？",
            "correct",
            observe("inspection_count", tally("quality_inspection", *q4, checked)),
            observe("unique_events", tally("quality_inspection", *q4, checked, distinct="parent_record_id")),
        ),
        (
            "causality",
            "12 月设备坏了，所以不合格肯定全是设备故障造成的，能这样写周报吗？",
            "insufficient_evidence",
            observe("fault_minutes", metric("downtime", "duration_min", month(12), state("故障"))),
            review("cause", "需要按设备、时段与产品匹配，排除工艺和物料差异。"),
        ),
        (
            "ambiguity",
            "给个产品良率。我没定首检还是含返工，也没定从什么时候开始。",
            "clarify",
            review("needed_clarification", "问清时间、质量阶段、分子分母和复检处理。"),
        ),
        (
            "stale_mart",
            "财务把旧成本快照当去年 12 月关账金额，我要拿它做决算。帮我核实。",
            "correct",
            observe("closed_dec", metric("cost_close", "closed_cost", month(12), state("关账"))),
            review("freshness", "核对快照覆盖期，释放前核对关账事实。"),
        ),
    )
    return out
