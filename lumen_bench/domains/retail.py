"""岚屿零售：有统一目录的全渠道电商与履约。"""
from ..cases import metric, modeling_cases, month, quarter, ratio, state, tally
from ..model import Domain, amount as money, count as units, difference, entity as d, process as p
from ..oracle import Arithmetic, case, expect, observe, review, turn

ENTITIES = (
    d("channel", "销售渠道", "直营商城|直营网店|内容电商|线下门店", "线上|线下"),
    d("category", "商品品类", "数码|小家电|服装|旅行|家居|日用品", "耐用消费|快消", history=True),
    d("brand", "品牌", "白屿|省心家|折线|林谷|蓝埠", "自营|经销"),
    d("vendor", "供货商", "晴川供应|贝港商贸|桥南器件|迢远贸易|晴合实业", "集采|本地"),
    d("sku", "最小库存单元", "折叠充电器|轻舟风扇|灰石背包|安睡枕|北辰线衫|厚底杯|续航线|穿行雨衣",
      "主推|常规|季节性", "category brand vendor", history=True),
    d("segment", "客群", "会员新客|会员老客|访客|企业购", "零售|团购"),
    d("customer", "匿名客户账户", "顾客甲|顾客乙|顾客丙|顾客丁|顾客戊|顾客己|顾客庚|顾客辛",
      "合成映射|合成匿名", "segment", history=True),
    d("store", "销售门店", "长桥门店|平湖门店|金湾门店|城北门店|云岚门店|西原门店|通河门店|星南门店",
      "旗舰|标准", "site", history=True),
    d("campaign", "营销活动", "初春换新|城市出行|夏日电器|秋日收纳|年末回馈|会员周",
      "季节性|常驻", "channel"),
    d("carrier", "承运商服务", "辰达普件|石帆日配|麦城冷链|晴途经济", "自营|外包"),
    d("method", "结算方式", "在线支付|门店支付|企业月结|积分抵扣", "即时|账期"),
    d("reason", "退货原因", "尺码不符|运输破损|非预期功能|重复购买|七天无理由", "质量|非质量"),
    d("bin", "仓库库位", "常温正品位|质检复核位|次品隔离位|大件区|退货暂存位|快配架",
      "正品|待处理", "site"),
    d("program", "营销权益", "满额券|会员折扣|专享权益|城市补贴|新客权益", "券|积分", "campaign"),
)

QTY = units("quantity", "件数")
GROSS = money("gross_amount", "商品标价金额", 3200, 120000)
DISCOUNT = money("discount_amount", "行优惠金额", 10, 3100)
NET = difference("net_amount", "优惠后订单金额", "gross_amount", "discount_amount")
PROCESSES = (
    p("sku_listing", "SKU 上架", "sku store channel", [money("list_price", "挂牌价")],
      "在售|停售|待上架", "pim"),
    p("inventory_receipt", "实物收货", "sku vendor bin", [QTY, money("book_cost", "入库成本")],
      "已收货|待质检|拒收", "wms"),
    p("stock_count", "库位盘点", "sku bin", [units("book_units", "账面量"), units("seen_units", "实盘量")],
      "已复核|待复核|已冲正", "wms"),
    p("stock_transfer", "库存调拨", "sku bin carrier", [QTY, money("transfer_cost", "调拨成本")],
      "已签收|调拨中|已取消", "wms"),
    p("web_visit", "入站访问", "customer channel campaign", [units("dwell_seconds", "停留秒数", 1, 1600)],
      "有效|去机器人|已退出", "web", rows=360),
    p("product_view", "浏览商品", "sku customer channel campaign", [units("view_seconds", "浏览秒数", 1, 800)],
      "有效|误触|已退出", "web", rows=300, after="web_visit"),
    p("cart_add", "加入购物车", "sku customer channel", [QTY],
      "已加入|已移除|已清空", "web", rows=240, after="product_view"),
    p("checkout", "提交结账", "sku customer channel method", [money("quoted", "应付报价")],
      "已提交|放弃|风控拒绝", "oms", rows=188, after="cart_add"),
    p("order_line", "电商订单行", "sku customer store channel campaign method",
      [QTY, GROSS, DISCOUNT, NET], "已支付|已取消|待支付", "oms", rows=680, scope="channel",
      note="订单日为下单日。已支付用于销售口径。"),
    p("payment_capture", "收款确认", "customer method store", [money("captured_amount", "确认收款额", parent="net_amount")],
      "已入账|待入账|失败", "pay", rows=490, after="order_line", parent_states="已支付"),
    p("invoice_issue", "电子发票", "customer store method", [money("tax_amount", "发票税额")],
      "已开票|已红冲|待开", "fin", after="payment_capture", parent_states="已入账"),
    p("pick_task", "仓内拣货", "sku bin store", [QTY, units("work_minutes", "工时分钟")],
      "已完成|缺货|取消", "wms", rows=360, after="order_line", parent_states="已支付"),
    p("parcel_manifest", "包裹交运", "sku carrier store", [money("billed_freight", "承运计费")],
      "已交运|待交运|已退回", "tms", rows=240, after="pick_task", parent_states="已完成"),
    p("delivery_scan", "包裹签收", "sku carrier store", [units("elapsed_hours", "配送小时数", 4, 180)],
      "已签收|拒收|异常", "tms", after="parcel_manifest", parent_states="已交运"),
    p("return_case", "退货申请", "sku customer store reason", [money("requested_amount", "申请退款额", parent="net_amount")],
      "审核通过|待审|驳回", "service", rows=250, after="order_line", parent_states="已支付"),
    p("refund_settlement", "退款入账", "sku customer store reason method", [money("refund_amount", "实退金额", parent="requested_amount")],
      "已入账|处理中|失败", "fin", rows=175, after="return_case", parent_states="审核通过"),
    p("chargeback_case", "支付拒付", "customer method", [money("disputed_amount", "拒付金额", parent="captured_amount")],
      "申诉中|败诉|结案", "pay", rows=75, after="payment_capture", parent_states="已入账"),
    p("service_ticket", "售后工单", "sku customer store reason", [units("close_hours", "处理小时数", 1, 168)],
      "已解决|跟进中|重开", "service", after="order_line", parent_states="已支付"),
    p("warranty_claim", "商品保修", "sku customer vendor", [money("service_cost", "维修成本")],
      "获批|驳回|待审", "service", rows=90, after="service_ticket"),
    p("replenishment_plan", "补货提议", "sku vendor bin", [QTY],
      "已采纳|待评审|放弃", "supply"),
    p("purchase_order", "供应采购单", "sku vendor", [QTY, money("po_value", "采购总额")],
      "已下达|已关闭|撤回", "erp", after="replenishment_plan"),
    p("vendor_receipt", "采购到货验收", "sku vendor bin", [QTY, units("rejected_units", "拒收数量", 0, 5)],
      "已入库|待查|拒收", "wms", after="purchase_order", parent_states="已下达"),
    p("supplier_invoice", "供应商对账发票", "sku vendor method", [money("invoice_amount", "对账金额")],
      "已匹配|差异|待匹配", "fin", after="vendor_receipt", parent_states="已入库"),
    p("supplier_payment", "供应商付款", "sku vendor method", [money("paid_amount", "付款额", parent="invoice_amount")],
      "已付款|挂起|待批", "fin", rows=120, after="supplier_invoice", parent_states="已匹配"),
    p("stock_reserve", "下单库存预占", "sku store bin", [QTY],
      "已占用|已释放|占用失败", "wms", after="order_line"),
    p("inventory_adjustment", "盘点差异过账", "sku bin", [units("change_units", "库存变动", -35, 45)],
      "已过账|待审批|已撤回", "wms", after="stock_count"),
    p("campaign_impression", "活动曝光", "channel customer campaign", [units("exposure_times", "曝光次数")],
      "有效|重复|拦截", "marketing", rows=260),
    p("coupon_redeem", "营销券核销", "channel customer campaign program", [money("coupon_amount", "核销额", 50, 2300)],
      "已核销|回补|待验", "marketing", after="order_line", parent_states="已支付"),
    p("price_revision", "商品调价", "sku store channel", [money("old_price", "调价前"), money("new_price", "调价后")],
      "执行|撤回|待检", "pim", after="sku_listing"),
    p("loyalty_earn", "积分发放", "customer store program", [units("points_added", "发放积分")],
      "已入账|冻结|撤销", "crm", rows=250, after="order_line", parent_states="已支付"),
    p("loyalty_spend", "积分消耗", "customer store program", [units("points_spent", "消耗积分")],
      "已入账|撤销|待确认", "crm", rows=128, after="loyalty_earn", parent_states="已入账"),
    p("gross_margin_recon", "已确认收入成本对账", "sku channel store", [money("cost_amount", "确认成本")],
      "已对账|含差异|待处理", "fin", after="order_line", parent_states="已支付"),
    p("loss_incident", "库存损失登记", "sku bin reason", [QTY, money("loss_amount", "损失价值")],
      "确认|申诉|结案", "wms", rows=102, after="stock_count"),
    p("demand_forecast", "需求预测批次", "sku store channel", [units("forecast_units", "需求预测件数", 4, 390)],
      "发布|模拟|作废", "supply"),
)

DOMAIN = Domain("retail", "岚屿零售", "线上零售、销售门店、仓配及财务一体化。",
                ("杭州运营中心", "广州运营中心", "天津运营中心", "成都运营中心",
                 "苏州运营中心", "深圳运营中心", "北京运营中心", "重庆运营中心"),
                ENTITIES, PROCESSES, (), {
    "01-warehouse-design.md": """# 岚屿零售数仓建设说明

源系统原记录入 ODS，保留重放，行标识是采集 ID。DWD 以业务事件 ID 保留最高版本，若同版本重复，取最新采集记录。订单、支付、拣货、退货和退款是不同事件。下游 parent_record_id 指向上游 record_id。
各事实的 business_day 是该过程发生日，business_month 是自然月首日。日汇总按网点，月汇总还保留状态。做跨过程汇总前分别聚合到需要的粒度。
经营区域由网点维表维护，实体历史表使用有效期左闭右开。展示名称可调整，事实关联用业务键。
""",
    "02-metrics.md": """# 销售与退款指标

已支付销售额按订单行发生日求和，使用 net_amount（gross_amount 减 discount_amount），单位为元。
收款额指已入账收款确认事件。批准退货申请不同于已入账退款。退款支出按退款入账日统计，结算状态为已入账。下单批次的退货或退款要回连原订单日，对未到期批次注明观察窗口。
除非明确要求冲减，销售额与退款分别报告。比较期按自然月以同一口径取数。一个订单行可对应多个下游事件，订单行计数须在自身粒度统计。
""",
    "03-delivery-operations.md": """# 履约落地说明

拣货完成是仓内事件，已交运表示承运人接手，已签收是承运扫描状态。配送时长来自签收过程，任何分位或平均必须明确取哪些签收记录。
仓库实盘数、账面量和库存调整各有来源。调拨在途量不是期末库存；需求预测批次是计划值。销售渠道及网点分别用于业务归属与运营责任。
""",
    "04-data-owners.md": """# 发布和使用约束

OMS 维护订单，财务维护入账退款与采购对账，仓储维护实物状态。分析者通过获准数据范围使用聚合。顾客映射是合成匿名标识，仅供粒度和关联验证，不是导出清单。
核账时先确认事实行数、键唯一性和各阶段状态，再按明确日期口径比较日汇总和明细。
""",
}, "顾客账户")


def scenarios(domain, warehouse):
    cases = modeling_cases(domain, warehouse, "order_line", "return_case", "sku", {
        "dimension": "SKU", "metric": "已支付销售额", "alternative": "确认收款额",
        "definition": "销售使用已支付的订单行净额和下单日。收款使用已入账收款确认金额和收款日，两者都是元。"
    })
    paid = state("已支付")
    booked = state("已入账")
    approved = state("审核通过")
    q4 = quarter(10)
    oct_paid = metric("order_line", "net_amount", month(10), paid)
    nov_paid = metric("order_line", "net_amount", month(11), paid)
    dec_paid = metric("order_line", "net_amount", month(12), paid)
    refunds_q4 = metric("refund_settlement", "refund_amount", *q4, booked)
    orders_q4 = metric("order_line", "net_amount", *q4, paid)
    case_list = [
        case("SING-01", "single_turn", "accuracy",
             turn("2025 年最后一季度已支付订单行的销售额是多少？用元。", observe("total_yuan", orders_q4))),
        case("SING-02", "single_turn", "accuracy",
             turn("2025 年 11 月有多少不同的客户付过款？同一个人好多单只算一次。",
                  observe("customers", tally("order_line", month(11), paid, distinct="customer_id")))),
        case("SING-03", "single_turn", "accuracy",
             turn("去年 12 月哪个销售渠道的已支付销售额最高？带上金额。",
                  observe("top_channel", metric("order_line", "net_amount", month(12), paid, by=("channel.name",), top=1)))),
        case("SING-04", "single_turn", "time_semantics",
             turn("财务去年第四季度实际入账的退款合计多少？",
                  observe("posted_refund_yuan", refunds_q4))),
        case("SING-05", "single_turn", "reasoning",
             turn("做库存复盘，2025 年 9 月已过账的库存盘点调整净变动多少件？",
                  observe("net_units", metric("inventory_adjustment", "change_units", month(9), state("已过账"))))),
        case("SING-06", "single_turn", "accuracy",
             turn("2025 年 8 月签收的包裹，实际平均配送用了多少小时？保留两位。",
                  observe("mean_hours", metric("delivery_scan", "elapsed_hours", month(8), state("已签收"), op="avg")))),
        case("SING-07", "single_turn", "accuracy",
             turn("去年 10 月，运营网点收到的已入库采购商品件数各是多少？",
                  observe("units_by_site", metric("vendor_receipt", "quantity", month(10), state("已入库"), by=("site.name",))))),
        case("SING-08", "single_turn", "reasoning",
             turn("2025 年 11 月，已解决售后工单里处理时间最长用了多少小时？",
                  observe("max_hours", metric("service_ticket", "close_hours", month(11), state("已解决"), op="max")))),
        case("MULTI-01", "multi_turn", "context_memory",
             turn("我们看 2025 年第四季度，先给已支付销售额总额。", observe("total_yuan", orders_q4)),
             turn("这段期间，拆成每月看。", observe("monthly", metric("order_line", "net_amount", *q4, paid, by=("business_month",)))),
             turn("现在只把上面拆分里的 11 月与 10 月比，销售额变化百分比是多少？",
                  observe("mom_pct", Arithmetic(nov_paid, oct_paid, "growth_pct")))),
        case("MULTI-02", "multi_turn", "context_memory",
             turn("去年 12 月，告诉我已支付金额最高的两个渠道。",
                  observe("top_two", metric("order_line", "net_amount", month(12), paid, by=("channel.name",), top=2))),
             turn("渠道维度留着。请把指标换成付过款的客户数，同个客户在同一渠道只算一次。",
                  observe("customer_channels", tally("order_line", month(12), paid, by=("channel.name",), distinct="customer_id"))),
             turn("把这一步的渠道取消掉，对全月客户再去重一次。",
                  observe("unique_customers", tally("order_line", month(12), paid, distinct="customer_id")))),
        case("MULTI-03", "multi_turn", "context_memory",
             turn("第四季度退货申请有哪些状态？给数量分布。",
                  observe("by_state", tally("return_case", *q4, by=("state",)))),
             turn("只留审核通过的，再按退货原因排名，前三。",
                  observe("top_reason", tally("return_case", *q4, approved, by=("reason.name",), top=3))),
             turn("上面通过审核的申请中，每个原因涉及的原订单行数是多少？申请重复对应同一行只算一次。",
                  observe("unique_orders", tally("return_case", *q4, approved, by=("reason.name",), distinct="parent_record_id")))),
        case("EXP-01", "explore", "diagnosis",
             turn("查查 2025 年第四季度销售是不是主要集中在少数运营区域。先列各区金额和全网合计。",
                  observe("network", orders_q4),
                  observe("regions", metric("order_line", "net_amount", *q4, paid, by=("site.region.name",))),
                  review("concentration", "解释区域集中度，列出分母和可用于复核的明细。")),
             turn("再把各区订单行数发我，看看金额和业务量的排名有什么不同。",
                  observe("order_volume", tally("order_line", *q4, paid, by=("site.region.name",)))),
             turn("请拆出 12 月的线上渠道对照，并说清哪些是数据观察，哪些还要验证。",
                  observe("dec_channel", metric("order_line", "net_amount", month(12), paid,
                          ("channel.name", "in", ("直营商城", "直营网店", "内容电商")), by=("channel.name",))),
                  review("limitations", "明确未验证的驱动因素，不把结构差异描述成因果。"))),
        case("EXP-02", "explore", "reasoning",
             turn("盘点调整最近是否拖累库存？先按月给第四季度已过账净调整。",
                  observe("monthly_units", metric("inventory_adjustment", "change_units", *q4, state("已过账"), by=("business_month",)))),
             turn("12 月哪些库位净调整最多？分库位给，我要看有负数的情况。",
                  observe("by_bin", metric("inventory_adjustment", "change_units", month(12), state("已过账"), by=("bin.name",)))),
             turn("补上 12 月待审批的调整量，给出一个核账建议。",
                  observe("pending_units", metric("inventory_adjustment", "change_units", month(12), state("待审批"))),
                  review("reconciliation", "把已过账差异与待审批区分开，指出还需期初期末和出入库明细。"))),
        case("EXP-03", "explore", "reasoning",
             turn("查下 9 月的退货审批压力，先比通过和待审各有多少。",
                  observe("statuses", tally("return_case", month(9), ("state", "in", ("审核通过", "待审")), by=("state",)))),
             turn("现在看通过的，哪个原因申请退款金额最高？",
                  observe("top_claim", metric("return_case", "requested_amount", month(9), approved, by=("reason.name",), top=1))),
             turn("顺便给 9 月实际已入账退款，提醒我这两组数字能不能直接对上。",
                  observe("settled", metric("refund_settlement", "refund_amount", month(9), booked)),
                  review("timing", "指出审批申请和入账的粒度与时点差异，建议通过原申请追溯。"))),
        case("RPT-01", "report", "reporting",
             turn("帮我写一份 2025 年第四季度经营复盘初稿，先交代销售和退款，再给出按月依据。",
                  observe("sales", orders_q4), observe("refunds", refunds_q4),
                  observe("monthly", metric("order_line", "net_amount", *q4, paid, by=("business_month",))),
                  review("business_artifact", "应给标题、统计口径、定量结论、明细表与来源。")),
             turn("管理层要把报告里的 12 月与 11 月变化写明，并补上百分比。",
                  observe("mom_pct", Arithmetic(dec_paid, nov_paid, "growth_pct"))),
             turn("把入账退款占季度已支付销售额比例补到终稿。不要据此判断同一批订单的退货率。最后列出复核事项。",
                  observe("outflow_ratio", ratio(refunds_q4, orders_q4)),
                  review("final_report", "保留金额变化与可核查来源，附复核清单，明确库存量及批次退货率未由该比例证明。"))),
        case("RPT-02", "report", "reporting",
             turn("仓配周会要看 2025 年第四季度的签收表现，用一份短报告说明各承运商签收量。",
                  observe("by_carrier", tally("delivery_scan", *q4, state("已签收"), by=("carrier.name",))),
                  review("draft", "交付按承运商拆分结果和签收定义，给出落地可追溯证据。")),
             turn("加上各家的平均配送小时，口径还是这些已签收件。",
                  observe("carrier_hours", metric("delivery_scan", "elapsed_hours", month(10), state("已签收"),
                                                   by=("carrier.name",), op="avg"))),
             turn("再把拒收单独列，改成能发给仓配负责人复核的版本。",
                  observe("refused", tally("delivery_scan", *q4, state("拒收"), by=("carrier.name",))),
                  review("final_report", "保留签收与拒收分别统计，明确时间、承运商、样本数和改进建议。"))),
        case("NEG-01", "counterexample", "join_reasoning",
             turn("我把源采集表直接按行计数，认为它就等于已支付订单行的发生数。你认可这个口径吗，帮我核算第四季度实际值。",
                  expect("verdict", "correct"), observe("paid_lines", tally("order_line", *q4, paid)),
                  review("correction", "解释重放、状态过滤及业务事件键，不能直接把采集行计入销量。")),
             tags=("cdc_replay",)),
        case("NEG-02", "counterexample", "time_semantics",
             turn("我准备把第四季度入账的退款都归到第四季度下单批次。财务和经营的两组退款金额真会相等吗？",
                  expect("verdict", "correct"), observe("posting_based", refunds_q4),
                  observe("cohort_based", metric("refund_settlement", "refund_amount", booked,
                          ("parent.parent.business_month", ">=", "2025-10-01"),
                          ("parent.parent.business_month", "<", "2026-01-01")))),
             tags=("cohort_vs_posting",)),
        case("NEG-03", "counterexample", "business_semantics",
             turn("销售看板说订单金额全额算销售，取消也算在内。我想按已经支付的销售口径比较。第四季度用哪个数？",
                  expect("verdict", "correct"), observe("actual_paid_yuan", orders_q4),
                  observe("included_nonpaid", Arithmetic(metric("order_line", "net_amount", *q4), orders_q4))),
             tags=("invalid_state",)),
        case("NEG-04", "counterexample", "unsupported_causality",
             turn("11 月市场部做了权益活动，我能说那个月的订单额都是活动带来的增量吗？需要什么证据？",
                  expect("verdict", "insufficient_evidence"), observe("observed_sales", nov_paid),
                  review("causality", "指出反事实、对照组或适当实验，有活动和有收入不足以证明增量。")),
             tags=("unsupported_causality",)),
        case("NEG-05", "counterexample", "ambiguity",
             turn("简单报一下最近的退款率吧，我还没想好用订单数还是金额，也没定观察期。",
                  expect("verdict", "clarify"), review("needed_clarification", "询问时间范围、分子分母、入账或下单批次。")),
             tags=("undefined_metric",)),
    ]
    return [*cases, *case_list]
