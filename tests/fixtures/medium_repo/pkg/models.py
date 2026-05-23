"""User domain models.

Deliberate Phase 2 markers:
- ``Admin`` inherits from ``User`` → INHERITS edge.
- ``GodClass`` has many methods → ``god_class`` rule positive.
"""

from __future__ import annotations


class User:
    """Base user."""

    def __init__(self, name: str) -> None:
        self.name = name

    def display(self) -> str:
        return self.name


class Admin(User):
    """Privileged user — inherits from User."""

    def display(self) -> str:
        return f"Admin: {self.name}"


class GodClass:
    """Bug fixture: a class with too many methods."""

    def m1(self) -> None: pass
    def m2(self) -> None: pass
    def m3(self) -> None: pass
    def m4(self) -> None: pass
    def m5(self) -> None: pass
    def m6(self) -> None: pass
    def m7(self) -> None: pass
    def m8(self) -> None: pass
    def m9(self) -> None: pass
    def m10(self) -> None: pass
    def m11(self) -> None: pass
    def m12(self) -> None: pass
    def m13(self) -> None: pass
    def m14(self) -> None: pass
    def m15(self) -> None: pass
    def m16(self) -> None: pass
    def m17(self) -> None: pass
    def m18(self) -> None: pass
    def m19(self) -> None: pass
    def m20(self) -> None: pass
    def m21(self) -> None: pass
    def m22(self) -> None: pass
    def m23(self) -> None: pass
    def m24(self) -> None: pass
    def m25(self) -> None: pass
    def m26(self) -> None: pass
