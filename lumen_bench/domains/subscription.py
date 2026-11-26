"""栖环云：营收、订阅、销售和工单独立建设，客户口径经常冲突。"""

from decimal import Decimal

from ..cases import metric, modeling_cases, month, negatives, quarter, singles, state, tally
from ..model import Defect, Domain, amount, count, entity, process
from ..oracle import Arithmetic, case, observe, review, turn

ENTITIES = (
    entity("segment", "客户规模", "轻量团队|中型企业|集团组织|公共服务", "SMB|企业"),
    entity("sector", "客户行业", "设计服务|制造|零售|咨询|地产|教育", "专业服务|实业"),
    entity(
        "account",
        "企业租户",
        "青柑设计|寂石机电|野云文创|松陵咨询|方圆工造|枝远数控|藏石商业|一屿传媒",
        "签约|试用",
        "segment sector site",
        history=True,
    ),
    entity("plan", "套餐", "入门|团队|运营|企业|旗舰|专属", "标准|定制", history=True),
    entity("product", "产品模块", "流程引擎|智能报表|素材协同|权限中心|文档归档|集成连接器", "核心|附加", "plan"),
    entity("agent", "销售负责组", "拓新一组|渠道二组|续约三组|大客四组|方案组|生态组", "直销|渠道", "site"),
    entity("channel", "获客渠道", "伙伴转介|产品自然转化|会展|搜索|外呼|专项活动", "自助|销售"),
    entity("cycle", "计费周期", "按月|按季|按年|按用量", "账期|即时"),
    entity("currency", "交易计价", "人民币账本|美元登记|欧元内部价目|套餐点数", "法币|内部"),
    entity("reason", "变更原因", "业务缩减|迁移|超出预算|缺少功能|团队扩张|系统接入", "经营|产品"),
    entity("ticket_category", "支持类型", "登录|连接器|配额|收据发票|报表|功能使用", "故障|咨询"),
    entity("environment", "产品环境", "主服务|归档环境|演试环境|压力模拟|冷备|一键试用", "付费|内部", "site"),
    entity(
        "campaign", "销售活动", "夏期试用|新企业礼包|伙伴秋招|季度续费|流程搭建营|运营直播", "新客|续费", "channel"
    ),
    entity("pricebook", "价目表", "标准续费|签约特价|生态伙伴|消费税前|旧年度|专项续费", "当前|历史", "plan"),
)

Q = count("seat_count", "席位数", 2, 120)
T = count("elapsed_hours", "耗时小时", 1, 168)


def snapshot_value(row, parent, rng):
    account = row["account_id"]
    month_number = int(row["business_month"][5:7])
    row["plan_id"] = (account - 1) % 6 + 1
    row["active_seats"] = 3 + account * 2 + (month_number - 1) // 3
    row["mrr_yuan"] = Decimal(row["active_seats"] * (21 + 2 * account)).quantize(Decimal("0.01"))
    if account == 1 and month_number >= 10:
        row["state"] = "终止"
    elif account in (2, 5) and month_number % 4 == 0:
        row["state"] = "挂账"
    else:
        row["state"] = "有效"


PROCESSES = (
    process(
        "lead_arrival",
        "线索入池",
        "channel campaign agent sector",
        [count("fit_score", "线索评分", 0, 100)],
        "接受|丢弃|退回",
        "crm",
    ),
    process(
        "qualification",
        "线索资格判定",
        "channel campaign agent sector",
        [count("sales_score", "商机评分", 0, 100)],
        "合格|无效|待跟进",
        "crm",
        after="lead_arrival",
    ),
    process(
        "trial_account",
        "试用租户开通",
        "account plan environment channel",
        [Q],
        "已开通|失效|拒绝",
        "growth",
        after="qualification",
    ),
    process(
        "activation",
        "产品自助激活",
        "account product environment channel",
        [count("action_count", "有效动作")],
        "激活|重复|取消",
        "telemetry",
        after="trial_account",
    ),
    process(
        "contract",
        "签约合同",
        "account plan agent cycle currency",
        [amount("signed_value", "签约合同额", 1300, 1260000), Q],
        "已签|撤回|草案",
        "crm",
        rows=530,
        scope="plan",
    ),
    process(
        "contract_amendment",
        "协议变更",
        "account plan pricebook reason",
        [amount("change_yuan", "合同变更差额", -28000, 63000)],
        "已确认|待审批|放弃",
        "crm",
        after="contract",
        parent_states="已签",
    ),
    process(
        "subscription_start",
        "订阅开户",
        "account plan cycle product",
        [Q, amount("setup_fee", "一次性实施费", 0, 45000)],
        "开通|待付款|撤销",
        "product",
        rows=420,
        after="contract",
        parent_states="已签",
    ),
    process(
        "mrr_snapshot",
        "月末订阅快照",
        "account plan",
        [amount("mrr_yuan", "标准化月度订阅经常性金额"), count("active_seats", "快照已开席位")],
        "有效|挂账|终止",
        "product",
        panel="account",
        enrich=snapshot_value,
        scope="plan",
    ),
    process(
        "seat_assignment",
        "席位分配",
        "account plan product environment",
        [Q],
        "已分配|释放|等待",
        "product",
        after="subscription_start",
        parent_states="开通",
    ),
    process(
        "usage_event",
        "功能用量事件",
        "account product environment plan",
        [count("usage_units", "用量单位", 1, 15000)],
        "计入|去重|作废",
        "telemetry",
        rows=610,
    ),
    process(
        "overage_meter",
        "超额计量",
        "account product plan",
        [amount("overage_yuan", "超额费")],
        "已确认|待核|豁免",
        "billing",
        after="usage_event",
        parent_states="计入",
    ),
    process(
        "invoice_line",
        "发票账单行",
        "account plan cycle pricebook product",
        [amount("invoice_yuan", "开票净额", 1800, 655000)],
        "已开票|草稿|红冲",
        "billing",
        rows=480,
        after="subscription_start",
        parent_states="开通",
        scope="plan",
    ),
    process(
        "cash_payment",
        "客户收款",
        "account plan cycle product",
        [amount("cash_yuan", "确认到账", parent="invoice_yuan")],
        "已到账|待分摊|冲回",
        "fin",
        rows=380,
        after="invoice_line",
        parent_states="已开票",
    ),
    process(
        "invoice_credit",
        "账单贷项",
        "account plan reason",
        [amount("credit_yuan", "贷项金额", parent="invoice_yuan")],
        "已入账|挂起|撤销",
        "billing",
        after="invoice_line",
        parent_states="已开票",
    ),
    process(
        "refund",
        "到账退款",
        "account plan reason",
        [amount("refund_yuan", "退款入账额", parent="cash_yuan")],
        "已入账|审批中|失败",
        "fin",
        rows=120,
        after="cash_payment",
        parent_states="已到账",
    ),
    process(
        "dunning_case",
        "逾期催收",
        "account plan agent",
        [count("age_days", "逾期天数", 1, 160)],
        "催收|已解决|核销",
        "fin",
        after="invoice_line",
    ),
    process(
        "renewal_offer",
        "续约报价",
        "account plan cycle pricebook agent",
        [amount("quoted_yuan", "续约报价", 3200, 610000)],
        "有效|过期|撤回",
        "crm",
        after="subscription_start",
    ),
    process(
        "renewal_accept",
        "续约签回",
        "account plan cycle agent",
        [amount("renewed_yuan", "续签金额", parent="quoted_yuan")],
        "续约|拒绝|未确认",
        "crm",
        after="renewal_offer",
        parent_states="有效",
    ),
    process(
        "cancellation",
        "订阅终止",
        "account plan reason product",
        [amount("ended_mrr", "终止前月费")],
        "已生效|已挽回|计划",
        "product",
        after="subscription_start",
        parent_states="开通",
    ),
    process(
        "downgrade",
        "套餐下调",
        "account plan pricebook reason",
        [amount("lost_mrr", "缩减经常性收入")],
        "生效|驳回|待确认",
        "product",
        after="contract_amendment",
        parent_states="已确认",
    ),
    process(
        "upgrade",
        "套餐增购",
        "account plan pricebook reason",
        [amount("extra_mrr", "增购经常性收入")],
        "生效|挂账|反向调整",
        "product",
        after="contract_amendment",
        parent_states="已确认",
    ),
    process("csm_touch", "成功经理跟进", "account agent channel reason", [T], "完成|约不到|转交", "csm"),
    process(
        "support_ticket",
        "客户支持工单",
        "account ticket_category product environment",
        [count("priority", "紧急程度", 1, 5)],
        "已受理|重复|无效",
        "helpdesk",
        rows=450,
        scope="ticket_category",
    ),
    process(
        "support_resolution",
        "客户支持结案",
        "account ticket_category product environment",
        [T],
        "已结案|重开|超时",
        "helpdesk",
        rows=300,
        after="support_ticket",
        parent_states="已受理",
    ),
    process(
        "service_incident",
        "服务中断事件",
        "product environment ticket_category",
        [count("outage_minutes", "中断分钟", 1, 380)],
        "已确认|演练|未定位",
        "telemetry",
        rows=120,
    ),
    process(
        "uptime_rollup",
        "模块可用性月度抽样",
        "product environment",
        [count("healthy_minutes", "可用分钟", 38000, 44000)],
        "确认|近似|重算",
        "telemetry",
        panel="product",
    ),
    process(
        "service_credit",
        "补偿额度",
        "account product plan",
        [amount("service_credit", "停服补款")],
        "已批准|拒绝|挂起",
        "csm",
        after="service_incident",
        parent_states="已确认",
    ),
    process(
        "campaign_touch",
        "活动触达",
        "account channel campaign agent",
        [count("touches", "触达次数")],
        "有效|未送达|重复",
        "growth",
    ),
    process(
        "conversion_attribution",
        "签约来源归功",
        "account channel campaign agent",
        [count("confidence", "规则置信档", 1, 5)],
        "已归功|多触点|争议",
        "growth",
        after="contract",
        parent_states="已签",
    ),
    process(
        "cloud_procurement",
        "基础设施采购",
        "product environment pricebook",
        [amount("procurement_yuan", "采购额")],
        "已采购|取消|审查",
        "ops",
    ),
    process(
        "cloud_cost",
        "模块云成本",
        "product environment",
        [amount("cost_yuan", "云计算成本")],
        "已分摊|待分摊|调账",
        "ops",
        after="cloud_procurement",
    ),
    process(
        "quota_adjustment",
        "产品配额调整",
        "account plan product reason",
        [count("change_units", "调整用量", -800, 1600)],
        "已生效|挂起|取消",
        "product",
        after="usage_event",
    ),
    process(
        "revenue_posting",
        "会计收入分摊",
        "account plan cycle product",
        [amount("recognized_yuan", "已分摊收入", parent="invoice_yuan")],
        "已过账|未过账|冲回",
        "fin",
        rows=330,
        after="invoice_line",
        parent_states="已开票",
    ),
    process(
        "deferred_balance",
        "递延收入期末余额",
        "account plan currency",
        [amount("balance_yuan", "期末递延余额")],
        "已结账|往年暂估|重开",
        "fin",
        panel="account",
    ),
)

DOMAIN = Domain(
    "subscription",
    "栖环云",
    "多租户订阅软件，销售、计费、产品、成功团队各维护自己的客户和报表。",
    ("华东运营组", "华南运营组", "华北运营组", "西南运营组", "华东伙伴组", "华南伙伴组", "华北大客组", "西南续费组"),
    ENTITIES,
    PROCESSES,
    (
        Defect("crm_mrr_by_contract", "contract", "state_filter", "销售 KPI", "销售把签约合同金额标成月度营收"),
        Defect("growth_mrr_jan_to_sep", "mrr_snapshot", "stale_snapshot", "增长旧板", "续约改版前的历史截面"),
        Defect("fin_mrr_monthly_tenant_dup", "mrr_snapshot", "replay", "财务试算", "快照重放后直接加总"),
        Defect("billing_invoice_amount_legacy", "invoice_line", "fanout", "账单旧管道", "当前历史组织被重复关联"),
        Defect("finance_income_percent", "revenue_posting", "unit_scale", "经营报损表", "金额倍数换算方向错误"),
        Defect("csm_seat_slice", "seat_assignment", "scope_loss", "局部成功组", "仅部分小组"),
        Defect("telemetry_usage_preview", "usage_event", "test_copy", "用量临时预览", "抽样被当作全量"),
        Defect("support_monthly_case_tmp", "support_resolution", "wrong_day", "支持组临时板", "自然日作月末"),
        Defect("crm_renewal_dash_2025", "renewal_accept", "replay", "续约中心", "旧 API 可能反复推送"),
        Defect("fin_collections_no_credit", "cash_payment", "state_filter", "现金核对", "未记录状态冲回"),
        Defect("ops_warehouse_cogs_east", "cloud_cost", "scope_loss", "运营成本小板", "只采部分区域"),
        Defect("saas_arr_early", "mrr_snapshot", "test_copy", "战情旧报", "前期调试样本"),
        Defect("usage_table_copy", "usage_event", "replay", "产品临时数", "未合并采集版本"),
        Defect("dws_invoice_repeat", "invoice_line", "replay", "开票临时管道", "草稿记录及重复开票"),
        Defect("csm_renewal_state_legacy", "renewal_offer", "stale_snapshot", "成功组交接", "报表只保存到 9 月"),
        Defect("success_rev_copy", "revenue_posting", "wrong_day", "经营同比旧表", "产物粒度未改成月"),
    ),
    {
        "revenue-handover.txt": """销售交接：已签合同月份的签约额叫“营收”。看续约另找表。
财务留言：收入按已过账分摊日算，客户到账另看。产品收费报告是按月末企业租户快照取有效状态。写 MRR 时金额是元，不能加三个期末当作期末数。
旧板历史截至时间各不相同，找到两份冲突数据先复核再抄。
""",
    },
    "公司租户联系人与私有支持工单",
)


def scenarios(domain, warehouse):
    q4 = quarter()
    effective = state("有效")
    posted = state("已过账")
    signed = state("已签")
    mrr_oct, mrr_nov, mrr_dec = (metric("mrr_snapshot", "mrr_yuan", month(m), effective) for m in (10, 11, 12))
    contract = metric("contract", "signed_value", *q4, signed)
    revenue = metric("revenue_posting", "recognized_yuan", *q4, posted)
    cash = metric("cash_payment", "cash_yuan", *q4, state("已到账"))
    out = modeling_cases(
        domain,
        warehouse,
        "invoice_line",
        "revenue_posting",
        "account",
        {
            "dimension": "企业租户",
            "metric": "已开票账单额",
            "alternative": "确认的会计收入",
            "definition": "已开票账单额取开票日、invoice_yuan。确认收入按已过账分摊日、recognized_yuan，两项加总时须各自守粒度。",
        },
    )
    out += singles(
        ("snapshot", "2025 年 12 月月末有效订阅的 MRR 是多少元？", observe("dec_mrr", mrr_dec)),
        ("accuracy", "2025 年第四季度已签合同金额合计多少？", observe("contract_yuan", contract)),
        ("accuracy", "2025 年第四季度会计已过账分摊收入是多少？", observe("recognized_yuan", revenue)),
        (
            "accuracy",
            "2025 年 12 月有几个不同企业租户处于有效订阅状态？",
            observe("paying_accounts", tally("mrr_snapshot", month(12), effective, distinct="account_id")),
        ),
        (
            "accuracy",
            "第四季度客户已到账金额按套餐分别是多少？",
            observe("cash_by_plan", metric("cash_payment", "cash_yuan", *q4, state("已到账"), by=("plan.name",))),
        ),
        (
            "reasoning",
            "2025 年 11 月已结案的支持工单平均用了多少小时？",
            observe("avg_hours", metric("support_resolution", "elapsed_hours", month(11), state("已结案"), op="avg")),
        ),
        (
            "accuracy",
            "去年 10 月的生效套餐增购增加多少月费？",
            observe("added", metric("upgrade", "extra_mrr", month(10), state("生效"))),
        ),
        (
            "accuracy",
            "去年第四季度，哪个支持类型受理的工单最多？给类型和单数。",
            observe("ticket_top", tally("support_ticket", *q4, state("已受理"), by=("ticket_category.name",), top=1)),
        ),
    )
    out += [
        case(
            "MULTI-01",
            "multi_turn",
            "context_memory",
            turn("先给 2025 年 12 月月末有效 MRR。", observe("mrr", mrr_dec)),
            turn(
                "上面这个值按套餐拆开。",
                observe("mrr_by_plan", metric("mrr_snapshot", "mrr_yuan", month(12), effective, by=("plan.name",))),
            ),
            turn(
                "套餐维度去掉，保持月末有效，给我 12 月相较 11 月环比百分比。",
                observe("mom", Arithmetic(mrr_dec, mrr_nov, "growth_pct")),
            ),
        ),
        case(
            "MULTI-02",
            "multi_turn",
            "context_memory",
            turn(
                "四季度订阅退款入账，按原因给出金额。",
                observe("refund", metric("refund", "refund_yuan", *q4, state("已入账"), by=("reason.name",))),
            ),
            turn(
                "按原原因归类，把金额改成不同发生退款的租户数。",
                observe(
                    "accounts", tally("refund", *q4, state("已入账"), by=("reason.name",), distinct="account_id")
                ),
            ),
            turn(
                "以上这些租户再跨原因去重，给我季度总数。",
                observe("distinct", tally("refund", *q4, state("已入账"), distinct="account_id")),
            ),
        ),
        case(
            "MULTI-03",
            "multi_turn",
            "context_memory",
            turn(
                "去年 12 月，已结案工单按支持类型看平均处理时长。",
                observe(
                    "mean",
                    metric(
                        "support_resolution",
                        "elapsed_hours",
                        month(12),
                        state("已结案"),
                        by=("ticket_category.name",),
                        op="avg",
                    ),
                ),
            ),
            turn(
                "按同一条件给出各类型的结案次数。",
                observe(
                    "by_type", tally("support_resolution", month(12), state("已结案"), by=("ticket_category.name",))
                ),
            ),
            turn(
                "只把时期放宽到整个第四季度，拿结案数最高的支持类型。",
                observe(
                    "top_type", tally("support_resolution", *q4, state("已结案"), by=("ticket_category.name",), top=1)
                ),
            ),
        ),
        case(
            "EXP-01",
            "explore",
            "diagnosis",
            turn(
                "销售和会计的四季度数字差距大，先拿签约与会计收入两个正确分口径的合计。",
                observe("contract", contract),
                observe("recognized", revenue),
            ),
            turn(
                "按月拆出会计已过账收入，再给实际到账总额。",
                observe("monthly", metric("revenue_posting", "recognized_yuan", *q4, posted, by=("business_month",))),
                observe("cash", cash),
            ),
            turn(
                "用差额和已有信息给个后续对账步骤。",
                observe("gap", Arithmetic(contract, revenue)),
                review("reconcile", "区分合同、会计和现金的日期与粒度，链接开票和分摊。"),
            ),
        ),
        case(
            "EXP-02",
            "explore",
            "reasoning",
            turn(
                "季度月末 MRR 怎么走？先列 10 月和 11 月有效快照。", observe("oct", mrr_oct), observe("nov", mrr_nov)
            ),
            turn(
                "再给 12 月和方向，我要看当月是否下滑。",
                observe("dec", mrr_dec),
                observe("delta_vs_nov", Arithmetic(mrr_dec, mrr_nov)),
            ),
            turn(
                "把 12 月的终止快照条数列出来，解释能不能直接指认为全部下滑原因。",
                observe("terminated", tally("mrr_snapshot", month(12), state("终止"))),
                review("explanation", "不能只凭单月状态断因，说明续约、增长和流失桥接证据。"),
            ),
        ),
        case(
            "EXP-03",
            "explore",
            "reasoning",
            turn(
                "四季度工单热点在哪里？按类型给已受理的总数。",
                observe("intake", tally("support_ticket", *q4, state("已受理"), by=("ticket_category.name",))),
            ),
            turn(
                "同一期按类型再看已结案的平均处理时长。",
                observe(
                    "duration",
                    metric(
                        "support_resolution",
                        "elapsed_hours",
                        *q4,
                        state("已结案"),
                        by=("ticket_category.name",),
                        op="avg",
                    ),
                ),
            ),
            turn(
                "补上 12 月已结案件数，说明如何估算真实积压。",
                observe("dec_done", tally("support_resolution", month(12), state("已结案"))),
                review("backlog", "需继承期初未结和同源计数，不能用不同队列直接相减断言积压。"),
            ),
        ),
        case(
            "RPT-01",
            "report",
            "reporting",
            turn(
                "给财务评审一份四季度订阅经营初稿，先把签约、已过账收入和到账分开。",
                observe("contract", contract),
                observe("recognized", revenue),
                observe("cash", cash),
                review("draft", "写三个不同业务时点和各自证据。"),
            ),
            turn(
                "再附 12 月月末有效 MRR，并给 11 月对照。", observe("dec_mrr", mrr_dec), observe("nov_mrr", mrr_nov)
            ),
            turn(
                "终稿加上四季度已经入账的退款，列清结论和复核动作。",
                observe("refunds", metric("refund", "refund_yuan", *q4, state("已入账"))),
                review("final_report", "报告有证据表、差异说明、风险与待核对项。"),
            ),
        ),
        case(
            "RPT-02",
            "report",
            "reporting",
            turn(
                "做一份四季度产品使用与支持概览，先按模块列有效用量。",
                observe("usage", metric("usage_event", "usage_units", *q4, state("计入"), by=("product.name",))),
                review("draft", "按模块给可追溯原始统计。"),
            ),
            turn(
                "再加 12 月各支持类型的结案量，和用量分开列。",
                observe(
                    "dec_support",
                    tally("support_resolution", month(12), state("已结案"), by=("ticket_category.name",)),
                ),
            ),
            turn(
                "加上 12 月已确认故障中断分钟，给运营总监发终稿。",
                observe("downtime", metric("service_incident", "outage_minutes", month(12), state("已确认"))),
                review("final_report", "明确用量、支持、故障证据和指标单位，注明局限与复核人。"),
            ),
        ),
    ]
    out += negatives(
        (
            "contract_vs_mrr",
            "我把四季度已签合同额直接写成 12 月月末 MRR，可以批准这段话吗？",
            "correct",
            observe("contract", contract),
            observe("mrr", mrr_dec),
        ),
        (
            "period_end_double_count",
            "把四季度三个 MRR 快照加起来当季度最后一天的 MRR。帮我算正确期末值。",
            "correct",
            observe("quarter_end_mrr", mrr_dec),
            observe("sum_of_snapshots", metric("mrr_snapshot", "mrr_yuan", *q4, effective)),
        ),
        (
            "cash_vs_accrual",
            "部门要拿四季度到账代替会计已过账收入，对外简报这样写可以吗？",
            "correct",
            observe("recognized_yuan", revenue),
            observe("cash_yuan", cash),
        ),
        (
            "stale_scope",
            "旧版增长报表九月之后没有记录，我想认定 10 月没有有效订阅。帮我复核。",
            "correct",
            observe("oct_mrr", mrr_oct),
            review("freshness", "先识别截面覆盖并回到有效快照。"),
        ),
        (
            "unsupported_cause",
            "既然 12 月工单增加，季度流失就肯定都是客服造成的。你认为证据够不够？",
            "insufficient_evidence",
            review("causal_evidence", "检查租户队列、工单日期、产品事件和对照情况。"),
        ),
        (
            "ambiguous_revenue",
            "最近营收怎样？按销售的签约还是财务确认我没决定，时间也没定。",
            "clarify",
            review("needed_scope", "询问业务口径、时期与金额单位。"),
        ),
    )
    return out
