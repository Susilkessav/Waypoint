"""Seeded fixture data. Entirely fictional - no real people, no real PII.

Search returns the searched member's whole *branch roster* rather than a single
row. That is deliberate and load-bearing: it guarantees the results grid always
holds eight rows whose View links share one accessible name, which is the
constraint that forces anchored relative locators (R-LOC-1, R-LOC-5)
instead of nth-child indexing. Legacy servicing consoles genuinely behave this
way - you search, and the grid shows the member in branch context.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sentinel IDs used by the chaos table (RULES.md, The test fixture).
NO_RECORDS_MEMBER_ID = "00000"
RESTRICTED_MEMBER_ID = "99999"  # permission-denied banner arrives in B1


@dataclass(frozen=True)
class Account:
    account_id: str
    kind: str  # "Savings" | "Checking"
    balance_cents: int
    status: str  # "active" | "dormant" | "frozen"

    @property
    def balance(self) -> str:
        return f"${self.balance_cents // 100:,}.{self.balance_cents % 100:02d}"

    @property
    def opened(self) -> str:
        """Seeded accounts predate the console's records; only sub-accounts carry a time."""
        return "-"


@dataclass(frozen=True)
class Member:
    member_id: str
    name: str
    branch: str
    status: str
    joined: str
    accounts: tuple[Account, ...]

    @property
    def savings(self) -> Account | None:
        return next((a for a in self.accounts if a.kind == "Savings"), None)


def _m(
    member_id: str,
    name: str,
    branch: str,
    status: str,
    joined: str,
    savings_cents: int,
    checking_cents: int,
) -> Member:
    return Member(
        member_id=member_id,
        name=name,
        branch=branch,
        status=status,
        joined=joined,
        accounts=(
            Account(f"SV-{member_id}-01", "Savings", savings_cents, status),
            Account(f"CK-{member_id}-01", "Checking", checking_cents, status),
        ),
    )


# Branch BR-014 holds eight members: every search against one of them returns
# all eight, producing eight identically-named View links.
MEMBERS: tuple[Member, ...] = (
    _m("10233", "Marion Vance", "BR-014", "active", "2004-03-11", 128845, 41290),
    _m("12345", "Dolores Whitfield", "BR-014", "active", "2001-07-22", 428119, 96355),
    _m("18820", "Harlan Mbeki", "BR-014", "active", "2009-11-02", 73402, 18810),
    _m("24170", "Priya Raghunathan", "BR-014", "frozen", "2013-05-19", 951077, 220400),
    _m("33915", "Ines Carrasco", "BR-014", "active", "2007-01-30", 264518, 55120),
    _m("48602", "Tobias Lindqvist", "BR-014", "dormant", "1999-09-14", 8890, 1204),
    _m("55471", "Amara Okonkwo", "BR-014", "active", "2016-08-08", 512663, 133975),
    _m("67890", "Bernard Sloane", "BR-014", "dormant", "2005-02-27", 91204, 22815),
    _m("71204", "Yusuf Demir", "BR-022", "active", "2011-06-06", 330911, 78400),
    _m("80336", "Clara Nkemelu", "BR-022", "active", "2018-12-01", 145200, 39615),
    _m("91158", "Ravi Shanmugam", "BR-022", "frozen", "2002-04-17", 762045, 180330),
    _m(RESTRICTED_MEMBER_ID, "Withheld Account", "BR-022", "active", "2020-10-10", 100000, 25000),
)

_BY_ID = {m.member_id: m for m in MEMBERS}


def get_member(member_id: str) -> Member | None:
    return _BY_ID.get(member_id.strip())


def branch_roster(member_id: str) -> list[Member]:
    """Every member sharing a branch with `member_id`, in stable ID order.

    Empty when the ID is unknown, which the search screen renders as the
    "No records found" banner rather than as an error.
    """
    member = get_member(member_id)
    if member is None:
        return []
    return sorted(
        (m for m in MEMBERS if m.branch == member.branch),
        key=lambda m: m.member_id,
    )
