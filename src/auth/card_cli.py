import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auth.card_codes import CardCodeService, CardCodeStore
from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService
from src.config.runtime import load_runtime_config


def build_service() -> CardCodeService:
    cfg = load_runtime_config()
    billing = SubscriptionService(BillingStore(cfg.data_dir / "billing"))
    return CardCodeService(CardCodeStore(cfg.data_dir / "auth"), billing)


def main():
    parser = argparse.ArgumentParser(description="Manage membership card codes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--tier", choices=["basic", "vip", "admin"], required=True)
    generate.add_argument("--duration-days", type=int)
    generate.add_argument("--days", type=int)
    generate.add_argument("--count", type=int, default=1)
    generate.add_argument("--batch", default="", help="Optional batch id")
    generate.add_argument("--platform", default="", help="Optional platform tag")
    generate.add_argument("--output", default="", help="Optional CSV output path")

    list_codes = subparsers.add_parser("list")
    list_codes.add_argument("--batch", default="", help="Optional batch id filter")

    disable = subparsers.add_parser("disable")
    disable.add_argument("--code", required=True)

    show = subparsers.add_parser("show")
    show.add_argument("--code", required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--batch", default="", help="Optional batch id filter")
    export.add_argument("--output", required=True)

    args = parser.parse_args()
    service = build_service()

    if args.command == "generate":
        duration_days = args.days or args.duration_days
        if not duration_days:
            raise SystemExit("Either --days or --duration-days is required")
        rows = service.generate_codes(
            args.tier,
            duration_days,
            args.count,
            batch_id=args.batch,
            platform=args.platform,
        )
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _write_csv(out_path, rows)
            print(out_path)
        else:
            _print_csv(rows)
        return

    if args.command == "list":
        _print_csv(service.list_codes(batch_id=args.batch or None))
        return

    if args.command == "disable":
        result = service.disable_code(args.code)
        print(f"{result['code']},{result['tier']},disabled")
        return

    if args.command == "show":
        _print_csv([service.get_code_status(args.code)])
        return

    if args.command == "export":
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(out_path, service.list_codes(batch_id=args.batch or None))
        print(out_path)
        return


def _print_csv(rows):
    writer = csv.DictWriter(__import__("sys").stdout, fieldnames=_fieldnames(rows))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def _write_csv(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_fieldnames(rows))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _fieldnames(rows):
    default = [
        "code",
        "tier",
        "duration_days",
        "created_at",
        "batch_id",
        "platform",
        "used_by",
        "used_at",
        "disabled",
        "expires_at",
    ]
    if not rows:
        return default
    present = []
    for key in default:
        if any(key in row for row in rows):
            present.append(key)
    for row in rows:
        for key in row.keys():
            if key not in present:
                present.append(key)
    return present


if __name__ == "__main__":
    main()
