"""Explicitly scripted discovery decisions for local fixture demonstrations; no model calls."""
from waypoint.discovery.cassette import DecisionContext
from waypoint.discovery.decisions import Decision, Expectation, ExpectedElement

PH = "‹$inputs.member_id›"
E = ExpectedElement


class ScriptedDecider:
    """NOT a model. Picks elements the way the model is instructed to, deterministically."""

    model = "scripted-test-decider"

    def decide(self, ctx: DecisionContext) -> Decision:
        def find(role: str, *, name: str | None = None, anchor: str | None = None,
                 empty: bool | None = None) -> str | None:
            for eid, e in ctx.elements:
                if e.role == role and (name is None or e.name == name) \
                        and (anchor is None or anchor in e.anchors) \
                        and (empty is None or (not e.value) == empty):
                    return eid
            return None

        def cell(column: str) -> str:
            return next(eid for eid, e in ctx.elements if e.role == "cell"
                        and e.anchors[:1] == (column,) and "Savings" in e.anchors)

        if find("button", name="Sign On"):
            for label, secret in (("User ID", "meridian_user"), ("Password", "meridian_password")):
                if box := find("textbox", anchor=label, empty=True):
                    return Decision("type", intent=f"Enter the operator {label}", element=box,
                                    value=f"$secrets.{secret}",
                                    expect=Expectation((E("textbox", "", label),)))
            return Decision("click", intent="Sign on", element=find("button", name="Sign On"),
                            expect=Expectation((E("link", "Sign Off"),)))
        if find("columnheader", name="Balance"):
            return Decision("finish", summary="Read the member's savings balance and status",
                            outputs={"savings_balance": cell("Balance"),
                                     "account_status": cell("Status")},
                            expect=Expectation((E("columnheader", "Balance"),
                                                E("LayoutTableCell", PH, "Member ID"))))
        if find("LayoutTableCell", name=PH) and (tab := find("cell", name="Accounts")):
            return Decision("click", intent="Open the Accounts tab", element=tab,
                            expect=Expectation((E("columnheader", "Balance"),)))
        if view := find("link", name="View", anchor=PH):
            return Decision("click", intent="Open this member's record", element=view,
                            expect=Expectation((E("LayoutTableCell", "Member Profile"),)))
        if box := find("textbox", anchor="Member ID", empty=True):
            return Decision("type", intent="Enter the member ID", element=box,
                            value="$inputs.member_id",
                            expect=Expectation((E("textbox", "", "Member ID"),)))
        if search := find("button", name="Search"):
            return Decision("click", intent="Run the search", element=search,
                            expect=Expectation((E("link", "View", PH),)))
        return Decision("give_up", reason="the scripted decider does not recognise this screen")
