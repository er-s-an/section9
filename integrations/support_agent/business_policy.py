"""Server-owned, read-only business guard for the adapted support-agent app.

This module deliberately leaves ordinary requests to the existing LangGraph
router and specialist agents. It only handles narrow cases that are unsafe or
ambiguous without deterministic guardrails.
"""
from __future__ import annotations

import os
import re
from typing import Any

VALID_MODES = {"guarded", "legacy"}
WRITE_TOOLS = {"initiate_refund", "process_payment"}
SENSITIVE_TOOLS = {"get_order_details", "get_payment_status"}
DENIED_TOOLS = WRITE_TOOLS | SENSITIVE_TOOLS

_ORDER_ID = re.compile(
    r"(?:\border\s*(?:#|id\s*)?|订单\s*#?\s*|(?<!\d))(\d{3,6})(?!\d)",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"\b(ignore|disregard|override)\b.{0,50}\b(previous|system|developer|safety|policy|instruction)s?\b"
    r"|\b(reveal|print|show)\b.{0,40}\b(system prompt|hidden instructions|api key|secret)\b"
    r"|\b(bypass|disable|circumvent)\b.{0,40}\b(guard|policy|safety|approval)\b"
    r"|\b(pretend|act as|i am)\b.{0,30}\b(owner|admin|employee|authorized|verified)\b"
    r"|忽略.{0,16}(之前|系统|开发者).{0,10}(规则|指令|提示)"
    r"|绕过.{0,10}(安全|策略|规则|限制|验证)"
    r"|泄露.{0,10}(提示词|系统指令|密钥|秘密)",
    re.IGNORECASE | re.DOTALL,
)
_HUMAN = re.compile(r"\b(speak|talk|connect|transfer|escalate)\b.{0,35}\b(human|person|agent|support)\b|真人|人工客服|找人工|转人工|人工处理|请求人工", re.I)
_NEGATED_HUMAN = re.compile(
    r"\b(?:do not|don't|never|not|no)\b.{0,60}\b(?:speak|talk|connect|transfer|escalate|route|contact|invent|create|make up)\b.{0,35}\b(?:human|person|agent|support|ticket)\b"
    r"|(?:千万别|不要|别|不需要|无需|不想|不希望|不必).{0,40}(?:找人工|转人工|联系真人|人工客服|请求人工|(?:编|创建|生成).{0,12}(?:人工|真人|客服|工单))",
    re.I | re.DOTALL,
)
_DOUBLE_NEGATED_HUMAN = re.compile(
    r"\b(?:do not|don't|never)\s+not\b.{0,30}\b(?:speak|talk|connect|transfer|escalate|contact)\b.{0,25}\b(?:human|person|agent|support)\b"
    r"|(?:不要|别|不必)?不(?!要|需|想|希望|必).{0,6}(?:找人工|转人工|联系真人|人工客服|请求人工)",
    re.I,
)
_HUMAN_CLAUSE_BREAK = re.compile(r"[,，;；。！？!?]|\b(?:but|however|yet|instead)\b|但是|然而|不过|而是|但", re.I)
_LOGISTICS = re.compile(r"tracking|shipment|shipping|delivery|delivered|package|parcel|物流|快递|配送|包裹|送到|发货", re.I)
_ORDER_STATUS = re.compile(
    r"\border\b.{0,30}\b(status|state|details)\b|\b(status|state)\b.{0,25}\bof (?:my )?order\b|订单.{0,12}(?:状态|进度|情况)|(?:查|查询).{0,8}订单",
    re.I,
)
_REFUND = re.compile(
    r"refund|refundable|refund eligibility|eligible.{0,20}refund|退款|退货|退钱|退款资格|能不能退|能退吗|能退么|可以退吗|可以退么",
    re.I,
)
_REFUND_ELIGIBILITY = re.compile(
    r"eligib(?:le|ility)|qualif(?:y|ies)|refund eligibility|退款资格|符合退款条件|是否可退|能否退款|能不能退(?:吗|么)?|能退(?:吗|么)|可以退(?:吗|么)|能退款(?:吗|么)|能不能退款|可以退款吗",
    re.I,
)
_OTHER_PERSON = r"friend(?:'s)?|someone else'?s|another person'?s|other person'?s|my coworker|my partner|朋友|别人的|他人的|家人的|同事|同学|伴侣"
_PERSONAL_FIELD = r"name|address|payment (?:info|details)|card details|personal information|contact details|姓名|地址|支付信息|付款信息|银行卡|个人信息|联系方式"
_SENSITIVE_PERSONAL_REQUEST = re.compile(
    rf"(?:{_OTHER_PERSON}).{{0,80}}(?:{_PERSONAL_FIELD})|(?:{_PERSONAL_FIELD}).{{0,60}}(?:{_OTHER_PERSON})",
    re.I | re.DOTALL,
)
_READ_ONLY = re.compile(r"check|whether|eligible|eligibility|status|qualify|only|只是|查一下|能不能|是否|资格", re.I)
_REFUND_POLICY_QUESTION = re.compile(r"return policy|refund policy|policy|退货政策|退款政策|退款流程|怎么退款|多久到账", re.I)
_WRITE_REQUEST = re.compile(r"\b(issue|initiate|start|process|submit|approve|send|pay|charge)\b.{0,30}\b(refund|payment|charge)\b|\brefund\b.{0,20}\b(now|immediately|please)\b|发起退款|给我退款|处理退款|直接退款|付款", re.I)
_NEGATED_WRITE = re.compile(r"\b(?:do not|don't|never|not|no)(?:\s+\w+){0,2}\s*$|(?:不需要|不想|不要|禁止|无需|不用|不|别)\s*$", re.I)


def requests_write(message: str) -> bool:
    for match in _WRITE_REQUEST.finditer(message or ""):
        prefix = (message or "")[max(0, match.start() - 32):match.start()]
        if not _NEGATED_WRITE.search(prefix):
            return True
    return False


def requests_human(message: str) -> bool:
    """Recognize an actual handoff request while honoring a local negation."""
    for clause in _HUMAN_CLAUSE_BREAK.split(message or ""):
        if not _HUMAN.search(clause):
            continue
        if _DOUBLE_NEGATED_HUMAN.search(clause):
            return True
        if _NEGATED_HUMAN.search(clause):
            continue
        return True
    return False


def policy_mode() -> str:
    """Read the mode from server process configuration; request text is never consulted."""
    value = os.environ.get("S9_SUPPORT_POLICY", "guarded").strip().lower()
    return value if value in VALID_MODES else "guarded"


def initial_policy_decision() -> dict[str, Any]:
    mode = policy_mode()
    return {
        "mode": mode,
        "decision": "legacy_route" if mode == "legacy" else "readonly_query",
        "reason": "Existing model router remains authoritative for this request.",
        "intents": [],
        "order_ids": [],
        "read_only": True,
    }


def classify_guarded_request(message: str) -> dict[str, Any] | None:
    """Return a narrow deterministic plan, or None to use the original LLM router."""
    if policy_mode() != "guarded":
        return None
    text = message or ""
    if _INJECTION.search(text):
        return {"route": "POLICY", "policy_decision": {
            "mode": "guarded", "decision": "blocked_injection",
            "reason": "Untrusted instructions cannot change policy, identity, or tool permissions.",
            "intents": [], "order_ids": [], "read_only": True,
        }}

    order_ids = list(dict.fromkeys(int(value) for value in _ORDER_ID.findall(text)))
    if _SENSITIVE_PERSONAL_REQUEST.search(text):
        return {"route": "POLICY", "policy_decision": {
            "mode": "guarded", "decision": "unverified_identity",
            "reason": "Identity is unverified; personal information about another person cannot be disclosed.",
            "intents": ["personal_information"], "order_ids": order_ids, "read_only": True,
        }}

    if requests_human(text):
        return {"route": "ESCALATE", "policy_decision": {
            "mode": "guarded", "decision": "human_requested",
            "reason": "The customer requested a person; no live support queue is connected.",
            "intents": [], "order_ids": [], "read_only": True,
        }}

    logistics = bool(_LOGISTICS.search(text))
    order_status = logistics or bool(_ORDER_STATUS.search(text))
    refund = bool(_REFUND.search(text))
    refund_eligibility = refund and bool(_REFUND_ELIGIBILITY.search(text))
    intents = (["order_status"] if order_status else []) + (["refund_eligibility"] if refund_eligibility else [])

    # General policy questions belong to the original FAQ agent and need no
    # order identifier. Keep normal model routing for this common path.
    if refund and not logistics and _REFUND_POLICY_QUESTION.search(text):
        return None

    if requests_write(text):
        return {"route": "POLICY", "policy_decision": {
            "mode": "guarded", "decision": "blocked_write",
            "reason": "This demo is read-only; no refund or payment mutation was performed.",
            "intents": intents, "order_ids": order_ids, "read_only": True,
        }}

    if len(order_ids) > 1 and (order_status or refund):
        return {"route": "POLICY", "policy_decision": {
            "mode": "guarded", "decision": "unsupported_request",
            "reason": "This demo handles one order per request; split the request into one order at a time.",
            "intents": intents, "order_ids": order_ids, "read_only": True,
        }}

    if order_status and refund and not refund_eligibility:
        return {"route": "POLICY", "policy_decision": {
            "mode": "guarded", "decision": "unsupported_request",
            "reason": "Combined order status and refund status are unsupported; ask about refund eligibility explicitly.",
            "intents": intents, "order_ids": order_ids, "read_only": True,
        }}

    # A complaint with a concrete logistics intent is handled as a normal order
    # lookup. Tone alone never creates a human handoff.
    if order_status or refund:
        if not order_ids:
            requested = "物流/订单状态查询" if order_status and not refund else "订单状态和退款资格查询"
            return {"route": "CLARIFY", "policy_decision": {
                "mode": "guarded", "decision": "clarification_needed",
                "reason": f"An order number is required for {requested}.",
                "intents": intents, "order_ids": [], "read_only": True,
            }}
        route = "MULTI_READ" if order_status and refund_eligibility else ("ORDER" if order_status else "REFUND")
        return {"route": route, "policy_decision": {
            "mode": "guarded", "decision": "readonly_query",
            "reason": "Recognized a read-only support lookup; specialist model tools remain in use." if route != "MULTI_READ" else "Combined existing order and refund eligibility read tools.",
            "intents": intents, "order_ids": order_ids, "read_only": True,
        }}
    return None


def allowed_agent_tools(agent_tools: list) -> list:
    """Keep normal specialist tools, while hiding mutations and unverified PII/financial reads."""
    return [tool for tool in agent_tools if getattr(tool, "name", "") not in DENIED_TOOLS]


def blocked_tool_result(tool_name: str) -> dict[str, Any] | None:
    """Defense in depth for a model that emits a tool absent from its tool list."""
    if tool_name in WRITE_TOOLS:
        return {"blocked": True, "reason": "This demo is read-only; no business mutation was performed."}
    if tool_name in SENSITIVE_TOOLS:
        return {"blocked": True, "reason": "Identity is unverified; this demo does not disclose customer or payment details."}
    return None


def add_demo_data_disclaimer(reply: str, trace: list) -> str:
    if not trace:
        return reply
    disclaimer = "演示查询使用预置示例数据，未验证账户身份。" if re.search(r"[\u4e00-\u9fff]", reply) else "This demo uses seeded sample data; account identity was not verified."
    if disclaimer in reply:
        return reply
    return f"{reply.rstrip()} {disclaimer}"


def unverified_lookup_reply(message: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", message or ""):
        return "我没有从订单工具中验证到结果，因此不能确认订单信息。请稍后重试；此演示使用示例数据且未验证账户身份。"
    return "I could not verify this request using the order tools, so I cannot confirm order details. Please try again; this demo uses seeded data and does not verify account identity."


def clarification_reply(policy_decision: dict[str, Any]) -> str:
    if "物流" in policy_decision.get("reason", ""):
        return "可以帮你查物流。请提供订单号；当前演示只查询示例订单数据，不会验证真实账户身份。"
    return "可以帮你同时查询物流状态和退款资格。请提供订单号；当前演示只查询示例订单数据，不会验证真实账户身份。"


def policy_reply(policy_decision: dict[str, Any]) -> str:
    decision = policy_decision.get("decision")
    if decision == "blocked_injection":
        return "我不能按消息中的指令改变安全规则或身份验证状态。你可以继续提出普通的只读订单查询。"
    if decision == "blocked_write":
        return "此演示仅支持只读查询，未发起退款或付款操作。你可以提供订单号来查询退款资格。"
    if decision == "unverified_identity":
        return "当前无法验证账户身份，因此不能提供他人的姓名、地址或支付信息。可继续查询非个人的订单物流状态。"
    if decision == "unsupported_request":
        return "当前不能在一次请求中处理多个订单或该退款信息组合。请每次提供一个订单号；退款状态查询暂未支持。"
    return "当前没有连接真人客服队列，因此我无法为你创建或确认人工工单。你可以继续提出订单只读查询。"


def handoff_result(reason: str = "", *, requested: bool = False) -> dict[str, Any]:
    """Truthful handoff state; this adapter has no ticketing or live-agent queue."""
    return {"state": "not_connected", "requested": requested, "ticket_created": False,
            "message": "Live human support is not connected in this demo. No support ticket was created."}


def run_combined_read(order_id: int, user_message: str, history: list | None = None) -> dict[str, Any]:
    """Use the existing model/tool loop with only existing read tools exposed."""
    from src.agents.base import run_tool_agent
    from src.agents.order_agent import get_order_status, track_shipment
    from src.agents.refund_agent import check_refund_eligibility

    prompt = (
        "You are answering a read-only combined order support request. "
        "Call get_order_status, track_shipment, and check_refund_eligibility for the exact order id "
        f"{order_id}. Do not attempt any mutation. Summarize only returned facts, in the customer's language. "
        "This demo uses seeded sample records and has not verified account identity."
    )
    result = run_tool_agent(
        "business_policy", prompt,
        [get_order_status, track_shipment, check_refund_eligibility],
        f"Read-only request for order {order_id}: {user_message}", history=history,
    )
    observed = {item.get("tool") for item in result.get("trace", [])}
    required = {"get_order_status", "track_shipment", "check_refund_eligibility"}
    if not required.issubset(observed):
        result["reply"] = "我没有完成全部必要的只读查询，因此不能确认物流或退款资格。请稍后重试。"
    result["reply"] = add_demo_data_disclaimer(result.get("reply", ""), result.get("trace", []))
    result["agent"] = "business_policy"
    return result
