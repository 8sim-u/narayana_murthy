from dataclasses import dataclass
from typing import List


@dataclass
class RuleEvaluationResult:
    alerts: List[str]
    decision: str


class RuleEngine:
    """
    Evaluates detection events against configured workplace activity rules.
    Decoupled from HTTP route handlers to allow extension and independent testing.
    """

    @staticmethod
    def evaluate(zone: str, objects: List[str]) -> RuleEvaluationResult:
        alerts: List[str] = []
        decision: str = "NO_RULE_TRIGGERED"

        normalized_zone = zone.strip().upper() if zone else "UNKNOWN"

        if normalized_zone == "RESTRICTED":
            alerts.append("RESTRICTED_ZONE_DETECTED")
            decision = "REVIEW_REQUIRED"

        return RuleEvaluationResult(alerts=alerts, decision=decision)
