"""Regression guards against order-placement capability in application code.

These are static AST/string checks over ``src/``. They catch accidental
introduction of order APIs; they are NOT proof against dynamic access
(``getattr`` with computed names, ``eval``, string-built URLs, third-party
code, or a modified allowlist in this file).
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

from src import webull_sandbox_cash_activities as cash_module
from src import webull_sandbox_readonly as readonly_module

SRC = Path(__file__).resolve().parents[1] / "src"

ORDER_VERB_IDENTIFIER = re.compile(
    r"(?i)(place|replace|cancel|preview|submit|modify|amend|create)_?orders?"
    r"|order_v\d|order_?api|order_?service"
)
ORDER_ENDPOINT_TEXT = re.compile(
    r"(?i)/trade/order|trading/orders?\b|orders?/(place|replace|cancel|preview)"
)
ALLOWED_SDK_MODULES = {
    "webull.core.client",
    "webull.trade.trade_client",
    "webull.core.http.response",
    "webull.data.data_client",
}
ALLOWED_CLIENT_ATTRIBUTES = {"account_v2", "activity", "market_data"}
SDK_CLIENT_NAMES = {"trade_client", "client"}
SDK_API_ATTRIBUTES = {"_account_api", "_activity_api", "_api"}
ALLOWED_SDK_OPERATIONS = {
    "get_account_list",
    "get_account_balance",
    "get_account_position",
    "list_activities",
}
# The only permitted order-named symbol: the stub that always raises.
ALLOWED_ORDER_DEFINITIONS = {("layer3.py", "submit_order_to_broker")}


def scan_source(source: str, filename: str = "module.py"):
    """Return (violations, observed SDK read operations) for one module."""
    tree = ast.parse(source)
    violations: list[str] = []
    observed: set[str] = set()

    def flag(node: ast.AST, message: str) -> None:
        violations.append(f"{filename}:{getattr(node, 'lineno', 0)} {message}")

    def check_identifier(node: ast.AST, name: str, *, definition: bool) -> None:
        if not ORDER_VERB_IDENTIFIER.search(name):
            return
        if definition and (filename, name) in ALLOWED_ORDER_DEFINITIONS:
            return
        flag(node, f"order-capable identifier {name!r}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            check_identifier(node, node.name, definition=True)
        elif isinstance(node, ast.Name):
            check_identifier(node, node.id, definition=False)
        elif isinstance(node, ast.Attribute):
            check_identifier(node, node.attr, definition=False)
            owner = node.value
            if (
                isinstance(owner, ast.Name)
                and owner.id in SDK_CLIENT_NAMES
                and node.attr not in ALLOWED_CLIENT_ATTRIBUTES
            ):
                flag(node, f"SDK client attribute {node.attr!r} is not allowed")
            if (
                isinstance(owner, ast.Call)
                and isinstance(owner.func, ast.Name)
                and owner.func.id == "_build_sdk_trade_client"
                and node.attr not in ALLOWED_CLIENT_ATTRIBUTES
            ):
                flag(node, f"SDK client attribute {node.attr!r} is not allowed")
            if isinstance(owner, ast.Attribute) and owner.attr in SDK_API_ATTRIBUTES:
                if node.attr in ALLOWED_SDK_OPERATIONS:
                    observed.add(node.attr)
                else:
                    flag(node, f"SDK operation {node.attr!r} is not read-only")
        elif isinstance(node, ast.keyword) and node.arg:
            check_identifier(node, node.arg, definition=False)
        elif isinstance(node, ast.alias):
            check_identifier(node, node.name.rsplit(".", 1)[-1], definition=False)
            if node.asname:
                check_identifier(node, node.asname, definition=False)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            pass
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ORDER_ENDPOINT_TEXT.search(node.value)
        ):
            flag(node, "order endpoint text in string constant")

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module.split(".")[0] == "webull" and module not in ALLOWED_SDK_MODULES:
                flag(node, f"SDK import {module!r} is not allowlisted")
    return violations, observed


def scan_application_code():
    violations: list[str] = []
    observed: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        file_violations, file_observed = scan_source(
            path.read_text(encoding="utf-8"), path.name
        )
        violations += file_violations
        observed |= file_observed
    return violations, observed


def test_application_code_has_no_order_capability():
    violations, _ = scan_application_code()
    assert violations == []


def test_read_only_account_and_activity_operations_are_preserved():
    _, observed = scan_application_code()
    assert observed == ALLOWED_SDK_OPERATIONS


@pytest.mark.parametrize(
    "snippet",
    [
        "def f(api):\n    api.place_order(1)\n",
        "def f(api):\n    api.cancel_order(1)\n",
        "def replace_order():\n    pass\n",
        "x = trade_client.order_v3\n",
        "x = trade_client.order_v2.place_order\n",
        "x = trade_client.something_else\n",
        "x = _build_sdk_trade_client(a, b).order_v3\n",
        "import webull.trade.trade_order_client\n",
        "from webull.trade.request.place_order_request import X\n",
        "from webull.trade import OrderService\n",
        "class S:\n    def go(self):\n        return self._account_api.place_order(1)\n",
        "class S:\n    def go(self):\n        return self._activity_api.transfer(1)\n",
        "class S:\n    def go(self):\n        return self._api.list_orders()\n",
        "URL = '/openapi/trade/order/place'\n",
        "URL = 'orders/cancel'\n",
        "f(submit_order=True)\n",
        "def submit_order_to_broker():\n    pass\n",
    ],
)
def test_scanner_rejects_order_capable_code(snippet):
    violations, _ = scan_source(snippet, "synthetic.py")
    assert violations, snippet


def test_scanner_accepts_the_existing_read_only_shape():
    source = (
        "from webull.core.client import ApiClient\n"
        "from webull.trade.trade_client import TradeClient\n"
        "class G:\n"
        "    def __init__(self, a):\n        self._account_api = a\n"
        "    def bal(self, i):\n        return self._account_api.get_account_balance(i)\n"
        "    def lst(self):\n        return self._account_api.get_account_list()\n"
        "    def pos(self, i):\n        return self._account_api.get_account_position(i)\n"
        "class A:\n"
        "    def __init__(self, a):\n        self._activity_api = a\n"
        "    def go(self):\n        return self._activity_api.list_activities()\n"
        "def build(trade_client):\n    return trade_client.account_v2, trade_client.activity\n"
        "PENDING = 'orders_complete'\nKIND = 'BUY_CANCELLED'\n"
    )
    violations, observed = scan_source(source, "synthetic.py")
    assert violations == []
    assert observed == ALLOWED_SDK_OPERATIONS


def test_disabled_stub_is_only_allowed_in_layer3():
    snippet = "def submit_order_to_broker():\n    pass\n"
    assert scan_source(snippet, "layer3.py")[0] == []
    assert scan_source(snippet, "other.py")[0] != []
    assert scan_source("submit_order_to_broker()\n", "layer3.py")[0] != []


def public_methods(cls) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(cls)
        if not name.startswith("_") and callable(value)
    }


def test_sdk_wrappers_expose_only_read_operations():
    assert public_methods(readonly_module.OfficialSdkGateway) == {
        "get_account_list",
        "get_account_balance",
        "get_account_position",
    }
    assert public_methods(cash_module.OfficialSdkCashActivitiesGateway) == {
        "list_cash_activities"
    }


def test_wrappers_delegate_only_read_methods_to_the_sdk():
    class Recorder:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.calls.append(name)
                return {}

            return record

    account = Recorder()
    gateway = readonly_module.OfficialSdkGateway(account)
    gateway.get_account_list()
    gateway.get_account_balance("a")
    gateway.get_account_position("a")
    assert account.calls == [
        "get_account_list",
        "get_account_balance",
        "get_account_position",
    ]
    assert not hasattr(gateway, "place_order")
