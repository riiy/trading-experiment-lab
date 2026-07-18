from __future__ import annotations

from enum import StrEnum


class AShareBoard(StrEnum):
    MAIN_SH = "MAIN_SH"
    MAIN_SZ = "MAIN_SZ"
    CHINEXT = "CHINEXT"
    STAR = "STAR"
    BEIJING = "BEIJING"
    NON_A_SHARE = "NON_A_SHARE"
    UNKNOWN = "UNKNOWN_BOARD"


def get_a_share_board(code: str) -> AShareBoard:
    value = str(code).strip().upper()
    digits = value.split(".")[0].removeprefix("SH").removeprefix("SZ").removeprefix("BJ")
    market = value.split(".")[-1] if "." in value else ""

    if (market == "SH" and digits.startswith("900")) or (market == "SZ" and digits.startswith("200")):
        return AShareBoard.NON_A_SHARE
    if market == "SH" and digits.startswith(("600", "601", "603", "605")):
        return AShareBoard.MAIN_SH
    if market == "SZ" and digits.startswith(("000", "001", "002", "003")):
        return AShareBoard.MAIN_SZ
    if market == "SZ" and digits.startswith(("300", "301")):
        return AShareBoard.CHINEXT
    if market == "SH" and digits.startswith(("688", "689")):
        return AShareBoard.STAR
    if market == "BJ" and digits.startswith(("4", "8", "9")):
        return AShareBoard.BEIJING
    return AShareBoard.UNKNOWN
