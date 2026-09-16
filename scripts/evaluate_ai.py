"""Opt-in live AI evaluation, or offline fixture validation without API requests."""

import argparse
import json
import os
import statistics
import time
from datetime import date
from pathlib import Path

from scripts.benchmark import percentile
from swim_tracker.ai_search import AISearchFilters, interpret_search

ROOT = Path(__file__).resolve().parents[1]


def load_cases(path):
    data = json.loads(path.read_text())
    for case in data["cases"]:
        AISearchFilters(**case["expected"])
    return data


def score(expected, actual):
    # Explanations are free-form; every executable filter is compared.
    fields = [name for name in AISearchFilters.model_fields if name != "explanation"]
    if expected["intent"] == "unsupported":
        return actual["intent"] == "unsupported", {"intent": actual["intent"] == "unsupported"}
    matches = {name: expected[name] == actual[name] for name in fields}
    return all(matches.values()), matches


def run(data, model, api_key):
    outputs = []
    for case in data["cases"]:
        start = time.perf_counter()
        usage = {}
        try:
            actual = interpret_search(
                case["query"],
                api_key=api_key,
                model=model,
                available_groups=data["available_groups"],
                reference_date=date.fromisoformat(data["reference_date"]),
                metrics=usage,
            ).model_dump()
            exact, fields = score(case["expected"], actual)
            output = {
                "id": case["id"],
                "exact": exact,
                "fields": fields,
                "actual": actual,
                "usage": usage,
            }
        except Exception as exc:
            # Never persist exception strings that could contain credentials or payloads.
            output = {
                "id": case["id"],
                "exact": False,
                "error_type": type(exc).__name__,
                "usage": usage,
            }
        output["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        outputs.append(output)
    unsupported = {
        case["id"] for case in data["cases"] if case["expected"]["intent"] == "unsupported"
    }
    latencies = [item["latency_ms"] for item in outputs]
    return {
        "model": model,
        "reference_date": data["reference_date"],
        "cases": len(outputs),
        "exact_match_accuracy": sum(item["exact"] for item in outputs) / len(outputs),
        "unsupported_accuracy": sum(item["exact"] for item in outputs if item["id"] in unsupported)
        / len(unsupported)
        if unsupported
        else None,
        "field_accuracy": {
            field: sum(
                item.get("fields", {}).get(field, False)
                for item in outputs
                if item["id"] not in unsupported
            )
            / max(1, len(outputs) - len(unsupported))
            for field in AISearchFilters.model_fields
            if field != "explanation"
        },
        "latency_median_ms": statistics.median(latencies),
        "latency_p95_ms": percentile(latencies, 0.95),
        "input_tokens": sum(item["usage"].get("input_tokens", 0) for item in outputs),
        "cached_input_tokens": sum(item["usage"].get("cached_input_tokens", 0) for item in outputs),
        "output_tokens": sum(item["usage"].get("output_tokens", 0) for item in outputs),
        "results": outputs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Make billable API requests, one per case."
    )
    parser.add_argument("--cases", type=Path, default=ROOT / "evals/search_cases.json")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"))
    parser.add_argument("--output", type=Path, default=ROOT / "evals/latest-results.json")
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--cached-input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    args = parser.parse_args()
    data = load_cases(args.cases)
    if not args.live:
        print(
            f"Validated {len(data['cases'])} labeled cases. No API calls made; model accuracy is not measured."
        )
    else:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            parser.error("Set OPENAI_API_KEY to run live evaluation.")
        result = run(data, args.model, key)
        prices = [
            args.input_price_per_million,
            args.cached_input_price_per_million,
            args.output_price_per_million,
        ]
        if all(price is not None and price >= 0 for price in prices):
            cost = (
                (result["input_tokens"] - result["cached_input_tokens"]) * prices[0]
                + result["cached_input_tokens"] * prices[1]
                + result["output_tokens"] * prices[2]
            ) / 1e6
            result["estimated_cost_usd"] = cost
            result["estimated_cost_per_query_usd"] = cost / result["cases"]
            result["price_source"] = (
                "operator-supplied per-million-token prices; excludes any provider-specific charges"
            )
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps({key: value for key, value in result.items() if key != "results"}, indent=2)
        )
